import json

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from common.security import require_user
from sqlalchemy import case
from sqlmodel import Session, select

from common.database import get_engine
from common.settings import read_config
from common.accounts import features
from integrations import jev as jev_order
from integrations.context import context_status
from common.models.integration import TaskAction, TaskContext
from common.models.task import Task, TaskCreate, TaskStatus, TaskUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_user)])


@router.get("", response_model=list[Task])
def list_tasks(
    archived: bool = False,
    status: TaskStatus | None = None,
    priority: Literal["low", "medium", "high", "overdue"] | None = None,
    category: str | None = None,
    sort_by: Literal["deadline", "priority", "created_at", "ai_score", "jev"] = "deadline",
    order: Literal["asc", "desc"] = "asc",
):
    if sort_by == 'ai_score':
        sort_by, order = 'jev', 'asc'
    if sort_by == 'jev':
        jev = read_config('ameba')
        if not jev['enabled'] or not features()['ameba'] or archived:
            sort_by, order = 'priority', 'asc'
    overdue = (
        (Task.deadline < datetime.now(timezone.utc))
        & (Task.status != "done")
        & (Task.archived.is_(False))
    )
    statement = select(Task).where(Task.archived == archived)
    if status is not None:
        statement = statement.where(Task.status == status)
    if priority == "overdue":
        statement = statement.where(overdue)
    elif priority is not None:
        statement = statement.where(Task.priority == priority)
    if category is not None:
        statement = statement.where(Task.category == category)

    column = {
        "deadline": Task.deadline,
        "ai_score": Task.ai_score,
        "jev": case((Task.priority == 'high', 0), (Task.priority == 'medium', 1), else_=2),
        "created_at": Task.created_at,
        "priority": case(
            (Task.priority == "high", 0),
            (Task.priority == "medium", 1),
            else_=2,
        ),
    }[sort_by]
    if sort_by in ("deadline", "ai_score"):
        statement = statement.order_by(column.is_(None))
    statement = statement.order_by(column.asc() if order == "asc" else column.desc(), Task.id.desc())
    with Session(get_engine()) as session:
        tasks = session.exec(statement).all()
        if sort_by == 'jev':
            positions = {task_id: index for index, task_id in enumerate(jev_order.ranking_status()['task_ids'])}
            tasks.sort(key=lambda task: (
                positions.get(task.id, len(positions)),
                {'high': 0, 'medium': 1, 'low': 2}.get(task.priority, 1),
                task.deadline.replace(tzinfo=None) if task.deadline else datetime.max,
                task.id,
            ))
        contexts = {row.task_id: row for row in session.exec(select(TaskContext)).all()}
        result = []
        for task in tasks:
            context = contexts.get(task.id)
            state = context_status(task, context)
            details = task.ai_details
            if not task.archived and task.status != 'done' and state == 'needs_context':
                details = json.dumps({'status': 'needs_context', 'reason': ' '.join(json.loads(context.questions))})
            elif not task.archived and task.status != 'done' and state == 'new':
                details = json.dumps({'status': 'context_pending'})
            elif details and json.loads(details).get('status') == 'needs_context':
                details = None
            result.append(task.model_copy(update={'ai_details': details}))
        return result


@router.get('/categories', response_model=list[str])
def list_categories(archived: bool = False):
    with Session(get_engine()) as session:
        statement = select(Task.category).where(
            Task.archived == archived, Task.category.is_not(None), Task.category != '',
        ).distinct().order_by(Task.category)
        return session.exec(statement).all()


@router.get("/{task_id}", response_model=Task)
def get_task(task_id: int):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        return task


@router.post("", response_model=Task, status_code=201)
def create_task(data: TaskCreate):
    with Session(get_engine()) as session:
        task = Task.model_validate(data.model_dump(exclude={'agent_notes'}))
        session.add(task)
        session.flush()
        if data.agent_notes.strip():
            session.add(TaskContext(task_id=task.id, owner_notes=data.agent_notes.strip()))
        session.add(TaskAction(task_id=task.id, action='create'))
        session.commit()
        session.refresh(task)
        return task


@router.patch("/{task_id}", response_model=Task)
def update_task(task_id: int, data: TaskUpdate):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        changes = data.model_dump(exclude_unset=True)
        notes = changes.pop('agent_notes', None)
        if notes is not None:
            row = session.get(TaskContext, task_id) or TaskContext(task_id=task_id)
            row.owner_notes = notes.strip()
            row.status = 'new'
            row.updated_at = datetime.now(timezone.utc)
            task.ai_score = task.ai_details = task.ai_scored_at = None
            session.add(row)
        task.sqlmodel_update(changes)
        if set(changes) & {'title', 'description', 'deadline', 'priority', 'status', 'category'}:
            task.ai_score = task.ai_details = task.ai_scored_at = None
        session.add(TaskAction(task_id=task_id, action='update', details=json.dumps(
            {key: value for key, value in changes.items() if key in {'status', 'priority', 'archived'}})))
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, permanent: bool = False):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        session.add(TaskAction(task_id=task_id, action='delete' if permanent else 'archive'))
        if permanent:
            if not task.archived:
                raise HTTPException(409, "Полностью удалить можно только задачу из архива")
            session.delete(task)
        else:
            task.archived = True
            task.updated_at = datetime.now(timezone.utc)
            session.add(task)
        session.commit()
    return Response(status_code=204)
