"""Monocle, Straus и Arrodes работают параллельно и делят браузер по очереди, а Ameba ждёт завершения всех агентов."""
import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import HTTPException

from common.identity import user_id
from common.rpc import call

WORK_PROVIDERS = frozenset({'monocle', 'straus', 'arrodes'})

condition = asyncio.Condition()
waiting = []
running = []
planned = {}
active_users = set()
ameba_users = set()
ranking_requests = {}


def _normalize_provider(provider, task_id=0):
    if provider == 'context':
        return 'monocle' if task_id else 'straus'
    if provider == 'codex':
        return 'arrodes'
    return provider


def _slot_key(provider, task_id=0):
    norm = _normalize_provider(provider, task_id)
    if norm == 'monocle' and task_id:
        return ('monocle', task_id)
    return (norm,)


def _active_work(owner):
    return [item for item in running if item['owner'] == owner and item['provider'] in WORK_PROVIDERS]


def _waiting_work(owner):
    return [item for item in waiting if item['owner'] == owner and item['provider'] in WORK_PROVIDERS]


def work_busy(owner=None):
    target = user_id() if owner is None else owner
    return bool(_active_work(target) or _waiting_work(target) or planned.get(target))


async def interrupt_ranking(own_only=False):
    # Прерываем только Ameba владельца запроса. Чужие очереди независимы.
    request = ranking_requests.get(user_id())
    if request:
        # Отменяем только запрос Ameba, не фоновый worker целиком.
        if not request['task'].cancelling():
            request['task'].cancel()
        await asyncio.gather(request['task'], return_exceptions=True)


async def run_ranking(body, endpoint='/rank'):
    from common import calendar
    run_id = uuid4().hex
    calendar.reset_ameba_plan(run_id)
    revision = calendar.read_config()['revision']

    async def request():
        try:
            return await call('ameba', endpoint, {key: value for key, value in body.items() if key != 'input_signature'} | {'run_id': run_id}, timeout=None)
        except asyncio.CancelledError:
            # Сначала останавливаем модель в отдельном сервисе, затем отдаём очередь.
            await call('ameba', '/cancel', {'run_id': run_id}, timeout=None)
            raise

    task = asyncio.create_task(request())
    entry = {'owner': user_id(), 'task': task, 'signature': body.get('input_signature')}
    ranking_requests[user_id()] = entry
    try:
        result = await task
        return result | {'plan_run_id': run_id, 'calendar_revision': revision}
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
    norm = _normalize_provider(provider, task_id)
    key = _slot_key(norm, task_id)
    entry = {'owner': owner, 'provider': norm, 'task_id': task_id}
    async with condition:
        waiting.append(entry)
    try:
        if norm in WORK_PROVIDERS:
            await interrupt_ranking()
        async with condition:
            def available():
                if owner in ameba_users:
                    return False
                if norm in WORK_PROVIDERS:
                    same_running = any(
                        item['owner'] == owner and _slot_key(item['provider'], item['task_id']) == key
                        for item in running
                    )
                    same_waiting = [
                        item for item in waiting
                        if item['owner'] == owner and _slot_key(item['provider'], item['task_id']) == key
                    ]
                    return not same_running and bool(same_waiting) and same_waiting[0] is entry
                if _active_work(owner) or _waiting_work(owner) or planned.get(owner):
                    return False
                ranking_waiting = [item for item in waiting if item['owner'] == owner and item['provider'] not in WORK_PROVIDERS]
                return bool(ranking_waiting) and ranking_waiting[0] is entry

            await condition.wait_for(available)
            waiting.remove(entry)
            if norm in WORK_PROVIDERS:
                active_users.add(owner)
                if norm == 'monocle' and owner in planned:
                    planned[owner] = [value for value in planned[owner] if value != task_id]
            else:
                ameba_users.add(owner)
            running.append(entry)
            condition.notify_all()
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
            if norm in WORK_PROVIDERS:
                if not _active_work(owner):
                    active_users.discard(owner)
            else:
                ameba_users.discard(owner)
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
    visible = {item['task_id'] for item in active + pending if item['provider'] == 'monocle'}
    pending.extend({'owner': owner, 'provider': 'monocle', 'task_id': task_id}
                   for task_id in planned.get(owner, []) if task_id not in visible)
    agents_busy = work_busy(owner)
    return {
        'active': active,
        'waiting': pending,
        'agents_busy': agents_busy,
        'browser_busy': agents_busy,
        'ameba_running': any(item['provider'] == 'ameba' for item in active),
        'ameba_waiting': any(item['provider'] == 'ameba' for item in pending),
        'busy': bool(active or pending),
    }


def queued(provider, task_id):
    owner = user_id()
    norm = _normalize_provider(provider, task_id)
    return any(item['owner'] == owner and item['provider'] == norm
               and item['task_id'] == task_id for item in waiting)
