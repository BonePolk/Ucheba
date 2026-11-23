"""Общие безопасные ошибки и список моделей для трёх AI-сервисов."""
import asyncio
from datetime import datetime, timezone
import logging

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI, APIConnectionError, APIStatusError

from common.codex_harness import cache_provider_models, detect_model_capabilities
from common.provider import gateway_url


MODEL_FIRST_DATA_TIMEOUT = 180
MODEL_IDLE_TIMEOUT = 180


def _gateway_headers(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'} if token else {}


def _gateway_health_url(endpoint: str) -> str:
    clean = endpoint.rstrip('/')
    if clean.endswith('/v1'):
        return clean[:-3] + '/health'
    return clean + '/health'


def _gateway_http_error(label: str, status_code: int) -> str:
    reason = {
        401: 'Antigravity Gateway отклонил токен доступа (HTTP 401)',
        403: 'Antigravity Gateway запретил доступ или аккаунт ограничен (HTTP 403)',
        404: 'Antigravity Gateway не поддерживает запрошенный endpoint или модель (HTTP 404)',
        429: 'превышен лимит запросов или квота Antigravity Gateway (HTTP 429)',
        503: 'Antigravity Gateway временно недоступен или нет готовых аккаунтов (HTTP 503)',
    }.get(status_code, f'Antigravity Gateway отклонил запрос (HTTP {status_code})')
    return f'{label}: {reason}'


async def _fetch_gateway_models(endpoint: str, token: str, label: str, *, timeout: float = 30.0) -> list[dict]:
    headers = _gateway_headers(token)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f'{endpoint}/models', headers=headers)
    except httpx.TimeoutException:
        raise HTTPException(502, f'{label}: Antigravity Gateway не ответил вовремя') from None
    except httpx.HTTPError:
        raise HTTPException(
            502,
            f'{label}: не удалось подключиться к Antigravity Gateway. Проверьте адрес и доступность из контейнера.',
        ) from None
    if not response.is_success:
        raise HTTPException(502, _gateway_http_error(label, response.status_code)) from None
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(502, f'{label}: Antigravity Gateway вернул некорректный список моделей') from None
    items = payload.get('data') if isinstance(payload, dict) else (payload if isinstance(payload, list) else None)
    if not isinstance(items, list):
        raise HTTPException(502, f'{label}: Antigravity Gateway вернул некорректный список моделей')
    cache_provider_models(endpoint, items, source_kind='antigravity_gateway', auth_mode='antigravity_gateway')
    return items


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
            raw_url = (body.get('base_url') or '').strip()
            if not raw_url:
                raise HTTPException(400, 'Укажите адрес Antigravity Gateway в разделе «Подключения AI»')
            try:
                endpoint = gateway_url(raw_url)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            token = (body.get('gateway_token') or body.get('api_key') or '').strip()
            items = await _fetch_gateway_models(endpoint, token, label)
            ids = []
            for item in items:
                mid = (item.get('id') or item.get('model')) if isinstance(item, dict) else (getattr(item, 'id', None) or getattr(item, 'model', None))
                if isinstance(mid, str) and mid.strip():
                    ids.append(mid.strip())
            return sorted(set(ids))
        if not body.get('api_key'):
            raise HTTPException(400, f'Сохраните API-ключ {label}')
        endpoint = body.get('base_url') or 'https://lenec.tech/v1'
        async with AsyncOpenAI(api_key=body['api_key'], base_url=endpoint,
                              http_client=httpx.AsyncClient(timeout=30), max_retries=0) as client:
            available = await client.models.list()
            items = [model async for model in available if getattr(model, 'id', None)]
        cache_provider_models(endpoint, items)
        return sorted({model.id for model in items})

    @app.post('/gateway/status')
    async def gateway_status(body: dict | None = None):
        data = body or {}
        raw_url = (data.get('base_url') or '').strip()
        if not raw_url:
            return {
                'configured': False,
                'reachable': False,
                'models_count': 0,
                'health_ok': None,
                'health_status': None,
                'error': None,
                'checked_at': None,
            }
        try:
            endpoint = gateway_url(raw_url)
        except ValueError as exc:
            if data.get('strict'):
                raise HTTPException(422, str(exc)) from None
            return {
                'configured': True,
                'reachable': False,
                'models_count': 0,
                'health_ok': None,
                'health_status': None,
                'error': str(exc),
                'checked_at': datetime.now(timezone.utc).isoformat(),
            }
        token = (data.get('gateway_token') or data.get('api_key') or '').strip()
        strict = bool(data.get('strict', False))
        probe_timeout = 20.0 if strict else 10.0
        try:
            items = await _fetch_gateway_models(endpoint, token, label, timeout=probe_timeout)
        except HTTPException as exc:
            if strict:
                raise
            return {
                'configured': True,
                'reachable': False,
                'models_count': 0,
                'health_ok': None,
                'health_status': None,
                'error': str(exc.detail),
                'checked_at': datetime.now(timezone.utc).isoformat(),
            }
        ids = {
            mid.strip()
            for item in items
            if isinstance(
                mid := (
                    (item.get('id') or item.get('model'))
                    if isinstance(item, dict)
                    else (getattr(item, 'id', None) or getattr(item, 'model', None))
                ),
                str,
            )
            and mid.strip()
        }
        health_ok = None
        health_status = None
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(_gateway_health_url(endpoint), headers=_gateway_headers(token))
            if resp.is_success:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    raw_status = payload.get('status')
                    if isinstance(raw_status, str) and raw_status.strip():
                        health_status = raw_status.strip()[:64]
                health_ok = health_status is None or health_status.lower() not in ('error', 'unhealthy', 'down')
            elif resp.status_code >= 500:
                health_ok = False
                health_status = f'HTTP {resp.status_code}'
        except httpx.HTTPError:
            pass
        return {
            'configured': True,
            'reachable': True,
            'models_count': len(ids),
            'health_ok': health_ok,
            'health_status': health_status,
            'error': None,
            'checked_at': datetime.now(timezone.utc).isoformat(),
        }

    @app.post('/capabilities')
    async def capabilities(body: dict | None = None):
        data = body or {}
        cfg = data.get('config') or {}
        default_mode = 'oauth' if label == 'Arrodes' else 'api_key'
        auth_mode = (cfg.get('auth_mode') or default_mode).strip()
        model = (cfg.get('model') or '').strip()
        if auth_mode == 'api_key':
            base_url = (cfg.get('base_url') or 'https://lenec.tech/v1').strip()
            api_key = (data.get('api_key') or '').strip()
        elif auth_mode == 'antigravity_gateway':
            base_url = (cfg.get('base_url') or '').strip()
            api_key = (data.get('api_key') or '').strip()
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
