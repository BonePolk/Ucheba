import asyncio
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, select

from common.accounts import check_feature
from common.database import get_engine
from common.dates import normalize_utc
from common.models.integration import TaskContext, TaskRanking
from common.models.task import Task
from common.rpc import call
from common.settings import read_config, report
from integrations.context import prepare, snapshot

ranking_lock = asyncio.Lock()


def task_state(session):
    tasks = session.exec(select(Task).where(Task.archived == False, Task.status != 'done').order_by(Task.id)).all()
    ready, waiting, state = [], [], []
    for task in tasks:
        context = session.get(TaskContext, task.id)
        valid = bool(context and context.status == 'ready' and context.task_snapshot == snapshot(task))
        state.append({'task': snapshot(task), 'id': task.id,
                      'context_version': str(context.updated_at) if context else None,
                      'ready': valid})
        if valid:
            ready.append({'id': task.id, 'title': task.title, 'description': (task.description or '')[:2000],
                          'deadline': normalize_utc(task.deadline), 'context': context.summary,
                          'priority': task.priority})
        else:
            waiting.append(task.id)
    # При равном сравнении сохраняем ручной приоритет, затем ближайший дедлайн.
    ready.sort(key=lambda task: ({'high': 0, 'medium': 1, 'low': 2}.get(task['priority'], 1),
                                 task['deadline'].isoformat() if task['deadline'] else '9999', task['id']))
    signature = json.dumps({'hour': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H'), 'tasks': state}, sort_keys=True)
    return signature, ready, waiting


def ranking_status():
    with Session(get_engine()) as session:
        signature, ready, waiting = task_state(session)
        saved = session.get(TaskRanking, 1)
        if saved and saved.snapshot == signature:
            return json.loads(saved.result) | {'needs_refresh': False}
    return {'task_ids': [], 'ranked_count': 0, 'pending_count': len(waiting),
            'total_count': len(ready) + len(waiting), 'needs_refresh': True,
            'uncertain_comparisons': 0}


async def rank_all(force=False, task_id=0):
    check_feature('jev')
    if not read_config('jev')['enabled']:
        raise HTTPException(400, 'Включите Jev в настройках')
    async with ranking_lock:
        with Session(get_engine()) as session:
            signature, ready, waiting = task_state(session)
            saved = session.get(TaskRanking, 1)
            if not force and saved and saved.snapshot == signature:
                return json.loads(saved.result)
        if len(ready) > 1:
            result = await call('jev', '/rank', {'tasks': ready, 'now': datetime.now(timezone.utc).isoformat(), 'task_id': task_id}, timeout=300)
        else:
            result = {'task_ids': [task['id'] for task in ready], 'comparisons': 0, 'uncertain_comparisons': 0}
        ids = result.get('task_ids', [])
        if len(ids) != len(ready) or set(ids) != {task['id'] for task in ready}:
            raise HTTPException(502, 'Jev вернул неполный порядок задач')
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
                task.ai_details = json.dumps({'version': 5, 'status': 'ranked'})
                task.ai_scored_at = datetime.now(timezone.utc)
                session.add(task)
            session.commit()
        report('jev', success=True)
        return result


async def evaluate(task_id=None):
    if task_id is None:
        # Проверка подключения по кнопке в профиле, без обращений к Luna.
        result = await call('jev', '/rank', {'tasks': [
            {'id': 1, 'title': 'Выбрать обои для рабочего стола', 'context': 'Необязательное украшение.'},
            {'id': 2, 'title': 'Уйти из горящего дома', 'context': 'Непосредственная угроза жизни.'},
        ], 'now': datetime.now(timezone.utc).isoformat()}, timeout=300)
        if result.get('task_ids') != [2, 1]:
            raise HTTPException(502, 'Jev не различил срочную угрозу жизни и необязательную задачу. На порядок модели пока нельзя полагаться.')
        return {'ok': True, 'comparisons': result['comparisons']}
    with Session(get_engine()) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(404, 'Задача не найдена')
        original = snapshot(task)
    context = await prepare(task_id)
    if context['status'] != 'ready':
        metrics = {'version': 5, 'status': 'needs_context', 'reason': ' '.join(context['questions'])}
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
                     'metrics': {'status': 'ranked', 'version': 5}}
