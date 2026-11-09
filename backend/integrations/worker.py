import asyncio
import time

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from integrations import codex, jev
from common.settings import read_config, read_settings
from common.models.integration import CodexRun
from common.models.task import Task


async def run():
    from routers.integrations import provider_call
    codex.recover_runs()
    next_score = next_prepare = 0
    while True:
        now = time.monotonic()
        try:
            config = read_config('jev')
            if config['enabled'] and now >= next_score:
                next_score = now + 60
                with Session(get_engine()) as session:
                    ids = [t.id for t in session.exec(select(Task).where(
                        Task.archived == False, Task.status != 'done', (Task.ai_scored_at.is_(None) | Task.ai_details.not_like('%"version": 5%'))).limit(3)).all()]
                for task_id in ids:
                    await provider_call('jev', jev.evaluate(task_id))
                await provider_call('jev', jev.rank_all())
            agent, _ = read_settings('codex')
            if agent['enabled'] and agent['auto_prepare'] and now >= next_prepare and not (codex.job_task and not codex.job_task.done()):
                next_prepare = now + 60
                with Session(get_engine()) as session:
                    task = session.exec(select(Task).where(Task.archived == False, Task.status == 'todo',
                        Task.source_chat_id.is_not(None), Task.id.not_in(select(CodexRun.task_id))).limit(1)).first()
                    if task:
                        codex.start_task(task.id)
        except HTTPException:
            pass
        await asyncio.sleep(5)
