"""Проверка адреса провайдера, без секретов в URL."""
from urllib.parse import urlsplit

_INTERNAL_SERVICE_HOSTS = frozenset({
    'backend', 'arrodes', 'codex', 'monocle', 'lucheba', 'ameba', 'straus',
    'browser', 'silo', 'telegram', 'collector', 'notifications', 'postgres',
    'frontend', 'nginx',
})


def provider_url(value):
    value = value.strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Укажите HTTP(S) endpoint без ключа, логина, query и fragment')
    return value


def gateway_url(value):
    cleaned = provider_url(value)
    host = (urlsplit(cleaned).hostname or '').lower()
    if host in _INTERNAL_SERVICE_HOSTS:
        raise ValueError('Нельзя указывать внутренний сервис Ucheba в качестве адреса шлюза')
    return cleaned
