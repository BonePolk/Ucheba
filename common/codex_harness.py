"""Общий Codex harness (openai-codex SDK + изолированный MCP) для всех агентов."""
import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import types
from typing import Any

from fastapi import HTTPException
import httpx
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox

from common.browser_client import browser_agent, browser_run_id, browser_task_id
from common.browser_tools import BROWSER_NAMES
from common.identity import current_user_id, user_id
from common.progress import timestamp
from common.provider import provider_url

HOME = Path('/home/codex')
WORKER_BIN = '/usr/local/bin/codex-worker'
MCP_STDIO_SCRIPT = '/app/common/mcp_stdio.py'
CONTAINER_SANDBOX = os.environ.get('ARRODES_SANDBOX', '').strip().lower() == 'container'
API_PROVIDER_ID = 'ucheba_api'
CLIENT_CLOSE_TIMEOUT = 10
MCP_SHUTDOWN_TIMEOUT = 5
TURN_START_TIMEOUT = 60
TURN_IDLE_TIMEOUT = 300
BUSY_IDLE_TIMEOUT = 600
MCP_TOOL_TIMEOUT = 180
USER_WAIT_TOOLS = frozenset({'ask_user', 'browser_wait_for_user'})
UNBOUNDED_TOOLS = frozenset({'ask_user', 'browser_wait_for_user', 'delete_silo_file'})

MODEL_CAP_TTL = 1800
MODEL_CAP_MISS_TTL = 300
MODEL_CAP_CACHE_SIZE = 256
MODEL_PROBE_TIMEOUT = 4.0

_CTX_WINDOW_KEYS = (
    'context_window', 'contextWindow', 'context_length', 'contextLength',
    'max_context_window', 'maxContextWindow', 'max_context_length',
    'max_context_tokens', 'max_model_len', 'max_position_embeddings', 'n_ctx',
)
_PROVIDER_CTX_KEYS = _CTX_WINDOW_KEYS + ('context', 'max_context')
_INPUT_TOKEN_KEYS = ('max_input_tokens', 'maxInputTokens', 'input_token_limit', 'inputTokenLimit', 'max_prompt_tokens', 'input')
_OUTPUT_TOKEN_KEYS = ('max_output_tokens', 'maxOutputTokens', 'output_token_limit', 'outputTokenLimit', 'max_completion_tokens', 'output')
_CTX_ERROR_MARKERS = ('context_length_exceeded', 'context length', 'context window', 'maximum context', 'too many tokens', 'prompt is too long', 'input exceeds')

_CAP_CACHE: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()


@dataclass(frozen=True)
class ModelCapabilities:
    model: str = ''
    context_window: int | None = None
    theoretical_context_window: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    source: str = 'unknown'

    @property
    def effective_limit(self) -> int | None:
        vals = [v for v in (self.context_window, self.max_input_tokens) if type(v) is int and v > 0]
        return min(vals) if vals else None


def _safe_int(val, *, minimum: int = 1, maximum: int = 10_000_000) -> int | None:
    return val if type(val) is int and minimum <= val <= maximum else None


def _safe_str(val) -> str:
    return val.strip() if type(val) is str else ''


def _field(obj, *names):
    if obj is None:
        return None
    extra = getattr(obj, 'model_extra', None)
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
            continue
        if isinstance(extra, dict) and name in extra and extra[name] is not None:
            return extra[name]
        val = getattr(obj, name, None)
        if val is not None:
            return val
    return None


def _ints(obj, keys: tuple[str, ...], minimum: int) -> list[int]:
    if obj is None:
        return []
    return [val for key in keys if (val := _safe_int(_field(obj, key), minimum=minimum)) is not None]


def extract_model_limits(raw) -> dict:
    """Извлекает подтверждённые ограничения контекста и вывода из метаданных модели или провайдера."""
    model_id = _safe_str(_field(raw, 'id', 'model'))
    arch = _field(raw, 'architecture')
    nested = [sub for name in ('top_provider', 'limits', 'capabilities') if (sub := _field(raw, name)) is not None]

    top_ctx = _ints(raw, _CTX_WINDOW_KEYS, 4096) + _ints(arch, _CTX_WINDOW_KEYS, 4096)
    nested_ctx = [val for sub in nested for val in _ints(sub, _PROVIDER_CTX_KEYS, 4096)]
    ctx_candidates = ([min(top_ctx)] if top_ctx else []) + nested_ctx
    theo_candidates = ([max(top_ctx)] if top_ctx else []) + nested_ctx

    in_vals = _ints(raw, _INPUT_TOKEN_KEYS[:5], 4096) + _ints(arch, _INPUT_TOKEN_KEYS[:5], 4096)
    in_vals.extend(val for sub in nested for val in _ints(sub, _INPUT_TOKEN_KEYS, 4096))

    out_vals = _ints(raw, _OUTPUT_TOKEN_KEYS[:5], 256) + _ints(arch, _OUTPUT_TOKEN_KEYS[:5], 256)
    out_vals.extend(val for sub in nested for val in _ints(sub, _OUTPUT_TOKEN_KEYS, 256))

    return {
        'model': model_id,
        'context_window': min(ctx_candidates) if ctx_candidates else None,
        'theoretical_context_window': max(theo_candidates) if theo_candidates else None,
        'max_input_tokens': min(in_vals) if in_vals else None,
        'max_output_tokens': min(out_vals) if out_vals else None,
    }


