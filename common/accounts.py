import os

from fastapi import HTTPException
from pwdlib import PasswordHash
from sqlmodel import Session, select

from common.database import engine
from common.identity import current_user_id, user_id
from common.models.user import Feature, User

passwords = PasswordHash.recommended()
FEATURES = {'telegram', 'gemini', 'codex', 'jev', 'context', 'ameba', 'straus', 'collector', 'notifications', 'registration'}


def bootstrap():
    with Session(engine) as session:
        if session.exec(select(User.id)).first() is None:
            password = os.getenv('ADMIN_PASSWORD', '')
            if len(password) < 16:
                raise RuntimeError('Для первоначального аккаунта admin задайте ADMIN_PASSWORD не короче 16 символов')
            session.add(User(id=1, username='admin', password_hash=passwords.hash(password), is_admin=True))
            session.commit()
    # Перенос старого ключа Lucheba только владельцу, с шифрованием в его настройках.
    from common.settings import save_settings
    from common.models.integration import IntegrationSetting
    token = current_user_id.set(1)
    try:
        with Session(engine) as session:
            context_exists = session.get(IntegrationSetting, 'context') is not None
        if not context_exists and os.getenv('LUNA_API_KEY'):
            save_settings('context', secrets={'api_key': os.environ['LUNA_API_KEY']})
    finally:
        current_user_id.reset(token)


def users():
    with Session(engine) as session:
        return session.exec(select(User.id)).all()


def account(owner=None):
    with Session(engine) as session:
        row = session.get(User, owner if owner is not None else user_id())
        if not row:
            raise HTTPException(401, 'Аккаунт не найден')
        return row


def public_user(row):
    return {'id': row.id, 'username': row.username, 'is_admin': row.is_admin}


def features():
    with Session(engine) as session:
        saved = {item.name: item.enabled for item in session.exec(select(Feature)).all()}
    previous_names = {'ameba': 'jev', 'straus': 'context'}
    return {name: saved.get(name, saved.get(previous_names.get(name, ''), True)) for name in sorted(FEATURES)}


def check_feature(name):
    if name not in FEATURES or not features()[name]:
        raise HTTPException(403, 'Эта функция отключена администратором')


def claim_bot(name, token):
    import hashlib
    from sqlalchemy.exc import IntegrityError
    from common.models.user import BotTokenClaim
    owner = user_id()
    hashed = hashlib.sha256(token.encode()).hexdigest() if token else None
    with Session(engine) as session:
        existing = session.get(BotTokenClaim, hashed) if hashed else None
        if existing and (existing.user_id != owner or existing.provider != name):
            raise HTTPException(409, 'Этот бот уже подключён к другому аккаунту или используется для другой функции')
        for row in session.exec(select(BotTokenClaim).where(
            BotTokenClaim.user_id == owner, BotTokenClaim.provider == name)).all():
            if row.token_hash != hashed:
                session.delete(row)
        if hashed and not existing:
            session.add(BotTokenClaim(token_hash=hashed, user_id=owner, provider=name))
        try:
            session.commit()
        except IntegrityError:
            raise HTTPException(409, 'Этот бот уже подключён') from None
