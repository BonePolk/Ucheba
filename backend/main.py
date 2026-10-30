import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import case, text
from sqlmodel import Session, SQLModel, create_engine, select

from models import Task, TaskCreate, TaskStatus, TaskUpdate

engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./tasks.db"),
    connect_args={"check_same_thread": False},
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE task SET deadline = deadline || ' 23:59:00.000000' "
            "WHERE length(deadline) = 10"
        ))
    yield


app = FastAPI(title="Task Manager", lifespan=lifespan)


@app.get("/api/tasks", response_model=list[Task])
def list_tasks(
    status: TaskStatus | None = None,
    priority: Literal["low", "medium", "high", "overdue"] | None = None,
    category: str | None = None,
    sort_by: Literal["deadline", "priority", "created_at"] = "deadline",
    order: Literal["asc", "desc"] = "asc",
):
    overdue = (
        (Task.deadline < datetime.now(timezone.utc))
        & (Task.status != "done")
    )
    statement = select(Task).order_by(case((overdue, 0), else_=1))
    if status is not None:
        statement = statement.where(Task.status == status)
    if priority == "overdue":
        statement = statement.where(overdue)
    elif priority is not None:
        statement = statement.where(Task.priority == priority, ~case((overdue, True), else_=False))
    if category is not None:
        statement = statement.where(Task.category == category)

    column = {
        "deadline": Task.deadline,
        "created_at": Task.created_at,
        "priority": case(
            (Task.priority == "high", 0),
            (Task.priority == "medium", 1),
            else_=2,
        ),
    }[sort_by]
    if sort_by == "deadline":
        statement = statement.order_by(Task.deadline.is_(None))
    statement = statement.order_by(column.asc() if order == "asc" else column.desc(), Task.id.desc())
    with Session(engine) as session:
        return session.exec(statement).all()


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        return task


@app.post("/api/tasks", response_model=Task, status_code=201)
def create_task(data: TaskCreate):
    with Session(engine) as session:
        task = Task.model_validate(data)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


@app.patch("/api/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, data: TaskUpdate):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        task.sqlmodel_update(data.model_dump(exclude_unset=True))
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Задача не найдена")
        session.delete(task)
        session.commit()
    return Response(status_code=204)
