"""Обработка ранее сохранённых предложений задач без обращения к AI."""
from datetime import datetime, timezone
from fastapi import HTTPException
from common.models.integration import TaskAction
from common.models.task import Task


def apply_suggestion(session, suggestion, values=None):
    if suggestion.status != 'pending':
        raise HTTPException(409, 'Предложение уже обработано')
    fields = values or {'title': suggestion.title, 'description': suggestion.description,
                        'deadline': suggestion.deadline}
    if suggestion.action == 'create':
        task = Task(**fields, source_chat_id=suggestion.source_chat_id, source_url=suggestion.source_url)
    else:
        task = session.get(Task, suggestion.target_task_id) if suggestion.target_task_id else None
        if not task:
            raise HTTPException(409, 'Выберите существующую задачу для изменения или отмены')
        if suggestion.action == 'cancel':
            task.archived = True
        else:
            task.sqlmodel_update(fields)
            task.ai_score = task.ai_details = task.ai_scored_at = None
        task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.flush()
    suggestion.task_id = task.id
    suggestion.status = 'accepted'
    session.add(suggestion)
    session.add(TaskAction(task_id=task.id, action=f'inbox_{suggestion.action}'))
    return task
