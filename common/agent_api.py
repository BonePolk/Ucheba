"""Общие безопасные ошибки и список моделей для трёх AI-сервисов."""
import asyncio
import logging

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI, APIConnectionError, APIStatusError


MODEL_FIRST_DATA_TIMEOUT = 180
MODEL_IDLE_TIMEOUT = 180


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
        async with AsyncOpenAI(api_key=body['api_key'], base_url=body.get('base_url') or 'https://lenec.tech/v1',
                              http_client=httpx.AsyncClient(timeout=30), max_retries=0) as client:
            available = await client.models.list()
            result = {model.id async for model in available if model.id}
        return sorted(result)


async def stream_model_response(client, progress, **arguments):
    """Первые данные и паузы потока — до 3 минут. Один повтор при сбое."""
    retryable = (APIConnectionError, httpx.TransportError, TimeoutError)
    for attempt in range(2):
        received_data = False
        try:
            # Счётчик включает открытие соединения. Служебные created/in_progress
            # не продлевают ожидание: нужны новые данные ответа или вызова инструмента.
            async with asyncio.timeout(MODEL_FIRST_DATA_TIMEOUT) as idle:
                async with client.responses.stream(**arguments) as stream:
                    async for event in stream:
                        progress.model_event(event)
                        if event.type in ('response.completed', 'response.incomplete'):
                            response = event.response
                            idle.reschedule(None)
                            break
                        if event.type.endswith('.delta') and getattr(event, 'delta', None):
                            received_data = True
                            idle.reschedule(asyncio.get_running_loop().time() + MODEL_IDLE_TIMEOUT)
                    else:
                        response = await stream.get_final_response()
            progress.finish_response(response)
            return response
        except (APIConnectionError, APIStatusError, httpx.TransportError, TimeoutError) as exc:
            transient = isinstance(exc, retryable) or getattr(exc, 'status_code', None) in (502, 503, 504)
            if not transient:
                raise
            timeout_reason = ('Модель не передавала новые данные 3 минуты' if received_data
                              else 'Модель не начала ответ за 3 минуты')
            if attempt:
                detail = (timeout_reason + ' после повторной попытки. Поручение не завершено.'
                          if isinstance(exc, TimeoutError) else 'Соединение с моделью не восстановилось после повторной попытки. Поручение не завершено.')
                raise HTTPException(502, detail) from None
            logging.getLogger('uvicorn.error').warning('model stream retry: %s', type(exc).__name__)
            progress.update(model_waiting=False)
            progress.add(timeout_reason + '; повторяет текущий запрос'
                         if isinstance(exc, TimeoutError) else 'Соединение с моделью оборвалось; повторяет текущий запрос')
            await asyncio.sleep(0.5)
            progress.begin_response()
