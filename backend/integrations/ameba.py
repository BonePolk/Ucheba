import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlmodel import Session, select

from common.accounts import check_feature
from common.calendar import planning_week as read_week, read_ameba_plan, read_config as read_calendar, save_ameba_plan, view_events
from common.database import get_engine
from common.dates import normalize_utc
from common.identity import user_id
from common.models.integration import IntegrationSetting, TaskContext, TaskRanking
from common.models.task import Task
from common.rpc import call
from common.settings import read_config, read_settings, report, resolve_agent_auth
from integrations import scheduler, subtasks as subtasks_service
from integrations.context import context_status, prepare, snapshot

ranking_locks = {}
deferred_refreshes = {}


def _load_setting(session: Session, name: str, default: dict) -> dict:
    row = session.get(IntegrationSetting, name)
    if not row or not row.config:
        return dict(default)
    try:
        data = json.loads(row.config)
        return data if isinstance(data, dict) else dict(default)
    except ValueError:
        return dict(default)


def _save_setting(session: Session, name: str, data: dict) -> None:
    row = session.get(IntegrationSetting, name) or IntegrationSetting(name=name)
    row.config = json.dumps(data, ensure_ascii=False)
    session.add(row)


def read_adjustments(session: Session | None = None) -> dict:
    default = {'text': '', 'day_overrides': {}, 'task_pins': {}, 'estimate_overrides': {}, 'task_notes': {}}
    if session is not None:
        raw = _load_setting(session, 'ameba_feedback', default)
    else:
        with Session(get_engine()) as local:
            raw = _load_setting(local, 'ameba_feedback', default)
    return {
        'text': str(raw.get('text', '') or ''),
        'day_overrides': dict(raw.get('day_overrides') or {}),
        'task_pins': dict(raw.get('task_pins') or {}),
        'estimate_overrides': dict(raw.get('estimate_overrides') or {}),
        'task_notes': dict(raw.get('task_notes') or {}),
    }


def save_adjustments(data: dict, session: Session | None = None) -> dict:
    clean = {
        'text': str(data.get('text', '') or '').strip(),
        'day_overrides': {k: v for k, v in (data.get('day_overrides') or {}).items() if v in ('normal', 'light', 'free', 'focused')},
        'task_pins': dict(data.get('task_pins') or {}),
        'estimate_overrides': {str(k): max(0, int(v)) for k, v in (data.get('estimate_overrides') or {}).items() if v is not None},
        'task_notes': {str(k): str(v).strip()[:400] for k, v in (data.get('task_notes') or {}).items() if str(v or '').strip()},
    }
    if session is not None:
        _save_setting(session, 'ameba_feedback', clean)
    else:
        with Session(get_engine()) as local:
            _save_setting(local, 'ameba_feedback', clean)
            local.commit()
    return clean


def plan_feedback():
    return read_adjustments()['text']


def save_plan_feedback(text):
    current = read_adjustments()
    current['text'] = (text or '').strip()
    save_adjustments(current)


def _collect_task_state(session):
    tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
    subtasks_map = subtasks_service.list_for_tasks(session, [task.id for task in tasks])
    task_notes = read_adjustments(session).get('task_notes', {})
    ready, waiting, state = [], [], []
    for task in tasks:
        context = session.get(TaskContext, task.id)
        usage = json.loads(context.usage) if context else {}
        valid = context_status(task, context) == 'ready'
        task_subs = subtasks_service.serialize_for_agent(subtasks_map.get(task.id, []))
        task_note = task_notes.get(str(task.id), '')
        state.append({
            'task': snapshot(task),
            'id': task.id,
            'context': {
                'subject': context.subject, 'notes': context.owner_notes, 'status': context.status,
                'questions': context.questions, 'full_context': usage.get('full_markdown'),
                'time_estimate': usage.get('time_estimate'), 'effort': usage.get('effort'),
                'impact': usage.get('impact'),
            } if context else None,
            'ready': valid,
            'completed_work_minutes': task.completed_work_minutes,
            'task_note': task_note,
            'subtasks': [
                {'title': s['title'], 'done': s['done'], 'estimated_minutes': s['estimated_minutes']}
                for s in task_subs
            ],
        })
        if valid:
            ready.append({
                'id': task.id, 'title': task['title'] if isinstance(task, dict) else task.title,
                'description': task.description or '',
                'category': task.category, 'subject': context.subject, 'owner_notes': context.owner_notes,
                'task_note': task_note,
                'sources': usage.get('sources', []),
                'full_context': usage['full_markdown'],
                'time_estimate': usage.get('time_estimate'),
                'effort': usage.get('effort'), 'impact': usage.get('impact'),
                'deadline': normalize_utc(task.deadline), 'priority': task.priority,
                'completed_work_minutes': task.completed_work_minutes,
                'subtasks': task_subs,
            })
        else:
            waiting.append(task.id)
    ready.sort(key=lambda task: ({'high': 0, 'medium': 1, 'low': 2}.get(task['priority'], 1),
                                 task['deadline'].isoformat() if task['deadline'] else '9999', task['id']))
    return state, ready, waiting