def _cache_put(key: tuple, value, ttl: float) -> None:
    _CAP_CACHE[key] = (time.monotonic() + ttl, value)
    _CAP_CACHE.move_to_end(key)
    while len(_CAP_CACHE) > MODEL_CAP_CACHE_SIZE:
        _CAP_CACHE.popitem(last=False)


def _fresh_get(key: tuple):
    item = _CAP_CACHE.get(key)
    if item is None:
        return None
    if item[0] <= time.monotonic():
        _CAP_CACHE.pop(key, None)
        return None
    _CAP_CACHE.move_to_end(key)
    return item[1]


def _lookup_cached_model(owner: int, source_kind: str, scope: str, model: str) -> dict | None:
    if not model:
        return None
    exact = _fresh_get(('model', owner, source_kind, scope, model))
    if exact is not None:
        return exact
    tail = model.rsplit('/', 1)[-1]
    now = time.monotonic()
    for key, (exp, val) in list(_CAP_CACHE.items()):
        if exp <= now:
            _CAP_CACHE.pop(key, None)
            continue
        if key[0] == 'model' and key[1:4] == (owner, source_kind, scope) and key[4].rsplit('/', 1)[-1] == tail:
            return val
    return None


def cache_provider_models(base_url: str, models_iter, *, source_kind: str = 'api_key', mark_probed: bool = True) -> None:
    owner = user_id()
    scope = (base_url or '').strip().rstrip('/')
    has_any = False
    for raw in models_iter or ():
        limits = extract_model_limits(raw)
        mid = limits['model']
        if not mid:
            continue
        has_any = True
        has_limits = any(limits[k] is not None for k in ('context_window', 'max_input_tokens', 'max_output_tokens'))
        _cache_put(('model', owner, source_kind, scope, mid), limits, MODEL_CAP_TTL if has_limits else MODEL_CAP_MISS_TTL)
    if mark_probed:
        _cache_put(('probed', owner, source_kind, scope), True, MODEL_CAP_TTL if has_any else MODEL_CAP_MISS_TTL)


async def fetch_provider_model_metadata(base_url: str, api_key: str, model: str) -> dict | None:
    owner = user_id()
    scope = (base_url or '').strip().rstrip('/')
    if not scope or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=MODEL_PROBE_TIMEOUT) as client:
            response = await client.get(
                f'{scope}/models',
                headers={'Authorization': f'Bearer {api_key}'},
            )
        if response.is_success:
            payload = response.json()
            items = payload.get('data') if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
            if isinstance(items, list):
                cache_provider_models(scope, items, source_kind='api_key')
                return _lookup_cached_model(owner, 'api_key', scope, model)
        else:
            logging.getLogger('uvicorn.error').warning('Provider /models metadata probe returned HTTP %s', response.status_code)
    except Exception as exc:
        logging.getLogger('uvicorn.error').warning('Provider /models metadata probe failed: %s', type(exc).__name__)
    _cache_put(('probed', owner, 'api_key', scope), True, MODEL_CAP_MISS_TTL)
    return None


def record_runtime_context_limit(
    auth_mode: str,
    base_url: str,
    model: str,
    limit_tokens: int,
    *,
    source: str = 'runtime',
    tighten_only: bool = False,
) -> None:
    """Записывает только подтверждённое рантаймом ограничение контекста модели (никогда не эвристику ошибки)."""
    limit = _safe_int(limit_tokens, minimum=4096)
    if limit is None:
        return
    key = ('runtime', user_id(), (auth_mode or '').strip(), (base_url or '').strip().rstrip('/'), (model or '').strip())
    existing = _fresh_get(key)
    if tighten_only and existing is not None and existing[0] <= limit:
        return
    _cache_put(key, (limit, source), MODEL_CAP_TTL)


