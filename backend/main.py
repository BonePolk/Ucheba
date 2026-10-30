from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.database import init_db
from routers.tasks import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Task Manager", lifespan=lifespan)
app.include_router(router)
