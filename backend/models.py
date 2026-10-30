from datetime import date, datetime, timezone
from typing import Literal

from pydantic import ConfigDict
from sqlmodel import Field, SQLModel

Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["todo", "in_progress", "done"]


class TaskCreate(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1)
    description: str | None = None
    deadline: date | None = None
    priority: Priority = "medium"
    status: TaskStatus = "todo"
    category: str | None = None


class Task(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    description: str | None = None
    deadline: date | None = None
    priority: str = "medium"
    status: str = "todo"
    category: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskUpdate(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(default=None, min_length=1)
    description: str | None = None
    deadline: date | None = None
    priority: Priority = "medium"
    status: TaskStatus = "todo"
    category: str | None = None
