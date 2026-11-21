import asyncio
import base64
import binascii
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlmodel import Session, select

from common.provider import provider_url
from common.database import get_engine
from common.rpc import call, internal_token, service_name
from common.security import require_user
from common.identity import user_id
from common.accounts import check_feature, features
from integrations import ameba, arrodes, telegram, context, jobs, scheduler, straus
from common import extraction as pipeline
from common.settings import read_config, SECRET_FIELDS, public_settings, read_settings, report, save_settings
from common.models.integration import ArrodesRun, Suggestion
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


@router.get('/monocle/schedule/import/status')
def schedule_import_status():
    check_feature('straus')
    previous = jobs.last('straus')
    error = previous.get('detail') if previous and previous['status'] == 'failed' else None
    return {'running': jobs.active('straus') is not None,
            'error': error if error != 'Операция остановлена' else None}


@router.post('/monocle/schedule/import/stop')
async def stop_schedule_import():
    result = await jobs.stop('straus')
    return result


@router.api_route('/agents/{name}/models', methods=['GET', 'POST'])
async def agent_models(name: Literal['monocle', 'ameba', 'straus', 'arrodes'], request: Request):
    config, _ = read_settings(name)
    check_feature(name)
    body = await request.json() if request.method == 'POST' else {}
    auth_mode = (body.get('auth_mode') or config.get('auth_mode') or ('oauth' if name == 'arrodes' else 'api_key')).strip()
    if auth_mode not in ('api_key', 'oauth'):
        raise HTTPException(422, 'Неизвестный режим авторизации')
    if auth_mode == 'oauth':
        return await call('arrodes', '/models', method='GET', timeout=45)
    provider_cfg, provider_secrets = read_settings('ai_provider', apply_feature=False)
    key = body.get('api_key') or provider_secrets.get('api_key')
    if not key:
        raise HTTPException(400, 'Сохраните общий API-ключ в разделе «Подключения AI»')
    try:
        endpoint = provider_url(body.get('base_url') or provider_cfg['base_url'])
    except ValueError:
        raise HTTPException(422, 'Проверьте endpoint провайдера') from None
    return await call(name, '/models', {'api_key': key, 'base_url': endpoint}, timeout=45)


@router.post('/ai_provider/check')
async def check_ai_provider(body: dict | None = None):
    check_any_agent_feature()
    data = body or {}
    provider_cfg, provider_secrets = read_settings('ai_provider', apply_feature=False)
    key = (data.get('api_key') or provider_secrets.get('api_key') or '').strip()
    if not key:
        raise HTTPException(400, 'Сохраните общий API-ключ в разделе «Подключения AI»')
    try:
        endpoint = provider_url(data.get('base_url') or provider_cfg['base_url'])
    except ValueError:
        raise HTTPException(422, 'Проверьте endpoint провайдера') from None
    enabled = features()
    service = next((name for name in ('monocle', 'ameba', 'straus', 'arrodes') if enabled[name]), None)
    if not service:
        raise HTTPException(403, 'Все AI-агенты отключены администратором')
    models = await provider_call('ai_provider', call(service, '/models', {'api_key': key, 'base_url': endpoint}, timeout=45))
    return {'ok': True, 'models_count': len(models)}


@router.post('/agents/{name}/check')
async def check_agent(name: Literal['monocle', 'ameba', 'straus', 'arrodes']):
    check_feature(name)
    operations = {
        'ameba': ameba.evaluate,
        'arrodes': arrodes.check,
        'monocle': context.check,
        'straus': straus.check,
    }
    return await provider_call(name, operations[name]())


def check_any_agent_feature():
    enabled = features()
    if not any(enabled[name] for name in ('monocle', 'ameba', 'straus', 'arrodes')):
        raise HTTPException(403, 'Все AI-агенты отключены администратором')


@router.get('/openai-oauth/status')
async def openai_oauth_status():
    check_any_agent_feature()
    return await arrodes.status()


@router.post('/openai-oauth/connect')
async def openai_oauth_connect():
    check_any_agent_feature()
    return await arrodes.start_login()


@router.post('/openai-oauth/disconnect')
async def openai_oauth_disconnect():
    check_any_agent_feature()
    await arrodes.disconnect()
    return {'ok': True}


