import os
from uuid import uuid4


from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent_memory import FactInput
from agent_issues import IssueInput

from common.rpc import service_name
from common.accounts import account, check_feature, features, passwords, public_user
from common.database import engine, user_engine
from common.models.user import Feature, User
from common.security import require_owner, require_user, signer, throttle

router = APIRouter(prefix='/api/auth', tags=['users'])
admin = APIRouter(prefix='/api/admin', dependencies=[Depends(require_owner)])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[a-zA-Zа-яА-ЯёЁ0-9_.-]+$')
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
    name = service_name(name)
    if name not in FEATURES:
        raise HTTPException(404, 'Неизвестная функция')
    with Session(engine) as session:
        row = session.get(Feature, name) or Feature(name=name)
        row.enabled = body.enabled
        session.add(row)
        session.commit()
    return {'ok': True}


@router.get('/agent-history')
def agent_history_list(request: Request, before: int | None = None):
    require_user(request)
    from agent_history import read
    return read(before)


@router.api_route('/agent-history/internal', methods=['GET', 'POST', 'PATCH'])
async def agent_history_internal(request: Request, before: int | None = None):
    from common.rpc import require_service
    from common.identity import current_user_id
    from agent_history import start, finish, read
    owner = require_service(request, 'context')
    token = current_user_id.set(owner)
    try:
        if request.method == 'GET':
            return read(before=before)
        body = await request.json()
        return start(body) if request.method == 'POST' else finish(body)
    finally:
        current_user_id.reset(token)


@router.get('/agent-memory')
def agent_memory_list(request: Request):
    require_user(request)
    from agent_memory import read
    return read()


class NewFact(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


@router.post('/agent-memory')
def agent_memory_create(body: NewFact, request: Request):
    require_user(request)
    from agent_memory import save
    return save({'key': 'user.' + uuid4().hex, 'text': body.text}, 'Владелец')


@router.put('/agent-memory')
def agent_memory_update(body: FactInput, request: Request):
    require_user(request)
    from agent_memory import save
    return save(body.model_dump(), 'Владелец')


@router.delete('/agent-memory/{key}')
def agent_memory_delete(key: str, request: Request):
    require_user(request)
    from agent_memory import delete
    return delete(key)


@router.api_route('/agent-memory/internal', methods=['GET', 'POST'])
async def agent_memory_internal(request: Request):
    from common.rpc import require_service
    from common.identity import current_user_id
    from agent_memory import read, save
    token = current_user_id.set(require_service(request, 'context'))
    try:
        if request.method == 'GET':
            return read()
        body = await request.json()
        if body.get('agent') not in ('Straus', 'Monocle', 'Lucheba', 'Ameba', 'Arrodes', '\u0410\u0440\u0440\u043e\u0434\u0435\u0441', 'Codex'):
            raise HTTPException(422, 'Неизвестный агент')
        agent = 'Arrodes' if body['agent'] in ('Codex', '\u0410\u0440\u0440\u043e\u0434\u0435\u0441') else body['agent']
        return save(body, agent)
    finally:
        current_user_id.reset(token)


@router.post('/agent-issues/internal')
async def report_agent_issue(request: Request):
    from common.rpc import require_service
    from common.identity import current_user_id
    from agent_issues import IssueInput, report
    token = current_user_id.set(require_service(request, 'context'))
    try:
        return report(IssueInput.model_validate(await request.json()))
    finally:
        current_user_id.reset(token)


@admin.get('/agent-issues')
def admin_agent_issues():
    from agent_issues import list_issues
    return list_issues()


@admin.post('/agent-issues/{issue_id}/resolve')
def resolve_agent_issue(issue_id: int):
    from agent_issues import resolve
    return resolve(issue_id)


@router.post('/agent-issues')
def report_own_agent_issue(body: IssueInput, request: Request):
    require_user(request)
    from agent_issues import report
    return report(body)


@router.post('/arrodes/tasks/internal')
async def arrodes_tasks_internal(request: Request):
    from common.rpc import require_service
    from common.identity import current_user_id
    from integrations.arrodes import read_tasks
    token = current_user_id.set(require_service(request, 'arrodes'))
    try:
        check_feature('arrodes')
        return read_tasks(**(await request.json()))
    finally:
        current_user_id.reset(token)
