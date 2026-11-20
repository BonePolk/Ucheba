import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from common.rpc import require_service
from common.identity import current_user_id
from common.progress import get_progress, get_work_progress, stream_progress
from common.agent_questions import Answer, answer_question


class HealthLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        return not (isinstance(args, tuple) and len(args) > 2
                    and isinstance(args[2], str) and args[2].split('?', 1)[0] == '/health')


def hide_health_logs():
    logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(item, HealthLogFilter) for item in logger.filters):
        logger.addFilter(HealthLogFilter())


class PrivateApiMiddleware:
    def __init__(self, app, *, name: str):
        self.app = app
        self.name = name

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        token = None
        started = False

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        try:
            request = Request(scope)
            if request.url.path != '/health':
                token = current_user_id.set(require_service(request, self.name))
            await self.app(scope, receive, tracked_send)
        except HTTPException as exc:
            if started:
                raise
            await JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})(scope, receive, send)
        except Exception as exc:
            code = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)
            logging.getLogger('uvicorn.error').error('%s: %s (API %s)', self.name, type(exc).__name__, code if isinstance(code, int) else 'unknown')
            if started:
                raise
            label = {'arrodes': 'Arrodes', 'monocle': 'Monocle'}.get(self.name, self.name)
            detail = f'{label}: запрос отклонён или сервис недоступен'
            if isinstance(code, int):
                detail += f' (API {code})'
            await JSONResponse(status_code=502, content={'detail': detail})(scope, receive, send)
        finally:
            if token is not None:
                current_user_id.reset(token)


def private_app(name, lifespan=None):
    hide_health_logs()
    app = FastAPI(title=f'{name} integration', lifespan=lifespan, docs_url=None, openapi_url=None)
    app.add_middleware(PrivateApiMiddleware, name=name)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={'detail': 'Некорректный внутренний запрос'})

    @app.post('/questions/{question_id}/answer')
    async def question_answer(question_id: str, body: Answer):
        return answer_question(question_id, body.text)

    @app.get('/health')
    def health():
        return {'ok': True}

    @app.get('/progress/stream')
    async def progress_stream(request: Request, task_id: int = 0, namespace: str = 'agent', work: bool = False):
        if task_id < 0 or namespace not in ('agent', 'ranking'):
            raise HTTPException(422, 'Некорректный журнал')
        return StreamingResponse(stream_progress(task_id, namespace, work, request=request), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/progress')
    async def progress(task_id: int = 0, work: bool = False):
        return get_work_progress() if work else get_progress(task_id)

    return app
