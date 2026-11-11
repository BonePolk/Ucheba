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


async def stream_model_response(client, progress, **arguments):
    """Один повтор сетевого сбоя. Инструменты выполняются только после полного ответа."""
    import asyncio
    retryable = (APIConnectionError, httpx.TransportError)
    for attempt in range(2):
        try:
            async with client.responses.stream(**arguments) as stream:
                async for event in stream:
                    progress.model_event(event)
                    if event.type in ('response.completed', 'response.incomplete'):
                        response = event.response
                        break
                else:
                    response = await stream.get_final_response()
            progress.finish_response(response)
            return response
        except (APIConnectionError, APIStatusError, httpx.TransportError) as exc:
            transient = isinstance(exc, retryable) or getattr(exc, 'status_code', None) in (502, 503, 504)
            if not transient:
                raise
            if attempt:
                raise HTTPException(502, 'Соединение с моделью не восстановилось после повторной попытки. Поручение не завершено.') from None
            logging.getLogger('uvicorn.error').warning('model stream retry: %s', type(exc).__name__)
            progress.update(model_waiting=False)
            progress.add('Соединение с моделью оборвалось; повторяет текущий запрос')
            await asyncio.sleep(0.5)
            progress.begin_response()
