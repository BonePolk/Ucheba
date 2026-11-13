import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from common.accounts import check_feature
from common.database import get_engine
from common.identity import user_id
from common.dates import normalize_utc
from common.models.integration import TaskContext, TaskRanking, IntegrationSetting
from common.models.task import Task
from common.rpc import call
from common.calendar import planning_week as read_week, read_config as read_calendar, read_ameba_plan, save_ameba_plan
from common.settings import read_config, read_settings, report
from integrations.context import context_status, prepare, snapshot
from integrations import scheduler

ranking_locks = {}


def plan_feedback():
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'ameba_feedback')
        return json.loads(row.config).get('text', '') if row else ''


def save_plan_feedback(text):
    with Session(get_engine()) as session:
        row = session.get(IntegrationSetting, 'ameba_feedback') or IntegrationSetting(name='ameba_feedback')
        row.config = json.dumps({'text': text.strip()})
        session.add(row)
        session.commit()


def task_state(session):
    tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
    ready, waiting, state = [], [], []
    for task in tasks:
        context = session.get(TaskContext, task.id)
        usage = json.loads(context.usage) if context else {}
        valid = context_status(task, context) == 'ready'
        state.append({'task': snapshot(task), 'id': task.id,
                      'context': {'subject': context.subject, 'notes': context.owner_notes, 'status': context.status,
                                  'questions': context.questions, 'full_context': usage.get('full_markdown'),
                                  'time_estimate': usage.get('time_estimate'), 'effort': usage.get('effort'),
                                  'impact': usage.get('impact')} if context else None,
                      'ready': valid, 'completed_work_minutes': task.completed_work_minutes})
        if valid:
            ready.append({'id': task.id, 'title': task.title, 'description': task.description or '',
                          'category': task.category, 'subject': context.subject, 'owner_notes': context.owner_notes,
                          'sources': usage.get('sources', []),
                          'full_context': usage['full_markdown'],
                          'time_estimate': usage.get('time_estimate'),
                          'effort': usage.get('effort'), 'impact': usage.get('impact'),
                          'deadline': normalize_utc(task.deadline), 'priority': task.priority,
                          'completed_work_minutes': task.completed_work_minutes})
        else:
            waiting.append(task.id)
    # При равном сравнении сохраняем ручной приоритет, затем ближайший дедлайн.
    ready.sort(key=lambda task: ({'high': 0, 'medium': 1, 'low': 2}.get(task['priority'], 1),
                                 task['deadline'].isoformat() if task['deadline'] else '9999', task['id']))
    from agent_memory import read as read_facts
    calendar = read_calendar()
    signature = json.dumps({'version': 35, 'tasks': state, 'calendar': {'events': sorted(calendar['events'], key=lambda event: event['id']), 'timezone': calendar['timezone']},
        'facts': sorted(({'key': fact['key'], 'text': fact['text']} for fact in read_facts() if fact['agent'] in ('Владелец', 'user')), key=lambda fact: fact['key']),
        'feedback': plan_feedback(),
        'provider': {key: read_config('ameba')[key] for key in ('model', 'base_url')}}, sort_keys=True)
    return signature, ready, waiting


def automatic_inputs():
    # Только изменения владельца; течение времени обрабатывается worker отдельно.
    from agent_memory import read as read_facts
    with Session(get_engine()) as session:
        tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
        inputs = []
        for task in tasks:
            context = session.get(TaskContext, task.id)
            inputs.append({'id': task.id, 'task': snapshot(task),
                           'notes': context.owner_notes if context else '', 'completed_work_minutes': task.completed_work_minutes})
    calendar = read_calendar()
    return json.dumps({'tasks': inputs,
        'calendar': {'events': sorted(calendar['events'], key=lambda event: event['id']), 'timezone': calendar['timezone']},
        'facts': sorted(({'key': fact['key'], 'text': fact['text']} for fact in read_facts()
                         if fact['agent'] in ('Владелец', 'user')), key=lambda fact: fact['key'])}, sort_keys=True)


def calendar_schedule():
    from common.calendar import view_events
    from zoneinfo import ZoneInfo
    config = read_calendar()
    zone = ZoneInfo(config['timezone'])
    start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    day = start.astimezone(zone).date()
    blocks = []
    for event in view_events(day, day + timedelta(days=6)):
        midnight = datetime.fromisoformat(event['date']).replace(tzinfo=zone)
        hour, minute = map(int, event['start'].split(':'))
        end_hour, end_minute = map(int, event['end'].split(':'))
        left = (midnight + timedelta(hours=hour, minutes=minute)).astimezone(timezone.utc)
        right = (midnight + timedelta(hours=end_hour, minutes=end_minute)).astimezone(timezone.utc)
        blocks.append({'id': event['id'] + '-' + event['date'], 'kind': event['kind'], 'title': event['title'],
            'task_id': event.get('task_id'), 'permanent': True, 'start': int((left - start).total_seconds() / 60),
            'minutes': int((right - left).total_seconds() / 60)})
    return {'start_at': start.isoformat(), 'timezone': config['timezone'], 'blocks': blocks,
            'rest_assumption': 'Постоянное расписание. Ameba учитывает его до размещения задач.'}


def plan_expired(result):
    from zoneinfo import ZoneInfo
    schedule = result.get('schedule') or {}
    if not schedule.get('start_at'):
        return True
    zone = ZoneInfo(read_calendar()['timezone'])
    now = datetime.now(timezone.utc)
    origin = datetime.fromisoformat(schedule['start_at'])
    return (origin.astimezone(zone).date() != now.astimezone(zone).date()
            or any(origin + timedelta(minutes=block['start']) < now
                   for block in schedule.get('blocks', []) if block['kind'] == 'task' and not block.get('permanent')))