def clear_model_capability_cache() -> None:
    _CAP_CACHE.clear()


async def _probe_codex_models(client, active_model: str, auth_mode: str) -> dict | None:
    if client is None:
        return None
    owner = user_id()
    scope = (auth_mode or '').strip()
    if _fresh_get(('probed', owner, 'codex', scope)) is None:
        models_fn = getattr(client, 'models', None)
        if callable(models_fn):
            try:
                res = models_fn()
                page = await asyncio.wait_for(res, timeout=MODEL_PROBE_TIMEOUT) if inspect.isawaitable(res) else res
                data = getattr(page, 'data', None)
                if isinstance(data, (list, tuple)):
                    cache_provider_models(scope, data, source_kind='codex')
                    if not active_model:
                        default_item = next(
                            (m for m in data if _field(m, 'is_default', 'isDefault') is True),
                            data[0] if len(data) == 1 else None,
                        )
                        if default_item is not None:
                            return extract_model_limits(default_item)
                else:
                    _cache_put(('probed', owner, 'codex', scope), True, MODEL_CAP_MISS_TTL)
            except Exception:
                _cache_put(('probed', owner, 'codex', scope), True, MODEL_CAP_MISS_TTL)
    return _lookup_cached_model(owner, 'codex', scope, active_model)


async def detect_model_capabilities(
    *,
    auth_mode: str,
    base_url: str = '',
    api_key: str = '',
    model: str = '',
    config: dict | None = None,
    client=None,
    thread=None,
) -> ModelCapabilities:
    """Определяет реальное и теоретическое контекстное окно модели без фиктивных значений."""
    owner = user_id()
    mode = (auth_mode or 'api_key').strip()
    scope = (base_url or '').strip().rstrip('/')
    active_model = _safe_str(_field(thread, 'model')) or _safe_str(model)
    cfg = config or {}
    cfg_cap = {
        'context_window': _safe_int(cfg.get('context_window'), minimum=4096),
        'max_input_tokens': _safe_int(cfg.get('max_input_tokens'), minimum=4096),
        'max_output_tokens': _safe_int(cfg.get('max_output_tokens'), minimum=256),
    }

    thread_cap = extract_model_limits(thread) if thread is not None else {}
    codex_cap = await _probe_codex_models(client, active_model, mode) or {}
    provider_cap = {}
    if mode == 'api_key' and scope:
        provider_cap = _lookup_cached_model(owner, 'api_key', scope, active_model) or {}
        if not provider_cap and api_key and _fresh_get(('probed', owner, 'api_key', scope)) is None:
            provider_cap = await fetch_provider_model_metadata(scope, api_key, active_model) or {}

    rt_entry = _fresh_get(('runtime', owner, mode, scope, active_model))
    if rt_entry is None and active_model != _safe_str(model):
        rt_entry = _fresh_get(('runtime', owner, mode, scope, _safe_str(model)))

    named_caps = (('config', cfg_cap), ('provider', provider_cap), ('codex', codex_cap), ('thread', thread_cap))
    ctx_sources = [(name, val) for name, cap in named_caps if (val := cap.get('context_window')) is not None]
    if rt_entry is not None:
        ctx_sources.append((rt_entry[1], rt_entry[0]))

    effective_ctx = min(val for _, val in ctx_sources) if ctx_sources else None
    theo_vals = [
        val
        for _, cap in named_caps[1:]
        for val in (cap.get('theoretical_context_window'), cap.get('context_window'))
        if val is not None
    ]
    theoretical_ctx = max(theo_vals) if theo_vals else effective_ctx

    in_sources = [(name, val) for name, cap in named_caps if (val := cap.get('max_input_tokens')) is not None]
    out_vals = [val for _, cap in named_caps if (val := cap.get('max_output_tokens')) is not None]
    min_in = min(val for _, val in in_sources) if in_sources else None
    limiting = [name for name, val in ctx_sources if val == effective_ctx] if ctx_sources else [
        name for name, val in in_sources if val == min_in
    ]

    return ModelCapabilities(
        model=active_model,
        context_window=effective_ctx,
        theoretical_context_window=theoretical_ctx,
        max_input_tokens=min_in,
        max_output_tokens=min(out_vals) if out_vals else None,
        source='+'.join(dict.fromkeys(limiting)) if limiting else 'unknown',
    )


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


