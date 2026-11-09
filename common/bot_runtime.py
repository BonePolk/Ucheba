import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from fastapi import HTTPException

from common.settings import read_settings, report
from common.accounts import claim_bot


async def check_bot(name):
    _, secret = read_settings(name)
    if not secret.get('bot_token'):
        raise HTTPException(400, 'Сохраните токен из BotFather')
    async with Bot(secret['bot_token']) as bot:
        me = await bot.get_me()
        report(name, success=True)
        return {'ok': True, 'username': me.username}


async def poll(name, dispatcher_factory, maintenance):
    while True:
        config, private = read_settings(name)
        token = private.get('bot_token')
        if not config['enabled'] or not token:
            await asyncio.sleep(5)
            continue
        polling = upkeep = bot = None
        try:
            claim_bot(name, token)
            bot = Bot(token)
            await bot.get_me()
            report(name, success=True)
            dispatcher = dispatcher_factory()
            polling = asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False,
                close_bot_session=False, handle_as_tasks=False, allowed_updates=dispatcher.resolve_used_update_types()))
            upkeep = asyncio.create_task(maintenance(bot))
            while not polling.done() and not upkeep.done():
                await asyncio.sleep(5)
                updated, secret = read_settings(name)
                if not updated['enabled'] or secret.get('bot_token') != token:
                    break
        except (TelegramAPIError, ValueError, OSError, HTTPException):
            report(name, error='Бот недоступен: проверьте токен, webhook и запуск другой копии бота')
        finally:
            for task in (polling, upkeep):
                if task:
                    task.cancel()
            await asyncio.gather(*(t for t in (polling, upkeep) if t), return_exceptions=True)
            if bot:
                await bot.session.close()
        await asyncio.sleep(10)
