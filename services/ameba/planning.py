"""Ядро планирования Ameba: календарная ёмкость, валидация предложений LLM, материализация и /refresh."""
from __future__ import annotations

from copy import deepcopy
from datetime import date as calendar_date, datetime, timedelta, timezone
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from common.ameba_guards import norm_text, task_matches_text, verify_progress_update
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
    load_type: Literal['cognitive', 'practical', 'mixed'] = Field(
        description='Тип нагрузки: cognitive — интеллектуальная/учёба/код; practical — физическая/бытовая/поручения; mixed — смешанная.',
    )
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
    strength: Literal['soft', 'hard'] = Field(
        description='soft — мягкое пожелание; hard — прямой запрет или обязательное ограничение владельца.',
    )
    blocked_dates: list[str] = Field(
        description='Даты YYYY-MM-DD, явно закрытые для работы при strength=hard и scope in (once, task); иначе [].',
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
    evidence_quote: str = Field(
        min_length=1, max_length=240,
        description='Дословная цитата из user_feedback или ответа в ask_user, подтверждающая уже выполненное время.',
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


def _target_minutes(rem_min: int, rem_max: int) -> int:
    """Вычисляет целевой объём планирования, не превышающий реальный остаток rem_max."""
    if rem_max <= 0:
        return 0
    low = max(0, min(rem_min, rem_max))
    weighted = 0.35 * low + 0.65 * rem_max
    if rem_max < 10:
        return min(rem_max, max(max(1, low), int(round(weighted))))
    return min(rem_max, max(low, max(1, round_step(weighted, 5))))


def format_hours(min_minutes: int, max_minutes: int | None = None) -> str:
    low = max(0, int(min_minutes))
    high = max(low, int(max_minutes if max_minutes is not None else low))
    if high == 0:
        return 'выполнено'
    if high < 60:
        if high < 10:
            if low == high or high - low < 2:
                return f'{high} мин'
            return f'{max(1, low)}–{high} мин'
        if low == high or high - low < 10:
            return f'≈ {max(10, round_step(high, 5))} мин'
        low_label = max(1, low) if low < 5 else round_step(low, 5)
        return f'{low_label}–{round_step(high, 5)} мин'

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
    enforce_deadline: bool | None = None,
) -> tuple[int, int, int]:
    """Возвращает (start_min, end_min, free_minutes) на дату с учётом открытия и жёсткого дедлайна."""
    start = int(day_info['start_bound'])
    end = 1440
    if available_from is not None:
        if day_date < available_from.date():
            return start, start, 0
        if day_date == available_from.date():
            start = max(start, available_from.hour * 60 + available_from.minute)
    should_enforce = (policy != 'soft') if enforce_deadline is None else enforce_deadline
    if should_enforce and deadline is not None:
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

        base_windows = merged_work if merged_work else [(0, 1440)]
        clipped_windows = [(max(a, start_bound), b) for a, b in base_windows if b > max(a, start_bound)]
        free_segs = _subtract_intervals(clipped_windows, merged_busy + effective_sleep + merged_meal)

        day_mode = manual_day_overrides.get(day_iso) or 'normal'
        if all_day_unavailable or day_mode == 'free' or (offset == 0 and (now_min >= 22 * 60 or physical_free < 25)):
            max_work = 0
            heavy_limit = 0
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
            if max_work == 0:
                heavy_limit = 0
            elif day_mode == 'focused' or busy_minutes < 180:
                heavy_limit = max_work
            elif busy_minutes < 360:
                heavy_limit = min(max_work, max(60, round_step(max_work * 0.7, 5)))
            else:
                heavy_limit = min(max_work, 60 if busy_minutes >= 450 else 90)

        free_windows = (
            []
            if max_work == 0
            else [f'{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}' for a, b in free_segs if b - a >= 15]
        )

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
            'heavy_work_limit_minutes': heavy_limit,
            'free_windows': free_windows,
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
            'heavy_work_limit_minutes': d['heavy_work_limit_minutes'],
        }
        if d.get('free_windows'):
            item['free_windows'] = d['free_windows']
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
            rem_min = min(rem_max, max(1, round(rem_max * 0.85)))
    return confirmed, rem_min, rem_max


