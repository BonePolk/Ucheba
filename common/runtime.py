import asyncio

from common.accounts import users
from common.identity import current_user_id


async def for_users(worker):
    running = {}

    async def run(owner):
        token = current_user_id.set(owner)
        try:
            await worker()
        finally:
            current_user_id.reset(token)

    try:
        while True:
            for owner in users():
                task = running.get(owner)
                if task is None or task.done():
                    if task:
                        task.exception()
                    running[owner] = asyncio.create_task(run(owner))
            await asyncio.sleep(5)
    finally:
        for task in running.values():
            task.cancel()
        await asyncio.gather(*running.values(), return_exceptions=True)