def _commit_persistent_preferences(items: list[dict]) -> int:
    """Сохраняет подтверждённые устойчивые привычки в общую память после commit плана."""
    if not items:
        return 0
    from agent_memory import read as read_facts, save as save_fact

    existing = read_facts()
    owner_keys = {f.get('key') for f in existing if f.get('agent') in ('Владелец', 'user')}
    known_texts = {(f.get('text') or '').strip().casefold() for f in existing}
    saved_count = 0
    for item in items:
        text = str(item.get('text') or '').strip()
        if not text:
            continue
        norm = text.casefold()
        if norm in known_texts:
            continue
        raw_key = str(item.get('key') or '').strip()
        if not raw_key or raw_key in owner_keys or not all(c.isalnum() or c in '_.-' for c in raw_key):
            raw_key = f'ameba.pref_{uuid4().hex[:8]}'
        elif not raw_key.startswith('ameba.'):
            raw_key = f'ameba.{raw_key}'[:100]
        try:
            save_fact({'key': raw_key, 'text': text}, 'Ameba')
        except HTTPException as exc:
            if exc.status_code == 422:
                continue
            raise
        known_texts.add(norm)
        saved_count += 1
    return saved_count


def _build_signature(session, state, include_ameba_facts: bool = True) -> str:
    from agent_memory import read as read_facts
    calendar = read_calendar()
    adjustments = read_adjustments(session)
    ameba_cfg = read_config('ameba')
    provider_base = read_settings('ai_provider', apply_feature=False)[0].get('base_url')
    allowed_agents = ('Владелец', 'user', 'Ameba') if include_ameba_facts else ('Владелец', 'user')
    return json.dumps({
        'version': 46,
        'tasks': state,
        'calendar': {
            'revision': calendar.get('revision', ''),
            'events': sorted(calendar['events'], key=lambda event: event['id']),
            'timezone': calendar['timezone'],
        },
        'facts': sorted(({'key': fact['key'], 'text': fact['text']} for fact in read_facts() if fact['agent'] in allowed_agents), key=lambda fact: fact['key']),
        'feedback': adjustments,
        'provider': {
            'base_url': provider_base,
            **{
                key: ameba_cfg.get(key)
                for key in ('auth_mode', 'model', 'reasoning_effort', 'planning_style', 'allow_context_recheck', 'custom_instructions')
            },
        },
    }, sort_keys=True)


def task_state(session):
    state, ready, waiting = _collect_task_state(session)
    return _build_signature(session, state, include_ameba_facts=True), ready, waiting


def _external_signature(session) -> str:
    state, _, _ = _collect_task_state(session)
    return _build_signature(session, state, include_ameba_facts=False)


def automatic_inputs():
    from agent_memory import read as read_facts
    with Session(get_engine()) as session:
        tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
        inputs = []
        for task in tasks:
            context = session.get(TaskContext, task.id)
            inputs.append({
                'id': task.id,
                'task': snapshot(task),
                'notes': context.owner_notes if context else '',
                'completed_work_minutes': task.completed_work_minutes,
            })
    calendar = read_calendar()
    return json.dumps({
        'tasks': inputs,
        'calendar': {'events': sorted(calendar['events'], key=lambda event: event['id']), 'timezone': calendar['timezone']},
        'facts': sorted(({'key': fact['key'], 'text': fact['text']} for fact in read_facts()
                         if fact['agent'] in ('Владелец', 'user')), key=lambda fact: fact['key']),
    }, sort_keys=True)


