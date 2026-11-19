import asyncio
from datetime import datetime, timedelta, timezone
import json
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
from integrations import scheduler
from integrations.context import context_status, prepare, snapshot

ranking_locks = {}
MARKER_WORDS = ('дедлайн', 'срок', 'сдача', 'сдать', 'напоминан', 'открыт', 'deadline', 'due', 'reminder')


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
    default = {'text': '', 'day_overrides': {}, 'task_pins': {}, 'estimate_overrides': {}}
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
    }


def save_adjustments(data: dict, session: Session | None = None) -> dict:
    clean = {
        'text': str(data.get('text', '') or '').strip(),
        'day_overrides': {k: v for k, v in (data.get('day_overrides') or {}).items() if v in ('normal', 'light', 'free', 'focused')},
        'task_pins': dict(data.get('task_pins') or {}),
        'estimate_overrides': {str(k): max(0, int(v)) for k, v in (data.get('estimate_overrides') or {}).items() if v is not None},
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


def read_profile(session: Session | None = None) -> dict:
    default = {'preferences': {}, 'observations': []}
    if session is not None:
        raw = _load_setting(session, 'ameba_profile', default)
    else:
        with Session(get_engine()) as local:
            raw = _load_setting(local, 'ameba_profile', default)
    return {
        'preferences': dict(raw.get('preferences') or {}),
        'observations': list(raw.get('observations') or [])[-100:],
    }


def _save_profile(session: Session, profile: dict) -> None:
    clean = {
        'preferences': dict(profile.get('preferences') or {}),
        'observations': list(profile.get('observations') or [])[-100:],
    }
    _save_setting(session, 'ameba_profile', clean)


def _day_busy_minutes(day_iso: str) -> int:
    day_obj = datetime.strptime(day_iso, '%Y-%m-%d').date()
    intervals = []
    for ev in view_events(day_obj, day_obj):
        if ev.get('kind') not in ('busy', 'task'):
            continue
        sh, sm = map(int, ev['start'].split(':'))
        eh, em = map(int, ev['end'].split(':'))
        dur = (eh * 60 + em) - (sh * 60 + sm)
        text = f"{ev.get('title', '')} {ev.get('summary', '')}".casefold()
        if (dur >= 1080 or (ev.get('kind') == 'task' and dur <= 30)) and any(w in text for w in MARKER_WORDS):
            continue
        intervals.append((sh * 60 + sm, eh * 60 + em))
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return sum(b - a for a, b in merged)


def record_observation(
    session: Session,
    task: Task,
    outcome: str,
    actual_minutes: int | None = None,
    period_id: str | None = None,
) -> None:
    saved = session.get(TaskRanking, 1)
    result = json.loads(saved.result) if saved and saved.result else {}
    schedule = result.get('schedule') or {}
    zone = ZoneInfo(read_calendar()['timezone'])
    today = datetime.now(zone).date()

    plan = next((item.get('plan') for item in result.get('timeline', []) if item.get('id') == task.id), None)
    planned_minutes = int(plan.get('planned_work_minutes', plan.get('work_max_minutes', 0)) or 0) if plan else 0
    effort = (plan or {}).get('effort') or 'medium'

    target_date = today
    if period_id:
        for p in schedule.get('periods', []):
            if p.get('id') == period_id:
                t_item = next((t for t in p.get('tasks', []) if t.get('task_id') == task.id), None)
                if t_item and outcome in ('partial', 'missed', 'too_hard') and t_item.get('minutes'):
                    planned_minutes = int(t_item['minutes'])
                if p.get('start_date'):
                    target_date = datetime.strptime(p['start_date'], '%Y-%m-%d').date()
                break

    busy_minutes = _day_busy_minutes(target_date.isoformat())
    profile = read_profile(session)
    profile['observations'].append({
        'task_id': task.id,
        'category': task.category or '',
        'effort': effort,
        'outcome': outcome,
        'planned_minutes': planned_minutes,
        'actual_minutes': int(actual_minutes) if actual_minutes is not None else None,
        'busy_day': busy_minutes >= 240,
        'weekday': target_date.weekday(),
        'recorded_at': datetime.now(timezone.utc).isoformat(),
    })
    _save_profile(session, profile)


def record_task_completion(session: Session, task: Task) -> None:
    if task.completed_work_minutes <= 0:
        return
    saved = session.get(TaskRanking, 1)
    if not saved or not saved.result:
        return
    result = json.loads(saved.result)
    if not any(item.get('id') == task.id and item.get('plan') for item in result.get('timeline', [])):
        return
    record_observation(session, task, 'done', actual_minutes=task.completed_work_minutes)


def _collect_task_state(session):
    tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
    ready, waiting, state = [], [], []
    for task in tasks:
        context = session.get(TaskContext, task.id)
        usage = json.loads(context.usage) if context else {}
        valid = context_status(task, context) == 'ready'
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
        })
        if valid:
            ready.append({
                'id': task.id, 'title': task.title, 'description': task.description or '',
                'category': task.category, 'subject': context.subject, 'owner_notes': context.owner_notes,
                'sources': usage.get('sources', []),
                'full_context': usage['full_markdown'],
                'time_estimate': usage.get('time_estimate'),
                'effort': usage.get('effort'), 'impact': usage.get('impact'),
                'deadline': normalize_utc(task.deadline), 'priority': task.priority,
                'completed_work_minutes': task.completed_work_minutes,
            })
        else:
            waiting.append(task.id)
    ready.sort(key=lambda task: ({'high': 0, 'medium': 1, 'low': 2}.get(task['priority'], 1),
                                 task['deadline'].isoformat() if task['deadline'] else '9999', task['id']))
    return state, ready, waiting