class AgentAnswer(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


@router.post('/agents/{name}/questions/{question_id}/answer')
async def answer_agent(name: Literal['monocle', 'ameba', 'straus', 'arrodes'], question_id: str, body: AgentAnswer):
    check_feature(name)
    if len(question_id) != 32 or any(char not in '0123456789abcdef' for char in question_id):
        raise HTTPException(422, 'Некорректный вопрос')
    return await call(name, f'/questions/{question_id}/answer', body.model_dump(), timeout=15)


@router.get('/queue')
async def agent_queue():
    state = scheduler.queue_state()
    enabled = features()
    browser_state = {'holder': None, 'waiting': []}
    if state['agents_busy']:
        try:
            from common.browser_client import browser_request
            browser_state = await asyncio.wait_for(browser_request('/session/status', method='GET'), timeout=2)
        except (HTTPException, TimeoutError):
            pass
    holder = browser_state.get('holder')
    browser_waiters = browser_state.get('waiting') or []

    def provider_feature(name, task_id):
        if name == 'context':
            return 'straus' if not task_id else 'monocle'
        return service_name(name)

    def item_browser_state(name, task_id):
        agent_label = {'monocle': 'Monocle', 'straus': 'Straus', 'arrodes': 'Arrodes'}.get(name)
        if not agent_label:
            return None
        if holder and holder.get('agent') == agent_label and (not task_id or not holder.get('task_id') or holder.get('task_id') == task_id):
            return 'using'
        if any(w.get('agent') == agent_label and (not task_id or not w.get('task_id') or w.get('task_id') == task_id) for w in browser_waiters):
            return 'waiting'
        return None

    with Session(get_engine()) as session:
        def enrich_browser_entry(entry):
            tid = entry.get('task_id') or 0
            task = session.get(Task, tid) if tid else None
            return entry | {'title': task.title if task else (entry.get('agent') or 'Агент')}

        for group in ('active', 'waiting'):
            entries = []
            for item in state[group]:
                feature = provider_feature(item['provider'], item['task_id'])
                if not enabled.get(feature, False):
                    continue
                task = session.get(Task, item['task_id']) if item['task_id'] else None
                title = task.title if task else ('Расписание · Straus' if feature == 'straus' else 'Общая сортировка')
                entry = {
                    'provider': feature if item['provider'] == 'context' else item['provider'],
                    'task_id': item['task_id'],
                    'title': title,
                }
                if group == 'active':
                    entry['browser_state'] = item_browser_state(entry['provider'], item['task_id'])
                entries.append(entry)
            state[group] = entries
        state['browser'] = {
            'holder': enrich_browser_entry(holder) if holder else None,
            'waiting': [enrich_browser_entry(item) for item in browser_waiters],
        }
    return state


@router.get('/progress')
async def agent_progress(task_id: int = 0, providers: str = 'context,arrodes'):
    if task_id < 0:
        raise HTTPException(422, 'Некорректный номер задачи')
    if task_id:
        context.get_context(task_id)
    enabled = features()
    allowed = {'context': enabled['context'] or (task_id == 0 and enabled['straus']),
               'monocle': enabled['context'], 'straus': task_id == 0 and enabled['straus'],
               'ameba': enabled['ameba'], 'arrodes': enabled['arrodes'], 'codex': enabled['arrodes']}
    names = [name for name in allowed if name in providers.split(',') and allowed[name]]

    async def read(name):
        queue_provider = ('monocle' if task_id else 'straus') if name == 'context' else service_name(name)
        queued = scheduler.queued(queue_provider, task_id)
        try:
            extras = []
            if name in ('monocle', 'straus'):
                service = 'context' if name == 'monocle' else 'straus'
                path = '/work/progress' if name == 'monocle' and task_id == 0 else f'/progress?task_id={task_id}'
                result = await call(service, path, method='GET', timeout=2)
                if isinstance(result, dict) and task_id == 0:
                    extras = result.pop('other_running', None) or []
                available = True
            elif name == 'context' and task_id == 0:
                operations = []
                if enabled['context']:
                    operations.append(call('monocle', '/work/progress', method='GET', timeout=2))
                if enabled['straus']:
                    operations.append(call('straus', '/progress', method='GET', timeout=2))
                results = await asyncio.gather(*operations, return_exceptions=True)
                runs = []
                for run in results:
                    if isinstance(run, dict):
                        extras.extend(run.pop('other_running', None) or [])
                        runs.append(run)
                result = max(runs, key=lambda run: (
                    run['status'] == 'running',
                    bool(run.get('pending_question')),
                    run.get('last_activity_at') or run['started_at'],
                )) if runs else None
                available = any(not isinstance(run, Exception) for run in results)
            else:
                service = name
                path = '/ranking/progress' if name == 'ameba' else '/progress'
                result = await call(service, f'{path}?task_id={task_id}' + ('&work=true' if name in ('arrodes', 'codex') and task_id == 0 else ''), method='GET', timeout=2)
                available = True
            items = [{'provider': name, 'run': result, 'available': available, 'queued': queued}]
            if task_id == 0:
                seen_ids = {result['id']} if isinstance(result, dict) and result.get('id') else set()
                for extra in extras:
                    if extra.get('id') in seen_ids:
                        continue
                    seen_ids.add(extra.get('id'))
                    items.append({'provider': f"{name}:{extra['task_id']}", 'run': extra, 'available': available, 'queued': False})
            return items
        except (HTTPException, httpx.HTTPError):
            return [{'provider': name, 'run': None, 'available': False, 'queued': queued}]

    groups = await asyncio.gather(*(read(name) for name in names))
    return [entry for group in groups for entry in group]


@router.get('/progress/stream')
async def agent_progress_stream(request: Request, task_id: int = 0, providers: str = 'context,arrodes'):
    if task_id < 0:
        raise HTTPException(422, 'Некорректный номер задачи')
    if task_id:
        context.get_context(task_id)
    enabled = features()
    sources = []
    requested = providers.split(',')
    if 'monocle' in requested and enabled['context']:
        sources.append(('monocle', 'monocle', 'agent', task_id == 0))
    if 'straus' in requested and task_id == 0 and enabled['straus']:
        sources.append(('straus', 'straus', 'agent', False))
    if 'context' in requested:
        if enabled['context']:
            sources.append(('context', 'context', 'agent', task_id == 0))
        if task_id == 0 and enabled['straus']:
            sources.append(('context', 'straus', 'agent', False))
    if 'ameba' in requested and enabled['ameba']:
        sources.append(('ameba', 'ameba', 'ranking', task_id == 0))
    for provider in ('arrodes', 'codex'):
        if provider in requested and enabled['arrodes']:
            sources.append((provider, 'arrodes', 'agent', task_id == 0))

    owner = user_id()

    async def events():
        changes = asyncio.Queue(maxsize=8)
        states = {}

        async def listen(provider, service, namespace, work):
            host = service_name(service)
            url = f'http://{host}:8000/progress/stream'
            headers = {'X-Service-Token': internal_token(service, owner), 'X-User-ID': str(owner)}
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
            if not sources:
                yield 'data: []\n\n'
                while not await request.is_disconnected():
                    await asyncio.sleep(15)
                    yield ': keepalive\n\n'
                return
            while not await request.is_disconnected():
                try:
                    provider, service, run, available = await asyncio.wait_for(changes.get(), timeout=15)
                    states[service] = {'run': run, 'available': available}
                    entries = []
                    for name in dict.fromkeys(source[0] for source in sources):
                        values = [states[source[1]] for source in sources if source[0] == name and source[1] in states]
                        raw_runs = [value['run'] for value in values if value['run']]
                        runs = []
                        extras = []
                        for item in raw_runs:
                            clean = dict(item)
                            extras.extend(clean.pop('other_running', None) or [])
                            runs.append(clean)
                        current = max(runs, key=lambda item: (
                            item['status'] == 'running',
                            bool(item.get('pending_question')),
                            item.get('last_activity_at') or item['started_at'],
                        )) if runs else None
                        now_iso = datetime.now(timezone.utc).isoformat()
                        is_available = any(value['available'] for value in values)
                        entries.append({'provider': name, 'run': current,
                                        'server_time': now_iso,
                                        'available': is_available,
                                        'queued': scheduler.queued(('monocle' if task_id else 'straus') if name == 'context' else service_name(name), task_id)})
                        if task_id == 0:
                            seen_ids = {current['id']} if current and current.get('id') else set()
                            for extra in extras:
                                if extra.get('id') in seen_ids:
                                    continue
                                seen_ids.add(extra.get('id'))
                                entries.append({'provider': f"{name}:{extra['task_id']}", 'run': extra,
                                                'server_time': now_iso,
                                                'available': is_available,
                                                'queued': False})
                    yield 'data: ' + json.dumps(entries, ensure_ascii=False) + '\n\n'
                except TimeoutError:
                    yield ': keepalive\n\n'
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


class AiProviderConfig(ProviderConfig):
    pass


class BaseAgentConfig(BaseModel):
    auth_mode: Literal['api_key', 'oauth'] = 'api_key'
    model: str = Field(default='gpt-6-luna', max_length=200)
    reasoning_effort: Literal['low', 'medium', 'high'] = 'medium'
    custom_instructions: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def check_api_key_model(self):
        self.model = self.model.strip()
        self.custom_instructions = self.custom_instructions.strip()
        if self.auth_mode == 'api_key' and getattr(self, 'enabled', False) and not self.model:
            raise ValueError('model')
        return self


class ArrodesConfig(BaseAgentConfig):
    enabled: bool = False
    auth_mode: Literal['api_key', 'oauth'] = 'oauth'
    model: str = Field(default='', max_length=200)
    auto_prepare: bool = False
    web_search_mode: Literal['cached', 'live', 'disabled'] = 'cached'
    allow_browser: bool = True
    allow_shell: bool = True
    ask_clarifications: bool = True


class NotificationsConfig(BaseModel):
    enabled: bool = False
    owner_id: int | None = Field(default=None, gt=0)
    lead_minutes: int = Field(default=30, ge=1, le=1440)


class CollectorConfig(BaseModel):
    enabled: bool = False


class ContextConfig(BaseAgentConfig):
    enabled: bool = True
    rewrite_title: bool = False
    allow_browser: bool = True
    ask_clarifications: bool = True


class AmebaConfig(BaseAgentConfig):
    enabled: bool = False
    context_window: int | None = Field(default=None, ge=4096, le=10_000_000)
    auto_replan: bool = True
    planning_style: Literal['gentle', 'balanced', 'intense'] = 'balanced'
    allow_context_recheck: bool = True


class StrausConfig(BaseAgentConfig):
    enabled: bool = True
    allow_browser: bool = True
    ask_clarifications: bool = True


CONFIG_MODELS = {
    'ai_provider': AiProviderConfig,
    'monocle': ContextConfig,
    'ameba': AmebaConfig,
    'straus': StrausConfig,
    'notifications': NotificationsConfig,
    'collector': CollectorConfig,
    'telegram': TelegramConfig,
    'arrodes': ArrodesConfig,
}


@router.get('')
async def settings():
    try:
        state = await telegram.status()
    except (HTTPException, httpx.HTTPError):
        state = {'connected': False, 'analyzing': False}
    return {**public_settings(), 'telegram_connected': state['connected'], 'analyzing': state['analyzing']}


@router.patch('/{name}')
async def configure(name: Literal['telegram', 'arrodes', 'collector', 'notifications', 'monocle', 'ameba', 'straus', 'ai_provider'], body: dict):
    name = service_name(name)
    if name == 'ai_provider':
        check_any_agent_feature()
    elif body.get('enabled'):
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
        if name == 'ai_provider' and current.get('base_url') != config.get('base_url'):
            if read_config('monocle').get('auth_mode') == 'api_key':
                context.invalidate_file_contexts()
        if name == 'monocle' and (current.get('model') != config.get('model') or current.get('auth_mode') != config.get('auth_mode')):
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


@router.get('/arrodes/status')
async def arrodes_status():
    return await provider_call('arrodes', arrodes.status())


@router.api_route('/arrodes/models', methods=['GET', 'POST'])
async def arrodes_models(request: Request):
    return await agent_models('arrodes', request)


@router.post('/arrodes/connect')
async def arrodes_connect():
    return await provider_call('arrodes', arrodes.start_login())


@router.post('/arrodes/disconnect')
async def arrodes_disconnect():
    await provider_call('arrodes', arrodes.disconnect())
    return {'ok': True}


@router.post('/arrodes/check')
async def arrodes_check():
    return await provider_call('arrodes', arrodes.check())


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


class PlanFeedbackInput(BaseModel):
    task_id: int = Field(gt=0)
    outcome: Literal['done', 'partial', 'missed', 'too_hard']
    actual_minutes: int | None = Field(default=None, ge=0, le=2880)
    period_id: str | None = Field(default=None, max_length=80)


class TaskPinInput(BaseModel):
    task_id: int = Field(gt=0)
    mode: Literal['shift', 'date'] | None = None
    target_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    not_before: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    shift_days: int | None = Field(default=None, ge=-14, le=14)
    clear: bool = False


class EstimateOverrideInput(BaseModel):
    task_id: int = Field(gt=0)
    remaining_minutes: int | None = Field(default=None, ge=0, le=10000)


class PlanAdjustmentInput(BaseModel):
    day_overrides: dict[str, Literal['normal', 'light', 'free', 'focused', 'auto'] | None] | None = None
    task_pin: TaskPinInput | None = None
    estimate_override: EstimateOverrideInput | None = None
    reset: bool = False
    clear_feedback: bool = False


@router.post('/ameba/feedback')
async def ameba_feedback(body: PlanFeedbackInput):
    check_feature('ameba')
    return await ameba.submit_feedback(body.task_id, body.outcome, body.actual_minutes, body.period_id)


@router.patch('/ameba/plan')
async def ameba_adjust_plan(body: PlanAdjustmentInput):
    check_feature('ameba')
    return await ameba.adjust_plan(
        day_overrides=body.day_overrides,
        task_pin=body.task_pin.model_dump() if body.task_pin else None,
        estimate_override=body.estimate_override.model_dump() if body.estimate_override else None,
        reset=body.reset,
        clear_feedback=body.clear_feedback,
    )


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


class ArrodesInstruction(BaseModel):
    instruction: str = Field(default='', max_length=12000)
    resume_run_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')


@router.post('/tasks/{task_id}/arrodes', status_code=202)
async def arrodes_task(task_id: int, body: ArrodesInstruction | None = None):
    return arrodes.start_task(task_id, **(body.model_dump() if body else {}))


@router.post('/arrodes/stop')
async def arrodes_stop():
    return await arrodes.stop()


@router.get('/tasks/{task_id}/runs')
def runs(task_id: int):
    with Session(get_engine()) as session:
        return session.exec(select(ArrodesRun).where(ArrodesRun.task_id == task_id).order_by(ArrodesRun.created_at.desc())).all()


def require_run(run_id):
    with Session(get_engine()) as session:
        if not session.get(ArrodesRun, run_id):
            raise HTTPException(404, 'Запуск не найден')


@router.get('/arrodes/runs/{run_id}/files')
async def files(run_id: str):
    require_run(run_id)
    return await provider_call('arrodes', call('arrodes', f'/runs/{run_id}/files', method='GET'))


@router.get('/arrodes/runs/{run_id}/files/{filename:path}')
async def download(run_id: str, filename: str):
    require_run(run_id)
    async with httpx.AsyncClient(timeout=30) as client:
        result = await client.get(f'http://arrodes:8000/runs/{run_id}/files/{quote(filename, safe="/")}',
            headers={'X-Service-Token': internal_token('arrodes'), 'X-User-ID': str(user_id())})
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
    name: Literal['telegram', 'arrodes', 'collector', 'notifications', 'monocle', 'ameba', 'straus'],
    path: str,
    request: Request,
):
    check_feature(name)
    headers = {'X-Service-Token': internal_token(name), 'X-User-ID': str(user_id())}
    if request.headers.get('content-type'):
        headers['Content-Type'] = request.headers['content-type']
    try:
        async with httpx.AsyncClient(timeout=330) as client:
            result = await client.request(request.method, f'http://{service_name(name)}:8000/{quote(path, safe="/")}',
                params=request.query_params.multi_items(), headers=headers, content=await request.body())
    except httpx.HTTPError:
        raise HTTPException(502, 'Сервис недоступен или превышено время ожидания') from None
    response_headers = {key: result.headers[key] for key in ('content-type', 'content-disposition') if key in result.headers}
    return Response(content=result.content, status_code=result.status_code, headers=response_headers)


