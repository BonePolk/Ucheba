"""Инструмент Ameba: складывает оценки этапов, саму работу не выполняет."""
from typing import Literal
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from common.schedule import slots_for_date

from pydantic import BaseModel, Field


class Step(BaseModel):
    model_config = {'extra': 'forbid'}
    title: str = Field(min_length=1, max_length=300)
    min_minutes: int = Field(ge=0)
    max_minutes: int = Field(ge=0)
    wait_minutes: int = Field(ge=0)


class Plan(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    steps: list[Step] = Field(min_length=1)
    assumptions: list[str]
    effort: Literal['low', 'medium', 'high', 'unknown']
    expected_result: str = Field(min_length=1, max_length=400)
    reward: float | None = Field(ge=0)
    reward_unit: str


class Plans(BaseModel):
    model_config = {'extra': 'forbid'}
    plans: list[Plan] = Field(min_length=1)


TOOL = {
    'type': 'function', 'name': 'plan_tasks', 'strict': True,
    'description': 'Сохранить или уточнить планы задач и вычислить время по этапам. Можно передать несколько задач.',
    'parameters': Plans.model_json_schema(),
}


class Order(BaseModel):
    model_config = {'extra': 'forbid'}
    task_ids: list[int]


ORDER_TOOL = {
    'type': 'function', 'name': 'reorder_tasks', 'strict': True,
    'description': 'Переставить все задачи и пересчитать накопленное время, нагрузку и ожидаемые результаты по сохранённым планам.',
    'parameters': Order.model_json_schema(),
}


def rest_timeline(order, plans):
    blocks = []
    elapsed = daily = 0

    def add(key, kind, minutes, task_id=None):
        nonlocal elapsed
        blocks.append({'id': key, 'kind': kind, 'task_id': task_id,
                       'start': elapsed, 'minutes': minutes})
        elapsed += minutes

    for task_id in order:
        remaining = plans[task_id]['max_minutes']
        part = 0
        while remaining > 0:
            # Условный день: 6 часов работы, еда, перерывы, личное время и сон.
            if daily == 360:
                day = elapsed // 1440
                add(f'meal-end-{day}', 'meal', 60)
                add(f'personal-{day}', 'personal', 450)
                add(f'sleep-{day}', 'sleep', 480)
                daily = 0
            elif daily and daily % 90 == 0:
                day = elapsed // 1440
                kind = 'meal' if daily == 180 else 'break'
                add(f'{kind}-{day}-{daily}', kind, 60 if kind == 'meal' else 15)
            minutes = min(remaining, 90 - daily % 90)
            add(f'task-{task_id}-{part}', 'task', minutes, task_id)
            remaining -= minutes
            daily += minutes
            part += 1
    return blocks


def weekly_timeline(order, plans, week, now):
    zone = ZoneInfo(week['timezone'])
    start = datetime.fromisoformat(now).astimezone(timezone.utc)
    day = start.astimezone(zone).date()
    remaining = {task_id: plans[task_id]['max_minutes'] for task_id in order}
    todo = [task_id for task_id in order if remaining[task_id] > 0]
    if not todo:
        return []
    blocks = []
    cursor = start
    parts = {}
    streak = 0

    def add(kind, end, task_id=None, title=''):
        nonlocal cursor
        length = int((end - cursor).total_seconds() / 60)
        if length <= 0:
            return
        key = f'task-{task_id}' if task_id is not None else kind
        part = parts.get(key, 0)
        parts[key] = part + 1
        blocks.append({'id': f'{key}-{part}', 'kind': kind, 'task_id': task_id,
                       'title': title, 'start': int((cursor - start).total_seconds() / 60), 'minutes': length})
        cursor = end

    for offset in range(366):
        date = day + timedelta(days=offset)
        midnight = datetime.combine(date, time(), zone)
        slots = slots_for_date(week, date)
        slots = slots + [{'start': '24:00', 'end': '24:00', 'kind': 'personal', 'title': ''}]
        for slot in slots:
            def at(value):
                hour, minute = map(int, value.split(':'))
                return (midnight + timedelta(hours=hour, minutes=minute)).astimezone(timezone.utc)
            left, right = at(slot['start']), at(slot['end'])
            if right <= cursor:
                continue
            if left > cursor:
                add('personal', left)
                streak = 0
            if not todo:
                return blocks
            if slot['kind'] != 'work':
                add(slot['kind'], right, title=slot['title'])
                streak = 0
                continue
            while cursor < right and todo:
                available = int((right - cursor).total_seconds() / 60)
                if available <= 0:
                    cursor = right
                    break
                if streak >= 90:
                    add('break', cursor + timedelta(minutes=min(15, available)))
                    streak = 0
                    continue
                task_id = todo[0]
                amount = min(available, 90 - streak, remaining[task_id])
                add('task', cursor + timedelta(minutes=amount), task_id)
                streak += amount
                remaining[task_id] -= amount
                if remaining[task_id] == 0:
                    todo.pop(0)
                    if not todo:
                        return blocks
        if not todo:
            return blocks
    raise ValueError('В расписании недостаточно рабочего времени на ближайший год')


def reorder(arguments, plans, expected, week=None, now=None):
    order = Order.model_validate_json(arguments).task_ids
    if len(order) != len(expected) or set(order) != set(expected) or set(plans) != set(expected):
        raise ValueError('Сначала оцени все задачи, затем передай каждый ID ровно один раз')
    low = high = wait = 0
    effort = {level: 0 for level in ('low', 'medium', 'high', 'unknown')}
    rewards = {}
    timeline = []
    for task_id in order:
        plan = plans[task_id]
        start = high
        low += plan['min_minutes']
        high += plan['max_minutes']
        wait += plan['wait_minutes']
        effort[plan['effort']] += plan['max_minutes']
        if plan['reward'] is not None:
            unit = plan['reward_unit']
            rewards[unit] = rewards.get(unit, 0) + plan['reward']
        timeline.append({'task_id': task_id, 'start_active_minutes': start,
                         'end_active_minutes': high,
                         'expected_result': plan['expected_result']})
    return {'task_ids': order, 'min_minutes': low, 'max_minutes': high,
            'wait_minutes': wait, 'effort_minutes': effort, 'potential_rewards': rewards,
            'timeline': timeline,
            'blocks': weekly_timeline(order, plans, week, now) if week and now else rest_timeline(order, plans),
            'start_at': now if week else None,
            'timezone': week['timezone'] if week else None,
            'rest_assumption': 'Личное недельное расписание; перерывы после 90 минут работы.' if week else 'Условные дни: 6 ч работы, еда и сон. Это не личное расписание.',
            'note': 'Активное время без сна и еды. Результаты приблизительны; баллы не гарантированы. '
                    'Смена порядка сама по себе не уменьшает объём работы; для этого нужно уточнить планы.'}


def calculate(arguments, expected):
    body = Plans.model_validate_json(arguments)
    result = {}
    for plan in body.plans:
        if plan.task_id not in expected or plan.task_id in result:
            raise ValueError('План должен относиться к переданной задаче и не дублироваться')
        if any(step.min_minutes > step.max_minutes for step in plan.steps):
            raise ValueError('Минимальная длительность этапа не может превышать максимальную')
        if plan.reward is not None and not plan.reward_unit.strip():
            raise ValueError('Для числового результата нужна единица измерения')
        low = sum(step.min_minutes for step in plan.steps)
        high = sum(step.max_minutes for step in plan.steps)
        wait = sum(step.wait_minutes for step in plan.steps)
        result[plan.task_id] = plan.model_dump() | {
            'min_minutes': low, 'max_minutes': high, 'wait_minutes': wait,
            'time_estimate': f'Примерно {low}–{high} мин активной работы; внешнее ожидание: {wait} мин.',
        }
    return result
