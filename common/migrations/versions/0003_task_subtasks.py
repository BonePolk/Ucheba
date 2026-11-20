"""Редактируемые подзадачи и флаг ручного контроля автозаполнения."""
from alembic import op
import sqlalchemy as sa

revision = '0003_task_subtasks'
down_revision = '0002_task_progress'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'subtasks_locked' not in {column['name'] for column in inspector.get_columns('task')}:
        op.add_column('task', sa.Column('subtasks_locked', sa.Boolean(), nullable=False, server_default=sa.false()))
    if not inspector.has_table('tasksubtask'):
        op.create_table(
            'tasksubtask',
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column('task_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=300), nullable=False),
            sa.Column('done', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('estimated_minutes', sa.Integer(), nullable=True),
            sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('source', sa.String(), nullable=False, server_default='user'),
            sa.Column('user_edited', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_tasksubtask_task_id', 'tasksubtask', ['task_id'])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('tasksubtask'):
        op.drop_index('ix_tasksubtask_task_id', table_name='tasksubtask')
        op.drop_table('tasksubtask')
    if 'subtasks_locked' in {column['name'] for column in inspector.get_columns('task')}:
        op.drop_column('task', 'subtasks_locked')
