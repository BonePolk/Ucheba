"""Чтение, проверка и отображение блоков, рассчитанных Python."""
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

import planning


READ_TOOL = {'type': 'function', 'name': 'read_current_plan', 'strict': True,
    'description': 'Прочитать весь текущий черновик: все блоки задач, недельный режим, постоянный календарь, исключения по датам и остаток работы. Размещение выполняет reorder_tasks.',
    'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}}


def initial(previous, expected, now, timezone_name):
    clock = datetime.fromisoformat(now).astimezone(timezone.utc)
    origin = clock.replace(second=0, microsecond=0)
    if origin < clock:
        origin += timedelta(minutes=1)
    result = {'start_at': origin.isoformat(), 'timezone': timezone_name, 'blocks': []}
    if not previous or not previous.get('start_at'):
        return result
    old_origin = datetime.fromisoformat(previous['start_at'])
    for block in previous.get('blocks', []):
        if block['kind'] != 'task' or block.get('permanent') or block.get('task_id') not in expected:
            continue
        left = old_origin + timedelta(minutes=block['start'])
        if left >= origin and (left.astimezone(ZoneInfo(timezone_name)).date() - origin.astimezone(ZoneInfo(timezone_name)).date()).days < 7:
            result['blocks'].append(block | {'start': int((left - origin).total_seconds() / 60)})
    return result


def remaining(draft, plans, expected):
    result = []
    for task_id in expected:
        if task_id not in plans:
            result.append({'task_id': task_id, 'needs_estimate': True})
            continue
        placed = sum(block['minutes'] - plans[task_id]['focus_minutes'] for block in draft['blocks'] if block['task_id'] == task_id)
        required = plans[task_id]['work_max_minutes']
        entry = {'task_id': task_id, 'remaining_work_minutes': max(0, required - placed),
                 'excess_work_minutes': max(0, placed - required)}
        if not placed and required > 0:
            entry['unscheduled'] = True
        result.append(entry)
    return result


def read(draft, week, plans, expected):
    return {'schedule': draft, 'weekly_schedule': week, 'free_windows': planning.free_windows(week, draft['start_at'], draft['blocks']), 'remaining': remaining(draft, plans, expected)}


def validate(draft, week, plans, tasks, now):
    origin = datetime.fromisoformat(draft['start_at'])
    zone = ZoneInfo(week['timezone'])
    tasks_by_id = {task['id']: task for task in tasks}
    previous_end = None
    for block in sorted(draft['blocks'], key=lambda value: value['start']):
        task_id = block['task_id']
        plan = plans[task_id]
        left = origin + timedelta(minutes=block['start'])
        right = left + timedelta(minutes=block['minutes'])
        label = f'Блок {block["id"]}, задача {task_id}'
        opening = planning.available_from(plan, week['timezone'])
        if opening is not None and left < opening:
            raise ValueError(label + ': выполнение ещё недоступно; блок начинается до открытия задания')
        if left < datetime.fromisoformat(now):
            raise ValueError(label + ': начало уже в прошлом; перенеси блок')
        if previous_end is not None and left < previous_end:
            raise ValueError(label + ': пересекается с предыдущей задачей')
        if block['minutes'] <= plan['focus_minutes']:
            raise ValueError(label + ': нужно время на полезную работу после концентрации')
        previous_end = right
        day = left.astimezone(zone).date()
        midnight = datetime.combine(day, time(), zone)
        fits = False
        for slot in planning.day_slots(week, day):
            if slot['kind'] != 'work' or slot.get('task_id') not in (None, task_id):
                continue
            sh, sm = map(int, slot['start'].split(':'))
            eh, em = map(int, slot['end'].split(':'))
            if left >= (midnight + timedelta(hours=sh, minutes=sm)).astimezone(timezone.utc) and right <= (midnight + timedelta(hours=eh, minutes=em)).astimezone(timezone.utc):
                fits = True
                break
        if not fits:
            raise ValueError(label + ': не помещается в свободное рабочее окно; прочитай план или измени режим даты через plan_day')
        raw = tasks_by_id.get(task_id, {}).get('deadline')
        if raw and plan.get('deadline_policy', 'soft') != 'soft':
            deadline = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if right > deadline:
                raise ValueError(label + ': заканчивается после жёсткого дедлайна')
    planning.validate_load(draft['blocks'], week, draft['start_at'])
    for item in remaining(draft, plans, list(plans)):
        if item.get('excess_work_minutes'):
            raise ValueError(f'Задача {item["task_id"]}: запланировано лишних {item["excess_work_minutes"]} минут; удали/сократи блоки или обоснованно уточни оценку')


def calendar_blocks(draft, week):
    """Для интерфейса дополняем задачи отдыхом и постоянным календарём."""
    origin = datetime.fromisoformat(draft['start_at'])
    zone = ZoneInfo(week['timezone'])
    first = origin.astimezone(zone).date()
    result = list(draft['blocks'])
    for offset in range(7):
        day = first + timedelta(days=offset)
        midnight = datetime.combine(day, time(), zone)
        for index, slot in enumerate(planning.day_slots(week, day)):
            if slot['kind'] == 'work':
                continue
            sh, sm = map(int, slot['start'].split(':'))
            eh, em = map(int, slot['end'].split(':'))
            left = max(origin, (midnight + timedelta(hours=sh, minutes=sm)).astimezone(timezone.utc))
            right = (midnight + timedelta(hours=eh, minutes=em)).astimezone(timezone.utc)
            if right <= left:
                continue
            result.append({'id': f'calendar-{day}-{index}', 'kind': slot['kind'], 'task_id': slot.get('task_id'),
                           'title': slot.get('title', ''), 'permanent': bool(slot.get('id')), 'focus_minutes': 0,
                           'start': int((left - origin).total_seconds() / 60), 'minutes': int((right - left).total_seconds() / 60)})
    return sorted(result, key=lambda value: value['start'])
