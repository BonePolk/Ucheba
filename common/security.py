import os
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

attempts = defaultdict(deque)


def cipher():
    key = os.getenv('INTEGRATION_ENCRYPTION_KEY', '')
    if not key:
        raise HTTPException(503, 'Задайте INTEGRATION_ENCRYPTION_KEY в .env')
    try:
        return Fernet(key.encode())
    except ValueError:
        raise HTTPException(503, 'INTEGRATION_ENCRYPTION_KEY должен быть ключом Fernet') from None


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ''
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise HTTPException(503, 'Не удалось расшифровать настройки: восстановите исходный ключ') from None


def signer():
    cipher()
    return URLSafeTimedSerializer(os.environ['INTEGRATION_ENCRYPTION_KEY'], salt='user-session-v1')


def session_user(request: Request):
    token = request.cookies.get('user_session', '')
    try:
        session = signer().loads(token, max_age=43200)
        if not isinstance(session, dict) or not isinstance(session.get('id'), int) or session['id'] < 1:
            raise BadSignature('invalid')
    except (BadSignature, SignatureExpired):
        return None
    from common.accounts import account
    row = account(session['id'])
    return row if row.session_version == session.get('version') else None


def require_user(request: Request):
    if not getattr(request.state, 'user', None):
        raise HTTPException(401, 'Войдите в аккаунт')
    return request.state.user


def require_owner(request: Request):
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(403, 'Требуется доступ администратора')
    return user


def throttle(request: Request):
    key = request.headers.get('x-real-ip', request.client.host)
    now = time.monotonic()
    recent = attempts[key]
    while recent and recent[0] < now - 60:
        recent.popleft()
    if len(recent) >= 10:
        raise HTTPException(429, 'Слишком много попыток. Подождите минуту')
    recent.append(now)
