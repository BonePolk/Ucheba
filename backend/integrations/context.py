import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from common.models.integration import TaskContext
from common.models.task import Task
from common.rpc import call
from common.settings import read_settings


def snapshot(task):
    return json.dumps(task.model_dump(include={'title', 'description', 'deadline', 'priority', 'status', 'category'}),
                      ensure_ascii=False, default=str, sort_keys=True)


def get_context(task_id):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        row = session.get(TaskContext, task_id)
        if not row:
            return {'status': 'new', 'subject': '', 'owner_notes': '', 'summary': '', 'questions': []}
        return row.model_dump(exclude={'questions', 'task_snapshot', 'usage'}) | {
            'questions': json.loads(row.questions), 'usage': json.loads(row.usage),
            'status': row.status if row.task_snapshot == snapshot(task) else 'new',
        }


def invalidate(task_id):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        row = session.get(TaskContext, task_id) or TaskContext(task_id=task_id)
        row.status = 'new'
        row.updated_at = datetime.now(timezone.utc)
        task.ai_score = task.ai_details = task.ai_scored_at = None
        session.add(row)
        session.add(task)
        session.commit()


def save_answer(task_id, subject, answer):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        row = session.get(TaskContext, task_id) or TaskContext(task_id=task_id)
        row.subject = subject.strip().casefold()
        row.owner_notes = answer.strip()
        row.status = 'new'
        row.updated_at = datetime.now(timezone.utc)
        task.ai_score = task.ai_details = task.ai_scored_at = None
        session.add(row)
        session.add(task)
        if row.subject:
            waiting = session.exec(select(TaskContext).where(
                TaskContext.task_id != task_id, TaskContext.status == 'needs_context',
            )).all()
            for other in waiting:
                if other.subject not in ('', row.subject):
                    continue
                other.status = 'new'
                session.add(other)
                pending = session.get(Task, other.task_id)
                if pending:
                    pending.ai_score = pending.ai_details = pending.ai_scored_at = None
                    session.add(pending)
        session.commit()


async def prepare(task_id, force=False):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        task_state = snapshot(task)
        row = session.get(TaskContext, task_id)
        version = row.updated_at if row else None
        row = row or TaskContext(task_id=task_id)
        if not force and row.task_snapshot == task_state and row.status in ('ready', 'needs_context'):
            return {'status': row.status, 'summary': row.summary, 'questions': json.loads(row.questions)}
        subject, notes = row.subject, row.owner_notes
        memory = []
        query = select(TaskContext).where(TaskContext.task_id != task_id)
        if subject:
            query = query.where(TaskContext.subject == subject)
        previous = session.exec(query.order_by(TaskContext.updated_at.desc()).limit(20)).all()
        for item in previous:
            source = session.get(Task, item.task_id)
            if source:
                memory.append({'task_id': source.id, 'subject': item.subject,
                               'title': source.title, 'description': (source.description or '')[:3000],
                               'owner_notes': item.owner_notes[:6000]})
    config, secrets = read_settings('context')
    if not config['enabled'] or not secrets.get('api_key'):
        raise HTTPException(400, 'В профиле включите Luna и сохраните API-ключ')
    result = await call('context', '/analyze', {
        'config': config, 'api_key': secrets['api_key'],
        'task_id': task_id, 'task': json.loads(task_state), 'subject': subject,
        'owner_notes': notes, 'past_tasks': memory,
    }, timeout=300)
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        row = session.get(TaskContext, task_id)
        current_version = row.updated_at if row else None
        row = row or TaskContext(task_id=task_id)
        if not task or snapshot(task) != task_state or row.subject != subject or row.owner_notes != notes or current_version != version:
            raise HTTPException(409, 'Задача или контекст изменились. Повторите оценку')
        row.status = result['status']
        row.summary = result['summary']
        row.questions = json.dumps(result['questions'], ensure_ascii=False)
        row.task_snapshot = task_state
        row.usage = json.dumps(result.get('usage', {}))
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
    return result
