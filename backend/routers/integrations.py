import asyncio
import base64
import binascii
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlmodel import Session, select

from common.provider import provider_url
from common.database import get_engine
from common.rpc import call, internal_token
from common.security import require_user
from common.identity import user_id
from common.accounts import check_feature, features
from integrations import ameba, codex, telegram, context, jobs, scheduler
from common import extraction as pipeline
from common.settings import read_config, SECRET_FIELDS, public_settings, read_settings, report, save_settings
from common.models.integration import CodexRun, Suggestion
from common.models.task import Task, TaskCreate

settings_lock = asyncio.Lock()


async def provider_call(name, operation):
    try:
        result = await operation
    except HTTPException as exc:
        report(name, error=str(exc.detail))
        raise
    except httpx.HTTPError:
        detail = f'{name}: сервис недоступен или превышено время ожидания'
        report(name, error=detail)
        raise HTTPException(502, detail) from None
    report(name, success=True)
    return result

router = APIRouter(prefix='/api/integrations', tags=['integrations'], dependencies=[Depends(require_user)])
inbox = APIRouter(prefix='/api/inbox', tags=['inbox'], dependencies=[Depends(require_user)])


@router.get('/jobs/{job_id}')
def agent_job(job_id: str):
    return jobs.result(job_id)


@router.get('/context/schedule/import/status')
def schedule_import_status():
    check_feature('straus')
    previous = jobs.last('straus')
    error = previous.get('detail') if previous and previous['status'] == 'failed' else None
    return {'running': jobs.active('straus') is not None,
            'error': error if error != 'Операция остановлена' else None}


@router.post('/context/schedule/import/stop')
async def stop_schedule_import():
    result = await jobs.stop('straus')
    return result


@router.api_route('/agents/{name}/models', methods=['GET', 'POST'])
async def agent_models(name: Literal['context', 'ameba', 'straus'], request: Request):
    config, secrets = read_settings(name)
    check_feature(name)
    body = await request.json() if request.method == 'POST' else {}
    key = body.get('api_key') or secrets.get('api_key')
    if not key:
        raise HTTPException(400, 'Введите API-ключ этого агента')
    try:
        endpoint = provider_url(body.get('base_url') or config['base_url'])
    except ValueError:
        raise HTTPException(422, 'Проверьте endpoint провайдера') from None
    return await call(name, '/models', {'api_key': key, 'base_url': endpoint}, timeout=45)


