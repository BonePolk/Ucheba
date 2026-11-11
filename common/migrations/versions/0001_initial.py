"""Начальная версия: подключить существующие базы без удаления данных."""
from alembic import op
from sqlalchemy import inspect, text
from sqlmodel import SQLModel
from common.models import user, task, integration

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    SQLModel.metadata.create_all(connection)
    inspector = inspect(connection)
    if connection.dialect.name == 'sqlite':
        additions = {
            'user': {'session_version': 'INTEGER NOT NULL DEFAULT 0'},
            'task': {'archived': 'BOOLEAN NOT NULL DEFAULT 0', 'source_url': 'TEXT', 'source_chat_id': 'BIGINT',
                     'ai_score': 'FLOAT', 'ai_details': 'TEXT', 'ai_scored_at': 'DATETIME'},
            'agentissue': {'description': "TEXT NOT NULL DEFAULT ''"},
        }
        for table, columns in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {column['name'] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql_type}'))
        connection.execute(text("UPDATE task SET deadline = deadline || ' 23:59:00.000000' WHERE length(deadline) = 10"))


def downgrade():
    raise RuntimeError('Откат начальной миграции не удаляет пользовательские таблицы. Используйте резервную копию.')
