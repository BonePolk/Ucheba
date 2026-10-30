from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.rpc import require_service


def private_app(name, lifespan=None):
    app = FastAPI(title=f'{name} integration', lifespan=lifespan, docs_url=None, openapi_url=None)

    @app.middleware('http')
    async def private_api(request: Request, call_next):
        try:
            if request.url.path != '/health':
                require_service(request, name)
            return await call_next(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})
        except Exception as exc:
            code = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)
            detail = f'{name}: запрос отклонён или сервис недоступен'
            if isinstance(code, int):
                detail += f' (API {code})'
            return JSONResponse(status_code=502, content={'detail': detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={'detail': 'Некорректный внутренний запрос'})

    @app.get('/health')
    def health():
        return {'ok': True}

    return app
