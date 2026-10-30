import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from common.database import init_db
from common.models import integration
from routers.tasks import router


@asynccontextmanager
async def lifespan(app):
    if os.getenv('DATA_DIR'):
        Path(os.environ['DATA_DIR']).mkdir(parents=True, exist_ok=True)
        Path(os.environ['DATA_DIR']).chmod(0o700)
    init_db()
    yield


app = FastAPI(title='Task Board', lifespan=lifespan)
app.include_router(router)


@app.get('/health')
def health():
    return {'ok': True}
