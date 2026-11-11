"""События календаря и предварительные планы в личной SQLite пользователя."""
import calendar
import json
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from common.database import get_engine
from common.models.integration import IntegrationSetting
from common.schedule import read_week, week_parity


class CalendarEvent(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=150)
    summary: str = Field(default='', max_length=500)
    task_id: int | None = Field(default=None, gt=0)
    plan_source: str = Field(default='', max_length=100)
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    start: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')
    kind: Literal['busy', 'task', 'sleep', 'meal', 'break', 'work'] = 'busy'
    repeat_unit: Literal['none', 'day', 'week', 'month'] = 'none'
    repeat_interval: int = Field(default=1, ge=1, le=366)
    repeat_count: int | None = Field(default=None, ge=1, le=10000)
    week_pattern: Literal['all', 'even', 'odd'] = 'all'
    parity_anchor: str | None = None

    @model_validator(mode='after')
    def validate_event(self):
        date.fromisoformat(self.date)
        if self.parity_anchor and date.fromisoformat(self.parity_anchor).weekday() != 0:
            raise ValueError('Первая учебная неделя должна начинаться с понедельника')
        if self.week_pattern != 'all' and self.repeat_unit != 'week':
            raise ValueError('Чётность применима только к недельным повторениям')
        if self.end <= self.start:
            raise ValueError('Конец события должен быть позже начала')
        if self.kind == 'task' and self.task_id is None:
            raise ValueError('Выберите задачу')
        if self.kind != 'task' and self.task_id is not None:
            raise ValueError('Задачу можно привязать только к блоку работы')
        return self


class CalendarConfig(BaseModel):
    model_config = {'extra': 'forbid'}
    events: list[CalendarEvent] = Field(default_factory=list)
    timezone: str = 'UTC'
    legacy_imported: bool = False
    auto_plan: bool = False
    revision: str = ''

    @model_validator(mode='after')
    def unique_ids(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError, KeyError):
            raise ValueError('Неизвестный часовой пояс') from None
        if len({event.id for event in self.events}) != len(self.events):
            raise ValueError('События должны иметь разные ID')
        return self


def legacy_events(week):
    # Старую БД не переписываем: недельные интервалы переводим в обычные события.
    anchor = date.fromisoformat(week['parity_anchor']) if week.get('parity_anchor') else date(1970, 1, 5)
    result = []
    for weekday, slots in enumerate(week['days']):
        for index, slot in enumerate(slots):
            first = anchor + timedelta(days=weekday)
            pattern = slot.get('week_pattern', 'all')
            if pattern != 'all' and week_parity(first, week.get('parity_anchor')) != pattern:
                first += timedelta(days=7)
            result.append(CalendarEvent(id=f'legacy:{weekday}:{index}', kind=slot['kind'],
                title=slot['title'] or slot['kind'], date=first.isoformat(), start=slot['start'], end=slot['end'],
                repeat_unit='week', repeat_interval=1, week_pattern=pattern,
                parity_anchor=week.get('parity_anchor')).model_dump())
    for index, slot in enumerate(week.get('events', [])):
        result.append(CalendarEvent(id=f'legacy:event:{index}', title=slot['title'] or 'Событие',
            kind=slot['kind'], date=slot['date'], start=slot['start'], end=slot['end']).model_dump())
    return result


def read_config():
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'calendar')
        raw = json.loads(row.config) if row else {}
    config = CalendarConfig.model_validate(raw).model_dump()
    if not config['legacy_imported']:
        week = read_week()
        if week:
            config['timezone'] = raw.get('timezone', week['timezone'])
            existing = {event['id'] for event in config['events']}
            config['events'] += [event for event in legacy_events(week) if event['id'] not in existing]
        config['legacy_imported'] = True
    return config


def save_config(config):
    # Вызывается из async endpoint без await: другой запрос не вклинится между чтением и записью.
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'calendar')
        previous = json.loads(row.config) if row else {}
        if previous.get('revision', '') != config.revision:
            raise HTTPException(409, 'Календарь изменился в другой вкладке. Обновите страницу.')
        config.revision = uuid4().hex
        row = row or IntegrationSetting(name='calendar')
        row.config = config.model_dump_json()
        session.add(row)
        session.commit()
    return config.model_dump()


def occurs(event, day):
    first = date.fromisoformat(event['date'])
    if day < first:
        return False
    pattern = event.get('week_pattern', 'all')
    if pattern != 'all' and week_parity(day, event.get('parity_anchor')) != pattern:
        return False
    unit = event.get('repeat_unit', 'none')
    interval = event.get('repeat_interval', 1)
    if unit == 'none':
        return day == first
    if unit == 'month':
        distance = (day.year - first.year) * 12 + day.month - first.month
        expected_day = min(first.day, calendar.monthrange(day.year, day.month)[1])
        if distance % interval or day.day != expected_day:
            return False
    else:
        distance = (day - first).days
        interval *= 7 if unit == 'week' else 1
        if distance % interval:
            return False
    index = distance // interval
    if pattern != 'all' and event.get('repeat_count') is not None:
        index = sum(week_parity(first + timedelta(days=step * interval), event.get('parity_anchor')) == pattern
                    for step in range(index))
    return event.get('repeat_count') is None or index < event['repeat_count']


