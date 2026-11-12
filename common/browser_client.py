from contextvars import ContextVar

import httpx
from fastapi import HTTPException

from common.identity import user_id
from common.rpc import internal_token


browser_run_id = ContextVar('browser_run_id', default='')


async def browser_request(path, body=None, method='POST'):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=8), transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            response = await client.request(method, f'http://browser:8000{path}', json=body,
                headers={'X-Service-Token': internal_token('monocle'), 'X-User-ID': str(user_id()), 'X-Agent-Run': browser_run_id.get()})
    except httpx.HTTPError as exc:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            detail = 'Браузер недоступен после повторного подключения. Проверьте сервис browser.'
        else:
            detail = 'Ответ браузера потерян. Прочитай текущую страницу перед новым нажатием: предыдущее действие могло выполниться.'
        raise HTTPException(503, detail) from None
    if not response.is_success:
        try:
            detail = response.json()['detail']
        except (ValueError, KeyError):
            detail = 'Ошибка браузера'
        raise HTTPException(response.status_code, detail)
    return response.json()
