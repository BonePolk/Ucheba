import asyncio

from fastapi import HTTPException
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.utils import get_peer_id

from common.settings import read_settings, report, save_settings

client = None
phone_code_hash = None
connected = False
lock = asyncio.Lock()


async def get_client():
    global client, connected
    if client is None:
        _, secret = read_settings('telegram')
        if not secret.get('api_id') or not secret.get('api_hash'):
            raise HTTPException(400, 'Сначала сохраните API ID и API Hash Telegram')
        client = TelegramClient(StringSession(secret.get('session', '')),
                                int(secret['api_id']), secret['api_hash'],
                                connection_retries=1, request_retries=1, flood_sleep_threshold=0)
    if not client.is_connected():
        await asyncio.wait_for(client.connect(), 20)
    connected = await client.is_user_authorized()
    return client


async def send_code():
    global phone_code_hash
    async with lock:
        current = await get_client()
        if connected:
            raise HTTPException(409, 'Аккаунт уже подключён')
        _, secret = read_settings('telegram')
        if not secret.get('phone'):
            raise HTTPException(400, 'Укажите телефон')
        sent = await current.send_code_request(secret['phone'])
        phone_code_hash = sent.phone_code_hash
        return {'step': 'code'}


async def sign_in(code=None, password=None):
    global connected, phone_code_hash
    async with lock:
        current = await get_client()
        _, secret = read_settings('telegram')
        try:
            if password:
                await current.sign_in(password=password)
            else:
                if not phone_code_hash:
                    raise HTTPException(400, 'Сначала запросите новый код')
                await current.sign_in(phone=secret['phone'], code=code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            return {'step': 'password'}
        save_settings('telegram', secrets={'session': current.session.save()})
        connected = True
        phone_code_hash = None
        report('telegram', success=True)
        return {'step': 'connected'}


async def dialogs():
    async with lock:
        current = await get_client()
        if not connected:
            raise HTTPException(401, 'Подключите Telegram заново')
        return [{'id': get_peer_id(dialog.entity), 'title': dialog.name or str(dialog.id),
                 'username': getattr(dialog.entity, 'username', None)}
                async for dialog in current.iter_dialogs(limit=300)]


async def close():
    global client, connected
    if client:
        await client.disconnect()
    client = None
    connected = False


async def disconnect():
    global phone_code_hash
    async with lock:
        if client:
            try:
                await client.log_out()
            except errors.UnauthorizedError:
                pass
        await close()
        phone_code_hash = None
        save_settings('telegram', config={'enabled': False, 'chat_ids': []}, secrets={'session': ''})


def error_message(exc):
    global connected
    if isinstance(exc, errors.FloodWaitError):
        return f'Telegram: повторите через {exc.seconds} секунд'
    if isinstance(exc, errors.RPCError):
        if exc.code == 401:
            connected = False
            return 'Telegram: авторизация истекла, подключите аккаунт заново'
        return f'Telegram: {type(exc).__name__}'
    return 'Telegram: соединение недоступно'
