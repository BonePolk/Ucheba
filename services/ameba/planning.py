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
    available_from: datetime | None = Field(description='Самое раннее начало выполнения из контекста: дата/время открытия контрольной, выдачи задания или доступа. ISO 8601 с часовым поясом; если известна только дата — местная полночь этой даты. null только при отсутствии ограничения. Открытие не является дедлайном; не планируй выполнение до него.')
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
    'description': 'Задать смысловой порядок всех задач. Python сам разместит работу с учётом жёстких сроков, нагрузки, режима и подходящих прошлых блоков. Возвращает проверенный план, остаток и конфликты. После изменений оценок/режима вызови снова.',
    'parameters': Order.model_json_schema(),
}


def hard_deadlines(plans, tasks):
    result = {}
    for task in tasks:
        if task.get('deadline') and plans[task['id']].get('deadline_policy', 'soft') != 'soft':
            raw = task['deadline']
            value = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
            result[task['id']] = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return result


def available_from(plan, timezone_name):
    raw = plan.get('available_from')
    if raw is None:
        return None
    value = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
    return value.replace(tzinfo=ZoneInfo(timezone_name)) if value.tzinfo is None else value


class LoadLimits(BaseModel):
    model_config = {'extra': 'forbid'}
    max_session_minutes: int = Field(ge=1)
    break_minutes: int = Field(ge=1)
    max_daily_minutes: int = Field(ge=1)


def load_limits(week):
    return {'max_session_minutes': 90, 'break_minutes': 15, 'max_daily_minutes': 360} | (week.get('load_limits') or {})


def validate_load(blocks, week, now):
    limits = load_limits(week)
    origin = datetime.fromisoformat(now)
    zone = ZoneInfo(week['timezone'])
    daily = {}
    streak = 0
    previous_end = None
    for block in sorted(blocks, key=lambda item: item['start']):
        if block['kind'] != 'task' or block.get('permanent'):
            continue
        day = (origin + timedelta(minutes=block['start'])).astimezone(zone).date()
        daily[day] = daily.get(day, 0) + block['minutes']
        if previous_end is None or block['start'] - previous_end >= limits['break_minutes']:
            streak = 0
        streak += block['minutes']
        if streak > limits['max_session_minutes'] or daily[day] > limits['max_daily_minutes']:
            raise ValueError(f'{day}: превышена согласованная нагрузка с учётом концентрации и перерывов')
        previous_end = block['start'] + block['minutes']


def capacity_before(windows, deadline, daily_limit, not_before=None):
    daily = {}
    for window in windows:
        start = datetime.fromisoformat(window['start_at'])
        if not_before is not None:
            start = max(start, not_before)
        minutes = max(0, int((min(deadline, datetime.fromisoformat(window['end_at'])) - start).total_seconds() / 60))
        daily[window['date']] = min(daily_limit, daily.get(window['date'], 0) + minutes)
    return sum(daily.values())


