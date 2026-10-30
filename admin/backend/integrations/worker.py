import asyncio
import time

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import engine
from integrations import codex, jev
from common.settings import read_settings
from common.models.integration import CodexRun
from common.models.task import Task


async def run():
    from routers.integrations import provider_call
    next_score = next_prepare = 0
    while True:
        now = time.monotonic()
        try:
            config, _ = read_settings('jev')
            if config['enabled'] and now >= next_score:
                next_score = now + 60
                with Session(engine) as session:
                    ids = [t.id for t in session.exec(select(Task).where(
                        Task.archived == False, Task.status != 'done', Task.ai_score.is_(None)).limit(3)).all()]
                for task_id in ids:
                    await provider_call('jev', jev.evaluate(task_id))
            agent, _ = read_settings('codex')
            if agent['enabled'] and agent['auto_prepare'] and now >= next_prepare and not (codex.job_task and not codex.job_task.done()):
                next_prepare = now + 60
                with Session(engine) as session:
                    task = session.exec(select(Task).where(Task.archived == False, Task.status == 'todo',
                        Task.source_chat_id.is_not(None), Task.id.not_in(select(CodexRun.task_id))).limit(1)).first()
                    if task:
                        codex.start_task(task.id)
        except HTTPException:
            pass
        await asyncio.sleep(5)
