"""Детерминированное ядро планирования Ameba: ёмкость дней, отдых, периоды и адаптация."""
from __future__ import annotations

from datetime import date as calendar_date, datetime, timedelta, timezone
import json
from typing import Annotated, Literal
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


# --- Схемы инструментов OpenAI (strict=True: все поля без значений по умолчанию) ---

class Step(BaseModel):
    model_config = {'extra': 'forbid'}
    title: str = Field(min_length=1, max_length=300)
    min_minutes: int = Field(ge=0)
    max_minutes: int = Field(ge=0)
    wait_minutes: int = Field(ge=0)


class Plan(BaseModel):
    model_config = {'extra': 'forbid'}
    task_id: int
    available_from: datetime | None = Field(
        description='Самое раннее начало выполнения из контекста (открытие контрольной/задания), ISO 8601 с поясом или null.',
    )
    depends_on: list[int] = Field(
        description='Подтверждённые ID задач из списка, которые необходимо завершить раньше этой; [] если зависимостей нет.',
    )
    deadline_policy: Literal['soft', 'finish_before', 'expires'] = Field(
        description='soft — можно позже; finish_before — нужно успеть до срока; expires — после срока сдача невозможна.',
    )
    importance: Literal['low', 'medium', 'high', 'critical'] = Field(
        description='Реальная значимость задачи с учётом последствий и контекста.',
    )
    effort: Literal['low', 'medium', 'high'] = Field(
        description='Интеллектуальная сложность и требуемая концентрация.',
    )
    splittable: bool = Field(
        description='true — задачу разумно вести постепенно по дням/периодам; false — лучше выполнить одним цельным подходом.',
    )
    steps: list[Step] = Field(min_length=1)
    assumptions: list[str]
    expected_result: str = Field(min_length=1, max_length=400)
    reward: float | None = Field(ge=0)
    reward_unit: str


class Plans(BaseModel):
    model_config = {'extra': 'forbid'}
    plans: list[Plan] = Field(min_length=1)


TOOL = {
    'type': 'function', 'name': 'plan_tasks', 'strict': True,
    'description': (
        'Оценить полный объём полезной работы по этапам (включая уже выполненное), сложность, значимость, '
        'делимость, зависимости и политику срока. Python сам один раз вычтет completed_work_minutes и применит персональную адаптацию.'
    ),
    'parameters': Plans.model_json_schema(),
}


class DayOverride(BaseModel):
    model_config = {'extra': 'forbid'}
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    mode: Literal['normal', 'light', 'free', 'focused']
    max_minutes: int | None = Field(ge=0, le=480)
    note: str = Field(max_length=200)


class RestPlan(BaseModel):
    model_config = {'extra': 'forbid'}
    sleep_hours: float | None = Field(ge=4, le=12, description='Длительность сна из памяти/настроек или null (базово 8 ч).')
    preferred_daily_minutes: int | None = Field(ge=30, le=480, description='Комфортный дневной лимит самост. работы в свободный день или null.')
    prefer_free_evenings: bool = Field(description='true — оставлять вечера после плотных занятий/работы свободными от сложной работы.')
    max_heavy_days_in_row: int = Field(ge=1, le=4, description='Сколько напряжённых дней подряд допустимо до облегчённого дня (обычно 2).')
    preferred_weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(max_length=7)
    light_weekdays: list[Annotated[int, Field(ge=0, le=6)]] = Field(max_length=7)
    day_overrides: list[DayOverride] = Field(max_length=21)
    assumption: str = Field(min_length=1, max_length=500)


REST_TOOL = {
    'type': 'function', 'name': 'plan_rest', 'strict': True,
    'description': (
        'Задать настройки нагрузки, восстановления и точечные режимы дат (normal/light/free/focused) '
        'без назначения произвольных часов. weekdays: 0=Пн…6=Вс. После вызова пересчитай reorder_tasks.'
    ),
    'parameters': RestPlan.model_json_schema(),
}


class PeriodNote(BaseModel):
    model_config = {'extra': 'forbid'}
    period_id: str = Field(min_length=1, max_length=80)
    title: str | None = Field(max_length=180)
    day_hint: str | None = Field(max_length=260)
    rest_note: str | None = Field(max_length=260)


class Order(BaseModel):
    model_config = {'extra': 'forbid'}
    task_ids: list[int]
    keep_previous_plan: bool = Field(
        description='true — бережно сохранять прежнее распределение по дням и периодам; false — пересобрать при существенных изменениях.',
    )
    period_notes: list[PeriodNote] = Field(max_length=20)


ORDER_TOOL = {
    'type': 'function', 'name': 'reorder_tasks', 'strict': True,
    'description': (
        'Задать смысловой приоритетный порядок всех задач. Python рассчитает реалистичную ёмкость дней, '
        'защитит отдых и жёсткие сроки, проверит выполнимость и соберёт гибкие периоды работы.'
    ),
    'parameters': Order.model_json_schema(),
}

READ_TOOL = {
    'type': 'function', 'name': 'read_current_plan', 'strict': True,
    'description': 'Прочитать текущую ёмкость дней календаря, персональную адаптацию, прошлый план периодов и ручные корректировки.',
    'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
}


# --- Форматирование и базовые утилиты ---

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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


# --- Персонализация на основе подтверждённых наблюдений ---

def derive_adaptation(profile: dict | None) -> dict:
    """Компактная статистическая адаптация с консервативным сжатием к априори."""
    profile = profile or {}
    prefs = profile.get('preferences') or {}
    observations = profile.get('observations') or []
    prior_k = 4.0

    global_w = 0.0
    global_wr = 0.0
    cat_stats: dict[str, list[float]] = {}
    eff_stats: dict[str, list[float]] = {}
    busy_count = 0
    busy_fail = 0.0
    weekday_stats = [[0.0, 0.0] for _ in range(7)]
    overrun_signals = 0.0
    too_hard_count = 0

    for item in observations[-100:]:
        outcome = item.get('outcome')
        planned = float(item.get('planned_minutes') or 0)
        actual = item.get('actual_minutes')
        category = (item.get('category') or '').strip().casefold()
        effort = item.get('effort') or 'medium'
        busy_day = bool(item.get('busy_day'))
        weekday = item.get('weekday')

        if outcome == 'too_hard':
            too_hard_count += 1
            overrun_signals += 1.0
        elif outcome == 'missed':
            overrun_signals += 0.8

        ratio = None
        weight = 0.0
        if planned > 0 and actual is not None and float(actual) > 0:
            actual_f = float(actual)
            if outcome == 'done':
                ratio = clamp(actual_f / planned, 0.5, 2.5)
                weight = 1.0
            elif outcome in ('partial', 'too_hard') and actual_f > planned:
                ratio = clamp(actual_f / planned, 1.0, 2.5)
                weight = 0.85
            if actual_f > planned * 1.15:
                overrun_signals += 0.7
        elif outcome == 'too_hard':
            ratio = 1.25
            weight = 0.5

        if ratio is not None and weight > 0:
            global_w += weight
            global_wr += weight * ratio
            if category:
                entry = cat_stats.setdefault(category, [0.0, 0.0])
                entry[0] += weight
                entry[1] += weight * ratio
            entry = eff_stats.setdefault(effort, [0.0, 0.0])
            entry[0] += weight
            entry[1] += weight * ratio

        if busy_day:
            busy_count += 1
            busy_fail += {'missed': 1.0, 'too_hard': 0.85, 'partial': 0.35}.get(outcome, 0.0)

        if isinstance(weekday, int) and 0 <= weekday <= 6:
            delta = {'done': 0.15, 'partial': 0.03, 'too_hard': -0.10, 'missed': -0.22}.get(outcome, 0.0)
            weekday_stats[weekday][0] += 1.0
            weekday_stats[weekday][1] += 1.0 + delta

    global_factor = clamp((prior_k * 1.0 + global_wr) / (prior_k + global_w), 0.75, 1.80)
    category_factors = {
        cat: round(clamp((prior_k * global_factor + wr) / (prior_k + w), 0.75, 1.85), 3)
        for cat, (w, wr) in cat_stats.items() if w > 0
    }
    effort_factors = {
        eff: round(clamp((prior_k * global_factor + wr) / (prior_k + w), 0.75, 1.85), 3)
        for eff, (w, wr) in eff_stats.items() if w > 0
    }

    busy_fail_rate = busy_fail / (prior_k + busy_count) if busy_count else 0.0
    busy_day_factor = round(max(0.65, 1.0 - 0.55 * busy_fail_rate), 3)

    weekday_factors = [
        round(clamp((prior_k * 1.0 + total) / (prior_k + cnt), 0.85, 1.15), 3) if cnt else 1.0
        for cnt, total in weekday_stats
    ]

    total_obs = len(observations)
    instability = overrun_signals / (prior_k + total_obs) if total_obs else 0.0
    reserve_ratio = round(clamp(0.22 + 0.18 * instability - (0.04 if total_obs >= 6 and overrun_signals == 0 else 0.0), 0.18, 0.35), 3)
    if prefs.get('reserve_ratio') is not None:
        reserve_ratio = round(clamp(float(prefs['reserve_ratio']), 0.10, 0.40), 3)

    comfortable_daily = 300
    if total_obs > 0 and too_hard_count > 0:
        comfortable_daily = round_step((prior_k * 300 + too_hard_count * 210 + max(0, total_obs - too_hard_count) * 300) / (prior_k + total_obs), 15)
        comfortable_daily = max(210, min(330, comfortable_daily))
    if prefs.get('preferred_daily_minutes'):
        comfortable_daily = int(clamp(int(prefs['preferred_daily_minutes']), 60, 480))

    summary_parts = []
    if total_obs >= 2:
        if abs(global_factor - 1.0) >= 0.06:
            summary_parts.append(f'длительность ×{global_factor:.2f}')
        shifted_cats = [f'{cat} ×{val:.2f}' for cat, val in category_factors.items() if abs(val - 1.0) >= 0.08]
        if shifted_cats:
            summary_parts.append('категории: ' + ', '.join(shifted_cats[:3]))
        if busy_day_factor < 0.94:
            summary_parts.append(f'ёмкость загруженных дней ×{busy_day_factor:.2f}')
        summary_parts.append(f'резерв {round(reserve_ratio * 100)}%')
    summary = f'Персонализация ({total_obs} наблюд.): ' + '; '.join(summary_parts) if summary_parts else None

    return {
        'observations_count': total_obs,
        'global_factor': round(global_factor, 3),
        'category_factors': category_factors,
        'effort_factors': effort_factors,
        'busy_day_factor': busy_day_factor,
        'weekday_factors': weekday_factors,
        'reserve_ratio': reserve_ratio,
        'comfortable_daily_minutes': comfortable_daily,
        'prefer_free_evenings': bool(prefs.get('prefer_free_evenings', False)),
        'max_heavy_days_in_row': int(prefs.get('max_heavy_days_in_row') or 2),
        'summary': summary,
    }


