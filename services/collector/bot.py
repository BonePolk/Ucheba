import asyncio
import json

from aiogram import Dispatcher, Router
from aiogram.types import Message
from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import engine
from common.security import decrypt, encrypt
from common.settings import read_settings, report
from common.models.integration import BotChat, CollectedMessage, TelegramChat
from common.extraction import process_batch

analysis_lock = asyncio.Lock()


async def collect(message: Message):
    config, _ = read_settings('collector')
    if not config['enabled']:
        return
    overflow = False
    with Session(engine) as session:
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


async def analyze():
    if analysis_lock.locked():
        raise HTTPException(409, 'Анализ уже выполняется')
    async with analysis_lock:
        config, _ = read_settings('collector')
        gemini, _ = read_settings('gemini')
        if not gemini['enabled']:
            raise HTTPException(400, 'Включите Gemini')
        if not config['chat_ids']:
            raise HTTPException(400, 'Выберите разрешённые чаты бота')
        owner = {'id': read_settings('notifications')[0]['owner_id'], 'context': gemini['owner_context']}
        counts = {'messages': 0, 'created': 0, 'pending': 0}
        for chat_id in config['chat_ids']:
            with Session(engine) as session:
                meta = session.get(BotChat, chat_id)
                if not meta:
                    continue
                chat = TelegramChat(id=chat_id, title=meta.title, username=meta.username)
                messages = session.exec(select(CollectedMessage).where(CollectedMessage.chat_id == chat_id)
                    .order_by(CollectedMessage.message_id).limit(30)).all()
                messages = [m.model_copy() for m in messages]
            if not messages:
                continue
            result = await process_batch(chat, [json.loads(decrypt(m.payload)) for m in messages],
                                         [m.message_id for m in messages], owner)
            with Session(engine) as session:
                for message in messages:
                    stored = session.get(CollectedMessage, (chat_id, message.message_id))
                    if stored:
                        session.delete(stored)
                session.commit()
            counts = {key: counts[key] + result[key] for key in counts}
        report('collector', success=True)
        report('gemini', success=True)
        return counts


async def maintain(instance):
    while True:
        config, _ = read_settings('gemini')
        if config['enabled']:
            try:
                await analyze()
            except Exception:
                report('collector', error='Не удалось обработать сообщения. Проверьте Gemini и разрешённые чаты')
        await asyncio.sleep(config['interval_seconds'])
