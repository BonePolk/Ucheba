"""Применить миграции реестра и всех пользователей: python -m common.migrate."""
from common.database import engine, init_db, user_engine
from common.models.user import User
from sqlmodel import Session, select


def main():
    init_db()
    with Session(engine) as session:
        owners = session.exec(select(User.id)).all()
    for owner in owners:
        if owner != 1:
            user_engine(owner)
    print(f'Миграции применены. Пользователей: {len(owners)}')


if __name__ == '__main__':
    main()
