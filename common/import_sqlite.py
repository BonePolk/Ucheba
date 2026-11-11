"""Явный перенос SQLite -> пустой PostgreSQL. SQLite остаётся нетронутой.

DATABASE_URL=... python -m common.import_sqlite --source /data
Не запускайте backend до завершения переноса. Ключ шифрования должен остаться прежним.
"""
import argparse
from datetime import datetime
from pathlib import Path

from sqlalchemy import Integer, MetaData, String, URL, cast, create_engine, func, select, text
from sqlmodel import SQLModel
from common.database import engine, init_db, user_engine
from common.models import user, task, integration


def copy_database(path, target):
    source = create_engine(URL.create('sqlite', database='file:' + str(path.resolve()), query={'mode': 'ro', 'uri': 'true'}))
    metadata = MetaData()
    metadata.reflect(source)
    with source.connect() as old, target.begin() as new:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in metadata.tables:
                continue
            if new.execute(select(func.count()).select_from(table)).scalar_one():
                raise RuntimeError('Целевая схема не пустая. Перенос остановлен без замены данных')
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in metadata.tables:
                continue
            original = metadata.tables[table.name]
            batch = []
            query = select(original)
            if table.name == 'task' and 'deadline' in original.c:
                query = query.add_columns(cast(original.c.deadline, String).label('_original_deadline'))
            for row in old.execute(query).mappings():
                values = {key: value for key, value in row.items() if key in table.c}
                deadline = row.get('_original_deadline')
                if deadline and len(deadline) == 10:
                    values['deadline'] = datetime.fromisoformat(deadline + 'T23:59:00')
                batch.append(values)
                if len(batch) == 500:
                    new.execute(table.insert(), batch)
                    batch = []
            if batch:
                new.execute(table.insert(), batch)
            for column in table.primary_key:
                if isinstance(column.type, Integer):
                    sequence = new.execute(text('SELECT pg_get_serial_sequence(:table, :column)'),
                        {'table': '"' + table.name + '"', 'column': column.name}).scalar_one_or_none()
                    maximum = new.execute(select(func.max(column))).scalar_one_or_none()
                    if sequence and maximum is not None:
                        new.execute(text('SELECT setval(CAST(:sequence AS regclass), :value, true)'),
                                    {'sequence': sequence, 'value': max(1, maximum)})
    source.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True, help='Папка с tasks.db и users/<id>.db')
    args = parser.parse_args()
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('DATABASE_URL должен указывать на PostgreSQL')
    if not (args.source / 'tasks.db').is_file():
        raise RuntimeError('Исходная tasks.db не найдена')
    init_db()
    databases = [(args.source / 'tasks.db', engine)]
    for path in sorted((args.source / 'users').glob('*.db')):
        if path.stem.isdecimal() and int(path.stem) > 1:
            databases.append((path, user_engine(int(path.stem))))
    # Проверяем все назначения до первой записи, чтобы не начать перенос поверх данных.
    for _, target in databases:
        with target.connect() as connection:
            for table in SQLModel.metadata.sorted_tables:
                if connection.execute(select(func.count()).select_from(table)).scalar_one():
                    raise RuntimeError('Целевая схема не пустая. Перенос остановлен без замены данных')
    for path, target in databases:
        copy_database(path, target)
    print('Перенос завершён. Исходные SQLite-файлы сохранены.')


if __name__ == '__main__':
    main()