def calendar_schedule():
    config = read_calendar()
    today = datetime.now(ZoneInfo(config['timezone'])).date()
    intervals_by_day = {(today + timedelta(days=offset)).isoformat(): [] for offset in range(7)}
    for ev in view_events(today, today + timedelta(days=6)):
        if ev['kind'] not in ('busy', 'task') or ev['date'] not in intervals_by_day:
            continue
        sh, sm = map(int, ev['start'].split(':'))
        eh, em = map(int, ev['end'].split(':'))
        if eh * 60 + em > sh * 60 + sm:
            intervals_by_day[ev['date']].append((sh * 60 + sm, eh * 60 + em))
    days_summary = []
    for date_iso, spans in intervals_by_day.items():
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        days_summary.append({'date': date_iso, 'busy_minutes': sum(b - a for a, b in merged)})
    return {'version': 3, 'timezone': config['timezone'], 'days_summary': days_summary}


def plan_expired(result):
    schedule = result.get('schedule') or {}
    if schedule.get('version') != 3 or not schedule.get('generated_date'):
        return True
    zone = ZoneInfo(read_calendar()['timezone'])
    today_iso = datetime.now(zone).date().isoformat()
    return schedule['generated_date'] != today_iso


def ranking_status():
    with Session(get_engine()) as session:
        signature, ready, waiting = task_state(session)
        saved = session.get(TaskRanking, 1)
        result = json.loads(saved.result) if saved else {}
        ready_ids = {task['id'] for task in ready}
        ids = [task_id for task_id in result.get('task_ids', []) if task_id in ready_ids]
        plan_reset = bool(saved) and read_ameba_plan().get('run_id') != result.get('plan_run_id')
        adjustments = read_adjustments(session)
        return result | {
            'enabled': bool(read_config('ameba').get('enabled')),
            'plan_reset': plan_reset,
            'has_plan': bool(result.get('schedule') and result['schedule'].get('version') in (2, 3)),
            'task_ids': ids,
            'ranked_count': len(ids),
            'reasons': [item for item in result.get('reasons', []) if item['task_id'] in ids],
            'pending_count': len(waiting),
            'total_count': len(ready) + len(waiting),
            'feedback': adjustments['text'],
            'manual_adjustments': adjustments,
            'needs_refresh': not saved or saved.snapshot != signature or plan_expired(result),
            'uncertain_comparisons': result.get('uncertain_comparisons', 0),
            'calendar_schedule': calendar_schedule(),
        }


async def clear_plan():
    lock = ranking_locks.setdefault(user_id(), asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, 'Дождитесь завершения текущего планирования Ameba')
    async with lock:
        with Session(get_engine()) as session:
            saved = session.get(TaskRanking, 1) or TaskRanking()
            saved.result = '{}'
            saved.snapshot = ''
            session.add(saved)
            temporary = session.get(IntegrationSetting, 'ameba_plan') or IntegrationSetting(name='ameba_plan')
            temporary.config = '{}'
            session.add(temporary)
            adj = read_adjustments(session)
            adj['text'] = ''
            adj['day_overrides'] = {}
            adj['task_pins'] = {}
            adj['estimate_overrides'] = {}
            adj['task_notes'] = {}
            save_adjustments(adj, session=session)
            session.commit()
    return {'ok': True}


async def request_ranking(tasks, task_id=0, checked_contexts=(), refresh=False):
    config, api_key = resolve_agent_auth('ameba', require_enabled=True, require_credentials=not refresh)
    with Session(get_engine()) as session:
        input_signature = task_state(session)[0]
        saved = session.get(TaskRanking, 1)
        previous = json.loads(saved.result) if saved else {}
        adjustments = read_adjustments(session)
    return await scheduler.run_ranking({
        'input_signature': input_signature,
        'api_key': api_key,
        'config': config,
        'tasks': tasks,
        'now': datetime.now(timezone.utc).isoformat(),
        'weekly_schedule': read_week(),
        'timezone': read_calendar()['timezone'],
        'task_id': task_id,
        'previous_plan': previous,
        'user_feedback': adjustments['text'],
        'manual_adjustments': adjustments,
        'checked_contexts': list(checked_contexts),
    }, endpoint='/refresh' if refresh else '/rank')


def _can_refresh_only(saved, ready_ids: set[int]) -> bool:
    if not saved or not saved.result:
        return False
    prev = json.loads(saved.result)
    sched = prev.get('schedule') or {}
    if sched.get('version') != 3:
        return False
    prev_ids = {item.get('id') for item in prev.get('timeline', []) if item.get('plan')}
    return bool(prev_ids) and ready_ids <= prev_ids


