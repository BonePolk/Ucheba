import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlmodel import Session
from common.database import engine
from common.settings import read_settings, report, save_settings
from common.models.integration import TelegramChat
from common.extraction import process_batch
import client as telegram

analysis_lock = asyncio.Lock()


async def select_chats(chat_ids):
    async with analysis_lock:
        available = {d['id']: d for d in await telegram.dialogs()}
        if any(chat_id not in available for chat_id in chat_ids):
            raise HTTPException(422, 'Выберите чат из списка Telegram')
        current = await telegram.get_client()
        old, _ = read_settings('telegram')
        for chat_id in chat_ids:
            if chat_id not in old['chat_ids']:
                latest = await current.get_messages(chat_id, limit=1)
                with Session(engine) as session:
                    chat = session.get(TelegramChat, chat_id) or TelegramChat(id=chat_id, title=available[chat_id]['title'])
                    chat.title = available[chat_id]['title']
                    chat.username = available[chat_id]['username']
                    chat.last_message_id = latest[0].id if latest else 0
                    chat.context = ''
                    session.add(chat)
                    session.commit()
        save_settings('telegram', config={'chat_ids': chat_ids})


async def process_messages(manual=False):
    if analysis_lock.locked():
        raise HTTPException(409, 'Анализ уже выполняется')
    async with analysis_lock:
        tg, _ = read_settings('telegram')
        config, secrets = read_settings('gemini')
        if not config['enabled'] or not secrets.get('api_key'):
            raise HTTPException(400, 'Включите Gemini и сохраните API-ключ')
        if not tg['chat_ids']:
            raise HTTPException(400, 'Выберите чаты для анализа')
        current = await telegram.get_client()
        if not telegram.connected:
            raise HTTPException(401, 'Подключите Telegram заново')
        await current.get_dialogs(limit=300)
        me = await current.get_me()
        owner = {'id': me.id, 'username': me.username, 'name': me.first_name}
        counts = {'messages': 0, 'created': 0, 'pending': 0}
        for chat_id in tg['chat_ids']:
            with Session(engine) as session:
                chat = session.get(TelegramChat, chat_id)
                if not chat:
                    continue
                chat = chat.model_copy()
            if manual:
                batch = list(reversed(await current.get_messages(chat_id, limit=50)))
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                batch = [m for m in batch if m.date >= cutoff]
            else:
                batch = [m async for m in current.iter_messages(chat_id, min_id=chat.last_message_id, reverse=True, limit=30)]
            messages = [{'id': m.id, 'date': m.date.isoformat(), 'sender_id': m.sender_id,
                         'reply_to': m.reply_to_msg_id, 'text': m.raw_text[:3000]} for m in batch if m.raw_text]
            result = await process_batch(chat, messages, [m.id for m in batch], owner)
            counts = {key: counts[key] + result[key] for key in counts}
        report('telegram', success=True)
        report('gemini', success=True)
        return counts