def allocate(order, plans, week, now, kept, deadlines, tasks):
    """Жёсткие сроки — раньше; остальная работа — сессиями по кругу и по нагрузке дней."""
    from uuid import uuid4
    origin = datetime.fromisoformat(now)
    zone = ZoneInfo(week['timezone'])
    limits = load_limits(week)
    blocks = list(kept)
    openings = {task_id: available_from(plans[task_id], week['timezone']) for task_id in order}
    remaining = {task_id: plans[task_id]['work_max_minutes'] - sum(
        block['minutes'] - block['focus_minutes'] for block in blocks if block['task_id'] == task_id) for task_id in order}
    windows = free_windows(week, now)
    reserved = {slot['task_id'] for slots in week.get('day_plans', {}).values() for slot in slots if slot.get('task_id') is not None}
    capacities = {}
    for window in windows:
        capacities[window['date']] = min(limits['max_daily_minutes'], capacities.get(window['date'], 0) + window['minutes'])

    def add_session(task_id, urgent):
        if remaining[task_id] <= 0:
            return False
        daily = {}
        for block in blocks:
            day = (origin + timedelta(minutes=block['start'])).astimezone(zone).date().isoformat()
            daily[day] = daily.get(day, 0) + block['minutes']
        focus = plans[task_id]['focus_minutes']
        raw_deadline = next((task.get('deadline') for task in tasks if task['id'] == task_id), None)
        soft_deadline = datetime.fromisoformat(raw_deadline.replace('Z', '+00:00')) if isinstance(raw_deadline, str) else raw_deadline
        if soft_deadline is not None and soft_deadline.tzinfo is None:
            soft_deadline = soft_deadline.replace(tzinfo=timezone.utc)
        candidates = []
        for window in free_windows(week, now, blocks, gap=limits['break_minutes']):
            if window['task_id'] not in (None, task_id) or task_id in reserved and window['task_id'] != task_id:
                continue
            left, right = datetime.fromisoformat(window['start_at']), datetime.fromisoformat(window['end_at'])
            if openings[task_id] is not None:
                left = max(left, openings[task_id])
            right = min(right, deadlines.get(task_id, right))
            budget = min(int((right - left).total_seconds() / 60), limits['max_session_minutes'],
                         limits['max_daily_minutes'] - daily.get(window['date'], 0))
            useful = min(remaining[task_id], budget - focus)
            if useful < min(30, remaining[task_id], limits['max_session_minutes'] - focus, limits['max_daily_minutes'] - focus) or useful <= 0:
                continue
            immediate = urgent and deadlines[task_id].astimezone(zone).date() == origin.astimezone(zone).date()
            # Небольшую работу можно закончить сегодня; длинную распределяем порциями нагрузки по дням.
            score = (left,) if immediate else (
                bool(soft_deadline and left + timedelta(minutes=focus + useful) > soft_deadline),
                int(daily.get(window['date'], 0) / max(1, min(capacities[window['date']], 2 * limits['max_session_minutes']))), left)
            candidates.append((score, left, useful))
        if not candidates:
            return False
        _, left, useful = min(candidates)
        blocks.append({'id': uuid4().hex, 'kind': 'task', 'task_id': task_id, 'title': '', 'permanent': False,
                       'start': int((left - origin).total_seconds() / 60), 'minutes': focus + useful, 'focus_minutes': focus})
        remaining[task_id] -= useful
        return True

    # Не отдаём всю неделю заведомо невыполнимой задаче в ущерб достижимым срокам.
    impossible = {task_id for task_id, deadline in deadlines.items() if remaining[task_id] + plans[task_id]['focus_minutes'] > capacity_before(
        [window for window in windows if window['task_id'] in (None, task_id)], deadline, limits['max_daily_minutes'], openings[task_id])}
    horizon = datetime.combine(origin.astimezone(zone).date() + timedelta(days=7), time(), zone)
    urgent = sorted((task_id for task_id in deadlines if deadlines[task_id] <= horizon and task_id not in impossible), key=lambda value: (deadlines[value], order.index(value)))
    for task_id in urgent:
        while add_session(task_id, True):
            pass
    flexible = [task_id for task_id in order if task_id not in urgent]
    while True:
        changed = False
        for task_id in flexible:
            changed = add_session(task_id, task_id in deadlines) or changed
        if not changed:
            break
    return sorted(blocks, key=lambda item: item['start'])


