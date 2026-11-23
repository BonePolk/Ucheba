"""Управление Google Antigravity OAuth-сессиями и Cloud Code статусом в backend."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time

from fastapi import HTTPException
import httpx

from common.accounts import features
from common.antigravity import (
    DEFAULT_CLOUDCODE_URL,
    DEFAULT_PUBLIC_CLIENT_ID,
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
from common.codex_harness import clear_model_capability_cache
from common.identity import user_id
from common.rpc import call
from common.settings import read_settings, report, save_settings

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


def _effective_client_credentials(cfg: dict, secrets: dict) -> tuple[str, str, str]:
    client_id = (cfg.get('client_id') or env_client_id() or DEFAULT_PUBLIC_CLIENT_ID).strip()
    client_secret = (secrets.get('client_secret') or env_client_secret() or '').strip()
    redirect_uri = validate_redirect_uri(cfg.get('redirect_uri'))
    return client_id, client_secret, redirect_uri


def start_login() -> dict:
    owner = user_id()
    cfg, secrets = read_settings('antigravity', apply_feature=False)
    client_id, client_secret, redirect_uri = _effective_client_credentials(cfg, secrets)
    if not client_id:
        raise HTTPException(422, 'Укажите Google OAuth Client ID в настройках Google Antigravity')
    _cleanup_pending(owner)
    verifier, challenge = generate_pkce()
    state = generate_state()
    _pending_logins[(owner, state)] = {
        'verifier': verifier,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'created_at': time.monotonic(),
    }
    return {
        'auth_url': build_oauth_url(client_id, state, challenge, redirect_uri=redirect_uri),
        'state': state,
        'redirect_uri': redirect_uri,
        'expires_in': _PENDING_TTL_SECONDS,
        'client_secret_configured': bool(client_secret),
    }


def _find_pending(owner: int, state: str | None) -> tuple[str, dict]:
    _cleanup_pending()
    if state:
        entry = _pending_logins.get((owner, state))
        if not entry:
            raise HTTPException(400, 'Сессия OAuth не найдена или устарела. Нажмите «Войти через Google» заново.')
        return state, entry
    candidates = [(k[1], v) for k, v in _pending_logins.items() if k[0] == owner]
    if not candidates:
        raise HTTPException(400, 'Сессия OAuth не найдена или устарела. Нажмите «Войти через Google» заново.')
    candidates.sort(key=lambda item: item[1]['created_at'], reverse=True)
    return candidates[0]


async def complete_login(callback_input: str, state: str | None = None) -> dict:
    owner = user_id()
    expected_state, entry = _find_pending(owner, (state or '').strip() or None)
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
    }
    sec_update = {'access_token': tokens['access_token']}
    if tokens.get('refresh_token'):
        sec_update['refresh_token'] = tokens['refresh_token']
    else:
        sec_update['refresh_token'] = ''
    save_settings('antigravity', config=cfg_update, secrets=sec_update)
    clear_model_capability_cache(owner)

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
            },
        )
        report('antigravity', error=None, success=True)
    except HTTPException as exc:
        verify_error = str(exc.detail)
        report('antigravity', error=verify_error)
    except httpx.HTTPError:
        verify_error = 'Не удалось проверить доступ к Cloud Code после авторизации'
        report('antigravity', error=verify_error)

    state_payload = await status(live=False)
    state_payload['subscription_verified'] = catalog is not None
    if catalog is not None:
        state_payload['models'] = catalog.get('models', [])
        state_payload['checked_at'] = catalog.get('checked_at')
    if verify_error:
        state_payload['error'] = verify_error
    return state_payload


async def ensure_valid_session(*, force_refresh: bool = False) -> dict:
    owner = user_id()
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
            client_id, client_secret, _ = _effective_client_credentials(cfg, secrets)
            try:
                refreshed = await refresh_oauth_token(
                    client_id=client_id,
                    client_secret=client_secret,
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
                        },
                        secrets={'access_token': '', 'refresh_token': ''},
                    )
                    clear_model_capability_cache(owner)
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

        effective_project = (cfg.get('project_id') or cfg.get('discovered_project_id') or '').strip()
        return {
            'access_token': access_token,
            'expires_at': expires_at,
            'cloudcode_url': validate_cloudcode_url(cfg.get('cloudcode_url')),
            'project_id': effective_project,
            'subscription_tier': str(cfg.get('subscription_tier') or ''),
            'user_agent': str(cfg.get('user_agent') or ''),
            'include_internal': bool(cfg.get('include_internal', False)),
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
        },
        secrets={'access_token': '', 'refresh_token': ''},
    )
    report('antigravity', error=None)
    clear_model_capability_cache(owner)
    return {'ok': True}


async def status(*, live: bool = False, force_refresh: bool = False) -> dict:
    cfg, secrets = read_settings('antigravity', apply_feature=False)
    # Read last_error / last_success without touching secrets.
    from common.settings import public_settings
    pub = public_settings()['antigravity']

    has_access = bool((secrets.get('access_token') or '').strip())
    has_refresh = bool((secrets.get('refresh_token') or '').strip())
    has_session = has_access or has_refresh
    _, env_or_saved_secret, _ = _effective_client_credentials(cfg, secrets)
    expires_at = cfg.get('token_expires_at')
    expires_iso = (
        datetime.fromtimestamp(float(expires_at), tz=timezone.utc).isoformat()
        if isinstance(expires_at, (int, float))
        else None
    )

    result = {
        'authorized': has_session,
        'has_refresh_token': has_refresh,
        'client_secret_configured': bool(env_or_saved_secret),
        'account_email': cfg.get('account_email') or '',
        'account_name': cfg.get('account_name') or '',
        'project_id': (cfg.get('project_id') or cfg.get('discovered_project_id') or '').strip(),
        'configured_project_id': cfg.get('project_id') or '',
        'discovered_project_id': cfg.get('discovered_project_id') or '',
        'subscription_tier': cfg.get('subscription_tier') or '',
        'cloudcode_url': cfg.get('cloudcode_url') or DEFAULT_CLOUDCODE_URL,
        'redirect_uri': cfg.get('redirect_uri') or '',
        'client_id': cfg.get('client_id') or DEFAULT_PUBLIC_CLIENT_ID,
        'user_agent': cfg.get('user_agent') or '',
        'include_internal': bool(cfg.get('include_internal', False)),
        'token_expires_at': expires_iso,
        'token_expired': bool(isinstance(expires_at, (int, float)) and time.time() >= float(expires_at)),
        'subscription_verified': False,
        'models': [],
        'error': pub.get('last_error'),
        'last_success': pub.get('last_success'),
        'checked_at': None,
    }

    if (live or force_refresh) and has_session:
        try:
            await ensure_valid_session(force_refresh=force_refresh)
            service = _pick_agent_service()
            catalog = await call(service, '/antigravity/catalog', {'force_refresh': False}, timeout=45)
            result['models'] = catalog.get('models', [])
            result['project_id'] = catalog.get('project_id') or result['project_id']
            result['subscription_tier'] = catalog.get('subscription_tier') or result['subscription_tier']
            result['checked_at'] = catalog.get('checked_at')
            result['subscription_verified'] = True
            result['error'] = None
            report('antigravity', error=None, success=True)
            # Re-read refreshed expiration timestamp.
            refreshed_cfg, refreshed_secrets = read_settings('antigravity', apply_feature=False)
            result['authorized'] = bool((refreshed_secrets.get('access_token') or '').strip() or (refreshed_secrets.get('refresh_token') or '').strip())
            if isinstance(refreshed_cfg.get('token_expires_at'), (int, float)):
                result['token_expires_at'] = datetime.fromtimestamp(float(refreshed_cfg['token_expires_at']), tz=timezone.utc).isoformat()
                result['token_expired'] = time.time() >= float(refreshed_cfg['token_expires_at'])
        except HTTPException as exc:
            logging.getLogger('uvicorn.error').warning('Antigravity live status check failed: HTTP %s', exc.status_code)
            result['error'] = str(exc.detail)
            # Refresh might have cleared revoked tokens.
            _, post_secrets = read_settings('antigravity', apply_feature=False)
            result['authorized'] = bool((post_secrets.get('access_token') or '').strip() or (post_secrets.get('refresh_token') or '').strip())
        except httpx.HTTPError:
            result['error'] = 'Не удалось связаться с сервисом агента для проверки каталога Google Antigravity'
    return result
