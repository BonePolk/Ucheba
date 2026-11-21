"""Минимальное ядро Ameba: контракт LLM-планирования, проверка объективных ограничений, материализация и /refresh."""
from __future__ import annotations

from datetime import date as calendar_date, datetime, timedelta, timezone
import re
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from common.calendar import occurs
from common.schedule import week_parity

WEEKDAYS_SHORT = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# --- Схема инструмента propose_plan (strict=True: без default, extra='forbid') ---

class TaskPlanItem(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    remaining_minutes: int = Field(
        ge=0, le=10000,
        description='Реалистичная оценка оставшейся активной работы с учётом уже подтверждённого completed_work_minutes.',
    )
    reason: str = Field(min_length=1, max_length=200, description='Краткая причина места задачи в приоритетах.')
    deadline_risk: str | None = Field(
        max_length=240,
        description='Причина риска не успеть в срок или конфликта ограничений; иначе null.',
    )


class PlanPeriod(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['work', 'rest']
    task_id: int | None = Field(description='ID задачи для kind=work; null для kind=rest.')
    start_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    estimated_minutes: int = Field(
        ge=0, le=5000,
        description='Ориентировочный объём активной работы за период (>0 для work, 0 для rest).',
    )
    goal: str = Field(min_length=1, max_length=180, description='Что сделать за период или характер отдыха.')
    context_hint: str | None = Field(
        max_length=220,
        description='Подтверждённая привязка к расписанию или контексту без домыслов; иначе null.',
    )
    reason: str | None = Field(max_length=260, description='Краткое объяснение выбора периода и объёма.')


class PreferenceNote(BaseModel):
    model_config = {'extra': 'forbid'}
    text: str = Field(min_length=1, max_length=400)
    scope: Literal['once', 'task', 'persistent'] = Field(
        description='once — на текущий план; task — контекст конкретной задачи; persistent — устойчивая привычка.',
    )
    task_id: int | None = Field(description='ID задачи для scope=task; иначе null.')
    key: str | None = Field(max_length=100, description='Короткий латинский ключ для persistent (вида ameba.<тема>) либо null.')


class PlanProposal(BaseModel):
    model_config = {'extra': 'forbid'}
    task_ids: list[int] = Field(description='Приоритетный порядок всех актуальных задач.')
    tasks: list[TaskPlanItem] = Field(min_length=1)
    periods: list[PlanPeriod] = Field(description='Гибкие периоды работы и при необходимости дни отдыха.')
    blocked_dates: list[str] = Field(
        description='Даты YYYY-MM-DD, явно закрытые владельцем для работы в этом расчёте; [] если нет.',
    )
    preferences: list[PreferenceNote] = Field(description='Пожелания владельца с областью действия.')
    explanation: str = Field(min_length=1, max_length=400, description='Короткое резюме плана и ключевых компромиссов.')


PROPOSE_TOOL = {
    'type': 'function',
    'name': 'propose_plan',
    'strict': True,
    'description': (
        'Предложить интеллектуальный план: приоритеты и оставшийся объём задач, гибкие периоды работы и отдыха, '
        'заблокированные даты, привязки к контексту и область пожеланий (once/task/persistent).'
    ),
    'parameters': PlanProposal.model_json_schema(),
}


# --- Время и фактический календарь без эвристик ---

def parse_dt(raw: datetime | str | None, default_tz=timezone.utc) -> datetime | None:
    if raw is None:
        return None
    value = datetime.fromisoformat(raw.replace('Z', '+00:00')) if isinstance(raw, str) else raw
    return value.replace(tzinfo=default_tz) if value.tzinfo is None else value


def format_hours(minutes: int) -> str:
    mins = max(0, int(minutes))
    if mins <= 0:
        return 'выполнено'
    if mins < 60:
        return f'{mins} мин'
    rounded = round((mins / 60) * 2) / 2
    if rounded >= 10:
        rounded = float(round(rounded))
    label = str(int(rounded)) if rounded == int(rounded) else f'{rounded:.1f}'
    return f'≈ {label} ч'


def restore_week(week, timezone_name: str = 'UTC') -> dict:
    if week:
        configured = dict(week)
        configured.setdefault('timezone', timezone_name)
        configured.setdefault('days', [[] for _ in range(7)])
        configured.setdefault('events', [])
        configured.setdefault('calendar_events', [])
        return configured
    return {'timezone': timezone_name, 'days': [[] for _ in range(7)], 'events': [], 'calendar_events': []}


def compute_horizon(tasks: list[dict], today: calendar_date, zone: ZoneInfo) -> int:
    horizon = 14
    for task in tasks:
        dl = parse_dt(task.get('deadline'), timezone.utc)
        if dl is not None:
            delta = (dl.astimezone(zone).date() - today).days + 1
            if 14 < delta <= 21:
                horizon = max(horizon, delta)
    return horizon


def _events_for_day(week: dict, day: calendar_date) -> list[dict]:
    parity = week_parity(day, week.get('parity_anchor'))
    days_list = week.get('days') or [[] for _ in range(7)]
    slots = [dict(slot) for slot in days_list[day.weekday()] if slot.get('week_pattern', 'all') in ('all', parity)]
    slots += [dict(slot) for slot in week.get('events', []) if slot.get('date') == day.isoformat()]
    slots += [dict(event) for event in week.get('calendar_events', []) if occurs(event, day)]
    return slots


def calendar_days(week: dict, now: str, horizon_days: int = 14, adjustments: dict | None = None) -> list[dict]:
    """Собирает фактические события и ручные режимы дней горизонта без эвристических лимитов."""
    overrides = (adjustments or {}).get('day_overrides') or {}
    zone = ZoneInfo(week.get('timezone', 'UTC'))
    today = parse_dt(now, timezone.utc).astimezone(zone).date()
    days: list[dict] = []
    for offset in range(horizon_days):
        day = today + timedelta(days=offset)
        day_iso = day.isoformat()
        events = [
            {
                'title': ev.get('title') or ev.get('kind', 'busy'),
                'kind': ev.get('kind', 'busy'),
                'start': ev['start'],
                'end': ev['end'],
                **({'summary': ev['summary']} if ev.get('summary') else {}),
            }
            for ev in _events_for_day(week, day)
            if ev.get('end', '') > ev.get('start', '')
        ]
        events.sort(key=lambda e: (e['start'], e['end']))
        days.append({
            'date': day_iso,
            'weekday': WEEKDAYS_SHORT[day.weekday()],
            'weekday_idx': day.weekday(),
            'mode': overrides.get(day_iso) or 'normal',
            'events': events,
        })
    return days


def calendar_overview(days: list[dict]) -> list[dict]:
    return [
        {
            'date': d['date'],
            'weekday': d['weekday'],
            **({'mode': d['mode']} if d['mode'] != 'normal' else {}),
            'events': d['events'],
        }
        for d in days
    ]


def _hhmm_to_min(hhmm: str) -> int:
    return 1440 if hhmm == '24:00' else int(hhmm[:2]) * 60 + int(hhmm[3:])


def _day_busy_minutes(events: list[dict]) -> int:
    intervals = sorted((_hhmm_to_min(e['start']), _hhmm_to_min(e['end'])) for e in events if e.get('kind') in ('busy', 'task'))
    merged: list[tuple[int, int]] = []
    for a, b in intervals:
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return sum(b - a for a, b in merged)


def _protected_dates(
    proposal: PlanProposal,
    days_by_date: dict[str, dict],
    adjustments: dict | None,
    extra_blocked_dates: list[str] | tuple[str, ...] | None,
) -> set[str]:
    protected = {d for d in proposal.blocked_dates if d in days_by_date}
    protected.update(d for d in (extra_blocked_dates or ()) if d in days_by_date)
    for d_iso, mode in ((adjustments or {}).get('day_overrides') or {}).items():
        if mode == 'free' and d_iso in days_by_date:
            protected.add(d_iso)
    return protected


def _effective_rem(item: TaskPlanItem, overrides: dict) -> int:
    manual = overrides.get(str(item.task_id))
    return max(0, int(manual)) if manual is not None else int(item.remaining_minutes)


# --- Минимальная проверка объективных ограничений ---

def validate_proposal(
    raw_arguments: str | dict | PlanProposal,
    tasks: list[dict],
    week: dict,
    now: str,
    *,
    adjustments: dict | None = None,
    extra_blocked_dates: list[str] | tuple[str, ...] | None = None,
) -> tuple[PlanProposal, list[dict], list[dict]]:
    """Проверяет целостность ссылок, дат, защищённых дней, доверенных доступности и сроков."""
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
    today = now_dt.astimezone(zone).date()
    today_iso = today.isoformat()
    horizon_days = compute_horizon(tasks, today, zone)
    days = calendar_days(week, now_dt.isoformat(), horizon_days=horizon_days, adjustments=adjustments)
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

    assessments: dict[int, TaskPlanItem] = {}
    for item in proposal.tasks:
        tid = item.task_id
        if tid not in expected_set:
            issues.append({'code': 'unknown_task', 'task_id': tid, 'message': f'Неизвестная задача {tid} в tasks.'})
        elif tid in assessments:
            issues.append({'code': 'duplicate_task', 'task_id': tid, 'message': f'Оценка задачи {tid} указана дважды.'})
        else:
            assessments[tid] = item
    missing_assess = [tid for tid in expected_ids if tid not in assessments]
    if missing_assess:
        issues.append({'code': 'missing_task', 'task_ids': missing_assess, 'message': f'Нет оценки для задач: {missing_assess}.'})

    for pref in proposal.preferences:
        if pref.scope == 'task' and (pref.task_id is None or pref.task_id not in expected_set):
            issues.append({'code': 'invalid_preference', 'task_id': pref.task_id, 'message': 'Для scope=task укажи существующий task_id.'})
        if pref.scope == 'persistent' and pref.task_id is not None:
            issues.append({'code': 'invalid_preference', 'message': 'Для scope=persistent поле task_id должно быть null.'})

    for d_iso in proposal.blocked_dates:
        if not _DATE_RE.fullmatch(d_iso):
            issues.append({'code': 'invalid_date', 'date': d_iso, 'message': f'Некорректная дата в blocked_dates: {d_iso}.'})
            continue
        try:
            calendar_date.fromisoformat(d_iso)
        except ValueError:
            issues.append({'code': 'invalid_date', 'date': d_iso, 'message': f'Несуществующая дата в blocked_dates: {d_iso}.'})

    if issues:
        return proposal, issues, days

    protected = _protected_dates(proposal, days_by_date, adjustments, extra_blocked_dates)
    overrides = (adjustments or {}).get('estimate_overrides') or {}
    task_pins = (adjustments or {}).get('task_pins') or {}
    work_periods: dict[int, list[PlanPeriod]] = {}

    for idx, p in enumerate(proposal.periods):
        try:
            calendar_date.fromisoformat(p.start_date)
            calendar_date.fromisoformat(p.end_date)
        except ValueError:
            issues.append({'code': 'invalid_period', 'period_index': idx, 'message': f'Период #{idx}: некорректная дата.'})
            continue
        if p.start_date > p.end_date:
            issues.append({'code': 'invalid_period', 'period_index': idx, 'message': f'Период #{idx}: start_date больше end_date.'})
            continue
        if p.start_date < today_iso:
            issues.append({'code': 'date_in_past', 'period_index': idx, 'message': f'Период #{idx} ({p.start_date}) начинается в прошлом.'})
            continue
        if p.end_date > horizon_end:
            issues.append({'code': 'outside_horizon', 'period_index': idx, 'message': f'Период #{idx} до {p.end_date} выходит за горизонт {horizon_end}.'})
            continue

        if p.kind == 'rest':
            if p.task_id is not None or p.estimated_minutes != 0:
                issues.append({'code': 'invalid_rest_period', 'period_index': idx, 'message': 'Для rest task_id должен быть null, а estimated_minutes — 0.'})
            continue

        tid = p.task_id
        if tid is None or tid not in expected_set:
            issues.append({'code': 'unknown_task', 'period_index': idx, 'task_id': tid, 'message': f'Период #{idx} ссылается на неизвестную задачу {tid}.'})
            continue
        if p.estimated_minutes <= 0:
            issues.append({'code': 'empty_work_period', 'period_index': idx, 'task_id': tid, 'message': f'Задача {tid}: рабочий период должен иметь estimated_minutes > 0.'})
            continue
        work_periods.setdefault(tid, []).append(p)

        blocked_hit = next((d for d in sorted(protected) if p.start_date <= d <= p.end_date), None)
        if blocked_hit is not None:
            issues.append({'code': 'blocked_day_violation', 'period_index': idx, 'task_id': tid, 'date': blocked_hit,
                           'message': f'Период задачи {tid} ({p.start_date}..{p.end_date}) задевает закрытый день {blocked_hit}.'})

        task = tasks_by_id[tid]
        av_dt = parse_dt(task.get('available_from'), zone)
        if av_dt is not None:
            av_iso = av_dt.astimezone(zone).date().isoformat()
            if p.start_date < av_iso:
                issues.append({'code': 'before_available_from', 'period_index': idx, 'task_id': tid,
                               'message': f'Задача {tid} начинается {p.start_date} до даты открытия {av_iso}.'})

        dl_dt = parse_dt(task.get('deadline'), timezone.utc)
        dl_type = task.get('deadline_type')
        if dl_dt is not None:
            dl_iso = dl_dt.astimezone(zone).date().isoformat()
            if dl_type == 'hard' and dl_dt <= now_dt:
                issues.append({'code': 'deadline_passed', 'period_index': idx, 'task_id': tid,
                               'message': f'Задача {tid}: жёсткий срок ({dl_iso}) уже прошёл.'})
            elif dl_dt > now_dt and dl_type != 'soft' and p.end_date > dl_iso:
                issues.append({'code': 'after_deadline', 'period_index': idx, 'task_id': tid,
                               'message': f'Задача {tid} заканчивается {p.end_date} после срока {dl_iso}.'})

        pin = task_pins.get(str(tid)) or {}
        if pin.get('not_before') and p.start_date < pin['not_before']:
            issues.append({'code': 'violates_not_before', 'period_index': idx, 'task_id': tid,
                           'message': f'Задача {tid} закреплена не раньше {pin["not_before"]}.'})

    for tid, item in assessments.items():
        pin = task_pins.get(str(tid)) or {}
        target_date = pin.get('target_date')
        rem = _effective_rem(item, overrides)
        if not target_date or target_date not in days_by_date or target_date in protected or rem <= 0:
            continue
        task = tasks_by_id[tid]
        av_dt = parse_dt(task.get('available_from'), zone)
        dl_dt = parse_dt(task.get('deadline'), timezone.utc)
        av_ok = av_dt is None or target_date >= av_dt.astimezone(zone).date().isoformat()
        dl_ok = dl_dt is None or task.get('deadline_type') == 'soft' or (dl_dt > now_dt and target_date <= dl_dt.astimezone(zone).date().isoformat())
        if av_ok and dl_ok and not any(p.start_date <= target_date <= p.end_date for p in work_periods.get(tid, ())):
            issues.append({'code': 'violates_task_pin', 'task_id': tid, 'date': target_date,
                           'message': f'Владелец закрепил задачу {tid} за {target_date}.'})

    return proposal, issues, days


# --- Материализация проверенного предложения в контракт UI ---

def materialize_plan(
    proposal: PlanProposal,
    tasks: list[dict],
    days: list[dict],
    week: dict,
    now: str,
    *,
    adjustments: dict | None = None,
    extra_blocked_dates: list[str] | tuple[str, ...] | None = None,
) -> dict:
    tasks_by_id = {task['id']: task for task in tasks}
    assessments = {item.task_id: item for item in proposal.tasks}
    zone = ZoneInfo(week.get('timezone', 'UTC'))
    now_dt = parse_dt(now, timezone.utc)
    today_iso = now_dt.astimezone(zone).date().isoformat()
    overrides = (adjustments or {}).get('estimate_overrides') or {}
    task_pins = (adjustments or {}).get('task_pins') or {}
    days_by_date = {d['date']: d for d in days}
    horizon_end = days[-1]['date']
    protected = _protected_dates(proposal, days_by_date, adjustments, extra_blocked_dates)

    plans: dict[int, dict] = {}
    for tid in proposal.task_ids:
        rem = _effective_rem(assessments[tid], overrides)
        confirmed = int(tasks_by_id[tid].get('completed_work_minutes') or 0)
        h_label = format_hours(rem)
        plans[tid] = {
            'task_id': tid,
            'remaining_minutes': rem,
            'work_min_minutes': rem,
            'work_max_minutes': rem,
            'min_minutes': rem,
            'max_minutes': rem,
            'planned_work_minutes': rem,
            'completed_work_minutes': confirmed,
            'hours_label': h_label,
            'time_estimate': h_label,
        }

    assigned_by_task: dict[int, int] = {tid: 0 for tid in proposal.task_ids}
    day_tasks_map: dict[str, dict[int, int]] = {d['date']: {} for d in days}
    for p in proposal.periods:
        if p.kind != 'work' or p.task_id is None:
            continue
        assigned_by_task[p.task_id] += p.estimated_minutes
        start = calendar_date.fromisoformat(p.start_date)
        end = calendar_date.fromisoformat(p.end_date)
        span = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        active_days = [d for d in span if d in day_tasks_map and d not in protected] or [d for d in span if d in day_tasks_map]
        if active_days:
            base, rem_mod = divmod(p.estimated_minutes, len(active_days))
            for idx_d, d_iso in enumerate(active_days):
                part = base + (1 if idx_d < rem_mod else 0)
                if part > 0:
                    day_tasks_map[d_iso][p.task_id] = day_tasks_map[d_iso].get(p.task_id, 0) + part

    daily_allocations = []
    for d in days:
        d_tasks = [
            {'task_id': tid, 'title': tasks_by_id[tid]['title'], 'minutes': mins, 'hours_label': format_hours(mins)}
            for tid, mins in day_tasks_map[d['date']].items()
        ]
        daily_allocations.append({
            'date': d['date'],
            'mode': d['mode'],
            'busy_minutes': _day_busy_minutes(d['events']),
            'allocated_minutes': sum(x['minutes'] for x in d_tasks),
            'tasks': d_tasks,
        })

    sorted_periods = sorted(
        proposal.periods,
        key=lambda p: (p.start_date, p.end_date, 0 if p.kind == 'work' else 1, p.task_id or 0),
    )
    periods_out = []
    for idx, p in enumerate(sorted_periods):
        if p.kind == 'work' and p.task_id is not None:
            tid = p.task_id
            task = tasks_by_id[tid]
            dl_dt = parse_dt(task.get('deadline'), timezone.utc)
            h_label = format_hours(p.estimated_minutes)
            completes = assigned_by_task[tid] >= plans[tid]['remaining_minutes']
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
                'minutes': p.estimated_minutes,
                'hours_label': h_label,
                'completed_work_minutes': plans[tid]['completed_work_minutes'],
                'completes_task': completes,
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'deadline_risk': assessments[tid].deadline_risk,
                'tasks': [{
                    'task_id': tid,
                    'title': task['title'],
                    'goal': p.goal.strip(),
                    'minutes': p.estimated_minutes,
                    'hours_label': h_label,
                    'completes_task': completes,
                    'deadline': dl_dt.isoformat() if dl_dt else None,
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
                'tasks': [],
            })

    conflicts = []
    remaining = []
    for tid in proposal.task_ids:
        rem = plans[tid]['remaining_minutes']
        assess = assessments[tid]
        task = tasks_by_id[tid]
        unplanned = max(0, rem - assigned_by_task[tid])
        remaining.append({'task_id': tid, 'remaining_work_minutes': unplanned})
        dl_dt = parse_dt(task.get('deadline'), timezone.utc)
        dl_iso = dl_dt.astimezone(zone).date().isoformat() if dl_dt else None
        is_overdue = bool(dl_dt and dl_dt <= now_dt and task.get('deadline_type') != 'soft' and rem > 0)
        pin_date = (task_pins.get(str(tid)) or {}).get('target_date')
        if pin_date and pin_date in protected and rem > 0:
            conflicts.append({
                'task_id': tid,
                'title': task['title'],
                'kind': 'constraint',
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'infeasible': True,
                'deficit_minutes': unplanned,
                'deficit_label': format_hours(unplanned) if unplanned else '',
                'reason': f'{task["title"]}: закреплено на {pin_date}, но этот день закрыт для работы.',
            })
        elif assess.deadline_risk or is_overdue or (dl_iso and dl_iso <= horizon_end and task.get('deadline_type') != 'soft' and unplanned > 0):
            infeasible = is_overdue or (bool(dl_iso and dl_iso <= horizon_end and task.get('deadline_type') != 'soft' and unplanned > 0))
            default_msg = f'{task["title"]}: срок уже прошёл.' if is_overdue else f'{task["title"]}: не хватает времени до срока.'
            conflicts.append({
                'task_id': tid,
                'title': task['title'],
                'kind': 'deadline',
                'deadline': dl_dt.isoformat() if dl_dt else None,
                'infeasible': infeasible,
                'deficit_minutes': unplanned,
                'deficit_label': format_hours(unplanned) if unplanned else '',
                'reason': assess.deadline_risk or default_msg,
            })

    total_rem = sum(plans[tid]['remaining_minutes'] for tid in proposal.task_ids)
    remaining_minutes = sum(r['remaining_work_minutes'] for r in remaining)
    schedule = {
        'version': 3,
        'valid': True,
        'task_ids': list(proposal.task_ids),
        'generated_date': today_iso,
        'start_at': now_dt.isoformat(),
        'timezone': week.get('timezone', 'UTC'),
        'feasible': not any(c['infeasible'] for c in conflicts),
        'fully_scheduled': remaining_minutes == 0,
        'remaining_minutes': remaining_minutes,
        'min_minutes': total_rem,
        'max_minutes': total_rem,
        'hours_label': format_hours(total_rem),
        'periods': periods_out,
        'blocked_dates': sorted(protected),
        'daily_allocations': daily_allocations,
        'deadline_conflicts': conflicts,
        'deadline_risks': [
            {'task_id': c['task_id'], 'title': c['title'], 'reason': c['reason'], 'infeasible': c['infeasible'], 'deficit_minutes': c['deficit_minutes']}
            for c in conflicts
        ],
        'remaining': remaining,
        'timeline': [
            {'task_id': tid, 'hours_label': plans[tid]['hours_label'], 'planned_minutes': assigned_by_task[tid]}
            for tid in proposal.task_ids
        ],
        'timeline_error': None,
    }
    reasons = [
        {
            'task_id': tid,
            'reason': assessments[tid].reason.strip(),
            'time_estimate': plans[tid]['time_estimate'],
            'deadline_risk': assessments[tid].deadline_risk,
            'plan': plans[tid],
        }
        for tid in proposal.task_ids
    ]
    return {
        'task_ids': list(proposal.task_ids),
        'explanation': proposal.explanation.strip(),
        'reasons': reasons,
        'timeline': [{'id': tid, 'title': tasks_by_id[tid]['title'], 'plan': plans[tid]} for tid in proposal.task_ids],
        'schedule': schedule,
        'plans': plans,
        'task_preferences': [{'task_id': p.task_id, 'text': p.text.strip()} for p in proposal.preferences if p.scope == 'task' and p.task_id is not None],
        'persistent_preferences': [{'key': p.key, 'text': p.text.strip()} for p in proposal.preferences if p.scope == 'persistent'],
    }


# --- Детерминированное обновление сохранённого плана без LLM (/refresh) ---

def refresh_schedule(
    previous: dict,
    tasks: list[dict],
    week: dict,
    now: str,
    *,
    adjustments: dict | None = None,
) -> dict:
    """Обновляет сохранённый план v3 под новый прогресс и сдвиги пинов; при конфликте требует перепланирования."""
    prev_schedule = (previous or {}).get('schedule') or {}
    if prev_schedule.get('version') != 3:
        raise ValueError('Требуется новый расчёт Ameba для формата v3')

    zone = ZoneInfo(week.get('timezone', 'UTC'))
    now_dt = parse_dt(now, timezone.utc)
    today = now_dt.astimezone(zone).date()
    today_iso = today.isoformat()
    tasks_by_id = {task['id']: task for task in tasks}
    active_ids = [task['id'] for task in tasks]
    if not active_ids:
        empty = {
            'version': 3, 'valid': True, 'task_ids': [], 'generated_date': today_iso, 'start_at': now_dt.isoformat(),
            'timezone': week.get('timezone', 'UTC'), 'feasible': True, 'fully_scheduled': True, 'remaining_minutes': 0,
            'min_minutes': 0, 'max_minutes': 0, 'hours_label': 'выполнено', 'periods': [], 'blocked_dates': [],
            'daily_allocations': [], 'deadline_conflicts': [], 'deadline_risks': [], 'remaining': [], 'timeline': [], 'timeline_error': None,
        }
        return previous | {'task_ids': [], 'timeline': [], 'reasons': [], 'schedule': empty}

    saved_plans = {item['id']: item['plan'] for item in previous.get('timeline', []) if item.get('id') in tasks_by_id and item.get('plan')}
    if any(tid not in saved_plans for tid in active_ids):
        raise ValueError('Есть неоценённые задачи — требуется полный пересчёт Ameba')

    overrides = (adjustments or {}).get('estimate_overrides') or {}
    task_pins = (adjustments or {}).get('task_pins') or {}
    reasons_by_id = {item['task_id']: item for item in previous.get('reasons', []) if item.get('task_id') in tasks_by_id}
    order = [tid for tid in previous.get('task_ids', []) if tid in tasks_by_id] + [tid for tid in active_ids if tid not in previous.get('task_ids', [])]

    new_rem: dict[int, int] = {}
    items: list[TaskPlanItem] = []
    for tid in order:
        old_p = saved_plans[tid]
        prev_rem = int(old_p.get('remaining_minutes', old_p.get('work_max_minutes', old_p.get('planned_work_minutes', 0))))
        delta_done = max(0, int(tasks_by_id[tid].get('completed_work_minutes') or 0) - int(old_p.get('completed_work_minutes') or 0))
        rem = max(0, int(overrides[str(tid)])) if str(tid) in overrides else max(0, prev_rem - delta_done)
        new_rem[tid] = rem
        old_r = reasons_by_id.get(tid) or {}
        items.append(TaskPlanItem(
            task_id=tid,
            remaining_minutes=rem,
            reason=str(old_r.get('reason') or 'По приоритету и срокам')[:200],
            deadline_risk=old_r.get('deadline_risk'),
        ))

    task_shifts: dict[int, int] = {}
    for tid in order:
        pin = task_pins.get(str(tid)) or {}
        anchor = pin.get('target_date') or pin.get('not_before')
        first_start = next((p['start_date'] for p in prev_schedule.get('periods', []) if p.get('kind') == 'work' and p.get('task_id') == tid), None)
        if anchor and first_start and (pin.get('target_date') or first_start < anchor):
            covers_target = any(
                p.get('kind') == 'work' and p.get('task_id') == tid and p['start_date'] <= anchor <= p['end_date']
                for p in prev_schedule.get('periods', [])
            )
            if not pin.get('target_date') or not covers_target:
                task_shifts[tid] = (calendar_date.fromisoformat(anchor) - calendar_date.fromisoformat(first_start)).days

    remaining_cap = dict(new_rem)
    old_assigned: dict[int, int] = {tid: 0 for tid in order}
    assigned: dict[int, int] = {tid: 0 for tid in order}
    rebuilt: list[PlanPeriod] = []
    for p in prev_schedule.get('periods', []):
        if p.get('kind') == 'rest':
            s_d = max(today_iso, p.get('start_date', ''))
            e_d = p.get('end_date', '')
            if s_d <= e_d:
                rebuilt.append(PlanPeriod(
                    kind='rest', task_id=None, start_date=s_d, end_date=e_d, estimated_minutes=0,
                    goal=str(p.get('goal') or p.get('title') or 'Отдых')[:180],
                    context_hint=p.get('context_hint'), reason=p.get('reason'),
                ))
            continue
        tid = p.get('task_id')
        if tid not in tasks_by_id:
            continue
        orig_mins = max(0, int(p.get('minutes', p.get('estimated_minutes', 0))))
        old_assigned[tid] += orig_mins
        if remaining_cap[tid] <= 0:
            continue
        shift = task_shifts.get(tid, 0)
        s_d = (calendar_date.fromisoformat(p['start_date']) + timedelta(days=shift)).isoformat()
        e_d = (calendar_date.fromisoformat(p['end_date']) + timedelta(days=shift)).isoformat()
        if e_d < today_iso:
            continue
        s_d = max(today_iso, s_d)
        mins = min(orig_mins, remaining_cap[tid])
        if mins <= 0:
            continue
        remaining_cap[tid] -= mins
        assigned[tid] += mins
        rebuilt.append(PlanPeriod(
            kind='work', task_id=tid, start_date=s_d, end_date=e_d, estimated_minutes=mins,
            goal=str(p.get('goal') or tasks_by_id[tid]['title'])[:180],
            context_hint=p.get('context_hint'), reason=p.get('reason'),
        ))

    for tid in order:
        if new_rem[tid] > 0 and assigned[tid] < new_rem[tid] and (old_assigned[tid] >= int(saved_plans[tid].get('remaining_minutes', saved_plans[tid].get('work_max_minutes', 0))) or assigned[tid] == 0):
            raise ValueError('Изменился остаток или периоды задачи — требуется перепланирование Ameba')

    prev_blocked = list(prev_schedule.get('blocked_dates') or [])
    candidate = PlanProposal(
        task_ids=order,
        tasks=items,
        periods=rebuilt,
        blocked_dates=prev_blocked,
        preferences=[],
        explanation=str(previous.get('explanation') or 'Актуализированный план на ближайшие дни.')[:400],
    )
    proposal, issues, days = validate_proposal(
        candidate, tasks, week, now, adjustments=adjustments, extra_blocked_dates=prev_blocked,
    )
    if issues:
        raise ValueError('Сохранённый план больше не соответствует ограничениям: ' + '; '.join(i.get('message', '') for i in issues[:2]))
    return previous | materialize_plan(proposal, tasks, days, week, now, adjustments=adjustments, extra_blocked_dates=prev_blocked)
