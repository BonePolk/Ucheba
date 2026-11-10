"""Одна Lucheba на аккаунт; Jeba ждёт освобождения всех Lucheba."""
import asyncio
from contextlib import asynccontextmanager

from common.identity import user_id

condition = asyncio.Condition()
waiting = []
running = []
planned = {}
active_users = set()
jeba_running = False


@asynccontextmanager
async def slot(provider, task_id=0):
    global jeba_running
    owner = user_id()
    entry = {'owner': owner, 'provider': provider, 'task_id': task_id}
    async with condition:
        waiting.append(entry)
        try:
            def available():
                if jeba_running:
                    return False
                if provider == 'context':
                    # FIFO внутри аккаунта; разные аккаунты могут работать одновременно.
                    first = next(item for item in waiting
                                 if item['provider'] == 'context' and item['owner'] == owner)
                    return owner not in active_users and first is entry
                return not active_users and not any(planned.values()) and not any(item['provider'] == 'context' for item in waiting)

            await condition.wait_for(available)
        finally:
            # Удаляем именно этот запрос, даже если в очереди есть такой же.
            waiting[:] = [item for item in waiting if item is not entry]
            condition.notify_all()
        if provider == 'context':
            active_users.add(owner)
            if owner in planned:
                planned[owner] = [task_id for task_id in planned[owner] if task_id != entry['task_id']]
        else:
            jeba_running = True
        running.append(entry)
    try:
        yield
    finally:
        async with condition:
            running[:] = [item for item in running if item is not entry]
            if provider == 'context':
                active_users.discard(owner)
            else:
                jeba_running = False
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
            'busy': bool(active_users or jeba_running or waiting or any(planned.values()))}


def queued(provider, task_id):
    owner = user_id()
    return any(item['owner'] == owner and item['provider'] == provider
               and item['task_id'] == task_id for item in waiting)
