"""Одна Lucheba на аккаунт; Jeba ждёт освобождения всех Lucheba."""
import asyncio
from contextlib import asynccontextmanager

from common.identity import user_id

condition = asyncio.Condition()
waiting = []
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
                return not active_users and not any(item['provider'] == 'context' for item in waiting)

            await condition.wait_for(available)
        finally:
            # Удаляем именно этот запрос, даже если в очереди есть такой же.
            waiting[:] = [item for item in waiting if item is not entry]
            condition.notify_all()
        if provider == 'context':
            active_users.add(owner)
        else:
            jeba_running = True
    try:
        yield
    finally:
        async with condition:
            if provider == 'context':
                active_users.discard(owner)
            else:
                jeba_running = False
            condition.notify_all()


def queued(provider, task_id):
    owner = user_id()
    return any(item['owner'] == owner and item['provider'] == provider
               and item['task_id'] == task_id for item in waiting)
