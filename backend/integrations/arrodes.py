import asyncio
import uuid
import json
import logging
from common.identity import user_id
from common.models.integration import TaskContext
from integrations import scheduler, subtasks as subtasks_service

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from common.rpc import call
from common.settings import report, resolve_agent_auth
from common.models.integration import ArrodesRun, TaskAction
from common.models.task import Task

job_tasks = {}


async def status():
    return await call('arrodes', '/status', method='GET', timeout=30)


async def start_login():
    return await call('arrodes', '/connect')


async def disconnect():
    return await call('arrodes', '/disconnect')


async def check():
    config, api_key = resolve_agent_auth('arrodes')
    result = await call('arrodes', '/check', {'config': config, 'api_key': api_key}, timeout=120)
    report('arrodes', success=True)
    return result


async def execute(run_id, payload, task_id, auth=None, options=None):
    try:
        with Session(get_engine()) as session:
            run = session.get(ArrodesRun, run_id)
            run.status = 'running'
            session.add(run)
            session.commit()
        async with scheduler.slot('arrodes', task_id):
            try:
                result = await call('arrodes', '/run', {
                    'run_id': run_id,
                    'payload': payload,
                    'task_id': task_id,
                    'auth': auth or {},
                    'options': options or {},
                }, timeout=None)
            except BaseException:
                try:
                    await call('arrodes', '/cancel', {'run_id': run_id}, timeout=None)
                except HTTPException as exc:
                    logging.getLogger('uvicorn.error').warning('Arrodes cancellation: HTTP %s', exc.status_code)
                raise
        with Session(get_engine()) as session:
            run = session.get(ArrodesRun, run_id)
            run.status, run.summary = 'succeeded', result['summary']
            session.add(run)
            task = session.get(Task, run.task_id)
            if task and task.status != 'done' and not task.archived:
                task.status = 'review'
                session.add(task)
            session.add(TaskAction(task_id=run.task_id, action='arrodes_prepared'))
            session.commit()
        report('arrodes', success=True)
    except asyncio.CancelledError:
        with Session(get_engine()) as session:
            run = session.get(ArrodesRun, run_id)
            run.status = 'interrupted'
            session.add(run)
            session.commit()
        raise
    except Exception as exc:
        with Session(get_engine()) as session:
            run = session.get(ArrodesRun, run_id)
            run.status = 'failed'
            run.error = str(exc.detail) if isinstance(exc, HTTPException) else 'Arrodes недоступен: проверьте авторизацию, лимит и Linux sandbox'
            session.add(run)
            session.commit()
        report('arrodes', error='Не удалось подготовить материалы')


def start_task(task_id, instruction='', resume_run_id=None):
    job_task = job_tasks.get(user_id())
    config, api_key = resolve_agent_auth('arrodes', require_enabled=True)
    auth = {
        'auth_mode': config['auth_mode'],
        'base_url': config['base_url'],
        'api_key': api_key,
    }
    options = {
        key: config[key]
        for key in ('context_window', 'reasoning_effort', 'web_search_mode', 'allow_browser', 'allow_shell', 'ask_clarifications', 'custom_instructions')
    }
    if job_task and not job_task.done():
        raise HTTPException(409, 'Arrodes уже обрабатывает задачу')
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task or task.archived:
            raise HTTPException(404, 'Задача не найдена или находится в архиве')
        run = ArrodesRun(id=uuid.uuid4().hex, task_id=task_id)
        if resume_run_id:
            previous = session.get(ArrodesRun, resume_run_id)
            if previous is None or previous.task_id != task_id or previous.status in ('queued', 'running'):
                raise HTTPException(409, 'Выберите завершённый запуск этой задачи')
        session.add(run)
        session.commit()
        run_id = run.id
        payload = {'task': task_data(session, task), 'task_id': task_id, 'model': config['model'].strip(),
                   'instruction': instruction.strip() or 'Выполни эту задачу и сохрани результат для моей проверки.',
                   'resume_run_id': resume_run_id,
                   'previous_summary': previous.summary if resume_run_id else None}
    job_tasks[user_id()] = asyncio.create_task(execute(run_id, payload, task_id, auth, options))
    return {'id': run_id, 'status': 'queued'}


def recover_runs():
    with Session(get_engine()) as session:
        for run in session.exec(select(ArrodesRun).where(ArrodesRun.status.in_(['queued', 'running']))).all():
            run.status = 'interrupted'
            run.error = 'Админка перезапущена. Повторите запуск вручную.'
            session.add(run)
        session.commit()


async def stop():
    task = job_tasks.get(user_id())
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return {'ok': True}


async def close():
    tasks = [task for task in job_tasks.values() if not task.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def task_data(session, task):
    context = session.get(TaskContext, task.id)
    subtasks = subtasks_service.serialize_for_agent(subtasks_service.list_for_task(session, task.id))
    return task.model_dump(mode='json') | {
        'subtasks': subtasks,
        'context': {
            'subject': context.subject, 'owner_notes': context.owner_notes, 'status': context.status,
            'questions': json.loads(context.questions), 'full_context': json.loads(context.usage).get('full_markdown'),
            'sources': json.loads(context.usage).get('sources', []),
        } if context else None,
    }


def read_tasks(task_id=None, query='', status='', offset=0):
    with Session(get_engine()) as session:
        if task_id is not None:
            task = session.get(Task, task_id)
            if task is None:
                raise HTTPException(404, 'Задача не найдена')
            return task_data(session, task)
        matches = []
        for task in session.exec(select(Task).order_by(Task.id)).all():
            data = task_data(session, task)
            if status and task.status != status:
                continue
            if query.casefold() not in json.dumps(data, ensure_ascii=False).casefold():
                continue
            matches.append({key: data[key] for key in ('id', 'title', 'category', 'status', 'deadline', 'archived')} |
                           {'subject': (data['context'] or {}).get('subject', '')})
        offset = max(0, offset)
        return {'tasks': matches[offset:offset + 50], 'total': len(matches), 'offset': offset}
