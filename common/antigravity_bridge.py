"""Встроенный loopback OpenAI Responses API мост поверх нативного клиента Cloud Code."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
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
_EPHEMERAL_KEY_SEED = secrets.token_bytes(32)
_MAX_PERSISTED_RESPONSES = 24
_MAX_PERSISTED_BYTES = 512 * 1024
_BRIDGE_LOG = logging.getLogger('uvicorn.error')

_CODEX_HARNESS_TOOLS = frozenset({
    'list_mcp_resources',
    'list_mcp_resource_templates',
    'read_mcp_resource',
    'request_user_input',
    'view_image',
    'get_goal',
    'create_goal',
    'update_goal',
})
_CODEX_HARNESS_NAMESPACES = frozenset({'multi_agent_v1'})
_HARNESS_STRIP_TAGS_BASE = ('skills_instructions', 'collaboration_mode', 'apps_instructions', 'system-conventions')
_ENV_CONTEXT_BLOCK_RE = re.compile(r'<environment_context\b[^>]*>.*?</environment_context>', re.S | re.I)
_CODEX_PREAMBLE_PREFIX = 'You are a coding agent running in the Codex CLI'


def _strip_exact_xml_blocks(text: str, tags: tuple[str, ...]) -> str:
    """Структурно удаляет только заданные корневые XML-секции Codex harness по точным парам тегов."""
    if not text or not tags:
        return text
    out = text
    for tag in tags:
        open_pat = re.compile(rf'(?:^|\n)[ \t]*<{re.escape(tag)}(?:\s[^>\n]*)?>', re.I)
        close_tag = f'</{tag}>'
        close_pat = re.compile(rf'</{re.escape(tag)}>[ \t]*(?:\n|$)', re.I)
        while True:
            m_open = open_pat.search(out)
            if not m_open:
                break
            m_close = close_pat.search(out, m_open.end())
            if not m_close:
                break
            start = m_open.start()
            # Сохраняем ведущий перенос строки, если удаляемый блок начинается не в 0.
            prefix = '\n' if start > 0 and out[start] == '\n' else ''
            out = out[:start] + prefix + out[m_close.end():]
    return out


def _sanitize_system_text(
    text: str,
    *,
    has_custom_agent_prompt: bool,
    preserve_permissions: bool = False,
) -> str:
    raw = (text or '').strip()
    if not raw:
        return ''
    if raw.startswith(_CODEX_PREAMBLE_PREFIX):
        if has_custom_agent_prompt:
            return ''
        parts = raw.split('\n\n', 1)
        raw = ('You are a helpful engineering assistant.\n\n' + parts[1]) if len(parts) > 1 else 'You are a helpful engineering assistant.'
    tags = _HARNESS_STRIP_TAGS_BASE if preserve_permissions else (*_HARNESS_STRIP_TAGS_BASE, 'permissions instructions')
    cleaned = _strip_exact_xml_blocks(raw, tags)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned

_ALLOWED_FINISH_REASONS = frozenset({'STOP', 'MAX_TOKENS', 'TOOL_CALLS', 'FUNCTION_CALL'})
_FAILED_FINISH_REASONS = frozenset({
    'SAFETY',
    'RECITATION',
    'BLOCKLIST',
    'PROHIBITED_CONTENT',
    'SPII',
    'IMAGE_SAFETY',
    'MALFORMED_FUNCTION_CALL',
    'UNEXPECTED_TOOL_CALL',
    'TOO_MANY_TOOL_CALLS',
})


def _bridge_fernet(owner_id: int, connection_id: str = '') -> Fernet:
    """Персональный ключ Fernet для защищённых метаданных моста, изолированный по владельцу и OAuth-сессии."""
    import base64
    secret = (os.getenv('INTERNAL_SECRET') or os.getenv('INTEGRATION_ENCRYPTION_KEY') or '').encode('utf-8')
    if not secret:
        secret = _EPHEMERAL_KEY_SEED
    scope_cid = (connection_id or '').strip()
    digest = hashlib.sha256(secret + f':antigravity-bridge-v2:{owner_id}:{scope_cid}'.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encode_encrypted_meta(payload: dict, *, owner_id: int, connection_id: str = '') -> str:
    data = dict(payload, uid=owner_id, cid=(connection_id or '').strip())
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return _bridge_fernet(owner_id, connection_id).encrypt(raw).decode('ascii')


def _decode_encrypted_meta(token: str | None, *, owner_id: int, connection_id: str = '') -> dict:
    if not isinstance(token, str) or not token.strip():
        return {}
    expected_cid = (connection_id or '').strip()
    try:
        raw = _bridge_fernet(owner_id, expected_cid).decrypt(token.strip().encode('ascii'))
        obj = json.loads(raw.decode('utf-8'))
        if not isinstance(obj, dict) or obj.get('uid') != owner_id:
            return {}
        if str(obj.get('cid') or '') != expected_cid:
            return {}
        return obj
    except (InvalidToken, ValueError, UnicodeError):
        return {}


def decode_thought_signature(token: str | None, *, owner_id: int, connection_id: str = '') -> str:
    """Расшифровывает первую thoughtSignature, привязанную к owner_id и connection_id."""
    meta = _decode_encrypted_meta(token, owner_id=owner_id, connection_id=connection_id)
    for bucket in ('call_sigs', 'item_sigs'):
        sigs = meta.get(bucket)
        if isinstance(sigs, dict):
            for val in sigs.values():
                if isinstance(val, str) and val:
                    return val
    return ''


def _sanitize_scope_slug(agent_scope: str) -> str:
    slug = re.sub(r'[^a-z0-9_-]+', '_', (agent_scope or 'default').strip().lower()).strip('_')
    return slug[:48] or 'default'


def _bridge_state_file(owner_id: int, *, agent_scope: str = 'default', connection_id: str = '') -> Path:
    env_dir = (os.getenv('UCHEBA_BRIDGE_STATE_DIR') or '').strip()
    if env_dir:
        base = Path(env_dir)
    else:
        codex_root = Path('/home/codex/.codex')
        base = codex_root / 'bridge-state' if codex_root.is_dir() else Path(tempfile.gettempdir()) / 'ucheba-bridge-state'
    slug = _sanitize_scope_slug(agent_scope)
    cid = (connection_id or '').strip()
    file_tag = f'conn_{hashlib.sha256(cid.encode("utf-8")).hexdigest()[:24]}.enc' if cid else 'default.enc'
    return base / str(owner_id) / slug / file_tag


def _truncate_text(val: str, limit: int) -> str:
    if len(val) <= limit:
        return val
    suffix = ' ... [обрезано]'
    return val[: max(32, limit - len(suffix))] + suffix


def _strip_heavy_blobs(item, *, max_text_chars: int = 16384):
    """Удаляет сырые base64-изображения и ограничивает слишком длинные поля перед сохранением на диск."""
    if not isinstance(item, dict):
        return item
    copy = dict(item)
    itype = copy.get('type')
    if itype in ('message', None):
        content = copy.get('content')
        if isinstance(content, str):
            copy['content'] = _truncate_text(content, max_text_chars)
        elif isinstance(content, list):
            cleaned_blocks = []
            for b in content:
                if not isinstance(b, dict):
                    cleaned_blocks.append(b)
                elif b.get('type') == 'input_image':
                    cleaned_blocks.append({'type': 'input_text', 'text': '[изображение]'})
                elif isinstance(b.get('text'), str):
                    nb = dict(b)
                    nb['text'] = _truncate_text(b['text'], max_text_chars)
                    cleaned_blocks.append(nb)
                else:
                    cleaned_blocks.append(b)
            copy['content'] = cleaned_blocks
    elif itype in ('function_call_output', 'custom_tool_call_output'):
        out = copy.get('output')
        if isinstance(out, str):
            copy['output'] = _truncate_text(out, max_text_chars)
        elif isinstance(out, dict):
            dumped = json.dumps(out, ensure_ascii=False, separators=(',', ':'))
            if len(dumped) > max_text_chars:
                copy['output'] = _truncate_text(dumped, max_text_chars)
        elif isinstance(out, list):
            cleaned_out = []
            for b in out:
                if not isinstance(b, dict):
                    cleaned_out.append(b)
                elif b.get('type') == 'input_image':
                    cleaned_out.append({'type': 'input_text', 'text': '[изображение]'})
                elif isinstance(b.get('text'), str):
                    nb = dict(b)
                    nb['text'] = _truncate_text(b['text'], max_text_chars)
                    cleaned_out.append(nb)
                else:
                    cleaned_out.append(b)
            copy['output'] = cleaned_out
    elif itype == 'function_call' and isinstance(copy.get('arguments'), str):
        copy['arguments'] = _truncate_text(copy['arguments'], max_text_chars)
    elif itype == 'custom_tool_call' and isinstance(copy.get('input'), str):
        copy['input'] = _truncate_text(copy['input'], max_text_chars)
    return copy


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


def _append_part(contents: list[dict], role: str, part: dict) -> None:
    if contents and contents[-1]['role'] == role:
        contents[-1]['parts'].append(part)
    else:
        contents.append({'role': role, 'parts': [part]})


class AntigravityBridgeSession:
    """Хранит контекст вызовов, thoughtSignature и сессию токена для одного пользователя, агента и OAuth-сессии."""

    def __init__(
        self,
        *,
        owner_id: int,
        cloudcode_url: str = '',
        state_path: Path | None = None,
        error_sink: dict | None = None,
        agent_scope: str = 'default',
        connection_id: str = '',
    ):
        self.owner_id = owner_id
        self.agent_scope = _sanitize_scope_slug(agent_scope)
        self.connection_id = (connection_id or '').strip()
        self.explicit_state_path = state_path
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
        self.state_path = state_path or _bridge_state_file(
            owner_id,
            agent_scope=self.agent_scope,
            connection_id=self.connection_id,
        )
        self.session_lock = asyncio.Lock()
        self.session_id: str = str(uuid.uuid4())
        self.error_sink = error_sink
        self.last_error: dict | None = None
        self.last_persistence_error: str | None = None
        self._load_persisted_state()

    def clear_error(self) -> None:
        self.last_error = None
        if isinstance(self.error_sink, dict):
            self.error_sink.clear()

    def record_error(self, exc: AntigravityError) -> None:
        info = {
            'status_code': exc.status_code,
            'code': exc.error_status or str(exc.status_code or 'error'),
            'message': exc.user_message,
            'retryable': bool(exc.retryable),
        }
        self.last_error = info
        if isinstance(self.error_sink, dict):
            self.error_sink.clear()
            self.error_sink.update(info)

    def bind_connection(self, connection_id: str) -> None:
        clean_cid = (connection_id or '').strip()
        if not clean_cid or clean_cid == self.connection_id:
            return
        self.connection_id = clean_cid
        self.call_meta.clear()
        self.item_signatures.clear()
        self.responses_history.clear()
        if self.explicit_state_path is None:
            self.state_path = _bridge_state_file(
                self.owner_id,
                agent_scope=self.agent_scope,
                connection_id=self.connection_id,
            )
        self._load_persisted_state()

    def _decrypt_state_blob(self, raw_token: str) -> dict | None:
        raw = _bridge_fernet(self.owner_id, self.connection_id).decrypt(raw_token.encode('ascii'))
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict) or data.get('uid') != self.owner_id:
            return None
        if str(data.get('cid') or '') != self.connection_id:
            return None
        return data

    def _load_persisted_state(self) -> None:
        try:
            if not self.state_path.is_file():
                return
            lock_path = Path(f'{self.state_path}.lock')
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH)
                if not self.state_path.is_file():
                    return
                raw_token = self.state_path.read_text(encoding='ascii').strip()
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            if not raw_token:
                return
            data = self._decrypt_state_blob(raw_token)
            if data is None:
                return
            entries = data.get('responses')
            if isinstance(entries, dict):
                for rid, entry in list(entries.items())[-_MAX_PERSISTED_RESPONSES:]:
                    if isinstance(rid, str) and isinstance(entry, dict) and isinstance(entry.get('history'), list):
                        self.responses_history[rid] = entry['history']
                        for cid, cmeta in (entry.get('call_meta') or {}).items():
                            if isinstance(cid, str) and isinstance(cmeta, dict):
                                self.call_meta.setdefault(cid, cmeta)
            self.last_persistence_error = None
        except InvalidToken:
            self.last_persistence_error = 'history_invalid_token_or_account_changed'
        except (OSError, ValueError, UnicodeError) as exc:
            self.last_persistence_error = f'history_read_failed:{type(exc).__name__}'
            _BRIDGE_LOG.warning(
                'Antigravity bridge history load failed (owner=%s agent=%s err=%s)',
                self.owner_id, self.agent_scope, type(exc).__name__,
            )

    def _pack_persisted_payload(self, merged_responses: dict[str, dict], merged_call_meta: dict[str, dict]) -> bytes | None:
        items = list(merged_responses.items())[-_MAX_PERSISTED_RESPONSES:]
        while items:
            for max_chars in (16384, 4096, 1024, 256):
                packed_entries = {}
                for rid, rec in items:
                    hist = rec.get('history') if isinstance(rec, dict) else rec
                    clean_hist = [_strip_heavy_blobs(h, max_text_chars=max_chars) for h in (hist or [])]
                    used_cids = {
                        str(h.get('call_id') or h.get('id') or '')
                        for h in clean_hist
                        if isinstance(h, dict) and h.get('type') in ('function_call', 'custom_tool_call')
                    }
                    entry_cmeta = dict(rec.get('call_meta') or {}) if isinstance(rec, dict) else {}
                    for cid in used_cids:
                        if cid in merged_call_meta:
                            entry_cmeta[cid] = merged_call_meta[cid]
                    packed_entries[rid] = {
                        'history': clean_hist,
                        'call_meta': {cid: entry_cmeta[cid] for cid in used_cids if cid in entry_cmeta},
                    }
                raw = json.dumps(
                    {'uid': self.owner_id, 'cid': self.connection_id, 'responses': packed_entries},
                    ensure_ascii=False,
                    separators=(',', ':'),
                ).encode('utf-8')
                if len(raw) <= _MAX_PERSISTED_BYTES:
                    return raw
            if len(items) > 1:
                items = items[1:]
                continue
            # Единственная запись все равно слишком велика: урезаем старейшие логические шаги,
            # не оставляя осиротевших function_call_output или фрагментов пары call/response.
            rid, rec = items[0]
            hist = list((rec.get('history') if isinstance(rec, dict) else rec) or [])
            while len(hist) > 1:
                first = hist.pop(0)
                if isinstance(first, dict) and first.get('type') in ('function_call', 'custom_tool_call'):
                    dropped_cid = str(first.get('call_id') or first.get('id') or '').strip()
                    if dropped_cid:
                        hist = [
                            h for h in hist
                            if not (
                                isinstance(h, dict)
                                and h.get('type') in ('function_call_output', 'custom_tool_call_output')
                                and str(h.get('call_id') or '').strip() == dropped_cid
                            )
                        ]
                while len(hist) > 1 and isinstance(hist[0], dict) and hist[0].get('type') in (
                    'function_call_output',
                    'custom_tool_call_output',
                    'reasoning',
                ):
                    hist.pop(0)
                if not hist:
                    break
                clean_hist = [_strip_heavy_blobs(h, max_text_chars=256) for h in hist]
                used_cids = {
                    str(h.get('call_id') or h.get('id') or '')
                    for h in clean_hist
                    if isinstance(h, dict) and h.get('type') in ('function_call', 'custom_tool_call')
                }
                entry_cmeta = dict(rec.get('call_meta') or {}) if isinstance(rec, dict) else {}
                for cid in used_cids:
                    if cid in merged_call_meta:
                        entry_cmeta[cid] = merged_call_meta[cid]
                raw = json.dumps(
                    {
                        'uid': self.owner_id,
                        'cid': self.connection_id,
                        'responses': {rid: {'history': clean_hist, 'call_meta': {cid: entry_cmeta[cid] for cid in used_cids if cid in entry_cmeta}}},
                    },
                    ensure_ascii=False,
                    separators=(',', ':'),
                ).encode('utf-8')
                if len(raw) <= _MAX_PERSISTED_BYTES:
                    return raw
            return None
        return json.dumps(
            {'uid': self.owner_id, 'cid': self.connection_id, 'responses': {}},
            ensure_ascii=False,
            separators=(',', ':'),
        ).encode('utf-8')

    def _save_persisted_state(self) -> bool:
        tmp_path: Path | None = None
        try:
            parent = self.state_path.parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(parent, 0o700)
            owner_dir = parent.parent
            if owner_dir.name == str(self.owner_id):
                os.chmod(owner_dir, 0o700)

            lock_path = Path(f'{self.state_path}.lock')
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                merged_responses: dict[str, dict] = {}
                merged_call_meta: dict[str, dict] = dict(self.call_meta)
                if self.state_path.is_file():
                    try:
                        existing_token = self.state_path.read_text(encoding='ascii').strip()
                        if existing_token:
                            existing = self._decrypt_state_blob(existing_token)
                            if existing and isinstance(existing.get('responses'), dict):
                                for rid, entry in existing['responses'].items():
                                    if isinstance(rid, str) and isinstance(entry, dict) and isinstance(entry.get('history'), list):
                                        merged_responses[rid] = entry
                                        for cid, cmeta in (entry.get('call_meta') or {}).items():
                                            if isinstance(cid, str) and isinstance(cmeta, dict):
                                                merged_call_meta.setdefault(cid, cmeta)
                    except (OSError, InvalidToken, ValueError, UnicodeError):
                        pass

                for rid, hist in self.responses_history.items():
                    merged_responses.pop(rid, None)
                    merged_responses[rid] = {'history': hist, 'call_meta': {}}

                raw = self._pack_persisted_payload(merged_responses, merged_call_meta)
                if raw is None or len(raw) > _MAX_PERSISTED_BYTES:
                    self.last_persistence_error = 'history_size_limit_exceeded'
                    _BRIDGE_LOG.warning(
                        'Antigravity bridge history exceeded max persisted bytes (owner=%s agent=%s limit=%d)',
                        self.owner_id, self.agent_scope, _MAX_PERSISTED_BYTES,
                    )
                    return False

                token = _bridge_fernet(self.owner_id, self.connection_id).encrypt(raw).decode('ascii')
                tmp_path = parent / f'{self.state_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}'
                fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='ascii') as f:
                    f.write(token)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.state_path)
                os.chmod(self.state_path, 0o600)
                tmp_path = None
                self.last_persistence_error = None
                return True
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
        except OSError as exc:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.last_persistence_error = f'history_write_failed:{type(exc).__name__}'
            _BRIDGE_LOG.warning(
                'Antigravity bridge history save failed (owner=%s agent=%s err=%s)',
                self.owner_id, self.agent_scope, type(exc).__name__,
            )
            return False

    async def ensure_credentials(self, *, force_refresh: bool = False) -> tuple[str, str, str, str]:
        async with self.session_lock:
            now = time.time()
            if force_refresh or not self.access_token or now >= self.token_expires_at - 60:
                session = await fetch_backend_session(force_refresh=force_refresh)
                new_cid = str(session.get('connection_id') or '').strip()
                if new_cid and new_cid != self.connection_id:
                    self.bind_connection(new_cid)
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

    def translate_tools(self, raw_tools: list | None) -> tuple[list[dict], dict[str, dict], dict[tuple[str | None, str], str], set[str]]:
        if not raw_tools:
            return [], {}, {}, set()
        used: set[str] = set()
        declarations: list[dict] = []
        safe_to_target: dict[str, dict] = {}
        target_to_safe: dict[tuple[str | None, str], str] = {}
        custom_names: set[str] = set()

        has_ucheba_mcp = any(
            isinstance(t, dict) and t.get('type') == 'namespace' and str(t.get('name') or '').startswith('mcp__ucheba')
            for t in raw_tools
        )
        strip_codex_harness_tools = has_ucheba_mcp

        def _add_declaration(
            *,
            orig_name: str,
            orig_namespace: str | None,
            description: str,
            params,
            is_custom: bool = False,
        ) -> None:
            raw_hint = f'{orig_namespace}__{orig_name}' if orig_namespace else orig_name
            safe_name = _sanitize_tool_name(raw_hint, used)
            safe_to_target[safe_name] = {
                'name': orig_name,
                'namespace': orig_namespace,
                'is_custom': is_custom,
            }
            target_to_safe[(orig_namespace, orig_name)] = safe_name
            if orig_namespace:
                target_to_safe.setdefault((None, f'{orig_namespace}__{orig_name}'), safe_name)
            else:
                target_to_safe.setdefault((None, orig_name), safe_name)

            if is_custom:
                custom_names.add(orig_name)
                declarations.append({
                    'name': safe_name,
                    'description': (description or orig_name)[:1024],
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
                return

            decl: dict = {
                'name': safe_name,
                'description': (description or orig_name)[:1024],
            }
            if isinstance(params, dict):
                cleaned = sanitize_schema_for_cloudcode(params)
                if cleaned.get('type') == 'OBJECT' and cleaned.get('properties'):
                    decl['parameters'] = cleaned
            declarations.append(decl)

        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            tool_type = tool.get('type')
            if tool_type in ('function', 'custom'):
                orig_name = str(tool.get('name') or '').strip()
                if not orig_name:
                    continue
                if strip_codex_harness_tools and orig_name in _CODEX_HARNESS_TOOLS:
                    continue
                _add_declaration(
                    orig_name=orig_name,
                    orig_namespace=str(tool.get('namespace') or '').strip() or None,
                    description=str(tool.get('description') or orig_name),
                    params=tool.get('parameters'),
                    is_custom=(tool_type == 'custom'),
                )
            elif tool_type == 'namespace':
                ns_name = str(tool.get('name') or '').strip() or None
                if strip_codex_harness_tools and ns_name in _CODEX_HARNESS_NAMESPACES:
                    continue
                sub_tools = tool.get('tools') or []
                if not isinstance(sub_tools, list):
                    continue
                for sub in sub_tools:
                    if not isinstance(sub, dict):
                        continue
                    sub_type = sub.get('type') or 'function'
                    if sub_type not in ('function', 'custom'):
                        continue
                    sub_name = str(sub.get('name') or '').strip()
                    if not sub_name:
                        continue
                    _add_declaration(
                        orig_name=sub_name,
                        orig_namespace=ns_name,
                        description=str(sub.get('description') or sub_name),
                        params=sub.get('parameters'),
                        is_custom=(sub_type == 'custom'),
                    )

        tools_payload = [{'functionDeclarations': declarations}] if declarations else []
        return tools_payload, safe_to_target, target_to_safe, custom_names

    def translate_request(self, body: dict) -> tuple[dict, dict]:
        model = str(body.get('model') or '').strip()
        if not model:
            raise AntigravityError('Не указана модель для запроса', status_code=400)

        tools_payload, safe_to_target, target_to_safe, custom_names = self.translate_tools(body.get('tools'))
        namespaces = sorted({info['namespace'] for info in safe_to_target.values() if info.get('namespace')})
        has_ucheba_mcp = any(ns.startswith('mcp__ucheba') for ns in namespaces)
        has_shell_tool = any(
            isinstance(t, dict) and str(t.get('name') or '') in ('shell', 'exec_command', 'local_shell', 'apply_patch')
            for t in (body.get('tools') or ())
        )
        safe_to_orig = {
            safe: (f"{info['namespace']}__{info['name']}" if info.get('namespace') else info['name'])
            for safe, info in safe_to_target.items()
        }

        def _resolve_safe_name(orig_name: str, namespace: str | None = None) -> str:
            ns = str(namespace or '').strip() or None
            if (ns, orig_name) in target_to_safe:
                return target_to_safe[(ns, orig_name)]
            if (None, orig_name) in target_to_safe:
                return target_to_safe[(None, orig_name)]
            hint = f'{ns}__{orig_name}' if ns else orig_name
            return _sanitize_tool_name(hint, set(safe_to_target))

        raw_system_parts: list[str] = []
        has_custom_agent_prompt = False
        if isinstance(body.get('instructions'), str) and body['instructions'].strip():
            inst_txt = body['instructions'].strip()
            raw_system_parts.append(inst_txt)
            if _sanitize_system_text(inst_txt, has_custom_agent_prompt=True, preserve_permissions=has_shell_tool):
                has_custom_agent_prompt = True

        raw_input = body.get('input')
        prev_id = body.get('previous_response_id')
        history_items: list = []
        if isinstance(prev_id, str) and prev_id.strip():
            prev_key = prev_id.strip()
            if prev_key not in self.responses_history:
                self._load_persisted_state()
            if prev_key not in self.responses_history:
                raise AntigravityError(
                    'Контекст предыдущего ответа не найден для продолжения сессии.',
                    status_code=400,
                    error_status='previous_response_not_found',
                )
            history_items.extend(self.responses_history[prev_key])

        if isinstance(raw_input, str):
            history_items.append({'type': 'message', 'role': 'user', 'content': raw_input})
        elif isinstance(raw_input, list):
            history_items.extend(raw_input)

        for item in history_items:
            if not isinstance(item, dict):
                continue
            if item.get('type') in ('message', None) and item.get('role') in ('system', 'developer'):
                content = item.get('content')
                chunks = [content] if isinstance(content, str) else [
                    p.get('text', '') for p in (content if isinstance(content, list) else ()) if isinstance(p, dict)
                ]
                for chunk in chunks:
                    if isinstance(chunk, str) and chunk.strip():
                        stripped = chunk.strip()
                        tags = _HARNESS_STRIP_TAGS_BASE if has_shell_tool else (*_HARNESS_STRIP_TAGS_BASE, 'permissions instructions')
                        if not stripped.startswith(_CODEX_PREAMBLE_PREFIX) and _strip_exact_xml_blocks(stripped, tags).strip():
                            has_custom_agent_prompt = True

        # Предварительно индексируем вызовы инструментов из истории запроса на случай полного реплея без кэша.
        inline_call_names: dict[str, tuple[str, str]] = {}
        for item in history_items:
            if isinstance(item, dict) and item.get('type') in ('function_call', 'custom_tool_call'):
                orig_name = str(item.get('name') or '').strip()
                orig_ns = str(item.get('namespace') or '').strip() or None
                call_id = str(item.get('call_id') or item.get('id') or '').strip()
                if orig_name and call_id:
                    safe_name = _resolve_safe_name(orig_name, orig_ns)
                    inline_call_names[call_id] = (orig_name, safe_name)

        contents: list[dict] = []
        sig_by_item_id: dict[str, str] = dict(self.item_signatures)
        pending_model_call_ids: set[str] = set()

        for item in history_items:
            if not isinstance(item, dict):
                continue
            item_type = item.get('type')
            role = item.get('role')

            if item_type == 'reasoning':
                meta = _decode_encrypted_meta(
                    item.get('encrypted_content'),
                    owner_id=self.owner_id,
                    connection_id=self.connection_id,
                )
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
                    cleaned_sys = _sanitize_system_text(
                        content,
                        has_custom_agent_prompt=has_custom_agent_prompt,
                        preserve_permissions=has_shell_tool,
                    )
                    if cleaned_sys:
                        raw_system_parts.append(cleaned_sys)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get('text'), str) and part['text'].strip():
                            cleaned_sys = _sanitize_system_text(
                                part['text'],
                                has_custom_agent_prompt=has_custom_agent_prompt,
                                preserve_permissions=has_shell_tool,
                            )
                            if cleaned_sys:
                                raw_system_parts.append(cleaned_sys)
                continue

            if item_type in ('message', None) and role in ('user', 'assistant'):
                pending_model_call_ids.clear()
                cc_role = 'model' if role == 'assistant' else 'user'
                content = item.get('content')
                item_id = item.get('id') if isinstance(item.get('id'), str) else ''
                attached_sig = sig_by_item_id.get(item_id, '')
                if isinstance(content, str):
                    txt = _ENV_CONTEXT_BLOCK_RE.sub('', content).strip() if (cc_role == 'user' and has_custom_agent_prompt and has_ucheba_mcp) else content
                    if txt:
                        part = {'text': txt}
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
                            raw_txt = block.get('text')
                            if isinstance(raw_txt, str):
                                txt = _ENV_CONTEXT_BLOCK_RE.sub('', raw_txt).strip() if (cc_role == 'user' and has_custom_agent_prompt and has_ucheba_mcp) else raw_txt
                                if txt:
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
                if not contents or contents[-1]['role'] != 'model':
                    pending_model_call_ids.clear()
                orig_ns = str(item.get('namespace') or '').strip() or None
                safe_name = _resolve_safe_name(orig_name, orig_ns)
                call_id = str(item.get('call_id') or item.get('id') or '').strip()
                if call_id:
                    pending_model_call_ids.add(call_id)
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
                inlined = inline_call_names.get(call_id)
                prev_ok = bool(contents) and (
                    contents[-1]['role'] == 'model'
                    or (
                        contents[-1]['role'] == 'user'
                        and any(isinstance(p, dict) and 'functionResponse' in p for p in contents[-1].get('parts', []))
                    )
                )
                is_matching_call = bool(call_id and call_id in pending_model_call_ids and prev_ok)

                raw_out = item.get('output')
                images: list[tuple[str, str]] = []
                text_parts: list[str] = []

                if isinstance(raw_out, list):
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

                if not is_matching_call:
                    raw_tool_name = str(item.get('name') or meta.get('original_name') or '').strip()
                    is_external_task_manager = (
                        raw_tool_name == 'task_manager'
                        and call_id not in self.call_meta
                        and call_id not in inline_call_names
                    )
                    if not is_external_task_manager:
                        raise AntigravityError(
                            f'Некорректная последовательность вызовов инструментов: неожиданный function_call_output для call_id={call_id or "empty"} ({raw_tool_name or "unknown"})',
                            status_code=400,
                            error_status='invalid_tool_sequence',
                        )
                    pending_model_call_ids.clear()
                    if isinstance(raw_out, str):
                        text_payload = raw_out
                    elif isinstance(raw_out, list):
                        text_payload = '\n'.join(text_parts)
                    elif isinstance(raw_out, dict):
                        text_payload = json.dumps(raw_out, ensure_ascii=False)
                    else:
                        text_payload = '' if raw_out is None else str(raw_out)
                    if text_payload.strip():
                        _append_part(contents, 'user', {'text': text_payload})
                    for mime, b64 in images:
                        _append_part(contents, 'user', {'inlineData': {'mimeType': mime, 'data': b64}})
                    continue

                pending_model_call_ids.discard(call_id)
                safe_name = (
                    meta.get('safe_name')
                    or (inlined[1] if inlined else '')
                    or _resolve_safe_name(str(item.get('name') or 'tool'), item.get('namespace'))
                )
                provider_id = meta.get('provider_id')

                if isinstance(raw_out, str):
                    try:
                        parsed_out = json.loads(raw_out)
                        resp_obj = parsed_out if isinstance(parsed_out, dict) else {'result': parsed_out}
                    except ValueError:
                        resp_obj = {'result': raw_out}
                elif isinstance(raw_out, dict):
                    resp_obj = raw_out
                elif isinstance(raw_out, list):
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
            elif isinstance(tc, dict) and tc.get('type') in ('function', 'custom') and tc.get('name'):
                target_safe = _resolve_safe_name(str(tc['name']).strip(), tc.get('namespace'))
                tool_config = {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': [target_safe]}}
            elif isinstance(tc, dict) and tc.get('type') == 'allowed_tools':
                mode_raw = str(tc.get('mode') or 'auto').lower()
                cc_mode = 'ANY' if mode_raw == 'required' else 'NONE' if mode_raw == 'none' else 'AUTO'
                allowed_list = []
                for spec in tc.get('tools') or []:
                    if isinstance(spec, dict) and spec.get('name'):
                        allowed_list.append(_resolve_safe_name(str(spec['name']).strip(), spec.get('namespace')))
                cfg_dict: dict = {'mode': cc_mode}
                if allowed_list and cc_mode != 'NONE':
                    cfg_dict['allowedFunctionNames'] = allowed_list
                tool_config = {'functionCallingConfig': cfg_dict}

        gen_config: dict = {}
        if type(body.get('max_output_tokens')) is int and body['max_output_tokens'] > 0:
            gen_config['maxOutputTokens'] = body['max_output_tokens']
        if isinstance(body.get('temperature'), (int, float)) and not isinstance(body.get('temperature'), bool):
            gen_config['temperature'] = float(body['temperature'])
        if isinstance(body.get('top_p'), (int, float)) and not isinstance(body.get('top_p'), bool):
            gen_config['topP'] = float(body['top_p'])

        system_parts = [p for p in raw_system_parts if p]
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

        sys_inst = {'role': 'user', 'parts': [{'text': '\n\n'.join(system_parts)}]} if system_parts else None
        meta = {
            'model': model,
            'safe_to_orig': safe_to_orig,
            'safe_to_target': safe_to_target,
            'custom_names': custom_names,
            'namespaces': namespaces,
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
    error: dict | None = None,
) -> dict:
    obj: dict = {
        'id': resp_id,
        'object': 'response',
        'created_at': int(time.time()),
        'status': status,
        'model': model,
        'output': output,
        'parallel_tool_calls': True,
        'error': error,
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


def _evaluate_terminal_outcome(
    *,
    finish_reason: str,
    block_reason: str,
    has_text: bool,
    has_tool_calls: bool,
    malformed_call_error: str = '',
) -> tuple[str, dict | None, dict | None]:
    """Определяет честный статус завершения Responses API из причины Cloud Code."""
    if block_reason:
        return (
            'failed',
            None,
            {'code': block_reason.lower(), 'message': f'Запрос заблокирован фильтром безопасности ({block_reason})'},
        )
    if malformed_call_error:
        return (
            'failed',
            None,
            {'code': 'malformed_function_call', 'message': malformed_call_error},
        )
    if finish_reason == 'MAX_TOKENS':
        return 'incomplete', {'reason': 'max_output_tokens'}, None
    if finish_reason in _FAILED_FINISH_REASONS:
        reasons = {
            'SAFETY': 'Ответ модели остановлен фильтром безопасности (SAFETY)',
            'RECITATION': 'Ответ модели остановлен фильтром цитирования (RECITATION)',
            'BLOCKLIST': 'Ответ модели заблокирован по списку ограничений (BLOCKLIST)',
            'PROHIBITED_CONTENT': 'Запрос или ответ отклонён политикой безопасности (PROHIBITED_CONTENT)',
            'SPII': 'Ответ остановлен фильтром персональных данных (SPII)',
            'MALFORMED_FUNCTION_CALL': 'Модель сформировала некорректный вызов инструмента (MALFORMED_FUNCTION_CALL)',
        }
        return (
            'failed',
            None,
            {'code': finish_reason.lower(), 'message': reasons.get(finish_reason, f'Генерация прервана ({finish_reason})')},
        )
    if finish_reason and finish_reason not in _ALLOWED_FINISH_REASONS:
        return (
            'failed',
            None,
            {'code': 'unexpected_finish_reason', 'message': f'Неизвестная причина завершения генерации: {finish_reason}'},
        )
    if not finish_reason:
        return (
            'failed',
            None,
            {'code': 'premature_stream_close', 'message': 'Поток генерации завершился без подтверждения окончания (отсутствует finishReason)'},
        )
    if not has_text and not has_tool_calls:
        return (
            'failed',
            None,
            {'code': 'empty_response', 'message': 'Модель не вернула ни текста, ни вызовов инструментов'},
        )
    return 'completed', None, None


def _sse(event_type: str, payload: dict) -> bytes:
    return f'event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'.encode('utf-8')


def create_bridge_app(session: AntigravityBridgeSession, bridge_token: str, label: str = 'Agent') -> FastAPI:
    app = FastAPI(docs_url=None, openapi_url=None)
    bridge_log = logging.getLogger('uvicorn.error')

    async def _run_as_owner(awaitable):
        tok = current_user_id.set(session.owner_id)
        try:
            return await awaitable
        finally:
            current_user_id.reset(tok)

    def _check_auth(request: Request) -> JSONResponse | None:
        auth = request.headers.get('authorization', '')
        expected = f'Bearer {bridge_token}'
        if not auth or not hmac.compare_digest(auth, expected):
            return JSONResponse(status_code=401, content={'error': {'message': 'Unauthorized', 'type': 'auth_error', 'code': 'unauthorized'}})
        return None

    async def _list_models(request: Request):
        if (denied := _check_auth(request)) is not None:
            return denied
        try:
            catalog = await _run_as_owner(fetch_live_catalog())
        except AntigravityError as exc:
            status = exc.status_code or 502
            return JSONResponse(status_code=status, content={'error': {'message': exc.user_message, 'type': 'api_error', 'code': exc.error_status or str(status)}})
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
        """Открывает поток Cloud Code с обработкой 401 refresh и ограниченным ретраем кратковременных сбоев без смены endpoint."""
        max_attempts = 3
        refreshed = False
        attempt = 0
        decls_count = len(meta['tools_payload'][0].get('functionDeclarations', [])) if meta['tools_payload'] else 0
        sys_chars = sum(len(str(p.get('text') or '')) for p in (meta['system_instruction'] or {}).get('parts', []))
        while True:
            token_val, project_id, cc_url, ua = await _run_as_owner(
                session.ensure_credentials(force_refresh=refreshed)
            )
            envelope = build_cloudcode_envelope(
                project_id=project_id,
                model=meta['model'],
                contents=meta['contents'],
                system_instruction=meta['system_instruction'],
                tools=meta['tools_payload'] or None,
                tool_config=meta['tool_config'],
                generation_config=meta['gen_config'] or None,
                session_id=session.session_id,
            )
            bridge_log.info(
                '%s stage=cloudcode_envelope_built model=%s decls=%d sys_chars=%d attempt=%d host=%s',
                label, meta['model'], decls_count, sys_chars, attempt + 1, cc_url,
            )
            t_start = time.monotonic()
            emitted_any = False
            try:
                async for chunk in stream_generate_content(token_val, envelope, cloudcode_url=cc_url, user_agent=ua):
                    if not emitted_any:
                        ttfb_ms = int((time.monotonic() - t_start) * 1000)
                        bridge_log.info(
                            '%s stage=cloudcode_response model=%s status=ok decls=%d',
                            label, meta['model'], decls_count,
                        )
                        bridge_log.info(
                            '%s stage=cloudcode_first_chunk model=%s ttfb_ms=%d',
                            label, meta['model'], ttfb_ms,
                        )
                        emitted_any = True
                    yield chunk
                return
            except asyncio.CancelledError:
                raise
            except AntigravityAuthError as exc:
                session.record_error(exc)
                bridge_log.warning(
                    '%s stage=cloudcode_response model=%s status=error http=%s code=%s retryable=%s decls=%d',
                    label, meta['model'], exc.status_code, exc.error_status or '-', not emitted_any and not refreshed, decls_count,
                )
                if not emitted_any and not refreshed:
                    refreshed = True
                    continue
                raise
            except AntigravityError as exc:
                session.record_error(exc)
                bridge_log.warning(
                    '%s stage=cloudcode_response model=%s status=error http=%s code=%s retryable=%s decls=%d',
                    label, meta['model'], exc.status_code, exc.error_status or '-', bool(exc.retryable), decls_count,
                )
                retry_limit = 1 if exc.status_code == 429 else (max_attempts - 1)
                if emitted_any or not exc.retryable or attempt >= retry_limit:
                    raise
                if exc.status_code == 429 and exc.retry_after is not None and exc.retry_after > 5.0:
                    raise
                attempt += 1
                delay = min(5.0, max(0.2, exc.retry_after)) if exc.retry_after is not None else min(8.0, 0.8 * (2 ** (attempt - 1)))
                bridge_log.warning(
                    '%s antigravity retry %d/%d (%s)', label, attempt, retry_limit, exc.status_code or exc.error_status or 'transient'
                )
                await asyncio.sleep(delay)

    async def _responses(request: Request):
        if (denied := _check_auth(request)) is not None:
            return denied
        session.clear_error()
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(status_code=400, content={'error': {'message': 'Invalid JSON', 'type': 'invalid_request_error'}})

        raw_model = str(body.get('model') or '-').strip() if isinstance(body, dict) else '-'
        bridge_log.info(
            '%s stage=bridge_request_received model=%s stream=%s raw_tools=%d',
            label,
            raw_model,
            bool(body.get('stream', False)) if isinstance(body, dict) else False,
            len(body.get('tools') or []) if isinstance(body, dict) and isinstance(body.get('tools'), list) else 0,
        )

        tok = current_user_id.set(session.owner_id)
        try:
            meta, raw_body = session.translate_request(body)
        except AntigravityError as exc:
            bridge_log.warning(
                '%s stage=bridge_request_parsed model=%s status=error http=%s code=%s',
                label, raw_model, exc.status_code or 400, exc.error_status or 'invalid_request',
            )
            return JSONResponse(
                status_code=exc.status_code or 400,
                content={'error': {'message': exc.user_message, 'type': 'invalid_request_error', 'code': exc.error_status or 'invalid_request'}},
            )
        finally:
            current_user_id.reset(tok)

        decls_count = len(meta['tools_payload'][0].get('functionDeclarations', [])) if meta['tools_payload'] else 0
        tc_mode = (
            (meta['tool_config'] or {}).get('functionCallingConfig', {}).get('mode')
            if meta['tool_config']
            else ('AUTO' if meta['tools_payload'] else 'NONE')
        )
        bridge_log.info(
            '%s stage=bridge_request_parsed model=%s contents=%d decls=%d namespaces=%d tool_choice=%s',
            label, meta['model'], len(meta['contents']), decls_count, len(meta['namespaces']), tc_mode,
        )

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
            block_reason = ''
            malformed_call_error = ''
            has_text = False
            has_tool_calls = False
            seq = -1

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
                    nonlocal last_usage, finish_reason, block_reason, malformed_call_error, pending_sig, has_text, has_tool_calls
                    usage = _extract_usage(chunk_resp)
                    if usage is not None:
                        last_usage = usage
                    pf = chunk_resp.get('promptFeedback')
                    if isinstance(pf, dict) and isinstance(pf.get('blockReason'), str) and pf['blockReason'].strip():
                        block_reason = pf['blockReason'].strip().upper()
                    candidates = chunk_resp.get('candidates') or []
                    if not candidates or not isinstance(candidates[0], dict):
                        return []
                    cand = candidates[0]
                    if isinstance(cand.get('finishReason'), str) and cand['finishReason'].strip():
                        finish_reason = cand['finishReason'].strip().upper()
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
                            has_text = True
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
                        elif 'functionCall' in part:
                            fc = part.get('functionCall')
                            if not isinstance(fc, dict):
                                malformed_call_error = 'Модель вернула некорректный объект functionCall'
                                continue
                            safe_name = str(fc.get('name') or '').strip()
                            if not safe_name:
                                malformed_call_error = 'Модель вернула вызов инструмента без имени'
                                continue
                            raw_args_val = fc.get('args')
                            if raw_args_val is not None and not isinstance(raw_args_val, dict):
                                malformed_call_error = 'Модель вернула некорректные аргументы вызова инструмента'
                                continue
                            has_tool_calls = True
                            if text_item_id is not None:
                                events_out.extend(_close_text_item())
                            target = (meta.get('safe_to_target') or {}).get(safe_name)
                            if target:
                                orig_name = target['name']
                                orig_ns = target.get('namespace')
                                is_custom = bool(target.get('is_custom'))
                            else:
                                orig_name = meta['safe_to_orig'].get(safe_name, safe_name)
                                orig_ns = None
                                is_custom = orig_name in meta['custom_names']
                            provider_id = str(fc.get('id') or '').strip() or None
                            call_id = provider_id or ('call_' + uuid.uuid4().hex[:24])
                            fc_item_id = 'fc_' + uuid.uuid4().hex[:24]
                            raw_args = raw_args_val if isinstance(raw_args_val, dict) else {}

                            sig = part_sig or pending_sig
                            pending_sig = ''
                            if sig:
                                call_sigs[call_id] = sig
                            session.call_meta[call_id] = {
                                'provider_id': provider_id,
                                'original_name': orig_name,
                                'namespace': orig_ns,
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
                                if orig_ns:
                                    fc_added['namespace'] = orig_ns
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
                                if orig_ns:
                                    fc_added['namespace'] = orig_ns
                                fc_done = {
                                    'id': fc_item_id,
                                    'type': 'function_call',
                                    'status': 'completed',
                                    'call_id': call_id,
                                    'name': orig_name,
                                    'arguments': args_json,
                                }
                                if orig_ns:
                                    fc_done['namespace'] = orig_ns
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
                                    args_done_ev = {
                                        'type': 'response.function_call_arguments.done',
                                        'sequence_number': next_seq(),
                                        'item_id': fc_item_id,
                                        'output_index': out_idx,
                                        'name': orig_name,
                                        'arguments': args_json,
                                    }
                                    if orig_ns:
                                        args_done_ev['namespace'] = orig_ns
                                    events_out.append(_sse('response.function_call_arguments.done', args_done_ev))
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
                        'encrypted_content': _encode_encrypted_meta(
                            {'call_sigs': call_sigs, 'item_sigs': item_sigs},
                            owner_id=session.owner_id,
                            connection_id=session.connection_id,
                        ),
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

                bridge_log.info(
                    '%s stage=cloudcode_finish model=%s finish_reason=%s block_reason=%s has_text=%s has_tools=%s',
                    label, model, finish_reason or '-', block_reason or '-', has_text, has_tool_calls,
                )

                final_status, incomplete_details, err_payload = _evaluate_terminal_outcome(
                    finish_reason=finish_reason,
                    block_reason=block_reason,
                    has_text=has_text,
                    has_tool_calls=has_tool_calls,
                    malformed_call_error=malformed_call_error,
                )

                bridge_log.info(
                    '%s stage=responses_emitted model=%s final_status=%s output_items=%d error_code=%s',
                    label, model, final_status, len(output_items), (err_payload or {}).get('code', '-'),
                )

                if final_status == 'failed':
                    if not stream_mode:
                        raise AntigravityError(err_payload['message'], status_code=502, error_status=err_payload['code'])
                    yield _sse('error', {
                        'type': 'error',
                        'sequence_number': next_seq(),
                        'code': err_payload['code'],
                        'message': err_payload['message'],
                        'param': None,
                    })
                    failed_resp = _make_response_obj(resp_id, model, 'failed', output_items, usage=last_usage, error=err_payload)
                    yield _sse('response.failed', {'type': 'response.failed', 'sequence_number': next_seq(), 'response': failed_resp})
                    return

                final_resp = _make_response_obj(
                    resp_id,
                    model,
                    final_status,
                    output_items,
                    usage=last_usage,
                    incomplete_details=incomplete_details,
                )
                session.responses_history[resp_id] = list(meta['history_items']) + list(output_items)
                if len(session.responses_history) > _MAX_PERSISTED_RESPONSES:
                    oldest = next(iter(session.responses_history))
                    session.responses_history.pop(oldest, None)
                session._save_persisted_state()

                if stream_mode:
                    ev_name = 'response.incomplete' if final_status == 'incomplete' else 'response.completed'
                    yield _sse(ev_name, {'type': ev_name, 'sequence_number': next_seq(), 'response': final_resp})
                else:
                    yield final_resp
            except asyncio.CancelledError:
                if 'gen' in locals():
                    await gen.aclose()
                raise
            except AntigravityError as exc:
                err_code = exc.error_status or str(exc.status_code or 502)
                bridge_log.warning(
                    '%s stage=responses_emitted model=%s final_status=failed http=%s error_code=%s',
                    label, model, exc.status_code or 502, err_code,
                )
                if stream_mode and seq >= 0:
                    err_obj = {'code': err_code, 'message': exc.user_message}
                    yield _sse('error', {
                        'type': 'error',
                        'sequence_number': next_seq(),
                        'code': err_code,
                        'message': exc.user_message,
                        'param': None,
                    })
                    failed_resp = _make_response_obj(resp_id, model, 'failed', output_items, usage=last_usage, error=err_obj)
                    yield _sse('response.failed', {'type': 'response.failed', 'sequence_number': next_seq(), 'response': failed_resp})
                else:
                    raise
            except Exception as exc:
                bridge_log.error(
                    '%s stage=bridge_internal_error model=%s exc=%s',
                    label, model, type(exc).__name__,
                )
                wrapped = AntigravityError(
                    f'Внутренняя ошибка Responses-моста ({type(exc).__name__})',
                    status_code=500,
                    error_status='bridge_internal_error',
                )
                if stream_mode and seq >= 0:
                    err_obj = {'code': 'bridge_internal_error', 'message': wrapped.user_message}
                    yield _sse('error', {
                        'type': 'error',
                        'sequence_number': next_seq(),
                        'code': 'bridge_internal_error',
                        'message': wrapped.user_message,
                        'param': None,
                    })
                    failed_resp = _make_response_obj(resp_id, model, 'failed', output_items, usage=last_usage, error=err_obj)
                    yield _sse('response.failed', {'type': 'response.failed', 'sequence_number': next_seq(), 'response': failed_resp})
                else:
                    raise wrapped from None

        if not stream_mode:
            try:
                result = None
                async for item in _collect_or_stream():
                    result = item
                if result is None:
                    raise AntigravityError('Модель не вернула ответ', status_code=502, error_status='empty_response')
                return JSONResponse(status_code=200, content=result)
            except asyncio.CancelledError:
                return JSONResponse(status_code=499, content={'error': {'message': 'Запрос отменён', 'type': 'cancelled', 'code': 'request_cancelled'}})
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
        except asyncio.CancelledError:
            await stream_gen.aclose()
            return JSONResponse(status_code=499, content={'error': {'message': 'Запрос отменён', 'type': 'cancelled', 'code': 'request_cancelled'}})
        except AntigravityError as exc:
            status = exc.status_code or 502
            return JSONResponse(
                status_code=status,
                content={'error': {'message': exc.user_message, 'type': 'api_error', 'code': exc.error_status or str(status)}},
            )

        async def _sse_bytes():
            try:
                if first_bytes:
                    yield first_bytes
                async for chunk_bytes in stream_gen:
                    yield chunk_bytes
            finally:
                await stream_gen.aclose()

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
async def antigravity_responses_bridge(
    label: str = 'Agent',
    *,
    cloudcode_url: str = '',
    error_sink: dict | None = None,
    agent_scope: str = '',
    connection_id: str = '',
):
    """Запускает изолированный per-run loopback Responses API мост в текущем event loop."""
    owner_id = user_id()
    bridge_token = secrets.token_urlsafe(32)
    session = AntigravityBridgeSession(
        owner_id=owner_id,
        cloudcode_url=cloudcode_url,
        error_sink=error_sink,
        agent_scope=agent_scope or label,
        connection_id=connection_id,
    )
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