def _build_signature(session, state, include_ameba_facts: bool = True) -> str:
    from agent_memory import read as read_facts
    calendar = read_calendar()
    adjustments = read_adjustments(session)
    profile = read_profile(session)
    ameba_cfg = read_config('ameba')
    provider_base = read_settings('ai_provider', apply_feature=False)[0].get('base_url')
    allowed_agents = ('Владелец', 'user', 'Ameba') if include_ameba_facts else ('Владелец', 'user')
    return json.dumps({
        'version': 41,
        'tasks': state,
        'calendar': {'events': sorted(calendar['events'], key=lambda event: event['id']), 'timezone': calendar['timezone']},
        'facts': sorted(({'key': fact['key'], 'text': fact['text']} for fact in read_facts() if fact['agent'] in allowed_agents), key=lambda fact: fact['key']),
        'feedback': adjustments,
        'preferences': profile.get('preferences', {}),
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
    zone = ZoneInfo(config['timezone'])
    today = datetime.now(zone).date()
    events = view_events(today, today + timedelta(days=6))
    by_day = {
        (today + timedelta(days=offset)).isoformat(): {
            'date': (today + timedelta(days=offset)).isoformat(),
            'weekday': (today + timedelta(days=offset)).weekday(),
            'busy_minutes': 0,
            'commitments': [],
            'markers': [],
        }
        for offset in range(7)
    }
    for event in events:
        entry = by_day.get(event['date'])
        if not entry or event['kind'] == 'work':
            continue
        sh, sm = map(int, event['start'].split(':'))
        eh, em = map(int, event['end'].split(':'))
        dur = max(0, (eh * 60 + em) - (sh * 60 + sm))
        text = f"{event.get('title', '')} {event.get('summary', '')}".casefold()
        all_day = dur >= 1080 or (sh == 0 and sm <= 5 and eh >= 23 and em >= 55)
        if (all_day or (event['kind'] == 'task' and dur <= 30)) and any(w in text for w in MARKER_WORDS):
            entry['markers'].append({'title': event['title'], 'kind': 'deadline', 'all_day': all_day})
        elif event['kind'] in ('busy', 'task'):
            entry['commitments'].append({'id': event['id'], 'title': event['title'], 'start': event['start'], 'end': event['end'], 'kind': event['kind']})
    for date_iso, entry in by_day.items():
        entry['busy_minutes'] = _day_busy_minutes(date_iso)
    return {
        'version': 2,
        'start_at': datetime.now(timezone.utc).isoformat(),
        'timezone': config['timezone'],
        'days_summary': list(by_day.values()),
        'rest_assumption': 'Постоянное расписание. Ameba учитывает его при оценке рабочей ёмкости дней.',
    }


def plan_expired(result):
    schedule = result.get('schedule') or {}
    if schedule.get('version') != 2 or not schedule.get('generated_date'):
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
        profile = read_profile(session)
        return result | {
            'enabled': bool(read_config('ameba').get('enabled')),
            'plan_reset': plan_reset,
            'has_plan': bool(result.get('schedule') and result['schedule'].get('version') == 2),
            'task_ids': ids,
            'ranked_count': len(ids),
            'reasons': [item for item in result.get('reasons', []) if item['task_id'] in ids],
            'pending_count': len(waiting),
            'total_count': len(ready) + len(waiting),
            'feedback': adjustments['text'],
            'manual_adjustments': adjustments,
            'observations_count': len(profile['observations']),
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
        profile = read_profile(session)
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
        'adaptation_profile': profile,
        'checked_contexts': list(checked_contexts),
    }, endpoint='/refresh' if refresh else '/rank')


def _can_refresh_only(saved, ready_ids: set[int]) -> bool:
    if not saved or not saved.result:
        return False
    prev = json.loads(saved.result)
    sched = prev.get('schedule') or {}
    if sched.get('version') != 2:
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
            saved = session.get(TaskRanking, 1) or TaskRanking()
            if not read_adjustments(session)['text']:
                saved.snapshot = task_state(session)[0]
            saved.result = json.dumps(result, ensure_ascii=False)
            session.add(saved)
            if result.get('plan_run_id'):
                save_ameba_plan(result['plan_run_id'], result, result['calendar_revision'], session)
            session.commit()
        return result


async def submit_feedback(task_id: int, outcome: str, actual_minutes: int | None = None, period_id: str | None = None):
    check_feature('ameba')
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        record_observation(session, task, outcome, actual_minutes=actual_minutes, period_id=period_id)
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
    await refresh_saved_plan()
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
                adj['task_pins'][tid] = {'target_date': task_pin['target_date'], 'not_before': task_pin.get('not_before')}
            elif task_pin.get('shift_days'):
                saved = session.get(TaskRanking, 1)
                res = json.loads(saved.result) if saved and saved.result else {}
                periods = (res.get('schedule') or {}).get('periods', [])
                cur_date = next((p['start_date'] for p in periods if any(t['task_id'] == int(tid) for t in p.get('tasks', []))), None)
                base_date = datetime.strptime(cur_date, '%Y-%m-%d').date() if cur_date else datetime.now(ZoneInfo(read_calendar()['timezone'])).date()
                target = max(datetime.now(ZoneInfo(read_calendar()['timezone'])).date(), base_date + timedelta(days=int(task_pin['shift_days'])))
                adj['task_pins'][tid] = {'target_date': target.isoformat(), 'not_before': target.isoformat() if int(task_pin['shift_days']) > 0 else None}
        if estimate_override:
            tid = str(estimate_override['task_id'])
            mins = estimate_override.get('remaining_minutes')
            if mins is None:
                adj['estimate_overrides'].pop(tid, None)
            else:
                adj['estimate_overrides'][tid] = max(0, int(mins))
        save_adjustments(adj, session=session)
        session.commit()
    if has_schedule_changes:
        await refresh_saved_plan()
    return ranking_status()


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
            for pending_id in pending:
                try:
                    await prepare(pending_id)
                except HTTPException as exc:
                    report('monocle', error=str(exc.detail))
                    raise
        with Session(get_engine()) as session:
            signature, ready, waiting = task_state(session)
            pre_external = _external_signature(session)
            saved = session.get(TaskRanking, 1)
            if not force and saved and saved.snapshot == signature and not plan_expired(json.loads(saved.result)):
                return json.loads(saved.result)
        checked_contexts = set()
        while ready:
            async with scheduler.slot('ameba', task_id):
                with Session(get_engine()) as session:
                    signature, ready, waiting = task_state(session)
                    pre_external = _external_signature(session)
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
                for context_id in requests:
                    await prepare(context_id, force=True, review_request=result.get('context_reason', ''))
                    checked_contexts.add(context_id)
            with Session(get_engine()) as session:
                signature, ready, waiting = task_state(session)
                pre_external = _external_signature(session)
        else:
            with Session(get_engine()) as session:
                wish_text = read_adjustments(session)['text'].strip()
            if wish_text:
                from agent_memory import save as save_fact
                save_fact({'key': 'ameba.wish_' + uuid4().hex[:8], 'text': wish_text}, 'Ameba')
            result = {
                'task_ids': [], 'comparisons': 0, 'uncertain_comparisons': 0,
                'explanation': 'Нет задач с готовым контекстом.', 'reasons': [],
                'schedule': {'version': 2, 'periods': [], 'daily_allocations': [], 'deadline_conflicts': [],
                             'remaining': [], 'feasible': True, 'min_minutes': 0, 'max_minutes': 0,
                             'hours_label': 'выполнено', 'generated_date': datetime.now(ZoneInfo(read_calendar()['timezone'])).date().isoformat(),
                             'start_at': datetime.now(timezone.utc).isoformat(), 'timeline_error': None},
            }
        ids = result.get('task_ids', [])
        if len(ids) != len(ready) or set(ids) != {task['id'] for task in ready}:
            raise HTTPException(502, 'Ameba вернула неполный порядок задач')
        schedule = result.get('schedule')
        if ready and (not schedule or schedule.get('timeline_error') or not schedule.get('start_at') or schedule.get('version') != 2):
            raise HTTPException(502, 'Ameba не построила корректный план периодов. Предыдущий результат сохранён.')
        result['task_ids'] = ids
        result.update(generated_at=datetime.now(timezone.utc).isoformat(), ranked_count=len(ids), pending_count=len(waiting), total_count=len(ready) + len(waiting))
        consumed_feedback = False
        with Session(get_engine()) as session:
            if task_state(session)[0] != signature and _external_signature(session) != pre_external:
                raise HTTPException(409, 'Задачи или контекст изменились. Повторите планирование')
            adj = read_adjustments(session)
            if adj['text']:
                consumed_feedback = True
                adj['text'] = ''
                save_adjustments(adj, session=session)
                session.flush()
            saved = session.get(TaskRanking, 1) or TaskRanking()
            final_signature = task_state(session)[0] if (consumed_feedback or task_state(session)[0] != signature) else signature
            saved.snapshot = final_signature
            saved.result = json.dumps(result, ensure_ascii=False)
            session.add(saved)
            for tid in ids:
                task = session.get(Task, tid)
                task.ai_details = json.dumps({'version': 41, 'status': 'ranked'})
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
        report('ameba', success=True)
        return result | ({'consumed_feedback': True} if consumed_feedback else {})


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
        metrics = {'version': 41, 'status': 'needs_context', 'reason': ' '.join(context['questions'])}
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
                     'metrics': {'status': 'ranked', 'version': 41}}
