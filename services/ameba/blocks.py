"""Черновик точных блоков задач. Каждый вызов меняет только указанные блоки."""
from datetime import datetime, timedelta, timezone, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

import planning


class TaskBlock(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    start: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')


class Edit(BaseModel):
    model_config = {'extra': 'forbid'}
    remove_ids: list[str] = Field(description='ID временных блоков задач для удаления/переноса. Постоянные события удалить нельзя.')
    add: list[TaskBlock] = Field(description='Новые блоки с точными датами и часами. Для переноса удали прежний ID и добавь новый блок одним вызовом.')


READ_TOOL = {'type': 'function', 'name': 'read_current_plan', 'strict': True,
    'description': 'Прочитать весь текущий черновик: все блоки задач, недельный режим, постоянный календарь, исключения по датам и остаток работы. Используй перед вставкой новых блоков.',
    'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}}
EDIT_TOOL = {'type': 'function', 'name': 'edit_task_blocks', 'strict': True,
    'description': 'Вставить блоки задач в текущий план или перенести выбранные блоки. Остальной план сохраняется. Время блока включает концентрацию. Проверяет рабочие окна, отдых, пересечения, объём работы и жёсткие сроки. При ошибке ничего не меняет. Возвращает только добавленные/удалённые блоки и остаток работы. Повторяй с исправленными часами до готовности.',
    'parameters': Edit.model_json_schema()}


def initial(previous, expected, now, timezone_name):
    origin = datetime.fromisoformat(now).astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    result = {'start_at': origin.isoformat(), 'timezone': timezone_name, 'blocks': []}
    if not previous or not previous.get('start_at'):
        return result
    old_origin = datetime.fromisoformat(previous['start_at'])
    for block in previous.get('blocks', []):
        if block['kind'] != 'task' or block.get('permanent') or block.get('task_id') not in expected:
            continue
        left = old_origin + timedelta(minutes=block['start'])
        if left >= origin:
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
    return {'schedule': draft, 'weekly_schedule': week, 'remaining': remaining(draft, plans, expected)}


def validate(draft, week, plans, tasks, now):
    origin = datetime.fromisoformat(draft['start_at'])
    zone = ZoneInfo(week['timezone'])
    tasks_by_id = {task['id']: task for task in tasks}
    previous_end = None
    streak = 0
    for block in sorted(draft['blocks'], key=lambda value: value['start']):
        task_id = block['task_id']
        plan = plans[task_id]
        left = origin + timedelta(minutes=block['start'])
        right = left + timedelta(minutes=block['minutes'])
        label = f'Блок {block["id"]}, задача {task_id}'
        if left < datetime.fromisoformat(now):
            raise ValueError(label + ': начало уже в прошлом; перенеси блок')
        if previous_end is not None and left < previous_end:
            raise ValueError(label + ': пересекается с предыдущей задачей')
        if previous_end is None or left - previous_end >= timedelta(minutes=15):
            streak = 0
        streak += block['minutes']
        if block['minutes'] <= plan['focus_minutes'] or streak > 90:
            raise ValueError(label + ': нужно время на полезную работу после концентрации и отдых не менее 15 минут после 90 минут нагрузки')
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
        raw = tasks_by_id[task_id].get('deadline')
        if raw and plan.get('deadline_policy', 'soft') != 'soft':
            deadline = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if right > deadline:
                raise ValueError(label + ': заканчивается после жёсткого дедлайна')
    for item in remaining(draft, plans, list(plans)):
        if item.get('excess_work_minutes'):
            raise ValueError(f'Задача {item["task_id"]}: запланировано лишних {item["excess_work_minutes"]} минут; удали/сократи блоки или обоснованно уточни оценку')


def edit(arguments, draft, week, plans, expected, tasks, now):
    body = Edit.model_validate_json(arguments)
    if set(plans) != set(expected):
        raise ValueError('Сначала оцени все задачи через plan_tasks')
    known = {block['id'] for block in draft['blocks']}
    if len(set(body.remove_ids)) != len(body.remove_ids) or set(body.remove_ids) - known:
        raise ValueError('remove_ids: нужны уникальные ID существующих временных блоков задач')
    candidate = dict(draft, blocks=[block for block in draft['blocks'] if block['id'] not in body.remove_ids])
    origin = datetime.fromisoformat(draft['start_at'])
    zone = ZoneInfo(week['timezone'])
    added = []
    for item in body.add:
        if item.task_id not in expected or item.end <= item.start:
            raise ValueError('add: неизвестный task_id или end <= start; блок через полночь раздели')
        midnight = datetime.combine(datetime.fromisoformat(item.date).date(), time(), zone)
        if not 0 <= (midnight.date() - origin.astimezone(zone).date()).days <= 6:
            raise ValueError('add.date: выбери дату от начала текущего плана до конца текущей недели (7 дней). Задачи на более поздний срок не размещай в таймлайн.')
        sh, sm = map(int, item.start.split(':'))
        eh, em = map(int, item.end.split(':'))
        left = (midnight + timedelta(hours=sh, minutes=sm)).astimezone(timezone.utc)
        right = (midnight + timedelta(hours=eh, minutes=em)).astimezone(timezone.utc)
        block = {'id': uuid4().hex, 'kind': 'task', 'task_id': item.task_id, 'permanent': False,
                 'start': int((left - origin).total_seconds() / 60), 'minutes': int((right - left).total_seconds() / 60),
                 'focus_minutes': plans[item.task_id]['focus_minutes'], 'title': ''}
        candidate['blocks'].append(block)
        added.append(block)
    candidate['blocks'].sort(key=lambda value: value['start'])
    validate(candidate, week, plans, tasks, now)
    return candidate, {'added_blocks': added, 'removed_ids': body.remove_ids,
                       'remaining': remaining(candidate, plans, expected)}


def calendar_blocks(draft, week):
    """Для интерфейса дополняем задачи отдыхом и постоянным календарём."""
    origin = datetime.fromisoformat(draft['start_at'])
    zone = ZoneInfo(week['timezone'])
    first = origin.astimezone(zone).date()
    last = max((origin + timedelta(minutes=b['start'] + b['minutes']) for b in draft['blocks']), default=origin).astimezone(zone).date()
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
