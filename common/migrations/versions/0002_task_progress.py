"""Подтверждённая полезная работа; прежние задачи начинают с нуля."""
from alembic import op
import sqlalchemy as sa

revision = '0002_task_progress'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    if 'completed_work_minutes' not in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('task')}:
        op.add_column('task', sa.Column('completed_work_minutes', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('task', 'completed_work_minutes')
