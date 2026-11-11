import asyncio
import json

from aiogram import Dispatcher, Router
from aiogram.types import Message
from sqlmodel import Session, select

from common.database import get_engine
from common.security import encrypt
from common.settings import read_settings, report
from common.models.integration import BotChat, CollectedMessage

from collections import defaultdict
from common.identity import user_id

locks = defaultdict(asyncio.Lock)


def analysis_lock():
    return locks[user_id()]


async def collect(message: Message):
    config, _ = read_settings('collector')
    if not config['enabled']:
        return
    overflow = False
    with Session(get_engine()) as session:
        chat = session.get(BotChat, message.chat.id) or BotChat(id=message.chat.id, title='')
        chat.title = message.chat.title or message.chat.full_name or str(message.chat.id)
        chat.username = message.chat.username
        session.add(chat)
        text = message.text or message.caption
        if message.chat.id in config['chat_ids'] and text and not session.get(CollectedMessage, (message.chat.id, message.message_id)):
            payload = {'id': message.message_id, 'date': message.date.isoformat(),
                'sender_id': message.from_user.id if message.from_user else None,
                'reply_to': message.reply_to_message.message_id if message.reply_to_message else None,
                'text': text[:3000]}
            session.add(CollectedMessage(chat_id=message.chat.id, message_id=message.message_id,
                                         payload=encrypt(json.dumps(payload, ensure_ascii=False))))
            older = session.exec(select(CollectedMessage).where(CollectedMessage.chat_id == message.chat.id)
                .order_by(CollectedMessage.message_id.desc()).offset(500)).all()
            for stored in older:
                session.delete(stored)
            overflow = bool(older)
        session.commit()
    if overflow:
        report('collector', error='Очередь переполнена: сохранены последние 500 сообщений чата')


def dispatcher():
    instance, router = Dispatcher(), Router()
    router.message.register(collect)
    router.channel_post.register(collect)
    instance.include_router(router)
    return instance