def task_duration_factor(adaptation: dict, category: str | None, effort: str) -> float:
    cat_key = (category or '').strip().casefold()
    eff_factor = adaptation['effort_factors'].get(effort, adaptation['global_factor'])
    if cat_key and cat_key in adaptation['category_factors']:
        return 0.65 * adaptation['category_factors'][cat_key] + 0.35 * eff_factor
    return eff_factor


# --- Оценка задач и проверка зависимостей ---

def _validate_dependencies(plans_map: dict[int, dict], expected: list[int]) -> None:
    for task_id, plan in plans_map.items():
        for dep in plan.get('depends_on', []):
            if dep == task_id or dep not in expected:
                raise ValueError(f'Задача {task_id}: некорректная зависимость {dep}')
    state: dict[int, int] = {}

    def dfs(node: int) -> None:
        status = state.get(node, 0)
        if status == 1:
            raise ValueError('Обнаружен цикл зависимостей между задачами')
        if status == 2:
            return
        state[node] = 1
        for dep in plans_map.get(node, {}).get('depends_on', []):
            if dep in plans_map:
                dfs(dep)
        state[node] = 2

    for tid in plans_map:
        dfs(tid)


def calculate(arguments, expected, completed=None, profile=None, tasks=None, adjustments=None):
    body = Plans.model_validate_json(arguments)
    tasks_by_id = {task['id']: task for task in (tasks or ())}
    adaptation = derive_adaptation(profile)
    overrides = (adjustments or {}).get('estimate_overrides') or {}

    result = {}
    for plan in body.plans:
        if plan.task_id not in expected or plan.task_id in result:
            raise ValueError(f'ID {plan.task_id} неизвестен или повторяется в вызове. Допустимые ID: {expected}')
        if any(step.min_minutes > step.max_minutes for step in plan.steps):
            raise ValueError(f'Задача {plan.task_id}: min_minutes каждого этапа должен быть не больше max_minutes')
        if plan.reward is not None and not plan.reward_unit.strip():
            raise ValueError(f'Задача {plan.task_id}: укажи reward_unit либо reward=null')
        if any(dep == plan.task_id or dep not in expected for dep in plan.depends_on):
            raise ValueError(f'Задача {plan.task_id}: некорректный ID в depends_on')

        raw_low = sum(step.min_minutes for step in plan.steps)
        raw_high = sum(step.max_minutes for step in plan.steps)
        category = tasks_by_id.get(plan.task_id, {}).get('category')
        factor = task_duration_factor(adaptation, category, plan.effort)
        scaled_low = round(raw_low * factor)
        scaled_high = max(scaled_low, round(raw_high * factor))

        confirmed = int((completed or {}).get(plan.task_id, 0) or 0)
        work_low = max(0, scaled_low - confirmed)
        work_high = max(0, scaled_high - confirmed)

        manual_minutes = overrides.get(str(plan.task_id))
        if manual_minutes is not None:
            manual_val = max(0, int(manual_minutes))
            work_low = min(work_low, manual_val) if manual_val else 0
            work_high = manual_val
            if work_high > 0 and work_low == work_high:
                work_low = max(5, round(work_high * 0.85))

        planned_work = 0 if work_high == 0 else max(10, round_step(0.35 * work_low + 0.65 * work_high, 5))
        wait = sum(step.wait_minutes for step in plan.steps)
        hours_label = format_hours(work_low, work_high)
        wait_suffix = f'; ожидание: {format_hours(wait, wait)}' if wait else ''

        result[plan.task_id] = plan.model_dump(mode='json') | {
            'calibration_factor': round(factor, 3),
            'raw_work_min_minutes': raw_low,
            'raw_work_max_minutes': raw_high,
            'work_min_minutes': work_low,
            'work_max_minutes': work_high,
            'min_minutes': work_low,
            'max_minutes': work_high,
            'planned_work_minutes': planned_work,
            'completed_work_minutes': confirmed,
            'wait_minutes': wait,
            'high_uncertainty': bool(work_high > 0 and work_high / max(1, work_low) > 1.6),
            'hours_label': hours_label,
            'time_estimate': f'{hours_label}{wait_suffix}',
        }
    return result


def recalculate_plans(saved_plans: dict[int, dict], completed=None, profile=None, tasks=None, adjustments=None) -> dict[int, dict]:
    """Пересчитывает сохранённые оценки без LLM после изменения прогресса или адаптации."""
    payload = []
    for tid, plan in saved_plans.items():
        steps = [
            {'title': s['title'], 'min_minutes': int(s['min_minutes']), 'max_minutes': int(s['max_minutes']), 'wait_minutes': int(s.get('wait_minutes', 0))}
            for s in plan.get('steps') or [{'title': 'Работа', 'min_minutes': plan.get('raw_work_min_minutes', plan.get('work_min_minutes', 0)),
                                            'max_minutes': plan.get('raw_work_max_minutes', plan.get('work_max_minutes', 0)), 'wait_minutes': plan.get('wait_minutes', 0)}]
        ]
        payload.append({
            'task_id': int(tid),
            'available_from': plan.get('available_from'),
            'depends_on': list(plan.get('depends_on', [])),
            'deadline_policy': plan.get('deadline_policy', 'soft'),
            'importance': plan.get('importance', 'medium'),
            'effort': plan.get('effort', 'medium'),
            'splittable': bool(plan.get('splittable', True)),
            'steps': steps,
            'assumptions': list(plan.get('assumptions', [])),
            'expected_result': plan.get('expected_result') or 'Выполнение задачи',
            'reward': plan.get('reward'),
            'reward_unit': plan.get('reward_unit', ''),
        })
    if not payload:
        return {}
    return calculate(json.dumps({'plans': payload}), list(saved_plans), completed=completed, profile=profile, tasks=tasks, adjustments=adjustments)


