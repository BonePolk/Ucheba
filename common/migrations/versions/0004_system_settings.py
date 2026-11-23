"""Глобальные системные настройки администратора (без смешивания с пользовательскими секретами)."""
from alembic import op
import sqlalchemy as sa

revision = '0004_system_settings'
down_revision = '0003_task_subtasks'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('systemsetting'):
        op.create_table(
            'systemsetting',
            sa.Column('name', sa.String(), primary_key=True, nullable=False),
            sa.Column('config', sa.String(), nullable=False, server_default='{}'),
            sa.Column('secrets', sa.String(), nullable=False, server_default=''),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('systemsetting'):
        op.drop_table('systemsetting')
