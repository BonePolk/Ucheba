from contextvars import ContextVar

from fastapi import HTTPException

current_user_id = ContextVar('user_id', default=None)


def user_id():
    value = current_user_id.get()
    if value is None:
        raise HTTPException(401, 'Войдите в аккаунт')
    return value
