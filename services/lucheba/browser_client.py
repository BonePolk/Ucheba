import httpx
from fastapi import HTTPException

from common.identity import user_id
from common.rpc import internal_token


async def browser_request(path, body=None, method='POST'):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=8)) as client:
            response = await client.request(method, f'http://browser:8000{path}', json=body,
                headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())})
    except httpx.HTTPError:
        raise HTTPException(503, 'Браузер недоступен. Проверьте сервис browser.') from None
    if not response.is_success:
        try:
            detail = response.json()['detail']
        except (ValueError, KeyError):
            detail = 'Ошибка браузера'
        raise HTTPException(response.status_code, detail)
    return response.json()
