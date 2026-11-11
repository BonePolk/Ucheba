import asyncio
import time

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from integrations import ameba, codex
from common.settings import read_config, read_settings
from common.models.integration import CodexRun
from common.models.task import Task


async def run():
    from routers.integrations import provider_call
    codex.recover_runs()
    next_score = next_prepare = 0
    # Запуск сервера сам по себе не повод заново строить расписание.
    previous_inputs = ameba.automatic_inputs()
    while True:
        now = time.monotonic()
        try:
            config = read_config('ameba')
            if config['enabled'] and now >= next_score:
                next_score = now + 60
                inputs = ameba.automatic_inputs()
                if inputs != previous_inputs:
                    # Не повторяем один и тот же запрос каждую минуту при ошибке модели.
                    previous_inputs = inputs
                    await provider_call('ameba', ameba.rank_all())
                    # После успешного расчёта учитываем уточнения Monocle.
                    # При ошибке новые изменения пользователя не теряются.
                    previous_inputs = ameba.automatic_inputs()
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
