import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from common.accounts import account, check_feature, features, passwords, public_user
from common.database import engine, user_engine
from common.models.user import Feature, User
from common.security import require_owner, require_user, signer, throttle

router = APIRouter(prefix='/api/auth', tags=['users'])
admin = APIRouter(prefix='/api/admin', dependencies=[Depends(require_owner)])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=12, max_length=256)


def authenticate(response, row):
    response.set_cookie('user_session', signer().dumps({'id': row.id, 'version': row.session_version}), httponly=True, samesite='strict',
                        max_age=43200, secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', path='/')
    response.delete_cookie('owner_session', path='/')
    return public_user(row)


@router.get('/registration')
def registration_status():
    return {'enabled': features()['registration']}


@router.get('/me')
def me(request: Request):
    row = require_user(request)
    return public_user(row) | {'features': features()}


@router.post('/register', status_code=201)
def register(body: Credentials, request: Request, response: Response):
    throttle(request)
    check_feature('registration')
    with Session(engine) as session:
        row = User(username=body.username.casefold(), password_hash=passwords.hash(body.password))
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            raise HTTPException(409, 'Имя пользователя уже занято') from None
        session.refresh(row)
        user_engine(row.id)
        return authenticate(response, row)


@router.post('/login')
def login(body: Credentials, request: Request, response: Response):
    throttle(request)
    with Session(engine) as session:
        row = session.exec(select(User).where(User.username == body.username.casefold())).first()
        # Одинаковая проверка хеша и для неизвестного имени.
        hashed = row.password_hash if row else account(1).password_hash
        valid = passwords.verify(body.password, hashed)
        if not row or not valid:
            raise HTTPException(401, 'Неверный логин или пароль')
        return authenticate(response, row)


@router.post('/logout')
def logout(response: Response):
    response.delete_cookie('user_session', path='/')
    return {'ok': True}


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.patch('/password')
def change_password(body: PasswordChange, request: Request, response: Response):
    throttle(request)
    row = require_user(request)
    if not passwords.verify(body.current_password, row.password_hash):
        raise HTTPException(400, 'Неверный текущий пароль')
    with Session(engine) as session:
        row.password_hash = passwords.hash(body.new_password)
        row.session_version += 1
        session.add(row)
        session.commit()
        session.refresh(row)
    authenticate(response, row)
    return {'ok': True}


@admin.get('')
def dashboard():
    with Session(engine) as session:
        count = session.exec(select(func.count()).select_from(User)).one()
    return {'user_count': count, 'features': features()}


class FeatureUpdate(BaseModel):
    enabled: bool


@admin.patch('/features/{name}')
def feature_update(name: str, body: FeatureUpdate):
    from common.accounts import FEATURES
    if name not in FEATURES:
        raise HTTPException(404, 'Неизвестная функция')
    with Session(engine) as session:
        row = session.get(Feature, name) or Feature(name=name)
        row.enabled = body.enabled
        session.add(row)
        session.commit()
    return {'ok': True}
