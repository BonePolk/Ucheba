import asyncio
import logging
from collections import OrderedDict

from fastapi import HTTPException
from common.http import private_app
from common.agent_api import agent_routes
from common.identity import user_id
from browser_client import browser_request, browser_run_id
import agent

app = private_app('straus')
agent_routes(app, 'Straus')
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
    token = browser_run_id.set(run_id)
    try:
        await browser_request('/session/start', {})
    except HTTPException as exc:
        logging.getLogger('uvicorn.error').warning('Straus browser session start failed: %s', exc.status_code)
    task = asyncio.create_task(agent.run(body))
    active_runs[key] = task
    try:
        return await task
    except asyncio.CancelledError:
        raise HTTPException(409, 'Работа Straus остановлена') from None
    finally:
        active_runs.pop(key, None)
        try:
            await browser_request('/session/end', {})
        except HTTPException as exc:
            logging.getLogger('uvicorn.error').warning('Straus browser session cleanup failed: %s', exc.status_code)
        browser_run_id.reset(token)
