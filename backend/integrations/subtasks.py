"""Хелперы хранения и безопасной синхронизации подзадач с защитой пользовательских правок."""
from datetime import datetime, timezone
from typing import Literal

from sqlmodel import Session, select

from common.models.task import Task, TaskSubtask


def list_for_task(session: Session, task_id: int) -> list[TaskSubtask]:
    statement = select(TaskSubtask).where(TaskSubtask.task_id == task_id).order_by(TaskSubtask.position, TaskSubtask.id)
    return list(session.exec(statement).all())


def list_for_tasks(session: Session, task_ids: list[int]) -> dict[int, list[TaskSubtask]]:
    if not task_ids:
        return {}
    statement = (
        select(TaskSubtask)
        .where(TaskSubtask.task_id.in_(task_ids))
        .order_by(TaskSubtask.task_id, TaskSubtask.position, TaskSubtask.id)
    )
    grouped: dict[int, list[TaskSubtask]] = {}
    for row in session.exec(statement).all():
        grouped.setdefault(row.task_id, []).append(row)
    return grouped


def serialize_for_agent(items: list[TaskSubtask]) -> list[dict]:
    return [
        {
            'id': item.id,
            'title': item.title,
            'done': item.done,
            'estimated_minutes': item.estimated_minutes,
            'source': item.source,
        }
        for item in items
    ]


def normalize_minutes(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        minutes = int(round(value))
        return min(10080, minutes) if minutes > 0 else None
    return None


def ameba_step_minutes(step: dict) -> int | None:
    low = max(0, int(step.get('min_minutes') or 0))
    high = max(low, int(step.get('max_minutes') or 0))
    if high <= 0:
        return None
    return max(5, int(round((0.35 * low + 0.65 * high) / 5) * 5))


def clean_items(items: list[dict], max_items: int = 15) -> list[dict]:
    cleaned: list[dict] = []
    seen: set[str] = set()
    for raw in items or ():
        if isinstance(raw, str):
            raw_title, raw_minutes = raw, None
        elif isinstance(raw, dict):
            raw_title, raw_minutes = raw.get('title'), raw.get('estimated_minutes')
        else:
            raw_title = getattr(raw, 'title', None)
            raw_minutes = getattr(raw, 'estimated_minutes', None)
        if not isinstance(raw_title, str):
            continue
        title = ' '.join(raw_title.split())[:300].strip()
        if not title:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            'title': title,
            'estimated_minutes': normalize_minutes(raw_minutes),
        })
        if len(cleaned) >= max_items:
            break
    return cleaned


def sync_agent_subtasks(
    session: Session,
    task: Task,
    items: list[dict],
    source: Literal['monocle', 'ameba'],
) -> bool:
    cleaned = clean_items(items)
    if not cleaned:
        return False
    if source == 'ameba' and len(cleaned) < 2:
        return False
    if source == 'monocle' and len(cleaned) == 1 and cleaned[0]['title'].casefold() == task.title.strip().casefold():
        return False
    if task.subtasks_locked:
        return False

    existing = list_for_task(session, task.id)
    if any(row.source == 'user' or row.user_edited or row.done for row in existing):
        return False
    if source == 'ameba' and any(row.source == 'monocle' for row in existing):
        return False

    current_shape = [(row.title, row.estimated_minutes, row.source) for row in existing]
    next_shape = [(item['title'], item['estimated_minutes'], source) for item in cleaned]
    if current_shape == next_shape:
        return False

    for row in existing:
        session.delete(row)
    session.flush()
    now = datetime.now(timezone.utc)
    for idx, item in enumerate(cleaned):
        session.add(TaskSubtask(
            task_id=task.id,
            title=item['title'],
            done=False,
            estimated_minutes=item['estimated_minutes'],
            position=idx,
            source=source,
            user_edited=False,
            created_at=now,
            updated_at=now,
        ))
    return True


def reindex_positions(session: Session, task_id: int) -> list[TaskSubtask]:
    rows = list_for_task(session, task_id)
    for idx, row in enumerate(rows):
        if row.position != idx:
            row.position = idx
            session.add(row)
    return rows
