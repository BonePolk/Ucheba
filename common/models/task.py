from datetime import datetime, timezone
from typing import Literal

from pydantic import ConfigDict, field_validator
from sqlmodel import Field, SQLModel

from common.dates import normalize_utc

Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["todo", "in_progress", "review", "done"]
SubtaskSource = Literal["user", "monocle"]


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
    subtasks_locked: bool = False
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
    subtasks_locked: bool | None = None


class TaskSubtask(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(index=True)
    title: str = Field(min_length=1, max_length=300)
    done: bool = False
    estimated_minutes: int | None = Field(default=None, ge=0, le=10080)
    position: int = 0
    source: str = "user"
    user_edited: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskSubtaskRead(SQLModel):
    id: int
    task_id: int
    title: str
    done: bool
    estimated_minutes: int | None = None
    position: int
    source: str
    user_edited: bool
    created_at: datetime
    updated_at: datetime


class SubtaskCreate(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    estimated_minutes: int | None = Field(default=None, ge=0, le=10080)


class SubtaskUpdate(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=300)
    done: bool | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=10080)


class SubtaskReorder(SQLModel):
    subtask_ids: list[int] = Field(min_length=1, max_length=200)


class TaskRead(SQLModel):
    archived: bool = False
    id: int
    title: str
    description: str | None = None
    deadline: datetime | None = None
    priority: str = "medium"
    status: str = "todo"
    category: str | None = None
    completed_work_minutes: int = 0
    subtasks_locked: bool = False
    source_url: str | None = None
    source_chat_id: int | None = None
    ai_score: float | None = None
    ai_details: str | None = None
    ai_scored_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    subtasks: list[TaskSubtaskRead] = Field(default_factory=list)