def weekly_timeline(order, plans, week, now, previous=None, tasks=()):
    import blocks as block_plan
    draft = block_plan.initial(previous, order, now, week['timezone'])
    kept = []
    for old in draft['blocks']:
        task_id = old['task_id']
        focus = plans[task_id]['focus_minutes']
        left = plans[task_id]['work_max_minutes'] - sum(b['minutes'] - b['focus_minutes'] for b in kept if b['task_id'] == task_id)
        block = old | {'focus_minutes': focus, 'minutes': min(old['minutes'], focus + left)}
        try:
            block_plan.validate(draft | {'blocks': kept + [block]}, week, plans, tasks, now)
        except ValueError:
            continue  # Изменённый календарь/объём/нагрузка может сделать прежний блок непригодным.
        kept.append(block)
    deadlines = hard_deadlines(plans, tasks)
    placed = allocate(order, plans, week, draft['start_at'], kept, deadlines, tasks)

    horizon = datetime.combine(datetime.fromisoformat(now).astimezone(ZoneInfo(week['timezone'])).date() + timedelta(days=7), time(), ZoneInfo(week['timezone']))

    def missed_hard(blocks):
        missing = [item['remaining_work_minutes'] for item in block_plan.remaining(draft | {'blocks': blocks}, plans, order)
                   if item['task_id'] in deadlines and deadlines[item['task_id']] <= horizon]
        return sum(value > 0 for value in missing), sum(missing)

    # Старые часы — предпочтение. Освобождаем их только ради улучшения жёстких сроков.
    if kept and missed_hard(placed)[0]:
        missing = [deadlines[item['task_id']] for item in block_plan.remaining(draft | {'blocks': placed}, plans, order)
                   if item['remaining_work_minutes'] and item['task_id'] in deadlines and deadlines[item['task_id']] <= horizon]
        zone = ZoneInfo(week['timezone'])
        release_before = datetime.combine(max(missing).astimezone(zone).date() + timedelta(days=1), time(), zone)
        origin = datetime.fromisoformat(draft['start_at'])
        unaffected = [block for block in kept if origin + timedelta(minutes=block['start']) >= release_before
                      or block['task_id'] in deadlines and deadlines[block['task_id']] < min(missing)]
        released = allocate(order, plans, week, draft['start_at'], unaffected, deadlines, tasks)
        if missed_hard(released) < missed_hard(placed):
            placed = released
    return placed


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


def free_windows(week, now, blocks=(), gap=0):
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
                left = origin + timedelta(minutes=block['start'] - gap)
                right = origin + timedelta(minutes=block['start'] + block['minutes'] + gap)
                pieces = [(a, b) for start, end in pieces for a, b in
                          ((start, min(end, left)), (max(start, right), end)) if b > a]
            for left, right in pieces:
                if right > left:
                    result.append({'date': day.isoformat(), 'start': left.astimezone(zone).strftime('%H:%M'),
                                   'end': '24:00' if right == midnight + timedelta(days=1) else right.astimezone(zone).strftime('%H:%M'),
                                   'start_at': left.isoformat(), 'end_at': right.isoformat(),
                                   'minutes': int((right - left).total_seconds() / 60), 'task_id': slot.get('task_id')})
    return result


