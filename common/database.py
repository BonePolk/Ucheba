import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy.pool import NullPool
from common.identity import user_id

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./tasks.db"),
    connect_args={"check_same_thread": False, "timeout": 30},
)


def init_db(target=None):
    target = target if target is not None else engine
    SQLModel.metadata.create_all(target)
    with target.begin() as connection:
        connection.execute(text('PRAGMA journal_mode=WAL'))
        if inspect(connection).has_table('user'):
            user_columns = {column['name'] for column in inspect(connection).get_columns('user')}
            if 'session_version' not in user_columns:
                connection.execute(text('ALTER TABLE user ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0'))
        columns = {column["name"] for column in inspect(connection).get_columns("task")}
        additions = {
            'archived': 'BOOLEAN NOT NULL DEFAULT 0', 'source_url': 'TEXT',
            'source_chat_id': 'BIGINT', 'ai_score': 'FLOAT', 'ai_details': 'TEXT',
            'ai_scored_at': 'DATETIME',
        }
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f'ALTER TABLE task ADD COLUMN {name} {sql_type}'))
        connection.execute(text(
            "UPDATE task SET deadline = deadline || ' 23:59:00.000000' "
            "WHERE length(deadline) = 10"
        ))


@lru_cache(maxsize=128)
def user_engine(owner):
    if owner == 1:
        return engine
    from common.models import task, integration
    root = Path(engine.url.database).resolve().parent / 'users'
    root.mkdir(exist_ok=True, mode=0o700)
    path = root / f'{int(owner)}.db'
    target = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 30},
                           poolclass=NullPool)
    init_db(target)
    path.chmod(0o600)
    return target


def get_engine():
    return user_engine(user_id())