def ranking_status():
    with Session(get_engine()) as session:
        signature, ready, waiting = task_state(session)
        saved = session.get(TaskRanking, 1)
        result = json.loads(saved.result) if saved else {}
        ready_ids = {task['id'] for task in ready}
        # Изменение одной задачи не должно стирать порядок остальных.
        ids = [task_id for task_id in result.get('task_ids', []) if task_id in ready_ids]
        plan_reset = bool(saved) and read_ameba_plan().get('run_id') != result.get('plan_run_id')
        return result | {'plan_reset': plan_reset, 'has_plan': bool(result.get('schedule')), 'task_ids': ids, 'ranked_count': len(ids),
                         'reasons': [item for item in result.get('reasons', []) if item['task_id'] in ids],
                         'pending_count': len(waiting), 'total_count': len(ready) + len(waiting),
                         'feedback': plan_feedback(),
                         'needs_refresh': not saved or saved.snapshot != signature or plan_expired(result),
                         'uncertain_comparisons': result.get('uncertain_comparisons', 0),
                         'calendar_schedule': calendar_schedule()}


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
            session.commit()
    return {'ok': True}


async def request_ranking(tasks, task_id=0, checked_contexts=(), refresh=False):
    config, secrets = read_settings('ameba')
    if not config['enabled']:
        raise HTTPException(400, 'Ameba выключена в настройках профиля')
    if not secrets.get('api_key'):
        raise HTTPException(400, 'Для Ameba не сохранён API-ключ. Добавьте его в профиле')
    with Session(get_engine()) as session:
        input_signature = task_state(session)[0]
        saved = session.get(TaskRanking, 1)
        previous = json.loads(saved.result) if saved else {}
    return await scheduler.run_ranking({'input_signature': input_signature, 'api_key': secrets['api_key'], 'config': config, 'tasks': tasks,
                      'now': datetime.now(timezone.utc).isoformat(), 'weekly_schedule': read_week(), 'timezone': read_calendar()['timezone'], 'task_id': task_id,
                      'previous_plan': previous,
                      'user_feedback': plan_feedback(), 'checked_contexts': list(checked_contexts)}, endpoint='/refresh' if refresh else '/rank')


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
        # Сначала собираем недостающий контекст, затем один раз сортируем весь список.
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
            saved = session.get(TaskRanking, 1)
            if not force and saved and saved.snapshot == signature and not plan_expired(json.loads(saved.result)):
                return json.loads(saved.result)
        checked_contexts = set()
        while ready:
            async with scheduler.slot('ameba', task_id):
                # Пока ждали браузер, задачи и контекст могли измениться.
                with Session(get_engine()) as session:
                    signature, ready, waiting = task_state(session)
                    saved = session.get(TaskRanking, 1)
                    refresh = not force and bool(saved and saved.snapshot == signature)
                result = await request_ranking(ready, task_id, checked_contexts, refresh=refresh)
            requests = result.get('context_requests', [])
            if not requests:
                break
            ready_ids = {task['id'] for task in ready}
            if any(value not in ready_ids or value in checked_contexts for value in requests):
                raise HTTPException(502, 'Ameba запросила некорректную перепроверку контекста')
            # Здесь Ameba уже остановлена и не занимает очередь браузера.
            async with scheduler.batch(requests):
                for context_id in requests:
                    await prepare(context_id, force=True, review_request=result.get('context_reason', ''))
                    checked_contexts.add(context_id)
            with Session(get_engine()) as session:
                signature, ready, waiting = task_state(session)
        else:
            result = {'task_ids': [], 'comparisons': 0, 'uncertain_comparisons': 0,
                      'explanation': 'Нет задач с готовым контекстом.', 'reasons': []}
        ids = result.get('task_ids', [])
        if len(ids) != len(ready) or set(ids) != {task['id'] for task in ready}:
            raise HTTPException(502, 'Ameba вернула неполный порядок задач')
        schedule = result.get('schedule')
        if ready and (not schedule or schedule.get('timeline_error') or not schedule.get('start_at')):
            raise HTTPException(502, 'Ameba не построила корректный календарный план. Предыдущий результат сохранён.')
        result['task_ids'] = ids
        result.update(generated_at=datetime.now(timezone.utc).isoformat(), ranked_count=len(ids), pending_count=len(waiting), total_count=len(ready) + len(waiting))
        with Session(get_engine()) as session:
            if task_state(session)[0] != signature:
                raise HTTPException(409, 'Задачи или контекст изменились. Повторите сортировку')
            saved = session.get(TaskRanking, 1) or TaskRanking()
            saved.snapshot = signature
            saved.result = json.dumps(result)
            session.add(saved)
            for task_id in ids:
                task = session.get(Task, task_id)
                task.ai_details = json.dumps({'version': 35, 'status': 'ranked'})
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
        return result


async def evaluate(task_id=None):
    if task_id is None:
        async with scheduler.slot('ameba'):
            return await check_model()
    return await evaluate_task(task_id)


async def check_model():
    config, secrets = read_settings('ameba')
    if not secrets.get('api_key'):
        raise HTTPException(400, 'Для Ameba не сохранён API-ключ')
    return await call('ameba', '/check', {'config': config, 'api_key': secrets['api_key']}, timeout=None)


async def evaluate_task(task_id):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        original = snapshot(task)
    context = await prepare(task_id)
    # Monocle мог уточнить поля задачи. Дальше сравниваем уже с её результатом.
    original = context['task_snapshot']
    if context['status'] != 'ready':
        metrics = {'version': 35, 'status': 'needs_context', 'reason': ' '.join(context['questions'])}
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
                     'metrics': {'status': 'ranked', 'version': 35}}