# --- Настройка режима отдыха/нагрузки ---

def restore_week(week, previous=None, now=None, timezone_name='UTC'):
    if week:
        configured = dict(week)
        configured.setdefault('timezone', timezone_name)
        configured.setdefault('days', [[] for _ in range(7)])
        configured.setdefault('events', [])
        configured.setdefault('calendar_events', [])
    else:
        configured = {'timezone': timezone_name, 'days': [[] for _ in range(7)], 'events': [], 'calendar_events': []}
    if previous and isinstance(previous.get('rest_config'), dict):
        configured['rest_config'] = dict(previous['rest_config'])
    return configured


def with_rest(week, now, arguments=None, timezone_name='UTC'):
    configured = restore_week(week, timezone_name=timezone_name)
    if arguments is None:
        return configured
    body = RestPlan.model_validate_json(arguments)
    seen_dates = set()
    overrides = {}
    for item in body.day_overrides:
        if item.date in seen_dates:
            raise ValueError(f'Повторяющаяся дата в day_overrides: {item.date}')
        seen_dates.add(item.date)
        overrides[item.date] = item.model_dump()
    configured['rest_config'] = {
        'sleep_hours': body.sleep_hours,
        'preferred_daily_minutes': body.preferred_daily_minutes,
        'prefer_free_evenings': body.prefer_free_evenings,
        'max_heavy_days_in_row': body.max_heavy_days_in_row,
        'preferred_weekdays': list(dict.fromkeys(body.preferred_weekdays)),
        'light_weekdays': list(dict.fromkeys(body.light_weekdays)),
        'day_overrides': overrides,
        'assumption': body.assumption.strip(),
    }
    return configured


# --- Анализ календаря и расчёт реалистичной рабочей ёмкости ---

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


def analyze_days(
    week: dict,
    now: str,
    horizon_days: int = 14,
    profile: dict | None = None,
    adjustments: dict | None = None,
) -> list[dict]:
    """Оценка физически свободного времени и реалистичной ёмкости каждого дня."""
    adaptation = derive_adaptation(profile)
    rest_cfg = week.get('rest_config') or {}
    manual_day_overrides = (adjustments or {}).get('day_overrides') or {}
    model_day_overrides = rest_cfg.get('day_overrides') or {}

    zone = ZoneInfo(week.get('timezone', 'UTC'))
    origin_utc = parse_dt(now, timezone.utc)
    now_local = origin_utc.astimezone(zone)
    today = now_local.date()
    now_min = now_local.hour * 60 + now_local.minute

    sleep_hours = rest_cfg.get('sleep_hours') or 8.0
    base_sleep = round(float(sleep_hours) * 60)
    base_daily = int(rest_cfg.get('preferred_daily_minutes') or adaptation['comfortable_daily_minutes'])
    prefer_free_evenings = bool(rest_cfg.get('prefer_free_evenings') or adaptation['prefer_free_evenings'])
    preferred_wd = set(rest_cfg.get('preferred_weekdays') or ())
    light_wd = set(rest_cfg.get('light_weekdays') or ())
    reserve_ratio = adaptation['reserve_ratio']

    days_profile = []
    prev_busy = 0

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
        ambiguous_all_day_minutes = 0
        has_commute_event = False
        has_evening_busy = False

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
                    ambiguous_all_day_minutes += 240
                commitments.append({'id': ev.get('id', ''), 'title': title, 'start': ev['start'], 'end': ev['end'], 'kind': kind, 'all_day': True})
                continue

            busy_intervals.append((start_m, end_m))
            if end_m >= 17 * 60:
                has_evening_busy = True
            commitments.append({'id': ev.get('id', ''), 'title': title, 'start': ev['start'], 'end': ev['end'], 'kind': kind, 'all_day': False})

        merged_busy = merge_intervals(busy_intervals)
        busy_minutes = sum(b - a for a, b in merged_busy) + ambiguous_all_day_minutes
        merged_sleep = merge_intervals(sleep_intervals)
        sleep_minutes = max(360, sum(b - a for a, b in merged_sleep)) if merged_sleep else base_sleep
        merged_meal = merge_intervals(meal_intervals)
        routine_minutes = max(120, sum(b - a for a, b in merged_meal))

        clusters = 0
        last_end = None
        for a, b in merged_busy:
            if last_end is None or a - last_end > 20:
                clusters += 1
            last_end = b
        commute_minutes = 0 if (has_commute_event or busy_minutes < 90) else min(60, 15 * clusters)

        span_minutes = (merged_busy[-1][1] - merged_busy[0][0]) if merged_busy else 0

        # Физически свободное время с учётом текущего часа (для today).
        start_bound = now_min if offset == 0 else 0
        if offset == 0:
            # Считаем будущую часть дня после now_min, вычитая оставшийся сон/быт и будущие обязательства.
            future_busy = sum(max(0, b - max(a, start_bound)) for a, b in merged_busy if b > start_bound)
            if merged_sleep:
                future_sleep = sum(max(0, b - max(a, start_bound)) for a, b in merged_sleep if b > start_bound)
            else:
                # Стандартное допущение сна: 00:00-07:00 и 23:00-24:00 (или пропорция по sleep_hours).
                morning_end = min(480, round((sleep_minutes / 2) + 60))
                night_start = max(1320, 1440 - (sleep_minutes - morning_end))
                future_sleep = max(0, morning_end - start_bound) + max(0, 1440 - max(start_bound, night_start))
            future_routine = round(routine_minutes * max(0.25, (1440 - start_bound) / 1440))
            future_commute = min(commute_minutes, 15 * sum(1 for _, b in merged_busy if b > start_bound))
            physical_free = max(0, (1440 - start_bound) - future_sleep - future_routine - future_busy - future_commute)
        else:
            physical_free = max(0, 1440 - sleep_minutes - routine_minutes - busy_minutes - commute_minutes)

        merged_work = merge_intervals(work_intervals)
        if merged_work:
            work_free = 0
            for wa, wb in merged_work:
                seg_start = max(wa, start_bound)
                if wb <= seg_start:
                    continue
                seg = wb - seg_start
                for ba, bb in merged_busy:
                    overlap = min(wb, bb) - max(seg_start, ba)
                    if overlap > 0:
                        seg -= overlap
                work_free += max(0, seg)
            physical_free = min(physical_free, work_free)

        # Режим дня: ручная правка пользователя имеет приоритет над моделью.
        manual_mode = manual_day_overrides.get(day_iso)
        model_ovr = model_day_overrides.get(day_iso) or {}
        mode = manual_mode or model_ovr.get('mode') or ('light' if day.weekday() in light_wd else 'normal')
        override_max = model_ovr.get('max_minutes') if not manual_mode else None

        if all_day_unavailable or mode == 'free' or override_max == 0 or (offset == 0 and (now_min >= 22 * 60 or physical_free < 35)):
            realistic = 0
        else:
            drain = round(
                0.45 * min(busy_minutes, 180)
                + 0.75 * max(0, busy_minutes - 180)
                + (25 if len(merged_busy) >= 3 or span_minutes >= 420 else 0)
                + 0.35 * commute_minutes
            )
            cap = max(0, base_daily - drain)
            if prev_busy >= 360:
                cap = round(cap * 0.85)
            if busy_minutes >= 240:
                cap = round(cap * adaptation['busy_day_factor'])
                cap = min(cap, round(physical_free * 0.40))
                if prefer_free_evenings and (has_evening_busy or busy_minutes >= 300):
                    cap = min(cap, 30)
            else:
                cap = min(cap, round(physical_free * 0.72))

            if day.weekday() in light_wd and mode == 'light':
                cap = min(cap, min(120, round(base_daily * 0.45)))
            elif day.weekday() in preferred_wd and mode == 'normal':
                cap = min(round(base_daily * 1.15), round(cap * 1.12), round(physical_free * 0.75))

            cap = round(cap * adaptation['weekday_factors'][day.weekday()])

            if mode == 'light':
                cap = min(cap, min(120, round(base_daily * 0.40)))
            elif mode == 'focused':
                cap = min(round(base_daily * 1.15), round(physical_free * 0.72), max(cap, round(cap * 1.15)))

            if override_max is not None:
                cap = min(cap, int(override_max))
            realistic = round_step(cap, 5)
            if 0 < realistic < 25:
                realistic = 0

        plannable = round_step(realistic * (1.0 - reserve_ratio), 5)
        if realistic >= 30 and 0 < plannable < 30:
            plannable = 30
        stretch = round_step(realistic * 0.92, 5)

        days_profile.append({
            'offset': offset,
            'date': day_iso,
            'weekday': day.weekday(),
            'mode': mode,
            'busy_minutes': busy_minutes,
            'sleep_minutes': sleep_minutes,
            'routine_minutes': routine_minutes,
            'commute_minutes': commute_minutes,
            'physical_free_minutes': physical_free,
            'realistic_capacity_minutes': realistic,
            'plannable_minutes': plannable,
            'max_stretch_minutes': stretch,
            'busy_day': busy_minutes >= 240,
            'merged_busy': merged_busy,
            'markers': markers,
            'commitments': sorted(commitments, key=lambda c: c['start']),
            'note': model_ovr.get('note', ''),
        })
        prev_busy = busy_minutes

    return days_profile


