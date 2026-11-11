"""История находится в отдельной SQLite каждого пользователя."""
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from common.models.integration import AgentHistory


def public(row):
    return row.model_dump() | {'request': json.loads(row.request), 'result': json.loads(row.result)}


def read(before=None, limit=20):
    target = get_engine()
    AgentHistory.__table__.create(target, checkfirst=True)
    query = select(AgentHistory).order_by(AgentHistory.id.desc()).limit(limit)
    if before is not None:
        query = query.where(AgentHistory.id < before)
    with Session(target) as session:
        return [public(row) for row in session.exec(query).all()]


def start(body):
    history = read()
    # В модель отправляем недавние поручения и результаты, без огромных старых календарей.
    context = []
    for item in reversed(history):
        result = item['result']
        context.append({key: item[key] for key in ('agent', 'created_at', 'status', 'request')} |
            {'result': {key: result[key] for key in ('reason', 'status', 'summary', 'full_markdown', 'questions', 'explanation', 'task_ids') if key in result}})
    with Session(get_engine()) as session:
        row = AgentHistory(agent=body['agent'], request=json.dumps(body['request'], ensure_ascii=False))
        session.add(row)
        session.commit()
        session.refresh(row)
        return {'id': row.id, 'history': context}


def finish(body):
    with Session(get_engine()) as session:
        row = session.get(AgentHistory, body['id'])
        if row is None:
            raise HTTPException(404, 'Запись истории не найдена')
        row.status = body['status']
        row.result = json.dumps(body.get('result', {}), ensure_ascii=False, default=str)
        row.finished_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
    return {'ok': True}
