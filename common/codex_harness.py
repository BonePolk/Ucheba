"""Общий Codex harness (openai-codex SDK + изолированный MCP) для всех агентов."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import struct
import tempfile
from typing import Any

from fastapi import HTTPException
import httpx
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

from common.browser_client import browser_run_id
from common.identity import current_user_id, user_id
from common.progress import timestamp
from common.provider import provider_url

HOME = Path('/home/codex')
WORKER_BIN = '/usr/local/bin/codex-worker'
MCP_STDIO_SCRIPT = '/app/common/mcp_stdio.py'
CONTAINER_SANDBOX = os.environ.get('ARRODES_SANDBOX', '').strip().lower() == 'container'
API_PROVIDER_ID = 'ucheba_api'
CLIENT_CLOSE_TIMEOUT = 15
MCP_SHUTDOWN_TIMEOUT = 5
TURN_START_TIMEOUT = 60
TURN_IDLE_TIMEOUT = 300
BUSY_IDLE_TIMEOUT = 600
MCP_TOOL_TIMEOUT = 180
USER_WAIT_TOOLS = frozenset({'ask_user', 'browser_wait_for_user'})
UNBOUNDED_TOOLS = frozenset({'ask_user', 'browser_wait_for_user', 'delete_silo_file'})


def user_home() -> Path:
    return HOME / '.codex' / 'users' / str(user_id())


def prepare_codex_root(migrate_legacy: bool = False) -> None:
    root = HOME / '.codex'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    users = root / 'users'
    users.mkdir(exist_ok=True, mode=0o711)
    owner_home = users / '1'
    owner_home.mkdir(exist_ok=True, mode=0o700)
    oauth_home = owner_home / '.codex'
    api_home = owner_home / '.codex-api'
    oauth_home.mkdir(exist_ok=True, mode=0o700)
    api_home.mkdir(exist_ok=True, mode=0o700)
    if migrate_legacy:
        for item in root.iterdir():
            if item.name in ('users', 'arrodes-state'):
                continue
            target = oauth_home / item.name
            try:
                if not target.exists():
                    shutil.move(str(item), target)
            except (FileNotFoundError, shutil.Error):
                pass
    for item in (owner_home, oauth_home, api_home):
        os.chown(item, 10001, 10001)
        item.chmod(0o700)
    for item in (HOME, root, users):
        os.chown(item, 0, 0)
        item.chmod(0o711)
    tmp = Path('/tmp')
    if tmp.is_dir():
        tmp.chmod(0o1733)


def prepare_codex_user() -> None:
    uid = 10000 + user_id()
    root = user_home()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(root, uid, uid)
    root.chmod(0o700)
    for sub in ('.codex', '.codex-api'):
        folder = root / sub
        folder.mkdir(exist_ok=True, mode=0o700)
        os.chown(folder, uid, uid)
        folder.chmod(0o700)


def normalize_auth(config: dict | None, api_key: str = '', *, default_mode: str = 'api_key', label: str = 'Агент'):
    cfg = config or {}
    auth_mode = (cfg.get('auth_mode') or default_mode).strip()
    if auth_mode not in ('oauth', 'api_key'):
        raise HTTPException(422, 'Неизвестный режим авторизации')
    model = (cfg.get('model') or '').strip()
    if auth_mode == 'api_key':
        key = (api_key or '').strip()
        if not key:
            raise HTTPException(400, f'Для {label} в режиме API-ключа сохраните API-ключ в профиле')
        if not model:
            raise HTTPException(400, f'Для {label} в режиме API-ключа укажите модель в профиле')
        try:
            base_url = provider_url(cfg.get('base_url') or 'https://lenec.tech/v1')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return 'api_key', key, base_url, model
    return 'oauth', '', '', model


def normalize_effort(value: str | None, default: str = 'medium') -> str:
    return value if value in ('low', 'medium', 'high') else default


def append_custom_instructions(base: str, custom: str | None) -> str:
    text = (custom or '').strip()[:2000]
    if not text:
        return base
    return base + '\n\nДополнительные указания владельца (не отменяют правила безопасности и формат ответа):\n' + text


def build_codex_config(
    auth_mode: str,
    *,
    api_key: str = '',
    base_url: str = '',
    allow_shell: bool = False,
    web_search: str = 'disabled',
    cwd: str | Path | None = None,
) -> tuple[CodexConfig, str | None]:
    mode_arg = 'api' if auth_mode == 'api_key' else 'oauth'
    web_search = web_search if web_search in ('disabled', 'cached', 'live') else 'disabled'
    args = [
        WORKER_BIN,
        str(user_id()),
        mode_arg,
        '-c',
        'sandbox_workspace_write.network_access=false',
        '-c',
        f'web_search={json.dumps(web_search)}',
    ]
    if not allow_shell:
        args.extend(['-c', 'features.shell_tool=false'])
    if auth_mode == 'api_key':
        args.extend([
            '-c', f'model_provider={json.dumps(API_PROVIDER_ID)}',
            '-c', f'model_providers.{API_PROVIDER_ID}.name="Ucheba API"',
            '-c', f'model_providers.{API_PROVIDER_ID}.base_url={json.dumps(base_url)}',
            '-c', f'model_providers.{API_PROVIDER_ID}.wire_api="responses"',
            '-c', f'model_providers.{API_PROVIDER_ID}.env_key="UCHEBA_API_KEY"',
            '-c', f'model_providers.{API_PROVIDER_ID}.requires_openai_auth=false',
            '-c', f'model_providers.{API_PROVIDER_ID}.stream_idle_timeout_ms=180000',
            '-c', f'model_providers.{API_PROVIDER_ID}.stream_max_retries=2',
        ])
        env = {'UCHEBA_API_KEY': api_key}
        thread_provider = API_PROVIDER_ID
    else:
        env = None
        thread_provider = None
    args.extend(['app-server', '--listen', 'stdio://'])
    return CodexConfig(cwd=str(cwd or user_home()), env=env, launch_args_override=tuple(args)), thread_provider


async def _close_client(label: str, client) -> None:
    if client is None:
        return
    try:
        async with asyncio.timeout(CLIENT_CLOSE_TIMEOUT):
            await client.close()
    except TimeoutError:
        logging.getLogger('uvicorn.error').warning('%s: codex client close timed out', label)
    except Exception:
        pass


@asynccontextmanager
async def open_codex(
    label: str,
    *,
    auth_mode: str,
    api_key: str = '',
    base_url: str = '',
    allow_shell: bool = False,
    web_search: str = 'disabled',
    cwd: str | Path | None = None,
):
    prepare_codex_user()
    config, thread_provider = build_codex_config(
        auth_mode,
        api_key=api_key,
        base_url=base_url,
        allow_shell=allow_shell,
        web_search=web_search,
        cwd=cwd,
    )
    client = AsyncCodex(config)
    try:
        async with asyncio.timeout(TURN_START_TIMEOUT):
            await client.__aenter__()
    except TimeoutError:
        await _close_client(label, client)
        raise HTTPException(502, f'{label}: не удалось запустить процесс агента') from None
    try:
        if auth_mode == 'oauth':
            try:
                async with asyncio.timeout(TURN_START_TIMEOUT):
                    account = await client.account()
            except TimeoutError:
                raise HTTPException(502, f'{label}: не удалось проверить OpenAI OAuth') from None
            if account.account is None:
                raise HTTPException(400, f'Подключите OpenAI OAuth в профиле для {label}')
        yield client, thread_provider
    finally:
        await _close_client(label, client)


async def start_codex_thread(client, label: str, *, resume_id: str | None = None, **kwargs):
    try:
        async with asyncio.timeout(TURN_START_TIMEOUT):
            if resume_id:
                return await client.thread_resume(resume_id, **kwargs)
            return await client.thread_start(**kwargs)
    except TimeoutError:
        raise HTTPException(502, f'{label}: не удалось инициализировать сессию агента') from None


@asynccontextmanager
async def scratch_env(writable: bool = False):
    uid = 10000 + user_id()
    with tempfile.TemporaryDirectory(prefix='codex-run-') as scratch:
        os.chmod(scratch, 0o711)
        run_home = Path(scratch) / 'home'
        run_tmp = Path(scratch) / 'tmp'
        work_dir = Path(scratch) / 'work'
        for item in (run_home, run_tmp):
            item.mkdir(mode=0o700)
            os.chown(item, uid, uid)
        if writable:
            work_dir.mkdir(mode=0o700)
            os.chown(work_dir, uid, uid)
        else:
            work_dir.mkdir(mode=0o555)
            os.chown(work_dir, 0, 0)
        yield work_dir, {
            'allow_login_shell': False,
            'shell_environment_policy': {
                'inherit': 'none',
                'set': {
                    'HOME': str(run_home),
                    'TMPDIR': str(run_tmp),
                    'TMP': str(run_tmp),
                    'TEMP': str(run_tmp),
                    'PATH': '/usr/local/bin:/usr/bin:/bin',
                    'LANG': 'C.UTF-8',
                },
            },
        }


class McpToolAdapter:
    def __init__(self, tools, handler, progress):
        self.tools = tools
        self.schemas = {item['name']: item for item in tools}
        self.handler = handler
        self.progress = progress
        self.turn = None
        self.lock = asyncio.Lock()
        self.active_calls = 0
        self.active_tools: list[str] = []
        self.terminal_result = None
        self.fatal_error = None

    @property
    def waiting_for_user(self) -> bool:
        return any(name in USER_WAIT_TOOLS for name in self.active_tools)

    async def respond(self, message):
        method = message['method']
        if method == 'initialize':
            return {
                'protocolVersion': message['params']['protocolVersion'],
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'ucheba', 'version': '1'},
            }
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {
                'tools': [
                    {
                        'name': item['name'],
                        'description': item['description'],
                        'inputSchema': item['parameters'],
                    }
                    for item in self.tools
                ]
            }
        if method != 'tools/call':
            raise ValueError('Метод не поддерживается')
        name = message['params']['name']
        if name not in self.schemas:
            raise ValueError('Неизвестный инструмент')
        args = message['params'].get('arguments', {})
        self.active_calls += 1
        self.active_tools.append(name)
        try:
            async with self.lock:
                if name in UNBOUNDED_TOOLS:
                    output, extra_content = await self.handler(name, args)
                else:
                    async with asyncio.timeout(MCP_TOOL_TIMEOUT):
                        output, extra_content = await self.handler(name, args)
            content = [{'type': 'text', 'text': json.dumps(output, ensure_ascii=False, default=str)}]
            for item in extra_content or ():
                if item.get('type') == 'input_text':
                    content.append({'type': 'text', 'text': item['text']})
                elif item.get('type') == 'input_image':
                    url = item['image_url']
                    header, data = url.split(',', 1)
                    content.append({'type': 'image', 'data': data, 'mimeType': header[5:].split(';')[0]})
            return {'content': content, 'isError': False}
        except TimeoutError:
            logging.getLogger('uvicorn.error').warning('Codex MCP tool timeout: %s', name)
            detail = 'Инструмент превысил время ожидания. Проверь состояние и попробуй другой шаг.'
            if self.progress is not None and self.fatal_error is None:
                self.progress.add('Инструмент превысил время ожидания')
            return {'content': [{'type': 'text', 'text': detail}], 'isError': True}
        except (HTTPException, httpx.HTTPError, ValueError, OSError, KeyError, TypeError) as exc:
            detail = str(exc.detail) if isinstance(exc, HTTPException) else 'Ошибка инструмента: ' + type(exc).__name__
            if self.progress is not None and self.fatal_error is None:
                self.progress.add(detail)
            return {'content': [{'type': 'text', 'text': detail}], 'isError': True}
        finally:
            self.active_calls -= 1
            if name in self.active_tools:
                self.active_tools.remove(name)


@asynccontextmanager
async def serve_mcp(adapter: McpToolAdapter):
    owner = user_id()
    run_id = browser_run_id.get()
    handlers = set()
    writers = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        token = current_user_id.set(owner)
        browser_token = browser_run_id.set(run_id)
        try:
            credentials = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if struct.unpack('3i', credentials)[1] != 10000 + owner:
                return
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.IncompleteReadError, ConnectionError, BrokenPipeError):
                    break
                except ValueError as exc:
                    logging.getLogger('uvicorn.error').warning('Codex MCP read error: %s', type(exc).__name__)
                    break
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (ValueError, json.JSONDecodeError) as exc:
                    logging.getLogger('uvicorn.error').warning('Codex MCP decode error: %s', type(exc).__name__)
                    break
                if 'id' not in message:
                    continue
                try:
                    result = {'result': await adapter.respond(message)}
                except (ValueError, KeyError):
                    result = {'error': {'code': -32602, 'message': 'Некорректный MCP-запрос'}}
                except Exception as exc:
                    # Keep the connection usable; expose no arguments or private exception text.
                    logging.getLogger('uvicorn.error').error('Codex MCP handler: %s', type(exc).__name__)
                    result = {'error': {'code': -32603, 'message': 'Внутренняя ошибка инструмента: ' + type(exc).__name__}}
                try:
                    writer.write((json.dumps({'jsonrpc': '2.0', 'id': message['id'], **result}, ensure_ascii=False) + '\n').encode())
                    await writer.drain()
                except (ConnectionError, BrokenPipeError):
                    break
        finally:
            try:
                writer.close()
                async with asyncio.timeout(2):
                    await writer.wait_closed()
            except Exception:
                if writer.transport is not None:
                    writer.transport.abort()
            finally:
                writers.discard(writer)
                browser_run_id.reset(browser_token)
                current_user_id.reset(token)
                handlers.discard(task)

    with tempfile.TemporaryDirectory(prefix='codex-mcp-') as directory:
        os.chmod(directory, 0o711)
        path = str(Path(directory) / 'tools.sock')
        server = await asyncio.start_unix_server(handle, path, limit=16 * 1024 * 1024)
        os.chown(path, 10000 + owner, 10000 + owner)
        os.chmod(path, 0o600)
        try:
            yield {
                'mcp_servers': {
                    'ucheba': {
                        'command': '/usr/local/bin/python',
                        'args': [MCP_STDIO_SCRIPT, path],
                        'tool_timeout_sec': 2147483647,
                        'required': True,
                        'default_tools_approval_mode': 'approve',
                    }
                }
            }
        finally:
            server.close()
            for task in list(handlers):
                task.cancel()
            for writer in list(writers):
                writer.close()
            try:
                async with asyncio.timeout(MCP_SHUTDOWN_TIMEOUT):
                    await asyncio.gather(*handlers, return_exceptions=True)
                    await server.wait_closed()
            except TimeoutError:
                for writer in list(writers):
                    if writer.transport is not None:
                        writer.transport.abort()
                logging.getLogger('uvicorn.error').warning('Codex MCP server shutdown timed out')


@dataclass
class TurnOutcome:
    text: str
    completed: bool
    status: str
    error_detail: str | None
    usage: dict
    terminal_result: Any = None


def _extract_error_info(error_obj):
    if error_obj is None:
        return None, None
    info = getattr(error_obj, 'codex_error_info', None) or getattr(error_obj, 'codexErrorInfo', None)
    if info is None and isinstance(error_obj, dict):
        info = error_obj.get('codex_error_info') or error_obj.get('codexErrorInfo')
    if info is None:
        return None, None
    inner = getattr(info, 'root', info)
    kind = getattr(inner, 'type', None)
    if kind is None and isinstance(inner, dict):
        kind = inner.get('type') or (next(iter(inner)) if len(inner) == 1 else None)
    elif kind is None and not isinstance(inner, str):
        kind = type(inner).__name__
    elif isinstance(inner, str):
        kind = inner
    status_code = (
        getattr(inner, 'http_status_code', None)
        or getattr(inner, 'httpStatusCode', None)
        or (inner.get('http_status_code') or inner.get('httpStatusCode') if isinstance(inner, dict) else None)
    )
    if status_code is None and isinstance(inner, dict) and len(inner) == 1:
        nested = next(iter(inner.values()))
        if isinstance(nested, dict):
            status_code = nested.get('http_status_code') or nested.get('httpStatusCode')
    return kind, status_code


def format_turn_error(label: str, turn_obj) -> str:
    error_obj = getattr(turn_obj, 'error', None)
    kind, status_code = _extract_error_info(error_obj)
    if status_code == 401 or kind == 'unauthorized':
        return f'{label}: шлюз или OpenAI отклонил авторизацию (401)'
    if status_code == 402:
        return f'{label}: недостаточно средств или исчерпан баланс ключа на шлюзе (HTTP 402)'
    if status_code == 403:
        return f'{label}: шлюз запретил доступ к модели (HTTP 403)'
    if status_code == 404 or kind == 'modelNotFound':
        return f'{label}: шлюз не нашёл модель или Responses API (HTTP 404)'
    if status_code == 429 or kind == 'usageLimitExceeded':
        return f'{label}: превышен лимит запросов или квота шлюза (HTTP 429)'
    if kind in ('httpConnectionFailed', 'responseStreamConnectionFailed', 'responseStreamDisconnected'):
        return f'{label}: ошибка соединения с модельным шлюзом'
    if kind == 'contextWindowExceeded':
        return f'{label}: превышен контекст модели'
    return f'{label}: агент не завершил работу'


async def _stop_turn(label: str, turn, client, task) -> None:
    try:
        async with asyncio.timeout(15):
            if turn is not None:
                await turn.interrupt()
            await asyncio.gather(task, return_exceptions=True)
    except Exception as exc:
        logging.getLogger('uvicorn.error').warning('%s interruption: %s', label, type(exc).__name__)
        await _close_client(label, client)
        task.cancel()
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(task, return_exceptions=True)
        except TimeoutError:
            pass


async def run_codex_turn(
    thread,
    prompt_input,
    *,
    label: str,
    progress,
    adapter: McpToolAdapter | None = None,
    effort: str | None = None,
    output_schema: dict | None = None,
    summary: str | None = 'auto',
    client=None,
    idle_timeout: int | float | None = TURN_IDLE_TIMEOUT,
) -> TurnOutcome:
    progress.begin_response()
    turn_kwargs = {}
    if effort is not None:
        turn_kwargs['effort'] = effort
    if output_schema is not None:
        turn_kwargs['output_schema'] = output_schema
    if summary is not None:
        turn_kwargs['summary'] = summary

    turn = None
    summary_text = None
    fallback_text = None
    delta_buffers = {}
    last_delta_id = None
    commentary = set()
    completed = False
    turn_status = ''
    error_detail = None
    usage = {'input_tokens': 0, 'output_tokens': 0}
    watch_task = None

    try:
        try:
            async with asyncio.timeout(TURN_START_TIMEOUT):
                turn = await thread.turn(prompt_input, **turn_kwargs)
        except TimeoutError:
            await _close_client(label, client)
            raise HTTPException(502, f'{label}: не удалось запустить шаг агента') from None
        if adapter is not None:
            adapter.turn = turn

        loop = asyncio.get_running_loop()
        idle_since = loop.time()
        timeout_limit = idle_timeout
        active_commands: set[str] = set()

        async def consume():
            nonlocal summary_text, fallback_text, last_delta_id, completed, turn_status, error_detail, usage, idle_since
            async for event in turn.stream():
                method = event.method
                data = event.payload
                meaningful = True
                if method in ('item/agentMessage/delta', 'item/reasoning/summaryTextDelta'):
                    meaningful = bool(getattr(data, 'delta', ''))
                elif method == 'error':
                    meaningful = False
                if meaningful:
                    idle_since = loop.time()
                    progress.update(last_activity_at=timestamp())
                if method in ('item/started', 'item/completed'):
                    item = data.item.root if hasattr(data.item, 'root') else data.item
                    item_type = getattr(getattr(item, 'type', None), 'value', getattr(item, 'type', None))
                    phase = getattr(getattr(item, 'phase', None), 'value', getattr(item, 'phase', None))
                    item_id = getattr(item, 'id', None)
                    if method == 'item/started':
                        progress.update(model_waiting=item_type in ('reasoning', 'agentMessage'))
                        if item_type == 'commandExecution' and item_id:
                            active_commands.add(item_id)
                        if item_type in ('commandExecution', 'fileChange', 'webSearch'):
                            labels = {
                                'commandExecution': 'Выполняет команду',
                                'fileChange': 'Изменяет файлы',
                                'webSearch': 'Ищет информацию',
                            }
                            progress.add(
                                labels[item_type],
                                description='Работает над материалами поручения; цель шага агент поясняет в комментариях.',
                            )
                        if item_type == 'agentMessage' and phase == 'commentary':
                            commentary.add(item.id)
                    else:
                        if item_id:
                            active_commands.discard(item_id)
                        if item_type == 'agentMessage':
                            if phase == 'final_answer':
                                summary_text = item.text
                            elif phase is None:
                                fallback_text = item.text
                        else:
                            progress.update(model_waiting=True)
                        if adapter is not None and (adapter.fatal_error is not None or adapter.terminal_result is not None):
                            try:
                                async with asyncio.timeout(5):
                                    await turn.interrupt()
                            except Exception:
                                pass
                            break
                elif method == 'item/agentMessage/delta':
                    if data.item_id in commentary:
                        comment = progress.summary(data.delta, ('commentary', data.item_id))
                        if comment:
                            progress.update(agent_comment=comment)
                    else:
                        delta_buffers[data.item_id] = delta_buffers.get(data.item_id, '') + data.delta
                        last_delta_id = data.item_id
                elif method == 'item/reasoning/summaryTextDelta':
                    progress.reasoning_delta(data.delta, (data.item_id, data.summary_index))
                elif method == 'thread/tokenUsage/updated':
                    usage_obj = data.token_usage.total
                    usage = {'input_tokens': usage_obj.input_tokens, 'output_tokens': usage_obj.output_tokens}
                    progress.update(token_usage=dict(usage), current_usage_known=True)
                elif method == 'error':
                    kind, status_code = _extract_error_info(getattr(data, 'error', None))
                    will_retry = getattr(data, 'will_retry', getattr(data, 'willRetry', None))
                    logging.getLogger('uvicorn.error').warning(
                        '%s: codex stream error (kind=%s, status=%s, retry=%s)',
                        label, kind, status_code, will_retry,
                    )
                    if will_retry is False and not error_detail:
                        error_detail = format_turn_error(label, data)
                elif method == 'turn/completed':
                    turn_status = getattr(getattr(data.turn, 'status', None), 'value', None) or str(getattr(data.turn, 'status', ''))
                    completed = turn_status == 'completed'
                    if not completed:
                        error_detail = format_turn_error(label, data.turn)

        async def watchdog():
            nonlocal idle_since, timeout_limit
            while True:
                await asyncio.sleep(5)
                waiting_user = bool(
                    progress.record.get('pending_question')
                    or (adapter is not None and adapter.waiting_for_user)
                )
                if waiting_user:
                    idle_since = loop.time()
                    continue
                busy = bool(
                    active_commands
                    or (adapter is not None and adapter.active_calls > 0)
                )
                limit = max(idle_timeout, BUSY_IDLE_TIMEOUT) if busy else idle_timeout
                if loop.time() - idle_since >= limit:
                    timeout_limit = limit
                    return

        consume_task = asyncio.create_task(consume())
        watch_task = asyncio.create_task(watchdog()) if idle_timeout else None
        try:
            if watch_task is None:
                await asyncio.shield(consume_task)
            else:
                done, _ = await asyncio.wait({consume_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
                if watch_task in done and not consume_task.done():
                    active_tools = list(adapter.active_tools) if adapter is not None else []
                    logging.getLogger('uvicorn.error').warning(
                        '%s: turn stream idle timeout after %ss (active_tools=%s, active_commands=%d)',
                        label,
                        timeout_limit,
                        active_tools,
                        len(active_commands),
                    )
                    progress.add(f'Модель не передавала новых данных {max(1, int(timeout_limit // 60))} мин — запуск остановлен')
                    await _stop_turn(label, turn, client, consume_task)
                    raise HTTPException(502, f'{label}: модель не передавала новых данных слишком долго')
                await consume_task
        except asyncio.CancelledError:
            await _stop_turn(label, turn, client, consume_task)
            raise
    finally:
        if watch_task is not None and not watch_task.done():
            watch_task.cancel()
            await asyncio.gather(watch_task, return_exceptions=True)
        if adapter is not None:
            adapter.turn = None
        progress.update(model_waiting=False)

    if adapter is not None and adapter.fatal_error is not None:
        raise adapter.fatal_error

    text = summary_text or fallback_text or (delta_buffers.get(last_delta_id, '') if last_delta_id else '')
    return TurnOutcome(
        text=text or '',
        completed=completed,
        status=turn_status,
        error_detail=error_detail,
        usage=usage,
        terminal_result=adapter.terminal_result if adapter is not None else None,
    )


async def check_connection(label: str, *, config: dict | None, api_key: str = '', default_mode: str = 'api_key') -> dict:
    auth_mode, key, base_url, model = normalize_auth(config, api_key, default_mode=default_mode, label=label)
    sandbox = Sandbox.full_access if CONTAINER_SANDBOX else Sandbox.read_only
    try:
        async with scratch_env(writable=False) as (work_dir, shell_policy), open_codex(
            label,
            auth_mode=auth_mode,
            api_key=key,
            base_url=base_url,
            allow_shell=False,
            web_search='disabled',
        ) as (client, thread_provider):
            thread = await start_codex_thread(
                client,
                label,
                cwd=str(work_dir),
                sandbox=sandbox,
                ephemeral=True,
                approval_mode=ApprovalMode.deny_all,
                model=model or None,
                model_provider=thread_provider,
                config=shell_policy,
            )
            async with asyncio.timeout(90):
                await thread.run('Ответь только OK.')
        return {'ok': True}
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(502, f'{label}: проверка подключения превысила время ожидания') from None
    except Exception as exc:
        logging.getLogger('uvicorn.error').warning('%s check failed: %s', label, type(exc).__name__)
        raise HTTPException(502, f'{label}: не удалось выполнить проверочный запрос ({type(exc).__name__})') from None
