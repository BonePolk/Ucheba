import asyncio
from contextlib import asynccontextmanager
import logging
from collections import OrderedDict

from fastapi import HTTPException
from common.codex_harness import check_connection, prepare_codex_root
from common.http import private_app
from common.agent_api import agent_routes
from common.identity import user_id
from browser_client import browser_request, browser_session_context
import agent


@asynccontextmanager
async def lifespan(app):
    prepare_codex_root()
    yield


app = private_app('straus', lifespan)
agent_routes(app, 'Straus')


@app.post('/check')
async def check_model(body: dict):
    return await check_connection(
        'Straus',
        config=body.get('config'),
        api_key=body.get('api_key', ''),
        default_mode='api_key',
    )
active_runs = {}
cancelled_runs = OrderedDict()


@app.post('/cancel')
async def cancel_edit(body: dict):
    run_id = body['run_id']
    key = (user_id(), run_id)
    cancelled_runs[key] = True
    while len(cancelled_runs) > 256:
        cancelled_runs.popitem(last=False)
    task = active_runs.get(key)
    if task:
        if not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await browser_request('/cancel', {'run_id': run_id})
    return {'ok': True}


@app.post('/edit')
async def edit_schedule(body: dict):
    run_id = body.pop('run_id')
    key = (user_id(), run_id)
    if key in cancelled_runs:
        raise HTTPException(409, 'Работа Straus остановлена')
    if any(owner == user_id() for owner, _ in active_runs):
        raise HTTPException(409, 'Straus уже работает')
    allow_browser = bool((body.get('config') or {}).get('allow_browser', True))
    with browser_session_context(run_id, 'Straus', 0):
        task = asyncio.create_task(agent.run(body))
        active_runs[key] = task
        try:
            return await task
        except asyncio.CancelledError:
            raise HTTPException(409, 'Работа Straus остановлена') from None
        finally:
            active_runs.pop(key, None)
            if allow_browser:
                try:
                    await browser_request('/session/end', {})
                except HTTPException as exc:
                    logging.getLogger('uvicorn.error').warning('Straus browser session cleanup failed: %s', exc.status_code)
