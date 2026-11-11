"""Явно сохранённые факты о владельце, общие для агентов его аккаунта."""
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from common.database import get_engine
from common.models.integration import AgentFact


class FactInput(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    text: str = Field(min_length=1, max_length=1500)


def target():
    engine = get_engine()
    AgentFact.__table__.create(engine, checkfirst=True)
    return engine


def read():
    with Session(target()) as session:
        return [row.model_dump(mode='json') for row in session.exec(select(AgentFact).order_by(AgentFact.updated_at.desc())).all()]


def save(body, agent):
    body = FactInput.model_validate(body)
    if not body.text.strip():
        raise HTTPException(422, 'Факт не должен быть пустым')
    with Session(target()) as session:
        row = session.get(AgentFact, body.key) or AgentFact(key=body.key, text=body.text, agent=agent)
        if row.agent in ('Владелец', 'user') and agent not in ('Владелец', 'user'):
            if row.text != body.text.strip():
                raise HTTPException(422, 'Факт задан владельцем. Не заменяй его догадкой; сохрани подтверждённое новое наблюдение отдельно')
            return row.model_dump(mode='json')
        row.text, row.agent = body.text.strip(), agent
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.model_dump(mode='json')


def delete(key):
    with Session(target()) as session:
        row = session.get(AgentFact, key)
        if row:
            session.delete(row)
            session.commit()
    return {'ok': True}
