from contextlib import contextmanager
from contextvars import ContextVar

import httpx
from fastapi import HTTPException

from common.identity import user_id
from common.rpc import internal_token


browser_run_id = ContextVar('browser_run_id', default='')
browser_agent = ContextVar('browser_agent', default='')
browser_task_id = ContextVar('browser_task_id', default=0)
SHORT_PATHS = frozenset({'/session/start', '/session/release', '/session/status', '/cancel', '/sites', '/screen', '/handoff'})


@contextmanager
def browser_session_context(run_id: str, agent: str = '', task_id: int = 0):
    run_token = browser_run_id.set(run_id)
    agent_token = browser_agent.set(agent)
    task_token = browser_task_id.set(task_id or 0)
    try:
        yield
    finally:
        browser_task_id.reset(task_token)
        browser_agent.reset(agent_token)
        browser_run_id.reset(run_token)


def _request_timeout(path: str) -> httpx.Timeout:
    if path == '/wait-user':
        return httpx.Timeout(None, connect=8)
    if path == '/session/acquire':
        return httpx.Timeout(45, connect=8)
    if path in SHORT_PATHS:
        return httpx.Timeout(30, connect=8)
    if path == '/session/end':
        return httpx.Timeout(60, connect=8)
    return httpx.Timeout(120, connect=8)


async def browser_request(path, body=None, method='POST'):
    try:
        async with httpx.AsyncClient(timeout=_request_timeout(path), transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            response = await client.request(method, f'http://browser:8000{path}', json=body,
                headers={
                    'X-Service-Token': internal_token('monocle'),
                    'X-User-ID': str(user_id()),
                    'X-Agent-Run': browser_run_id.get(),
                    'X-Agent-Name': browser_agent.get(),
                    'X-Agent-Task': str(browser_task_id.get() or 0),
                })
    except httpx.HTTPError as exc:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            status_code = 503
            detail = 'Браузер недоступен после повторного подключения. Проверьте сервис browser.'
        elif isinstance(exc, httpx.TimeoutException):
            status_code = 504
            detail = 'Браузер не ответил вовремя. Прочитай текущую страницу перед повтором: предыдущее действие могло выполниться.'
        else:
            status_code = 503
            detail = 'Ответ браузера потерян. Прочитай текущую страницу перед новым нажатием: предыдущее действие могло выполниться.'
        raise HTTPException(status_code, detail) from None
    if not response.is_success:
        try:
            detail = response.json()['detail']
        except (ValueError, KeyError):
            detail = 'Ошибка браузера'
        raise HTTPException(response.status_code, detail)
    return response.json()
