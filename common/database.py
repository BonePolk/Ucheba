import os

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./tasks.db"),
    connect_args={"check_same_thread": False, "timeout": 30},
)


def init_db():
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('PRAGMA journal_mode=WAL'))
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