class AgentAnswer(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


@router.post('/agents/{name}/questions/{question_id}/answer')
async def answer_agent(name: Literal['context', 'ameba', 'straus'], question_id: str, body: AgentAnswer):
    check_feature(name)
    if len(question_id) != 32 or any(char not in '0123456789abcdef' for char in question_id):
        raise HTTPException(422, 'Некорректный вопрос')
    return await call(name, f'/questions/{question_id}/answer', body.model_dump(), timeout=15)


@router.get('/queue')
async def agent_queue():
    state = scheduler.queue_state()
    enabled = features()
    with Session(get_engine()) as session:
        for group in ('active', 'waiting'):
            entries = []
            for item in state[group]:
                feature = 'ameba' if item['provider'] == 'ameba' else 'straus' if item['provider'] == 'context' and not item['task_id'] else item['provider']
                if not enabled.get(feature, False):
                    continue
                task = session.get(Task, item['task_id']) if item['task_id'] else None
                entries.append({'provider': item['provider'], 'task_id': item['task_id'],
                                'title': task.title if task else 'Расписание · Straus' if item['provider'] == 'context' else 'Общая сортировка'})
            state[group] = entries
    return state


@router.get('/progress')
async def agent_progress(task_id: int = 0, providers: str = 'context,codex'):
    if task_id < 0:
        raise HTTPException(422, 'Некорректный номер задачи')
    if task_id:
        context.get_context(task_id)
    enabled = features()
    allowed = {'context': enabled['context'] or (task_id == 0 and enabled['straus']),
               'monocle': enabled['context'], 'straus': task_id == 0 and enabled['straus'],
               'ameba': enabled['ameba'], 'codex': enabled['codex']}
    names = [name for name in allowed if name in providers.split(',') and allowed[name]]

    async def read(name):
        queued = scheduler.queued('context' if name in ('monocle', 'straus') else name, task_id)
        try:
            if name in ('monocle', 'straus'):
                service = 'context' if name == 'monocle' else 'straus'
                path = '/work/progress' if name == 'monocle' and task_id == 0 else f'/progress?task_id={task_id}'
                result = await call(service, path, method='GET', timeout=2)
                available = True
            elif name == 'context' and task_id == 0:
                operations = []
                if enabled['context']:
                    operations.append(call('context', '/work/progress', method='GET', timeout=2))
                if enabled['straus']:
                    operations.append(call('straus', '/progress', method='GET', timeout=2))
                results = await asyncio.gather(*operations, return_exceptions=True)
                runs = [run for run in results if isinstance(run, dict)]
                result = max(runs, key=lambda run: (run['status'] == 'running', run['started_at'])) if runs else None
                available = any(not isinstance(run, Exception) for run in results)
            else:
                service = name
                path = '/ranking/progress' if name == 'ameba' else '/progress'
                result = await call(service, f'{path}?task_id={task_id}', method='GET', timeout=2)
                available = True
            return {'provider': name, 'run': result, 'available': available, 'queued': queued}
        except (HTTPException, httpx.HTTPError):
            return {'provider': name, 'run': None, 'available': False, 'queued': queued}

    return await asyncio.gather(*(read(name) for name in names))


@router.get('/progress/stream')
async def agent_progress_stream(task_id: int = 0, providers: str = 'context,codex'):
    if task_id < 0:
        raise HTTPException(422, 'Некорректный номер задачи')
    if task_id:
        context.get_context(task_id)
    enabled = features()
    sources = []
    requested = providers.split(',')
    if 'monocle' in requested and enabled['context']:
        sources.append(('monocle', 'context', 'agent', task_id == 0))
    if 'straus' in requested and task_id == 0 and enabled['straus']:
        sources.append(('straus', 'straus', 'agent', False))
    if 'context' in requested:
        if enabled['context']:
            sources.append(('context', 'context', 'agent', task_id == 0))
        if task_id == 0 and enabled['straus']:
            sources.append(('context', 'straus', 'agent', False))
    if 'ameba' in requested and enabled['ameba']:
        sources.append(('ameba', 'ameba', 'ranking', task_id == 0))
    if 'codex' in requested and enabled['codex']:
        sources.append(('codex', 'codex', 'agent', False))

    async def events():
        changes = asyncio.Queue(maxsize=8)
        states = {}

        async def listen(provider, service, namespace, work):
            host = 'lucheba' if service == 'context' else service
            url = f'http://{host}:8000/progress/stream'
            headers = {'X-Service-Token': internal_token(service), 'X-User-ID': str(user_id())}
            while True:
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=8)) as client:
                        async with client.stream('GET', url, headers=headers,
                            params={'task_id': task_id, 'namespace': namespace, 'work': str(work).lower()}) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if line.startswith('data: '):
                                    await changes.put((provider, service, json.loads(line[6:]), True))
                except (httpx.HTTPError, ValueError):
                    pass
                await changes.put((provider, service, None, False))
                await asyncio.sleep(2)

        tasks = [asyncio.create_task(listen(*source)) for source in sources]
        try:
            yield ': connected\n\n'
            while sources:
                try:
                    provider, service, run, available = await asyncio.wait_for(changes.get(), timeout=15)
                    states[service] = {'run': run, 'available': available}
                    entries = []
                    for name in dict.fromkeys(source[0] for source in sources):
                        values = [states[source[1]] for source in sources if source[0] == name and source[1] in states]
                        runs = [value['run'] for value in values if value['run']]
                        current = max(runs, key=lambda item: (item['status'] == 'running', item['started_at'])) if runs else None
                        entries.append({'provider': name, 'run': current,
                                        'server_time': datetime.now(timezone.utc).isoformat(),
                                        'available': any(value['available'] for value in values),
                                        'queued': scheduler.queued('context' if name in ('monocle', 'straus') else name, task_id)})
                    yield 'data: ' + json.dumps(entries, ensure_ascii=False) + '\n\n'
                except TimeoutError:
                    yield ': keepalive\n\n'
            yield 'data: []\n\n'
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