_HARD_EXPIRY_CUES = (
    'после срока недоступн',
    'после дедлайна недоступн',
    'после срока не принима',
    'после дедлайна не принима',
    'работа недоступна',
    'сдача недоступна',
    'доступ закрыва',
    'доступ закроет',
    'форма закроет',
    'тест закроет',
    'закрывается после',
    'автоматически закро',
    'сгорает',
    'жесткий дедлайн',
    'строгий дедлайн',
    'строго до',
    'нельзя сдать позже',
    'позже не принима',
    'hard deadline',
    'strict deadline',
)
_HARD_EXPIRY_RE = re.compile(
    r'\b(?:после|по)\s+(?:срока|дедлайна|истечени\w*)\b[^.!?\n]{0,40}\b(?:недоступн|не\s+принима|закры|нельзя)\b'
)
_SOFT_DEADLINE_CUES = (
    'мягкий дедлайн',
    'мягкий срок',
    'ориентировочный срок',
    'ориентировочный дедлайн',
    'можно сдать позже',
    'можно позже',
    'не жесткий',
    'не строгий',
    'soft deadline',
)
_SOFT_NEGATION_CUES = ('не мягкий', 'не мягк', 'нельзя позже', 'нельзя сдать позже', 'не ориентировоч')

_SOFTENER_RE = re.compile(
    r'\b(?:'
    r'желательно|по\s+возможности|было\s+бы\s+хорошо|лучше|хотелось\s+бы|'
    r'постарайся|если\s+получится|полегче|поменьше|побольше\s+отдыхать|'
    r'щадящ\w*|preferably|ideally|try\s+to|lighter'
    r')\b'
)
_ABSOLUTE_BLOCK_RE = re.compile(
    r'\b(?:'
    r'освободи|освободить|оставь\s+свободн\w*|оставить\s+свободн\w*|'
    r'сделай\s+свободн\w*|полностью\s+свободн\w*|ничего\s+не\s+планируй|'
    r'не\s+планируй\s+ничего|ничего\s+не\s+ставь|не\s+ставь\s+ничего|'
    r'вообще\s+ничего|без\s+работы|без\s+задач|не\s+работаю|'
    r'день\s+отдыха|полный\s+отдых|выходной|занят\s+весь\s+день|занята\s+весь\s+день|'
    r'недоступен|недоступна|не\s+могу\s+работать|'
    r'no\s+work|leave\b[^.!?\n]{0,20}\bfree|keep\b[^.!?\n]{0,20}\bfree|'
    r"don'?t\s+schedule\s+anything|nothing\s+on"
    r')\b'
)
_NEGATED_BLOCK_RE = re.compile(r'\bне\s+(?:освобождай|оставляй\s+свободн\w*|делай\s+свободн\w*)\b')
_TASK_DAY_FORBID_RE = re.compile(r"\b(?:не\s+ставь|не\s+планируй|убери|не\s+назначай|don'?t\s+put|don'?t\s+schedule)\b")
_CLAUSE_VERB_RE = re.compile(
    r'\b(?:'
    r'освобод\w*|остав\w*|сдела\w*|не\s+план\w*|не\s+став\w*|перенес\w*|сдвин\w*|'
    r'растян\w*|добав\w*|потрат\w*|выполн\w*|заним\w*|поработ\w*|убер\w*|'
    r'желательн\w*|лучше|постарай\w*|попробу\w*|хочу|нужно|надо|планирую|'
    r'уже\s+сделал\w*|перевез\w*|позанимал\w*'
    r')\b'
)
_WEEKDAY_PATTERNS = (
    (0, re.compile(r'\b(?:понедельник\w*|пн|monday)\b')),
    (1, re.compile(r'\b(?:вторник\w*|вт|tuesday)\b')),
    (2, re.compile(r'\b(?:сред[ауеы]|ср|wednesday)\b')),
    (3, re.compile(r'\b(?:четверг\w*|чт|thursday)\b')),
    (4, re.compile(r'\b(?:пятниц[ауеы]|пт|friday)\b')),
    (5, re.compile(r'\b(?:суббот[ауеы]|сб|saturday)\b')),
    (6, re.compile(r'\b(?:воскресен[ьейя]|вс|sunday)\b')),
)
_ROUTE_ASSERT_RE = re.compile(
    r'\b(?:по\s+дороге|по\s+пути|по\s+маршруту|рядом\s+с|недалеко\s+от|поблизости\s+от|заехать\s+по|зайти\s+по|on\s+the\s+way|along\s+the\s+route|nearby)\b'
)
_CONDITIONAL_CUE_RE = re.compile(
    r'\b(?:если|при\s+условии|возможно|может\s+быть|если\s+удобно|если\s+окажется|предположительно|if\b|maybe|assuming)\b'
)
_ROUTE_FACT_RE = re.compile(
    r'\b(?:по\s+дороге|по\s+пути|по\s+маршруту|маршрут\w*|рядом|недалеко|поблизости|заехать|зайти|on\s+the\s+way|along\s+the\s+route|nearby)\b'
)