def _day_capacity_before(day_info: dict, deadline_local: datetime, use_stretch: bool = False) -> int:
    cap = day_info['max_stretch_minutes'] if use_stretch else day_info['plannable_minutes']
    dl_date = deadline_local.date().isoformat()
    if day_info['date'] < dl_date:
        return cap
    if day_info['date'] > dl_date:
        return 0
    dl_min = deadline_local.hour * 60 + deadline_local.minute
    if dl_min <= 8 * 60:
        return 0
    # Примерная доля рабочего дня до дедлайна (с 08:00 до 22:00).
    fraction = clamp((dl_min - 8 * 60) / (14 * 60), 0.1, 1.0)
    return round_step(cap * fraction, 5)


def _available_fraction_on_day(day_info: dict, avail_local: datetime | None) -> float:
    if avail_local is None:
        return 1.0
    av_date = avail_local.date().isoformat()
    if day_info['date'] < av_date:
        return 0.0
    if day_info['date'] > av_date:
        return 1.0
    av_min = avail_local.hour * 60 + avail_local.minute
    if av_min >= 21 * 60:
        return 0.0
    return clamp((22 * 60 - max(8 * 60, av_min)) / (14 * 60), 0.15, 1.0)


# --- Совместная проверка выполнимости и распределение по дням ---

def _task_meta(tasks: list[dict], plans: dict[int, dict], zone: ZoneInfo) -> dict[int, dict]:
    tasks_by_id = {task['id']: task for task in tasks}
    meta = {}
    for task_id, plan in plans.items():
        task = tasks_by_id.get(task_id, {})
        raw_dl = task.get('deadline')
        dl_dt = parse_dt(raw_dl, timezone.utc)
        dl_local = dl_dt.astimezone(zone) if dl_dt else None
        av_dt = parse_dt(plan.get('available_from'), zone)
        av_local = av_dt.astimezone(zone) if av_dt else None
        meta[task_id] = {
            'task_id': task_id,
            'title': task.get('title', f'Задача #{task_id}'),
            'priority': task.get('priority', 'medium'),
            'category': task.get('category'),
            'deadline': dl_dt,
            'deadline_local': dl_local,
            'available_local': av_local,
            'policy': plan.get('deadline_policy', 'soft'),
            'importance': plan.get('importance', 'medium'),
            'effort': plan.get('effort', 'medium'),
            'splittable': bool(plan.get('splittable', True)),
            'depends_on': list(plan.get('depends_on', [])),
            'required': int(plan.get('planned_work_minutes', plan.get('work_max_minutes', 0))),
            'min_work': int(plan.get('work_min_minutes', 0)),
            'max_work': int(plan.get('work_max_minutes', 0)),
        }
    # Распространяем жёсткий срок на зависимости и самое раннее открытие от зависимостей.
    changed = True
    while changed:
        changed = False
        for tid, info in meta.items():
            for dep_id in info['depends_on']:
                dep = meta.get(dep_id)
                if not dep:
                    continue
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
    return meta


def check_feasibility(
    order: list[int],
    meta: dict[int, dict],
    days: list[dict],
    now_local: datetime,
) -> tuple[list[dict], set[int]]:
    """Совместная EDF-проверка конкурирующих жёстких дедлайнов с защитой отдыха."""
    hard_tasks = [
        meta[tid] for tid in order
        if meta[tid]['required'] > 0 and meta[tid]['policy'] != 'soft' and meta[tid]['deadline_local'] is not None
    ]
    hard_tasks.sort(key=lambda item: (item['deadline_local'], order.index(item['task_id'])))

    conflicts: list[dict] = []
    infeasible_ids: set[int] = set()
    now_utc = now_local.astimezone(timezone.utc)
    horizon_end = days[-1]['date']

    # Сначала проверяем задачи с уже истёкшим дедлайном или открытием после дедлайна.
    valid_hard = []
    for info in hard_tasks:
        tid = info['task_id']
        dl_local = info['deadline_local']
        av_local = info['available_local']
        if info['deadline'] <= now_utc or (av_local is not None and av_local >= dl_local):
            infeasible_ids.add(tid)
            conflicts.append({
                'task_id': tid,
                'title': info['title'],
                'policy': info['policy'],
                'deadline': info['deadline'].isoformat(),
                'infeasible': True,
                'deficit_minutes': info['required'],
                'deficit_label': format_hours(info['required'], info['max_work']),
                'competing_task_ids': [],
                'reason': (
                    f"{info['title']}: срок выполнения уже наступил или задача открывается позже дедлайна."
                ),
                'options': [
                    'Согласовать перенос дедлайна или сдачи',
                    'Оценить, есть ли смысл сдавать работу частично',
                    'Пересмотреть приоритеты других задач',
                ],
            })
        elif dl_local.date().isoformat() <= horizon_end:
            valid_hard.append(info)

    # Для каждой отсечки дедлайна симулируем совместное размещение на max_stretch и plannable.
    def deficit_for_prefix(prefix: list[dict], use_stretch: bool) -> int:
        rem_cap = [d['max_stretch_minutes'] if use_stretch else d['plannable_minutes'] for d in days]
        missing = 0
        for item in prefix:
            need = item['required']
            dl_local = item['deadline_local']
            av_local = item['available_local']
            for idx, d in enumerate(days):
                if d['date'] > dl_local.date().isoformat():
                    break
                cap_dl = _day_capacity_before(d, dl_local, use_stretch=use_stretch)
                cap_av = round_step((d['max_stretch_minutes'] if use_stretch else d['plannable_minutes']) * _available_fraction_on_day(d, av_local), 5)
                usable = min(rem_cap[idx], cap_dl, cap_av)
                if usable <= 0:
                    continue
                take = min(need, usable)
                rem_cap[idx] -= take
                need -= take
                if need <= 0:
                    break
            missing += need
        return missing

    # Накапливаем достижимые жёсткие задачи по порядку срока; заведомо непоместившийся гигант не ломает остальных.
    accepted: list[dict] = []
    for info in valid_hard:
        tid = info['task_id']
        solo_deficit = deficit_for_prefix([info], use_stretch=True)
        if solo_deficit > 0:
            infeasible_ids.add(tid)
            conflicts.append({
                'task_id': tid,
                'title': info['title'],
                'policy': info['policy'],
                'deadline': info['deadline'].isoformat(),
                'infeasible': True,
                'deficit_minutes': solo_deficit,
                'deficit_label': format_hours(solo_deficit),
                'competing_task_ids': [],
                'reason': (
                    f"{info['title']}: объём ({format_hours(info['min_work'], info['max_work'])}) превышает "
                    f"реалистичную рабочую ёмкость до дедлайна без ущерба для сна и отдыха (не хватает ≈ {format_hours(solo_deficit)})."
                ),
                'options': [
                    'Сократить объём работы или согласовать минимально достаточный результат',
                    'Перенести мягкий/внешний срок при возможности',
                    'Освободить дополнительные дни от необязательных дел',
                ],
            })
            continue

        group_deficit = deficit_for_prefix(accepted + [info], use_stretch=True)
        if group_deficit > 0:
            infeasible_ids.add(tid)
            competing = [item['task_id'] for item in accepted]
            conflicts.append({
                'task_id': tid,
                'title': info['title'],
                'policy': info['policy'],
                'deadline': info['deadline'].isoformat(),
                'infeasible': True,
                'deficit_minutes': group_deficit,
                'deficit_label': format_hours(group_deficit),
                'competing_task_ids': competing,
                'reason': (
                    f"{info['title']}: при совместном учёте с другими обязательными дедлайнами не хватает "
                    f"≈ {format_hours(group_deficit)} реалистичной ёмкости (сон и базовый отдых защищены)."
                ),
                'options': [
                    'Изменить приоритеты и перенести менее критичную задачу',
                    'Снизить глубину проработки одной из конкурирующих работ',
                    'Перенести необязательные задачи на более поздние периоды',
                ],
            })
        else:
            accepted.append(info)
            tight_deficit = deficit_for_prefix(accepted, use_stretch=False)
            if tight_deficit > 0:
                conflicts.append({
                    'task_id': tid,
                    'title': info['title'],
                    'policy': info['policy'],
                    'deadline': info['deadline'].isoformat(),
                    'infeasible': False,
                    'deficit_minutes': tight_deficit,
                    'deficit_label': format_hours(tight_deficit),
                    'competing_task_ids': [item['task_id'] for item in accepted if item['task_id'] != tid],
                    'reason': (
                        f"{info['title']}: выполнимо к сроку только при использовании части резерва "
                        f"(запас времени на грани, ≈ {format_hours(tight_deficit)})."
                    ),
                    'options': [
                        'Не добавлять новые сложные дела до закрытия срока',
                        'Начать задачу при первой возможности без откладывания',
                    ],
                })
    return conflicts, infeasible_ids


