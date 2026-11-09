from contextlib import asynccontextmanager

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from common.http import private_app
import runner


@asynccontextmanager
async def lifespan(app):
    runner.prepare_directories()
    try:
        yield
    finally:
        await runner.close()


app = private_app('codex', lifespan)


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
async def check():
    return await runner.check()


class Run(BaseModel):
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    payload: dict


@app.post('/run')
async def run(body: Run):
    return await runner.execute(body.run_id, body.payload)


def result_root(run_id):
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise HTTPException(404, 'Запуск не найден')
    return runner.work() / run_id


@app.get('/runs/{run_id}/files')
async def files(run_id: str):
    root = result_root(run_id)
    return [str(path.relative_to(root)) for path in root.rglob('*')
            if path.is_file() and path.resolve().is_relative_to(root.resolve())][:200] if root.exists() else []


@app.get('/runs/{run_id}/files/{filename:path}')
async def download(run_id: str, filename: str):
    root = result_root(run_id).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raise HTTPException(404, 'Файл не найден или больше 10 МБ')
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')