class TelegramConfig(BaseModel):
    enabled: bool = False


class ProviderConfig(BaseModel):
    base_url: str = Field(default='https://lenec.tech/v1', max_length=2000)

    @field_validator('base_url')
    @classmethod
    def valid_endpoint(cls, value):
        return provider_url(value)


class CodexConfig(BaseModel):
    enabled: bool = False
    auto_prepare: bool = False


class NotificationsConfig(BaseModel):
    enabled: bool = False
    owner_id: int | None = Field(default=None, gt=0)
    lead_minutes: int = Field(default=30, ge=1, le=1440)


class CollectorConfig(BaseModel):
    enabled: bool = False


class ContextConfig(ProviderConfig):
    enabled: bool = True
    rewrite_title: bool = False
    model: str = Field(default='gpt-6-luna', min_length=1, max_length=200)


class AgentConfig(ProviderConfig):
    enabled: bool = True
    model: str = Field(default='gpt-6-luna', min_length=1, max_length=200)


CONFIG_MODELS = {'context': ContextConfig, 'ameba': AgentConfig, 'straus': AgentConfig, 'notifications': NotificationsConfig, 'collector': CollectorConfig, 'telegram': TelegramConfig, 'codex': CodexConfig}


@router.get('')
async def settings():
    try:
        state = await telegram.status()
    except (HTTPException, httpx.HTTPError):
        state = {'connected': False, 'analyzing': False}
    return {**public_settings(), 'telegram_connected': state['connected'], 'analyzing': state['analyzing']}


@router.patch('/{name}')
async def configure(name: Literal['telegram', 'codex', 'collector', 'notifications', 'context', 'ameba', 'straus'], body: dict):
    if body.get('enabled'):
        check_feature(name)
    allowed = set(CONFIG_MODELS[name].model_fields) | (SECRET_FIELDS[name] - {'session'})
    if set(body) - allowed:
        raise HTTPException(422, 'Неизвестное поле настроек')
    current, _ = read_settings(name, apply_feature=False)
    try:
        fields = {key: value for key, value in body.items() if key in CONFIG_MODELS[name].model_fields}
        config = CONFIG_MODELS[name].model_validate({**current, **fields}).model_dump()
        if name == 'notifications' and config['enabled'] and not config['owner_id']:
            raise ValueError('owner_id')
        if name == 'telegram' and body.get('api_id') and (not str(body['api_id']).isdecimal() or not 0 < int(body['api_id']) < 2**31):
            raise ValueError('api_id')
    except (ValidationError, ValueError):
        raise HTTPException(422, 'Проверьте настройки, часовой пояс и числовые значения') from None
    if name in ('collector', 'notifications') and body.get('bot_token'):
        other = 'collector' if name == 'notifications' else 'notifications'
        if read_settings(other)[1].get('bot_token') == body['bot_token']:
            raise HTTPException(422, 'Используйте два разных токена BotFather для двух ботов')
    secrets = {key: str(value) if value is not None else '' for key, value in body.items()
               if key in SECRET_FIELDS[name] and key != 'session' and value != ''}
    async with settings_lock:
        if name == 'telegram' and secrets:
            _, private = read_settings('telegram')
            if private.get('session'):
                raise HTTPException(409, 'Отключите Telegram перед сменой реквизитов аккаунта')
            await provider_call('telegram', telegram.close())
        save_settings(name, config=config, secrets=secrets if secrets else None)
        if name == 'context' and current.get('model') != config.get('model'):
            context.invalidate_file_contexts()
    return {'ok': True}


@router.post('/telegram/code')
async def telegram_code():
    return await provider_call('telegram', telegram.send_code())


class TelegramLogin(BaseModel):
    code: str | None = None
    password: str | None = None


@router.post('/telegram/login')
async def telegram_login(body: TelegramLogin):
    return await provider_call('telegram', telegram.sign_in(body.code, body.password))


