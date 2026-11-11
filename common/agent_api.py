"""Общие безопасные ошибки и список моделей для трёх AI-сервисов."""
import logging

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI, APIConnectionError, APIStatusError


def agent_routes(app, label):
    async def api_error(request, exc):
        code = getattr(exc, 'status_code', None)
        logging.getLogger('uvicorn.error').error('%s: %s (API %s)', label, type(exc).__name__, code or 'unknown')
        detail = f'{label}: не удалось подключиться к шлюзу' if code is None else f'{label}: шлюз отклонил запрос (HTTP {code})'
        return JSONResponse(status_code=502, content={'detail': detail})

    async def stream_error(request, exc):
        logging.getLogger('uvicorn.error').warning('%s: stream interrupted (%s)', label, type(exc).__name__)
        return JSONResponse(status_code=502, content={'detail': f'{label}: соединение с AI-шлюзом оборвалось во время ответа. Повторите запрос.'})

    app.add_exception_handler(httpx.RemoteProtocolError, stream_error)
    app.add_exception_handler(httpx.ReadError, stream_error)
    app.add_exception_handler(APIConnectionError, api_error)
    app.add_exception_handler(APIStatusError, api_error)

    @app.post('/models')
    async def models(body: dict):
        if not body.get('api_key'):
            raise HTTPException(400, f'Сохраните API-ключ {label}')
        async with AsyncOpenAI(api_key=body['api_key'], base_url='https://lenec.tech/v1',
                              http_client=httpx.AsyncClient(timeout=30), max_retries=0) as client:
            available = await client.models.list()
        return sorted({model.id for model in available.data})
