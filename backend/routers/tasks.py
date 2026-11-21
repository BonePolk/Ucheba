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
from integrations import ameba as ameba_order, subtasks as subtasks_service
from integrations.context import context_status
from common.models.integration import TaskAction, TaskContext
from common.models.task import (
    SubtaskCreate,
    SubtaskReorder,
    SubtaskUpdate,
    Task,
    TaskCreate,
    TaskRead,
    TaskStatus,
    TaskSubtask,
    TaskSubtaskRead,
    TaskUpdate,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_user)])
_UNSET = object()


def _task_read(task: Task, items: list[TaskSubtask], ai_details=_UNSET) -> TaskRead:
    data = task.model_dump()
    if ai_details is not _UNSET:
        data['ai_details'] = ai_details
    data['subtasks'] = [TaskSubtaskRead.model_validate(item) for item in items]
    return TaskRead.model_validate(data)


def _require_active_task(session: Session, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Задача не найдена")
    if task.archived:
        raise HTTPException(409, "Нельзя изменять подзадачи архивной задачи")
    return task


@router.get("", response_model=list[TaskRead])
def list_tasks(
    archived: bool = False,
    status: TaskStatus | None = None,
    priority: Literal["low", "medium", "high", "overdue"] | None = None,
    category: str | None = None,
    sort_by: Literal["deadline", "priority", "created_at", "ai_score", "ameba"] = "deadline",
    order: Literal["asc", "desc"] = "asc",
):
    if sort_by == 'ai_score':
        sort_by, order = 'ameba', 'asc'
    if sort_by == 'ameba':
        ranking_config = read_config('ameba')
        if not ranking_config['enabled'] or not features()['ameba'] or archived:
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
        "ameba": case((Task.priority == 'high', 0), (Task.priority == 'medium', 1), else_=2),
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
        if sort_by == 'ameba':
            positions = {task_id: index for index, task_id in enumerate(ameba_order.ranking_status()['task_ids'])}
            tasks.sort(key=lambda task: (
                positions.get(task.id, len(positions)),
                {'high': 0, 'medium': 1, 'low': 2}.get(task.priority, 1),
                task.deadline.replace(tzinfo=None) if task.deadline else datetime.max,
                task.id,
            ))
        contexts = {row.task_id: row for row in session.exec(select(TaskContext)).all()}
        subtasks_map = subtasks_service.list_for_tasks(session, [task.id for task in tasks])
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
            result.append(_task_read(task, subtasks_map.get(task.id, []), ai_details=details))
        return result


@router.get('/categories', response_model=list[str])
def list_categories(archived: bool = False):
    with Session(get_engine()) as session:
        statement = select(Task.category).where(
            Task.archived == archived, Task.category.is_not(None), Task.category != '',
        ).distinct().order_by(Task.category)
        return session.exec(statement).all()


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        return _task_read(task, subtasks_service.list_for_task(session, task_id))


@router.post("", response_model=TaskRead, status_code=201)
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
        return _task_read(task, [])


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(task_id: int, data: TaskUpdate):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        changes = data.model_dump(exclude_unset=True)
        notes = changes.pop('agent_notes', None)
        if 'subtasks_locked' in changes and changes['subtasks_locked'] is None:
            changes.pop('subtasks_locked')
        unlock_only = notes is None and set(changes) == {'subtasks_locked'}
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
        if not unlock_only:
            task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(task)
        return _task_read(task, subtasks_service.list_for_task(session, task_id))


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
            for row in subtasks_service.list_for_task(session, task_id):
                session.delete(row)
            session.delete(task)
        else:
            task.archived = True
            task.updated_at = datetime.now(timezone.utc)
            session.add(task)
        session.commit()
    return Response(status_code=204)


@router.post("/{task_id}/subtasks", response_model=TaskSubtaskRead, status_code=201)
def create_subtask(task_id: int, data: SubtaskCreate):
    with Session(get_engine()) as session:
        task = _require_active_task(session, task_id)
        existing = subtasks_service.list_for_task(session, task_id)
        position = max((row.position for row in existing), default=-1) + 1
        now = datetime.now(timezone.utc)
        subtask = TaskSubtask(
            task_id=task_id,
            title=data.title,
            done=False,
            estimated_minutes=subtasks_service.normalize_minutes(data.estimated_minutes),
            position=position,
            source='user',
            user_edited=True,
            created_at=now,
            updated_at=now,
        )
        task.subtasks_locked = True
        session.add(subtask)
        session.add(task)
        session.commit()
        session.refresh(subtask)
        return TaskSubtaskRead.model_validate(subtask)


@router.put("/{task_id}/subtasks/reorder", response_model=list[TaskSubtaskRead])
def reorder_subtasks(task_id: int, data: SubtaskReorder):
    with Session(get_engine()) as session:
        task = _require_active_task(session, task_id)
        existing = subtasks_service.list_for_task(session, task_id)
        by_id = {row.id: row for row in existing}
        if len(data.subtask_ids) != len(by_id) or set(data.subtask_ids) != set(by_id):
            raise HTTPException(422, "Передайте точный список всех подзадач этой задачи")
        now = datetime.now(timezone.utc)
        ordered = []
        for idx, sub_id in enumerate(data.subtask_ids):
            row = by_id[sub_id]
            row.position = idx
            row.user_edited = True
            row.updated_at = now
            session.add(row)
            ordered.append(row)
        task.subtasks_locked = True
        session.add(task)
        session.commit()
        for row in ordered:
            session.refresh(row)
        return [TaskSubtaskRead.model_validate(row) for row in ordered]


@router.patch("/{task_id}/subtasks/{subtask_id}", response_model=TaskSubtaskRead)
def update_subtask(task_id: int, subtask_id: int, data: SubtaskUpdate):
    with Session(get_engine()) as session:
        task = _require_active_task(session, task_id)
        subtask = session.get(TaskSubtask, subtask_id)
        if subtask is None or subtask.task_id != task_id:
            raise HTTPException(404, "Подзадача не найдена")
        changes = data.model_dump(exclude_unset=True)
        if 'title' in changes and changes['title'] is None:
            raise HTTPException(422, "Укажите название подзадачи")
        if 'estimated_minutes' in changes:
            changes['estimated_minutes'] = subtasks_service.normalize_minutes(changes['estimated_minutes'])
        subtask.sqlmodel_update(changes)
        subtask.user_edited = True
        subtask.updated_at = datetime.now(timezone.utc)
        task.subtasks_locked = True
        session.add(subtask)
        session.add(task)
        session.commit()
        session.refresh(subtask)
        return TaskSubtaskRead.model_validate(subtask)


@router.delete("/{task_id}/subtasks/{subtask_id}", status_code=204)
def delete_subtask(task_id: int, subtask_id: int):
    with Session(get_engine()) as session:
        task = _require_active_task(session, task_id)
        subtask = session.get(TaskSubtask, subtask_id)
        if subtask is None or subtask.task_id != task_id:
            raise HTTPException(404, "Подзадача не найдена")
        session.delete(subtask)
        session.flush()
        subtasks_service.reindex_positions(session, task_id)
        task.subtasks_locked = True
        session.add(task)
        session.commit()
    return Response(status_code=204)