@router.post('/telegram/disconnect')
async def telegram_disconnect():
    async with settings_lock:
        await provider_call('telegram', telegram.disconnect())
    return {'ok': True}


@router.get('/telegram/chats')
async def telegram_chats():
    return await provider_call('telegram', telegram.dialogs())


class ChatSelection(BaseModel):
    chat_ids: list[int] = Field(max_length=30)


@router.post('/telegram/chats')
async def telegram_select(body: ChatSelection):
    await provider_call('telegram', call('telegram', '/chats', {'chat_ids': list(dict.fromkeys(body.chat_ids))}))
    return {'ok': True}


@router.get('/codex/status')
async def codex_status():
    return await provider_call('codex', codex.status())


@router.post('/codex/connect')
async def codex_connect():
    return await provider_call('codex', codex.start_login())


@router.post('/codex/disconnect')
async def codex_disconnect():
    await provider_call('codex', codex.disconnect())
    return {'ok': True}


@router.post('/codex/check')
async def codex_check():
    return await provider_call('codex', codex.check())


@router.post('/ameba/check')
async def ameba_check(background: bool = False):
    if background:
        return jobs.start(lambda: provider_call('ameba', ameba.evaluate()))
    return await provider_call('ameba', ameba.evaluate())


@router.get('/ameba/ranking')
def ameba_ranking():
    check_feature('ameba')
    return ameba.ranking_status()


@router.delete('/ameba/plan')
async def delete_ameba_plan():
    check_feature('ameba')
    return await ameba.clear_plan()


class RankingRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=4000)


@router.post('/ameba/rank')
async def ameba_rank(background: bool = False, body: RankingRequest | None = None):
    check_feature('ameba')
    if not read_config('ameba')['enabled']:
        raise HTTPException(400, 'Включите Ameba в настройках')
    if body is not None and body.feedback is not None:
        ameba.save_plan_feedback(body.feedback)
    with Session(get_engine()) as session:
        signature = ameba.task_state(session)[0]
    current = scheduler.ranking_requests.get(user_id())
    if current and current.get('signature') == signature:
        return {'status': 'running', 'unchanged': True}
    status = ameba.ranking_status()
    if not status['needs_refresh']:
        return status | {'unchanged': True}
    await scheduler.interrupt_ranking(own_only=True)
    from common.calendar import reset_ameba_plan
    from uuid import uuid4
    reset_ameba_plan(uuid4().hex)
    if background:
        return jobs.start(lambda: provider_call('ameba', ameba.rank_all()))
    return await provider_call('ameba', ameba.rank_all())


@router.post('/tasks/{task_id}/ameba')
async def ameba_score(task_id: int, background: bool = False):
    config, _ = read_settings('ameba')
    if not config['enabled']:
        raise HTTPException(400, 'Включите Ameba в настройках')
    if background:
        return jobs.start(lambda: provider_call('ameba', ameba.evaluate(task_id)))
    return await provider_call('ameba', ameba.evaluate(task_id))


@router.post('/tasks/{task_id}/codex', status_code=202)
async def codex_task(task_id: int):
    return codex.start_task(task_id)


@router.get('/tasks/{task_id}/runs')
def runs(task_id: int):
    with Session(get_engine()) as session:
        return session.exec(select(CodexRun).where(CodexRun.task_id == task_id).order_by(CodexRun.created_at.desc())).all()


def require_run(run_id):
    with Session(get_engine()) as session:
        if not session.get(CodexRun, run_id):
            raise HTTPException(404, 'Запуск не найден')


@router.get('/codex/runs/{run_id}/files')
async def files(run_id: str):
    require_run(run_id)
    return await provider_call('codex', call('codex', f'/runs/{run_id}/files', method='GET'))