def _previous_task_days(previous_schedule: dict | None) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    if not previous_schedule:
        return result
    for day in previous_schedule.get('daily_allocations', []):
        date_str = day.get('date')
        for item in day.get('tasks', []):
            tid = item.get('task_id')
            if tid and item.get('minutes', 0) > 0:
                result.setdefault(tid, set()).add(date_str)
    return result


def _detect_replan_mode(
    previous_schedule: dict | None,
    keep_previous_plan: bool,
    days: list[dict],
    meta: dict[int, dict],
    conflicts: list[dict],
) -> str:
    if not previous_schedule or previous_schedule.get('version') != 2:
        return 'initial'
    if not keep_previous_plan or any(c.get('infeasible') for c in conflicts):
        return 'major_replan'
    old_days = {d['date']: d for d in previous_schedule.get('daily_allocations', [])}
    today_iso = days[0]['date']
    # Крупный пропуск прошедших дней или обвал ёмкости календаря >= 30% -> существенное перепланирование.
    for d in days[:5]:
        old = old_days.get(d['date'])
        if old and old.get('plannable_minutes', 0) > 0:
            if d['plannable_minutes'] < old['plannable_minutes'] * 0.65:
                return 'major_replan'
    missed_yesterday = sum(
        d.get('allocated_minutes', 0) for date_iso, d in old_days.items() if date_iso < today_iso
    )
    if missed_yesterday >= 180:
        return 'major_replan'
    # Новый жёсткий срок, которого не было раньше.
    old_tasks = {t['task_id'] for d in old_days.values() for t in d.get('tasks', [])}
    if any(info['policy'] != 'soft' and tid not in old_tasks for tid, info in meta.items() if info['required'] > 0):
        return 'major_replan'
    return 'minor_adjustment'


