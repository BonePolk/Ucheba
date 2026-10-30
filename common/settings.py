import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session

from common.database import engine
from common.security import decrypt, encrypt
from common.models.integration import IntegrationSetting

DEFAULTS = {
    'notifications': {'enabled': False, 'owner_id': None, 'lead_minutes': 30},
    'collector': {'enabled': False, 'chat_ids': []},
    'telegram': {'enabled': False, 'chat_ids': []},
    'gemini': {'enabled': False, 'model': 'gemini-3.8-flash', 'interval_seconds': 300,
               'confidence_threshold': 0.9, 'timezone': 'UTC', 'owner_context': ''},
    'codex': {'enabled': False, 'auto_prepare': False},
    'jev': {'enabled': False, 'account_id': '', 'model': 'typesafe/jev'},
}
SECRET_FIELDS = {
    'notifications': {'bot_token'}, 'collector': {'bot_token'},
    'telegram': {'api_id', 'api_hash', 'phone', 'session'},
    'gemini': {'api_key'}, 'codex': set(), 'jev': {'api_token'},
}


def read_settings(name):
    if name not in DEFAULTS:
        raise HTTPException(404, 'Неизвестная интеграция')
    with Session(engine) as session:
        row = session.get(IntegrationSetting, name)
        config = DEFAULTS[name] | (json.loads(row.config) if row else {})
        secrets = json.loads(decrypt(row.secrets)) if row and row.secrets else {}
        return config, secrets


def save_settings(name, config=None, secrets=None):
    current, private = read_settings(name)
    current.update(config or {})
    private.update(secrets or {})
    with Session(engine) as session:
        row = session.get(IntegrationSetting, name) or IntegrationSetting(name=name)
        row.config = json.dumps(current)
        if secrets is not None:
            row.secrets = encrypt(json.dumps(private))
        session.add(row)
        session.commit()


def report(name, error=None, success=False):
    with Session(engine) as session:
        row = session.get(IntegrationSetting, name) or IntegrationSetting(name=name)
        row.last_error = error
        if success:
            row.last_success = datetime.now(timezone.utc)
        session.add(row)
        session.commit()


def public_settings():
    result = {}
    with Session(engine) as session:
        for name in DEFAULTS:
            row = session.get(IntegrationSetting, name)
            config, secrets = read_settings(name)
            result[name] = {
                **config,
                'configured': {key: bool(secrets.get(key)) for key in SECRET_FIELDS[name] if key != 'session'},
                'last_success': row.last_success if row else None,
                'last_error': row.last_error if row else None,
            }
    return result


def read_config(name):
    with Session(engine) as session:
        row = session.get(IntegrationSetting, name)
        return DEFAULTS[name] | (json.loads(row.config) if row else {}) | {'last_error': row.last_error if row else None}
