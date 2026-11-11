from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from pydantic import BaseModel, Field

from common import calendar
from common.database import get_engine
from common.models.task import Task
from common.security import require_user
from common.accounts import check_feature, features
from integrations import straus

router = APIRouter(prefix='/api/calendar', tags=['calendar'], dependencies=[Depends(require_user)])


@router.get('')
def get_calendar(start: date, end: date):
    if end < start or (end - start).days > 62:
        raise HTTPException(422, 'Выберите период не длиннее 63 дней')
    config = calendar.read_config()
    return {'config': config, 'timezone': config['timezone'],
            'events': calendar.view_events(start, end) + (calendar.ameba_events(start, end) if features()['ameba'] else []), 'today': calendar.today().isoformat()}


@router.put('')
async def update_calendar(body: calendar.CalendarConfig):
    with Session(get_engine()) as session:
        if any(event.task_id is not None and not session.get(Task, event.task_id) for event in body.events):
            raise HTTPException(404, 'Задача календаря не найдена')
    return calendar.save_config(body)


class CalendarCommand(BaseModel):
    instruction: str = Field(min_length=1, max_length=12000)


@router.post('/agent')
async def edit_with_agent(body: CalendarCommand):
    check_feature('straus')
    return straus.start_edit(body.instruction)