@router.get('/codex/runs/{run_id}/files/{filename:path}')
async def download(run_id: str, filename: str):
    require_run(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        result = await client.get(f'http://codex:8000/runs/{run_id}/files/{quote(filename, safe="/")}',
            headers={'X-Service-Token': internal_token('codex')})
        if not result.is_success:
            raise HTTPException(result.status_code, 'Файл недоступен')
        return Response(content=result.content, media_type='application/octet-stream',
            headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(Path(filename).name)}"})


@inbox.get('')
def pending_suggestions():
    with Session(get_engine()) as session:
        return session.exec(select(Suggestion).where(Suggestion.status == 'pending').order_by(Suggestion.id.desc())).all()


class Acceptance(BaseModel):
    values: TaskCreate | None = None
    target_task_id: int | None = None


@inbox.post('/{suggestion_id}/accept')
def accept(suggestion_id: int, body: Acceptance):
    with Session(get_engine()) as session:
        suggestion = session.get(Suggestion, suggestion_id)
        if not suggestion:
            raise HTTPException(404, 'Предложение не найдено')
        if body.target_task_id is not None:
            suggestion.target_task_id = body.target_task_id
        values = body.values.model_dump() if body.values else None
        if values and suggestion.action != 'create':
            values = {key: values[key] for key in ('title', 'description', 'deadline')}
        task = pipeline.apply_suggestion(session, suggestion, values)
        session.commit()
        session.refresh(task)
        return task


@inbox.post('/{suggestion_id}/reject')
def reject(suggestion_id: int):
    with Session(get_engine()) as session:
        suggestion = session.get(Suggestion, suggestion_id)
        if not suggestion:
            raise HTTPException(404, 'Предложение не найдено')
        if suggestion.status != 'pending':
            raise HTTPException(409, 'Предложение уже обработано')
        suggestion.status = 'rejected'
        session.add(suggestion)
        session.commit()
    return {'ok': True}


@router.post('/bots/{name}/check')
async def check_bot(name: Literal['collector', 'notifications']):
    return await provider_call(name, call(name, '/check'))


@router.get('/bots/collector/chats')
async def collector_chats():
    return await provider_call('collector', call('collector', '/chats', method='GET'))


@router.post('/bots/collector/chats')
async def collector_select(body: ChatSelection):
    return await provider_call('collector', call('collector', '/chats', body.model_dump()))


@router.api_route('/services/{name}/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
async def service_access(
    name: Literal['telegram', 'codex', 'collector', 'notifications', 'context', 'ameba', 'straus'],
    path: str,
    request: Request,
):
    check_feature(name)
    headers = {'X-Service-Token': internal_token(name), 'X-User-ID': str(user_id())}
    if request.headers.get('content-type'):
        headers['Content-Type'] = request.headers['content-type']
    try:
        async with httpx.AsyncClient(timeout=330) as client:
            result = await client.request(request.method, f'http://{"lucheba" if name == "context" else name}:8000/{quote(path, safe="/")}',
                params=request.query_params.multi_items(), headers=headers, content=await request.body())
    except httpx.HTTPError:
        raise HTTPException(502, 'Сервис недоступен или превышено время ожидания') from None
    response_headers = {key: result.headers[key] for key in ('content-type', 'content-disposition') if key in result.headers}
    return Response(content=result.content, status_code=result.status_code, headers=response_headers)


class ContextAnswer(BaseModel):
    subject: str = Field(default='', max_length=200)
    answer: str = Field(min_length=1, max_length=12000)


@router.get('/tasks/{task_id}/context')
def task_context(task_id: int):
    return context.get_context(task_id)


@router.post('/tasks/{task_id}/context/analyze')
async def analyze_context(task_id: int, background: bool = False):
    if background:
        return jobs.start(lambda: provider_call('context', context.prepare(task_id, force=True)))
    return await provider_call('context', context.prepare(task_id, force=True))


@router.post('/tasks/{task_id}/context')
async def answer_context(task_id: int, body: ContextAnswer, background: bool = False):
    check_feature('context')
    context.save_answer(task_id, body.subject, body.answer)
    if not features()['ameba'] or not read_settings('ameba')[0]['enabled']:
        if background:
            return jobs.start(lambda: provider_call('context', context.prepare(task_id, force=True)))
        return await provider_call('context', context.prepare(task_id, force=True))
    if background:
        return jobs.start(lambda: provider_call('ameba', ameba.evaluate(task_id)))
    return await provider_call('ameba', ameba.evaluate(task_id))


class ContextFile(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=14 * 1024 * 1024)


class WorkspaceFile(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1024 * 1024)
    version: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


@router.get('/context/workspace')
async def workspace_tree():
    from common.browser_client import browser_request
    # Перенос прежних локальных загрузок выполняется перед чтением общего списка.
    warning = None
    try:
        await browser_request('/downloads/migrate', {})
    except HTTPException:
        warning = 'Не удалось проверить старые загрузки браузера. Файлы Silo показаны ниже.'
    return {'files': await call('context', '/workspace', method='GET'), 'warning': warning}


class WorkspaceUpload(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=14 * 1024 * 1024)


class WorkspaceArchive(BaseModel):
    paths: list[str] = Field(min_length=2, max_length=50)
    output_path: str = Field(min_length=1, max_length=500)


@router.post('/context/workspace/upload')
async def upload_workspace_file(body: WorkspaceUpload):
    return await call('context', '/workspace/upload', body.model_dump())


@router.post('/context/workspace/archive')
async def workspace_archive(body: WorkspaceArchive):
    return await call('context', '/workspace/archive', body.model_dump())


@router.get('/context/workspace/preview')
async def preview_workspace_file(path: str, page: int = 1, sheet: str = '', offset: int = 0):
    if page < 1 or offset < 0:
        raise HTTPException(422, 'Неверная страница')
    from common.browser_client import browser_request
    original = await call('context', '/workspace/preview?path=' + quote(path, safe=''), method='GET')
    return await browser_request('/file-preview', original | {'page': page, 'sheet': sheet, 'offset': offset})


@router.get('/context/workspace/download')
async def download_workspace_file(path: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            result = await client.get('http://lucheba:8000/workspace/download', params={'path': path},
                headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())})
    except httpx.HTTPError:
        raise HTTPException(503, 'Silo недоступен') from None
    return Response(result.content, status_code=result.status_code,
                    headers={key: result.headers[key] for key in ('content-type', 'content-disposition') if key in result.headers})


@router.get('/context/workspace/file')
async def workspace_read(path: str):
    return await call('context', '/workspace/file?path=' + quote(path, safe=''), method='GET')


@router.post('/context/workspace/folder')
async def workspace_folder(path: str):
    return await call('context', '/workspace/folder?path=' + quote(path, safe=''))


@router.put('/context/workspace/file')
async def workspace_save(body: WorkspaceFile):
    result = await call('context', '/workspace/file', body.model_dump(), method='PUT')
    context.invalidate_file_contexts(result.get('task_id'))
    return result


@router.delete('/context/workspace/file')
async def workspace_delete(path: str):
    return await call('context', '/workspace/file?path=' + quote(path, safe=''), method='DELETE')


class SavedContextFile(BaseModel):
    source_task_id: int = Field(gt=0)
    file_id: str = Field(pattern=r'^[a-f0-9]{32}$')


@router.get('/context/files')
async def saved_context_files():
    return await call('context', '/files', method='GET')


@router.post('/tasks/{task_id}/context/files/reuse')
async def reuse_context_file(task_id: int, body: SavedContextFile):
    context.get_context(task_id)
    result = await call('context', f'/tasks/{task_id}/files/reuse', body.model_dump())
    context.invalidate(task_id)
    return result


@router.get('/tasks/{task_id}/context/files')
async def context_files(task_id: int):
    context.get_context(task_id)  # Проверяем задачу в базе текущего пользователя.
    return await call('context', f'/tasks/{task_id}/files', method='GET')


@router.post('/tasks/{task_id}/context/files')
async def attach_context_file(task_id: int, body: ContextFile):
    check_feature('context')
    context.get_context(task_id)
    try:
        content = base64.b64decode(body.content, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, 'Не удалось прочитать загруженный файл') from None
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Максимальный размер файла — 10 МБ')
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            result = await client.post(f'http://lucheba:8000/tasks/{task_id}/files', params={'name': body.name},
                headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())}, content=content)
    except httpx.HTTPError:
        raise HTTPException(503, 'Хранилище файлов недоступно') from None
    if not result.is_success:
        try:
            detail = result.json()['detail']
        except (ValueError, KeyError):
            detail = 'Не удалось сохранить файл'
        raise HTTPException(result.status_code, detail)
    context.invalidate(task_id)
    return result.json()