async def refresh_saved_plan():
    """Детерминированно пересчитывает сохранённый план периодов без LLM."""
    if not read_config('ameba')['enabled']:
        return None
    async with ranking_locks.setdefault(user_id(), asyncio.Lock()):
        with Session(get_engine()) as session:
            signature, ready, waiting = task_state(session)
            saved = session.get(TaskRanking, 1)
            if not _can_refresh_only(saved, {t['id'] for t in ready}):
                return None
        try:
            async with scheduler.slot('ameba', 0):
                with Session(get_engine()) as session:
                    signature, ready, waiting = task_state(session)
                    pre_calendar_revision = read_calendar().get('revision')
                    saved = session.get(TaskRanking, 1)
                    if not _can_refresh_only(saved, {t['id'] for t in ready}):
                        return None
                result = await request_ranking(ready, refresh=True)
        except HTTPException as exc:
            if exc.status_code == 409:
                return None
            raise
        schedule = result.get('schedule')
        if not schedule or schedule.get('timeline_error'):
            return None
        ids = result.get('task_ids', [])
        result.update(
            generated_at=datetime.now(timezone.utc).isoformat(),
            ranked_count=len(ids),
            pending_count=len(waiting),
            total_count=len(ready) + len(waiting),
        )
        with Session(get_engine()) as session:
            if task_state(session)[0] != signature or read_calendar().get('revision') != pre_calendar_revision:
                return None
            saved = session.get(TaskRanking, 1) or TaskRanking()
            if not read_adjustments(session)['text']:
                saved.snapshot = task_state(session)[0]
            saved.result = json.dumps(result, ensure_ascii=False)
            session.add(saved)
            if result.get('plan_run_id'):
                save_ameba_plan(result['plan_run_id'], result, result['calendar_revision'], session)
            session.commit()
        return result


async def _run_deferred_refresh(owner: int):
    try:
        await refresh_saved_plan()
    except HTTPException as exc:
        if exc.status_code != 409:
            logging.getLogger('uvicorn.error').warning('Ameba deferred refresh failed for user %s: %s', owner, exc.detail)
    except Exception as exc:
        logging.getLogger('uvicorn.error').warning('Ameba deferred refresh error for user %s: %s', owner, type(exc).__name__)


async def _trigger_refresh():
    owner = user_id()
    if scheduler.work_busy(owner):
        existing = deferred_refreshes.get(owner)
        if existing is None or existing.done():
            task = asyncio.create_task(_run_deferred_refresh(owner))
            deferred_refreshes[owner] = task
            task.add_done_callback(lambda done, key=owner: deferred_refreshes.pop(key, None) if deferred_refreshes.get(key) is done else None)
        return 'deferred'
    return 'refreshed' if (await refresh_saved_plan()) is not None else 'needs_replan'


async def submit_feedback(task_id: int, outcome: str, actual_minutes: int | None = None, period_id: str | None = None):
    check_feature('ameba')
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        if outcome == 'done':
            task.status = 'done'
            if actual_minutes is not None and actual_minutes > 0:
                task.completed_work_minutes = max(task.completed_work_minutes, int(actual_minutes))
        elif outcome == 'partial':
            if task.status == 'todo':
                ctx = session.get(TaskContext, task.id)
                was_valid = ctx is not None and context_status(task, ctx) == 'ready'
                task.status = 'in_progress'
                if was_valid:
                    ctx.task_snapshot = snapshot(task)
                    session.add(ctx)
            if actual_minutes is not None and actual_minutes > 0:
                task.completed_work_minutes += int(actual_minutes)
        elif outcome in ('missed', 'too_hard') and actual_minutes is not None and actual_minutes > 0:
            task.completed_work_minutes += int(actual_minutes)
        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
    await _trigger_refresh()
    return ranking_status() | {'feedback_saved': True}


