"""Ядро планирования Ameba: календарная ёмкость, валидация предложений LLM, материализация и /refresh."""
from __future__ import annotations

from copy import deepcopy
from datetime import date as calendar_date, datetime, timedelta, timezone
import json
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from common.calendar import occurs
from common.schedule import week_parity

MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)
WEEKDAYS_SHORT = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
MARKER_KEYWORDS = ('дедлайн', 'срок', 'сдача', 'сдать', 'напоминан', 'открыт', 'deadline', 'due', 'reminder')
UNAVAILABLE_KEYWORDS = ('отпуск', 'поездк', 'отъезд', 'больнич', 'конференц', 'экскурс', 'дежурств', 'смена', 'выходной')
COMMUTE_KEYWORDS = ('дорог', 'путь', 'метро', 'транспорт', 'поездка туда')

STYLE_LIMITS = {
    'gentle': (270, 480),
    'balanced': (360, 540),
    'intense': (420, 600),
}


# --- Схема инструмента propose_plan (strict=True для Responses/MCP: без default, extra='forbid') ---

class DayAllocation(BaseModel):
    model_config = {'extra': 'forbid'}
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    minutes: int = Field(gt=0, le=600)


class TaskAssessment(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    total_minutes_min: int = Field(
        ge=0, le=10000,
        description='Полный реалистичный минимум активной работы с нуля (Python сам вычтет completed_work_minutes).',
    )
    total_minutes_max: int = Field(
        ge=0, le=10000,
        description='Полный реалистичный максимум активной работы с нуля.',
    )
    wait_minutes: int = Field(ge=0, le=100000, description='Внешнее ожидание (ответы, проверки и т.п.), не занимающее внимание.')
    splittable: bool = Field(description='true — задачу разумно делать постепенно; false — требует одного цельного подхода.')
    deadline_policy: Literal['soft', 'finish_before', 'expires'] = Field(
        description='soft — мягкий срок; finish_before — важно успеть к сроку; expires — после срока выполнение невозможно.',
    )
    available_from: datetime | None = Field(
        description='Дата/время открытия задания из контекста (ISO 8601 с поясом) либо null.',
    )
    depends_on: list[int] = Field(description='ID задач из списка, которые нужно выполнить раньше этой; [] если нет.')
    effort: Literal['low', 'medium', 'high'] = Field(description='Уровень требуемого внимания и нагрузки.')
    reason: str = Field(min_length=1, max_length=200, description='Краткая причина места задачи в приоритетах.')
    impact: str = Field(min_length=1, max_length=180, description='Конкретная польза или результат выполнения.')
    deadline_risk: str | None = Field(
        max_length=240,
        description='Причина риска не успеть в срок или незапланированного остатка; иначе null.',
    )


class PlanPeriod(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['work', 'rest']
    task_id: int | None = Field(description='ID задачи для kind=work; обязательно null для kind=rest.')
    start_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    allocations: list[DayAllocation] = Field(
        description='Расчётная нагрузка по выбранным датам внутри периода (непусто для work, [] для rest).',
    )
    goal: str = Field(min_length=1, max_length=180, description='Что сделать за период или характер свободного дня.')
    context_hint: str | None = Field(
        max_length=220,
        description='Практичная привязка к расписанию или контексту без выдумки фактов; иначе null.',
    )
    reason: str | None = Field(
        max_length=260,
        description='Краткое объяснение выбора именно этих дней и объёма.',
    )


class PreferenceItem(BaseModel):
    model_config = {'extra': 'forbid'}
    text: str = Field(min_length=1, max_length=400)
    scope: Literal['once', 'task', 'persistent'] = Field(
        description='once — только текущий расчёт; task — контекст конкретной задачи; persistent — устойчивая привычка.',
    )
    task_id: int | None = Field(description='ID задачи для scope=task; иначе null.')
    key: str | None = Field(max_length=100, description='Короткий латинский ключ для persistent (вида ameba.<тема>) либо null.')


class ProgressUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    operation: Literal['add', 'set'] = Field(
        description='add — прибавить к текущему completed_work_minutes; set — явно заменить суммарный прогресс только если владелец указал новое итоговое значение.',
    )
    minutes: int = Field(
        ge=0, le=10000,
        description='Минуты для операции add (>0) или итоговое значение для set (>=0) только по явному сообщению владельца в user_feedback.',
    )


class PlanProposal(BaseModel):
    model_config = {'extra': 'forbid'}
    task_ids: list[int] = Field(description='Приоритетный порядок всех актуальных задач.')
    tasks: list[TaskAssessment] = Field(min_length=1)
    periods: list[PlanPeriod] = Field(description='Гибкие периоды работы и при необходимости дни отдыха.')
    preferences: list[PreferenceItem] = Field(description='Извлечённые из user_feedback пожелания с областью действия.')
    progress_updates: list[ProgressUpdate] = Field(description='Обновления выполненного времени из явных слов владельца.')
    explanation: str = Field(min_length=1, max_length=400, description='Короткое резюме плана и главных компромиссов.')


class PeriodReplacement(BaseModel):
    model_config = {'extra': 'forbid'}
    index: int = Field(ge=0, description='Индекс заменяемого периода в списке periods из предыдущего предложения.')
    period: PlanPeriod


class TaskReplacement(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    assessment: TaskAssessment


class PlanRepair(BaseModel):
    model_config = {'extra': 'forbid'}
    task_ids: list[int] | None = Field(
        description='Новый приоритетный порядок всех задач либо null (оставить прежний).',
    )
    task_replacements: list[TaskReplacement] = Field(
        description='Точечные замены или добавления оценок задач по task_id; [] если не меняются.',
    )
    period_replacements: list[PeriodReplacement] = Field(
        description='Точечные замены периодов по index; [] если не меняются.',
    )
    remove_period_indices: list[int] = Field(
        description='Индексы периодов для удаления; [] если удалять не нужно.',
    )
    add_periods: list[PlanPeriod] = Field(
        description='Новые добавляемые периоды; [] если добавлять не нужно.',
    )
    preferences: list[PreferenceItem] | None = Field(
        description='Новый список пожеланий либо null (оставить прежний); [] — намеренно очистить.',
    )
    progress_updates: list[ProgressUpdate] | None = Field(
        description='Новый список обновлений прогресса либо null (оставить прежний); [] — намеренно очистить.',
    )
    explanation: str | None = Field(
        max_length=400,
        description='Новое короткое резюме плана либо null (оставить прежнее).',
    )


PROPOSE_TOOL = {
    'type': 'function',
    'name': 'propose_plan',
    'strict': True,
    'description': (
        'Предложить и проверить полный интеллектуальный план: оценки и приоритеты всех задач, гибкие периоды работы и отдыха, '
        'привязки к контексту, область действия пожеланий (once/task/persistent) и подтверждённый прогресс.'
    ),
    'parameters': PlanProposal.model_json_schema(),
}

REPAIR_TOOL = {
    'type': 'function',
    'name': 'repair_plan',
    'strict': True,
    'description': (
        'Точечно исправить ранее предложенный план без повторной отправки всех задач, оценок и периодов. '
        'Поля со значением null сохраняют значения предыдущего предложения; пустые списки замен/добавлений ничего не меняют.'
    ),
    'parameters': PlanRepair.model_json_schema(),
}


def apply_progress(old_minutes: int, update: ProgressUpdate | dict) -> int:
    """Безопасно вычисляет обновлённый подтверждённый прогресс по операции add/set."""
    op = update['operation'] if isinstance(update, dict) else update.operation
    mins = int(update['minutes'] if isinstance(update, dict) else update.minutes)
    current = max(0, int(old_minutes or 0))
    if op == 'add':
        if mins <= 0 or mins > 10000:
            raise ValueError('Для operation="add" количество минут должно быть больше 0.')
        total = current + mins
    elif op == 'set':
        if mins < 0 or mins > 10000:
            raise ValueError('Для operation="set" количество минут должно быть от 0 до 10000.')
        total = mins
    else:
        raise ValueError(f'Неизвестная операция прогресса: {op}')
    if total > 10000:
        raise ValueError('Суммарный подтверждённый прогресс не может превышать 10000 минут.')
    return total


def apply_repair(candidate: PlanProposal, raw_repair: str | dict | PlanRepair) -> PlanProposal:
    """Применяет частичный патч PlanRepair к копии сохранённого черновика плана."""
    if isinstance(raw_repair, PlanRepair):
        repair = raw_repair
    elif isinstance(raw_repair, dict):
        repair = PlanRepair.model_validate(raw_repair)
    else:
        repair = PlanRepair.model_validate_json(raw_repair)

    result = deepcopy(candidate)

    seen_tasks: set[int] = set()
    for item in repair.task_replacements:
        if item.assessment.task_id != item.task_id:
            raise ValueError(f'Task ID mismatch: {item.task_id} != {item.assessment.task_id}')
        if item.task_id in seen_tasks:
            raise ValueError(f'Повторяющийся task_id в task_replacements: {item.task_id}')
        seen_tasks.add(item.task_id)

    for item in repair.task_replacements:
        first_idx = next((i for i, task in enumerate(result.tasks) if task.task_id == item.task_id), None)
        if first_idx is None:
            result.tasks.append(item.assessment)
        else:
            result.tasks[first_idx] = item.assessment
            result.tasks = [
                task for i, task in enumerate(result.tasks)
                if i == first_idx or task.task_id != item.task_id
            ]

    if repair.task_ids is not None:
        result.task_ids = list(repair.task_ids)
        allowed = set(result.task_ids)
        result.tasks = [task for task in result.tasks if task.task_id in allowed]

    original_count = len(result.periods)
    replacements: dict[int, PlanPeriod] = {}
    for item in repair.period_replacements:
        if item.index in replacements:
            raise ValueError(f'Повторяющийся index в period_replacements: {item.index}')
        if item.index < 0 or item.index >= original_count:
            raise ValueError(f'Invalid period index: {item.index}')
        replacements[item.index] = item.period

    removals: set[int] = set()
    for idx in repair.remove_period_indices:
        if idx in removals:
            raise ValueError(f'Повторяющийся index в remove_period_indices: {idx}')
        if idx < 0 or idx >= original_count:
            raise ValueError(f'Invalid period index: {idx}')
        removals.add(idx)

    if replacements.keys() & removals:
        raise ValueError('Cannot replace and remove the same period')

    result.periods = [
        replacements.get(i, period)
        for i, period in enumerate(result.periods)
        if i not in removals
    ]
    result.periods.extend(repair.add_periods)

    if repair.preferences is not None:
        result.preferences = list(repair.preferences)
    if repair.progress_updates is not None:
        result.progress_updates = list(repair.progress_updates)
    if repair.explanation is not None:
        if not repair.explanation.strip():
            raise ValueError('explanation не должен быть пустым.')
        result.explanation = repair.explanation

    return result


# --- Базовые утилиты времени и форматирования ---

def round_step(minutes: float, step: int = 5) -> int:
    return max(0, int(round(minutes / step) * step))


def format_hours(min_minutes: int, max_minutes: int | None = None) -> str:
    low = max(0, int(min_minutes))
    high = max(low, int(max_minutes if max_minutes is not None else low))
    if high == 0:
        return 'выполнено'
    if high < 60:
        if low == high or high - low < 10:
            return f'≈ {max(10, round_step(high, 5))} мин'
        return f'{max(5, round_step(low, 5))}–{round_step(high, 5)} мин'

    def fmt(hours: float) -> str:
        rounded = round(hours * 2) / 2
        if rounded >= 10:
            rounded = float(round(rounded))
        return str(int(rounded)) if rounded == int(rounded) else f'{rounded:.1f}'

    low_h = max(0.5, round(low / 60 * 2) / 2)
    high_h = max(low_h, round(high / 60 * 2) / 2)
    if high_h - low_h < 0.5:
        return f'≈ {fmt(high_h)} ч'
    return f'{fmt(low_h)}–{fmt(high_h)} ч'


def format_date_ru(day: calendar_date) -> str:
    return f'{day.day} {MONTHS_GENITIVE[day.month - 1]}'


def parse_dt(raw: datetime | str | None, default_tz=timezone.utc) -> datetime | None:
    if raw is None:
        return None
    value = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
    return value.replace(tzinfo=default_tz) if value.tzinfo is None else value


def time_to_minutes(hhmm: str) -> int:
    if hhmm == '24:00':
        return 1440
    hour, minute = map(int, hhmm.split(':'))
    return hour * 60 + minute


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for left, right in sorted(intervals):
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _subtract_intervals(base: list[tuple[int, int]], blocked: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result = list(base)
    for b_start, b_end in merge_intervals(blocked):
        next_res = []
        for a_start, a_end in result:
            if b_end <= a_start or b_start >= a_end:
                next_res.append((a_start, a_end))
            else:
                if b_start > a_start:
                    next_res.append((a_start, b_start))
                if b_end < a_end:
                    next_res.append((b_end, a_end))
        result = next_res
    return result


# --- Календарь и реалистичная доступность ---

def restore_week(week, timezone_name: str = 'UTC') -> dict:
    if week:
        configured = dict(week)
        configured.setdefault('timezone', timezone_name)
        configured.setdefault('days', [[] for _ in range(7)])
        configured.setdefault('events', [])
        configured.setdefault('calendar_events', [])
        return configured
    return {'timezone': timezone_name, 'days': [[] for _ in range(7)], 'events': [], 'calendar_events': []}


def _events_for_day(week: dict, day: calendar_date) -> list[dict]:
    parity = week_parity(day, week.get('parity_anchor'))
    days_list = week.get('days') or [[] for _ in range(7)]
    slots = [dict(slot) for slot in days_list[day.weekday()] if slot.get('week_pattern', 'all') in ('all', parity)]
    slots += [dict(slot) for slot in week.get('events', []) if slot.get('date') == day.isoformat()]
    slots += [dict(event) for event in week.get('calendar_events', []) if occurs(event, day)]
    return slots


def _is_marker_event(event: dict, duration: int, all_day: bool) -> bool:
    text = f"{event.get('title', '')} {event.get('summary', '')}".casefold()
    has_marker = any(word in text for word in MARKER_KEYWORDS)
    if all_day:
        return event.get('kind') == 'task' or has_marker
    return event.get('kind') == 'task' and duration <= 30 and has_marker


def _default_sleep_intervals(merged_sleep: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if merged_sleep:
        return merged_sleep
    return [(0, 420), (1380, 1440)]


def free_minutes_in_window(day_info: dict, start_min: int, end_min: int) -> int:
    """Точная доступность внутри [start_min, end_min] на конкретный день."""
    if day_info.get('all_day_unavailable') or day_info.get('day_mode') == 'free':
        return 0
    left = max(day_info['start_bound'], int(start_min))
    right = min(1440, int(end_min))
    if right <= left:
        return 0
    base = day_info['merged_work'] if day_info['merged_work'] else [(0, 1440)]
    clipped = []
    for a, b in base:
        ca, cb = max(a, left), min(b, right)
        if cb > ca:
            clipped.append((ca, cb))
    blocked = day_info['merged_busy'] + day_info['effective_sleep'] + day_info['merged_meal']
    free_segs = _subtract_intervals(clipped, blocked)
    raw = sum(b - a for a, b in free_segs)
    return min(raw, day_info['max_work_minutes'])


def available_for_task_on_day(
    day_info: dict,
    day_date: calendar_date,
    available_from: datetime | None,
    deadline: datetime | None,
    policy: str = 'soft',
) -> tuple[int, int, int]:
    """Возвращает (start_min, end_min, free_minutes) на дату с учётом открытия и жёсткого дедлайна."""
    start = int(day_info['start_bound'])
    end = 1440
    if available_from is not None:
        if day_date < available_from.date():
            return start, start, 0
        if day_date == available_from.date():
            start = max(start, available_from.hour * 60 + available_from.minute)
    if policy != 'soft' and deadline is not None:
        if day_date > deadline.date():
            return start, start, 0
        if day_date == deadline.date():
            end = min(end, deadline.hour * 60 + deadline.minute)
    if end <= start:
        return start, end, 0
    return start, end, free_minutes_in_window(day_info, start, end)


def analyze_days(
    week: dict,
    now: str,
    horizon_days: int = 14,
    adjustments: dict | None = None,
    planning_style: str = 'balanced',
) -> list[dict]:
    """Строит карту реальных событий и границ допустимой нагрузки на каждый день горизонта."""
    manual_day_overrides = (adjustments or {}).get('day_overrides') or {}
    zone = ZoneInfo(week.get('timezone', 'UTC'))
    now_local = parse_dt(now, timezone.utc).astimezone(zone)
    today = now_local.date()
    now_min = now_local.hour * 60 + now_local.minute
    daily_limit, total_ceiling = STYLE_LIMITS.get(planning_style, STYLE_LIMITS['balanced'])

    days_info = []
    for offset in range(horizon_days):
        day = today + timedelta(days=offset)
        day_iso = day.isoformat()
        raw_events = _events_for_day(week, day)

        busy_intervals: list[tuple[int, int]] = []
        work_intervals: list[tuple[int, int]] = []
        sleep_intervals: list[tuple[int, int]] = []
        meal_intervals: list[tuple[int, int]] = []
        markers: list[dict] = []
        commitments: list[dict] = []
        all_day_unavailable = False
        ambiguous_all_day = 0
        has_commute_event = False

        for ev in raw_events:
            start_m = time_to_minutes(ev['start'])
            end_m = time_to_minutes(ev['end'])
            if end_m <= start_m:
                continue
            duration = end_m - start_m
            kind = ev.get('kind', 'busy')
            title = ev.get('title') or kind
            text = f"{title} {ev.get('summary', '')}".casefold()
            if any(word in text for word in COMMUTE_KEYWORDS):
                has_commute_event = True

            all_day = (start_m <= 5 and end_m >= 1435) or duration >= 1080
            if kind == 'work':
                work_intervals.append((start_m, end_m))
                continue
            if kind == 'sleep':
                sleep_intervals.append((start_m, end_m))
                continue
            if kind in ('meal', 'break'):
                meal_intervals.append((start_m, end_m))
                continue
            if _is_marker_event(ev, duration, all_day):
                markers.append({'title': title, 'kind': 'deadline', 'all_day': all_day})
                continue
            if all_day:
                if any(word in text for word in UNAVAILABLE_KEYWORDS):
                    all_day_unavailable = True
                else:
                    ambiguous_all_day += 240
                commitments.append({'id': ev.get('id', ''), 'title': title, 'start': ev['start'], 'end': ev['end'], 'kind': kind, 'all_day': True})
                continue

            busy_intervals.append((start_m, end_m))
            commitments.append({'id': ev.get('id', ''), 'title': title, 'start': ev['start'], 'end': ev['end'], 'kind': kind, 'all_day': False})

        merged_busy = merge_intervals(busy_intervals)
        merged_work = merge_intervals(work_intervals)
        merged_sleep = merge_intervals(sleep_intervals)
        effective_sleep = _default_sleep_intervals(merged_sleep)
        merged_meal = merge_intervals(meal_intervals)

        busy_minutes = sum(b - a for a, b in merged_busy) + ambiguous_all_day
        sleep_minutes = max(360, sum(b - a for a, b in merged_sleep)) if merged_sleep else 480
        routine_minutes = max(120, sum(b - a for a, b in merged_meal)) if merged_meal else 120

        clusters = 0
        last_end = None
        for a, b in merged_busy:
            if last_end is None or a - last_end > 20:
                clusters += 1
            last_end = b
        commute_minutes = 0 if (has_commute_event or busy_minutes < 90) else min(60, 15 * clusters)

        start_bound = now_min if offset == 0 else 0
        if offset == 0:
            future_busy = sum(max(0, b - max(a, start_bound)) for a, b in merged_busy if b > start_bound)
            future_sleep = sum(max(0, b - max(a, start_bound)) for a, b in effective_sleep if b > start_bound)
            future_routine = round(routine_minutes * max(0.25, (1440 - start_bound) / 1440))
            future_commute = min(commute_minutes, 15 * sum(1 for _, b in merged_busy if b > start_bound))
            physical_free = max(0, (1440 - start_bound) - future_sleep - future_routine - future_busy - future_commute)
        else:
            physical_free = max(0, 1440 - sleep_minutes - routine_minutes - busy_minutes - commute_minutes)

        if merged_work:
            work_segs = _subtract_intervals([(max(a, start_bound), b) for a, b in merged_work if b > start_bound], merged_busy + merged_meal)
            physical_free = min(physical_free, sum(b - a for a, b in work_segs))

        day_mode = manual_day_overrides.get(day_iso) or 'normal'
        if all_day_unavailable or day_mode == 'free' or (offset == 0 and (now_min >= 22 * 60 or physical_free < 25)):
            max_work = 0
        else:
            # Осторожный личный буфер: 120 мин для полного дня, масштабируется для остатка сегодняшнего дня.
            rest_reserve = 90 if offset == 0 and start_bound >= 12 * 60 else 120
            raw_after_reserve = max(0, physical_free - rest_reserve)
            # Не перегружаем суммарную нагрузку (обязательные дела + самостоятельная работа).
            ceiling_cap = max(0, total_ceiling - busy_minutes)
            max_work = min(raw_after_reserve, daily_limit, ceiling_cap)
            if physical_free >= 120 and busy_minutes >= 240 and max_work < 90:
                # Даже в плотный учебный день оставляем место для небольшого поручения по дороге (если физически свободно).
                max_work = min(raw_after_reserve, 90)
            if day_mode == 'light':
                max_work = min(max_work, 120)
            elif day_mode == 'focused':
                max_work = min(physical_free, max(max_work, daily_limit + 90))
            max_work = round_step(max_work, 5)
            if 0 < max_work < 20:
                max_work = 0

        days_info.append({
            'offset': offset,
            'date': day_iso,
            'weekday': day.weekday(),
            'weekday_name': WEEKDAYS_SHORT[day.weekday()],
            'day_mode': day_mode,
            'start_bound': start_bound,
            'busy_minutes': busy_minutes,
            'physical_free_minutes': physical_free,
            'max_work_minutes': max_work,
            'all_day_unavailable': all_day_unavailable,
            'merged_busy': merged_busy,
            'merged_work': merged_work,
            'effective_sleep': effective_sleep,
            'merged_meal': merged_meal,
            'markers': markers,
            'commitments': sorted(commitments, key=lambda c: c['start']),
        })

    return days_info


def compute_horizon(tasks: list[dict], today: calendar_date, zone: ZoneInfo) -> int:
    horizon = 14
    for task in tasks:
        dl = parse_dt(task.get('deadline'), timezone.utc)
        if dl is not None:
            delta = (dl.astimezone(zone).date() - today).days + 1
            if 14 < delta <= 21:
                horizon = max(horizon, delta)
    return horizon


def calendar_overview(days: list[dict]) -> list[dict]:
    overview = []
    for d in days:
        item = {
            'date': d['date'],
            'weekday': d['weekday_name'],
            'busy_minutes': d['busy_minutes'],
            'physical_free_minutes': d['physical_free_minutes'],
            'max_work_minutes': d['max_work_minutes'],
        }
        if d['day_mode'] != 'normal':
            item['mode'] = d['day_mode']
        if d['merged_work']:
            item['work_windows'] = [f'{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}' for a, b in d['merged_work']]
        if d['commitments']:
            item['commitments'] = [
                {'title': c['title'], 'start': c['start'], 'end': c['end'], **({'all_day': True} if c['all_day'] else {})}
                for c in d['commitments']
            ]
        if d['markers']:
            item['markers'] = [m['title'] for m in d['markers']]
        overview.append(item)
    return overview


# --- Подготовка остатков задач и проверка зависимостей ---

def _task_budget(
    assessment: TaskAssessment,
    task: dict,
    overrides: dict,
    progress_override: dict[int, int] | None = None,
) -> tuple[int, int, int]:
    """Возвращает (confirmed_minutes, remaining_min, remaining_max) с однократным вычитанием прогресса."""
    tid = assessment.task_id
    confirmed = int(
        progress_override[tid] if (progress_override and tid in progress_override)
        else (task.get('completed_work_minutes') or 0)
    )
    rem_min = max(0, assessment.total_minutes_min - confirmed)
    rem_max = max(rem_min, assessment.total_minutes_max - confirmed)
    manual = overrides.get(str(tid))
    if manual is not None:
        val = max(0, int(manual))
        rem_min = min(rem_min, val) if val else 0
        rem_max = val
        if rem_max > 0 and rem_min == rem_max:
            rem_min = max(5, round(rem_max * 0.85))
    return confirmed, rem_min, rem_max


def _check_dependencies(assessments: dict[int, TaskAssessment], expected: set[int]) -> list[dict]:
    issues = []
    for tid, item in assessments.items():
        for dep in item.depends_on:
            if dep == tid or dep not in expected:
                issues.append({
                    'code': 'invalid_dependency',
                    'task_id': tid,
                    'message': f'Задача {tid}: некорректная зависимость {dep}.',
                })
    if issues:
        return issues

    state: dict[int, int] = {}

    def dfs(node: int) -> bool:
        st = state.get(node, 0)
        if st == 1:
            return True
        if st == 2:
            return False
        state[node] = 1
        for dep in assessments[node].depends_on:
            if dfs(dep):
                return True
        state[node] = 2
        return False

    if any(dfs(tid) for tid in assessments):
        issues.append({'code': 'dependency_cycle', 'message': 'Обнаружен цикл зависимостей между задачами.'})
    return issues


# --- Валидатор предложений LLM ---

def validate_proposal(
    raw_arguments: str | dict | PlanProposal,
    tasks: list[dict],
    week: dict,
    now: str,
    *,
    planning_style: str = 'balanced',
    adjustments: dict | None = None,
    has_feedback: bool = False,
) -> tuple[PlanProposal | None, list[dict], list[dict], dict[int, dict]]:
    """Проверяет предложение модели по инвариантам. Возвращает (proposal, issues, days, budgets)."""
    if isinstance(raw_arguments, PlanProposal):
        proposal = raw_arguments
    elif isinstance(raw_arguments, dict):
        proposal = PlanProposal.model_validate(raw_arguments)
    else:
        proposal = PlanProposal.model_validate_json(raw_arguments)

    tasks_by_id = {task['id']: task for task in tasks}
    expected_ids = [task['id'] for task in tasks]
    expected_set = set(expected_ids)
    zone = ZoneInfo(week.get('timezone', 'UTC'))
    now_dt = parse_dt(now, timezone.utc)
    now_local = now_dt.astimezone(zone)
    today = now_local.date()
    today_iso = today.isoformat()
    horizon_days = compute_horizon(tasks, today, zone)
    days = analyze_days(week, now_dt.isoformat(), horizon_days=horizon_days, adjustments=adjustments, planning_style=planning_style)
    days_by_date = {d['date']: d for d in days}
    horizon_end = days[-1]['date']

    issues: list[dict] = []
    seen_order: set[int] = set()
    for tid in proposal.task_ids:
        if tid not in expected_set:
            issues.append({'code': 'unknown_task', 'task_id': tid, 'message': f'Неизвестная задача {tid} в task_ids.'})
        elif tid in seen_order:
            issues.append({'code': 'duplicate_task', 'task_id': tid, 'message': f'Задача {tid} повторяется в task_ids.'})
        seen_order.add(tid)
    missing_order = [tid for tid in expected_ids if tid not in seen_order]
    if missing_order:
        issues.append({'code': 'missing_task', 'task_ids': missing_order, 'message': f'В task_ids отсутствуют задачи: {missing_order}.'})

    assessments: dict[int, TaskAssessment] = {}
    for item in proposal.tasks:
        tid = item.task_id
        if tid not in expected_set:
            issues.append({'code': 'unknown_task', 'task_id': tid, 'message': f'Неизвестная задача {tid} в tasks.'})
            continue
        if tid in assessments:
            issues.append({'code': 'duplicate_task', 'task_id': tid, 'message': f'Оценка задачи {tid} указана дважды.'})
            continue
        if item.total_minutes_min > item.total_minutes_max:
            issues.append({'code': 'invalid_estimate', 'task_id': tid, 'message': f'Задача {tid}: total_minutes_min больше total_minutes_max.'})
        assessments[tid] = item

    missing_assess = [tid for tid in expected_ids if tid not in assessments]
    if missing_assess:
        issues.append({'code': 'missing_task', 'task_ids': missing_assess, 'message': f'Нет оценки для задач: {missing_assess}.'})

    # Проверка progress_updates и preferences.
    progress_override: dict[int, int] = {}
    if proposal.progress_updates and not has_feedback:
        issues.append({
            'code': 'progress_update_without_feedback',
            'message': 'progress_updates разрешены только при явном сообщении владельца в user_feedback.',
        })
    seen_progress_tasks: set[int] = set()
    for upd in proposal.progress_updates:
        if upd.task_id not in expected_set:
            issues.append({'code': 'unknown_task', 'task_id': upd.task_id, 'message': f'progress_updates: неизвестная задача {upd.task_id}.'})
            continue
        if upd.task_id in seen_progress_tasks:
            issues.append({'code': 'duplicate_progress_update', 'task_id': upd.task_id, 'message': f'progress_updates: задача {upd.task_id} указана дважды.'})
            continue
        seen_progress_tasks.add(upd.task_id)
        current_done = int(tasks_by_id[upd.task_id].get('completed_work_minutes') or 0)
        try:
            progress_override[upd.task_id] = apply_progress(current_done, upd)
        except ValueError as exc:
            issues.append({'code': 'invalid_progress_update', 'task_id': upd.task_id, 'message': str(exc)})

    for pref in proposal.preferences:
        if pref.scope == 'task' and (pref.task_id is None or pref.task_id not in expected_set):
            issues.append({'code': 'invalid_preference', 'task_id': pref.task_id, 'message': 'Для scope=task укажи существующий task_id.'})
        if pref.scope == 'persistent' and pref.task_id is not None:
            issues.append({'code': 'invalid_preference', 'message': 'Для scope=persistent поле task_id должно быть null.'})

    if issues:
        return proposal, issues, days, {}

    issues.extend(_check_dependencies(assessments, expected_set))
    if issues:
        return proposal, issues, days, {}

    overrides = (adjustments or {}).get('estimate_overrides') or {}
    task_pins = (adjustments or {}).get('task_pins') or {}
    budgets: dict[int, dict] = {}
    meta: dict[int, dict] = {}
    for tid, item in assessments.items():
        task = tasks_by_id[tid]
        confirmed, rem_min, rem_max = _task_budget(item, task, overrides, progress_override)
        dl_dt = parse_dt(task.get('deadline'), timezone.utc)
        dl_local = dl_dt.astimezone(zone) if dl_dt else None
        av_dt = parse_dt(item.available_from, zone)
        av_local = av_dt.astimezone(zone) if av_dt else None
        budgets[tid] = {
            'confirmed': confirmed,
            'rem_min': rem_min,
            'rem_max': rem_max,
            'target': 0 if rem_max == 0 else max(10, round_step(0.35 * rem_min + 0.65 * rem_max, 5)),
        }
        meta[tid] = {
            'deadline': dl_dt,
            'deadline_local': dl_local,
            'available_local': av_local,
            'policy': item.deadline_policy,
            'depends_on': list(item.depends_on),
        }

    # Распространение раннего available_from и жёсткого deadline по зависимостям.
    changed = True
    while changed:
        changed = False
        for tid, info in meta.items():
            for dep_id in info['depends_on']:
                dep = meta[dep_id]
                if info['policy'] != 'soft' and info['deadline'] is not None:
                    if dep['deadline'] is None or dep['deadline'] > info['deadline']:
                        dep['deadline'] = info['deadline']
                        dep['deadline_local'] = info['deadline_local']
                        if dep['policy'] == 'soft':
                            dep['policy'] = info['policy']
                        changed = True
                if dep['available_local'] is not None and (info['available_local'] is None or info['available_local'] < dep['available_local']):
                    info['available_local'] = dep['available_local']
                    changed = True

    # Проверка периодов и аллокаций.
    rest_dates: set[str] = set()
    work_periods_by_task: dict[int, list[PlanPeriod]] = {}
    daily_load: dict[str, int] = {}
    day_period_indices: dict[str, list[int]] = {}
    task_day_minutes: dict[tuple[int, str], int] = {}
    task_dates: dict[int, set[str]] = {}
    task_total: dict[int, int] = {}

    for period_idx, period in enumerate(proposal.periods):
        if period.start_date > period.end_date:
            issues.append({'code': 'invalid_period', 'period_index': period_idx, 'task_id': period.task_id, 'message': f'Период #{period_idx}: start_date ({period.start_date}) больше end_date ({period.end_date}).'})
            continue
        if period.start_date < today_iso:
            issues.append({'code': 'date_in_past', 'period_index': period_idx, 'task_id': period.task_id, 'message': f'Период #{period_idx} ({period.start_date}) начинается в прошлом.'})
            continue
        if period.end_date > horizon_end:
            issues.append({'code': 'outside_horizon', 'period_index': period_idx, 'task_id': period.task_id, 'message': f'Период #{period_idx} до {period.end_date} выходит за горизонт {horizon_end}.'})
            continue

        if period.kind == 'rest':
            if period.task_id is not None or period.allocations:
                issues.append({'code': 'invalid_rest_period', 'period_index': period_idx, 'message': 'Для периода отдыха task_id должен быть null, а allocations — пустым [].'})
                continue
            cur = calendar_date.fromisoformat(period.start_date)
            end = calendar_date.fromisoformat(period.end_date)
            while cur <= end:
                rest_dates.add(cur.isoformat())
                cur += timedelta(days=1)
            continue

        tid = period.task_id
        if tid is None or tid not in expected_set:
            issues.append({'code': 'unknown_task', 'period_index': period_idx, 'task_id': tid, 'message': f'Период #{period_idx} ссылается на несуществующую задачу {tid}.'})
            continue
        if not period.allocations:
            issues.append({'code': 'empty_work_period', 'period_index': period_idx, 'task_id': tid, 'message': f'Задача {tid} (период #{period_idx}): рабочий период должен содержать allocations.'})
            continue

        work_periods_by_task.setdefault(tid, []).append(period)
        seen_period_dates: set[str] = set()
        for item in period.allocations:
            if item.date in seen_period_dates:
                issues.append({'code': 'duplicate_allocation', 'period_index': period_idx, 'task_id': tid, 'date': item.date, 'message': f'Задача {tid}: дата {item.date} повторяется в периоде.'})
                continue
            seen_period_dates.add(item.date)
            if not (period.start_date <= item.date <= period.end_date):
                issues.append({'code': 'outside_period', 'period_index': period_idx, 'task_id': tid, 'date': item.date, 'message': f'Задача {tid}: дата {item.date} вне периода {period.start_date}..{period.end_date}.'})
                continue
            if item.date not in days_by_date:
                issues.append({'code': 'outside_horizon', 'period_index': period_idx, 'task_id': tid, 'date': item.date, 'message': f'Дата {item.date} вне допустимого горизонта.'})
                continue
            if (tid, item.date) in task_day_minutes:
                issues.append({'code': 'duplicate_allocation', 'period_index': period_idx, 'task_id': tid, 'date': item.date, 'message': f'Задача {tid} назначена дважды на {item.date}.'})
                continue

            task_day_minutes[(tid, item.date)] = item.minutes
            daily_load[item.date] = daily_load.get(item.date, 0) + item.minutes
            day_period_indices.setdefault(item.date, [])
            if period_idx not in day_period_indices[item.date]:
                day_period_indices[item.date].append(period_idx)
            task_dates.setdefault(tid, set()).add(item.date)
            task_total[tid] = task_total.get(tid, 0) + item.minutes

    # Конфликт работы и отмеченных дней отдыха.
    for (tid, date_iso), _mins in task_day_minutes.items():
        if date_iso in rest_dates:
            issues.append({'code': 'rest_day_conflict', 'task_id': tid, 'date': date_iso, 'period_indices': day_period_indices.get(date_iso, []), 'message': f'День {date_iso} помечен как отдых, но на него назначена задача {tid}.'})

    # Проверка дневной ёмкости.
    for date_iso, total_mins in daily_load.items():
        day_info = days_by_date[date_iso]
        cap = day_info['max_work_minutes']
        p_indices = day_period_indices.get(date_iso, [])
        if cap == 0:
            issues.append({
                'code': 'unavailable_day',
                'date': date_iso,
                'available_minutes': 0,
                'period_indices': p_indices,
                'message': f'День {date_iso} недоступен для самостоятельной работы (выходной/отсутствие/занятость).',
            })
        elif total_mins > cap:
            issues.append({
                'code': 'over_capacity',
                'date': date_iso,
                'excess_minutes': total_mins - cap,
                'available_minutes': cap,
                'period_indices': p_indices,
                'message': f'День {date_iso} перегружен на {total_mins - cap} мин (назначено {total_mins}, допустимо {cap}).',
            })

    # Проверка границ открытия, дедлайнов (включая их пересечение и конкуренцию окон), неделимости, зависимостей и пинов.
    day_task_windows: dict[str, list[tuple[int, int, int, int]]] = {}
    for tid, item in assessments.items():
        budget = budgets[tid]
        info = meta[tid]
        dates = sorted(task_dates.get(tid, ()))
        assigned = task_total.get(tid, 0)

        if assigned > budget['rem_max']:
            issues.append({
                'code': 'overallocated_task',
                'task_id': tid,
                'excess_minutes': assigned - budget['rem_max'],
                'message': f'Задача {tid}: назначено {assigned} мин при остатке не более {budget["rem_max"]} мин.',
            })

        if not item.splittable and assigned > 0:
            if len(dates) > 1 or len(work_periods_by_task.get(tid, ())) > 1:
                issues.append({
                    'code': 'indivisible_split',
                    'task_id': tid,
                    'message': f'Неделимая задача {tid} (splittable=false) раздроблена на несколько дней.',
                })
            elif assigned < budget['rem_min']:
                issues.append({
                    'code': 'indivisible_split',
                    'task_id': tid,
                    'message': f'Неделимая задача {tid} назначена частично ({assigned} мин из {budget["rem_min"]} мин).',
                })

        av_local = info['available_local']
        dl_local = info['deadline_local']
        av_iso = av_local.date().isoformat() if av_local is not None else None
        dl_iso = dl_local.date().isoformat() if (info['policy'] != 'soft' and dl_local is not None) else None

        if info['policy'] != 'soft' and info['deadline'] is not None and info['deadline'] <= now_dt and assigned > 0 and info['policy'] == 'expires':
            issues.append({
                'code': 'deadline_passed',
                'task_id': tid,
                'message': f'Задача {tid}: жёсткий срок ({dl_iso}) уже прошёл.',
            })

        for d_iso in dates:
            mins = task_day_minutes[(tid, d_iso)]
            if av_iso is not None and d_iso < av_iso:
                issues.append({
                    'code': 'before_available_from',
                    'task_id': tid,
                    'date': d_iso,
                    'message': f'Задача {tid} назначена на {d_iso} до даты открытия {av_iso}.',
                })
                continue
            if dl_iso is not None and d_iso > dl_iso:
                issues.append({
                    'code': 'after_deadline',
                    'task_id': tid,
                    'date': d_iso,
                    'message': f'Задача {tid} назначена на {d_iso} после жёсткого срока {dl_iso}.',
                })
                continue

            day_info = days_by_date[d_iso]
            day_obj = calendar_date.fromisoformat(d_iso)
            w_start, w_end, w_avail = available_for_task_on_day(
                day_info, day_obj, av_local, dl_local, policy=info['policy'],
            )
            day_task_windows.setdefault(d_iso, []).append((w_start, w_end, tid, mins))
            has_day_deadline = dl_iso == d_iso
            has_day_open = av_iso == d_iso
            if (has_day_deadline or has_day_open) and mins > w_avail:
                if has_day_deadline:
                    issues.append({
                        'code': 'deadline_day_over_capacity',
                        'task_id': tid,
                        'date': d_iso,
                        'excess_minutes': mins - w_avail,
                        'available_minutes': w_avail,
                        'message': (
                            f'Задача {tid} на {d_iso} в окне {w_start // 60:02d}:{w_start % 60:02d}–'
                            f'{w_end // 60:02d}:{w_end % 60:02d}: доступно только {w_avail} мин, назначено {mins} мин.'
                        ),
                    })
                else:
                    issues.append({
                        'code': 'before_available_from',
                        'task_id': tid,
                        'date': d_iso,
                        'available_minutes': w_avail,
                        'message': f'Задача {tid} открывается {av_iso} в {av_local.strftime("%H:%M")}; после открытия доступно только {w_avail} мин.',
                    })

        if info['policy'] != 'soft' and dl_iso is not None and dl_iso <= horizon_end and assigned < budget['rem_min'] and not (item.deadline_risk and item.deadline_risk.strip()):
            issues.append({
                'code': 'missing_deadline_risk',
                'task_id': tid,
                'message': f'Задача {tid}: к сроку {dl_iso} запланировано {assigned} из {budget["rem_min"]} мин — укажи причину риска в deadline_risk.',
            })

        for dep_id in info['depends_on']:
            if budgets[dep_id]['rem_min'] == 0:
                continue
            dep_dates = sorted(task_dates.get(dep_id, ()))
            if dates:
                if not dep_dates or task_total.get(dep_id, 0) < budgets[dep_id]['rem_min']:
                    issues.append({
                        'code': 'dependency_order_violation',
                        'task_id': tid,
                        'message': f'Задача {tid} запланирована до завершения зависимости {dep_id}.',
                    })
                elif min(dates) < max(dep_dates):
                    issues.append({
                        'code': 'dependency_order_violation',
                        'task_id': tid,
                        'message': f'Задача {tid} ({min(dates)}) начинается раньше окончания зависимости {dep_id} ({max(dep_dates)}).',
                    })

        pin = task_pins.get(str(tid)) or {}
        if pin.get('not_before') and any(d_iso < pin['not_before'] for d_iso in dates):
            issues.append({
                'code': 'violates_not_before',
                'task_id': tid,
                'message': f'Задача {tid} закреплена не раньше {pin["not_before"]}.',
            })
        target_date = pin.get('target_date')
        if target_date and target_date in days_by_date and days_by_date[target_date]['max_work_minutes'] > 0 and budget['rem_max'] > 0:
            dl_ok = info['policy'] == 'soft' or dl_local is None or target_date <= dl_local.date().isoformat()
            av_ok = av_local is None or target_date >= av_local.date().isoformat()
            if dl_ok and av_ok and target_date not in dates:
                issues.append({
                    'code': 'violates_task_pin',
                    'task_id': tid,
                    'date': target_date,
                    'message': f'Владелец закрепил задачу {tid} за {target_date}. Включи её в этот день либо при невозможности опиши конфликт в deadline_risk.',
                })

    # Проверка конкуренции нескольких задач за общие временные окна одного дня.
    seen_window_conflicts: set[tuple[str, int, int]] = set()
    for d_iso, entries in day_task_windows.items():
        if len(entries) < 2:
            continue
        day_info = days_by_date[d_iso]
        starts = sorted({entry[0] for entry in entries})
        ends = sorted({entry[1] for entry in entries})
        for left in starts:
            for right in ends:
                if right <= left:
                    continue
                if left <= day_info['start_bound'] and right >= 1440:
                    continue
                contained = [entry for entry in entries if entry[0] >= left and entry[1] <= right]
                if len(contained) < 2:
                    continue
                win_avail = free_minutes_in_window(day_info, left, right)
                win_total = sum(entry[3] for entry in contained)
                if win_total > win_avail and (d_iso, left, right) not in seen_window_conflicts:
                    seen_window_conflicts.add((d_iso, left, right))
                    tids = [entry[2] for entry in contained]
                    issues.append({
                        'code': 'deadline_day_over_capacity',
                        'task_ids': tids,
                        'date': d_iso,
                        'excess_minutes': win_total - win_avail,
                        'available_minutes': win_avail,
                        'message': (
                            f'На {d_iso} в окне {left // 60:02d}:{left % 60:02d}–{right // 60:02d}:{right % 60:02d} '
                            f'доступно только {win_avail} мин, а для конкурирующих задач {tids} назначено {win_total} мин.'
                        ),
                    })

    # Не принимаем пустой план, если есть доступное время и активные незавершённые задачи.
    total_capacity = sum(d['max_work_minutes'] for d in days)
    total_needed = sum(b['rem_min'] for b in budgets.values())
    if sum(daily_load.values()) == 0 and total_needed > 0 and total_capacity >= 60:
        issues.append({
            'code': 'empty_plan',
            'message': 'Не распределена ни одна задача при наличии свободного времени.',
        })

    return proposal, issues, days, budgets


# --- Материализация проверенного предложения в сохраняемый формат ---

def materialize_plan(
    proposal: PlanProposal,
    tasks: list[dict],
    days: list[dict],
    budgets: dict[int, dict],
    week: dict,
    now: str,
) -> dict:
    tasks_by_id = {task['id']: task for task in tasks}
    assessments = {item.task_id: item for item in proposal.tasks}
    zone = ZoneInfo(week.get('timezone', 'UTC'))
    now_dt = parse_dt(now, timezone.utc)
    today_iso = now_dt.astimezone(zone).date().isoformat()

    plans: dict[int, dict] = {}
    for tid in proposal.task_ids:
        item = assessments[tid]
        b = budgets[tid]
        hours_label = format_hours(b['rem_min'], b['rem_max'])
        wait_suffix = f'; ожидание: {format_hours(item.wait_minutes, item.wait_minutes)}' if item.wait_minutes else ''
        plans[tid] = {
            'task_id': tid,
            'total_minutes_min': item.total_minutes_min,
            'total_minutes_max': item.total_minutes_max,
            'work_min_minutes': b['rem_min'],
            'work_max_minutes': b['rem_max'],
            'min_minutes': b['rem_min'],
            'max_minutes': b['rem_max'],
            'planned_work_minutes': b['target'],
            'completed_work_minutes': b['confirmed'],
            'wait_minutes': item.wait_minutes,
            'splittable': item.splittable,
            'deadline_policy': item.deadline_policy,
            'available_from': item.available_from.isoformat() if item.available_from else None,
            'depends_on': list(item.depends_on),
            'effort': item.effort,
            'impact': item.impact,
            'hours_label': hours_label,
            'time_estimate': f'{hours_label}{wait_suffix}',
        }

    # Группировка аллокаций по дням для Calendar.jsx и /refresh.
    day_tasks_map: dict[str, list[dict]] = {d['date']: [] for d in days}
    assigned_by_task: dict[int, int] = {tid: 0 for tid in proposal.task_ids}
    for period in proposal.periods:
        if period.kind != 'work' or period.task_id is None:
            continue
        tid = period.task_id
        title = tasks_by_id[tid]['title']
        for item in period.allocations:
            assigned_by_task[tid] += item.minutes
            day_tasks_map[item.date].append({
                'task_id': tid,
                'title': title,
                'minutes': item.minutes,
                'hours_label': format_hours(item.minutes, item.minutes),
            })

    daily_allocations = []
    for d in days:
        d_tasks = day_tasks_map[d['date']]
        alloc_mins = sum(x['minutes'] for x in d_tasks)
        daily_allocations.append({
            'date': d['date'],
            'weekday': d['weekday'],
            'mode': d['day_mode'],
            'busy_minutes': d['busy_minutes'],
            'physical_free_minutes': d['physical_free_minutes'],
            'max_work_minutes': d['max_work_minutes'],
            'allocated_minutes': alloc_mins,
            'tasks': d_tasks,
            'markers': d['markers'],
            'commitments': d['commitments'],
        })

    # Сборка гибких периодов с сохранением порядка по дате начала.
    sorted_periods = sorted(
        proposal.periods,
        key=lambda p: (p.start_date, p.end_date, 0 if p.kind == 'work' else 1, p.task_id or 0),
    )
    periods_out = []
    for idx, p in enumerate(sorted_periods):
        if p.kind == 'work' and p.task_id is not None:
            tid = p.task_id
            task = tasks_by_id[tid]
            assess = assessments[tid]
            mins = sum(a.minutes for a in p.allocations)
            h_label = format_hours(mins, mins)
            dl_dt = parse_dt(task.get('deadline'), timezone.utc)
            completes = assigned_by_task[tid] >= budgets[tid]['rem_min']
            alloc_list = [{'date': a.date, 'minutes': a.minutes} for a in sorted(p.allocations, key=lambda x: x.date)]
            periods_out.append({
                'id': f'p-{idx}-{p.start_date}-{p.end_date}-{tid}',
                'kind': 'work',
                'task_id': tid,
                'title': task['title'],
                'start_date': p.start_date,
                'end_date': p.end_date,
                'goal': p.goal.strip(),
                'context_hint': p.context_hint.strip() if p.context_hint and p.context_hint.strip() else None,
                'reason': p.reason.strip() if p.reason and p.reason.strip() else None,
                'minutes': mins,
                'hours_label': h_label,
                'allocations': alloc_list,
                'completes_task': completes,
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'deadline_policy': assess.deadline_policy,
                'deadline_risk': assess.deadline_risk,
                'tasks': [{
                    'task_id': tid,
                    'title': task['title'],
                    'goal': p.goal.strip(),
                    'minutes': mins,
                    'hours_label': h_label,
                    'completes_task': completes,
                    'deadline': dl_dt.isoformat() if dl_dt else None,
                    'deadline_policy': assess.deadline_policy,
                }],
            })
        else:
            periods_out.append({
                'id': f'p-{idx}-{p.start_date}-{p.end_date}-rest',
                'kind': 'rest',
                'task_id': None,
                'title': p.goal.strip(),
                'start_date': p.start_date,
                'end_date': p.end_date,
                'goal': p.goal.strip(),
                'context_hint': p.context_hint.strip() if p.context_hint and p.context_hint.strip() else None,
                'reason': p.reason.strip() if p.reason and p.reason.strip() else None,
                'minutes': 0,
                'hours_label': '',
                'allocations': [],
                'completes_task': False,
                'deadline': None,
                'deadline_policy': 'soft',
                'deadline_risk': None,
                'tasks': [],
            })

    # Риски сроков и незапланированные остатки.
    conflicts = []
    remaining = []
    for tid in proposal.task_ids:
        b = budgets[tid]
        assess = assessments[tid]
        task = tasks_by_id[tid]
        unplanned = max(0, b['target'] - assigned_by_task[tid])
        remaining.append({'task_id': tid, 'remaining_work_minutes': unplanned})
        deficit = max(0, b['rem_min'] - assigned_by_task[tid])
        if assess.deadline_risk or (assess.deadline_policy != 'soft' and deficit > 0):
            dl_dt = parse_dt(task.get('deadline'), timezone.utc)
            conflicts.append({
                'task_id': tid,
                'title': task['title'],
                'policy': assess.deadline_policy,
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'infeasible': assess.deadline_policy != 'soft' and deficit > 0,
                'deficit_minutes': deficit,
                'deficit_label': format_hours(deficit) if deficit else '',
                'reason': assess.deadline_risk or f"{task['title']}: недостаточно времени до срока.",
            })

    total_low = sum(plans[tid]['work_min_minutes'] for tid in proposal.task_ids)
    total_high = sum(plans[tid]['work_max_minutes'] for tid in proposal.task_ids)
    total_wait = sum(plans[tid]['wait_minutes'] for tid in proposal.task_ids)
    feasible = not any(c['infeasible'] for c in conflicts)
    remaining_minutes = sum(r['remaining_work_minutes'] for r in remaining)
    fully_scheduled = remaining_minutes == 0 and all(
        assigned_by_task[tid] >= budgets[tid]['rem_min'] for tid in proposal.task_ids
    )
    deadline_risks = [
        {
            'task_id': c['task_id'],
            'title': c['title'],
            'reason': c['reason'],
            'infeasible': c['infeasible'],
            'deficit_minutes': c['deficit_minutes'],
        }
        for c in conflicts
    ]

    schedule = {
        'version': 3,
        'valid': True,
        'task_ids': list(proposal.task_ids),
        'generated_date': today_iso,
        'start_at': now_dt.isoformat(),
        'timezone': week.get('timezone', 'UTC'),
        'feasible': feasible,
        'fully_scheduled': fully_scheduled,
        'remaining_minutes': remaining_minutes,
        'min_minutes': total_low,
        'max_minutes': total_high,
        'hours_label': format_hours(total_low, total_high),
        'wait_minutes': total_wait,
        'periods': periods_out,
        'daily_allocations': daily_allocations,
        'deadline_conflicts': conflicts,
        'deadline_risks': deadline_risks,
        'remaining': remaining,
        'timeline': [
            {
                'task_id': tid,
                'expected_result': assessments[tid].impact,
                'hours_label': plans[tid]['hours_label'],
                'planned_minutes': assigned_by_task[tid],
            }
            for tid in proposal.task_ids
        ],
        'timeline_error': None,
    }

    reasons = [
        {
            'task_id': tid,
            'reason': assessments[tid].reason.strip(),
            'time_estimate': plans[tid]['time_estimate'],
            'effort': {'low': 'Низкая', 'medium': 'Средняя', 'high': 'Высокая'}[assessments[tid].effort],
            'impact': assessments[tid].impact.strip(),
            'deadline_risk': assessments[tid].deadline_risk,
            'plan': plans[tid],
        }
        for tid in proposal.task_ids
    ]
    timeline = [
        {'id': tid, 'title': tasks_by_id[tid]['title'], 'plan': plans[tid]}
        for tid in proposal.task_ids
    ]

    return {
        'task_ids': list(proposal.task_ids),
        'explanation': proposal.explanation.strip(),
        'reasons': reasons,
        'timeline': timeline,
        'schedule': schedule,
        'plans': plans,
        'task_preferences': [
            {'task_id': p.task_id, 'text': p.text.strip()}
            for p in proposal.preferences if p.scope == 'task' and p.task_id is not None
        ],
        'persistent_preferences': [
            {'key': p.key, 'text': p.text.strip()}
            for p in proposal.preferences if p.scope == 'persistent'
        ],
        'progress_updates': [
            {
                'task_id': u.task_id,
                'operation': u.operation,
                'minutes': u.minutes,
                'base_completed_minutes': int(tasks_by_id[u.task_id].get('completed_work_minutes') or 0),
                'completed_work_minutes': budgets[u.task_id]['confirmed'],
            }
            for u in proposal.progress_updates
        ],
    }


# --- Детерминированное обновление существующего плана без LLM (/refresh) ---

_EFFORT_FROM_RU = {'Низкая': 'low', 'Средняя': 'medium', 'Высокая': 'high'}


def refresh_schedule(
    previous: dict,
    tasks: list[dict],
    week: dict,
    now: str,
    *,
    planning_style: str = 'balanced',
    adjustments: dict | None = None,
) -> dict:
    """Адаптирует сохранённый план v3 под прогресс и календарь; при нарушении ограничений требует перепланирования."""
    prev_schedule = (previous or {}).get('schedule') or {}
    if prev_schedule.get('version') != 3:
        raise ValueError('Требуется новый расчёт Ameba для формата v3')

    tz = week.get('timezone', 'UTC')
    zone = ZoneInfo(tz)
    now_dt = parse_dt(now, timezone.utc)
    today = now_dt.astimezone(zone).date()
    today_iso = today.isoformat()
    tasks_by_id = {task['id']: task for task in tasks}
    active_ids = [task['id'] for task in tasks]

    if not active_ids:
        empty_schedule = {
            'version': 3,
            'valid': True,
            'task_ids': [],
            'generated_date': today_iso,
            'start_at': now_dt.isoformat(),
            'timezone': tz,
            'feasible': True,
            'fully_scheduled': True,
            'remaining_minutes': 0,
            'min_minutes': 0,
            'max_minutes': 0,
            'hours_label': 'выполнено',
            'wait_minutes': 0,
            'periods': [],
            'daily_allocations': [],
            'deadline_conflicts': [],
            'deadline_risks': [],
            'remaining': [],
            'timeline': [],
            'timeline_error': None,
        }
        return previous | {'task_ids': [], 'timeline': [], 'reasons': [], 'schedule': empty_schedule}

    saved_plans = {
        item['id']: item['plan']
        for item in previous.get('timeline', [])
        if item.get('id') in tasks_by_id and item.get('plan')
    }
    if any(tid not in saved_plans for tid in active_ids):
        raise ValueError('Есть неоценённые задачи — требуется полный пересчёт Ameba')

    horizon_days = compute_horizon(tasks, today, zone)
    days = analyze_days(week, now_dt.isoformat(), horizon_days=horizon_days, adjustments=adjustments, planning_style=planning_style)
    days_by_date = {d['date']: d for d in days}
    overrides = (adjustments or {}).get('estimate_overrides') or {}
    task_pins = (adjustments or {}).get('task_pins') or {}
    reasons_by_id = {item['task_id']: item for item in previous.get('reasons', []) if item.get('task_id') in tasks_by_id}

    order = [tid for tid in previous.get('task_ids', []) if tid in tasks_by_id]
    for tid in active_ids:
        if tid not in order:
            order.append(tid)

    # Пересчитываем бюджеты и восстанавливаем TaskAssessment для повторной валидации.
    assessments: list[TaskAssessment] = []
    budgets: dict[int, dict] = {}
    old_assigned_by_task: dict[int, int] = {tid: 0 for tid in order}
    task_dates_map: dict[int, list[str]] = {tid: [] for tid in order}
    for p in prev_schedule.get('periods', []):
        tid = p.get('task_id')
        if p.get('kind') == 'work' and tid in tasks_by_id:
            for a in p.get('allocations', []):
                old_assigned_by_task[tid] += int(a.get('minutes', 0))
                if a.get('date'):
                    task_dates_map[tid].append(a['date'])

    for tid in order:
        old_p = saved_plans[tid]
        total_min = int(old_p.get('total_minutes_min', old_p.get('raw_work_min_minutes', old_p.get('work_min_minutes', 0))))
        total_max = max(total_min, int(old_p.get('total_minutes_max', old_p.get('raw_work_max_minutes', old_p.get('work_max_minutes', total_min)))))
        old_reason = reasons_by_id.get(tid) or {}
        raw_effort = old_p.get('effort') or old_reason.get('effort', 'medium')
        effort = _EFFORT_FROM_RU.get(raw_effort, raw_effort if raw_effort in ('low', 'medium', 'high') else 'medium')
        av_raw = old_p.get('available_from')
        av_dt = parse_dt(av_raw, zone) if av_raw else None
        assess = TaskAssessment(
            task_id=tid,
            total_minutes_min=total_min,
            total_minutes_max=total_max,
            wait_minutes=max(0, int(old_p.get('wait_minutes', 0))),
            splittable=bool(old_p.get('splittable', True)),
            deadline_policy=old_p.get('deadline_policy', 'soft') if old_p.get('deadline_policy') in ('soft', 'finish_before', 'expires') else 'soft',
            available_from=av_dt,
            depends_on=[int(d) for d in old_p.get('depends_on', []) if int(d) in tasks_by_id],
            effort=effort,
            reason=str(old_reason.get('reason') or 'По приоритету и срокам')[:200],
            impact=str(old_reason.get('impact') or tasks_by_id[tid]['title'])[:180],
            deadline_risk=old_reason.get('deadline_risk'),
        )
        assessments.append(assess)
        confirmed, rem_min, rem_max = _task_budget(assess, tasks_by_id[tid], overrides)
        target = 0 if rem_max == 0 else max(10, round_step(0.35 * rem_min + 0.65 * rem_max, 5))
        budgets[tid] = {'confirmed': confirmed, 'rem_min': rem_min, 'rem_max': rem_max, 'target': target}

    # Определяем сдвиг для задач с task_pins (очевидный одиночный перенос на новую дату).
    task_shifts: dict[int, int] = {}
    for tid in order:
        pin = task_pins.get(str(tid)) or {}
        cur_dates = sorted(task_dates_map.get(tid, []))
        target_date = pin.get('target_date')
        not_before = pin.get('not_before')
        if not cur_dates or budgets[tid]['rem_max'] == 0:
            continue
        needs_shift = (target_date and target_date not in cur_dates) or (not_before and cur_dates[0] < not_before)
        if needs_shift:
            anchor = target_date or not_before
            shift_days = (calendar_date.fromisoformat(anchor) - calendar_date.fromisoformat(cur_dates[0])).days
            task_shifts[tid] = shift_days

    # Проверяем сохранённые периоды под обновлённый бюджет, сдвиг пинов и актуальную ёмкость дней.
    remaining_cap = {tid: budgets[tid]['rem_max'] for tid in active_ids}
    day_remaining = {d['date']: d['max_work_minutes'] for d in days}
    assigned_by_task = {tid: 0 for tid in active_ids}
    rebuilt_periods: list[PlanPeriod] = []

    for period in prev_schedule.get('periods', []):
        if period.get('kind') == 'rest':
            end_d = min(days[-1]['date'], period.get('end_date', ''))
            start_d = max(today_iso, period.get('start_date', ''))
            if start_d <= end_d:
                rebuilt_periods.append(PlanPeriod(
                    kind='rest',
                    task_id=None,
                    start_date=start_d,
                    end_date=end_d,
                    allocations=[],
                    goal=str(period.get('goal') or period.get('title') or 'Свободное время')[:180],
                    context_hint=period.get('context_hint'),
                    reason=period.get('reason'),
                ))
            continue

        tid = period.get('task_id')
        if tid not in tasks_by_id or remaining_cap.get(tid, 0) <= 0:
            continue
        shift = task_shifts.get(tid, 0)
        kept_allocs: list[DayAllocation] = []
        for alloc in period.get('allocations', []):
            orig_iso = alloc.get('date')
            if not orig_iso or remaining_cap[tid] <= 0:
                continue
            d_iso = (calendar_date.fromisoformat(orig_iso) + timedelta(days=shift)).isoformat() if shift else orig_iso
            orig_mins = max(0, int(alloc.get('minutes', 0)))
            needed = min(orig_mins, remaining_cap[tid])
            if needed <= 0:
                continue
            if d_iso not in days_by_date:
                raise ValueError('Часть сохранённых дат недоступна — требуется перепланирование Ameba')
            if needed > day_remaining[d_iso]:
                raise ValueError('Доступное время дня уменьшилось — требуется перепланирование Ameba')
            remaining_cap[tid] -= needed
            day_remaining[d_iso] -= needed
            assigned_by_task[tid] += needed
            kept_allocs.append(DayAllocation(date=d_iso, minutes=needed))

        if not kept_allocs:
            continue
        kept_allocs.sort(key=lambda a: a.date)
        rebuilt_periods.append(PlanPeriod(
            kind='work',
            task_id=tid,
            start_date=kept_allocs[0].date,
            end_date=kept_allocs[-1].date,
            allocations=kept_allocs,
            goal=str(period.get('goal') or tasks_by_id[tid]['title'])[:180],
            context_hint=period.get('context_hint'),
            reason=period.get('reason'),
        ))

    for tid in order:
        old_rem_min = int(saved_plans[tid].get('work_min_minutes', 0))
        was_covered = old_assigned_by_task.get(tid, 0) >= old_rem_min
        if (was_covered or budgets[tid]['rem_min'] > old_rem_min) and assigned_by_task.get(tid, 0) < budgets[tid]['rem_min']:
            raise ValueError('Изменился остаток задачи — требуется перепланирование Ameba')

    candidate = PlanProposal(
        task_ids=order,
        tasks=assessments,
        periods=rebuilt_periods,
        preferences=[],
        progress_updates=[],
        explanation=str(previous.get('explanation') or 'Актуализированный план на ближайшие дни.')[:400],
    )
    proposal, issues, days, validated_budgets = validate_proposal(
        candidate,
        tasks,
        week,
        now,
        planning_style=planning_style,
        adjustments=adjustments,
        has_feedback=False,
    )
    if issues:
        summary = '; '.join(i.get('message', i.get('code', 'ошибка')) for i in issues[:2])
        raise ValueError(f'Сохранённый план больше не соответствует ограничениям — требуется перепланирование Ameba: {summary}')

    materialized = materialize_plan(proposal, tasks, days, validated_budgets, week, now)
    if previous.get('explanation'):
        materialized['explanation'] = previous['explanation']
    return previous | materialized