def _trusted_task_text(task: dict) -> str:
    return norm_text(' '.join(
        str(task.get(field) or '')
        for field in ('title', 'description', 'owner_notes', 'task_note', 'full_context')
    ))


def _has_hard_expiry_cue(task: dict) -> bool:
    text = _trusted_task_text(task)
    if not text:
        return False
    return any(cue in text for cue in _HARD_EXPIRY_CUES) or bool(_HARD_EXPIRY_RE.search(text))


def _has_explicit_soft_deadline_cue(task: dict) -> bool:
    text = _trusted_task_text(task)
    if not text or any(neg in text for neg in _SOFT_NEGATION_CUES):
        return False
    return any(cue in text for cue in _SOFT_DEADLINE_CUES)


def effective_deadline_policy(task: dict, proposed_policy: str, now_dt: datetime) -> tuple[str, bool, bool]:
    """Определяет (effective_policy, enforce_deadline, is_overdue) из доверенных данных задачи."""
    dl_dt = parse_dt(task.get('deadline'), timezone.utc)
    if dl_dt is None:
        return 'soft', False, False
    is_overdue = dl_dt <= now_dt
    if _has_hard_expiry_cue(task) or proposed_policy == 'expires':
        return 'expires', True, is_overdue
    if _has_explicit_soft_deadline_cue(task) and proposed_policy == 'soft':
        return 'soft', False, is_overdue
    return 'finish_before', not is_overdue, is_overdue


def _split_feedback_clauses(text_norm: str) -> list[str]:
    if not text_norm:
        return []
    clauses: list[str] = []
    for seg in re.split(r'[.;!?\n]+|\s+[—-]\s+|\s+(?:но|а)\s+', text_norm):
        seg = seg.strip()
        if not seg:
            continue
        if ',' not in seg:
            clauses.append(seg)
            continue
        pieces = [p.strip() for p in seg.split(',') if p.strip()]
        merged: list[str] = []
        for piece in pieces:
            if not merged:
                merged.append(piece)
            elif _CLAUSE_VERB_RE.search(merged[-1]) and _CLAUSE_VERB_RE.search(piece):
                merged.append(piece)
            else:
                merged[-1] = f'{merged[-1]}, {piece}'
        clauses.extend(merged)
    return clauses


def _resolve_dates_in_clause(clause_norm: str, days: list[dict]) -> list[str]:
    if not days:
        return []
    today_date = calendar_date.fromisoformat(days[0]['date'])
    days_by_iso = {d['date']: d for d in days}
    found: list[str] = []
    is_next_week = bool(re.search(r'\bследующ\w*|\bnext\s+week\b', clause_norm))

    def add_iso(d_iso: str) -> None:
        if d_iso in days_by_iso and d_iso not in found:
            found.append(d_iso)

    for m in re.finditer(r'\b(\d{4}-\d{2}-\d{2})\b', clause_norm):
        add_iso(m.group(1))
    for m in re.finditer(r'\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b', clause_norm):
        day_num, mon_num, yr_raw = int(m.group(1)), int(m.group(2)), m.group(3)
        years = [int(yr_raw)] if yr_raw else [today_date.year, today_date.year + 1]
        for yr in years:
            try:
                cand = calendar_date(yr, mon_num, day_num)
            except ValueError:
                continue
            if not yr_raw and cand < today_date - timedelta(days=30):
                continue
            if cand.isoformat() in days_by_iso:
                add_iso(cand.isoformat())
                break

    if re.search(r'\b(?:сегодня|today)\b', clause_norm):
        add_iso(today_date.isoformat())
    if re.search(r'\b(?:послезавтра|day\s+after\s+tomorrow)\b', clause_norm):
        add_iso((today_date + timedelta(days=2)).isoformat())
    if re.search(r'\b(?:завтра|tomorrow)\b', clause_norm):
        add_iso((today_date + timedelta(days=1)).isoformat())
    if re.search(r'\b(?:выходн(?:ые|ым|ыми|ых)|weekend)\b', clause_norm):
        days_until_mon = (7 - today_date.weekday()) % 7 or 7
        next_mon = (today_date + timedelta(days=days_until_mon)).isoformat()
        for d in days:
            if d['weekday'] not in (5, 6):
                continue
            if is_next_week:
                if next_mon <= d['date'] < (today_date + timedelta(days=days_until_mon + 7)).isoformat():
                    add_iso(d['date'])
            elif d['date'] < next_mon:
                add_iso(d['date'])

    for wd, pattern in _WEEKDAY_PATTERNS:
        if not pattern.search(clause_norm):
            continue
        matches = [d for d in days if d['weekday'] == wd]
        if not matches:
            continue
        if is_next_week:
            later = [d for d in matches if d['offset'] >= 7]
            chosen = later[0] if later else matches[-1]
        else:
            chosen = matches[0]
        add_iso(chosen['date'])

    return found


