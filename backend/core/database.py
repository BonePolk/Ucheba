import os

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./tasks.db"),
    connect_args={"check_same_thread": False},
)


def init_db():
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        if "archived" not in {column["name"] for column in inspect(connection).get_columns("task")}:
            connection.execute(text("ALTER TABLE task ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"))
        connection.execute(text(
            "UPDATE task SET deadline = deadline || ' 23:59:00.000000' "
            "WHERE length(deadline) = 10"
        ))
