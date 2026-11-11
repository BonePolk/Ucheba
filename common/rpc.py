import hashlib
import hmac
import logging
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
    if path not in ('/close', '/disconnect', '/cancel'):
        if service == 'context' and path.startswith('/browser/'):
            from common.accounts import features
            if not (features()['context'] or features()['straus']):
                check_feature('context')
        else:
            check_feature(service)
    async with httpx.AsyncClient(timeout=timeout) as client:
        host = 'lucheba' if service == 'context' else service
        try:
            response = await client.request(method, f'http://{host}:8000{path}',
                headers={'X-Service-Token': internal_token(service), 'X-User-ID': str(user_id())}, json=jsonable_encoder(body))
        except httpx.HTTPError as exc:
            # Не выводим тело запроса: в нём могут быть ключи и личные данные.
            logging.getLogger('uvicorn.error').warning('%s: %s', host, type(exc).__name__)
            label = {'straus': 'Straus', 'context': 'Lucheba', 'ameba': 'Ameba'}.get(service, service)
            if isinstance(exc, httpx.TimeoutException):
                detail = f'{label}: истекло время ожидания сервиса. Повторите запрос.'
            elif isinstance(exc, httpx.ConnectError):
                detail = f'{label}: не удалось подключиться к сервису. Возможно, он перезапускается.'
            else:
                detail = f'{label}: соединение с сервисом оборвалось. Повторите запрос.'
            raise HTTPException(502, detail) from None
        if not response.is_success:
            try:
                detail = response.json()['detail']
            except (ValueError, KeyError):
                detail = f'{service}: ошибка сервиса HTTP {response.status_code}'
            raise HTTPException(response.status_code, detail)
        return response.json()