def extract_explicit_blocked_days(
    user_feedback: str,
    days: list[dict],
    tasks: list[dict] | None = None,
) -> list[dict]:
    """Извлекает из user_feedback явные жёсткие временные запреты (blocked_day / blocked_task_day)."""
    text_norm = norm_text(user_feedback)
    if not text_norm or not days:
        return []

    constraints: list[dict] = []
    seen: set[tuple[str, str, int | None]] = set()
    for clause in _split_feedback_clauses(text_norm):
        if _NEGATED_BLOCK_RE.search(clause):
            continue
        dates = _resolve_dates_in_clause(clause, days)
        if not dates:
            continue
        has_softener = bool(_SOFTENER_RE.search(clause))
        has_abs = bool(_ABSOLUTE_BLOCK_RE.search(clause))
        has_task_forbid = bool(_TASK_DAY_FORBID_RE.search(clause))

        if has_abs and not has_softener:
            for d_iso in dates:
                key = ('blocked_day', d_iso, None)
                if key not in seen:
                    seen.add(key)
                    constraints.append({
                        'kind': 'blocked_day',
                        'date': d_iso,
                        'scope': 'once',
                        'strength': 'hard',
                        'task_id': None,
                    })
            continue

        if has_task_forbid and not has_softener and tasks:
            scored = [(task_matches_text(t, clause), t['id']) for t in tasks]
            scored.sort(reverse=True)
            if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                tid = scored[0][1]
                for d_iso in dates:
                    key = ('blocked_task_day', d_iso, tid)
                    if key not in seen:
                        seen.add(key)
                        constraints.append({
                            'kind': 'blocked_task_day',
                            'date': d_iso,
                            'scope': 'task',
                            'strength': 'hard',
                            'task_id': tid,
                        })
    return constraints


def _soft_only_dates(user_feedback: str, days: list[dict], hard_dates: set[str]) -> set[str]:
    text_norm = norm_text(user_feedback)
    if not text_norm or not days:
        return set()
    soft_dates: set[str] = set()
    for clause in _split_feedback_clauses(text_norm):
        if _SOFTENER_RE.search(clause) and not _ABSOLUTE_BLOCK_RE.search(clause):
            for d_iso in _resolve_dates_in_clause(clause, days):
                if d_iso not in hard_dates:
                    soft_dates.add(d_iso)
    return soft_dates