@router.delete('/tasks/{task_id}/context/files/{file_id}')
async def delete_context_file(task_id: int, file_id: str):
    context.get_context(task_id)
    if len(file_id) != 32 or any(c not in '0123456789abcdef' for c in file_id):
        raise HTTPException(404, 'Файл не найден')
    result = await call('context', f'/tasks/{task_id}/files/{file_id}', method='DELETE')
    context.invalidate(task_id)
    return result


@router.get('/context/files/{task_id}/{file_id}')
@router.get('/tasks/{task_id}/context/files/{file_id}')
async def download_context_file(task_id: int, file_id: str):
    check_feature('context')
    # Файл может остаться в библиотеке после удаления задачи. Владение задаётся
    # пользовательским префиксом Silo и подписанным межсервисным запросом.
    if task_id <= 0:
        raise HTTPException(404, 'Файл не найден')
    if len(file_id) != 32 or any(c not in '0123456789abcdef' for c in file_id):
        raise HTTPException(404, 'Файл не найден')
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            result = await client.get(f'http://lucheba:8000/tasks/{task_id}/files/{file_id}',
                headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())})
    except httpx.HTTPError:
        raise HTTPException(503, 'Хранилище файлов недоступно') from None
    return Response(result.content, status_code=result.status_code,
                    headers={key: result.headers[key] for key in ('content-type', 'content-disposition') if key in result.headers})


