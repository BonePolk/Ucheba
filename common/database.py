import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from common.identity import user_id


def database_url():
    value = os.getenv('DATABASE_URL', 'sqlite:///./tasks.db')
    if value.startswith(('postgres://', 'postgresql://')):
        value = 'postgresql+psycopg://' + value.split('://', 1)[1]
    return value


url = database_url()
engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 30} if url.startswith('sqlite:') else
                       {'options': '-csearch_path=public'}, pool_pre_ping=True)


def init_db(target=None):
    from alembic import command
    from alembic.config import Config
    from common.models import user, task, integration
    target = target if target is not None else engine
    with target.connect() as connection:
        if target.dialect.name == 'sqlite':
            connection.exec_driver_sql('PRAGMA journal_mode=WAL')
            connection.commit()
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            # Общий lock на схему защищает миграции от одновременного запуска сервисов.
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtext(current_schema()))'))
        config = Config()
        config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        connection.commit()


@lru_cache(maxsize=128)
def user_engine(owner):
    owner = int(owner)
    if owner <= 0:
        raise ValueError('Некорректный пользователь')
    if owner == 1:
        return engine
    if engine.dialect.name == 'postgresql':
        schema = f'user_{owner}'
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        target = create_engine(engine.url, connect_args={'options': f'-csearch_path={schema}'}, poolclass=NullPool)
        init_db(target)
        return target
    root = Path(engine.url.database).resolve().parent / 'users'
    root.mkdir(exist_ok=True, mode=0o700)
    path = root / f'{owner}.db'
    target = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': 30}, poolclass=NullPool)
    init_db(target)
    path.chmod(0o600)
    return target


def get_engine():
    return user_engine(user_id())
