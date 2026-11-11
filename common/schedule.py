"""Личное недельное расписание хранится в существующей пользовательской БД."""
import json
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from common.database import get_engine
from common.models.integration import IntegrationSetting


class Slot(BaseModel):
    model_config = {'extra': 'forbid'}
    kind: Literal['work', 'sleep', 'meal', 'break', 'busy']
    title: str = Field(max_length=150)
    start: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(pattern=r'^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$')
    week_pattern: Literal['all', 'even', 'odd'] = 'all'


class Event(Slot):
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    kind: Literal['sleep', 'meal', 'break', 'busy'] = 'busy'


def minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


class Week(BaseModel):
    model_config = {'extra': 'forbid'}
    timezone: str = Field(max_length=100)
    days: list[list[Slot]] = Field(min_length=7, max_length=7)
    events: list[Event] = Field(default_factory=list)
    parity_anchor: str | None = None

    @model_validator(mode='after')
    def validate_week(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Неизвестный часовой пояс')
        if self.parity_anchor and date.fromisoformat(self.parity_anchor).weekday() != 0:
            raise ValueError('Начало первой учебной недели должно быть понедельником')
        def check(slots):
            for i, slot in enumerate(slots):
                if minutes(slot.end) <= minutes(slot.start):
                    raise ValueError('Конец интервала должен быть позже начала; сон разделите в полночь')
                for other in slots[:i]:
                    if slot.start < other.end and slot.end > other.start:
                        if (slot.kind == 'work') != (other.kind == 'work'):
                            continue
                        raise ValueError('Пересекаются интервалы одного дня')
        for day in self.days:
            for parity in ('even', 'odd'):
                check([slot for slot in day if slot.week_pattern in ('all', parity)])
        for event in self.events:
            event_date = date.fromisoformat(event.date)
            parity = week_parity(event_date, self.parity_anchor)
            check([slot for slot in self.days[event_date.weekday()] if slot.week_pattern in ('all', parity)]
                  + [slot for slot in self.events if slot.date == event.date])
        return self


def week_parity(day, anchor=None):
    number = (day - date.fromisoformat(anchor)).days // 7 + 1 if anchor else day.isocalendar().week
    return 'even' if number % 2 == 0 else 'odd'


def slots_for_date(week, day):
    parity = week_parity(day, week.get('parity_anchor'))
    slots = [dict(slot) for slot in week['days'][day.weekday()]
             if slot.get('week_pattern', 'all') in ('all', parity)]
    slots += [dict(slot) for slot in week.get('events', []) if slot['date'] == day.isoformat()]
    from common.calendar import occurs
    slots += [dict(event) for event in week.get('calendar_events', []) if occurs(event, day)]
    occupied = [slot for slot in slots if slot['kind'] != 'work']
    work = []
    for slot in (slot for slot in slots if slot['kind'] == 'work'):
        pieces = [slot]
        for event in occupied:
            updated = []
            for piece in pieces:
                if piece['start'] >= event['end'] or piece['end'] <= event['start']:
                    updated.append(piece)
                else:
                    if piece['start'] < event['start']:
                        updated.append(piece | {'end': event['start']})
                    if piece['end'] > event['end']:
                        updated.append(piece | {'start': event['end']})
            pieces = updated
        work.extend(pieces)
    return sorted(work + occupied, key=lambda item: item['start'])


def default_week():
    slots = [dict(kind=kind, title=title, start=start, end=end) for kind, title, start, end in [
        ('sleep', 'Сон', '00:00', '07:00'), ('work', 'Работа', '09:00', '12:00'),
        ('meal', 'Обед', '12:00', '13:00'), ('work', 'Работа', '13:00', '16:00'),
        ('meal', 'Ужин', '19:00', '20:00'), ('sleep', 'Сон', '23:00', '24:00')]]
    return {'timezone': 'UTC', 'days': [[dict(slot) for slot in slots] for _ in range(7)]}


def read_week():
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'weekly_schedule')
        return json.loads(row.config) if row else None


def save_week(week):
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'weekly_schedule') or IntegrationSetting(name='weekly_schedule')
        row.config = week.model_dump_json()
        session.add(row)
        session.commit()
    return week.model_dump()
