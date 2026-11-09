import asyncio
import uuid

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from common.rpc import call
from common.settings import read_settings, report
from common.models.integration import CodexRun, TaskAction
from common.models.task import Task

job_task = None


async def status():
    return await call('codex', '/status', method='GET', timeout=30)


async def start_login():
    return await call('codex', '/connect')


async def disconnect():
    return await call('codex', '/disconnect')


async def check():
    result = await call('codex', '/check', timeout=100)
    report('codex', success=True)
    return result


async def execute(run_id, payload, task_id):
    try:
        with Session(get_engine()) as session:
            run = session.get(CodexRun, run_id)
            run.status = 'running'
            session.add(run)
            session.commit()
        result = await call('codex', '/run', {'run_id': run_id, 'payload': payload, 'task_id': task_id}, timeout=320)
        with Session(get_engine()) as session:
            run = session.get(CodexRun, run_id)
            run.status, run.summary = 'succeeded', result['summary']
            session.add(run)
            task = session.get(Task, run.task_id)
            if task and task.status != 'done' and not task.archived:
                task.status = 'review'
                session.add(task)
            session.add(TaskAction(task_id=run.task_id, action='codex_prepared'))
            session.commit()
        report('codex', success=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        with Session(get_engine()) as session:
            run = session.get(CodexRun, run_id)
            run.status = 'failed'
            run.error = 'Codex недоступен: проверьте авторизацию, лимит и Linux sandbox'
            session.add(run)
            session.commit()
        report('codex', error='Не удалось подготовить материалы')


def start_task(task_id):
    global job_task
    if not read_settings('codex')[0]['enabled']:
        raise HTTPException(400, 'Включите Codex в настройках')
    if job_task and not job_task.done():
        raise HTTPException(409, 'Codex уже обрабатывает задачу')
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task or task.archived:
            raise HTTPException(404, 'Задача не найдена или находится в архиве')
        run = CodexRun(id=uuid.uuid4().hex, task_id=task_id)
        session.add(run)
        session.commit()
        run_id = run.id
        payload = {'title': task.title, 'description': task.description}
    job_task = asyncio.create_task(execute(run_id, payload, task_id))
    return {'id': run_id, 'status': 'queued'}


def recover_runs():
    with Session(get_engine()) as session:
        for run in session.exec(select(CodexRun).where(CodexRun.status.in_(['queued', 'running']))).all():
            run.status = 'interrupted'
            run.error = 'Админка перезапущена. Повторите запуск вручную.'
            session.add(run)
        session.commit()


async def close():
    if job_task and not job_task.done():
        job_task.cancel()
        await asyncio.gather(job_task, return_exceptions=True)
