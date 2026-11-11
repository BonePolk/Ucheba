"""Lucheba и Straus делят браузер и имеют приоритет перед Ameba."""
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import HTTPException

from common.identity import user_id
from common.rpc import call

condition = asyncio.Condition()
waiting = []
running = []
planned = {}
active_users = set()
jeba_users = set()
ranking_requests = {}


async def interrupt_ranking(own_only=False):
    # Прерываем только Ameba владельца запроса. Чужие очереди независимы.
    request = ranking_requests.get(user_id())
    if request:
        # Отменяем только запрос Ameba, не фоновый worker целиком.
        if not request['task'].cancelling():
            request['task'].cancel()
        await asyncio.gather(request['task'], return_exceptions=True)


async def run_ranking(body):
    run_id = uuid4().hex

    async def request():
        try:
            return await call('ameba', '/rank', body | {'run_id': run_id}, timeout=None)
        except asyncio.CancelledError:
            # Сначала останавливаем модель в отдельном сервисе, затем отдаём очередь.
            await call('ameba', '/cancel', {'run_id': run_id}, timeout=None)
            raise

    task = asyncio.create_task(request())
    entry = {'owner': user_id(), 'task': task}
    ranking_requests[user_id()] = entry
    try:
        return await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
        raise HTTPException(409, 'Ameba остановлена. Последний порядок сохранён; сортировку можно запустить заново.') from None
    finally:
        if ranking_requests.get(user_id()) is entry:
            ranking_requests.pop(user_id(), None)


@asynccontextmanager
async def slot(provider, task_id=0):
    owner = user_id()
    entry = {'owner': owner, 'provider': provider, 'task_id': task_id}
    async with condition:
        waiting.append(entry)
    try:
        if provider == 'context':
            await interrupt_ranking()
        async with condition:
            def available():
                if owner in jeba_users or owner in active_users:
                    return False
                browser_waiting = [item for item in waiting if item['owner'] == owner and item['provider'] == 'context']
                if provider == 'context':
                    return bool(browser_waiting) and browser_waiting[0] is entry
                ranking_waiting = [item for item in waiting if item['owner'] == owner and item['provider'] != 'context']
                return not browser_waiting and not planned.get(owner) and ranking_waiting[0] is entry

            await condition.wait_for(available)
            waiting.remove(entry)
            if provider == 'context':
                active_users.add(owner)
                if owner in planned:
                    planned[owner] = [value for value in planned[owner] if value != task_id]
            else:
                jeba_users.add(owner)
            running.append(entry)
    except BaseException:
        async with condition:
            waiting[:] = [item for item in waiting if item is not entry]
            condition.notify_all()
        raise
    try:
        yield
    finally:
        async with condition:
            running[:] = [item for item in running if item is not entry]
            if provider == 'context':
                active_users.discard(owner)
            else:
                jeba_users.discard(owner)
            condition.notify_all()


@asynccontextmanager
async def batch(task_ids):
    owner = user_id()
    planned[owner] = list(task_ids)
    try:
        yield
    finally:
        async with condition:
            planned.pop(owner, None)
            condition.notify_all()


def queue_state():
    owner = user_id()
    active = [dict(item) for item in running if item['owner'] == owner]
    pending = [dict(item) for item in waiting if item['owner'] == owner]
    visible = {item['task_id'] for item in active + pending if item['provider'] == 'context'}
    pending.extend({'owner': owner, 'provider': 'context', 'task_id': task_id}
                   for task_id in planned.get(owner, []) if task_id not in visible)
    return {'active': active, 'waiting': pending,
            'browser_busy': bool(owner in active_users or any(item['provider'] == 'context' for item in pending) or planned.get(owner)),
            'ameba_running': any(item['provider'] == 'jev' for item in active),
            'busy': bool(active or pending)}


def queued(provider, task_id):
    owner = user_id()
    return any(item['owner'] == owner and item['provider'] == provider
               and item['task_id'] == task_id for item in waiting)
