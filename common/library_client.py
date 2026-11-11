"""Доступ обеих ролей к одной библиотеке Silo через её существующий сервис."""
import httpx
from fastapi import HTTPException

from common.identity import user_id
from common.rpc import internal_token


async def library_request(name, args):
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post('http://lucheba:8000/library/tool', json={'name': name, 'arguments': args},
                    headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())})
            if response.status_code in (502, 503, 504) and not attempt:
                continue
            if not response.is_success:
                raise HTTPException(response.status_code, 'Не удалось прочитать библиотеку файлов')
            return response.json()
        except httpx.TransportError:
            if attempt:
                raise HTTPException(503, 'Библиотека файлов недоступна после повторного подключения') from None
