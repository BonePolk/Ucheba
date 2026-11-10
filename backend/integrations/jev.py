import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from common.accounts import check_feature
from common.database import get_engine
from common.dates import normalize_utc
from common.models.integration import TaskContext, TaskRanking
from common.models.task import Task
from common.rpc import call
from common.settings import read_config, report
from integrations.context import context_status, prepare, snapshot
from integrations import scheduler

ranking_lock = asyncio.Lock()


def task_state(session):
    tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
    ready, waiting, state = [], [], []
    for task in tasks:
        context = session.get(TaskContext, task.id)
        usage = json.loads(context.usage) if context else {}
        valid = context_status(task, context) == 'ready'
        state.append({'task': snapshot(task), 'id': task.id,
                      'context_version': str(context.updated_at) if context else None,
                      'ready': valid})
        if valid:
            ready.append({'id': task.id, 'ranking_summary': usage['ranking_summary'],
                          'deadline': normalize_utc(task.deadline), 'priority': task.priority})
        else:
            waiting.append(task.id)
    # При равном сравнении сохраняем ручной приоритет, затем ближайший дедлайн.
    ready.sort(key=lambda task: ({'high': 0, 'medium': 1, 'low': 2}.get(task['priority'], 1),
                                 task['deadline'].isoformat() if task['deadline'] else '9999', task['id']))
    signature = json.dumps({'version': 7, 'hour': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H'), 'tasks': state}, sort_keys=True)
    return signature, ready, waiting


def overdue_first(ids, ready):
    now = datetime.now(timezone.utc)
    overdue = {task['id'] for task in ready if task['deadline'] and task['deadline'] < now}
    return sorted(ids, key=lambda task_id: task_id not in overdue)


def ranking_status():
    with Session(get_engine()) as session:
        signature, ready, waiting = task_state(session)
        saved = session.get(TaskRanking, 1)
        result = json.loads(saved.result) if saved else {}
        ready_ids = {task['id'] for task in ready}
        # Изменение одной задачи или часа не должно стирать порядок остальных.
        ids = overdue_first([task_id for task_id in result.get('task_ids', []) if task_id in ready_ids], ready)
        return result | {'task_ids': ids, 'ranked_count': len(ids),
                         'pending_count': len(waiting), 'total_count': len(ready) + len(waiting),
                         'needs_refresh': not saved or saved.snapshot != signature,
                         'uncertain_comparisons': result.get('uncertain_comparisons', 0)}


async def rank_all(force=False, task_id=0):
    check_feature('jev')
    if not read_config('jev')['enabled']:
        raise HTTPException(400, 'Включите Jeba в настройках')
    async with ranking_lock:
        with Session(get_engine()) as session:
            signature, ready, waiting = task_state(session)
            saved = session.get(TaskRanking, 1)
            if not force and saved and saved.snapshot == signature:
                return json.loads(saved.result)
        if len(ready) > 1:
            async with scheduler.slot('jev', task_id):
                # Пока ждали Lucheba, контекст и состав задач могли измениться.
                with Session(get_engine()) as session:
                    signature, ready, waiting = task_state(session)
                result = await call('jev', '/rank', {'tasks': ready, 'now': datetime.now(timezone.utc).isoformat(), 'task_id': task_id}, timeout=None)
        else:
            result = {'task_ids': [task['id'] for task in ready], 'comparisons': 0, 'uncertain_comparisons': 0}
        ids = result.get('task_ids', [])
        if len(ids) != len(ready) or set(ids) != {task['id'] for task in ready}:
            raise HTTPException(502, 'Jeba вернул неполный порядок задач')
        ids = overdue_first(ids, ready)
        result['task_ids'] = ids
        result.update(ranked_count=len(ids), pending_count=len(waiting), total_count=len(ready) + len(waiting))
        with Session(get_engine()) as session:
            if task_state(session)[0] != signature:
                raise HTTPException(409, 'Задачи или контекст изменились. Повторите сортировку')
            saved = session.get(TaskRanking, 1) or TaskRanking()
            saved.snapshot = signature
            saved.result = json.dumps(result)
            session.add(saved)
            for task_id in ids:
                task = session.get(Task, task_id)
                task.ai_details = json.dumps({'version': 7, 'status': 'ranked'})
                task.ai_scored_at = datetime.now(timezone.utc)
                session.add(task)
            session.commit()
        report('jev', success=True)
        return result


async def evaluate(task_id=None):
    if task_id is None:
        async with scheduler.slot('jev'):
            return await check_model()
    return await evaluate_task(task_id)


async def check_model():
    # Проверка подключения по кнопке в профиле, без обращений к Lucheba.
    now = datetime.now(timezone.utc)
    result = await call('jev', '/rank', {'tasks': [
        {'id': 1, 'title': 'Обновить оформление внутренней презентации',
         'deadline': (now + timedelta(days=14)).isoformat(),
         'ranking_summary': 'Необязательная косметическая правка внутреннего черновика. '
                         'Текущее оформление подходит, работа команды не заблокирована.'},
        {'id': 2, 'title': 'Отправить клиенту согласованный договор',
         'deadline': (now + timedelta(hours=1)).isoformat(),
         'ranking_summary': 'Договор уже согласован и готов к отправке. Клиент ждёт его в течение часа '
                         'для подписания и оплаты. Задержка сорвёт сегодняшнюю сделку и отложит оплату.'},
    ], 'now': now.isoformat()}, timeout=None)
    if result.get('task_ids') != [2, 1]:
        raise HTTPException(502, 'Jeba не поставил срочную отправку договора клиенту выше косметической правки презентации. На порядок модели пока нельзя полагаться.')
    return {'ok': True, 'comparisons': result['comparisons']}


async def evaluate_task(task_id):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        original = snapshot(task)
    context = await prepare(task_id)
    # Lucheba могла уточнить поля задачи. Дальше сравниваем уже с её результатом.
    original = context['task_snapshot']
    if context['status'] != 'ready':
        metrics = {'version': 7, 'status': 'needs_context', 'reason': ' '.join(context['questions'])}
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
                     'metrics': {'status': 'ranked', 'version': 7}}
