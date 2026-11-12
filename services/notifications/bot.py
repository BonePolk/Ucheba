import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlmodel import Session, select

from common.database import get_engine
from common.settings import read_settings, report
from common.models.integration import ArrodesRun, NotificationSent, Suggestion
from common.models.task import Task


def dispatcher():
    instance, router = Dispatcher(), Router()

    @router.message(CommandStart())
    async def start(message: Message):
        config, _ = read_settings('notifications')
        if message.chat.type == 'private' and message.from_user.id == config['owner_id']:
            await message.answer('Уведомления задач подключены. Настройки меняются в профиле.')

    instance.include_router(router)
    return instance


async def send_notifications(bot):
    config, _ = read_settings('notifications')
    owner = config['owner_id']
    if not owner:
        return
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as session:
        events = []
        for task in session.exec(select(Task).where(Task.archived == False, Task.status != 'done',
                Task.deadline.is_not(None), Task.deadline <= now + timedelta(minutes=config['lead_minutes']))).all():
            deadline = task.deadline if task.deadline.tzinfo else task.deadline.replace(tzinfo=timezone.utc)
            kind = 'overdue' if deadline <= now else 'soon'
            key = f'{owner}:task:{task.id}:{deadline.isoformat()}:{kind}'
            label = 'Дедлайн просрочен' if kind == 'overdue' else 'Приближается дедлайн'
            events.append((key, f'{label}: {task.title}\nСрок: {deadline.isoformat()}'))
        for item in session.exec(select(Suggestion).where(Suggestion.status == 'pending')).all():
            events.append((f'{owner}:suggestion:{item.id}', f'Новая задача на подтверждение: {item.title}\n{item.reason}'))
        for run in session.exec(select(ArrodesRun).where(ArrodesRun.status == 'succeeded')).all():
            events.append((f'{owner}:codex:{run.id}', f'Arrodes подготовил материалы для задачи #{run.task_id}. Результат ожидает проверки.'))
        unsent = [(key, text) for key, text in events if not session.get(NotificationSent, key)][:5]
    for key, text in unsent:
        await bot.send_message(owner, text[:3500])
        with Session(get_engine()) as session:
            session.add(NotificationSent(key=key))
            session.commit()
    if unsent:
        report('notifications', success=True)


async def maintain(bot):
    while True:
        try:
            await send_notifications(bot)
        except Exception:
            report('notifications', error='Не удалось доставить уведомления. Проверьте Owner ID, /start и разрешения бота')
        await asyncio.sleep(30)
