"""Встроенный loopback OpenAI Responses API мост поверх нативного клиента Cloud Code."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import hmac
import json
import logging
import re
import secrets
import socket
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from common.antigravity import (
    AntigravityAuthError,
    AntigravityError,
    build_cloudcode_envelope,
    fetch_backend_session,
    fetch_live_catalog,
    load_code_assist,
    sanitize_schema_for_cloudcode,
    save_backend_project,
    stream_generate_content,
    validate_cloudcode_url,
)
from common.identity import current_user_id, user_id

_SAFE_NAME_RE = re.compile(r'[^a-zA-Z0-9_-]')
_DATA_URI_RE = re.compile(r'^data:([^;,]+);base64,(.+)$', re.S)


def _sanitize_tool_name(name: str, used: set[str]) -> str:
    cleaned = _SAFE_NAME_RE.sub('_', (name or '').strip() or 'tool')
    if not cleaned[0].isalpha() and cleaned[0] != '_':
        cleaned = 't_' + cleaned
    candidate = cleaned[:64]
    idx = 2
    while candidate in used:
        suffix = f'_{idx}'
        candidate = cleaned[: 64 - len(suffix)] + suffix
        idx += 1
    used.add(candidate)
    return candidate


def _parse_data_uri(url: str) -> tuple[str, str] | None:
    if not isinstance(url, str):
        return None
    match = _DATA_URI_RE.match(url.strip())
    if not match:
        return None
    mime = match.group(1).strip() or 'image/jpeg'
    data = match.group(2).strip()
    if not data:
        return None
    return mime, data


def _encode_encrypted_meta(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii')


def _decode_encrypted_meta(token: str | None) -> dict:
    if not isinstance(token, str) or not token.strip():
        return {}
    try:
        padded = token + '=' * (-len(token) % 4)
        obj = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _append_part(contents: list[dict], role: str, part: dict) -> None:
    if contents and contents[-1]['role'] == role:
        contents[-1]['parts'].append(part)
    else:
        contents.append({'role': role, 'parts': [part]})


class AntigravityBridgeSession:
    """Хранит контекст вызовов, thoughtSignature и сессию токена в пределах одного запуска."""

    def __init__(self, *, owner_id: int, cloudcode_url: str = ''):
        self.owner_id = owner_id
        self.cloudcode_url_override = (cloudcode_url or '').strip()
        self.access_token: str = ''
        self.token_expires_at: float = 0.0
        self.project_id: str = ''
        self.subscription_tier: str = ''
        self.user_agent: str = ''
        self.cloudcode_url: str = validate_cloudcode_url(self.cloudcode_url_override)
        self.call_meta: dict[str, dict] = {}
        self.item_signatures: dict[str, str] = {}
        self.responses_history: dict[str, list] = {}
        self.session_lock = asyncio.Lock()

    async def ensure_credentials(self, *, force_refresh: bool = False) -> tuple[str, str, str, str]:
        async with self.session_lock:
            now = time.time()
            if force_refresh or not self.access_token or now >= self.token_expires_at - 60:
                session = await fetch_backend_session(force_refresh=force_refresh)
                self.access_token = session['access_token']
                self.token_expires_at = float(session.get('expires_at') or (now + 3000))
                self.user_agent = str(session.get('user_agent') or '')
                if not self.cloudcode_url_override and session.get('cloudcode_url'):
                    self.cloudcode_url = validate_cloudcode_url(session['cloudcode_url'])
                if session.get('project_id'):
                    self.project_id = str(session['project_id']).strip()
                if session.get('subscription_tier'):
                    self.subscription_tier = str(session['subscription_tier']).strip()
            if not self.project_id:
                info = await load_code_assist(
                    self.access_token,
                    cloudcode_url=self.cloudcode_url,
                    user_agent=self.user_agent,
                )
                self.project_id = info['project_id']
                self.subscription_tier = info.get('subscription_tier', '') or self.subscription_tier
                await save_backend_project(self.project_id, self.subscription_tier)
            return self.access_token, self.project_id, self.cloudcode_url, self.user_agent

    def translate_tools(self, raw_tools: list | None) -> tuple[list[dict], dict[str, str], dict[str, str], set[str]]:
        if not raw_tools:
            return [], {}, {}, set()
        used: set[str] = set()
        declarations: list[dict] = []
        safe_to_orig: dict[str, str] = {}
        orig_to_safe: dict[str, str] = {}
        custom_names: set[str] = set()

        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            tool_type = tool.get('type')
            if tool_type == 'function':
                orig_name = str(tool.get('name') or '').strip()
                if not orig_name:
                    continue
                safe_name = _sanitize_tool_name(orig_name, used)
                safe_to_orig[safe_name] = orig_name
                orig_to_safe[orig_name] = safe_name
                decl: dict = {
                    'name': safe_name,
                    'description': str(tool.get('description') or orig_name)[:1024],
                }
                params = tool.get('parameters')
                if isinstance(params, dict):
                    cleaned = sanitize_schema_for_cloudcode(params)
                    if cleaned.get('properties'):
                        decl['parameters'] = cleaned
                declarations.append(decl)
            elif tool_type == 'custom':
                orig_name = str(tool.get('name') or '').strip()
                if not orig_name:
                    continue
                safe_name = _sanitize_tool_name(orig_name, used)
                safe_to_orig[safe_name] = orig_name
                orig_to_safe[orig_name] = safe_name
                custom_names.add(orig_name)
                declarations.append({
                    'name': safe_name,
                    'description': str(tool.get('description') or orig_name)[:1024],
                    'parameters': {
                        'type': 'OBJECT',
                        'properties': {
                            'input': {
                                'type': 'STRING',
                                'description': 'Полный текст входных данных для инструмента.',
                            }
                        },
                        'required': ['input'],
                    },
                })
        tools_payload = [{'functionDeclarations': declarations}] if declarations else []
        return tools_payload, safe_to_orig, orig_to_safe, custom_names

    def translate_request(self, body: dict) -> tuple[dict, dict]:
        model = str(body.get('model') or '').strip()
        if not model:
            raise AntigravityError('Не указана модель для запроса', status_code=400)

        tools_payload, safe_to_orig, orig_to_safe, custom_names = self.translate_tools(body.get('tools'))

        system_parts: list[str] = []
        if isinstance(body.get('instructions'), str) and body['instructions'].strip():
            system_parts.append(body['instructions'].strip())

        raw_input = body.get('input')
        prev_id = body.get('previous_response_id')
        history_items: list = []
        if isinstance(prev_id, str) and prev_id in self.responses_history:
            history_items.extend(self.responses_history[prev_id])

        if isinstance(raw_input, str):
            history_items.append({'type': 'message', 'role': 'user', 'content': raw_input})
        elif isinstance(raw_input, list):
            history_items.extend(raw_input)

        contents: list[dict] = []
        sig_by_item_id: dict[str, str] = dict(self.item_signatures)

        for item in history_items:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            role = item.get('role')

            if item_type == 'reasoning':
                meta = _decode_encrypted_meta(item.get('encrypted_content'))
                for k, v in (meta.get('call_sigs') or {}).items():
                    if isinstance(k, str) and isinstance(v, str) and v:
                        self.call_meta.setdefault(k, {})['thought_signature'] = v
                for k, v in (meta.get('item_sigs') or {}).items():
                    if isinstance(k, str) and isinstance(v, str) and v:
                        sig_by_item_id[k] = v
                continue

            if item_type in ('message', None) and role in ('system', 'developer'):
                content = item.get('content')
                if isinstance(content, str) and content.strip():
                    system_parts.append(content.strip())
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get('text'), str) and part['text'].strip():
                            system_parts.append(part['text'].strip())
                continue

            if item_type in ('message', None) and role in ('user', 'assistant'):
                cc_role = 'model' if role == 'assistant' else 'user'
                content = item.get('content')
                item_id = item.get('id') if isinstance(item.get('id'), str) else ''
                attached_sig = sig_by_item_id.get(item_id, '')
                if isinstance(content, str):
                    if content:
                        part = {'text': content}
                        if cc_role == 'model' and attached_sig:
                            part['thoughtSignature'] = attached_sig
                            attached_sig = ''
                        _append_part(contents, cc_role, part)
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get('type')
                        if btype in ('input_text', 'output_text', 'text'):
                            txt = block.get('text')
                            if isinstance(txt, str) and txt:
                                part = {'text': txt}
                                if cc_role == 'model' and attached_sig:
                                    part['thoughtSignature'] = attached_sig
                                    attached_sig = ''
                                _append_part(contents, cc_role, part)
                        elif btype == 'input_image':
                            parsed = _parse_data_uri(block.get('image_url') or '')
                            if parsed:
                                mime, b64 = parsed
                                _append_part(contents, cc_role, {'inlineData': {'mimeType': mime, 'data': b64}})
                continue

            if item_type in ('function_call', 'custom_tool_call'):
                orig_name = str(item.get('name') or '').strip()
                if not orig_name:
                    continue
                safe_name = orig_to_safe.get(orig_name) or _sanitize_tool_name(orig_name, set(safe_to_orig))
                call_id = str(item.get('call_id') or item.get('id') or '').strip()
                meta = self.call_meta.get(call_id, {})
                provider_id = meta.get('provider_id')
                sig = meta.get('thought_signature') or sig_by_item_id.get(str(item.get('id') or ''), '')

                if item_type == 'custom_tool_call':
                    args_obj = {'input': str(item.get('input') or '')}
                else:
                    raw_args = item.get('arguments')
                    if isinstance(raw_args, dict):
                        args_obj = raw_args
                    elif isinstance(raw_args, str) and raw_args.strip():
                        try:
                            parsed_args = json.loads(raw_args)
                            args_obj = parsed_args if isinstance(parsed_args, dict) else {'value': parsed_args}
                        except ValueError:
                            args_obj = {'raw': raw_args}
                    else:
                        args_obj = {}

                fc: dict = {'name': safe_name, 'args': args_obj}
                if provider_id:
                    fc['id'] = provider_id
                part: dict = {'functionCall': fc}
                if sig:
                    part['thoughtSignature'] = sig
                _append_part(contents, 'model', part)
                continue

            if item_type in ('function_call_output', 'custom_tool_call_output'):
                call_id = str(item.get('call_id') or '').strip()
                meta = self.call_meta.get(call_id, {})
                safe_name = meta.get('safe_name') or str(item.get('name') or 'tool')
                provider_id = meta.get('provider_id')
                raw_out = item.get('output')
                images: list[tuple[str, str]] = []
                text_parts: list[str] = []

                if isinstance(raw_out, str):
                    try:
                        parsed_out = json.loads(raw_out)
                        resp_obj = parsed_out if isinstance(parsed_out, dict) else {'result': parsed_out}
                    except ValueError:
                        resp_obj = {'result': raw_out}
                elif isinstance(raw_out, dict):
                    resp_obj = raw_out
                elif isinstance(raw_out, list):
                    for entry in raw_out:
                        if not isinstance(entry, dict):
                            continue
                        etype = entry.get('type')
                        if etype in ('input_text', 'text') and isinstance(entry.get('text'), str):
                            text_parts.append(entry['text'])
                        elif etype == 'input_image':
                            parsed_img = _parse_data_uri(entry.get('image_url') or '')
                            if parsed_img:
                                images.append(parsed_img)
                    if len(text_parts) == 1:
                        try:
                            parsed_out = json.loads(text_parts[0])
                            resp_obj = parsed_out if isinstance(parsed_out, dict) else {'result': parsed_out}
                        except ValueError:
                            resp_obj = {'result': text_parts[0]}
                    else:
                        resp_obj = {'result': '\n'.join(text_parts) if text_parts else ('Изображение приложено.' if images else '')}
                else:
                    resp_obj = {'result': '' if raw_out is None else str(raw_out)}

                fr: dict = {'name': safe_name, 'response': resp_obj}
                if provider_id:
                    fr['id'] = provider_id
                _append_part(contents, 'user', {'functionResponse': fr})
                for mime, b64 in images:
                    _append_part(contents, 'user', {'inlineData': {'mimeType': mime, 'data': b64}})

        if not contents:
            _append_part(contents, 'user', {'text': 'Продолжи.'})

        tool_config = None
        tc = body.get('tool_choice')
        if tools_payload:
            if tc == 'none':
                tool_config = {'functionCallingConfig': {'mode': 'NONE'}}
            elif tc == 'required':
                tool_config = {'functionCallingConfig': {'mode': 'ANY'}}
            elif isinstance(tc, dict) and tc.get('type') == 'function' and tc.get('name'):
                target_safe = orig_to_safe.get(tc['name'], tc['name'])
                tool_config = {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': [target_safe]}}
            else:
                tool_config = {'functionCallingConfig': {'mode': 'AUTO'}}

        gen_config: dict = {}
        if type(body.get('max_output_tokens')) is int and body['max_output_tokens'] > 0:
            gen_config['maxOutputTokens'] = body['max_output_tokens']
        if isinstance(body.get('temperature'), (int, float)) and not isinstance(body.get('temperature'), bool):
            gen_config['temperature'] = float(body['temperature'])
        if isinstance(body.get('top_p'), (int, float)) and not isinstance(body.get('top_p'), bool):
            gen_config['topP'] = float(body['top_p'])

        text_cfg = body.get('text')
        if isinstance(text_cfg, dict) and isinstance(text_cfg.get('format'), dict):
            fmt = text_cfg['format']
            if fmt.get('type') == 'json_schema' and isinstance(fmt.get('schema'), dict):
                if not tools_payload:
                    gen_config['responseMimeType'] = 'application/json'
                    gen_config['responseSchema'] = sanitize_schema_for_cloudcode(fmt['schema'])
                else:
                    system_parts.append(
                        'Когда завершаешь работу без вызова инструментов, финальный текстовый ответ обязан быть '
                        'строгим JSON, соответствующим следующей JSON Schema (без Markdown-обёртки):\n'
                        + json.dumps(fmt['schema'], ensure_ascii=False, separators=(',', ':'))
                    )
            elif fmt.get('type') == 'json_object' and not tools_payload:
                gen_config['responseMimeType'] = 'application/json'

        sys_inst = {'parts': [{'text': '\n\n'.join(system_parts)}]} if system_parts else None
        meta = {
            'model': model,
            'safe_to_orig': safe_to_orig,
            'custom_names': custom_names,
            'history_items': history_items,
            'tools_payload': tools_payload,
            'contents': contents,
            'system_instruction': sys_inst,
            'tool_config': tool_config,
            'gen_config': gen_config,
        }
        return meta, body


def _make_response_obj(
    resp_id: str,
    model: str,
    status: str,
    output: list[dict],
    *,
    usage: dict | None = None,
    incomplete_details: dict | None = None,
) -> dict:
    obj: dict = {
        'id': resp_id,
        'object': 'response',
        'created_at': int(time.time()),
        'status': status,
        'model': model,
        'output': output,
        'parallel_tool_calls': True,
        'error': None,
        'incomplete_details': incomplete_details,
    }
    if usage is not None:
        obj['usage'] = usage
    return obj


def _extract_usage(chunk_resp: dict) -> dict | None:
    um = chunk_resp.get('usageMetadata')
    if not isinstance(um, dict):
        return None
    in_tok = um.get('promptTokenCount')
    out_tok = um.get('candidatesTokenCount')
    thoughts_tok = um.get('thoughtsTokenCount') or 0
    cached_tok = um.get('cachedContentTokenCount') or 0
    total_tok = um.get('totalTokenCount')
    if not isinstance(in_tok, int) and not isinstance(out_tok, int):
        return None
    in_val = int(in_tok or 0)
    out_val = int(out_tok or 0) + (int(thoughts_tok) if isinstance(thoughts_tok, int) else 0)
    tot_val = int(total_tok) if isinstance(total_tok, int) else (in_val + out_val)
    return {
        'input_tokens': in_val,
        'output_tokens': out_val,
        'total_tokens': tot_val,
        'input_tokens_details': {'cached_tokens': int(cached_tok) if isinstance(cached_tok, int) else 0},
        'output_tokens_details': {'reasoning_tokens': int(thoughts_tok) if isinstance(thoughts_tok, int) else 0},
    }


def _sse(event_type: str, payload: dict) -> bytes:
    return f'event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'.encode('utf-8')


def create_bridge_app(session: AntigravityBridgeSession, bridge_token: str, label: str = 'Agent') -> FastAPI:
    app = FastAPI(docs_url=None, openapi_url=None)

    def _check_auth(request: Request) -> JSONResponse | None:
        auth = request.headers.get('authorization', '')
        expected = f'Bearer {bridge_token}'
        if not auth or not hmac.compare_digest(auth, expected):
            return JSONResponse(status_code=401, content={'error': {'message': 'Unauthorized', 'type': 'auth_error', 'code': 'unauthorized'}})
        return None

    async def _list_models(request: Request):
        if (denied := _check_auth(request)) is not None:
            return denied
        token = current_user_id.set(session.owner_id)
        try:
            catalog = await fetch_live_catalog()
        except AntigravityError as exc:
            status = exc.status_code or 502
            return JSONResponse(status_code=status, content={'error': {'message': exc.user_message, 'type': 'api_error', 'code': exc.error_status or str(status)}})
        finally:
            current_user_id.reset(token)
        return {
            'object': 'list',
            'data': [
                {
                    'id': m['id'],
                    'object': 'model',
                    'display_name': m['name'],
                    'context_window': m['context_window'],
                    'max_input_tokens': m['max_input_tokens'],
                    'max_output_tokens': m['max_output_tokens'],
                }
                for m in catalog['models']
            ],
        }

    app.add_api_route('/v1/models', _list_models, methods=['GET'])
    app.add_api_route('/models', _list_models, methods=['GET'])

    async def _run_generation(meta: dict):
        """Открывает поток Cloud Code с обработкой 401 refresh и ретраем временных сбоев до первого чанка."""
        max_attempts = 3
        refreshed = False
        attempt = 0
        while True:
            token_val, project_id, cc_url, ua = await session.ensure_credentials(force_refresh=refreshed)
            envelope = build_cloudcode_envelope(
                project_id=project_id,
                model=meta['model'],
                contents=meta['contents'],
                system_instruction=meta['system_instruction'],
                tools=meta['tools_payload'] or None,
                tool_config=meta['tool_config'],
                generation_config=meta['gen_config'] or None,
            )
            emitted_any = False
            try:
                async for chunk in stream_generate_content(token_val, envelope, cloudcode_url=cc_url, user_agent=ua):
                    emitted_any = True
                    yield chunk
                return
            except AntigravityAuthError:
                if not emitted_any and not refreshed:
                    refreshed = True
                    continue
                raise
            except AntigravityError as exc:
                if emitted_any or not exc.retryable or attempt >= max_attempts - 1:
                    raise
                attempt += 1
                delay = exc.retry_after if exc.retry_after is not None else min(8.0, 0.8 * (2 ** (attempt - 1)))
                logging.getLogger('uvicorn.error').warning(
                    '%s antigravity retry %d/%d (%s)', label, attempt, max_attempts, exc.status_code or exc.error_status or 'transient'
                )
                await asyncio.sleep(delay)

    async def _responses(request: Request):
        if (denied := _check_auth(request)) is not None:
            return denied
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(status_code=400, content={'error': {'message': 'Invalid JSON', 'type': 'invalid_request_error'}})

        token = current_user_id.set(session.owner_id)
        try:
            meta, raw_body = session.translate_request(body)
        except AntigravityError as exc:
            current_user_id.reset(token)
            return JSONResponse(status_code=exc.status_code or 400, content={'error': {'message': exc.user_message, 'type': 'invalid_request_error'}})

        stream_mode = bool(raw_body.get('stream', False))
        resp_id = 'resp_' + uuid.uuid4().hex[:24]
        model = meta['model']

        async def _collect_or_stream():
            output_items: list[dict] = []
            text_item_id: str | None = None
            text_output_index: int | None = None
            text_chunks: list[str] = []
            call_sigs: dict[str, str] = {}
            item_sigs: dict[str, str] = {}
            last_usage: dict | None = None
            finish_reason = ''
            seq = 0

            def next_seq() -> int:
                nonlocal seq
                seq += 1
                return seq

            def _ensure_text_item() -> list[bytes]:
                nonlocal text_item_id, text_output_index
                if text_item_id is not None:
                    return []
                text_item_id = 'msg_' + uuid.uuid4().hex[:24]
                text_output_index = len(output_items)
                msg_item = {
                    'id': text_item_id,
                    'type': 'message',
                    'status': 'in_progress',
                    'role': 'assistant',
                    'content': [],
                }
                output_items.append(msg_item)
                return [
                    _sse('response.output_item.added', {
                        'type': 'response.output_item.added',
                        'sequence_number': next_seq(),
                        'output_index': text_output_index,
                        'item': msg_item,
                    }),
                    _sse('response.content_part.added', {
                        'type': 'response.content_part.added',
                        'sequence_number': next_seq(),
                        'item_id': text_item_id,
                        'output_index': text_output_index,
                        'content_index': 0,
                        'part': {'type': 'output_text', 'text': '', 'annotations': []},
                    }),
                ]

            def _close_text_item() -> list[bytes]:
                nonlocal text_item_id, text_output_index
                if text_item_id is None or text_output_index is None:
                    return []
                full_text = ''.join(text_chunks)
                part_done = {'type': 'output_text', 'text': full_text, 'annotations': []}
                done_item = {
                    'id': text_item_id,
                    'type': 'message',
                    'status': 'completed',
                    'role': 'assistant',
                    'content': [part_done],
                }
                output_items[text_output_index] = done_item
                events = [
                    _sse('response.output_text.done', {
                        'type': 'response.output_text.done',
                        'sequence_number': next_seq(),
                        'item_id': text_item_id,
                        'output_index': text_output_index,
                        'content_index': 0,
                        'text': full_text,
                    }),
                    _sse('response.content_part.done', {
                        'type': 'response.content_part.done',
                        'sequence_number': next_seq(),
                        'item_id': text_item_id,
                        'output_index': text_output_index,
                        'content_index': 0,
                        'part': part_done,
                    }),
                    _sse('response.output_item.done', {
                        'type': 'response.output_item.done',
                        'sequence_number': next_seq(),
                        'output_index': text_output_index,
                        'item': done_item,
                    }),
                ]
                text_item_id = None
                text_output_index = None
                text_chunks.clear()
                return events

            try:
                gen = _run_generation(meta)
                first_chunk = None
                try:
                    first_chunk = await gen.__anext__()
                except StopAsyncIteration:
                    first_chunk = None

                if stream_mode:
                    created = _make_response_obj(resp_id, model, 'in_progress', [])
                    yield _sse('response.created', {'type': 'response.created', 'sequence_number': next_seq(), 'response': created})
                    yield _sse('response.in_progress', {'type': 'response.in_progress', 'sequence_number': next_seq(), 'response': created})

                pending_sig = ''

                async def _process_chunk(chunk_resp: dict):
                    nonlocal last_usage, finish_reason, pending_sig
                    usage = _extract_usage(chunk_resp)
                    if usage is not None:
                        last_usage = usage
                    candidates = chunk_resp.get('candidates') or []
                    if not candidates or not isinstance(candidates[0], dict):
                        return []
                    cand = candidates[0]
                    if isinstance(cand.get('finishReason'), str) and cand['finishReason']:
                        finish_reason = cand['finishReason']
                    content = cand.get('content') or {}
                    parts = content.get('parts') or []
                    events_out: list[bytes] = []

                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        part_sig = part.get('thoughtSignature') or part.get('thought_signature') or ''
                        if isinstance(part_sig, str) and part_sig:
                            pending_sig = part_sig

                        # Скрытые рассуждения никогда не передаются в UI как текст/summary.
                        if part.get('thought') is True:
                            continue

                        if isinstance(part.get('text'), str) and part['text'] != '':
                            events_out.extend(_ensure_text_item())
                            if pending_sig and text_item_id:
                                item_sigs[text_item_id] = pending_sig
                                session.item_signatures[text_item_id] = pending_sig
                                pending_sig = ''
                            delta = part['text']
                            text_chunks.append(delta)
                            if stream_mode:
                                events_out.append(_sse('response.output_text.delta', {
                                    'type': 'response.output_text.delta',
                                    'sequence_number': next_seq(),
                                    'item_id': text_item_id,
                                    'output_index': text_output_index,
                                    'content_index': 0,
                                    'delta': delta,
                                }))
                        elif isinstance(part.get('functionCall'), dict):
                            if text_item_id is not None:
                                events_out.extend(_close_text_item())
                            fc = part['functionCall']
                            safe_name = str(fc.get('name') or '').strip()
                            orig_name = meta['safe_to_orig'].get(safe_name, safe_name)
                            is_custom = orig_name in meta['custom_names']
                            provider_id = str(fc.get('id') or '').strip() or None
                            call_id = provider_id or ('call_' + uuid.uuid4().hex[:24])
                            fc_item_id = 'fc_' + uuid.uuid4().hex[:24]
                            raw_args = fc.get('args') if isinstance(fc.get('args'), dict) else {}

                            sig = part_sig or pending_sig
                            pending_sig = ''
                            if sig:
                                call_sigs[call_id] = sig
                            session.call_meta[call_id] = {
                                'provider_id': provider_id,
                                'original_name': orig_name,
                                'safe_name': safe_name,
                                'thought_signature': sig,
                                'is_custom': is_custom,
                            }

                            out_idx = len(output_items)
                            if is_custom:
                                custom_input = str(raw_args.get('input') if 'input' in raw_args else json.dumps(raw_args, ensure_ascii=False))
                                fc_added = {
                                    'id': fc_item_id,
                                    'type': 'custom_tool_call',
                                    'call_id': call_id,
                                    'name': orig_name,
                                    'input': '',
                                }
                                fc_done = dict(fc_added, input=custom_input)
                                output_items.append(fc_done)
                                if stream_mode:
                                    events_out.append(_sse('response.output_item.added', {
                                        'type': 'response.output_item.added',
                                        'sequence_number': next_seq(),
                                        'output_index': out_idx,
                                        'item': fc_added,
                                    }))
                                    if custom_input:
                                        events_out.append(_sse('response.custom_tool_call_input.delta', {
                                            'type': 'response.custom_tool_call_input.delta',
                                            'sequence_number': next_seq(),
                                            'item_id': fc_item_id,
                                            'output_index': out_idx,
                                            'delta': custom_input,
                                        }))
                                    events_out.append(_sse('response.custom_tool_call_input.done', {
                                        'type': 'response.custom_tool_call_input.done',
                                        'sequence_number': next_seq(),
                                        'item_id': fc_item_id,
                                        'output_index': out_idx,
                                        'input': custom_input,
                                    }))
                                    events_out.append(_sse('response.output_item.done', {
                                        'type': 'response.output_item.done',
                                        'sequence_number': next_seq(),
                                        'output_index': out_idx,
                                        'item': fc_done,
                                    }))
                            else:
                                args_json = json.dumps(raw_args, ensure_ascii=False)
                                fc_added = {
                                    'id': fc_item_id,
                                    'type': 'function_call',
                                    'status': 'in_progress',
                                    'call_id': call_id,
                                    'name': orig_name,
                                    'arguments': '',
                                }
                                fc_done = {
                                    'id': fc_item_id,
                                    'type': 'function_call',
                                    'status': 'completed',
                                    'call_id': call_id,
                                    'name': orig_name,
                                    'arguments': args_json,
                                }
                                output_items.append(fc_done)
                                if stream_mode:
                                    events_out.append(_sse('response.output_item.added', {
                                        'type': 'response.output_item.added',
                                        'sequence_number': next_seq(),
                                        'output_index': out_idx,
                                        'item': fc_added,
                                    }))
                                    if args_json:
                                        events_out.append(_sse('response.function_call_arguments.delta', {
                                            'type': 'response.function_call_arguments.delta',
                                            'sequence_number': next_seq(),
                                            'item_id': fc_item_id,
                                            'output_index': out_idx,
                                            'delta': args_json,
                                        }))
                                    events_out.append(_sse('response.function_call_arguments.done', {
                                        'type': 'response.function_call_arguments.done',
                                        'sequence_number': next_seq(),
                                        'item_id': fc_item_id,
                                        'output_index': out_idx,
                                        'arguments': args_json,
                                    }))
                                    events_out.append(_sse('response.output_item.done', {
                                        'type': 'response.output_item.done',
                                        'sequence_number': next_seq(),
                                        'output_index': out_idx,
                                        'item': fc_done,
                                    }))
                    return events_out

                if first_chunk is not None:
                    for ev in await _process_chunk(first_chunk):
                        if stream_mode:
                            yield ev
                    async for chunk in gen:
                        for ev in await _process_chunk(chunk):
                            if stream_mode:
                                yield ev

                for ev in _close_text_item():
                    if stream_mode:
                        yield ev

                if call_sigs or item_sigs:
                    rs_id = 'rs_' + uuid.uuid4().hex[:24]
                    rs_item = {
                        'id': rs_id,
                        'type': 'reasoning',
                        'summary': [],
                        'encrypted_content': _encode_encrypted_meta({'call_sigs': call_sigs, 'item_sigs': item_sigs}),
                    }
                    rs_idx = len(output_items)
                    output_items.append(rs_item)
                    if stream_mode:
                        yield _sse('response.output_item.added', {
                            'type': 'response.output_item.added',
                            'sequence_number': next_seq(),
                            'output_index': rs_idx,
                            'item': rs_item,
                        })
                        yield _sse('response.output_item.done', {
                            'type': 'response.output_item.done',
                            'sequence_number': next_seq(),
                            'output_index': rs_idx,
                            'item': rs_item,
                        })

                incomplete = finish_reason == 'MAX_TOKENS'
                final_status = 'incomplete' if incomplete else 'completed'
                final_resp = _make_response_obj(
                    resp_id,
                    model,
                    final_status,
                    output_items,
                    usage=last_usage,
                    incomplete_details={'reason': 'max_output_tokens'} if incomplete else None,
                )
                session.responses_history[resp_id] = list(meta['history_items']) + list(output_items)
                if len(session.responses_history) > 32:
                    oldest = next(iter(session.responses_history))
                    session.responses_history.pop(oldest, None)

                if stream_mode:
                    ev_name = 'response.incomplete' if incomplete else 'response.completed'
                    yield _sse(ev_name, {'type': ev_name, 'sequence_number': next_seq(), 'response': final_resp})
                else:
                    yield final_resp
            except AntigravityError as exc:
                if stream_mode and seq > 0:
                    yield _sse('error', {
                        'type': 'error',
                        'sequence_number': next_seq(),
                        'code': exc.error_status or str(exc.status_code or 502),
                        'message': exc.user_message,
                        'param': None,
                    })
                    failed_resp = _make_response_obj(resp_id, model, 'failed', output_items, usage=last_usage)
                    yield _sse('response.failed', {'type': 'response.failed', 'sequence_number': next_seq(), 'response': failed_resp})
                else:
                    raise
            finally:
                current_user_id.reset(token)

        if not stream_mode:
            try:
                result = None
                async for item in _collect_or_stream():
                    result = item
                return JSONResponse(status_code=200, content=result or _make_response_obj(resp_id, model, 'completed', []))
            except AntigravityError as exc:
                status = exc.status_code or 502
                return JSONResponse(
                    status_code=status,
                    content={'error': {'message': exc.user_message, 'type': 'api_error', 'code': exc.error_status or str(status)}},
                )

        # До начала потока перехватываем ошибки открытия соединения, чтобы вернуть реальный HTTP-код.
        stream_gen = _collect_or_stream()
        try:
            first_bytes = await stream_gen.__anext__()
        except StopAsyncIteration:
            first_bytes = b''
        except AntigravityError as exc:
            status = exc.status_code or 502
            return JSONResponse(
                status_code=status,
                content={'error': {'message': exc.user_message, 'type': 'api_error', 'code': exc.error_status or str(status)}},
            )

        async def _sse_bytes():
            if first_bytes:
                yield first_bytes
            async for chunk_bytes in stream_gen:
                yield chunk_bytes

        return StreamingResponse(
            _sse_bytes(),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    app.add_api_route('/v1/responses', _responses, methods=['POST'])
    app.add_api_route('/responses', _responses, methods=['POST'])

    @app.get('/health')
    async def health():
        return {'ok': True}

    return app


@asynccontextmanager
async def antigravity_responses_bridge(label: str = 'Agent', *, cloudcode_url: str = ''):
    """Запускает изолированный per-run loopback Responses API мост в текущем event loop."""
    owner_id = user_id()
    bridge_token = secrets.token_urlsafe(32)
    session = AntigravityBridgeSession(owner_id=owner_id, cloudcode_url=cloudcode_url)
    app = create_bridge_app(session, bridge_token, label=label)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('127.0.0.1', 0))
    sock.listen(64)
    sock.setblocking(False)
    port = sock.getsockname()[1]

    config = uvicorn.Config(
        app,
        host='127.0.0.1',
        port=port,
        log_level='warning',
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    serve_task = asyncio.create_task(server.serve(sockets=[sock]))

    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serve_task.done():
                    exc = serve_task.exception()
                    raise RuntimeError(f'{label}: локальный мост Antigravity не запустился: {type(exc).__name__ if exc else "stopped"}')
                await asyncio.sleep(0.02)
        yield f'http://127.0.0.1:{port}/v1', bridge_token
    finally:
        server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(serve_task, return_exceptions=True)
        except TimeoutError:
            serve_task.cancel()
            await asyncio.gather(serve_task, return_exceptions=True)
        try:
            sock.close()
        except OSError:
            pass