class ContextAnswer(BaseModel):
    subject: str = Field(default='', max_length=200)
    answer: str = Field(min_length=1, max_length=12000)


@router.get('/tasks/{task_id}/monocle')
def task_context(task_id: int):
    return context.get_context(task_id)


@router.post('/tasks/{task_id}/monocle/analyze')
async def analyze_context(task_id: int, background: bool = False):
    if background:
        return jobs.start(lambda: provider_call('monocle', context.prepare(task_id, force=True)))
    return await provider_call('monocle', context.prepare(task_id, force=True))


@router.post('/tasks/{task_id}/monocle')
async def answer_context(task_id: int, body: ContextAnswer, background: bool = False):
    check_feature('monocle')
    context.save_answer(task_id, body.subject, body.answer)
    if not features()['ameba'] or not read_settings('ameba')[0]['enabled']:
        if background:
            return jobs.start(lambda: provider_call('monocle', context.prepare(task_id, force=True)))
        return await provider_call('monocle', context.prepare(task_id, force=True))
    if background:
        return jobs.start(lambda: provider_call('ameba', ameba.evaluate(task_id)))
    return await provider_call('ameba', ameba.evaluate(task_id))


class ContextFile(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=36 * 1024 * 1024)


def _trim_filename(name: str, limit: int = 200) -> str:
    clean = name.replace('\\', '/').split('/')[-1].strip() or 'file'
    if len(clean) <= limit:
        return clean
    suffix = Path(clean).suffix
    if 0 < len(suffix) < limit:
        return clean[:limit - len(suffix)] + suffix
    return clean[:limit]


