"""Straus изменяет календарь только по явному поручению владельца."""
import asyncio
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, select

from common import calendar
from common.database import get_engine
from common.models.task import Task
from common.rpc import call
from common.settings import read_settings, report
from integrations import jobs, scheduler


def start_edit(instruction):
    config, secrets = read_settings('straus')
    if not config['enabled'] or not secrets.get('api_key'):
        raise HTTPException(400, 'Включите Straus и сохраните его API-ключ')
    run_id = uuid4().hex

    async def operation():
        async with scheduler.slot('context'):
            try:
                original = calendar.read_config()
                with Session(get_engine()) as session:
                    tasks = [{'id': task.id, 'title': task.title, 'summary': task.description or ''}
                             for task in session.exec(select(Task).where(Task.archived == False, Task.status != 'done')).all()]
                result = await call('straus', '/edit', {'instruction': instruction, 'calendar': original,
                    'tasks': tasks, 'run_id': run_id, 'config': config, 'api_key': secrets['api_key']}, timeout=None)
                updated = calendar.CalendarConfig.model_validate(result['calendar'])
                updated.revision = original['revision']
                original_ids = {event['task_id'] for event in original['events'] if event.get('task_id')}
                task_ids = original_ids | {task['id'] for task in tasks}
                if any(event.task_id is not None and event.task_id not in task_ids for event in updated.events):
                    raise HTTPException(502, 'Straus указал неизвестную задачу. Изменения не сохранены.')
                changed = updated.model_dump() != original
                status = result.get('status')
                if status not in ('changed', 'unchanged', 'blocked') or (status == 'changed') != changed:
                    raise HTTPException(502, 'Результат Straus не совпадает с изменениями календаря. Ничего не сохранено.')
                if changed:
                    calendar.save_config(updated)
                if status != 'blocked':
                    report('straus', success=True)
                return {'changed': changed, 'status': status, 'reason': result['reason']}
            except asyncio.CancelledError:
                await call('straus', '/cancel', {'run_id': run_id}, timeout=None)
                raise
    return jobs.start(operation, kind='straus')
