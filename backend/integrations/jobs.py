"""Долгий анализ запускается сразу; интерфейс отдельно запрашивает результат."""
import asyncio
import logging
from collections import OrderedDict
from uuid import uuid4

from fastapi import HTTPException
import httpx

from common.identity import user_id

jobs = OrderedDict()


def start(operation, kind=None):
    if kind and active(kind):
        raise HTTPException(409, 'Straus уже работает. Сначала остановите текущее поручение.')
    job_id = uuid4().hex
    record = {'owner': user_id(), 'status': 'running', 'kind': kind}
    jobs[job_id] = record

    async def run():
        try:
            record['result'] = await operation()
            record['status'] = 'completed'
        except HTTPException as exc:
            record.update(status='failed', detail=exc.detail, code=exc.status_code)
        except httpx.HTTPError as exc:
            logging.getLogger('uvicorn.error').warning('agent job: %s', type(exc).__name__)
            record.update(status='failed', detail='Сервис анализа недоступен', code=502)
        except asyncio.CancelledError:
            record.update(status='failed', detail='Операция остановлена', code=409)
            raise
        except Exception as exc:
            logging.getLogger('uvicorn.error').error('agent job: %s', type(exc).__name__)
            record.update(status='failed', detail='Не удалось завершить анализ', code=500)
        finally:
            record.pop('task', None)

    record['task'] = asyncio.create_task(run())
    for key in list(jobs):
        if len(jobs) <= 128:
            break
        if jobs[key]['status'] != 'running':
            del jobs[key]
    return {'job_id': job_id}


def active(kind):
    return next((record for record in jobs.values()
                 if record['owner'] == user_id() and record.get('kind') == kind
                 and record['status'] == 'running'), None)


def last(kind):
    records = [record for record in jobs.values()
               if record['owner'] == user_id() and record.get('kind') == kind]
    return records[-1] if records else None


async def stop(kind):
    record = active(kind)
    if record:
        task = record.get('task')
        if task:
            if not task.cancelling():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Задача могла быть отменена до первого запуска её coroutine.
        record.update(status='failed', detail='Операция остановлена', code=409)
        record.pop('task', None)
    return {'running': False}


def result(job_id):
    record = jobs.get(job_id)
    if not record or record['owner'] != user_id():
        raise HTTPException(404, 'Операция не найдена. Возможно, backend был перезапущен')
    if record['status'] == 'failed':
        raise HTTPException(record['code'], record['detail'])
    return {'status': record['status'], 'result': record.get('result')}
