"""Общие безопасные ошибки и список моделей для четырёх AI-сервисов."""
import asyncio
import logging

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI, APIConnectionError, APIStatusError

from common.antigravity import AntigravityError, fetch_live_catalog, test_generation_direct
from common.codex_harness import cache_provider_models, detect_model_capabilities


MODEL_FIRST_DATA_TIMEOUT = 180
MODEL_IDLE_TIMEOUT = 180


def agent_routes(app, label, *, oauth_client_getter=None):
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
        auth_mode = (body.get('auth_mode') or 'api_key').strip()
        if auth_mode == 'antigravity_gateway':
            auth_mode = 'antigravity'
        if auth_mode == 'antigravity':
            try:
                catalog = await fetch_live_catalog(
                    force_refresh=bool(body.get('force_refresh')),
                    include_internal=body.get('include_internal'),
                )
            except AntigravityError as exc:
                status = 400 if exc.status_code == 400 else 401 if exc.status_code == 401 else 502
                raise HTTPException(status, f'{label}: {exc.user_message}') from None
            if body.get('detailed'):
                return catalog['models']
            return [m['id'] for m in catalog['models']]
        if not body.get('api_key'):
            raise HTTPException(400, f'Сохраните API-ключ {label}')
        endpoint = body.get('base_url') or 'https://lenec.tech/v1'
        async with AsyncOpenAI(api_key=body['api_key'], base_url=endpoint,
                              http_client=httpx.AsyncClient(timeout=30), max_retries=0) as client:
            available = await client.models.list()
            items = [model async for model in available if getattr(model, 'id', None)]
        cache_provider_models(endpoint, items)
        return sorted({model.id for model in items})

    @app.post('/antigravity/catalog')
    async def antigravity_catalog(body: dict | None = None):
        data = body or {}
        try:
            return await fetch_live_catalog(
                force_refresh=bool(data.get('force_refresh')),
                include_internal=data.get('include_internal'),
            )
        except AntigravityError as exc:
            status = 400 if exc.status_code == 400 else 401 if exc.status_code == 401 else 502
            raise HTTPException(status, exc.user_message) from None

    @app.post('/antigravity/test-generation')
    async def antigravity_test_generation(body: dict | None = None):
        data = body or {}
        model = str(data.get('model') or '').strip()
        if not model:
            raise HTTPException(422, 'Укажите модель для проверки генерации')
        try:
            return await test_generation_direct(model=model)
        except AntigravityError as exc:
            status = exc.status_code if exc.status_code in (400, 401, 403, 404, 429) else 502
            raise HTTPException(status, exc.user_message) from None

    @app.post('/capabilities')
    async def capabilities(body: dict | None = None):
        data = body or {}
        cfg = data.get('config') or {}
        default_mode = 'oauth' if label == 'Arrodes' else 'api_key'
        auth_mode = (cfg.get('auth_mode') or default_mode).strip()
        if auth_mode == 'antigravity_gateway':
            auth_mode = 'antigravity'
        model = (cfg.get('model') or '').strip()
        if auth_mode == 'api_key':
            base_url = (cfg.get('base_url') or 'https://lenec.tech/v1').strip()
            api_key = (data.get('api_key') or '').strip()
        elif auth_mode == 'antigravity':
            base_url = (cfg.get('cloudcode_url') or cfg.get('base_url') or '').strip()
            api_key = ''
        else:
            base_url = ''
            api_key = ''
        client = await oauth_client_getter() if auth_mode == 'oauth' and callable(oauth_client_getter) else None
        caps = await detect_model_capabilities(
            auth_mode=auth_mode,
            base_url=base_url,
            api_key=api_key,
            model=model,
            config=cfg,
            client=client,
        )
        return caps.to_dict()


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