def reorder(arguments, plans, expected, week, now, previous=None, tasks=()):
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
    import blocks as block_plan
    validate_rest(week, now)
    deadline_conflicts = []
    tasks_by_id = {task['id']: task for task in tasks}
    limits = load_limits(week)
    if any(plan['work_max_minutes'] > 0 and plan['focus_minutes'] >= min(limits['max_session_minutes'], limits['max_daily_minutes']) for plan in plans.values()):
        raise ValueError('Лимиты сессии и дня должны вмещать концентрацию и полезную работу. Уточни реальные ограничения.')
    placed = weekly_timeline(order, plans, week, now, previous if request.keep_previous_times else None, tasks)
    draft = {'start_at': now, 'timezone': week['timezone'], 'blocks': placed}
    block_plan.validate(draft, week, plans, tasks, now)
    blocks = block_plan.calendar_blocks(draft, week)
    validate_blocks(blocks, week, now)
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
                capacity = capacity_before([window for window in windows if window['task_id'] in (None, task_id)], deadline, limits['max_daily_minutes'], available_from(plans[task_id], week['timezone']))
                due = [plans[other_id]['work_max_minutes'] + plans[other_id]['focus_minutes']
                       for other_id, due_at in deadlines.items()
                       if due_at <= deadline and plans[other_id]['work_max_minutes'] > 0]
                pooled_capacity = capacity_before(windows, deadline, limits['max_daily_minutes'])
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
            'blocks': blocks, 'remaining': remaining, 'timeline_error': None, 'deadline_conflicts': deadline_conflicts,
            'start_at': now if week else None,
            'timezone': week['timezone'] if week else None,
            'rest_assumption': week.get('load_assumption', 'Допущение: сессия до 90 мин, перерыв от 15 мин, нагрузка до 360 мин в день, включая концентрацию.'),
            'load_limits': load_limits(week),
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
        result[plan.task_id] = plan.model_dump(mode='json') | {
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
    load_limits: LoadLimits | None = Field(description='Числовые ограничения из настроек/памяти владельца. null — явно обозначенные допущения: сессия 90, перерыв 15, день 360 минут с концентрацией.')


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
    configured['work_restricted'] = any(slot['kind'] == 'work' and not slot.get('temporary') for day in configured['days'] for slot in day) or any(event['kind'] == 'work' for event in configured.get('calendar_events', []))
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
    if body.load_limits is not None:
        configured['load_limits'] = body.load_limits.model_dump()
        configured['load_assumption'] = body.assumption
    elif not configured.get('load_limits'):
        configured['load_assumption'] = body.assumption + ' Допущение нагрузки: сессия до 90 мин, перерыв от 15 мин, день до 360 мин с концентрацией.'
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
    configured['approved_work_windows'] = previous.get('approved_work_windows', {})
    configured['load_limits'] = previous.get('load_limits', {})
    if previous.get('load_limits') and previous.get('schedule', {}).get('rest_assumption'):
        configured['load_assumption'] = previous['schedule']['rest_assumption']
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
    'description': 'Настроить или пересобрать одну конкретную дату. Передай рабочие окна и при необходимости новый отдых. Неуказанные виды отдыха сохраняются из текущего плана дня. Выход за рабочие границы владельца потребует его подтверждения через приложение. Можно ограничить окна предпочтениями и выделить разным задачам разные часы. task — именное рабочее окно с учётом концентрации; одна задача может иметь окна в разные дни. Постоянные события не меняются. После изменений вызови reorder_tasks.',
    'parameters': DayPlan.model_json_schema()}


def work_bounds(week, day):
    working = dict(week, days=[[slot for slot in slots if slot['kind'] == 'work'] for slots in week['days']],
                   events=[slot for slot in week.get('events', []) if slot['kind'] == 'work'],
                   calendar_events=[slot for slot in week.get('calendar_events', []) if slot['kind'] == 'work'])
    intervals = [(slot['start'], slot['end']) for slot in slots_for_date(working, day)]
    intervals += [(slot['start'], slot['end']) for slot in week.get('approved_work_windows', {}).get(day.isoformat(), [])]
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def day_slots(week, day):
    override = week.get('day_plans', {}).get(day.isoformat())
    if override is None:
        return slots_for_date(week, day)
    # Постоянная занятость остаётся, а временные правила этого дня заменяются.
    daily = dict(week, days=list(week['days']),
                 calendar_events=[event for event in week.get('calendar_events', []) if event['kind'] != 'work'])
    daily['days'][day.weekday()] = [slot for slot in week['days'][day.weekday()]
                                  if not slot.get('temporary') and slot['kind'] != 'work'] + override
    effective = slots_for_date(daily, day)
    if week.get('work_restricted'):
        bounds = work_bounds(week, day)
        effective = [slot for slot in effective if slot['kind'] != 'work'] + [
            slot | {'start': max(slot['start'], start), 'end': min(slot['end'], end)}
            for slot in effective if slot['kind'] == 'work' for start, end in bounds
            if slot['start'] < end and slot['end'] > start]
    return sorted(effective, key=lambda item: item['start'])


def plan_day(arguments, week, expected, now, confirmed=False):
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
        configured.setdefault('approved_work_windows', {}).pop(body.date, None)
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
    work = [slot for slot in slots if slot['kind'] == 'work']
    if week.get('work_restricted') and any(not any(start <= slot['start'] and end >= slot['end']
            for start, end in work_bounds(week, day)) for slot in work):
        if not confirmed:
            return week, {'status': 'needs_confirmation', 'date': body.date, 'work_windows': work}
        configured.setdefault('approved_work_windows', {})[body.date] = work
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
