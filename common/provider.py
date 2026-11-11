"""Проверка адреса провайдера, без секретов в URL."""
from urllib.parse import urlsplit


def provider_url(value):
    value = value.strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Укажите HTTP(S) endpoint без ключа, логина, query и fragment')
    return value
