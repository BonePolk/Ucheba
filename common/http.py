import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from common.rpc import require_service
from common.identity import current_user_id
from common.progress import get_progress, stream_progress


class HealthLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        return not (isinstance(args, tuple) and len(args) > 2
                    and isinstance(args[2], str) and args[2].split('?', 1)[0] == '/health')


def hide_health_logs():
    logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(item, HealthLogFilter) for item in logger.filters):
        logger.addFilter(HealthLogFilter())


def private_app(name, lifespan=None):
    hide_health_logs()
    app = FastAPI(title=f'{name} integration', lifespan=lifespan, docs_url=None, openapi_url=None)

    @app.middleware('http')
    async def private_api(request: Request, call_next):
        token = None
        try:
            if request.url.path != '/health':
                token = current_user_id.set(require_service(request, name))
            return await call_next(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})
        except Exception as exc:
            code = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)
            logging.getLogger('uvicorn.error').error('%s: %s (API %s)', name, type(exc).__name__, code if isinstance(code, int) else 'unknown')
            detail = f'{name}: запрос отклонён или сервис недоступен'
            if isinstance(code, int):
                detail += f' (API {code})'
            return JSONResponse(status_code=502, content={'detail': detail})

        finally:
            if token is not None:
                current_user_id.reset(token)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={'detail': 'Некорректный внутренний запрос'})

    @app.get('/health')
    def health():
        return {'ok': True}

    @app.get('/progress/stream')
    async def progress_stream(task_id: int = 0, namespace: str = 'agent', work: bool = False):
        if task_id < 0 or namespace not in ('agent', 'ranking'):
            raise HTTPException(422, 'Некорректный журнал')
        return StreamingResponse(stream_progress(task_id, namespace, work), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/progress')
    async def progress(task_id: int = 0):
        return get_progress(task_id)

    return app