class WorkspaceFile(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1024 * 1024)
    version: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


@router.get('/monocle/workspace')
async def workspace_tree():
    from common.browser_client import browser_request
    # Перенос прежних локальных загрузок выполняется перед чтением общего списка.
    warning = None
    try:
        await browser_request('/downloads/migrate', {})
    except HTTPException:
        warning = 'Не удалось проверить старые загрузки браузера. Файлы Silo показаны ниже.'
    return {'files': await call('monocle', '/workspace', method='GET'), 'warning': warning}


class WorkspaceUpload(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=36 * 1024 * 1024)
    overwrite: bool = False


class WorkspaceArchive(BaseModel):
    paths: list[str] = Field(min_length=2, max_length=50)
    output_path: str = Field(min_length=1, max_length=500)


@router.post('/monocle/workspace/upload')
async def upload_workspace_file(body: WorkspaceUpload):
    return await call('monocle', '/workspace/upload', body.model_dump())


@router.post('/monocle/workspace/archive')
async def workspace_archive(body: WorkspaceArchive):
    return await call('monocle', '/workspace/archive', body.model_dump())


@router.get('/monocle/workspace/preview')
async def preview_workspace_file(path: str, page: int = 1, sheet: str = '', offset: int = 0):
    if page < 1 or offset < 0:
        raise HTTPException(422, 'Неверная страница')
    from common.browser_client import browser_request
    original = await call('monocle', '/workspace/preview?path=' + quote(path, safe=''), method='GET')
    return await browser_request('/file-preview', original | {'page': page, 'sheet': sheet, 'offset': offset})


