"""Долгий анализ запускается сразу; интерфейс отдельно запрашивает результат."""
import asyncio
import logging
from collections import OrderedDict
from uuid import uuid4

from fastapi import HTTPException
import httpx

from common.identity import user_id

jobs = OrderedDict()


def start(operation):
    job_id = uuid4().hex
    record = {'owner': user_id(), 'status': 'running'}
    jobs[job_id] = record

    async def run():
        try:
            record['result'] = await operation()
            record['status'] = 'completed'
        except HTTPException as exc:
            record.update(status='failed', detail=exc.detail, code=exc.status_code)
        except httpx.HTTPError:
            record.update(status='failed', detail='Сервис анализа недоступен', code=502)
        except asyncio.CancelledError:
            record.update(status='failed', detail='Анализ прерван перезапуском сервиса', code=503)
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


def result(job_id):
    record = jobs.get(job_id)
    if not record or record['owner'] != user_id():
        raise HTTPException(404, 'Операция не найдена. Возможно, backend был перезапущен')
    if record['status'] == 'failed':
        raise HTTPException(record['code'], record['detail'])
    return {'status': record['status'], 'result': record.get('result')}
