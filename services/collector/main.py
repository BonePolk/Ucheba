import asyncio
from contextlib import asynccontextmanager

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from common.bot_runtime import check_bot, poll
from common.database import get_engine
from common.http import private_app
from common.runtime import for_users
from common.models.integration import BotChat, CollectedMessage
from common.settings import save_settings
import bot


@asynccontextmanager
async def lifespan(app):
    background = asyncio.create_task(for_users(lambda: poll('collector', bot.dispatcher, bot.maintain)))
    try:
        yield
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)


app = private_app('collector', lifespan)


@app.post('/check')
async def check():
    return await check_bot('collector')


@app.get('/chats')
async def chats():
    with Session(get_engine()) as session:
        return session.exec(select(BotChat)).all()


class Selection(BaseModel):
    chat_ids: list[int] = Field(max_length=30)


@app.post('/chats')
async def select_chats(body: Selection):
    async with bot.analysis_lock():
        with Session(get_engine()) as session:
            available = {chat.id for chat in session.exec(select(BotChat)).all()}
            if set(body.chat_ids) - available:
                raise HTTPException(422, 'Выберите известные боту чаты')
            for message in session.exec(select(CollectedMessage).where(CollectedMessage.chat_id.not_in(body.chat_ids))).all():
                session.delete(message)
            session.commit()
        save_settings('collector', config={'chat_ids': list(set(body.chat_ids))})
    return {'ok': True}


@app.post('/analyze')
async def analyze():
    return await bot.analyze()
