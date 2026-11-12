import asyncio
import hashlib
import hmac
import logging
import os

import httpx
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from common.identity import user_id


def service_name(name):
    return {'context': 'monocle', 'lucheba': 'monocle', 'codex': 'arrodes'}.get(name, name)


def internal_token(service, owner=None):
    # Preserve service signatures for already deployed clients.
    service = {'monocle': 'context', 'arrodes': 'codex'}.get(service_name(service), service)
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
    service = service_name(service)
    if path not in ('/close', '/disconnect', '/cancel'):
        if service == 'monocle' and path.startswith(('/browser/', '/workspace')):
            from common.accounts import features
            enabled = features()
            if not (enabled['monocle'] or enabled['straus'] or enabled['arrodes']):
                check_feature('monocle')
        else:
            check_feature(service)
    async with httpx.AsyncClient(timeout=timeout, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
        host = service
        try:
            # Чтение списка безопасно повторить, если Watch ещё перезапускает сервис.
            # Запуск агентов и остальные операции здесь не повторяем.
            attempts = 3 if path == '/models' else 1
            for attempt in range(attempts):
                try:
                    response = await client.request(method, f'http://{host}:8000{path}',
                        headers={'X-Service-Token': internal_token(service), 'X-User-ID': str(user_id())}, json=jsonable_encoder(body))
                    break
                except httpx.ConnectError:
                    if attempt == attempts - 1:
                        raise
                    await asyncio.sleep(attempt + 1)
        except httpx.HTTPError as exc:
            # Не выводим тело запроса: в нём могут быть ключи и личные данные.
            logging.getLogger('uvicorn.error').warning('%s: %s', host, type(exc).__name__)
            label = {'arrodes': 'Arrodes', 'straus': 'Straus', 'monocle': 'Monocle', 'ameba': 'Ameba'}.get(service, service)
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
