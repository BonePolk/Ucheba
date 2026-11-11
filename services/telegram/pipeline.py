import asyncio
from fastapi import HTTPException
from sqlmodel import Session
from common.database import get_engine
from common.settings import read_settings, save_settings
from common.models.integration import TelegramChat
import client as telegram

from collections import defaultdict
from common.identity import user_id

locks = defaultdict(asyncio.Lock)


def analysis_lock():
    return locks[user_id()]


async def select_chats(chat_ids):
    async with analysis_lock():
        available = {d['id']: d for d in await telegram.dialogs()}
        if any(chat_id not in available for chat_id in chat_ids):
            raise HTTPException(422, 'Выберите чат из списка Telegram')
        current = await telegram.get_client()
        old, _ = read_settings('telegram')
        for chat_id in chat_ids:
            if chat_id not in old['chat_ids']:
                latest = await current.get_messages(chat_id, limit=1)
                with Session(get_engine()) as session:
                    chat = session.get(TelegramChat, chat_id) or TelegramChat(id=chat_id, title=available[chat_id]['title'])
                    chat.title = available[chat_id]['title']
                    chat.username = available[chat_id]['username']
                    chat.last_message_id = latest[0].id if latest else 0
                    chat.context = ''
                    session.add(chat)
                    session.commit()
        save_settings('telegram', config={'chat_ids': chat_ids})