async def adjust_plan(
    day_overrides: dict | None = None,
    task_pin: dict | None = None,
    estimate_override: dict | None = None,
    reset: bool = False,
    clear_feedback: bool = False,
):
    check_feature('ameba')
    has_schedule_changes = bool(day_overrides or task_pin or estimate_override or reset)
    with Session(get_engine()) as session:
        adj = read_adjustments(session)
        if reset:
            adj['day_overrides'] = {}
            adj['task_pins'] = {}
            adj['estimate_overrides'] = {}
        if clear_feedback:
            adj['text'] = ''
        if day_overrides:
            for date_iso, mode in day_overrides.items():
                if mode is None or mode == 'auto':
                    adj['day_overrides'].pop(date_iso, None)
                else:
                    adj['day_overrides'][date_iso] = mode
        if task_pin:
            tid = str(task_pin['task_id'])
            if task_pin.get('clear'):
                adj['task_pins'].pop(tid, None)
            elif task_pin.get('target_date'):
                adj['task_pins'][tid] = {'mode': task_pin.get('mode') or 'date', 'target_date': task_pin['target_date'], 'not_before': task_pin.get('not_before')}
            elif task_pin.get('shift_days'):
                existing_pin = adj['task_pins'].get(tid) or {}
                saved = session.get(TaskRanking, 1)
                res = json.loads(saved.result) if saved and saved.result else {}
                periods = (res.get('schedule') or {}).get('periods', [])
                cur_date = next(
                    (
                        p['start_date']
                        for p in periods
                        if p.get('kind', 'work') == 'work'
                        and (p.get('task_id') == int(tid) or any(t.get('task_id') == int(tid) for t in p.get('tasks', [])))
                    ),
                    None,
                )
                today_local = datetime.now(ZoneInfo(read_calendar()['timezone'])).date()
                base_iso = cur_date or existing_pin.get('target_date')
                base_date = datetime.strptime(base_iso, '%Y-%m-%d').date() if base_iso else today_local
                shift = int(task_pin['shift_days'])
                target = max(today_local, base_date + timedelta(days=shift))
                adj['task_pins'][tid] = {'mode': 'shift', 'target_date': target.isoformat(), 'not_before': target.isoformat() if shift > 0 else None}
        if estimate_override:
            tid = str(estimate_override['task_id'])
            mins = estimate_override.get('remaining_minutes')
            if mins is None:
                adj['estimate_overrides'].pop(tid, None)
            else:
                adj['estimate_overrides'][tid] = max(0, int(mins))
        save_adjustments(adj, session=session)
        session.commit()
    refresh_status = 'refreshed'
    if has_schedule_changes:
        refresh_status = await _trigger_refresh()
    status = ranking_status()
    if has_schedule_changes:
        status['refreshed'] = refresh_status == 'refreshed'
        status['deferred'] = refresh_status == 'deferred'
        status['needs_replan'] = refresh_status != 'refreshed'
    return status


async def _prepare_parallel(task_ids, **kwargs):
    if not task_ids:
        return [], None
    results = await asyncio.gather(
        *(prepare(tid, **kwargs) for tid in task_ids),
        return_exceptions=True,
    )
    succeeded = []
    first_error = None
    for tid, item in zip(task_ids, results):
        if isinstance(item, BaseException):
            if not isinstance(item, Exception):
                raise item
            if first_error is None:
                first_error = item
        else:
            succeeded.append(tid)
    if first_error is not None:
        if isinstance(first_error, HTTPException):
            report('monocle', error=str(first_error.detail))
        return succeeded, first_error
    return succeeded, None


