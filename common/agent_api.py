"""Общие безопасные ошибки и список моделей для трёх AI-сервисов."""
import asyncio
import logging

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI, APIConnectionError, APIStatusError

from common.codex_harness import cache_provider_models


MODEL_FIRST_DATA_TIMEOUT = 180
MODEL_IDLE_TIMEOUT = 180


def agent_routes(app, label):
    async def api_error(request, exc):
        code = getattr(exc, 'status_code', None)
        logging.getLogger('uvicorn.error').error('%s: %s (API %s)', label, type(exc).__name__, code or 'unknown')
        if code is None:
            detail = f'{label}: не удалось подключиться к шлюзу'
        else:
            reason = {
                401: 'шлюз отклонил API-ключ (HTTP 401)',
                402: 'недостаточно средств или исчерпан баланс ключа на шлюзе (HTTP 402)',
                403: 'шлюз запретил доступ к модели (HTTP 403)',
                404: 'шлюз не нашёл модель или Responses API (HTTP 404)',
                429: 'превышен лимит запросов или квота шлюза (HTTP 429)',
            }.get(code, f'шлюз отклонил запрос (HTTP {code})')
            detail = f'{label}: {reason}'
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
        endpoint = body.get('base_url') or 'https://lenec.tech/v1'
        async with AsyncOpenAI(api_key=body['api_key'], base_url=endpoint,
                              http_client=httpx.AsyncClient(timeout=30), max_retries=0) as client:
            available = await client.models.list()
            items = [model async for model in available if getattr(model, 'id', None)]
        cache_provider_models(endpoint, items)
        return sorted({model.id for model in items})


def response_text(response) -> str:
    """Извлекает текстовый итог из output_text, message.content или буфера потока."""
    direct = getattr(response, 'output_text', None)
    if isinstance(direct, str) and direct.strip():
        return direct
    parts = []
    for item in getattr(response, 'output', None) or ():
        item_type = item.get('type') if isinstance(item, dict) else getattr(item, 'type', None)
        if item_type in ('output_text', 'text'):
            text = item.get('text') if isinstance(item, dict) else getattr(item, 'text', None)
            if isinstance(text, str) and text:
                parts.append(text)
        elif item_type == 'message':
            content = item.get('content') if isinstance(item, dict) else getattr(item, 'content', None)
            for part in content or ():
                part_type = part.get('type') if isinstance(part, dict) else getattr(part, 'type', None)
                if part_type in ('output_text', 'text'):
                    text = part.get('text') if isinstance(part, dict) else getattr(part, 'text', None)
                    if isinstance(text, str) and text:
                        parts.append(text)
    if parts and ''.join(parts).strip():
        return ''.join(parts)
    streamed = getattr(response, '_streamed_output_text', '')
    return streamed if isinstance(streamed, str) and streamed.strip() else ''


async def stream_model_response(client, progress, **arguments):
    """Первые данные и паузы потока — до 3 минут; временные сбои повторяются до ответа."""
    retryable = (APIConnectionError, httpx.TransportError, TimeoutError)
    attempt = 0
    while True:
        received_data = False
        text_chunks = []
        done_chunks = []
        try:
            # Счётчик включает открытие соединения. Служебные created/in_progress
            # не продлевают ожидание: нужны новые данные ответа или вызова инструмента.
            async with asyncio.timeout(MODEL_FIRST_DATA_TIMEOUT) as idle:
                async with client.responses.stream(**arguments) as stream:
                    async for event in stream:
                        progress.model_event(event)
                        if event.type == 'response.output_text.delta' and getattr(event, 'delta', None):
                            text_chunks.append(event.delta)
                        elif event.type == 'response.output_text.done' and getattr(event, 'text', None):
                            done_chunks.append(event.text)
                        if event.type in ('response.completed', 'response.incomplete'):
                            response = event.response
                            idle.reschedule(None)
                            break
                        if event.type.endswith('.delta') and getattr(event, 'delta', None):
                            received_data = True
                            idle.reschedule(asyncio.get_running_loop().time() + MODEL_IDLE_TIMEOUT)
                    else:
                        response = await stream.get_final_response()
            streamed = ''.join(text_chunks) if text_chunks else ''.join(done_chunks)
            if streamed:
                try:
                    object.__setattr__(response, '_streamed_output_text', streamed)
                except Exception:
                    pass
            progress.finish_response(response)
            return response
        except (APIConnectionError, APIStatusError, httpx.TransportError, TimeoutError) as exc:
            transient = isinstance(exc, retryable) or getattr(exc, 'status_code', None) in (408, 429, 500, 502, 503, 504)
            if not transient:
                raise
            attempt += 1
            timeout_reason = ('Модель не передавала новые данные 3 минуты' if received_data
                              else 'Модель не начала ответ за 3 минуты')
            logging.getLogger('uvicorn.error').warning('model stream retry: %s', type(exc).__name__)
            progress.update(model_waiting=False)
            progress.add((timeout_reason if isinstance(exc, TimeoutError) else 'Соединение с моделью оборвалось')
                         + f'; повторяет запрос (попытка {attempt + 1})')
            await asyncio.sleep(min(30, 0.5 * 2 ** min(attempt - 1, 6)))
            progress.begin_response()
