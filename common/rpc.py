import hashlib
import hmac
import os

import httpx
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder


def internal_token(service):
    secret = os.getenv('INTERNAL_SECRET', '')
    if not secret:
        raise HTTPException(503, 'Настройте ключ шифрования интеграций в .env')
    return hmac.new(secret.encode(), service.encode(), hashlib.sha256).hexdigest()


def require_service(request: Request, service):
    if not hmac.compare_digest(request.headers.get('x-service-token', ''), internal_token(service)):
        raise HTTPException(403, 'Недопустимый внутренний запрос')


async def call(service, path, body=None, method='POST', timeout=90):
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, f'http://{service}:8000{path}',
            headers={'X-Service-Token': internal_token(service)}, json=jsonable_encoder(body))
        if not response.is_success:
            try:
                detail = response.json()['detail']
            except (ValueError, KeyError):
                detail = f'{service}: ошибка сервиса HTTP {response.status_code}'
            raise HTTPException(response.status_code, detail)
        return response.json()