async def rank_all(force=False, task_id=0):
    check_feature('ameba')
    if not read_config('ameba')['enabled']:
        raise HTTPException(400, 'Включите сортировку Ameba в настройках')
    async with ranking_locks.setdefault(user_id(), asyncio.Lock()):
        with Session(get_engine()) as session:
            signature = task_state(session)[0]
            saved = session.get(TaskRanking, 1)
            if not force and saved and saved.snapshot == signature and not plan_expired(json.loads(saved.result)):
                return json.loads(saved.result) | {'unchanged': True}
        with Session(get_engine()) as session:
            tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done')).all()
            pending = [task.id for task in tasks
                       if context_status(task, session.get(TaskContext, task.id)) == 'new']
        async with scheduler.batch(pending):
            if pending:
                _, error = await _prepare_parallel(pending)
                if error is not None:
                    raise error
        with Session(get_engine()) as session:
            signature, ready, waiting = task_state(session)
            pre_external = _external_signature(session)
            pre_calendar_revision = read_calendar().get('revision')
            saved = session.get(TaskRanking, 1)
            if not force and saved and saved.snapshot == signature and not plan_expired(json.loads(saved.result)):
                return json.loads(saved.result)
        checked_contexts = set()
        result = None
        while ready:
            async with scheduler.slot('ameba', task_id):
                with Session(get_engine()) as session:
                    signature, ready, waiting = task_state(session)
                    if not ready:
                        result = None
                        break
                    pre_external = _external_signature(session)
                    pre_calendar_revision = read_calendar().get('revision')
                    saved = session.get(TaskRanking, 1)
                    refresh = not force and bool(saved and saved.snapshot == signature and _can_refresh_only(saved, {t['id'] for t in ready}))
                result = await request_ranking(ready, task_id, checked_contexts, refresh=refresh)
            requests = result.get('context_requests', [])
            if not requests:
                break
            ready_ids = {task['id'] for task in ready}
            if any(value not in ready_ids or value in checked_contexts for value in requests):
                raise HTTPException(502, 'Ameba запросила некорректную перепроверку контекста')
            async with scheduler.batch(requests):
                succeeded, error = await _prepare_parallel(
                    requests,
                    force=True,
                    review_request=result.get('context_reason', ''),
                )
                checked_contexts.update(succeeded)
                if error is not None:
                    raise error
            with Session(get_engine()) as session:
                signature, ready, waiting = task_state(session)
                pre_external = _external_signature(session)
                pre_calendar_revision = read_calendar().get('revision')
                if not ready:
                    result = None
        if result is None:
            result = {
                'task_ids': [], 'comparisons': 0, 'uncertain_comparisons': 0,
                'explanation': 'Нет задач с готовым контекстом.', 'reasons': [],
                'schedule': {'version': 3, 'valid': True, 'periods': [], 'daily_allocations': [], 'deadline_conflicts': [],
                             'deadline_risks': [], 'remaining': [], 'feasible': True, 'no_objective_conflicts': True,
                             'feasibility_status': 'no_objective_conflicts', 'fully_scheduled': True, 'remaining_minutes': 0,
                             'min_minutes': 0, 'max_minutes': 0, 'hours_label': 'выполнено',
                             'generated_date': datetime.now(ZoneInfo(read_calendar()['timezone'])).date().isoformat(),
                             'start_at': datetime.now(timezone.utc).isoformat(), 'timeline': [], 'timeline_error': None},
            }
        ids = result.get('task_ids', [])
        if len(ids) != len(ready) or set(ids) != {task['id'] for task in ready}:
            raise HTTPException(502, 'Ameba вернула неполный порядок задач')
        schedule = result.get('schedule')
        if ready and (not schedule or schedule.get('timeline_error') or not schedule.get('start_at') or schedule.get('version') != 3):
            raise HTTPException(502, 'Ameba не построила корректный план периодов. Предыдущий результат сохранён.')
        task_preferences = result.pop('task_preferences', []) or []
        persistent_preferences = result.pop('persistent_preferences', []) or []
        result.pop('progress_updates', None)
        confirmed_progress = result.pop('confirmed_progress', []) or []
        result.pop('memory_saved_count', None)
        result['task_ids'] = ids
        result.update(generated_at=datetime.now(timezone.utc).isoformat(), ranked_count=len(ids), pending_count=len(waiting), total_count=len(ready) + len(waiting))
        consumed_feedback = False
        task_notes_saved_count = 0
        progress_updated_count = 0
        final_signature = signature
        with Session(get_engine()) as session:
            if (
                task_state(session)[0] != signature
                or _external_signature(session) != pre_external
                or read_calendar().get('revision') != pre_calendar_revision
            ):
                raise HTTPException(409, 'Задачи, прогресс или календарь изменились. Повторите планирование')
            adj = read_adjustments(session)
            active_ids_str = {str(t.id) for t in session.exec(select(Task).where(Task.archived == False, Task.status != 'done')).all()}
            adj['task_notes'] = {k: v for k, v in adj['task_notes'].items() if k in active_ids_str}
            adj['task_pins'] = {k: v for k, v in adj['task_pins'].items() if k in active_ids_str}
            adj['estimate_overrides'] = {k: v for k, v in adj['estimate_overrides'].items() if k in active_ids_str}
            ready_id_set = set(ids)
            for item in task_preferences:
                tid = item.get('task_id')
                text = str(item.get('text') or '').strip()[:400]
                if tid in ready_id_set and text:
                    adj['task_notes'][str(tid)] = text
                    task_notes_saved_count += 1
            for entry in confirmed_progress:
                if not isinstance(entry, dict) or entry.get('confirmed_by_user') is not True:
                    continue
                tid = entry.get('task_id')
                try:
                    add_mins, base_mins = int(entry.get('add_minutes')), int(entry.get('base_completed_minutes'))
                except (TypeError, ValueError):
                    raise HTTPException(409, 'Некорректное обновление прогресса задачи. Повторите планирование') from None
                if tid not in ready_id_set or add_mins <= 0 or add_mins > 1440 or base_mins < 0:
                    raise HTTPException(409, 'Некорректное обновление прогресса задачи. Повторите планирование')
                task_row = session.get(Task, tid)
                if not task_row or task_row.archived or task_row.status == 'done' or task_row.completed_work_minutes != base_mins:
                    raise HTTPException(409, 'Прогресс задачи изменился во время расчёта. Повторите планирование')
                task_row.completed_work_minutes += add_mins
                if task_row.status == 'todo':
                    ctx = session.get(TaskContext, tid)
                    was_valid = ctx is not None and context_status(task_row, ctx) == 'ready'
                    task_row.status = 'in_progress'
                    if was_valid:
                        ctx.task_snapshot = snapshot(task_row)
                        session.add(ctx)
                task_row.updated_at = datetime.now(timezone.utc)
                session.add(task_row)
                progress_updated_count += 1
            if adj['text']:
                consumed_feedback = True
                adj['text'] = ''
            if consumed_feedback or task_notes_saved_count or progress_updated_count:
                save_adjustments(adj, session=session)
                session.flush()
            saved = session.get(TaskRanking, 1) or TaskRanking()
            final_signature = task_state(session)[0] if (consumed_feedback or task_notes_saved_count or progress_updated_count or task_state(session)[0] != signature) else signature
            saved.snapshot = final_signature
            saved.result = json.dumps(result, ensure_ascii=False)
            session.add(saved)
            for tid in ids:
                task = session.get(Task, tid)
                task.ai_details = json.dumps({'version': 46, 'status': 'ranked'})
                task.ai_scored_at = datetime.now(timezone.utc)
                session.add(task)
            if result.get('plan_run_id'):
                save_ameba_plan(result['plan_run_id'], result, result['calendar_revision'], session)
            elif not ids:
                temporary = session.get(IntegrationSetting, 'ameba_plan')
                if temporary:
                    temporary.config = '{}'
                    session.add(temporary)
            session.commit()
        memory_saved_count = _commit_persistent_preferences(persistent_preferences)
        if memory_saved_count > 0:
            with Session(get_engine()) as session:
                saved = session.get(TaskRanking, 1)
                if saved and saved.snapshot == final_signature:
                    saved.snapshot = task_state(session)[0]
                    session.add(saved)
                    session.commit()
        report('ameba', success=True)
        extras = {
            'memory_saved_count': memory_saved_count,
            'task_notes_saved_count': task_notes_saved_count,
            'progress_updated_count': progress_updated_count,
        }
        return result | extras | ({'consumed_feedback': True} if consumed_feedback else {})


async def evaluate(task_id=None):
    if task_id is None:
        async with scheduler.slot('ameba'):
            return await check_model()
    return await evaluate_task(task_id)


async def check_model():
    config, api_key = resolve_agent_auth('ameba')
    return await call('ameba', '/check', {'config': config, 'api_key': api_key}, timeout=120)


async def evaluate_task(task_id):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        original = snapshot(task)
    context = await prepare(task_id)
    original = context['task_snapshot']
    if context['status'] != 'ready':
        metrics = {'version': 46, 'status': 'needs_context', 'reason': ' '.join(context['questions'])}
        with Session(get_engine()) as session:
            task = session.get(Task, task_id)
            if not task:
                raise HTTPException(404, 'Задача не найдена')
            if snapshot(task) != original:
                raise HTTPException(409, 'Задача изменилась. Повторите проверку')
            task.ai_details = json.dumps(metrics)
            task.ai_scored_at = datetime.now(timezone.utc)
            session.add(task)
            session.commit()
        return {'rank': None, 'metrics': metrics}
    result = await rank_all(task_id=task_id)
    ids = result['task_ids']
    return result | {'rank': ids.index(task_id) + 1 if task_id in ids else None,
                     'metrics': {'status': 'ranked', 'version': 46}}
