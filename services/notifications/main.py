import asyncio
from contextlib import asynccontextmanager

from common.bot_runtime import check_bot, poll
from common.http import private_app
from common.runtime import for_users
import bot


@asynccontextmanager
async def lifespan(app):
    background = asyncio.create_task(for_users(lambda: poll('notifications', bot.dispatcher, bot.maintain)))
    try:
        yield
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)


app = private_app('notifications', lifespan)


@app.post('/check')
async def check():
    return await check_bot('notifications')