def _has_verified_route_fact(
    task: dict,
    days_by_date: dict[str, dict],
    period_dates: list[str],
    user_feedback: str,
    user_answers: list[dict] | None,
    user_memory: list[dict] | None,
) -> bool:
    parts = [_trusted_task_text(task), norm_text(user_feedback)]
    for ans in (user_answers or []):
        if isinstance(ans, dict):
            parts.append(norm_text(ans.get('answer') or ''))
    for fact in (user_memory or []):
        if isinstance(fact, dict):
            parts.append(norm_text(fact.get('text') or ''))
    for d_iso in period_dates:
        d_info = days_by_date.get(d_iso) or {}
        for c in d_info.get('commitments', []):
            parts.append(norm_text(f"{c.get('title', '')} {c.get('summary', '')}"))
    corpus = ' '.join(p for p in parts if p)
    return bool(_ROUTE_FACT_RE.search(corpus))


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
    user_feedback: str = '',
    user_answers: list[dict] | None = None,
    user_memory: list[dict] | None = None,
    extra_blocked_dates: list[str] | None = None,
    verify_context_hints: bool = True,
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

    # Проверка progress_updates и preferences (подтверждённый прогресс меняется только по явному сообщению).
    progress_override: dict[int, int] = {}
    seen_progress_tasks: set[int] = set()
    for upd in proposal.progress_updates:
        if upd.task_id not in expected_set:
            issues.append({'code': 'unknown_task', 'task_id': upd.task_id, 'message': f'progress_updates: неизвестная задача {upd.task_id}.'})
            continue
        if upd.task_id in seen_progress_tasks:
            issues.append({'code': 'duplicate_progress_update', 'task_id': upd.task_id, 'message': f'progress_updates: задача {upd.task_id} указана дважды.'})
            continue
        seen_progress_tasks.add(upd.task_id)
        assess_item = assessments.get(upd.task_id)
        ok_progress, err_code, err_msg = verify_progress_update(
            upd.model_dump(),
            tasks_by_id[upd.task_id],
            user_feedback=user_feedback,
            user_answers=user_answers,
            all_tasks=tasks,
            total_minutes_min=assess_item.total_minutes_min if assess_item else None,
            total_minutes_max=assess_item.total_minutes_max if assess_item else None,
        )
        if not ok_progress:
            issues.append({'code': err_code, 'task_id': upd.task_id, 'message': err_msg})
            continue
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
        if pref.scope == 'persistent' and pref.blocked_dates:
            issues.append({'code': 'invalid_preference', 'message': 'Устойчивые привычки (scope=persistent) не должны задавать blocked_dates.'})
        for d_iso in pref.blocked_dates:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d_iso):
                issues.append({'code': 'invalid_preference', 'message': f'Некорректная дата в blocked_dates: {d_iso}.'})
                break
            try:
                calendar_date.fromisoformat(d_iso)
            except ValueError:
                issues.append({'code': 'invalid_preference', 'message': f'Несуществующая дата в blocked_dates: {d_iso}.'})
                break

    if issues:
        return proposal, issues, days, {}

    issues.extend(_check_dependencies(assessments, expected_set))
    if issues:
        return proposal, issues, days, {}

    # Временные жёсткие ограничения из текста владельца, /refresh и классификации hard preferences.
    hard_blocked_dates: set[str] = set()
    hard_task_blocked: dict[int, set[str]] = {}
    for item in extract_explicit_blocked_days(user_feedback, days, tasks=tasks):
        if item['kind'] == 'blocked_day':
            hard_blocked_dates.add(item['date'])
        elif item['kind'] == 'blocked_task_day' and item['task_id'] is not None:
            hard_task_blocked.setdefault(item['task_id'], set()).add(item['date'])
    for d_iso in (extra_blocked_dates or ()):
        if d_iso in days_by_date:
            hard_blocked_dates.add(d_iso)
    if user_feedback.strip():
        soft_dates = _soft_only_dates(user_feedback, days, hard_blocked_dates)
        for pref in proposal.preferences:
            if pref.strength != 'hard' or not pref.blocked_dates:
                continue
            for d_iso in pref.blocked_dates:
                if d_iso not in days_by_date or d_iso in soft_dates:
                    continue
                if pref.scope == 'once':
                    hard_blocked_dates.add(d_iso)
                elif pref.scope == 'task' and pref.task_id in expected_set:
                    hard_task_blocked.setdefault(pref.task_id, set()).add(d_iso)

    overrides = (adjustments or {}).get('estimate_overrides') or {}
    task_pins = (adjustments or {}).get('task_pins') or {}
    budgets: dict[int, dict] = {}
    meta: dict[int, dict] = {}
    for tid, item in assessments.items():
        task = tasks_by_id[tid]
        confirmed, rem_min, rem_max = _task_budget(item, task, overrides, progress_override)
        eff_policy, enforce_dl, is_overdue = effective_deadline_policy(task, item.deadline_policy, now_dt)
        dl_dt = parse_dt(task.get('deadline'), timezone.utc)
        dl_local = dl_dt.astimezone(zone) if dl_dt else None
        trusted_av_dt = parse_dt(task.get('available_from'), zone)
        model_av_dt = parse_dt(item.available_from, zone)
        if trusted_av_dt is not None and model_av_dt is not None:
            av_dt = max(trusted_av_dt, model_av_dt)
        else:
            av_dt = trusted_av_dt or model_av_dt
        av_local = av_dt.astimezone(zone) if av_dt else None
        budgets[tid] = {
            'confirmed': confirmed,
            'rem_min': rem_min,
            'rem_max': rem_max,
            'target': _target_minutes(rem_min, rem_max),
            'effective_policy': eff_policy,
            'is_overdue': is_overdue,
            'load_type': item.load_type,
            'constraint_conflict': None,
            'blocked_dates': sorted(hard_blocked_dates),
        }
        meta[tid] = {
            'deadline': dl_dt,
            'deadline_local': dl_local,
            'available_local': av_local,
            'policy': eff_policy,
            'enforce_deadline': enforce_dl,
            'is_overdue': is_overdue,
            'depends_on': list(item.depends_on),
        }

    # Распространение раннего available_from и жёсткого deadline по зависимостям (подготовку можно делать заранее).
    changed = True
    while changed:
        changed = False
        for tid, info in meta.items():
            for dep_id in info['depends_on']:
                dep = meta[dep_id]
                if info['enforce_deadline'] and info['deadline'] is not None:
                    if dep['deadline'] is None or dep['deadline'] > info['deadline']:
                        dep['deadline'] = info['deadline']
                        dep['deadline_local'] = info['deadline_local']
                        dep['enforce_deadline'] = True
                        if dep['policy'] == 'soft':
                            dep['policy'] = info['policy']
                        changed = True
                    elif not dep['enforce_deadline'] and dep['deadline'] == info['deadline']:
                        dep['enforce_deadline'] = True
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

    # Конфликт работы, отмеченных дней отдыха и явных запретов владельца; проверка context_hint.
    for period_idx, period in enumerate(proposal.periods):
        if period.kind != 'work' or period.task_id is None or period.task_id not in expected_set:
            continue
        if verify_context_hints and period.context_hint and period.context_hint.strip():
            hint_norm = norm_text(period.context_hint)
            if _ROUTE_ASSERT_RE.search(hint_norm) and not _CONDITIONAL_CUE_RE.search(hint_norm):
                p_dates = [a.date for a in period.allocations if a.date in days_by_date]
                if not _has_verified_route_fact(
                    tasks_by_id[period.task_id],
                    days_by_date,
                    p_dates,
                    user_feedback,
                    user_answers,
                    user_memory,
                ):
                    issues.append({
                        'code': 'unverified_context_hint',
                        'period_index': period_idx,
                        'task_id': period.task_id,
                        'message': (
                            f'Период #{period_idx} утверждает удобный маршрут или близость («{period.context_hint.strip()}»), '
                            'но этого факта нет в задаче, памяти или календаре. Используй условную формулировку («если по дороге…») либо убери домысел.'
                        ),
                    })

    for (tid, date_iso), _mins in task_day_minutes.items():
        if date_iso in rest_dates:
            issues.append({'code': 'rest_day_conflict', 'task_id': tid, 'date': date_iso, 'period_indices': day_period_indices.get(date_iso, []), 'message': f'День {date_iso} помечен как отдых, но на него назначена задача {tid}.'})
        if date_iso in hard_blocked_dates:
            issues.append({
                'code': 'blocked_by_user_constraint',
                'task_id': tid,
                'date': date_iso,
                'period_indices': day_period_indices.get(date_iso, []),
                'message': f'День {date_iso} закрыт для работы по ограничению владельца, но туда назначена задача {tid}.',
            })
        elif date_iso in hard_task_blocked.get(tid, set()):
            issues.append({
                'code': 'blocked_by_user_constraint',
                'task_id': tid,
                'date': date_iso,
                'period_indices': day_period_indices.get(date_iso, []),
                'message': f'Задача {tid} запрещена к планированию на {date_iso} по ограничению владельца.',
            })

    # Проверка дневной ёмкости и контекстной нагрузки.
    daily_high_effort: dict[str, list[tuple[int, int]]] = {}
    for (tid, date_iso), mins in task_day_minutes.items():
        if assessments[tid].effort == 'high':
            daily_high_effort.setdefault(date_iso, []).append((tid, mins))

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
        dl_iso = dl_local.date().isoformat() if (info['enforce_deadline'] and dl_local is not None) else None

        if info['policy'] == 'expires' and info['is_overdue'] and assigned > 0:
            overdue_iso = dl_local.date().isoformat() if dl_local is not None else ''
            issues.append({
                'code': 'deadline_passed',
                'task_id': tid,
                'message': f'Задача {tid}: жёсткий срок ({overdue_iso}) уже прошёл — работа после него недоступна.',
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
                day_info,
                day_obj,
                av_local,
                dl_local,
                policy=info['policy'],
                enforce_deadline=info['enforce_deadline'],
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

        needs_risk_overdue = info['is_overdue'] and info['policy'] != 'soft' and assigned > 0
        needs_risk_shortfall = info['enforce_deadline'] and dl_iso is not None and dl_iso <= horizon_end and assigned < budget['rem_min']
        if (needs_risk_overdue or needs_risk_shortfall) and not (item.deadline_risk and item.deadline_risk.strip()):
            risk_date = dl_local.date().isoformat() if dl_local else ''
            issues.append({
                'code': 'missing_deadline_risk',
                'task_id': tid,
                'message': f'Задача {tid}: к сроку {risk_date} запланировано {assigned} из {budget["rem_min"]} мин или срок уже прошёл — укажи причину риска в deadline_risk.',
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
            if target_date in hard_blocked_dates or target_date in hard_task_blocked.get(tid, set()):
                budget['constraint_conflict'] = (
                    f'{tasks_by_id[tid]["title"]}: закреплено на {target_date}, но этот день закрыт ограничением владельца.'
                )
            else:
                dl_ok = True
                if info['enforce_deadline'] and dl_local is not None:
                    _, _, pin_avail = available_for_task_on_day(
                        days_by_date[target_date],
                        calendar_date.fromisoformat(target_date),
                        av_local,
                        dl_local,
                        policy=info['policy'],
                        enforce_deadline=True,
                    )
                    dl_ok = pin_avail > 0
                av_ok = av_local is None or target_date >= av_local.date().isoformat()
                if dl_ok and av_ok and target_date not in dates:
                    issues.append({
                        'code': 'violates_task_pin',
                        'task_id': tid,
                        'date': target_date,
                        'message': f'Владелец закрепил задачу {tid} за {target_date}. Включи её в этот день либо при невозможности опиши конфликт в deadline_risk.',
                    })

    # Проверка контекстного перегруза высокой когнитивной/тяжёлой нагрузкой в плотные дни при наличии более свободных дней.
    for date_iso, entries in daily_high_effort.items():
        day_info = days_by_date[date_iso]
        heavy_cap = day_info['heavy_work_limit_minutes']
        heavy_total = sum(m for _, m in entries)
        if day_info['busy_minutes'] < 360 or day_info['day_mode'] == 'focused' or heavy_total <= heavy_cap:
            continue
        for tid, _mins in entries:
            if (task_pins.get(str(tid)) or {}).get('target_date') == date_iso:
                continue
            info = meta[tid]
            has_free_alt = False
            for alt in days:
                alt_iso = alt['date']
                if alt_iso == date_iso or alt['busy_minutes'] >= 360 or alt['max_work_minutes'] < 90:
                    continue
                if alt_iso in rest_dates or alt_iso in hard_blocked_dates or alt_iso in hard_task_blocked.get(tid, set()):
                    continue
                if alt['max_work_minutes'] - daily_load.get(alt_iso, 0) < 30:
                    continue
                _, _, alt_avail = available_for_task_on_day(
                    alt,
                    calendar_date.fromisoformat(alt_iso),
                    info['available_local'],
                    info['deadline_local'],
                    policy=info['policy'],
                    enforce_deadline=info['enforce_deadline'],
                )
                if alt_avail >= 30:
                    has_free_alt = True
                    break
            if has_free_alt:
                issues.append({
                    'code': 'heavy_day_overload',
                    'task_id': tid,
                    'date': date_iso,
                    'excess_minutes': heavy_total - heavy_cap,
                    'available_minutes': heavy_cap,
                    'period_indices': day_period_indices.get(date_iso, []),
                    'message': (
                        f'День {date_iso} уже плотно занят ({day_info["busy_minutes"]} мин); '
                        f'для задач с effort=high допустимо до {heavy_cap} мин (назначено {heavy_total} мин), '
                        'а на более свободных днях есть доступное окно.'
                    ),
                })
                break

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
    total_capacity = sum(d['max_work_minutes'] for d in days if d['date'] not in hard_blocked_dates)
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
    blocked_dates: list[str] = []
    for tid in proposal.task_ids:
        item = assessments[tid]
        b = budgets[tid]
        if not blocked_dates and b.get('blocked_dates'):
            blocked_dates = list(b['blocked_dates'])
        eff_policy = b.get('effective_policy') or effective_deadline_policy(tasks_by_id[tid], item.deadline_policy, now_dt)[0]
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
            'deadline_policy': eff_policy,
            'available_from': item.available_from.isoformat() if item.available_from else None,
            'depends_on': list(item.depends_on),
            'effort': item.effort,
            'load_type': b.get('load_type') or item.load_type,
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
            eff_policy = plans[tid]['deadline_policy']
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
                'completed_work_minutes': budgets[tid]['confirmed'],
                'allocations': alloc_list,
                'completes_task': completes,
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'deadline_policy': eff_policy,
                'deadline_risk': assess.deadline_risk,
                'tasks': [{
                    'task_id': tid,
                    'title': task['title'],
                    'goal': p.goal.strip(),
                    'minutes': mins,
                    'hours_label': h_label,
                    'completes_task': completes,
                    'deadline': dl_dt.isoformat() if dl_dt else None,
                    'deadline_policy': eff_policy,
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

    # Риски сроков, конфликты ограничений и незапланированные остатки.
    conflicts = []
    remaining = []
    for tid in proposal.task_ids:
        b = budgets[tid]
        assess = assessments[tid]
        task = tasks_by_id[tid]
        eff_policy = plans[tid]['deadline_policy']
        is_overdue = bool(b.get('is_overdue'))
        constraint_conflict = b.get('constraint_conflict')
        unplanned = max(0, b['target'] - assigned_by_task[tid])
        remaining.append({'task_id': tid, 'remaining_work_minutes': unplanned})
        deficit = max(0, b['rem_min'] - assigned_by_task[tid])
        if constraint_conflict:
            dl_dt = parse_dt(task.get('deadline'), timezone.utc)
            conflicts.append({
                'task_id': tid,
                'title': task['title'],
                'kind': 'constraint',
                'policy': eff_policy,
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'infeasible': True,
                'deficit_minutes': deficit,
                'deficit_label': format_hours(deficit) if deficit else '',
                'reason': constraint_conflict,
            })
        elif assess.deadline_risk or (eff_policy != 'soft' and (deficit > 0 or (is_overdue and b['rem_max'] > 0))):
            dl_dt = parse_dt(task.get('deadline'), timezone.utc)
            infeasible = eff_policy != 'soft' and (deficit > 0 or (eff_policy == 'expires' and is_overdue and b['rem_max'] > 0))
            default_reason = (
                f"{task['title']}: срок уже прошёл."
                if is_overdue
                else f"{task['title']}: недостаточно времени до срока."
            )
            conflicts.append({
                'task_id': tid,
                'title': task['title'],
                'kind': 'deadline',
                'policy': eff_policy,
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'infeasible': infeasible,
                'deficit_minutes': deficit,
                'deficit_label': format_hours(deficit) if deficit else '',
                'reason': assess.deadline_risk or default_reason,
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
        'blocked_dates': blocked_dates,
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
                'evidence_quote': u.evidence_quote,
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
        raw_load = old_p.get('load_type')
        load_type = raw_load if raw_load in ('cognitive', 'practical', 'mixed') else 'mixed'
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
            load_type=load_type,
            reason=str(old_reason.get('reason') or 'По приоритету и срокам')[:200],
            impact=str(old_reason.get('impact') or tasks_by_id[tid]['title'])[:180],
            deadline_risk=old_reason.get('deadline_risk'),
        )
        assessments.append(assess)
        confirmed, rem_min, rem_max = _task_budget(assess, tasks_by_id[tid], overrides)
        target = _target_minutes(rem_min, rem_max)
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
        extra_blocked_dates=prev_schedule.get('blocked_dates') or [],
        verify_context_hints=False,
    )
    if issues:
        summary = '; '.join(i.get('message', i.get('code', 'ошибка')) for i in issues[:2])
        raise ValueError(f'Сохранённый план больше не соответствует ограничениям — требуется перепланирование Ameba: {summary}')

    materialized = materialize_plan(proposal, tasks, days, validated_budgets, week, now)
    if previous.get('explanation'):
        materialized['explanation'] = previous['explanation']
    return previous | materialized
