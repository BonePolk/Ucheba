"""Инструмент Ameba: складывает оценки этапов, саму работу не выполняет."""
from typing import Annotated, Literal
from datetime import date as calendar_date, datetime, timedelta, timezone, time
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
    deadline_policy: Literal['soft', 'finish_before', 'expires'] = Field(description='soft: после срока можно; finish_before: необходимо завершить до срока; expires: после срока выполнение или сдача недопустимы. Выбирай по фактам из контекста.')
    focus_minutes: int = Field(ge=10, le=30, description='Время на концентрацию при начале/возобновлении задачи. Этапы steps не включают это время.')
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
    'description': 'Оценить полный объём полезной работы по этапам, включая уже выполненное. Python вычтет completed_work_minutes владельца. Можно передать несколько задач.',
    'parameters': Plans.model_json_schema(),
}


class Order(BaseModel):
    model_config = {'extra': 'forbid'}
    task_ids: list[int]
    keep_previous_times: bool = Field(description='Сохранять прежнее время начала задач, когда оно ещё подходит. false — если замечания владельца, сроки или новые приоритеты требуют перестановки.')


ORDER_TOOL = {
    'type': 'function', 'name': 'reorder_tasks', 'strict': True,
    'description': 'Зафиксировать общий порядок задач и проверить текущие точные блоки, размещённые через edit_task_blocks. Пересчитать время, нагрузку и результаты. Сам не добавляет и не перемещает блоки.',
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


def hard_deadlines(plans, tasks):
    result = {}
    for task in tasks:
        if task.get('deadline') and plans[task['id']].get('deadline_policy', 'soft') != 'soft':
            raw = task['deadline']
            value = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
            result[task['id']] = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return result


def weekly_timeline(order, plans, week, now, previous=None, tasks=()):
    zone = ZoneInfo(week['timezone'])
    start = datetime.fromisoformat(now).astimezone(timezone.utc)
    day = start.astimezone(zone).date()
    remaining = {task_id: plans[task_id].get('work_max_minutes', plans[task_id]['max_minutes']) for task_id in order}
    todo = [task_id for task_id in order if remaining[task_id] > 0]
    if not todo:
        return []
    blocks = []
    cursor = start
    preferred = {}
    if previous and previous.get('start_at'):
        origin = datetime.fromisoformat(previous['start_at']).astimezone(timezone.utc)
        for block in previous.get('blocks', []):
            if block['kind'] == 'task' and not block.get('permanent'):
                target = origin + timedelta(minutes=block['start'])
                if target >= start:
                    preferred.setdefault(block['task_id'], target)
    reserved = {slot['task_id'] for slots in week.get('day_plans', {}).values() for slot in slots if slot.get('task_id') is not None}
    deadlines = hard_deadlines(plans, tasks)
    parts = {}

    def add(kind, end, task_id=None, title='', permanent=False, focus_minutes=0):
        nonlocal cursor
        length = int((end - cursor).total_seconds() / 60)
        if length <= 0:
            return
        key = f'task-{task_id}' if task_id is not None else kind
        part = parts.get(key, 0)
        parts[key] = part + 1
        blocks.append({'id': f'{key}-{part}', 'kind': kind, 'task_id': task_id,
                       'title': title, 'permanent': permanent, 'focus_minutes': focus_minutes, 'start': int((cursor - start).total_seconds() / 60), 'minutes': length})
        cursor = end

    for offset in range(7):
        date = day + timedelta(days=offset)
        midnight = datetime.combine(date, time(), zone)
        slots = day_slots(week, date)
        slots = slots + [{'start': '24:00', 'end': '24:00', 'kind': 'personal', 'title': ''}]
        for slot in slots:
            def at(value):
                hour, minute = map(int, value.split(':'))
                return (midnight + timedelta(hours=hour, minutes=minute)).astimezone(timezone.utc)
            left, right = at(slot['start']), at(slot['end'])
            if right <= cursor:
                continue
            if left > cursor:
                cursor = left  # Свободное время не обязано становиться событием.
            if slot['kind'] != 'work':
                add(slot['kind'], right, slot.get('task_id'), title=slot.get('title', ''), permanent=bool(slot.get('id')))
                continue
            while cursor < right and todo:
                available = int((right - cursor).total_seconds() / 60)
                if available <= 0:
                    cursor = right
                    break
                task_id = slot.get('task_id')
                if task_id is None:
                    task_id = next((value for value in todo if value not in reserved), None)
                if task_id not in todo:
                    break  # Именное окно не заполняем другой задачей.
                target = preferred.pop(task_id, None) if slot.get('task_id') is None else None
                if target and target > cursor:
                    cursor = min(target, right)
                    if cursor == right:
                        preferred[task_id] = target
                        break
                    available = int((right - cursor).total_seconds() / 60)
                task_right = min(right, deadlines.get(task_id, right))
                available = int((task_right - cursor).total_seconds() / 60)
                if available <= 0:
                    todo.remove(task_id)
                    continue
                focus = plans[task_id].get('focus_minutes', 20)
                minimum_work = min(30, remaining[task_id])
                if available < focus + minimum_work:
                    if task_right < right:
                        todo.remove(task_id)
                        continue
                    cursor = right  # Не начинаем большую задачу ради десяти минут работы.
                    break
                capacity = available - focus
                amount = min(capacity, remaining[task_id])
                if remaining[task_id] > capacity and remaining[task_id] - capacity < 30:
                    amount = min(capacity, (remaining[task_id] + 1) // 2)
                add('task', cursor + timedelta(minutes=focus + amount), task_id, focus_minutes=focus)
                remaining[task_id] -= amount
                if remaining[task_id] == 0:
                    todo.remove(task_id)
        if not todo:
            return blocks
    return blocks


def validate_blocks(blocks, week=None, now=None):
    checked = []
    for block in sorted(blocks, key=lambda item: item['start']):
        if block['minutes'] <= 0 or block['start'] < 0:
            raise ValueError('Неположительная длина или начало блока в прошлом')
        for other in checked:
            if block['start'] < other['start'] + other['minutes'] and not (block.get('permanent') and other.get('permanent')):
                raise ValueError('Временные блоки плана пересекаются с занятостью')
        checked.append(block)
        if block['kind'] != 'task' or block.get('permanent') or not week or not now:
            continue
        origin = datetime.fromisoformat(now).astimezone(timezone.utc)
        left = origin + timedelta(minutes=block['start'])
        right = left + timedelta(minutes=block['minutes'])
        zone = ZoneInfo(week['timezone'])
        day = left.astimezone(zone).date()
        while day <= right.astimezone(zone).date():
            midnight = datetime.combine(day, time(), zone)
            for slot in day_slots(week, day):
                if slot['kind'] == 'work':
                    continue
                hour, minute = map(int, slot['start'].split(':'))
                end_hour, end_minute = map(int, slot['end'].split(':'))
                occupied_start = (midnight + timedelta(hours=hour, minutes=minute)).astimezone(timezone.utc)
                occupied_end = (midnight + timedelta(hours=end_hour, minutes=end_minute)).astimezone(timezone.utc)
                if left < occupied_end and right > occupied_start:
                    raise ValueError(f'Задача {block["task_id"]} пересекает занятость {day.isoformat()}; некорректный план не показан')
            day += timedelta(days=1)


def free_windows(week, now, blocks=()):
    origin = datetime.fromisoformat(now)
    zone = ZoneInfo(week['timezone'])
    first = origin.astimezone(zone).date()
    result = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        midnight = datetime.combine(day, time(), zone)
        for slot in day_slots(week, day):
            if slot['kind'] != 'work':
                continue
            def at(value):
                hour, minute = map(int, value.split(':'))
                return (midnight + timedelta(hours=hour, minutes=minute)).astimezone(timezone.utc)
            pieces = [(max(origin, at(slot['start'])), at(slot['end']))]
            for block in blocks:
                if block['kind'] != 'task' or block.get('permanent'):
                    continue
                left = origin + timedelta(minutes=block['start'])
                right = left + timedelta(minutes=block['minutes'])
                pieces = [(a, b) for start, end in pieces for a, b in
                          ((start, min(end, left)), (max(start, right), end)) if b > a]
            for left, right in pieces:
                if right > left:
                    result.append({'date': day.isoformat(), 'start': left.astimezone(zone).strftime('%H:%M'),
                                   'end': '24:00' if right == midnight + timedelta(days=1) else right.astimezone(zone).strftime('%H:%M'),
                                   'start_at': left.isoformat(), 'end_at': right.isoformat(),
                                   'minutes': int((right - left).total_seconds() / 60), 'task_id': slot.get('task_id')})
    return result


def reorder(arguments, plans, expected, week=None, now=None, previous=None, tasks=(), placed_blocks=None):
    if now:
        clock = datetime.fromisoformat(now).astimezone(timezone.utc)
        minute = clock.replace(second=0, microsecond=0)
        now = (minute + timedelta(minutes=1) if minute < clock else minute).isoformat()
    request = Order.model_validate_json(arguments)
    order = request.task_ids
    missing_plans = [task_id for task_id in expected if task_id not in plans]
    if missing_plans:
        raise ValueError(f'Сначала вызови plan_tasks для задач: {missing_plans}')
    if len(order) != len(expected) or set(order) != set(expected):
        raise ValueError(f'В task_ids нужны все ID ровно по одному разу: {expected}')
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
    timeline_error = None
    deadline_conflicts = []
    scheduled_order = list(order)
    tasks_by_id = {task['id']: task for task in tasks}
    preserved = previous if request.keep_previous_times else None
    try:
        if placed_blocks is not None:
            blocks = list(placed_blocks)
        elif week and now:
            blocks = weekly_timeline(scheduled_order, plans, week, now, preserved, tasks)
        else:
            blocks = rest_timeline(scheduled_order, plans)
        validate_blocks(blocks, week, now)
    except ValueError as exc:
        blocks = []
        timeline_error = str(exc)
    if now:
        origin = datetime.fromisoformat(now).astimezone(timezone.utc)
        horizon = datetime.combine(origin.astimezone(ZoneInfo(week['timezone'])).date() + timedelta(days=7), time(), ZoneInfo(week['timezone'])) if week else origin
        windows = free_windows(week, now) if week else []
        deadlines = hard_deadlines(plans, tasks)
        for task_id in order:
            task = tasks_by_id.get(task_id, {})
            if task_id not in deadlines:
                continue
            policy = plans[task_id]['deadline_policy']
            deadline = deadlines[task_id]
            placed = [block for block in blocks if block['kind'] == 'task'
                      and not block.get('permanent') and block.get('task_id') == task_id]
            finish = max((origin + timedelta(minutes=block['start'] + block['minutes']) for block in placed), default=None)
            assigned = sum(block['minutes'] - block.get('focus_minutes', 0) for block in placed)
            required = plans[task_id]['work_max_minutes']
            incomplete = assigned < required and deadline <= horizon
            if required > 0 and (deadline < origin or finish is not None and finish > deadline or incomplete):
                capacity = sum(max(0, int((min(deadline, datetime.fromisoformat(window['end_at'])) - datetime.fromisoformat(window['start_at'])).total_seconds() / 60))
                               for window in windows if window['task_id'] in (None, task_id))
                due = [plans[other_id]['work_max_minutes'] + plans[other_id]['focus_minutes']
                       for other_id, due_at in deadlines.items()
                       if due_at <= deadline and plans[other_id]['work_max_minutes'] > 0]
                pooled_capacity = sum(max(0, int((min(deadline, datetime.fromisoformat(window['end_at'])) - datetime.fromisoformat(window['start_at'])).total_seconds() / 60)) for window in windows)
                impossible = bool(week and deadline <= horizon and (capacity < required + plans[task_id]['focus_minutes'] or sum(due) > pooled_capacity))
                reason = (f"{task['title']}: свободного времени до жёсткого срока недостаточно для обязательного объёма."
                          if impossible else f"{task['title']}: полное завершение к жёсткому сроку в этом плане не обеспечено.")
                deadline_conflicts.append({'task_id': task_id, 'policy': policy, 'deadline': deadline.isoformat(),
                                           'reason': reason, 'infeasible': impossible,
                                           'remaining_work_minutes': max(0, required - assigned)})
    blocked = {item['task_id'] for item in deadline_conflicts}
    rewards = {}
    for task_id in order:
        plan = plans[task_id]
        if task_id not in blocked and plan['reward'] is not None:
            rewards[plan['reward_unit']] = rewards.get(plan['reward_unit'], 0) + plan['reward']
    focus_total = sum(block.get('focus_minutes', 0) for block in blocks if block['kind'] == 'task' and not block.get('permanent'))
    additional_focus = max(0, focus_total - sum(plans[task_id].get('focus_minutes', 0) for task_id in order if plans[task_id].get('work_max_minutes', plans[task_id]['max_minutes']) > 0))
    low += additional_focus
    high += additional_focus
    for task_id in order:
        repeated_focus = max(0, sum(block.get('focus_minutes', 0) for block in blocks if block.get('task_id') == task_id and not block.get('permanent')) - plans[task_id].get('focus_minutes', 0))
        effort[plans[task_id]['effort']] += repeated_focus
    if blocks:
        active = 0
        positions = {}
        for block in blocks:
            if block['kind'] != 'task' or block.get('permanent'):
                continue
            item = positions.setdefault(block['task_id'], {'start': active, 'end': active})
            active += block['minutes']
            item['end'] = active
        timeline = [{'task_id': task_id, 'start_active_minutes': positions.get(task_id, {}).get('start', active),
                     'end_active_minutes': positions.get(task_id, {}).get('end', active),
                     'expected_result': plans[task_id]['expected_result']} for task_id in order]
    remaining = [{'task_id': task_id, 'remaining_work_minutes': max(0, plans[task_id]['work_max_minutes'] -
                  sum(block['minutes'] - block.get('focus_minutes', 0) for block in blocks
                      if block['kind'] == 'task' and not block.get('permanent') and block.get('task_id') == task_id))}
                 for task_id in order]
    return {'focus_minutes': focus_total, 'task_ids': order, 'min_minutes': low, 'max_minutes': high,
            'wait_minutes': wait, 'effort_minutes': effort, 'potential_rewards': rewards,
            'timeline': timeline,
            'blocks': blocks, 'remaining': remaining, 'timeline_error': timeline_error, 'deadline_conflicts': deadline_conflicts,
            'start_at': now if week else None,
            'timezone': week['timezone'] if week else None,
            'rest_assumption': 'Личное расписание и временный режим Ameba.' if week else 'Условные дни: 6 ч работы, еда и сон. Это не личное расписание.',
            'note': 'Активное время без сна и еды. Результаты приблизительны; баллы не гарантированы. '
                    'Смена порядка сама по себе не уменьшает объём работы; для этого нужно уточнить планы.'}


def calculate(arguments, expected, completed=None):
    body = Plans.model_validate_json(arguments)
    result = {}
    for plan in body.plans:
        if plan.task_id not in expected or plan.task_id in result:
            raise ValueError(f'ID {plan.task_id} неизвестен или повторяется в вызове. Допустимые ID: {expected}')
        if any(step.min_minutes > step.max_minutes for step in plan.steps):
            raise ValueError(f'Задача {plan.task_id}: min_minutes каждого этапа должен быть не больше max_minutes')
        if plan.reward is not None and not plan.reward_unit.strip():
            raise ValueError(f'Задача {plan.task_id}: укажи reward_unit для числового reward либо reward=null, если выгода неизвестна')
        work_low = sum(step.min_minutes for step in plan.steps)
        work_high = sum(step.max_minutes for step in plan.steps)
        confirmed = (completed or {}).get(plan.task_id, 0)
        total_low, total_high = work_low, work_high
        work_low = max(0, work_low - confirmed)
        work_high = max(0, work_high - confirmed)
        low = work_low + (plan.focus_minutes if work_high else 0)
        high = work_high + (plan.focus_minutes if work_high else 0)
        wait = sum(step.wait_minutes for step in plan.steps)
        result[plan.task_id] = plan.model_dump() | {
            'work_min_minutes': work_low, 'work_max_minutes': work_high,
            'total_work_min_minutes': total_low, 'total_work_max_minutes': total_high, 'completed_work_minutes': confirmed,
            'min_minutes': low, 'max_minutes': high, 'wait_minutes': wait,
            'time_estimate': f'Примерно {low}–{high} мин с входом в задачу {plan.focus_minutes} мин; внешнее ожидание: {wait} мин.',
        }
    return result


class RestPattern(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['sleep', 'meal', 'break']
    title: str = Field(min_length=1, max_length=150)
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')


class RestPlan(BaseModel):
    model_config = {'extra': 'forbid'}
    patterns: list[RestPattern] = Field(max_length=100)
    assumption: str = Field(min_length=1, max_length=500)


REST_TOOL = {'type': 'function', 'name': 'plan_rest', 'strict': True,
    'description': 'Задать временные сон, еду и отдых для текущего плана Ameba. weekdays: 0=понедельник…6=воскресенье. Сон через полночь разделить на два блока. Повторный вызов заменяет только твои временные правила; после вызови reorder_tasks. Постоянный календарь не изменяется. assumption — основание выбора времени или явно обозначенное допущение.',
    'parameters': RestPlan.model_json_schema()}


def with_rest(week, now, arguments=None, timezone_name='UTC'):
    from copy import deepcopy
    from common.schedule import default_week
    configured = deepcopy(week) if week else default_week()
    if not week:
        configured['timezone'] = timezone_name
        for slots in configured['days']:
            for slot in slots:
                slot['temporary'] = True
    # Если владелец задал только занятия/занятость, рабочие окна — допущение плана.
    has_work = any(slot['kind'] == 'work' for day in configured['days'] for slot in day)
    has_work = has_work or any(event['kind'] == 'work' for event in configured.get('calendar_events', []))
    if not has_work:
        work = [slot for slot in default_week()['days'][0] if slot['kind'] == 'work']
        for day in configured['days']:
            day.extend(dict(slot, temporary=True) for slot in work)
    if arguments is None:
        return configured
    body = RestPlan.model_validate_json(arguments)
    configured['days'] = [[slot for slot in day if not slot.get('temporary') or slot['kind'] == 'work']
                          for day in configured['days']]
    for index, pattern in enumerate(body.patterns):
        if pattern.end <= pattern.start:
            raise ValueError(f'patterns.{index}.end: {pattern.start}–{pattern.end}; конец должен быть позже начала. '
                             'Интервал через полночь раздели на два: до 24:00 и с 00:00 следующего дня')
        if len(set(pattern.weekdays)) != len(pattern.weekdays):
            raise ValueError(f'patterns.{index}.weekdays: дни повторяются: {pattern.weekdays}; нужны уникальные значения 0..6')
        for day in pattern.weekdays:
            configured['days'][day].append({'kind': pattern.kind, 'title': pattern.title,
                'start': pattern.start, 'end': pattern.end, 'temporary': True})
    return validate_rest(configured, now)


def validate_rest(configured, now):
    start = datetime.fromisoformat(now).astimezone(ZoneInfo(configured['timezone'])).date()
    for offset in range(7):
        date = start + timedelta(days=offset)
        occupied = [slot for slot in day_slots(configured, date) if slot['kind'] != 'work']
        missing = {'sleep', 'meal'} - {slot['kind'] for slot in occupied}
        if missing:
            raise ValueError(f'{date.isoformat()}: добавь сон/еду: ' + ', '.join(sorted(missing)))
        for index, slot in enumerate(occupied):
            for other in occupied[:index]:
                if slot['start'] < other['end'] and slot['end'] > other['start'] and (slot.get('temporary') or other.get('temporary')):
                    raise ValueError(f'{date.isoformat()}: {slot["kind"]} «{slot.get("title", "") }» '
                                     f'{slot["start"]}–{slot["end"]} пересекается с '
                                     f'{other["kind"]} «{other.get("title", "") }» {other["start"]}–{other["end"]}. '
                                     'Измени временный отдых или задай исключение через plan_day; постоянный календарь менять нельзя')
    return configured


def restore_week(week, previous, now, timezone_name):
    configured = with_rest(week, now, timezone_name=timezone_name)
    patterns = previous.get('rest_patterns', [])
    if patterns:
        for day in configured['days']:
            day[:] = [slot for slot in day if not slot.get('temporary') or slot['kind'] == 'work']
        for pattern in patterns:
            for weekday in pattern['weekdays']:
                configured['days'][weekday].append(dict(pattern, temporary=True))
    configured['day_plans'] = previous.get('day_plans', {})
    return configured


class DaySlot(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['work', 'task', 'sleep', 'meal', 'break']
    title: str = Field(min_length=1, max_length=150)
    start: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')
    task_id: int | None = Field(description='ID задачи только для kind=task; для остальных null')


class DayPlan(BaseModel):
    model_config = {'extra': 'forbid'}
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    slots: list[DaySlot] = Field(max_length=48, description='Рабочие окна и необязательные изменения отдыха конкретной даты. Неуказанные виды отдыха сохраняются. Пустой список отменяет настройку этого дня.')
    reason: str = Field(min_length=1, max_length=500)


DAY_TOOL = {'type': 'function', 'name': 'plan_day', 'strict': True,
    'description': 'Настроить или пересобрать одну конкретную дату. Передай рабочие окна и при необходимости новый отдых. Неуказанные виды отдыха сохраняются из текущего плана дня. Можно расширить рабочие окна перед дедлайном и выделить разным задачам разные часы. task — именное рабочее окно с учётом концентрации; одна задача может иметь окна в разные дни. Постоянные события не меняются. После изменений вызови reorder_tasks.',
    'parameters': DayPlan.model_json_schema()}


def day_slots(week, day):
    override = week.get('day_plans', {}).get(day.isoformat())
    if override is None:
        return slots_for_date(week, day)
    # Постоянная занятость остаётся, а временные правила этого дня заменяются.
    daily = dict(week, days=list(week['days']),
                 calendar_events=[event for event in week.get('calendar_events', []) if event['kind'] != 'work'])
    daily['days'][day.weekday()] = [slot for slot in week['days'][day.weekday()]
                                  if not slot.get('temporary') and slot['kind'] != 'work'] + override
    return slots_for_date(daily, day)


def plan_day(arguments, week, expected, now):
    from copy import deepcopy
    body = DayPlan.model_validate_json(arguments)
    day = calendar_date.fromisoformat(body.date)
    today = datetime.fromisoformat(now).astimezone(ZoneInfo(week['timezone'])).date()
    if not 0 <= (day - today).days <= 6:
        raise ValueError('Выбери дату от сегодня до конца текущей недели (7 дней). Задачи на более поздний срок не размещай в таймлайн.')
    configured = deepcopy(week)
    overrides = configured.setdefault('day_plans', {})
    if not body.slots:
        overrides.pop(body.date, None)
        return configured, {'date': body.date, 'status': 'reset'}
    slots = []
    # При изменении рабочих часов не заставляем модель заново описывать сон и еду.
    # Переданный вид отдыха заменяется целиком; остальные берём из текущего дня.
    changed_rest = {item.kind for item in body.slots if item.kind in ('sleep', 'meal', 'break')}
    for slot in day_slots(week, day):
        if slot.get('temporary') and slot['kind'] in ('sleep', 'meal', 'break') and slot['kind'] not in changed_rest:
            slots.append(dict(slot))
    for item in body.slots:
        if item.end <= item.start:
            raise ValueError('Нужен end > start; сон через полночь раздели на два дня')
        if (item.kind == 'task' and item.task_id not in expected) or (item.kind != 'task' and item.task_id is not None):
            raise ValueError('Для task укажи ID из задач, для остальных task_id=null')
        if any(item.start < slot['end'] and item.end > slot['start']
               and ((item.kind in ('work', 'task')) == (slot['kind'] == 'work')) for slot in slots):
            raise ValueError('Интервалы временного дня не должны пересекаться')
        slots.append(item.model_dump() | {'kind': 'work' if item.kind == 'task' else item.kind, 'temporary': True})
    overrides[body.date] = slots
    effective = day_slots(configured, day)
    occupied = [slot for slot in effective if slot['kind'] != 'work']
    for index, slot in enumerate(occupied):
        for other in occupied[:index]:
            if slot['start'] < other['end'] and slot['end'] > other['start'] and (slot.get('temporary') or other.get('temporary')):
                raise ValueError('Сон/еда/отдых пересекаются с постоянной занятостью или друг с другом')
    if not {'sleep', 'meal'} <= {slot['kind'] for slot in occupied}:
        raise ValueError('В полном плане дня должны быть сон и еда с учётом постоянного календаря')
    work = [slot for slot in effective if slot['kind'] == 'work']
    work_minutes = sum((int(slot['end'][:2]) * 60 + int(slot['end'][3:]))
                       - (int(slot['start'][:2]) * 60 + int(slot['start'][3:])) for slot in work)
    return configured, {'date': body.date, 'work_minutes': work_minutes, 'work_windows': work,
                        'notice': 'План дня сохранён временно. Теперь пересчитай reorder_tasks и проверь дедлайны.'}
