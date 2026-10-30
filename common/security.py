import hashlib
import hmac
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
    password = os.getenv('ADMIN_PASSWORD', '')
    if len(password) < 16:
        raise HTTPException(503, 'Задайте ADMIN_PASSWORD длиной не менее 16 символов в .env')
    key = os.environ['INTEGRATION_ENCRYPTION_KEY'] + password
    return URLSafeTimedSerializer(hashlib.sha256(key.encode()).digest(), salt='owner')


def require_owner(request: Request):
    token = request.cookies.get('owner_session', '')
    try:
        if signer().loads(token, max_age=43200) != 'owner':
            raise BadSignature('invalid')
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, 'Войдите как владелец') from None


def login(password: str, request: Request):
    serializer = signer()
    key = request.client.host
    now = time.monotonic()
    recent = attempts[key]
    while recent and recent[0] < now - 60:
        recent.popleft()
    if len(recent) >= 5:
        raise HTTPException(429, 'Слишком много попыток. Подождите минуту')
    recent.append(now)
    if not hmac.compare_digest(password.encode(), os.environ['ADMIN_PASSWORD'].encode()):
        raise HTTPException(401, 'Неверный пароль')
    recent.clear()
    return serializer.dumps('owner')
