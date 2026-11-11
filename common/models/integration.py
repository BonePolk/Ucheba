from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Field, SQLModel


class IntegrationSetting(SQLModel, table=True):
    name: str = Field(primary_key=True)
    config: str = '{}'
    secrets: str = ''
    last_success: datetime | None = None
    last_error: str | None = None


class TelegramChat(SQLModel, table=True):
    id: int = Field(primary_key=True)
    title: str
    username: str | None = None
    last_message_id: int = 0
    context: str = ''


class ProcessedMessage(SQLModel, table=True):
    chat_id: int = Field(primary_key=True)
    message_id: int = Field(primary_key=True)


class Suggestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dedup_key: str = Field(unique=True)
    title: str
    description: str | None = None
    deadline: datetime | None = None
    action: str = 'create'
    target_task_id: int | None = None
    task_id: int | None = None
    confidence: float
    reason: str
    assignee: str | None = None
    source_chat_id: int
    source_message_id: int
    source_title: str
    source_url: str | None = None
    status: str = 'pending'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskAction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    task_id: int
    action: str
    details: str = '{}'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskRanking(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    snapshot: str = ''
    result: str = '{}'


class CodexRun(SQLModel, table=True):
    id: str = Field(primary_key=True)
    task_id: int
    status: str = 'queued'
    summary: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DetectedTask(BaseModel):
    title: str = PydanticField(min_length=1, max_length=500)
    description: str | None = None
    deadline: datetime | None = None
    action: Literal['create', 'update', 'cancel']
    target_task_id: int | None = None
    confidence: float = PydanticField(ge=0, le=1)
    ambiguous: bool = True
    addressed_to_owner: bool = False
    assignee: str | None = None
    reason: str
    source_message_id: int


class Analysis(BaseModel):
    tasks: list[DetectedTask] = PydanticField(max_length=30)


class BotChat(SQLModel, table=True):
    id: int = Field(primary_key=True)
    title: str
    username: str | None = None


class CollectedMessage(SQLModel, table=True):
    chat_id: int = Field(primary_key=True)
    message_id: int = Field(primary_key=True)
    payload: str
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NotificationSent(SQLModel, table=True):
    key: str = Field(primary_key=True)
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskContext(SQLModel, table=True):
    task_id: int = Field(primary_key=True)
    subject: str = ''
    owner_notes: str = ''
    summary: str = ''
    questions: str = '[]'
    status: str = 'new'
    task_snapshot: str = ''
    usage: str = '{}'
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentHistory(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    agent: str
    request: str = '{}'
    result: str = '{}'
    status: str = 'running'
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class AgentFact(SQLModel, table=True):
    key: str = Field(primary_key=True)
    text: str
    agent: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
