"""Нативный Python-клиент Google Antigravity (OAuth PKCE + Cloud Code API)."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import ipaddress
import os
import platform
import re
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import httpx

from common.identity import user_id
from common.rpc import internal_token

OAUTH_AUTH_URL = 'https://accounts.google.com/o/oauth2/auth'
OAUTH_TOKEN_URL = 'https://oauth2.googleapis.com/token'
OAUTH_USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'
OAUTH_REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
DEFAULT_REDIRECT_URI = 'https://antigravity.google/oauth-callback'
DEFAULT_CLOUDCODE_URL = 'https://daily-cloudcode-pa.googleapis.com'
ALLOWED_CLOUDCODE_HOSTS = frozenset({
    'daily-cloudcode-pa.googleapis.com',
    'cloudcode-pa.googleapis.com',
    'autopush-cloudcode-pa.googleapis.com',
})
DEFAULT_CLI_VERSION = '1.5.15'
DEFAULT_PUBLIC_CLIENT_ID = '1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com'
OAUTH_SCOPES = (
    'https://www.googleapis.com/auth/cloud-platform',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/cclog',
    'https://www.googleapis.com/auth/experimentsandconfigs',
    'https://www.googleapis.com/auth/aicode',
    'openid',
)

OAUTH_HINTS = {
    'invalid_client': 'Google отклонил OAuth-клиент. Проверьте Client ID и Client Secret в настройках Google Antigravity.',
    'invalid_grant': 'Код авторизации устарел, уже использован или токен был отозван. Выполните вход заново.',
    'redirect_uri_mismatch': 'Redirect URI не совпадает с зарегистрированным в OAuth-клиенте Google.',
    'invalid_request': 'Google OAuth отклонил параметры запроса.',
    'unauthorized_client': 'Этот OAuth-клиент не имеет прав для запрошенного потока.',
    'access_denied': 'Доступ отклонён пользователем на странице авторизации Google.',
}

_PARAM_FIELD_RE = re.compile(r'\b(client_secret|client_id|redirect_uri|grant_type|code_verifier|code|refresh_token)\b', re.I)
_PARAM_MISSING_RE = re.compile(r'\b(missing|required|must be provided|not provided)\b', re.I)
_CTX_KEYS = ('maxTokens', 'contextWindow', 'context_window', 'contextLength', 'context_length', 'maxContextTokens')
_IN_KEYS = ('maxInputTokens', 'max_input_tokens', 'inputTokenLimit', 'input_token_limit')
_OUT_KEYS = ('maxOutputTokens', 'max_output_tokens', 'outputTokenLimit', 'output_token_limit')


class AntigravityError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_status: str = '',
        retryable: bool = False,
        auth_expired: bool = False,
        revoked: bool = False,
        quota_exceeded: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.user_message = message
        self.status_code = status_code
        self.error_status = error_status
        self.retryable = retryable
        self.auth_expired = auth_expired
        self.revoked = revoked
        self.quota_exceeded = quota_exceeded
        self.retry_after = retry_after


class AntigravityAuthError(AntigravityError):
    pass


def client_user_agent(override: str = '') -> str:
    custom = (override or '').strip() or os.environ.get('AGY_USER_AGENT', '').strip()
    if custom:
        return custom
    machine = platform.machine().lower()
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(machine, machine or 'amd64')
    system = {'linux': 'linux', 'darwin': 'darwin', 'win32': 'windows'}.get(sys.platform, sys.platform)
    version = os.environ.get('AGY_CLI_VERSION', DEFAULT_CLI_VERSION).strip() or DEFAULT_CLI_VERSION
    return f'antigravity/cli/{version} (aidev_client; os_type={system}; arch={arch}; auth_method=consumer)'


def validate_cloudcode_url(value: str | None) -> str:
    raw = (value or '').strip()
    if not raw:
        return DEFAULT_CLOUDCODE_URL
    parsed = urlsplit(raw)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Укажите HTTPS-адрес подтверждённого Google Cloud Code endpoint без логина, query и fragment')
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Некорректный порт в адресе Cloud Code endpoint') from None
    if port not in (None, 443):
        raise ValueError('Для Cloud Code endpoint разрешён только стандартный порт HTTPS (443)')
    if parsed.path not in ('', '/'):
        raise ValueError('Cloud Code endpoint не должен содержать путь')
    try:
        ipaddress.ip_address(host)
        raise ValueError('IP-адреса запрещены для Cloud Code endpoint')
    except ValueError as exc:
        if 'IP-адреса запрещены' in str(exc):
            raise
    if host not in ALLOWED_CLOUDCODE_HOSTS:
        raise ValueError(
            'Неподтверждённый хост Cloud Code. Разрешены только: ' + ', '.join(sorted(ALLOWED_CLOUDCODE_HOSTS))
        )
    return f'https://{host}'


def validate_redirect_uri(value: str | None) -> str:
    raw = (value or '').strip()
    if not raw:
        return DEFAULT_REDIRECT_URI
    parsed = urlsplit(raw)
    host = (parsed.hostname or '').lower()
    is_local = parsed.scheme == 'http' and host in ('localhost', '127.0.0.1')
    if (parsed.scheme != 'https' and not is_local) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Укажите HTTPS redirect_uri (или http://localhost) без параметров и фрагмента')
    return raw.rstrip('/') if parsed.path in ('', '/') else raw


def env_client_secret() -> str:
    return (os.environ.get('ANTIGRAVITY_OAUTH_CLIENT_SECRET') or os.environ.get('AGY_OAUTH_CLIENT_SECRET') or '').strip()


def env_client_id() -> str:
    return (os.environ.get('ANTIGRAVITY_OAUTH_CLIENT_ID') or os.environ.get('AGY_OAUTH_CLIENT_ID') or '').strip()


def generate_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('=')
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_urlsafe(24)


def build_oauth_url(
    client_id: str,
    state: str,
    challenge: str,
    *,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scopes: tuple[str, ...] | list[str] | None = None,
) -> str:
    scope_list = [s.strip() for s in (scopes or OAUTH_SCOPES) if isinstance(s, str) and s.strip()] or list(OAUTH_SCOPES)
    params = {
        'client_id': client_id.strip(),
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': ' '.join(scope_list),
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return f'{OAUTH_AUTH_URL}?{urlencode(params)}'


def parse_oauth_callback(
    value: str,
    expected_state: str,
    *,
    expected_redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    """Строго требует полный URL обратного вызова с обязательной проверкой origin, path, state и code."""
    raw = (value or '').strip()
    if not raw or not raw.startswith(('http://', 'https://')):
        raise ValueError('Вставьте полный URL перенаправления из браузера, содержащий параметры state и code')
    parsed = urlsplit(raw)
    expected = urlsplit(expected_redirect_uri)
    if (
        parsed.scheme.lower() != expected.scheme.lower()
        or parsed.netloc.lower() != expected.netloc.lower()
        or (parsed.path.rstrip('/') or '/') != (expected.path.rstrip('/') or '/')
    ):
        raise ValueError('URL перенаправления не соответствует настроенному redirect_uri')
    q = parse_qs(parsed.query)
    got_state = (q.get('state') or [''])[0].strip()
    if not got_state or not expected_state or not hmac.compare_digest(got_state, expected_state):
        raise ValueError('Несовпадение или отсутствие OAuth state. Начните вход заново.')
    if 'error' in q:
        err_code = re.sub(r'[^a-zA-Z0-9_.-]', '', str(q['error'][0]))[:64] or 'oauth_error'
        raise ValueError(f'Google OAuth вернул ошибку: {err_code}')
    code = (q.get('code') or [''])[0].strip()
    if not code or any(ch.isspace() for ch in code):
        raise ValueError('В URL перенаправления отсутствует корректный параметр code')
    return code


def safe_oauth_error(status_code: int, payload, *, is_refresh: bool = False) -> tuple[str, str, bool]:
    data = payload if isinstance(payload, dict) else {}
    error = data.get('error', 'unknown_error')
    if not isinstance(error, str) or not error.replace('_', '').replace('-', '').isalnum():
        error = 'unknown_error'
    desc = data.get('error_description', '')
    detail = ''
    if isinstance(desc, str):
        field = _PARAM_FIELD_RE.search(desc)
        if field and _PARAM_MISSING_RE.search(desc):
            missing = field.group(1).lower()
            if missing == 'client_secret':
                detail = 'OAuth-клиент требует Client Secret. Администратор должен указать Client Secret в системных настройках Google Antigravity.'
            else:
                detail = f'Отсутствует обязательный OAuth-параметр: {missing}.'
    revoked = is_refresh and error in ('invalid_grant', 'unauthorized_client')
    hint = OAUTH_HINTS.get(error, 'Сервер авторизации Google отклонил запрос.')
    if revoked:
        hint = 'Сессия Google отозвана или истекла (invalid_grant). Войдите через Google заново.'
    msg = f'Google OAuth HTTP {status_code}: {error}. {detail or hint}'
    return error, msg, revoked


def extract_id_token_claims(id_token: str | None) -> dict:
    if not isinstance(id_token, str) or id_token.count('.') < 2:
        return {}
    try:
        segment = id_token.split('.')[1]
        padded = segment + '=' * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode('utf-8'))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


async def _post_oauth_form(url: str, data: dict, *, is_refresh: bool = False, timeout: float = 30.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                url,
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
            )
    except httpx.TimeoutException:
        raise AntigravityError('Google OAuth не ответил вовремя', retryable=True) from None
    except httpx.HTTPError:
        raise AntigravityError('Не удалось подключиться к Google OAuth', retryable=True) from None
    if 300 <= response.status_code < 400:
        raise AntigravityError('Google OAuth вернул неожиданное перенаправление', status_code=502)
    if response.is_success:
        try:
            payload = response.json()
        except ValueError:
            raise AntigravityError('Google OAuth вернул некорректный JSON', status_code=502) from None
        if not isinstance(payload, dict):
            raise AntigravityError('Google OAuth вернул неожиданный ответ', status_code=502)
        return payload
    try:
        err_payload = response.json()
    except ValueError:
        err_payload = {}
    err_code, msg, revoked = safe_oauth_error(response.status_code, err_payload, is_refresh=is_refresh)
    if revoked or response.status_code in (400, 401):
        raise AntigravityAuthError(msg, status_code=response.status_code, error_status=err_code, revoked=revoked)
    raise AntigravityError(
        msg,
        status_code=response.status_code,
        error_status=err_code,
        retryable=response.status_code in (408, 429, 500, 502, 503, 504),
    )


async def exchange_oauth_code(
    *,
    client_id: str,
    client_secret: str = '',
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    code: str,
    code_verifier: str,
) -> dict:
    form = {
        'grant_type': 'authorization_code',
        'client_id': client_id.strip(),
        'redirect_uri': redirect_uri,
        'code': code.strip(),
        'code_verifier': code_verifier,
    }
    secret = (client_secret or '').strip()
    if secret:
        form['client_secret'] = secret
    result = await _post_oauth_form(OAUTH_TOKEN_URL, form, is_refresh=False)
    if not isinstance(result.get('access_token'), str) or not result['access_token']:
        raise AntigravityAuthError('Google OAuth не вернул access_token')
    return result


async def refresh_oauth_token(
    *,
    client_id: str,
    client_secret: str = '',
    refresh_token: str,
) -> dict:
    form = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id.strip(),
    }
    secret = (client_secret or '').strip()
    if secret:
        form['client_secret'] = secret
    result = await _post_oauth_form(OAUTH_TOKEN_URL, form, is_refresh=True)
    if not isinstance(result.get('access_token'), str) or not result['access_token']:
        raise AntigravityAuthError('Обновление токена Google OAuth не вернуло access_token')
    return result


async def fetch_userinfo(access_token: str, *, timeout: float = 15.0) -> dict:
    if not access_token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(
                OAUTH_USERINFO_URL,
                headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
            )
        if resp.is_success:
            data = resp.json()
            return data if isinstance(data, dict) else {}
    except (httpx.HTTPError, ValueError):
        pass
    return {}


async def revoke_oauth_token(token: str, *, timeout: float = 5.0) -> None:
    clean = (token or '').strip()
    if not clean:
        return
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            await client.post(
                OAUTH_REVOKE_URL,
                data={'token': clean},
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
    except httpx.HTTPError:
        pass


def cloudcode_headers(access_token: str, user_agent: str = '', *, accept: str = 'application/json') -> dict[str, str]:
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept': accept,
        'User-Agent': client_user_agent(user_agent),
    }


def _parse_retry_after(headers: httpx.Headers | None) -> float | None:
    if not headers:
        return None
    raw = headers.get('retry-after')
    if not raw:
        return None
    try:
        val = float(raw.strip())
        return max(0.5, min(60.0, val))
    except ValueError:
        return None


def _cloudcode_http_error(status_code: int, raw_bytes: bytes, path: str, headers: httpx.Headers | None = None) -> AntigravityError:
    err_status = 'HTTP_ERROR'
    raw_msg = ''
    try:
        parsed = json.loads(raw_bytes.decode('utf-8'))
        if isinstance(parsed, dict):
            err = parsed.get('error')
            if isinstance(err, dict):
                err_status = str(err.get('status') or err_status)[:64]
                raw_msg = str(err.get('message') or '')[:240]
            elif isinstance(parsed.get('message'), str):
                raw_msg = parsed['message'][:240]
    except (UnicodeError, ValueError):
        pass
    retry_after = _parse_retry_after(headers)
    if status_code == 401:
        return AntigravityAuthError(
            'Google Antigravity отклонил токен доступа (HTTP 401)',
            status_code=401,
            error_status=err_status,
            auth_expired=True,
        )
    if status_code == 403:
        msg = 'Google Antigravity запретил доступ к ресурсу или подписке (HTTP 403)'
        if raw_msg:
            msg = f'{msg}: {raw_msg}'
        return AntigravityError(msg, status_code=403, error_status=err_status)
    if status_code == 404:
        msg = f'Google Antigravity не нашёл запрошенную модель или метод ({path}, HTTP 404)'
        if raw_msg:
            msg = f'{msg}: {raw_msg}'
        return AntigravityError(msg, status_code=404, error_status=err_status)
    if status_code == 429:
        msg = 'Превышен лимит запросов или квота Google Antigravity (HTTP 429)'
        if raw_msg:
            msg = f'{msg}: {raw_msg}'
        return AntigravityError(
            msg,
            status_code=429,
            error_status=err_status,
            retryable=True,
            quota_exceeded=True,
            retry_after=retry_after,
        )
    retryable = status_code in (408, 500, 502, 503, 504)
    msg = f'Cloud Code HTTP {status_code} ({err_status})'
    if raw_msg:
        msg = f'{msg}: {raw_msg}'
    return AntigravityError(
        msg,
        status_code=status_code,
        error_status=err_status,
        retryable=retryable,
        retry_after=retry_after,
    )


async def cloudcode_post(
    path: str,
    data: dict,
    access_token: str,
    *,
    cloudcode_url: str = DEFAULT_CLOUDCODE_URL,
    user_agent: str = '',
    timeout: float = 60.0,
) -> dict:
    base = validate_cloudcode_url(cloudcode_url)
    url = f'{base}{path}'
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                url,
                json=data,
                headers=cloudcode_headers(access_token, user_agent),
            )
    except httpx.TimeoutException:
        raise AntigravityError(f'Google Antigravity не ответил вовремя ({path})', retryable=True) from None
    except httpx.HTTPError:
        raise AntigravityError(f'Ошибка соединения с Google Antigravity ({path})', retryable=True) from None
    if 300 <= response.status_code < 400:
        raise AntigravityError(f'Google Antigravity вернул неожиданный редирект ({response.status_code})', status_code=502)
    if not response.is_success:
        raise _cloudcode_http_error(response.status_code, response.content, path, response.headers)
    try:
        payload = response.json()
    except ValueError:
        raise AntigravityError(f'Google Antigravity вернул некорректный JSON ({path})', status_code=502) from None
    if not isinstance(payload, dict):
        raise AntigravityError(f'Google Antigravity вернул неожиданный формат ответа ({path})', status_code=502)
    return payload


def extract_project_info(info: dict) -> tuple[str, str]:
    project = info.get('cloudaicompanionProject') or (info.get('response') or {}).get('cloudaicompanionProject')
    if isinstance(project, dict):
        project = project.get('id') or project.get('projectId') or project.get('project')
    project_str = project.strip() if isinstance(project, str) else ''

    tier = info.get('currentTier') or (info.get('response') or {}).get('currentTier')
    if isinstance(tier, dict):
        tier_str = str(tier.get('name') or tier.get('id') or tier.get('tier') or '').strip()
    elif isinstance(tier, str):
        tier_str = tier.strip()
    else:
        tier_str = str(info.get('paidTier') or info.get('tier') or '').strip()
    return project_str, tier_str


async def load_code_assist(
    access_token: str,
    *,
    cloudcode_url: str = DEFAULT_CLOUDCODE_URL,
    user_agent: str = '',
    timeout: float = 30.0,
) -> dict:
    info = await cloudcode_post(
        '/v1internal:loadCodeAssist',
        {'metadata': {'ideType': 'ANTIGRAVITY'}},
        access_token,
        cloudcode_url=cloudcode_url,
        user_agent=user_agent,
        timeout=timeout,
    )
    project_str, tier_str = extract_project_info(info)
    if not project_str:
        raise AntigravityError(
            'Cloud Code loadCodeAssist не вернул cloudaicompanionProject. Проверьте подписку или задайте Project ID вручную.',
            status_code=403,
            error_status='NO_PROJECT',
        )
    return {'project_id': project_str, 'subscription_tier': tier_str}


async def fetch_available_models(
    access_token: str,
    project_id: str,
    *,
    cloudcode_url: str = DEFAULT_CLOUDCODE_URL,
    user_agent: str = '',
    timeout: float = 30.0,
) -> dict:
    return await cloudcode_post(
        '/v1internal:fetchAvailableModels',
        {'project': project_id},
        access_token,
        cloudcode_url=cloudcode_url,
        user_agent=user_agent,
        timeout=timeout,
    )


def _positive_int(obj: dict, keys: tuple[str, ...], *, minimum: int = 1) -> int | None:
    for key in keys:
        val = obj.get(key)
        if type(val) is int and val >= minimum:
            return val
    return None


def normalize_models(body: dict, *, include_internal: bool = False) -> list[dict]:
    models = body.get('models', {})
    if isinstance(models, list):
        models = {
            (m.get('model') or m.get('id')): m
            for m in models
            if isinstance(m, dict) and (m.get('model') or m.get('id'))
        }
    if not isinstance(models, dict):
        raise AntigravityError('Неожиданный ответ fetchAvailableModels: поле models не является словарём', status_code=502)

    picker: list[str] = []
    for item in body.get('agentModelSorts') or []:
        if not isinstance(item, dict):
            continue
        for group in item.get('groups') or []:
            if not isinstance(group, dict):
                continue
            for mid in group.get('modelIds') or []:
                if isinstance(mid, str) and mid.strip():
                    picker.append(mid.strip())
    picker_set = set(picker)
    ordered = list(dict.fromkeys([*picker, *(str(k).strip() for k in models.keys() if str(k).strip())]))

    result: list[dict] = []
    for model_id in ordered:
        model = models.get(model_id)
        if not isinstance(model, dict):
            continue
        name = model.get('displayName') or model.get('name') or ''
        name = name.strip() if isinstance(name, str) else ''
        in_picker = model_id in picker_set
        internal = bool(model.get('isInternal')) or (not name and not in_picker)
        if internal and not include_internal:
            continue

        quota = model.get('quotaInfo')
        if not isinstance(quota, dict):
            quota = {}
        raw_fraction = quota.get('remainingFraction')
        if isinstance(raw_fraction, (int, float)) and not isinstance(raw_fraction, bool) and 0 <= raw_fraction <= 1:
            remaining_fraction = float(raw_fraction)
            pct = round(remaining_fraction * 100, 1)
            remaining_percent = int(pct) if pct == int(pct) else pct
        else:
            remaining_fraction = None
            remaining_percent = None

        reset_time = quota.get('resetTime')
        if not isinstance(reset_time, str) or not reset_time.strip():
            reset_time = None
        else:
            reset_time = reset_time.strip()

        disabled_flag = bool(model.get('isDisabled') or model.get('disabled')) or model.get('available') is False
        available = not disabled_flag and (remaining_fraction is None or remaining_fraction > 0)

        max_tokens = _positive_int(model, _CTX_KEYS, minimum=256)
        max_input_tokens = _positive_int(model, _IN_KEYS, minimum=256)
        max_output_tokens = _positive_int(model, _OUT_KEYS, minimum=64)
        context_window = max_tokens or max_input_tokens

        result.append({
            'id': model_id,
            'name': name or model_id,
            'available': available,
            'remaining_fraction': remaining_fraction,
            'remaining_percent': remaining_percent,
            'reset_time': reset_time,
            'context_window': context_window,
            'max_tokens': max_tokens,
            'max_input_tokens': max_input_tokens,
            'max_output_tokens': max_output_tokens,
            'internal': internal,
            'recommended': in_picker,
        })
    return result


def _resolve_ref(ref: str, defs: dict) -> dict | None:
    if not isinstance(ref, str):
        return None
    for prefix in ('#/$defs/', '#/definitions/'):
        if ref.startswith(prefix):
            name = ref[len(prefix):]
            target = defs.get(name)
            return target if isinstance(target, dict) else None
    return None


def _extract_non_null_type(type_val) -> tuple[str | None, bool]:
    if isinstance(type_val, str):
        if type_val == 'null':
            return None, True
        return type_val, False
    if isinstance(type_val, list):
        nullable = 'null' in type_val
        non_null = [t for t in type_val if isinstance(t, str) and t != 'null']
        return (non_null[0] if non_null else None), nullable
    return None, False


def sanitize_schema_for_cloudcode(schema: dict, *, defs: dict | None = None, seen_refs: frozenset[str] = frozenset()) -> dict:
    """Преобразует JSON Schema (включая strict OpenAI-схемы) в допустимый для Cloud Code формат."""
    if not isinstance(schema, dict):
        return {'type': 'OBJECT'}
    root_defs = defs if defs is not None else {
        **(schema.get('$defs') if isinstance(schema.get('$defs'), dict) else {}),
        **(schema.get('definitions') if isinstance(schema.get('definitions'), dict) else {}),
    }

    if '$ref' in schema:
        ref = str(schema['$ref'])
        if ref not in seen_refs:
            resolved = _resolve_ref(ref, root_defs)
            if resolved is not None:
                merged = {k: v for k, v in schema.items() if k != '$ref'}
                merged = resolved | merged
                return sanitize_schema_for_cloudcode(merged, defs=root_defs, seen_refs=seen_refs | {ref})
        return {'type': 'STRING', 'description': str(schema.get('description') or '')[:200] or 'Value'}

    nullable = False
    working = dict(schema)

    for union_key in ('anyOf', 'oneOf'):
        variants = working.get(union_key)
        if isinstance(variants, list) and variants:
            non_null = []
            for item in variants:
                if isinstance(item, dict) and item.get('type') == 'null':
                    nullable = True
                else:
                    non_null.append(item)
            if len(non_null) == 1 and isinstance(non_null[0], dict):
                base_desc = working.get('description')
                working = dict(non_null[0])
                if base_desc and not working.get('description'):
                    working['description'] = base_desc
                if '$ref' in working:
                    cleaned = sanitize_schema_for_cloudcode(working, defs=root_defs, seen_refs=seen_refs)
                    if nullable:
                        cleaned['nullable'] = True
                    return cleaned
            elif non_null and isinstance(non_null[0], dict):
                enum_vals = [v.get('const') for v in non_null if isinstance(v, dict) and 'const' in v]
                if len(enum_vals) == len(non_null):
                    working = {
                        'type': 'string' if all(isinstance(v, str) for v in enum_vals) else 'integer',
                        'enum': enum_vals,
                        **({'description': working['description']} if working.get('description') else {}),
                    }
                else:
                    working = dict(non_null[0])

    if 'const' in working and 'enum' not in working:
        val = working['const']
        working['enum'] = [val]
        if 'type' not in working:
            working['type'] = 'boolean' if isinstance(val, bool) else 'integer' if isinstance(val, int) else 'number' if isinstance(val, float) else 'string'

    raw_type, type_nullable = _extract_non_null_type(working.get('type'))
    nullable = nullable or type_nullable

    if not raw_type:
        if 'properties' in working:
            raw_type = 'object'
        elif 'items' in working:
            raw_type = 'array'
        elif 'enum' in working:
            raw_type = 'string'
        else:
            raw_type = 'string'

    type_map = {
        'object': 'OBJECT',
        'string': 'STRING',
        'integer': 'INTEGER',
        'number': 'NUMBER',
        'boolean': 'BOOLEAN',
        'array': 'ARRAY',
    }
    upper_type = type_map.get(raw_type.lower(), 'STRING')
    out: dict = {'type': upper_type}
    if nullable:
        out['nullable'] = True

    constraints = []
    for key, label in (('minimum', 'min'), ('maximum', 'max'), ('minLength', 'minLen'), ('maxLength', 'maxLen'), ('pattern', 'pattern')):
        if key in working and working[key] is not None:
            constraints.append(f'{label}={working[key]}')
    desc = working.get('description')
    desc_text = desc.strip() if isinstance(desc, str) else ''
    if constraints:
        suffix = f"({', '.join(constraints)})"
        desc_text = f'{desc_text} {suffix}'.strip() if desc_text else suffix
    if desc_text:
        out['description'] = desc_text[:500]

    if isinstance(working.get('enum'), list) and working['enum']:
        out['enum'] = [str(v) if upper_type == 'STRING' and v is not None else v for v in working['enum'] if v is not None]

    if isinstance(working.get('format'), str) and working['format'] in ('date-time', 'date', 'email', 'uuid', 'enum'):
        out['format'] = working['format']

    if upper_type == 'OBJECT':
        props = working.get('properties')
        if isinstance(props, dict) and props:
            clean_props = {
                str(k): sanitize_schema_for_cloudcode(v, defs=root_defs, seen_refs=seen_refs)
                for k, v in props.items()
                if isinstance(v, dict)
            }
            if clean_props:
                out['properties'] = clean_props
                req = working.get('required')
                if isinstance(req, list):
                    valid_req = [str(r) for r in req if str(r) in clean_props]
                    if valid_req:
                        out['required'] = valid_req
    elif upper_type == 'ARRAY':
        items = working.get('items')
        out['items'] = sanitize_schema_for_cloudcode(items, defs=root_defs, seen_refs=seen_refs) if isinstance(items, dict) else {'type': 'STRING'}

    return out


def build_cloudcode_envelope(
    *,
    project_id: str,
    model: str,
    contents: list[dict],
    system_instruction: dict | None = None,
    tools: list[dict] | None = None,
    tool_config: dict | None = None,
    generation_config: dict | None = None,
) -> dict:
    req: dict = {'contents': contents}
    if system_instruction:
        req['systemInstruction'] = system_instruction
    if tools:
        req['tools'] = tools
    if tool_config:
        req['toolConfig'] = tool_config
    if generation_config:
        req['generationConfig'] = generation_config
    return {
        'project': project_id,
        'model': model,
        'request': req,
        'userAgent': 'antigravity',
        'requestId': str(uuid.uuid4()),
        'requestType': 'agent',
    }


async def stream_generate_content(
    access_token: str,
    envelope: dict,
    *,
    cloudcode_url: str = DEFAULT_CLOUDCODE_URL,
    user_agent: str = '',
    connect_timeout: float = 30.0,
    first_byte_timeout: float = 180.0,
    idle_timeout: float = 180.0,
):
    """Потоковая генерация через Cloud Code SSE с fallback на generateContent только при 404/405/501."""
    base = validate_cloudcode_url(cloudcode_url)
    stream_path = '/v1internal:streamGenerateContent'
    headers = cloudcode_headers(access_token, user_agent, accept='text/event-stream')
    timeout = httpx.Timeout(connect=connect_timeout, read=idle_timeout, write=30.0, pool=30.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            async with asyncio.timeout(first_byte_timeout):
                cm = client.stream('POST', f'{base}{stream_path}', params={'alt': 'sse'}, json=envelope, headers=headers)
                response = await cm.__aenter__()
        except TimeoutError:
            raise AntigravityError('Google Antigravity не начал ответ вовремя', retryable=True) from None
        except httpx.TimeoutException:
            raise AntigravityError('Истекло время ожидания Google Antigravity', retryable=True) from None
        except httpx.HTTPError:
            raise AntigravityError('Ошибка соединения с Google Antigravity', retryable=True) from None

        try:
            if 300 <= response.status_code < 400:
                raise AntigravityError(
                    f'Google Antigravity вернул неожиданный редирект ({response.status_code})',
                    status_code=502,
                )
            if response.status_code in (404, 405, 501):
                await cm.__aexit__(None, None, None)
                single = await cloudcode_post(
                    '/v1internal:generateContent',
                    envelope,
                    access_token,
                    cloudcode_url=base,
                    user_agent=user_agent,
                    timeout=first_byte_timeout,
                )
                yield single.get('response', single)
                return

            if not response.is_success:
                raw = await response.aread()
                raise _cloudcode_http_error(response.status_code, raw, stream_path, response.headers)

            data_lines: list[str] = []

            def _flush_event() -> dict | None:
                if not data_lines:
                    return None
                raw_text = '\n'.join(data_lines).strip()
                data_lines.clear()
                if not raw_text or raw_text == '[DONE]':
                    return None
                try:
                    chunk = json.loads(raw_text)
                except ValueError:
                    return None
                if not isinstance(chunk, dict):
                    return None
                if 'error' in chunk and isinstance(chunk['error'], dict):
                    err = chunk['error']
                    code = err.get('code') if isinstance(err.get('code'), int) else 502
                    raise _cloudcode_http_error(code, json.dumps({'error': err}).encode('utf-8'), stream_path)
                return chunk.get('response', chunk)

            async for line in response.aiter_lines():
                if line == '':
                    event = _flush_event()
                    if event is not None:
                        yield event
                    continue
                if line.startswith(':'):
                    continue
                if line.startswith('data:'):
                    payload_line = line[5:].lstrip()
                    data_lines.append(payload_line)
                    # Некоторые прокси/серверы отдают одиночный JSON на каждой строке data: без пустой строки.
                    if payload_line.startswith('{') and payload_line.endswith('}'):
                        event = _flush_event()
                        if event is not None:
                            yield event

            tail = _flush_event()
            if tail is not None:
                yield tail
        except httpx.TimeoutException:
            raise AntigravityError('Поток Google Antigravity не передавал данные слишком долго', retryable=True) from None
        except httpx.HTTPError:
            raise AntigravityError('Поток ответа Google Antigravity оборвался', retryable=True) from None
        finally:
            await cm.__aexit__(None, None, None)


# --- Взаимодействие сервисов с backend (единый владелец БД и токенов) ---

async def fetch_backend_session(*, force_refresh: bool = False) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            response = await client.post(
                'http://backend:8000/api/auth/antigravity/session/internal',
                json={'force_refresh': force_refresh},
                headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())},
            )
    except httpx.TimeoutException:
        raise AntigravityError('Backend не ответил при получении сессии Antigravity', retryable=True) from None
    except httpx.HTTPError:
        raise AntigravityError('Не удалось получить сессию Antigravity от backend', retryable=True) from None
    if not response.is_success:
        try:
            detail = response.json().get('detail') or f'HTTP {response.status_code}'
        except ValueError:
            detail = f'HTTP {response.status_code}'
        if response.status_code == 401:
            raise AntigravityAuthError(str(detail), status_code=401, auth_expired=True)
        raise AntigravityError(str(detail), status_code=response.status_code)
    data = response.json()
    if not isinstance(data, dict) or not data.get('access_token'):
        raise AntigravityAuthError('Аккаунт Google Antigravity не подключён', status_code=400)
    return data


async def save_backend_project(project_id: str, subscription_tier: str = '') -> None:
    if not project_id:
        return
    try:
        async with httpx.AsyncClient(timeout=15, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            await client.post(
                'http://backend:8000/api/auth/antigravity/project/internal',
                json={'project_id': project_id, 'subscription_tier': subscription_tier},
                headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())},
            )
    except httpx.HTTPError:
        pass


async def fetch_live_catalog(*, force_refresh: bool = False, include_internal: bool | None = None) -> dict:
    session = await fetch_backend_session(force_refresh=force_refresh)
    token = session['access_token']
    cloudcode_url = validate_cloudcode_url(session.get('cloudcode_url'))
    user_agent = str(session.get('user_agent') or '')
    project_id = str(session.get('project_id') or '').strip()
    subscription_tier = str(session.get('subscription_tier') or '').strip()
    show_internal = bool(session.get('include_internal')) if include_internal is None else bool(include_internal)

    async def _discover_and_fetch(tok: str) -> tuple[str, str, dict]:
        pid = project_id
        tier = subscription_tier
        if not pid:
            info = await load_code_assist(tok, cloudcode_url=cloudcode_url, user_agent=user_agent)
            pid = info['project_id']
            tier = info.get('subscription_tier', '') or tier
            await save_backend_project(pid, tier)
        raw_catalog = await fetch_available_models(tok, pid, cloudcode_url=cloudcode_url, user_agent=user_agent)
        return pid, tier, raw_catalog

    try:
        pid, tier, raw_catalog = await _discover_and_fetch(token)
    except AntigravityAuthError:
        if force_refresh:
            raise
        session = await fetch_backend_session(force_refresh=True)
        token = session['access_token']
        pid, tier, raw_catalog = await _discover_and_fetch(token)

    models = normalize_models(raw_catalog, include_internal=show_internal)

    # Обновляем общий кэш лимитов моделей для resolve_session_capabilities.
    try:
        from common.codex_harness import cache_provider_models
        cache_provider_models(cloudcode_url, models, source_kind='antigravity', auth_mode='antigravity')
    except ImportError:
        pass

    return {
        'project_id': pid,
        'subscription_tier': tier,
        'models': models,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'cloudcode_url': cloudcode_url,
    }


async def test_generation_direct(model: str) -> dict:
    """Явная проверка реальной генерации через Cloud Code для подтверждения работоспособности квоты и модели."""
    target_model = (model or '').strip()
    if not target_model:
        raise AntigravityError('Укажите модель для проверки генерации Google Antigravity', status_code=400)

    session = await fetch_backend_session(force_refresh=False)
    cloudcode_url = validate_cloudcode_url(session.get('cloudcode_url'))
    user_agent = str(session.get('user_agent') or '')
    project_id = str(session.get('project_id') or '').strip()
    subscription_tier = str(session.get('subscription_tier') or '').strip()

    async def _run_once(tok: str) -> dict:
        pid = project_id
        tier = subscription_tier
        if not pid:
            info = await load_code_assist(tok, cloudcode_url=cloudcode_url, user_agent=user_agent)
            pid = info['project_id']
            tier = info.get('subscription_tier', '') or tier
            await save_backend_project(pid, tier)

        envelope = build_cloudcode_envelope(
            project_id=pid,
            model=target_model,
            contents=[{'role': 'user', 'parts': [{'text': 'Reply with one word: OK'}]}],
            generation_config={'maxOutputTokens': 32, 'temperature': 0},
        )
        chunks: list[str] = []
        finish_reason = ''
        block_reason = ''
        async for event in stream_generate_content(
            tok,
            envelope,
            cloudcode_url=cloudcode_url,
            user_agent=user_agent,
            first_byte_timeout=45.0,
            idle_timeout=45.0,
        ):
            fb = event.get('promptFeedback') or {}
            if isinstance(fb, dict) and fb.get('blockReason'):
                block_reason = str(fb['blockReason'])
            candidates = event.get('candidates') or []
            if not candidates or not isinstance(candidates[0], dict):
                continue
            cand = candidates[0]
            if cand.get('finishReason'):
                finish_reason = str(cand['finishReason'])
            parts = ((cand.get('content') or {}).get('parts') or [])
            for part in parts:
                if isinstance(part, dict) and not part.get('thought') and isinstance(part.get('text'), str):
                    chunks.append(part['text'])

        if block_reason:
            raise AntigravityError(f'Google Antigravity заблокировал тестовый запрос ({block_reason})', status_code=400)
        text = ''.join(chunks).strip()
        if finish_reason != 'STOP' or not text:
            reason_label = finish_reason or 'EMPTY_RESPONSE'
            raise AntigravityError(
                f'Google Antigravity не подтвердил генерацию для модели {target_model} ({reason_label})',
                status_code=502,
            )
        return {
            'ok': True,
            'model': target_model,
            'project_id': pid,
            'subscription_tier': tier,
            'finish_reason': finish_reason,
            'sample_text': text[:120],
            'checked_at': datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await _run_once(session['access_token'])
    except AntigravityAuthError:
        refreshed = await fetch_backend_session(force_refresh=True)
        return await _run_once(refreshed['access_token'])