@router.api_route('/context/browser/sites', methods=['GET', 'POST'])
async def browser_sites(request: Request):
    body = await request.json() if request.method == 'POST' else None
    return await call('context', '/browser/sites', body, method=request.method)


@router.post('/context/browser/handoff')
async def browser_handoff(body: dict):
    from common.browser_client import browser_request
    return await browser_request('/handoff', body)


@router.post('/context/browser/manual')
async def profile_browser_action(body: dict):
    if scheduler.queue_state()['busy']:
        raise HTTPException(409, 'Дождитесь завершения работы агентов перед ручным управлением браузером')
    return await call('context', '/browser/manual', body)


@router.patch('/context/browser/sites/{site_id}')
async def update_browser_site(site_id: str, body: dict):
    return await call('context', f'/browser/sites/{site_id}', body, method='PATCH')


@router.delete('/context/browser/sites/{site_id}')
async def delete_browser_site(site_id: str):
    if len(site_id) != 32 or any(c not in '0123456789abcdef' for c in site_id):
        raise HTTPException(404, 'Личный кабинет не найден')
    return await call('context', f'/browser/sites/{site_id}', method='DELETE')


@router.get('/context/browser/screen')
async def luna_screen(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await call('context', '/browser/screen', method='GET', timeout=5)


@router.get('/context/browser/stream')
async def luna_browser_stream():
    if not (features()['context'] or features()['straus']):
        check_feature('context')
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=8))
    try:
        upstream = await client.send(client.build_request('GET', 'http://browser:8000/screen/stream',
            headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())}), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, 'Видеопоток браузера недоступен') from None
    if not upstream.is_success:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(503, 'Браузер не открыл видеопоток')

    async def frames():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(frames(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.api_route('/tasks/{task_id}/context/browser', methods=['GET', 'PUT'])
async def task_browser(task_id: int, request: Request):
    context.get_context(task_id)
    body = await request.json() if request.method == 'PUT' else None
    result = await call('context', f'/tasks/{task_id}/browser', body, method=request.method)
    if request.method == 'PUT':
        context.invalidate(task_id)
    return result


@router.post('/tasks/{task_id}/context/browser/action')
async def browser_action(task_id: int, body: dict):
    context.get_context(task_id)
    result = await call('context', '/browser/manual', body)
    if body.get('action') != 'state':
        context.invalidate(task_id)
    return result