@router.get('/monocle/workspace/download')
async def download_workspace_file(path: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            result = await client.get('http://monocle:8000/workspace/download', params={'path': path},
                headers={'X-Service-Token': internal_token('monocle'), 'X-User-ID': str(user_id())})
    except httpx.HTTPError:
        raise HTTPException(503, 'Silo недоступен') from None
    return Response(result.content, status_code=result.status_code,
                    headers={key: result.headers[key] for key in ('content-type', 'content-disposition') if key in result.headers})


@router.get('/monocle/workspace/file')
async def workspace_read(path: str):
    return await call('monocle', '/workspace/file?path=' + quote(path, safe=''), method='GET')


@router.post('/monocle/workspace/folder')
async def workspace_folder(path: str):
    return await call('monocle', '/workspace/folder?path=' + quote(path, safe=''))


@router.put('/monocle/workspace/file')
async def workspace_save(body: WorkspaceFile):
    result = await call('monocle', '/workspace/file', body.model_dump(), method='PUT')
    context.invalidate_file_contexts(result.get('task_id'))
    return result


@router.delete('/monocle/workspace/file')
async def workspace_delete(path: str):
    return await call('monocle', '/workspace/file?path=' + quote(path, safe=''), method='DELETE')


class SavedContextFile(BaseModel):
    source_task_id: int = Field(gt=0)
    file_id: str = Field(pattern=r'^[a-f0-9]{32}$')


@router.get('/monocle/files')
async def saved_context_files():
    return await call('monocle', '/files', method='GET')


@router.post('/tasks/{task_id}/monocle/files/reuse')
async def reuse_context_file(task_id: int, body: SavedContextFile):
    context.get_context(task_id)
    result = await call('monocle', f'/tasks/{task_id}/files/reuse', body.model_dump())
    context.invalidate(task_id)
    return result


@router.get('/tasks/{task_id}/monocle/files')
async def context_files(task_id: int):
    context.get_context(task_id)  # Проверяем задачу в базе текущего пользователя.
    return await call('monocle', f'/tasks/{task_id}/files', method='GET')


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_TTL_SECONDS = 600
_staged_uploads: dict[tuple[int, str], dict] = {}


def _drop_staged_upload(key: tuple[int, str]):
    entry = _staged_uploads.pop(key, None)
    if entry:
        entry['path'].unlink(missing_ok=True)


def _cleanup_staged_uploads(owner: int | None = None):
    cutoff = time.monotonic() - UPLOAD_TTL_SECONDS
    for key in [item_key for item_key, entry in _staged_uploads.items() if entry['updated_at'] < cutoff]:
        _drop_staged_upload(key)
    if owner is not None:
        owned = sorted(((entry['updated_at'], item_key) for item_key, entry in _staged_uploads.items() if item_key[0] == owner), key=lambda item: item[0])
        for _, key in owned[:-4]:
            _drop_staged_upload(key)


async def _store_task_file(task_id: int, name: str, content: bytes):
    safe_name = _trim_filename(name)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            result = await client.post(f'http://monocle:8000/tasks/{task_id}/files', params={'name': safe_name},
                headers={'X-Service-Token': internal_token('monocle'), 'X-User-ID': str(user_id())}, content=content)
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


class UploadChunk(BaseModel):
    upload_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    index: int = Field(default=0, ge=0, le=200)
    content: str = Field(default='', max_length=2 * 1024 * 1024)
    done: bool = False
    target: Literal['task', 'workspace']
    task_id: int | None = Field(default=None, gt=0)
    name: str = Field(default='', max_length=255)
    path: str = Field(default='', max_length=500)
    overwrite: bool = False


@router.post('/uploads/chunk')
async def upload_chunk(body: UploadChunk):
    enabled = features()
    if body.target == 'task':
        if not (enabled['monocle'] or enabled['arrodes']):
            check_feature('monocle')
        if not body.task_id or not body.name.strip():
            raise HTTPException(422, 'Укажите задачу и имя файла')
        context.get_context(body.task_id)
    else:
        if not (enabled['monocle'] or enabled['straus'] or enabled['arrodes']):
            check_feature('monocle')
        if not body.path.strip():
            raise HTTPException(422, 'Укажите путь к файлу')
    owner = user_id()
    _cleanup_staged_uploads(owner if body.upload_id is None else None)
    if body.upload_id is None:
        if body.index != 0:
            raise HTTPException(422, 'Неверный порядок фрагментов загрузки')
        upload_id = uuid.uuid4().hex
        key = (owner, upload_id)
        fd, temp_path = tempfile.mkstemp(prefix='ucheba-upload-')
        os.close(fd)
        entry = {'path': Path(temp_path), 'size': 0, 'next_index': 0, 'completed': False, 'updated_at': time.monotonic()}
        _staged_uploads[key] = entry
    else:
        upload_id = body.upload_id
        key = (owner, upload_id)
        entry = _staged_uploads.get(key)
        if not entry:
            raise HTTPException(410, 'Сессия загрузки истекла, повторите отправку файла')
    if not entry['completed']:
        if body.index != entry['next_index']:
            _drop_staged_upload(key)
            raise HTTPException(422, 'Нарушен порядок фрагментов файла')
        try:
            chunk = base64.b64decode(body.content, validate=True)
        except (ValueError, binascii.Error):
            _drop_staged_upload(key)
            raise HTTPException(422, 'Не удалось прочитать фрагмент файла') from None
        if entry['size'] + len(chunk) > MAX_UPLOAD_BYTES:
            _drop_staged_upload(key)
            raise HTTPException(413, 'Максимальный размер файла — 25 МБ')
        with entry['path'].open('ab') as handle:
            handle.write(chunk)
        entry['size'] += len(chunk)
        entry['next_index'] += 1
        entry['updated_at'] = time.monotonic()
    if not body.done:
        return {'upload_id': upload_id, 'received': entry['size']}
    if entry['size'] <= 0:
        _drop_staged_upload(key)
        raise HTTPException(422, 'Файл пустой (0 байт)')
    entry['completed'] = True
    entry['updated_at'] = time.monotonic()
    content = entry['path'].read_bytes()
    if body.target == 'task':
        try:
            return await _store_task_file(body.task_id, body.name, content)
        finally:
            _drop_staged_upload(key)
    try:
        result = await call('monocle', '/workspace/upload',
            {'path': body.path, 'content': base64.b64encode(content).decode(), 'overwrite': body.overwrite}, timeout=120)
    except HTTPException as exc:
        if exc.status_code == 409 and not body.overwrite:
            return JSONResponse(status_code=409, content={'detail': str(exc.detail), 'upload_id': upload_id})
        _drop_staged_upload(key)
        raise
    except Exception:
        _drop_staged_upload(key)
        raise
    _drop_staged_upload(key)
    return result


@router.delete('/uploads/{upload_id}')
def cancel_staged_upload(upload_id: str):
    if len(upload_id) != 32 or any(char not in '0123456789abcdef' for char in upload_id):
        raise HTTPException(422, 'Некорректная сессия загрузки')
    _drop_staged_upload((user_id(), upload_id))
    return {'ok': True}


@router.post('/tasks/{task_id}/monocle/files')
async def attach_context_file(task_id: int, body: ContextFile):
    enabled = features()
    if not (enabled['monocle'] or enabled['arrodes']):
        check_feature('monocle')
    context.get_context(task_id)
    try:
        content = base64.b64decode(body.content, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, 'Не удалось прочитать загруженный файл') from None
    if not content:
        raise HTTPException(422, 'Файл пустой (0 байт)')
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Максимальный размер файла — 25 МБ')
    return await _store_task_file(task_id, body.name, content)


@router.delete('/tasks/{task_id}/monocle/files/{file_id}')
async def delete_context_file(task_id: int, file_id: str):
    context.get_context(task_id)
    if len(file_id) != 32 or any(c not in '0123456789abcdef' for c in file_id):
        raise HTTPException(404, 'Файл не найден')
    result = await call('monocle', f'/tasks/{task_id}/files/{file_id}', method='DELETE')
    context.invalidate(task_id)
    return result


@router.get('/monocle/files/{task_id}/{file_id}')
@router.get('/tasks/{task_id}/monocle/files/{file_id}')
async def download_context_file(task_id: int, file_id: str):
    check_feature('monocle')
    # Файл может остаться в библиотеке после удаления задачи. Владение задаётся
    # пользовательским префиксом Silo и подписанным межсервисным запросом.
    if task_id <= 0:
        raise HTTPException(404, 'Файл не найден')
    if len(file_id) != 32 or any(c not in '0123456789abcdef' for c in file_id):
        raise HTTPException(404, 'Файл не найден')
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            result = await client.get(f'http://monocle:8000/tasks/{task_id}/files/{file_id}',
                headers={'X-Service-Token': internal_token('monocle'), 'X-User-ID': str(user_id())})
    except httpx.HTTPError:
        raise HTTPException(503, 'Хранилище файлов недоступно') from None
    return Response(result.content, status_code=result.status_code,
                    headers={key: result.headers[key] for key in ('content-type', 'content-disposition') if key in result.headers})


@router.api_route('/monocle/browser/sites', methods=['GET', 'POST'])
async def browser_sites(request: Request):
    body = await request.json() if request.method == 'POST' else None
    return await call('monocle', '/browser/sites', body, method=request.method)


@router.post('/monocle/browser/handoff')
async def browser_handoff(body: dict):
    from common.browser_client import browser_request
    return await browser_request('/handoff', body)


@router.post('/monocle/browser/manual')
async def profile_browser_action(body: dict):
    if scheduler.work_busy():
        raise HTTPException(409, 'Дождитесь завершения работы агентов перед ручным управлением браузером')
    return await call('monocle', '/browser/manual', body)


@router.patch('/monocle/browser/sites/{site_id}')
async def update_browser_site(site_id: str, body: dict):
    return await call('monocle', f'/browser/sites/{site_id}', body, method='PATCH')


@router.delete('/monocle/browser/sites/{site_id}')
async def delete_browser_site(site_id: str):
    if len(site_id) != 32 or any(c not in '0123456789abcdef' for c in site_id):
        raise HTTPException(404, 'Личный кабинет не найден')
    return await call('monocle', f'/browser/sites/{site_id}', method='DELETE')


@router.get('/monocle/browser/screen')
async def luna_screen(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await call('monocle', '/browser/screen', method='GET', timeout=5)


@router.get('/monocle/browser/stream')
async def luna_browser_stream(request: Request):
    if not (features()['context'] or features()['straus'] or features()['arrodes']):
        check_feature('monocle')
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=8))
    try:
        upstream = await client.send(client.build_request('GET', 'http://browser:8000/screen/stream',
            headers={'X-Service-Token': internal_token('monocle'), 'X-User-ID': str(user_id())}), stream=True)
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
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(frames(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@router.api_route('/tasks/{task_id}/monocle/browser', methods=['GET', 'PUT'])
async def task_browser(task_id: int, request: Request):
    context.get_context(task_id)
    body = await request.json() if request.method == 'PUT' else None
    result = await call('monocle', f'/tasks/{task_id}/browser', body, method=request.method)
    if request.method == 'PUT':
        context.invalidate(task_id)
    return result


@router.post('/tasks/{task_id}/monocle/browser/action')
async def browser_action(task_id: int, body: dict):
    context.get_context(task_id)
    result = await call('monocle', '/browser/manual', body)
    if body.get('action') != 'state':
        context.invalidate(task_id)
    return result