def planning_week():
    config = read_config()
    if not config['events']:
        return None
    return {'timezone': config['timezone'], 'days': [[] for _ in range(7)],
            'events': [], 'calendar_events': config['events']}


def today():
    return datetime.now(ZoneInfo(read_config()['timezone'])).date()


def view_events(start, end):
    config = read_config()
    return [event | {'date': (start + timedelta(days=offset)).isoformat(), 'source': 'calendar'}
            for offset in range((end - start).days + 1)
            for event in config['events'] if occurs(event, start + timedelta(days=offset))]


def reset_ameba_plan(run_id):
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'ameba_plan') or IntegrationSetting(name='ameba_plan')
        row.config = json.dumps({'run_id': run_id})
        session.add(row)
        session.commit()


def read_ameba_plan():
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'ameba_plan')
        return json.loads(row.config) if row else {}


def save_ameba_plan(run_id, result, revision):
    if not result.get('schedule'):
        return
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'ameba_plan')
        if not row or json.loads(row.config).get('run_id') != run_id:
            return  # Ответ отменённого запуска не возвращает старый план.
        row.config = json.dumps({'run_id': run_id, 'revision': revision,
            'schedule': result['schedule'], 'rest_patterns': result.get('rest_patterns', []),
            'day_plans': result.get('day_plans', {}),
            'tasks': result.get('timeline', [])}, ensure_ascii=False)
        session.add(row)
        session.commit()


def ameba_events(start, end):
    saved = read_ameba_plan()
    schedule = saved.get('schedule', {})
    config = read_config()
    if not schedule.get('start_at') or saved.get('revision') != config['revision']:
        return []
    zone = ZoneInfo(config['timezone'])
    origin = datetime.fromisoformat(schedule['start_at'])
    titles = {task['id']: task['title'] for task in saved.get('tasks', [])}
    names = {'sleep': 'Сон', 'meal': 'Еда', 'break': 'Отдых', 'personal': 'Личное время'}
    result = []
    def add(key, left, right, kind, title, task_id=None):
        day = left.astimezone(zone).date()
        while left < right:
            midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
            stop = min(right, midnight)
            if start <= day <= end:
                result.append({'id': f'ameba-{saved["run_id"]}-{key}-{day}', 'date': day.isoformat(),
                    'start': left.astimezone(zone).strftime('%H:%M'),
                    'end': '24:00' if stop == midnight else stop.astimezone(zone).strftime('%H:%M'),
                    'kind': kind, 'title': title, 'task_id': task_id, 'source': 'ameba',
                    'summary': 'Временный план Ameba. Сбросится при следующем запуске.'})
            left, day = stop, day + timedelta(days=1)
    day_plans = saved.get('day_plans', {})
    patterns = list(saved.get('rest_patterns', []))
    patterns += [slot | {'date': day} for day, slots in day_plans.items()
                 for slot in slots if slot['kind'] in ('sleep', 'meal', 'break')]
    for index, pattern in enumerate(patterns):
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            if day < origin.astimezone(zone).date():
                continue
            if pattern.get('date'):
                if day.isoformat() != pattern['date']:
                    continue
            elif day.isoformat() in day_plans or day.weekday() not in pattern['weekdays']:
                continue
            left = datetime.combine(day, datetime.min.time(), zone)
            hour, minute = map(int, pattern['start'].split(':'))
            right_hour, right_minute = map(int, pattern['end'].split(':'))
            begin = left + timedelta(hours=hour, minutes=minute)
            finish = left + timedelta(hours=right_hour, minutes=right_minute)
            if finish > origin:
                add(f'rest-{index}', max(begin, origin), finish, pattern['kind'], pattern['title'])
    for block in schedule.get('blocks', []):
        if block.get('permanent'):
            continue
        left = (origin + timedelta(minutes=block['start'])).astimezone(zone)
        right = (origin + timedelta(minutes=block['start'] + block['minutes'])).astimezone(zone)
        if any(event['kind'] == block['kind'] and event['date'] == left.date().isoformat()
               and event['start'] <= left.strftime('%H:%M')
               and event['end'] >= ('24:00' if right.date() != left.date() else right.strftime('%H:%M'))
               and (left.date() == right.date() or (right.hour == 0 and right.minute == 0)) for event in result):
            continue
        add(block['id'], left, right, block['kind'], titles.get(block.get('task_id')) or block.get('title') or names.get(block['kind'], 'Занято'), block.get('task_id'))
    return result
