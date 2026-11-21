import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from common.database import get_engine
from common.rpc import service_name
from common.security import decrypt, encrypt
from common.models.integration import IntegrationSetting

DEFAULTS = {
    'notifications': {'enabled': False, 'owner_id': None, 'lead_minutes': 30},
    'collector': {'enabled': False, 'chat_ids': []},
    'telegram': {'enabled': False, 'chat_ids': []},
    'ai_provider': {'base_url': 'https://lenec.tech/v1'},
    'monocle': {
        'enabled': True,
        'auth_mode': 'api_key',
        'model': 'gpt-6-luna',
        'context_window': None,
        'reasoning_effort': 'medium',
        'custom_instructions': '',
        'rewrite_title': False,
        'allow_browser': True,
        'ask_clarifications': True,
    },
    'ameba': {
        'enabled': False,
        'auth_mode': 'api_key',
        'model': 'gpt-6-luna',
        'context_window': None,
        'reasoning_effort': 'medium',
        'custom_instructions': '',
        'auto_replan': True,
        'planning_style': 'balanced',
        'allow_context_recheck': True,
    },
    'straus': {
        'enabled': True,
        'auth_mode': 'api_key',
        'model': 'gpt-6-luna',
        'context_window': None,
        'reasoning_effort': 'medium',
        'custom_instructions': '',
        'allow_browser': True,
        'ask_clarifications': True,
    },
    'arrodes': {
        'enabled': False,
        'auth_mode': 'oauth',
        'model': '',
        'context_window': None,
        'reasoning_effort': 'medium',
        'custom_instructions': '',
        'auto_prepare': False,
        'web_search_mode': 'cached',
        'allow_browser': True,
        'allow_shell': True,
        'ask_clarifications': True,
    },
}
SECRET_FIELDS = {
    'notifications': {'bot_token'},
    'collector': {'bot_token'},
    'telegram': {'api_id', 'api_hash', 'phone', 'session'},
    'ai_provider': {'api_key'},
    'monocle': set(),
    'ameba': set(),
    'straus': set(),
    'arrodes': set(),
}
AGENT_LABELS = {
    'monocle': 'Monocle',
    'ameba': 'Ameba',
    'straus': 'Straus',
    'arrodes': 'Arrodes',
}


def setting_key(name):
    return {'monocle': 'context', 'arrodes': 'codex'}.get(service_name(name), name)


def inherit_ai_provider():
    with Session(get_engine()) as session:
        if session.get(IntegrationSetting, 'ai_provider'):
            return
        base_url = DEFAULTS['ai_provider']['base_url']
        api_key = ''
        context_row = session.get(IntegrationSetting, 'context')
        if context_row and context_row.config:
            base_url = json.loads(context_row.config).get('base_url') or base_url
        for legacy_name in ('context', 'ameba', 'straus', 'codex'):
            legacy = session.get(IntegrationSetting, legacy_name)
            if not legacy:
                continue
            private = json.loads(decrypt(legacy.secrets)) if legacy.secrets else {}
            if private.get('api_key'):
                api_key = private['api_key']
                if legacy.config:
                    base_url = json.loads(legacy.config).get('base_url') or base_url
                break
        row = IntegrationSetting(
            name='ai_provider',
            config=json.dumps({'base_url': base_url}),
            secrets=encrypt(json.dumps({'api_key': api_key})) if api_key else '',
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if not session.get(IntegrationSetting, 'ai_provider'):
                raise


def inherit_agent_settings(name):
    # Одноразовый перенос прежних общих настроек в независимые записи.
    if name not in ('ameba', 'straus'):
        return
    with Session(get_engine()) as session:
        if session.get(IntegrationSetting, setting_key(name)):
            return
        context = session.get(IntegrationSetting, 'context')
        # Старые настройки только наследуются; удалённый сервис не вызывается.
        old = session.get(IntegrationSetting, 'jev') if name == 'ameba' else context
        config = json.loads(old.config) if old else {}
        inherited = dict(DEFAULTS[name], enabled=config.get('enabled', DEFAULTS[name]['enabled']))
        if context:
            inherited['model'] = json.loads(context.config).get('model', DEFAULTS[name]['model'])
        row = IntegrationSetting(name=name, config=json.dumps(inherited), secrets='')
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if not session.get(IntegrationSetting, setting_key(name)):
                raise


def read_settings(name, *, apply_feature=True):
    name = service_name(name)
    if name not in DEFAULTS:
        raise HTTPException(404, 'Неизвестная интеграция')
    if name == 'ai_provider':
        inherit_ai_provider()
    else:
        inherit_agent_settings(name)
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, setting_key(name))
        stored = json.loads(row.config) if row else {}
        config = {key: stored.get(key, default) for key, default in DEFAULTS[name].items()}
        secrets = json.loads(decrypt(row.secrets)) if row and row.secrets else {}
        from common.accounts import features
        enabled = features()
        if apply_feature and name in enabled and not enabled[name]:
            config['enabled'] = False
        return config, secrets