def _force_kill_client(label: str, client) -> None:
    if client is None:
        return
    queue = deque([(client, 0)])
    seen = {id(client)}
    killed = 0
    primitives = (str, bytes, bytearray, int, float, bool, type, types.ModuleType)

    def enqueue(value, next_depth: int) -> None:
        if value is None or isinstance(value, primitives) or next_depth > 4 or len(seen) >= 64:
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        queue.append((value, next_depth))

    while queue:
        obj, depth = queue.popleft()
        if isinstance(obj, subprocess.Popen):
            if obj.poll() is None:
                try:
                    obj.kill()
                    killed += 1
                except OSError:
                    pass
            continue
        if isinstance(obj, asyncio.subprocess.Process):
            if obj.returncode is None:
                try:
                    obj.kill()
                    killed += 1
                except ProcessLookupError:
                    pass
            continue
        if depth >= 4 or len(seen) >= 64:
            continue
        if isinstance(obj, dict):
            for value in obj.values():
                enqueue(value, depth + 1)
            continue
        if isinstance(obj, (list, tuple, set, frozenset)):
            for value in obj:
                enqueue(value, depth + 1)
            continue
        attrs = getattr(obj, '__dict__', None)
        if isinstance(attrs, dict):
            for value in attrs.values():
                enqueue(value, depth + 1)
        for cls in type(obj).__mro__:
            slots = cls.__dict__.get('__slots__')
            if not slots:
                continue
            slot_names = (slots,) if isinstance(slots, str) else slots
            for slot in slot_names:
                if slot in ('__dict__', '__weakref__'):
                    continue
                attr_name = f'_{cls.__name__.lstrip("_")}{slot}' if slot.startswith('__') and not slot.endswith('__') else slot
                enqueue(getattr(obj, attr_name, None), depth + 1)

    if killed:
        logging.getLogger('uvicorn.error').warning('%s: force-killed %d hung codex subprocess(es)', label, killed)


async def _close_client(label: str, client) -> None:
    if client is None:
        return
    try:
        async with asyncio.timeout(CLIENT_CLOSE_TIMEOUT):
            await client.close()
    except TimeoutError:
        logging.getLogger('uvicorn.error').warning('%s: codex client close timed out', label)
        _force_kill_client(label, client)
    except Exception:
        _force_kill_client(label, client)


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
        self.last_usage: dict | None = None
        self.context_window: int | None = None
        self.compaction_count: int = 0
        self.on_token_usage = None
        self.on_compaction = None

    @property
    def waiting_for_user(self) -> bool:
        return any(name in USER_WAIT_TOOLS for name in self.active_tools) or bool(
            self.progress is not None and self.progress.record.get('browser_waiting')
        )

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
                if name in UNBOUNDED_TOOLS or name in BROWSER_NAMES:
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
    agent_name = browser_agent.get()
    task_id = browser_task_id.get()
    handlers = set()
    writers = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        writers.add(writer)
        token = current_user_id.set(owner)
        browser_token = browser_run_id.set(run_id)
        agent_token = browser_agent.set(agent_name)
        task_token = browser_task_id.set(task_id)
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
                browser_task_id.reset(task_token)
                browser_agent.reset(agent_token)
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
    last_usage: dict | None = None
    context_window: int | None = None
    compacted: bool = False
    error_kind: str | None = None