def _allocate_days(
    order: list[int],
    meta: dict[int, dict],
    days: list[dict],
    infeasible_ids: set[int],
    previous_schedule: dict | None,
    replan_mode: str,
    rest_cfg: dict,
    adaptation: dict,
    adjustments: dict | None,
) -> tuple[list[dict], dict[int, int]]:
    """Распределение нагрузки по дням горизонта без назначения конкретных часов."""
    max_heavy_streak = int(rest_cfg.get('max_heavy_days_in_row') or adaptation['max_heavy_days_in_row'])
    prev_days = _previous_task_days(previous_schedule) if replan_mode != 'major_replan' else {}
    task_pins = (adjustments or {}).get('task_pins') or {}

    alloc = {d['offset']: [] for d in days}
    used = {d['offset']: 0 for d in days}
    remaining = {tid: meta[tid]['required'] for tid in order}

    # Проверка, выполнена ли зависимость к индексу дня idx (все её дни <= idx, либо вся её работа уже распределена до/на idx).
    task_last_day: dict[int, int] = {}

    def day_cap(d: dict, allow_stretch: bool) -> int:
        return d['max_stretch_minutes'] if allow_stretch else d['plannable_minutes']

    def heavy_streak_before(idx: int) -> int:
        streak = 0
        pos = idx - 1
        while pos >= 0:
            d = days[pos]
            strain = d['busy_minutes'] * 0.65 + used[pos]
            if used[pos] >= 165 or strain >= 340:
                streak += 1
                pos -= 1
            else:
                break
        return streak

    def valid_day_for_task(idx: int, tid: int, allow_stretch: bool) -> int:
        d = days[idx]
        info = meta[tid]
        cap = day_cap(d, allow_stretch)
        if info['deadline_local'] is not None and info['policy'] != 'soft':
            cap = min(cap, _day_capacity_before(d, info['deadline_local'], use_stretch=allow_stretch))
        cap = min(cap, round_step(day_cap(d, allow_stretch) * _available_fraction_on_day(d, info['available_local']), 5))
        if d['mode'] == 'light' and info['effort'] == 'high' and not (info['policy'] != 'soft' and info['deadline_local'] and d['date'] == info['deadline_local'].date().isoformat()):
            return 0
        pin = task_pins.get(str(tid)) or {}
        if pin.get('not_before') and d['date'] < pin['not_before']:
            return 0
        for dep_id in info['depends_on']:
            if remaining.get(dep_id, 0) > 0:
                return 0
            if dep_id in task_last_day and idx < task_last_day[dep_id]:
                return 0
        return max(0, cap - used[idx])

    def pick_day_and_chunk(tid: int, chunk_limit: int | None, allow_stretch: bool, urgent: bool) -> bool:
        rem = remaining[tid]
        if rem <= 0:
            return False
        info = meta[tid]
        pin = task_pins.get(str(tid)) or {}
        pin_target = pin.get('target_date')
        preferred_dates = prev_days.get(tid, set())
        candidates = []

        for idx, d in enumerate(days):
            avail = valid_day_for_task(idx, tid, allow_stretch)
            if avail < min(rem, 30):
                continue
            max_tasks_today = 3 if rem <= 45 else 2
            if tid not in {x['task_id'] for x in alloc[idx]} and len(alloc[idx]) >= max_tasks_today:
                continue

            penalty = 0.0
            if pin_target:
                target_day = calendar_date.fromisoformat(pin_target)
                cur_day = calendar_date.fromisoformat(d['date'])
                penalty += abs((cur_day - target_day).days) * 18.0
            if d['date'] in preferred_dates:
                penalty -= 25.0 if replan_mode == 'minor_adjustment' else 10.0
            if info['effort'] == 'high' and d['busy_day']:
                penalty += 22.0
            if not urgent and heavy_streak_before(idx) >= max_heavy_streak:
                penalty += 28.0
            # Мягкий дедлайн: штраф за размещение позже срока.
            if info['deadline_local'] is not None and info['policy'] == 'soft' and d['date'] > info['deadline_local'].date().isoformat():
                penalty += 30.0
            # Равномерность: штраф пропорционален текущей заполненности дня.
            load_ratio = used[idx] / max(1, d['realistic_capacity_minutes'])
            penalty += load_ratio * 14.0
            # Чтобы небольшие задачи не откладывались далеко без причины.
            penalty += idx * (1.1 if urgent else 1.5)
            candidates.append((penalty, idx, avail))

        if not candidates:
            return False
        _, best_idx, avail = min(candidates)
        take = min(rem, avail)
        if chunk_limit is not None and take > chunk_limit and rem > chunk_limit:
            take = chunk_limit
        if take < rem:
            take = (int(take) // 5) * 5
        if take <= 0:
            return False

        # Если остаток задачи после этой порции совсем крошечный (< 30 мин), лучше добрать его если вмещается.
        if 0 < rem - take < 30 and rem <= avail:
            take = rem

        existing = next((x for x in alloc[best_idx] if x['task_id'] == tid), None)
        if existing:
            existing['minutes'] += take
        else:
            alloc[best_idx].append({'task_id': tid, 'minutes': take})
        used[best_idx] += take
        remaining[tid] -= take
        task_last_day[tid] = max(task_last_day.get(tid, best_idx), best_idx)
        return True

    # 1. Достижимые жёсткие дедлайны (сначала более ранний срок; зависимости перед зависимой задачей).
    hard_achievable = [
        tid for tid in order
        if tid not in infeasible_ids and meta[tid]['policy'] != 'soft' and meta[tid]['deadline_local'] is not None
    ]
    hard_achievable.sort(key=lambda tid: (meta[tid]['deadline_local'], order.index(tid)))
    ordered_hard: list[int] = []
    seen_h: set[int] = set()

    def add_with_deps(tid: int) -> None:
        if tid in seen_h:
            return
        for dep in meta[tid]['depends_on']:
            add_with_deps(dep)
        seen_h.add(tid)
        ordered_hard.append(tid)

    for tid in hard_achievable:
        add_with_deps(tid)

    for tid in ordered_hard:
        limit = 180 if (meta[tid]['splittable'] and remaining[tid] >= 240) else None
        while pick_day_and_chunk(tid, limit, allow_stretch=False, urgent=True):
            pass
        # Если в резервном бюджете чуть не хватило до дедлайна, используем max_stretch (без нарушения отдыха).
        while remaining[tid] > 0 and pick_day_and_chunk(tid, limit, allow_stretch=True, urgent=True):
            pass

    # 2. Остальные задачи (мягкие и без срока) — пошагово раундами, чтобы можно было вести параллельно.
    flexible = [tid for tid in order if tid not in seen_h and tid not in infeasible_ids]
    ordered_flex: list[int] = []
    seen_f = set(seen_h)

    def add_flex(tid: int) -> None:
        if tid in seen_f:
            return
        for dep in meta[tid]['depends_on']:
            add_flex(dep)
        seen_f.add(tid)
        ordered_flex.append(tid)

    for tid in flexible:
        add_flex(tid)

    while True:
        progressed = False
        for tid in ordered_flex:
            if remaining[tid] <= 0:
                continue
            info = meta[tid]
            if info['splittable'] and remaining[tid] >= 240:
                chunk = 150
            elif info['splittable'] and remaining[tid] >= 160:
                chunk = 120
            else:
                chunk = None
            if pick_day_and_chunk(tid, chunk, allow_stretch=False, urgent=False):
                progressed = True
        if not progressed:
            break

    # Заведомо невыполнимые задачи получают остаток свободной ёмкости только если она осталась после всех достижимых.
    for tid in order:
        if tid in infeasible_ids and remaining[tid] > 0:
            while pick_day_and_chunk(tid, 150, allow_stretch=False, urgent=False):
                pass

    # Итоговая дневная сводка.
    daily_result = []
    for idx, d in enumerate(days):
        day_tasks = []
        for item in alloc[idx]:
            tid = item['task_id']
            info = meta[tid]
            mins = item['minutes']
            ratio = mins / max(1, info['required'])
            min_est = round_step(max(10, info['min_work'] * ratio), 5) if info['min_work'] else mins
            max_est = max(min_est, round_step(info['max_work'] * ratio, 5) if info['max_work'] else mins)
            day_tasks.append({
                'task_id': tid,
                'title': info['title'],
                'minutes': mins,
                'min_minutes': min_est,
                'max_minutes': max_est,
                'hours_label': format_hours(min_est, max_est),
            })

        total_alloc = used[idx]
        cap = d['realistic_capacity_minutes']
        effective_mode = d['mode']
        if total_alloc == 0:
            if d['mode'] == 'free' or (cap == 0 and d['busy_minutes'] == 0):
                effective_mode = 'free'
            elif idx > 0 and (days[idx - 1]['busy_minutes'] * 0.65 + used[idx - 1] >= 320):
                effective_mode = 'recovery'
            else:
                effective_mode = 'free' if d['busy_minutes'] == 0 else 'light'
        elif cap > 0 and total_alloc <= min(95, round(cap * 0.4)) and d['mode'] == 'normal':
            effective_mode = 'light'

        if total_alloc == 0 and d['busy_minutes'] < 120:
            load_level = 'free'
        elif (cap > 0 and total_alloc > cap) or (d['busy_minutes'] >= 360 and total_alloc >= 90):
            load_level = 'overload'
        elif total_alloc >= 190 or (cap > 0 and total_alloc >= cap * 0.80) or (d['busy_minutes'] >= 240 and total_alloc >= 60):
            load_level = 'high'
        elif total_alloc >= 85 or d['busy_minutes'] >= 150:
            load_level = 'medium'
        else:
            load_level = 'light'

        daily_result.append({
            'date': d['date'],
            'weekday': d['weekday'],
            'mode': effective_mode,
            'load_level': load_level,
            'busy_minutes': d['busy_minutes'],
            'busy_day': d['busy_day'],
            'physical_free_minutes': d['physical_free_minutes'],
            'realistic_capacity_minutes': cap,
            'plannable_minutes': d['plannable_minutes'],
            'allocated_minutes': total_alloc,
            'tasks': day_tasks,
            'markers': d['markers'],
            'commitments': d['commitments'],
            'note': d['note'],
        })
    return daily_result, remaining


# --- Группировка дней в гибкие периоды ---

def _step_goal_for_slice(plan: dict, prev_ratio: float, end_ratio: float) -> str:
    steps = plan.get('steps') or []
    if not steps:
        return plan.get('expected_result', '')
    if prev_ratio <= 0.05 and end_ratio >= 0.95:
        return plan.get('expected_result') or 'Полное выполнение задачи'
    total = sum(max(1, s.get('max_minutes', 0)) for s in steps)
    acc = 0.0
    chosen = []
    for step in steps:
        w = max(1, step.get('max_minutes', 0)) / total
        left, right = acc, acc + w
        acc = right
        if right > prev_ratio + 0.02 and left < end_ratio - 0.02:
            chosen.append(step['title'])
    if not chosen:
        chosen = [steps[-1]['title']] if end_ratio >= 0.9 else [steps[0]['title']]
    label = ', '.join(chosen[:2])
    if len(chosen) > 2:
        label += ' и др.'
    return label


def _build_periods(
    daily: list[dict],
    plans: dict[int, dict],
    meta: dict[int, dict],
    conflicts: list[dict],
    rest_cfg: dict,
    period_notes: list[PeriodNote] | None = None,
) -> list[dict]:
    # Обрезаем длинный пустой хвост в конце горизонта, но сохраняем разумный обзор (минимум 7 дней).
    last_active = max((i for i, d in enumerate(daily) if d['allocated_minutes'] > 0), default=-1)
    visible_count = min(len(daily), max(7, last_active + 2))
    visible_days = daily[:visible_count]

    def main_task_id(day: dict) -> int | None:
        if not day['tasks']:
            return None
        return max(day['tasks'], key=lambda t: t['minutes'])['task_id']

    def can_merge(group: list[dict], nxt: dict) -> bool:
        prev = group[-1]
        if (prev['allocated_minutes'] == 0) != (nxt['allocated_minutes'] == 0):
            return False
        if prev['allocated_minutes'] == 0 and nxt['allocated_minutes'] == 0:
            # Объединяем подряд идущие свободные дни в конце недели (Сб–Вс), но сохраняем отдельные recovery-дни если они разные.
            return prev['mode'] == nxt['mode'] and len(group) < 2
        m_prev = main_task_id(prev)
        m_next = main_task_id(nxt)
        # Если в один день заканчивается жёсткий срок ведущей задачи, не тянем период за него без той же задачи.
        for t in prev['tasks']:
            info = meta[t['task_id']]
            if info['policy'] != 'soft' and info['deadline_local'] and info['deadline_local'].date().isoformat() == prev['date']:
                if t['task_id'] not in {x['task_id'] for x in nxt['tasks']}:
                    return False
        same_main = m_prev is not None and m_prev == m_next
        shared = bool({x['task_id'] for x in prev['tasks']} & {x['task_id'] for x in nxt['tasks']})
        big_project = same_main and meta[m_prev]['required'] >= 480
        max_len = 4 if big_project else 3
        if len(group) >= max_len:
            return False
        if same_main:
            return True
        if shared and (prev['busy_minutes'] >= 240 or nxt['busy_minutes'] >= 240 or prev['allocated_minutes'] <= 90 or nxt['allocated_minutes'] <= 90):
            return True
        if len(group) == 1 and prev['allocated_minutes'] <= 120 and nxt['allocated_minutes'] <= 120:
            return True
        return False

    groups: list[list[dict]] = []
    for day in visible_days:
        if not groups or not can_merge(groups[-1], day):
            groups.append([day])
        else:
            groups[-1].append(day)

    cumulative_done: dict[int, int] = {tid: 0 for tid in plans}
    conflicts_by_task: dict[int, list[dict]] = {}
    for c in conflicts:
        conflicts_by_task.setdefault(c['task_id'], []).append(c)

    notes_by_pid = {item.period_id: item for item in (period_notes or ())}
    periods = []

    for group in groups:
        start_date = group[0]['date']
        end_date = group[-1]['date']
        pid = f'period-{start_date}-{end_date}'
        total_alloc = sum(d['allocated_minutes'] for d in group)

        # Собираем задачи периода, сохраняя порядок значимости внутри периода.
        agg: dict[int, dict] = {}
        for d in group:
            for item in d['tasks']:
                tid = item['task_id']
                entry = agg.setdefault(tid, {'task_id': tid, 'title': item['title'], 'minutes': 0, 'min_minutes': 0, 'max_minutes': 0})
                entry['minutes'] += item['minutes']
                entry['min_minutes'] += item['min_minutes']
                entry['max_minutes'] += item['max_minutes']

        period_tasks = []
        period_risks = []
        for tid, entry in sorted(agg.items(), key=lambda kv: -kv[1]['minutes']):
            info = meta[tid]
            req = max(1, info['required'])
            prev_ratio = cumulative_done.get(tid, 0) / req
            cumulative_done[tid] = cumulative_done.get(tid, 0) + entry['minutes']
            end_ratio = min(1.0, cumulative_done[tid] / req)
            goal = _step_goal_for_slice(plans[tid], prev_ratio, end_ratio)
            completes = cumulative_done[tid] >= info['required']
            period_tasks.append({
                'task_id': tid,
                'title': entry['title'],
                'goal': goal,
                'minutes': entry['minutes'],
                'min_minutes': entry['min_minutes'],
                'max_minutes': entry['max_minutes'],
                'hours_label': format_hours(entry['min_minutes'], entry['max_minutes']),
                'share_percent': min(100, max(5, round((end_ratio - prev_ratio) * 100))),
                'completes_task': completes,
                'deadline': info['deadline'].isoformat() if info['deadline'] else None,
                'deadline_policy': info['policy'],
            })
            for c in conflicts_by_task.get(tid, []):
                period_risks.append(c['reason'])

        if total_alloc == 0:
            modes = {d['mode'] for d in group}
            has_busy = any(d['busy_minutes'] >= 180 for d in group)
            if 'recovery' in modes:
                kind = 'recovery'
                title = 'Восстановление после плотной нагрузки'
            elif 'light' in modes or has_busy:
                kind = 'light'
                title = 'Облегчённый день' if len(group) == 1 else 'Облегчённые дни'
            else:
                kind = 'free'
                title = 'Свободный день' if len(group) == 1 else 'Свободные дни'
        else:
            kind = 'work'
            lead = period_tasks[0]
            if len(period_tasks) == 1:
                title = lead['title'] if lead['completes_task'] else f"{lead['title']} ({lead['goal']})"
            elif len(period_tasks) == 2:
                title = f"{period_tasks[0]['title']} и {period_tasks[1]['title']}"
            else:
                title = f"{period_tasks[0]['title']} и ещё {len(period_tasks) - 1} задачи"

        # Итоговая нагрузка периода.
        levels_rank = {'free': 0, 'light': 1, 'medium': 2, 'high': 3, 'overload': 4}
        max_lvl = max((d['load_level'] for d in group), key=lambda v: levels_rank.get(v, 0))
        load_level = 'free' if (total_alloc == 0 and kind == 'free') else 'light' if (total_alloc == 0) else max_lvl

        # Подсказки по распределению усилий между днями периода.
        day_hints = []
        if len(group) >= 2 and total_alloc > 0:
            busiest = max(group, key=lambda d: d['busy_minutes'])
            lightest = min(group, key=lambda d: d['busy_minutes'])
            heaviest_work = max(group, key=lambda d: d['allocated_minutes'])
            if busiest['date'] != lightest['date'] and busiest['busy_minutes'] - lightest['busy_minutes'] >= 150 and heaviest_work['allocated_minutes'] > busiest['allocated_minutes']:
                b_date = format_date_ru(calendar_date.fromisoformat(busiest['date']))
                w_date = format_date_ru(calendar_date.fromisoformat(heaviest_work['date']))
                day_hints.append(
                    f"{b_date} плотное расписание ({format_hours(busiest['busy_minutes'])}), основную часть лучше оставить на {w_date}."
                )
            elif len(group) == 2 and abs(group[0]['allocated_minutes'] - group[1]['allocated_minutes']) >= 45:
                first_d = format_date_ru(calendar_date.fromisoformat(group[0]['date']))
                second_d = format_date_ru(calendar_date.fromisoformat(group[1]['date']))
                if group[0]['allocated_minutes'] > group[1]['allocated_minutes']:
                    day_hints.append(f'{first_d} — основной объём работы, {second_d} — завершение и проверка.')
                else:
                    day_hints.append(f'{first_d} — спокойный старт, {second_d} — основной рабочий день.')
            else:
                day_hints.append('Нагрузку можно распределять равномерно в удобное время внутри этих дней.')
        elif len(group) == 1 and total_alloc > 0 and group[0]['busy_minutes'] >= 240:
            day_hints.append(f"День насыщен обязательными делами ({format_hours(group[0]['busy_minutes'])}), планируйте только посильный минимум.")

        for d in group:
            if d['note']:
                day_hints.append(f"{format_date_ru(calendar_date.fromisoformat(d['date']))}: {d['note']}")

        # Рекомендации по отдыху.
        if kind == 'free':
            rest_note = 'Отдых, личные дела и необязательный резерв для незавершённых мелочей.'
        elif kind == 'recovery':
            rest_note = 'Сниженная нагрузка для восстановления после серии напряжённых дней; избегайте новых сложных задач.'
        elif kind == 'light':
            rest_note = 'Облегчённый режим: только лёгкое повторение и сохранение свободного времени.'
        elif load_level in ('high', 'overload'):
            rest_note = 'Высокая нагрузка: не добавляйте лишних сложных задач и защитите ночной сон и вечерний отдых.'
        elif any(d['busy_day'] for d in group) and rest_cfg.get('prefer_free_evenings'):
            rest_note = 'Вечера после плотных занятий желательно оставить свободными для восстановления.'
        else:
            rest_note = 'Работайте комфортными подходами в выбранное вами время с регулярными паузами.'

        total_min = sum(t['min_minutes'] for t in period_tasks)
        total_max = sum(t['max_minutes'] for t in period_tasks)

        ovr = notes_by_pid.get(pid)
        if ovr:
            if ovr.title:
                title = ovr.title.strip()
            if ovr.day_hint:
                day_hints = [ovr.day_hint.strip()] + [h for h in day_hints if h != ovr.day_hint.strip()]
            if ovr.rest_note:
                rest_note = ovr.rest_note.strip()

        periods.append({
            'id': pid,
            'start_date': start_date,
            'end_date': end_date,
            'kind': kind,
            'title': title,
            'min_minutes': total_min,
            'max_minutes': total_max,
            'hours_label': format_hours(total_min, total_max) if total_alloc > 0 else 'без обязательной нагрузки',
            'load_level': load_level,
            'tasks': period_tasks,
            'day_hints': list(dict.fromkeys(day_hints)),
            'rest_note': rest_note,
            'risks': list(dict.fromkeys(period_risks)),
            'days_summary': [
                {
                    'date': d['date'],
                    'weekday': d['weekday'],
                    'mode': d['mode'],
                    'busy_minutes': d['busy_minutes'],
                    'physical_free_minutes': d['physical_free_minutes'],
                    'realistic_capacity_minutes': d['realistic_capacity_minutes'],
                    'allocated_minutes': d['allocated_minutes'],
                    'markers': d['markers'],
                    'commitments': d['commitments'],
                }
                for d in group
            ],
        })
    return periods


def _summarize_changes(previous_schedule: dict | None, periods: list[dict], replan_mode: str) -> list[str]:
    if not previous_schedule or previous_schedule.get('version') != 2:
        return ['Сформирован первый адаптивный план периодов без жёстких почасовых слотов.']
    changes = []
    old_map: dict[int, tuple[str, str]] = {}
    for p in previous_schedule.get('periods', []):
        for t in p.get('tasks', []):
            old_map.setdefault(t['task_id'], (p['start_date'], p['end_date']))
    new_map: dict[int, tuple[str, str, str]] = {}
    for p in periods:
        for t in p.get('tasks', []):
            new_map.setdefault(t['task_id'], (p['start_date'], p['end_date'], t['title']))

    moved = []
    for tid, (s, e, title) in new_map.items():
        if tid in old_map and old_map[tid] != (s, e):
            moved.append(f'«{title}» перенесена на {format_date_ru(calendar_date.fromisoformat(s))}' + (f'–{format_date_ru(calendar_date.fromisoformat(e))}' if s != e else ''))
        elif tid not in old_map:
            moved.append(f'добавлена задача «{title}»')
    if moved:
        changes.extend(moved[:4])
    elif replan_mode == 'minor_adjustment':
        changes.append('Учтён текущий прогресс; основные периоды и цели сохранены без перестройки.')
    else:
        changes.append('Обновлены объёмы и распределение с учётом актуального календаря.')
    return changes


# --- Главная точка входа планировщика ---

def reorder(
    arguments,
    plans,
    expected,
    week,
    now,
    previous=None,
    tasks=(),
    profile=None,
    adjustments=None,
):
    request = Order.model_validate_json(arguments) if isinstance(arguments, str) else Order.model_validate(arguments)
    order = request.task_ids
    missing_plans = [task_id for task_id in expected if task_id not in plans]
    if missing_plans:
        raise ValueError(f'Сначала вызови plan_tasks для задач: {missing_plans}')
    if len(order) != len(expected) or set(order) != set(expected):
        raise ValueError(f'В task_ids нужны все ID ровно по одному разу: {expected}')

    _validate_dependencies(plans, expected)

    week = restore_week(week, timezone_name=(week or {}).get('timezone', 'UTC'))
    zone = ZoneInfo(week['timezone'])
    now_dt = parse_dt(now, timezone.utc)
    now_local = now_dt.astimezone(zone)
    today = now_local.date()

    adaptation = derive_adaptation(profile)
    rest_cfg = week.get('rest_config') or {}
    meta = _task_meta(list(tasks), plans, zone)

    # Автоматически расширяем горизонт до 21 дня при наличии дедлайнов в 3-й неделе или крупных проектов.
    total_work = sum(info['required'] for info in meta.values())
    horizon_days = 14
    for info in meta.values():
        if info['deadline_local'] is not None:
            delta = (info['deadline_local'].date() - today).days + 1
            if 14 < delta <= 21:
                horizon_days = max(horizon_days, delta)
    if total_work >= 1200:
        horizon_days = max(horizon_days, 21)

    days = analyze_days(week, now_dt.isoformat(), horizon_days=horizon_days, profile=profile, adjustments=adjustments)
    conflicts, infeasible_ids = check_feasibility(order, meta, days, now_local)

    prev_schedule = previous if isinstance(previous, dict) else None
    if prev_schedule and 'schedule' in prev_schedule:
        prev_schedule = prev_schedule['schedule']

    replan_mode = _detect_replan_mode(prev_schedule, request.keep_previous_plan, days, meta, conflicts)
    daily, remaining_map = _allocate_days(
        order, meta, days, infeasible_ids, prev_schedule, replan_mode, rest_cfg, adaptation, adjustments,
    )
    periods = _build_periods(daily, plans, meta, conflicts, rest_cfg, request.period_notes)
    plan_changes = _summarize_changes(prev_schedule, periods, replan_mode)

    low = sum(plans[tid]['work_min_minutes'] for tid in order)
    high = sum(plans[tid]['work_max_minutes'] for tid in order)
    wait = sum(plans[tid]['wait_minutes'] for tid in order)
    effort_minutes = {'low': 0, 'medium': 0, 'high': 0}
    rewards = {}
    blocked = {c['task_id'] for c in conflicts if c.get('infeasible')}
    for tid in order:
        p = plans[tid]
        effort_minutes[p['effort']] = effort_minutes.get(p['effort'], 0) + p['work_max_minutes']
        if tid not in blocked and p['reward'] is not None:
            rewards[p['reward_unit']] = rewards.get(p['reward_unit'], 0) + p['reward']

    timeline = []
    assigned_by_task = {tid: sum(item['minutes'] for d in daily for item in d['tasks'] if item['task_id'] == tid) for tid in order}
    for tid in order:
        p = plans[tid]
        timeline.append({
            'task_id': tid,
            'expected_result': p['expected_result'],
            'hours_label': p.get('hours_label') or format_hours(p['work_min_minutes'], p['work_max_minutes']),
            'planned_minutes': assigned_by_task[tid],
        })

    remaining = [
        {'task_id': tid, 'remaining_work_minutes': max(0, remaining_map.get(tid, 0))}
        for tid in order
    ]
    feasible = not any(c.get('infeasible') for c in conflicts)

    return {
        'version': 2,
        'task_ids': order,
        'generated_date': today.isoformat(),
        'start_at': now_dt.isoformat(),
        'timezone': week['timezone'],
        'replan_mode': replan_mode,
        'feasible': feasible,
        'reserve_ratio': adaptation['reserve_ratio'],
        'min_minutes': low,
        'max_minutes': high,
        'hours_label': format_hours(low, high),
        'wait_minutes': wait,
        'effort_minutes': effort_minutes,
        'potential_rewards': rewards,
        'periods': periods,
        'daily_allocations': daily,
        'deadline_conflicts': conflicts,
        'remaining': remaining,
        'plan_changes': plan_changes,
        'timeline': timeline,
        'timeline_error': None,
        'rest_assumption': rest_cfg.get('assumption') or (
            f"Сон ~{rest_cfg.get('sleep_hours') or 8} ч, базовый быт и перерывы защищены; "
            f"резерв ёмкости {round(adaptation['reserve_ratio'] * 100)}%."
        ),
        'adaptation_summary': adaptation.get('summary'),
        'note': 'Гибкие периоды без жёсткого назначения часов. Выбирайте удобное время внутри периода.',
    }


def read_summary(week: dict, now: str, previous: dict | None, plans: dict, expected: list[int], profile=None, adjustments=None) -> dict:
    days = analyze_days(week, now, horizon_days=14, profile=profile, adjustments=adjustments)
    adaptation = derive_adaptation(profile)
    return {
        'timezone': week.get('timezone', 'UTC'),
        'adaptation': adaptation,
        'rest_config': week.get('rest_config', {}),
        'days_capacity': [
            {
                'date': d['date'],
                'weekday': WEEKDAYS_SHORT[d['weekday']],
                'mode': d['mode'],
                'busy_minutes': d['busy_minutes'],
                'physical_free_minutes': d['physical_free_minutes'],
                'realistic_capacity_minutes': d['realistic_capacity_minutes'],
                'plannable_minutes': d['plannable_minutes'],
                'markers': d['markers'],
                'commitments': d['commitments'],
            }
            for d in days[:10]
        ],
        'previous_periods': (previous or {}).get('schedule', {}).get('periods', []),
        'estimated_task_ids': list(plans),
        'missing_plan_ids': [tid for tid in expected if tid not in plans],
    }
