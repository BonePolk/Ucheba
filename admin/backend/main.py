import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from integrations import codex, worker
from routers.integrations import admin, inbox, router


@asynccontextmanager
async def lifespan(app):
    codex.recover_runs()
    background = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)
        await codex.close()


app = FastAPI(title='Task Manager Administration', lifespan=lifespan,
              root_path='/admin', docs_url='/api/docs', openapi_url='/api/openapi.json', redoc_url=None)
app.include_router(admin)
app.include_router(inbox)
app.include_router(router)


@app.get('/health')
def health():
    return {'ok': True}


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={'detail': 'Некорректные данные запроса'})
