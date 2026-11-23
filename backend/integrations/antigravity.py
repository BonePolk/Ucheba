"""Управление глобальной конфигурацией и пользовательскими OAuth-сессиями Google Antigravity в backend."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
import httpx
from sqlmodel import Session

from common.accounts import features
from common.antigravity import (
    DEFAULT_CLOUDCODE_URL,
    DEFAULT_PUBLIC_CLIENT_ID,
    DEFAULT_REDIRECT_URI,
    OAUTH_SCOPES,
    AntigravityAuthError,
    AntigravityError,
    build_oauth_url,
    env_client_id,
    env_client_secret,
    exchange_oauth_code,
    extract_id_token_claims,
    fetch_userinfo,
    generate_pkce,
    generate_state,
    parse_oauth_callback,
    refresh_oauth_token,
    revoke_oauth_token,
    validate_cloudcode_url,
    validate_redirect_uri,
)
from common.database import engine
from common.identity import user_id
from common.models.integration import IntegrationSetting
from common.models.user import SystemSetting, User
from common.rpc import call
from common.security import decrypt, encrypt
from common.settings import read_settings, read_system_setting, report, save_settings, save_system_setting

_PENDING_TTL_SECONDS = 900
_pending_logins: dict[tuple[int, str], dict] = {}
_refresh_locks: dict[int, asyncio.Lock] = {}


def _cleanup_pending(owner: int | None = None) -> None:
    cutoff = time.monotonic() - _PENDING_TTL_SECONDS
    for key in [k for k, v in _pending_logins.items() if v['created_at'] < cutoff]:
        _pending_logins.pop(key, None)
    if owner is not None:
        owned = sorted(((v['created_at'], k) for k, v in _pending_logins.items() if k[0] == owner), key=lambda item: item[0])
        for _, key in owned[:-5]:
            _pending_logins.pop(key, None)


def _pick_agent_service() -> str:
    enabled = features()
    service = next((name for name in ('monocle', 'ameba', 'straus', 'arrodes') if enabled.get(name)), None)
    if not service:
        raise HTTPException(403, 'Все AI-агенты отключены администратором')
    return service


def _normalize_scopes(raw) -> list[str]:
    if raw is None:
        return []
    items = raw.replace('\n', ' ').replace(',', ' ').split() if isinstance(raw, str) else list(raw)
    cleaned: list[str] = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        if any(ch.isspace() for ch in s):
            raise ValueError('Некорректный формат OAuth scope')
        cleaned.append(s)
    return list(dict.fromkeys(cleaned))


def _effective_global_oauth() -> dict:
    sys_cfg, sys_secrets = read_system_setting('antigravity')
    raw_client_id = str(sys_cfg.get('client_id') or '').strip()
    raw_client_secret = str(sys_secrets.get('client_secret') or '').strip()
    effective_client_id = (raw_client_id or env_client_id() or DEFAULT_PUBLIC_CLIENT_ID).strip()
    effective_client_secret = (raw_client_secret or env_client_secret() or '').strip()
    redirect_uri = validate_redirect_uri(sys_cfg.get('redirect_uri') or DEFAULT_REDIRECT_URI)
    cloudcode_url = validate_cloudcode_url(sys_cfg.get('cloudcode_url') or DEFAULT_CLOUDCODE_URL)
    user_agent = str(sys_cfg.get('user_agent') or '').strip()
    stored_scopes = [str(s).strip() for s in (sys_cfg.get('allowed_scopes') or []) if isinstance(s, str) and str(s).strip()]
    allowed_scopes = stored_scopes or list(OAUTH_SCOPES)
    secret_source = 'admin' if raw_client_secret else ('env' if env_client_secret() else 'unset')
    return {
        'enabled': bool(sys_cfg.get('enabled', True)),
        'configured': bool(effective_client_id and effective_client_secret),
        'client_id': effective_client_id,
        'custom_client_id': bool(raw_client_id),
        'client_secret': effective_client_secret,
        'client_secret_configured': bool(effective_client_secret),
        'client_secret_source': secret_source,
        'redirect_uri': redirect_uri,
        'cloudcode_url': cloudcode_url,
        'user_agent': user_agent,
        'allowed_scopes': allowed_scopes,
        'custom_scopes': bool(stored_scopes),
        'diagnostic_mode': bool(sys_cfg.get('diagnostic_mode', False)),
    }


def read_global_config() -> dict:
    """Возвращает глобальные системные настройки Google Antigravity БЕЗ client_secret."""
    g = dict(_effective_global_oauth())
    g.pop('client_secret', None)
    return g


def public_global_config() -> dict:
    """Публичное представление системной конфигурации исключительно для админки (без секретов)."""
    return read_global_config()


def save_global_config(patch: dict) -> dict:
    config_update: dict = {}
    secrets_update: dict | None = None

    if 'enabled' in patch and patch['enabled'] is not None:
        config_update['enabled'] = bool(patch['enabled'])
    if 'diagnostic_mode' in patch and patch['diagnostic_mode'] is not None:
        config_update['diagnostic_mode'] = bool(patch['diagnostic_mode'])
    if 'client_id' in patch and patch['client_id'] is not None:
        config_update['client_id'] = str(patch['client_id']).strip()
    if 'user_agent' in patch and patch['user_agent'] is not None:
        config_update['user_agent'] = str(patch['user_agent']).strip()
    if 'redirect_uri' in patch and patch['redirect_uri'] is not None:
        try:
            config_update['redirect_uri'] = validate_redirect_uri(str(patch['redirect_uri']))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    if 'cloudcode_url' in patch and patch['cloudcode_url'] is not None:
        try:
            config_update['cloudcode_url'] = validate_cloudcode_url(str(patch['cloudcode_url']))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    if 'allowed_scopes' in patch and patch['allowed_scopes'] is not None:
        try:
            config_update['allowed_scopes'] = _normalize_scopes(patch['allowed_scopes'])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    if 'client_secret' in patch and isinstance(patch['client_secret'], str) and patch['client_secret'].strip():
        secrets_update = {'client_secret': patch['client_secret'].strip()}

    save_system_setting('antigravity', config=config_update, secrets=secrets_update)
    return public_global_config()


def reset_global_secret() -> dict:
    save_system_setting('antigravity', secrets={'client_secret': ''})
    return public_global_config()


def validate_global_config() -> dict:
    cfg = read_global_config()
    checks = {
        'enabled': bool(cfg['enabled']),
        'client_id': bool(cfg['client_id']),
        'client_secret_configured': bool(cfg['client_secret_configured']),
        'redirect_uri_valid': False,
        'cloudcode_url_valid': False,
        'scopes_non_empty': bool(cfg['allowed_scopes']),
    }
    try:
        validate_redirect_uri(cfg['redirect_uri'])
        checks['redirect_uri_valid'] = True
    except ValueError:
        pass
    try:
        validate_cloudcode_url(cfg['cloudcode_url'])
        checks['cloudcode_url_valid'] = True
    except ValueError:
        pass
    ok = all(checks.values())
    return {'ok': ok, 'checks': checks, 'config': cfg}


def migrate_legacy_antigravity_config() -> dict:
    """Одноразовый безопасный перенос системных OAuth-полей из реестровой админской записи в SystemSetting."""
    legacy_config_keys = ('client_id', 'redirect_uri', 'cloudcode_url', 'user_agent', 'project_id')
    with Session(engine) as session:
        if session.get(SystemSetting, 'antigravity'):
            return {'migrated': False, 'reason': 'already_exists'}
        admin_user = session.get(User, 1)
        if not admin_user or not admin_user.is_admin:
            return {'migrated': False, 'reason': 'no_primary_admin'}
        legacy_row = session.get(IntegrationSetting, 'antigravity')
        if not legacy_row:
            return {'migrated': False, 'reason': 'no_legacy_row'}

        stored = json.loads(legacy_row.config) if legacy_row.config else {}
        secrets = json.loads(decrypt(legacy_row.secrets)) if legacy_row.secrets else {}
        has_legacy_fields = any(str(stored.get(k) or '').strip() for k in legacy_config_keys) or bool(
            str(secrets.get('client_secret') or '').strip()
        )
        if not has_legacy_fields:
            return {'migrated': False, 'reason': 'nothing_to_migrate'}

        try:
            redirect_uri = validate_redirect_uri(stored.get('redirect_uri'))
        except ValueError:
            redirect_uri = DEFAULT_REDIRECT_URI
        try:
            cloudcode_url = validate_cloudcode_url(stored.get('cloudcode_url'))
        except ValueError:
            cloudcode_url = DEFAULT_CLOUDCODE_URL

        sys_cfg = {
            'enabled': True,
            'client_id': str(stored.get('client_id') or '').strip(),
            'redirect_uri': redirect_uri,
            'cloudcode_url': cloudcode_url,
            'user_agent': str(stored.get('user_agent') or '').strip(),
            'allowed_scopes': [],
            'diagnostic_mode': False,
        }
        legacy_secret = str(secrets.get('client_secret') or '').strip()
        sys_row = SystemSetting(
            name='antigravity',
            config=json.dumps(sys_cfg),
            secrets=encrypt(json.dumps({'client_secret': legacy_secret})) if legacy_secret else '',
            updated_at=datetime.now(timezone.utc),
        )

        # Переносим user-часть: сохраняем access_token/refresh_token и профиль аккаунта администратора.
        if not str(stored.get('discovered_project_id') or '').strip() and str(stored.get('project_id') or '').strip():
            stored['discovered_project_id'] = str(stored['project_id']).strip()
        for key in legacy_config_keys:
            stored.pop(key, None)
        secrets.pop('client_secret', None)
        legacy_row.config = json.dumps(stored)
        legacy_row.secrets = encrypt(json.dumps(secrets)) if secrets else ''

        session.add(sys_row)
        session.add(legacy_row)
        session.commit()
        return {'migrated': True, 'had_client_secret': bool(legacy_secret)}


def _require_system_ready(*, require_oauth: bool = True) -> dict:
    g = _effective_global_oauth()
    if not g['enabled']:
        raise HTTPException(403, 'Google Antigravity отключён администратором')
    if require_oauth and not (g['client_id'] and g['client_secret']):
        raise HTTPException(400, 'Google Antigravity не настроен администратором: укажите OAuth Client ID и Client Secret в админ-панели')
    return g


def start_login() -> dict:
    owner = user_id()
    g = _require_system_ready(require_oauth=True)
    _cleanup_pending(owner)
    verifier, challenge = generate_pkce()
    state = generate_state()
    _pending_logins[(owner, state)] = {
        'verifier': verifier,
        'client_id': g['client_id'],
        'client_secret': g['client_secret'],
        'redirect_uri': g['redirect_uri'],
        'created_at': time.monotonic(),
    }
    return {
        'auth_url': build_oauth_url(
            g['client_id'],
            state,
            challenge,
            redirect_uri=g['redirect_uri'],
            scopes=g['allowed_scopes'],
        ),
        'state': state,
        'redirect_uri': g['redirect_uri'],
        'expires_in': _PENDING_TTL_SECONDS,
    }


def _find_pending(owner: int, callback_input: str, explicit_state: str | None) -> tuple[str, dict]:
    _cleanup_pending()
    state = (explicit_state or '').strip()
    if not state:
        raw = (callback_input or '').strip()
        if raw.startswith(('http://', 'https://')):
            q = parse_qs(urlsplit(raw).query)
            state = (q.get('state') or [''])[0].strip()
    if not state:
        raise HTTPException(422, 'В URL обратного вызова отсутствует обязательный параметр OAuth state')
    entry = _pending_logins.get((owner, state))
    if not entry:
        raise HTTPException(400, 'Сессия OAuth не найдена, принадлежит другому входу или устарела. Нажмите «Войти через Google» заново.')
    return state, entry


async def complete_login(callback_input: str, state: str | None = None) -> dict:
    owner = user_id()
    _require_system_ready(require_oauth=True)
    expected_state, entry = _find_pending(owner, callback_input, state)
    try:
        code = parse_oauth_callback(
            callback_input,
            expected_state,
            expected_redirect_uri=entry['redirect_uri'],
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    try:
        tokens = await exchange_oauth_code(
            client_id=entry['client_id'],
            client_secret=entry['client_secret'],
            redirect_uri=entry['redirect_uri'],
            code=code,
            code_verifier=entry['verifier'],
        )
    except AntigravityAuthError as exc:
        report('antigravity', error=exc.user_message)
        raise HTTPException(400, exc.user_message) from None
    except AntigravityError as exc:
        report('antigravity', error=exc.user_message)
        raise HTTPException(502, exc.user_message) from None
    finally:
        _pending_logins.pop((owner, expected_state), None)

    claims = extract_id_token_claims(tokens.get('id_token'))
    email = str(claims.get('email') or '').strip()
    name = str(claims.get('name') or '').strip()
    if not email:
        info = await fetch_userinfo(tokens['access_token'])
        email = str(info.get('email') or '').strip() or email
        name = str(info.get('name') or '').strip() or name

    expires_at = time.time() + max(60, int(tokens.get('expires_in') or 3600))
    cfg_update = {
        'account_email': email,
        'account_name': name,
        'discovered_project_id': '',
        'subscription_tier': '',
        'token_expires_at': expires_at,
        'scopes': str(tokens.get('scope') or ''),
        'catalog_accessible': False,
        'catalog_checked_at': None,
        'cached_models': [],
        'generation_verified': False,
        'generation_verified_at': None,
        'generation_verified_model': '',
    }
    sec_update = {'access_token': tokens['access_token'], 'refresh_token': tokens.get('refresh_token') or ''}
    save_settings('antigravity', config=cfg_update, secrets=sec_update)

    # Проверяем реальную подписку / доступ к Cloud Code через агентский сервис (не считая OAuth доказательством подписки).
    catalog = None
    verify_error = None
    try:
        service = _pick_agent_service()
        catalog = await call(service, '/antigravity/catalog', {'force_refresh': False}, timeout=45)
        save_settings(
            'antigravity',
            config={
                'discovered_project_id': catalog.get('project_id') or '',
                'subscription_tier': catalog.get('subscription_tier') or '',
                'catalog_accessible': True,
                'catalog_checked_at': catalog.get('checked_at') or datetime.now(timezone.utc).isoformat(),
                'cached_models': catalog.get('models', []),
            },
        )
        report('antigravity', error=None, success=True)
    except HTTPException as exc:
        verify_error = str(exc.detail)
        save_settings('antigravity', config={'catalog_accessible': False})
        report('antigravity', error=verify_error)
    except httpx.HTTPError:
        verify_error = 'Не удалось проверить доступ к Cloud Code после авторизации'
        save_settings('antigravity', config={'catalog_accessible': False})
        report('antigravity', error=verify_error)

    state_payload = await status(live=False)
    if catalog is not None:
        state_payload['models'] = catalog.get('models', [])
        state_payload['checked_at'] = catalog.get('checked_at')
    if verify_error:
        state_payload['quota_error'] = verify_error
        state_payload['error'] = verify_error
    return state_payload


async def ensure_valid_session(*, force_refresh: bool = False) -> dict:
    owner = user_id()
    g = _require_system_ready(require_oauth=True)
    lock = _refresh_locks.setdefault(owner, asyncio.Lock())
    async with lock:
        cfg, secrets = read_settings('antigravity', apply_feature=False)
        access_token = (secrets.get('access_token') or '').strip()
        refresh_token = (secrets.get('refresh_token') or '').strip()
        if not access_token and not refresh_token:
            raise HTTPException(400, 'Подключите аккаунт Google Antigravity в разделе «Подключения AI»')

        expires_at = float(cfg.get('token_expires_at') or 0)
        needs_refresh = force_refresh or not access_token or time.time() >= expires_at - 60
        if needs_refresh:
            if not refresh_token:
                msg = 'Срок действия access token истёк, а refresh_token отсутствует. Войдите через Google заново.'
                report('antigravity', error=msg)
                raise HTTPException(401, msg)
            try:
                refreshed = await refresh_oauth_token(
                    client_id=g['client_id'],
                    client_secret=g['client_secret'],
                    refresh_token=refresh_token,
                )
            except AntigravityAuthError as exc:
                if exc.revoked:
                    save_settings(
                        'antigravity',
                        config={
                            'token_expires_at': None,
                            'discovered_project_id': '',
                            'subscription_tier': '',
                            'catalog_accessible': False,
                            'cached_models': [],
                            'generation_verified': False,
                            'generation_verified_at': None,
                            'generation_verified_model': '',
                        },
                        secrets={'access_token': '', 'refresh_token': ''},
                    )
                report('antigravity', error=exc.user_message)
                raise HTTPException(401, exc.user_message) from None
            except AntigravityError as exc:
                report('antigravity', error=exc.user_message)
                raise HTTPException(502, exc.user_message) from None

            access_token = refreshed['access_token']
            expires_at = time.time() + max(60, int(refreshed.get('expires_in') or 3600))
            sec_update = {'access_token': access_token}
            if refreshed.get('refresh_token'):
                sec_update['refresh_token'] = refreshed['refresh_token']
            save_settings(
                'antigravity',
                config={'token_expires_at': expires_at},
                secrets=sec_update,
            )
            report('antigravity', error=None)

        effective_project = (cfg.get('discovered_project_id') or '').strip()
        return {
            'access_token': access_token,
            'expires_at': expires_at,
            'cloudcode_url': g['cloudcode_url'],
            'project_id': effective_project,
            'subscription_tier': str(cfg.get('subscription_tier') or ''),
            'user_agent': g['user_agent'],
            'include_internal': bool(cfg.get('include_internal', False)),
            'diagnostic_mode': g['diagnostic_mode'],
        }


def save_discovered_project(project_id: str, subscription_tier: str = '') -> dict:
    clean_project = (project_id or '').strip()
    if not clean_project:
        return {'ok': False}
    cfg, _ = read_settings('antigravity', apply_feature=False)
    update = {'discovered_project_id': clean_project}
    if subscription_tier:
        update['subscription_tier'] = subscription_tier.strip()
    if cfg.get('discovered_project_id') != update['discovered_project_id'] or (
        'subscription_tier' in update and cfg.get('subscription_tier') != update['subscription_tier']
    ):
        save_settings('antigravity', config=update)
    return {'ok': True}


async def test_generation(model: str) -> dict:
    target_model = (model or '').strip()
    if not target_model:
        raise HTTPException(422, 'Выберите модель для проверки реальной генерации')
    _require_system_ready(require_oauth=True)
    await ensure_valid_session(force_refresh=False)
    service = _pick_agent_service()
    try:
        res = await call(service, '/antigravity/test-generation', {'model': target_model}, timeout=60)
    except HTTPException as exc:
        save_settings('antigravity', config={'generation_verified': False})
        report('antigravity', error=str(exc.detail))
        raise
    except httpx.HTTPError:
        msg = 'Не удалось выполнить тестовую генерацию через агентский сервис'
        save_settings('antigravity', config={'generation_verified': False})
        report('antigravity', error=msg)
        raise HTTPException(502, msg) from None

    checked_at = res.get('checked_at') or datetime.now(timezone.utc).isoformat()
    save_settings(
        'antigravity',
        config={
            'generation_verified': True,
            'generation_verified_at': checked_at,
            'generation_verified_model': target_model,
            'discovered_project_id': res.get('project_id') or '',
            'subscription_tier': res.get('subscription_tier') or '',
        },
    )
    report('antigravity', error=None, success=True)
    return {**res, 'status': await status(live=False)}


async def logout() -> dict:
    owner = user_id()
    for key in [k for k in _pending_logins if k[0] == owner]:
        _pending_logins.pop(key, None)
    _, secrets = read_settings('antigravity', apply_feature=False)
    tok = (secrets.get('refresh_token') or secrets.get('access_token') or '').strip()
    if tok:
        await revoke_oauth_token(tok)
    save_settings(
        'antigravity',
        config={
            'account_email': '',
            'account_name': '',
            'discovered_project_id': '',
            'subscription_tier': '',
            'token_expires_at': None,
            'scopes': '',
            'catalog_accessible': False,
            'catalog_checked_at': None,
            'cached_models': [],
            'generation_verified': False,
            'generation_verified_at': None,
            'generation_verified_model': '',
        },
        secrets={'access_token': '', 'refresh_token': ''},
    )
    report('antigravity', error=None)
    return {'ok': True}


async def status(*, live: bool = False, force_refresh: bool = False) -> dict:
    g = read_global_config()
    cfg, secrets = read_settings('antigravity', apply_feature=False)
    from common.settings import public_settings

    pub = public_settings()['antigravity']
    has_access = bool((secrets.get('access_token') or '').strip())
    has_refresh = bool((secrets.get('refresh_token') or '').strip())
    has_session = has_access or has_refresh
    expires_at = cfg.get('token_expires_at')
    expires_iso = (
        datetime.fromtimestamp(float(expires_at), tz=timezone.utc).isoformat()
        if isinstance(expires_at, (int, float))
        else None
    )

    result = {
        'system_enabled': bool(g['enabled']),
        'system_configured': bool(g['configured']),
        'oauth_authorized': has_session,
        'authorized': has_session,
        'has_refresh_token': has_refresh,
        'account_email': cfg.get('account_email') or '',
        'account_name': cfg.get('account_name') or '',
        'project_id': (cfg.get('discovered_project_id') or '').strip(),
        'discovered_project_id': cfg.get('discovered_project_id') or '',
        'subscription_tier': cfg.get('subscription_tier') or '',
        'include_internal': bool(cfg.get('include_internal', False)),
        'token_expires_at': expires_iso,
        'token_expired': bool(isinstance(expires_at, (int, float)) and time.time() >= float(expires_at)),
        'catalog_accessible': bool(cfg.get('catalog_accessible', False)),
        'subscription_verified': bool(cfg.get('catalog_accessible', False)),
        'generation_verified': bool(cfg.get('generation_verified', False)),
        'generation_verified_at': cfg.get('generation_verified_at'),
        'generation_verified_model': cfg.get('generation_verified_model') or '',
        'models': cfg.get('cached_models') if isinstance(cfg.get('cached_models'), list) else [],
        'checked_at': cfg.get('catalog_checked_at'),
        'oauth_error': None,
        'quota_error': None,
        'error': pub.get('last_error'),
        'last_success': pub.get('last_success'),
    }

    if (live or force_refresh) and has_session and g['enabled'] and g['configured']:
        try:
            await ensure_valid_session(force_refresh=force_refresh)
        except HTTPException as exc:
            logging.getLogger('uvicorn.error').warning('Antigravity OAuth session check failed: HTTP %s', exc.status_code)
            result['oauth_error'] = str(exc.detail)
            result['error'] = str(exc.detail)
            _, post_secrets = read_settings('antigravity', apply_feature=False)
            post_has = bool((post_secrets.get('access_token') or '').strip() or (post_secrets.get('refresh_token') or '').strip())
            result['oauth_authorized'] = post_has
            result['authorized'] = post_has
            return result

        try:
            service = _pick_agent_service()
            catalog = await call(service, '/antigravity/catalog', {'force_refresh': False}, timeout=45)
            models = catalog.get('models', [])
            checked_at = catalog.get('checked_at') or datetime.now(timezone.utc).isoformat()
            pid = catalog.get('project_id') or result['project_id']
            tier = catalog.get('subscription_tier') or result['subscription_tier']
            save_settings(
                'antigravity',
                config={
                    'discovered_project_id': pid,
                    'subscription_tier': tier,
                    'catalog_accessible': True,
                    'catalog_checked_at': checked_at,
                    'cached_models': models,
                },
            )
            result['models'] = models
            result['project_id'] = pid
            result['discovered_project_id'] = pid
            result['subscription_tier'] = tier
            result['checked_at'] = checked_at
            result['catalog_accessible'] = True
            result['subscription_verified'] = True
            result['error'] = None
            report('antigravity', error=None, success=True)
            refreshed_cfg, refreshed_secrets = read_settings('antigravity', apply_feature=False)
            post_has = bool((refreshed_secrets.get('access_token') or '').strip() or (refreshed_secrets.get('refresh_token') or '').strip())
            result['oauth_authorized'] = post_has
            result['authorized'] = post_has
            if isinstance(refreshed_cfg.get('token_expires_at'), (int, float)):
                result['token_expires_at'] = datetime.fromtimestamp(float(refreshed_cfg['token_expires_at']), tz=timezone.utc).isoformat()
                result['token_expired'] = time.time() >= float(refreshed_cfg['token_expires_at'])
        except HTTPException as exc:
            logging.getLogger('uvicorn.error').warning('Antigravity live catalog check failed: HTTP %s', exc.status_code)
            save_settings('antigravity', config={'catalog_accessible': False})
            result['catalog_accessible'] = False
            result['subscription_verified'] = False
            if exc.status_code == 401:
                result['oauth_error'] = str(exc.detail)
            else:
                result['quota_error'] = str(exc.detail)
            result['error'] = str(exc.detail)
            _, post_secrets = read_settings('antigravity', apply_feature=False)
            post_has = bool((post_secrets.get('access_token') or '').strip() or (post_secrets.get('refresh_token') or '').strip())
            result['oauth_authorized'] = post_has
            result['authorized'] = post_has
        except httpx.HTTPError:
            msg = 'Не удалось связаться с сервисом агента для проверки каталога Google Antigravity'
            save_settings('antigravity', config={'catalog_accessible': False})
            result['catalog_accessible'] = False
            result['subscription_verified'] = False
            result['quota_error'] = msg
            result['error'] = msg
    return result
