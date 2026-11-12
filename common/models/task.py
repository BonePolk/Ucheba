from datetime import datetime, timezone
from typing import Literal

from pydantic import ConfigDict, field_validator
from sqlmodel import Field, SQLModel

from common.dates import normalize_utc

Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["todo", "in_progress", "review", "done"]


class TaskCreate(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1)
    description: str | None = None
    deadline: datetime | None = None
    priority: Priority = "medium"
    status: TaskStatus = "todo"
    category: str | None = None
    completed_work_minutes: int = Field(default=0, ge=0)
    agent_notes: str = Field(default="", max_length=12000)

    @field_validator("deadline")
    @classmethod
    def normalize_deadline(cls, value):
        return normalize_utc(value)


class Task(SQLModel, table=True):
    archived: bool = False
    id: int | None = Field(default=None, primary_key=True)
    title: str
    description: str | None = None
    deadline: datetime | None = None
    priority: str = "medium"
    status: str = "todo"
    category: str | None = None
    completed_work_minutes: int = Field(default=0, ge=0)
    source_url: str | None = None
    source_chat_id: int | None = None
    ai_score: float | None = None
    ai_details: str | None = None
    ai_scored_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskUpdate(TaskCreate):
    archived: bool = False
    title: str = Field(default=None, min_length=1)