def _extract_error_info(error_obj):
    if error_obj is None:
        return None, None
    info = _field(error_obj, 'codex_error_info', 'codexErrorInfo')
    inner = _field(info, 'root') or info
    kind = None
    status_code = None
    if inner is not None:
        if isinstance(inner, str):
            kind = inner
        else:
            kind = _field(inner, 'type') or (
                next(iter(inner)) if isinstance(inner, dict) and len(inner) == 1 else type(inner).__name__
            )
        status_code = _field(inner, 'http_status_code', 'httpStatusCode')
        if status_code is None and isinstance(inner, dict) and len(inner) == 1:
            nested = next(iter(inner.values()))
            if isinstance(nested, dict):
                status_code = nested.get('http_status_code') or nested.get('httpStatusCode')
    if kind != 'contextWindowExceeded':
        msg = _field(inner, 'message') or _field(error_obj, 'message')
        if status_code == 413 or (type(msg) is str and any(m in msg.lower() for m in _CTX_ERROR_MARKERS)):
            kind = 'contextWindowExceeded'
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
    error_kind = None
    usage = {'input_tokens': 0, 'output_tokens': 0}
    last_usage = None
    runtime_context_window = None
    compacted = False
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
        last_method = ''
        last_item_type = ''

        async def consume():
            nonlocal summary_text, fallback_text, last_delta_id, completed, turn_status, error_detail, error_kind, usage, last_usage, runtime_context_window, compacted, idle_since, last_method, last_item_type
            async for event in turn.stream():
                method = event.method
                last_method = method
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
                    last_item_type = str(item_type or '')
                    phase = getattr(getattr(item, 'phase', None), 'value', getattr(item, 'phase', None))
                    item_id = getattr(item, 'id', None)
                    if method == 'item/started':
                        progress.update(model_waiting=item_type in ('reasoning', 'agentMessage'))
                        if item_type == 'commandExecution' and item_id:
                            active_commands.add(item_id)
                        if item_type == 'contextCompaction':
                            compacted = True
                            progress.update(current_action='Сжимает контекст диалога')
                        if item_type in ('commandExecution', 'fileChange', 'webSearch'):
                            labels = {
                                'commandExecution': 'Выполняет команду',
                                'fileChange': 'Изменяет файлы',
                                'webSearch': 'Ищет информацию',
                            }
                            progress.add(labels[item_type])
                        if item_type == 'agentMessage' and phase == 'commentary':
                            commentary.add(item.id)
                    else:
                        if item_id:
                            active_commands.discard(item_id)
                        if item_type == 'contextCompaction':
                            compacted = True
                            if adapter is not None:
                                adapter.compaction_count += 1
                                if callable(adapter.on_compaction):
                                    adapter.on_compaction()
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
                elif method == 'thread/compacted':
                    compacted = True
                    progress.update(current_action='Сжимает контекст диалога')
                    if adapter is not None:
                        adapter.compaction_count += 1
                        if callable(adapter.on_compaction):
                            adapter.on_compaction()
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
                    tu = _field(data, 'token_usage', 'tokenUsage')
                    total_obj = _field(tu, 'total')
                    last_obj = _field(tu, 'last')
                    if total_obj is not None:
                        in_tok = _safe_int(_field(total_obj, 'input_tokens', 'inputTokens'), minimum=0)
                        out_tok = _safe_int(_field(total_obj, 'output_tokens', 'outputTokens'), minimum=0)
                        if in_tok is not None or out_tok is not None:
                            usage = {'input_tokens': in_tok or 0, 'output_tokens': out_tok or 0}
                            progress.update(token_usage=dict(usage), current_usage_known=True)
                    if last_obj is not None:
                        l_in = _safe_int(_field(last_obj, 'input_tokens', 'inputTokens'), minimum=0)
                        l_out = _safe_int(_field(last_obj, 'output_tokens', 'outputTokens'), minimum=0)
                        l_tot = _safe_int(_field(last_obj, 'total_tokens', 'totalTokens'), minimum=0)
                        if l_in is not None or l_tot is not None:
                            last_usage = {
                                'input_tokens': l_in if l_in is not None else max(0, (l_tot or 0) - (l_out or 0)),
                                'output_tokens': l_out or 0,
                                'total_tokens': l_tot if l_tot is not None else (l_in or 0) + (l_out or 0),
                            }
                    ctx_win = _safe_int(_field(tu, 'model_context_window', 'modelContextWindow'), minimum=4096)
                    if ctx_win is not None:
                        runtime_context_window = ctx_win
                    if adapter is not None:
                        if last_usage is not None:
                            adapter.last_usage = dict(last_usage)
                        if runtime_context_window is not None:
                            adapter.context_window = runtime_context_window
                        if callable(adapter.on_token_usage):
                            adapter.on_token_usage(last_usage=last_usage, total_usage=usage, context_window=runtime_context_window)
                elif method == 'error':
                    kind, status_code = _extract_error_info(getattr(data, 'error', None))
                    will_retry = getattr(data, 'will_retry', getattr(data, 'willRetry', None))
                    logging.getLogger('uvicorn.error').warning(
                        '%s: codex stream error (kind=%s, status=%s, retry=%s)',
                        label, kind, status_code, will_retry,
                    )
                    if will_retry is False and not error_detail:
                        error_detail = format_turn_error(label, data)
                        error_kind = kind
                elif method == 'turn/completed':
                    turn_status = getattr(getattr(data.turn, 'status', None), 'value', None) or str(getattr(data.turn, 'status', ''))
                    completed = turn_status == 'completed'
                    if not completed:
                        error_detail = format_turn_error(label, data.turn)
                        turn_kind, _ = _extract_error_info(getattr(data.turn, 'error', None))
                        if turn_kind:
                            error_kind = turn_kind
                    break

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
                        '%s: turn stream idle timeout after %ss (last=%s/%s, active_tools=%s, active_commands=%d)',
                        label,
                        timeout_limit,
                        last_method or '-',
                        last_item_type or '-',
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
        last_usage=last_usage,
        context_window=runtime_context_window,
        compacted=compacted,
        error_kind=error_kind,
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
