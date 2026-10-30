from datetime import datetime, timezone
from typing import Literal

from pydantic import ConfigDict, field_validator
from sqlmodel import Field, SQLModel

Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["todo", "in_progress", "done"]


class TaskCreate(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1)
    description: str | None = None
    deadline: datetime | None = None
    priority: Priority = "medium"
    status: TaskStatus = "todo"
    category: str | None = None

    @field_validator("deadline")
    @classmethod
    def normalize_deadline(cls, value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Task(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    description: str | None = None
    deadline: datetime | None = None
    priority: str = "medium"
    status: str = "todo"
    category: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskUpdate(TaskCreate):
    title: str = Field(default=None, min_length=1)
