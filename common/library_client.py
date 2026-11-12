"""Доступ обеих ролей к одной библиотеке Silo через её существующий сервис."""
import httpx
from fastapi import HTTPException

from common.identity import user_id
from common.rpc import internal_token


async def library_request(name, args):
    attempts = 1 if name in ('save_file_to_silo', 'write_silo_file', 'delete_silo_file', 'export_table') else 2
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post('http://monocle:8000/library/tool', json={'name': name, 'arguments': args},
                    headers={'X-Service-Token': internal_token('monocle'), 'X-User-ID': str(user_id())})
            if response.status_code in (502, 503, 504) and attempt + 1 < attempts:
                continue
            if not response.is_success:
                detail = 'Не удалось выполнить операцию с библиотекой файлов'
                if response.status_code in (400, 404, 409, 413, 415, 422):
                    try:
                        detail = str(response.json().get('detail', detail))[:500]
                    except ValueError:
                        pass
                raise HTTPException(response.status_code, detail)
            return response.json()
        except httpx.TransportError:
            if attempt + 1 == attempts:
                detail = ('Ответ Silo потерян. Проверьте результат операции перед повтором: она могла выполниться.'
                          if attempts == 1 else 'Библиотека файлов недоступна после повторного подключения')
                raise HTTPException(503, detail) from None
