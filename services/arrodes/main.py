import asyncio
import logging
import os
import stat
from urllib.parse import quote
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from common.identity import user_id
from common.browser_client import browser_request, browser_run_id

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from common.http import private_app
from common.progress import Progress
import runner


@asynccontextmanager
async def lifespan(app):
    runner.prepare_directories()
    try:
        yield
    finally:
        await runner.close()


app = private_app('arrodes', lifespan)


@app.get('/status')
async def status():
    return await runner.status()


@app.post('/connect')
async def connect():
    return await runner.start_login()


@app.post('/disconnect')
async def disconnect():
    return await runner.disconnect()


@app.post('/check')
async def check(body: dict | None = None):
    return await runner.check((body or {}).get('model'))


@app.get('/models')
async def models():
    return await runner.models()


class Run(BaseModel):
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    payload: dict
    task_id: int = Field(default=0, ge=0)


active_runs = {}
cancelled_runs = OrderedDict()


@app.post('/cancel')
async def cancel(body: dict):
    key = (user_id(), body['run_id'])
    cancelled_runs[key] = True
    while len(cancelled_runs) > 256:
        cancelled_runs.popitem(last=False)
    task = active_runs.get(key)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await browser_request('/cancel', {'run_id': body['run_id']})
    return {'ok': True}


@app.post('/run')
async def run(body: Run):
    key = (user_id(), body.run_id)
    if key in cancelled_runs or any(owner == user_id() for owner, _ in active_runs):
        raise HTTPException(409, 'Arrodes остановлен или уже работает')
    token = browser_run_id.set(body.run_id)
    async def execute():
        with Progress('Arrodes', body.task_id) as progress:
            try:
                await browser_request('/session/start', {})
            except HTTPException as exc:
                logging.getLogger('uvicorn.error').warning('Arrodes browser start: HTTP %s', exc.status_code)
            try:
                payload = body.payload | {'run_id': body.run_id, 'task_id': body.task_id, '_progress': progress}
                return await runner.execute_with_history(payload)
            finally:
                try:
                    await browser_request('/session/end', {})
                except HTTPException as exc:
                    logging.getLogger('uvicorn.error').warning('Arrodes browser cleanup: HTTP %s', exc.status_code)
    task = asyncio.create_task(execute())
    active_runs[key] = task
    try:
        return await task
    except asyncio.CancelledError:
        raise HTTPException(409, 'Arrodes остановлен') from None
    finally:
        active_runs.pop(key, None)
        browser_run_id.reset(token)


def result_root(run_id):
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise HTTPException(404, 'Запуск не найден')
    return runner.result_path(run_id)


@app.get('/runs/{run_id}/files')
async def files(run_id: str):
    root = result_root(run_id)
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    except OSError:
        raise HTTPException(404, 'Рабочая директория недоступна') from None
    try:
        result = []
        for base, folders, filenames, fd in os.fwalk('.', dir_fd=directory, follow_symlinks=False):
            for name in filenames:
                if stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                    result.append(str(Path(base) / name))
                    if len(result) == 200:
                        return result
        return result
    finally:
        os.close(directory)


@app.get('/runs/{run_id}/files/{filename:path}')
async def download(run_id: str, filename: str):
    root = result_root(run_id)
    try:
        fd = runner.open_local(root, filename, os.O_RDONLY)
    except (OSError, ValueError):
        raise HTTPException(404, 'Файл не найден') from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 10 * 1024 * 1024:
        os.close(fd)
        raise HTTPException(404, 'Файл не найден или больше 10 МБ')
    def chunks():
        with os.fdopen(fd, 'rb') as file:
            remaining = info.st_size
            while remaining:
                chunk = file.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    return StreamingResponse(chunks(), media_type='application/octet-stream',
        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(Path(filename).name, safe='')})