def save_settings(name, config=None, secrets=None):
    name = service_name(name)
    # Общий запрет влияет на запуск, но не меняет личный переключатель.
    current, private = read_settings(name, apply_feature=False)
    current.update(config or {})
    private.update(secrets or {})
    if name in ('collector', 'notifications') and secrets is not None and 'bot_token' in secrets:
        from common.accounts import claim_bot
        claim_bot(name, private.get('bot_token', ''))
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, setting_key(name)) or IntegrationSetting(name=setting_key(name))
        row.config = json.dumps(current)
        if secrets is not None:
            row.secrets = encrypt(json.dumps(private))
        session.add(row)
        session.commit()


def report(name, error=None, success=False):
    name = service_name(name)
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, setting_key(name)) or IntegrationSetting(name=setting_key(name))
        row.last_error = error
        if success:
            row.last_success = datetime.now(timezone.utc)
        session.add(row)
        session.commit()


def public_settings():
    inherit_ai_provider()
    for name in ('ameba', 'straus'):
        inherit_agent_settings(name)
    from common.accounts import features
    enabled = features()
    result = {}
    with Session(get_engine()) as session:
        for name in DEFAULTS:
            row = session.get(IntegrationSetting, setting_key(name))
            stored = json.loads(row.config) if row else {}
            config = {key: stored.get(key, default) for key, default in DEFAULTS[name].items()}
            if name in enabled and not enabled[name]:
                config['enabled'] = False
            secrets = json.loads(decrypt(row.secrets)) if row and row.secrets else {}
            result[name] = {
                **config,
                'configured': {key: bool(secrets.get(key)) for key in SECRET_FIELDS[name] if key != 'session'},
                'last_success': row.last_success if row else None,
                'last_error': row.last_error if row else None,
            }
    # Old API clients keep reading the same settings, not another integration.
    return result | {'context': result['monocle'], 'codex': result['arrodes']}


def read_config(name):
    name = service_name(name)
    if name == 'ai_provider':
        inherit_ai_provider()
    else:
        inherit_agent_settings(name)
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, setting_key(name))
        stored = json.loads(row.config) if row else {}
        config = {key: stored.get(key, default) for key, default in DEFAULTS[name].items()}
        from common.accounts import features
        enabled = features()
        if name in enabled and not enabled[name]:
            config['enabled'] = False
        return config | {'last_error': row.last_error if row else None}


def resolve_agent_auth(name, *, require_enabled=False, require_credentials=True):
    name = service_name(name)
    label = AGENT_LABELS.get(name, name)
    config, _ = read_settings(name)
    provider_cfg, provider_secrets = read_settings('ai_provider', apply_feature=False)
    if require_enabled and not config.get('enabled'):
        raise HTTPException(400, f'Включите {label} в настройках')
    auth_mode = (config.get('auth_mode') or ('oauth' if name == 'arrodes' else 'api_key')).strip()
    effective = dict(config, auth_mode=auth_mode, base_url=provider_cfg['base_url'])
    if auth_mode == 'api_key':
        api_key = (provider_secrets.get('api_key') or '').strip()
        if require_credentials:
            if not api_key:
                raise HTTPException(400, 'Сохраните общий API-ключ в разделе «Подключения AI»')
            if not config.get('model', '').strip():
                raise HTTPException(400, f'Выберите модель для {label} в настройках агента')
        return effective, api_key
    return effective, ''
