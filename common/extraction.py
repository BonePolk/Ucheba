import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from common.database import engine
from common.security import decrypt, encrypt
from common import gemini
from common.settings import read_settings
from common.models.integration import ProcessedMessage, Suggestion, TaskAction, TelegramChat
from common.models.task import Task
from common.dates import normalize_utc


def source_url(chat, message_id):
    if chat.id < 0 and chat.username:
        return f'https://t.me/{chat.username}/{message_id}'
    if chat.id < -1000000000000:
        return f'https://t.me/c/{-chat.id - 1000000000000}/{message_id}'
    return None


def apply_suggestion(session, suggestion, values=None):
    if suggestion.status != 'pending':
        raise HTTPException(409, 'Предложение уже обработано')
    fields = values or {'title': suggestion.title, 'description': suggestion.description,
                        'deadline': suggestion.deadline}
    if suggestion.action == 'create':
        task = Task(**fields, source_chat_id=suggestion.source_chat_id, source_url=suggestion.source_url)
    else:
        task = session.get(Task, suggestion.target_task_id) if suggestion.target_task_id else None
        if not task:
            raise HTTPException(409, 'Выберите существующую задачу для изменения или отмены')
        if suggestion.action == 'cancel':
            task.archived = True
        else:
            task.sqlmodel_update(fields)
            task.ai_score = task.ai_details = task.ai_scored_at = None
        task.updated_at = datetime.now(timezone.utc)
    session.add(task)
    session.flush()
    suggestion.task_id = task.id
    suggestion.status = 'accepted'
    session.add(suggestion)
    session.add(TaskAction(task_id=task.id, action=f'gemini_{suggestion.action}'))
    return task


async def process_batch(chat, messages, message_ids, owner):
    config, _ = read_settings('gemini')
    with Session(engine) as session:
        unseen = {mid for mid in message_ids if not session.get(ProcessedMessage, (chat.id, mid))}
        messages = [m for m in messages if m['id'] in unseen]
        if not unseen:
            return {'messages': 0, 'created': 0, 'pending': 0}
        stored = session.get(TelegramChat, chat.id)
        context = json.loads(decrypt(stored.context) or '[]') if stored else []
        existing = [t.model_dump(include={'id', 'title', 'description', 'deadline'}) for t in
                    session.exec(select(Task).where(Task.archived == False).order_by(Task.id.desc()).limit(100)).all()]
        previous = session.exec(select(Suggestion).where(Suggestion.source_chat_id == chat.id)
                                .order_by(Suggestion.id.desc()).limit(50)).all()
        existing += [{'title': s.title, 'action': s.action, 'decision': s.status,
                      'target_task_id': s.target_task_id} for s in previous]
    analysis = await gemini.analyze(messages, context, existing, chat.title, owner) if messages else None
    if analysis and any(t.source_message_id not in {m['id'] for m in messages} for t in analysis.tasks):
        raise HTTPException(502, 'Gemini вернул неизвестный источник; пакет не отмечен обработанным')
    counts = {'messages': len(unseen), 'created': 0, 'pending': 0}
    with Session(engine) as session:
        for found in analysis.tasks if analysis else []:
            key = hashlib.sha256(f'{chat.id}:{found.source_message_id}:{found.action}:{found.target_task_id}:{found.title.casefold()}'.encode()).hexdigest()
            if session.exec(select(Suggestion).where(Suggestion.dedup_key == key)).first():
                continue
            if found.action == 'create' and session.exec(select(Suggestion).where(
                    Suggestion.source_chat_id == chat.id, Suggestion.action == 'create',
                    Suggestion.title == found.title)).first():
                continue
            if found.deadline and found.deadline.tzinfo is None:
                found.ambiguous = True
                found.reason += ' Часовой пояс дедлайна не определён.'
            proposal = Suggestion(dedup_key=key, title=found.title, description=found.description,
                deadline=normalize_utc(found.deadline), action=found.action,
                target_task_id=found.target_task_id, confidence=found.confidence,
                reason=found.reason, assignee=found.assignee,
                source_chat_id=chat.id, source_message_id=found.source_message_id,
                source_title=chat.title, source_url=source_url(chat, found.source_message_id))
            session.add(proposal)
            if (found.action == 'create' and found.confidence >= config['confidence_threshold']
                    and found.addressed_to_owner and not found.ambiguous):
                apply_suggestion(session, proposal)
                counts['created'] += 1
            else:
                counts['pending'] += 1
        for message_id in unseen:
            session.add(ProcessedMessage(chat_id=chat.id, message_id=message_id))
        stored = session.get(TelegramChat, chat.id) or chat
        stored.last_message_id = max(stored.last_message_id, max(unseen))
        stored.context = encrypt(json.dumps((context + messages)[-10:], ensure_ascii=False))
        session.add(stored)
        session.commit()
    return counts


