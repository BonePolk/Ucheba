import hashlib
import hmac
import os

import httpx
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from common.identity import user_id


def internal_token(service, owner=None):
    secret = os.getenv('INTERNAL_SECRET', '')
    if not secret:
        raise HTTPException(503, 'Настройте ключ шифрования интеграций в .env')
    owner = user_id() if owner is None else owner
    return hmac.new(secret.encode(), f'{service}:{owner}'.encode(), hashlib.sha256).hexdigest()


def require_service(request: Request, service):
    raw = request.headers.get('x-user-id', '')
    if not raw.isdecimal() or not 0 < int(raw) < 2**31:
        raise HTTPException(403, 'Недопустимый пользователь сервиса')
    owner = int(raw)
    if not hmac.compare_digest(request.headers.get('x-service-token', ''), internal_token(service, owner)):
        raise HTTPException(403, 'Недопустимый внутренний запрос')
    return owner


async def call(service, path, body=None, method='POST', timeout=90):
    from common.accounts import check_feature
    if path not in ('/close', '/disconnect'):
        check_feature(service)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, f'http://{service}:8000{path}',
            headers={'X-Service-Token': internal_token(service), 'X-User-ID': str(user_id())}, json=jsonable_encoder(body))
        if not response.is_success:
            try:
                detail = response.json()['detail']
            except (ValueError, KeyError):
                detail = f'{service}: ошибка сервиса HTTP {response.status_code}'
            raise HTTPException(response.status_code, detail)
        return response.json()
