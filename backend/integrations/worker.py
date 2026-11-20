import asyncio
import json
import time

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import get_engine
from common.identity import user_id
from integrations import ameba, arrodes, scheduler
from common.settings import read_config, read_settings
from common.models.integration import ArrodesRun, TaskRanking
from common.models.task import Task


async def run():
    from routers.integrations import provider_call
    arrodes.recover_runs()
    next_score = next_prepare = 0
    # Запуск сервера сам по себе не повод заново строить расписание.
    previous_inputs = ameba.automatic_inputs()
    refresh_hour = int(time.time() // 3600)
    while True:
        now = time.monotonic()
        try:
            config = read_config('ameba')
            if config['enabled'] and now >= next_score and not scheduler.work_busy():
                next_score = now + 60
                inputs = ameba.automatic_inputs()
                if config.get('auto_replan', True) and inputs != previous_inputs:
                    # Не повторяем один и тот же запрос каждую минуту при ошибке модели.
                    prev_inputs = previous_inputs
                    previous_inputs = inputs
                    try:
                        await provider_call('ameba', ameba.rank_all())
                    except HTTPException as exc:
                        if exc.status_code == 409:
                            previous_inputs = prev_inputs
                            next_score = 0
                        raise
                    # После успешного расчёта учитываем уточнения Monocle.
                    # При ошибке новые изменения пользователя не теряются.
                    previous_inputs = ameba.automatic_inputs()
                elif int(time.time() // 3600) != refresh_hour:
                    prev_hour = refresh_hour
                    refresh_hour = int(time.time() // 3600)
                    with Session(get_engine()) as session:
                        saved = session.get(TaskRanking, 1)
                        refresh = bool(saved and saved.snapshot == ameba.task_state(session)[0]
                                       and ameba.plan_expired(json.loads(saved.result)))
                    if refresh:
                        try:
                            refreshed = await ameba.refresh_saved_plan()
                        except HTTPException as exc:
                            if exc.status_code == 409:
                                refresh_hour = prev_hour
                                next_score = 0
                            raise
                        if not refreshed:
                            if scheduler.work_busy():
                                refresh_hour = prev_hour
                                next_score = 0
                            elif config.get('auto_replan', True):
                                try:
                                    await provider_call('ameba', ameba.rank_all())
                                except HTTPException as exc:
                                    if exc.status_code == 409:
                                        refresh_hour = prev_hour
                                        next_score = 0
                                    raise
            agent, _ = read_settings('arrodes')
            if agent['enabled'] and agent['auto_prepare'] and now >= next_prepare and not (arrodes.job_tasks.get(user_id()) and not arrodes.job_tasks[user_id()].done()):
                next_prepare = now + 60
                with Session(get_engine()) as session:
                    task = session.exec(select(Task).where(Task.archived == False, Task.status == 'todo',
                        Task.source_chat_id.is_not(None), Task.id.not_in(select(ArrodesRun.task_id))).limit(1)).first()
                    if task:
                        arrodes.start_task(task.id)
        except HTTPException:
            pass
        await asyncio.sleep(5)
