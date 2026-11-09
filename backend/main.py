import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.accounts import bootstrap
from common.database import init_db
from common.identity import current_user_id
from common.runtime import for_users
from common.security import session_user
from integrations import codex, worker
from routers.integrations import inbox, router as integrations
from routers.tasks import router as tasks
from routers.users import admin, router as users


@asynccontextmanager
async def lifespan(app):
    if os.getenv('DATA_DIR'):
        root = Path(os.environ['DATA_DIR'])
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
    init_db()
    bootstrap()
    background = asyncio.create_task(for_users(worker.run))
    try:
        yield
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)
        await codex.close()


app = FastAPI(title='Task Manager', lifespan=lifespan)


@app.middleware('http')
async def user_session(request: Request, call_next):
    token = None
    try:
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # JSON API + strict cookie: чужой сайт не может отправить авторизованную форму.
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                raise HTTPException(415, 'Используйте application/json')
        request.state.user = session_user(request) if request.cookies.get('user_session') else None
        if request.state.user:
            token = current_user_id.set(request.state.user.id)
        return await call_next(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})
    finally:
        if token is not None:
            current_user_id.reset(token)


app.include_router(users)
app.include_router(admin)
app.include_router(tasks)
app.include_router(integrations)
app.include_router(inbox)


@app.get('/health')
def health():
    return {'ok': True}


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={'detail': 'Проверьте введённые данные'})
