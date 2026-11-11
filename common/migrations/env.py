from alembic import context
from sqlmodel import SQLModel
from common.models import user, task, integration
from common.database import engine


def migrate(connection):
    context.configure(connection=connection, target_metadata=SQLModel.metadata,
                      render_as_batch=connection.dialect.name == 'sqlite', compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get('connection')
if connection is not None:
    migrate(connection)
else:
    with engine.connect() as connection:
        migrate(connection)
