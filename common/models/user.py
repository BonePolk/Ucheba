from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    is_admin: bool = False
    session_version: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Feature(SQLModel, table=True):
    name: str = Field(primary_key=True)
    enabled: bool = True


class BotTokenClaim(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    user_id: int
    provider: str
