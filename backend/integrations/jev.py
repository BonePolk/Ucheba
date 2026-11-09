import asyncio
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session

from common.database import get_engine
from common.rpc import call
from common.settings import report
from common.models.integration import TaskAction
from common.models.task import Task
from integrations.context import prepare, snapshot


evaluation_lock = asyncio.Lock()


async def evaluate(task_id=None):
    async with evaluation_lock:
        return await _evaluate(task_id)


async def _evaluate(task_id=None):
    with Session(get_engine()) as session:
        task = session.get(Task, task_id) if task_id else None
        if task_id and not task:
            raise HTTPException(404, 'Задача не найдена')
        original = snapshot(task) if task else None
        state = task.model_dump(include={'title', 'description', 'deadline', 'priority', 'status', 'category'}) if task else {'title': 'Подготовить отчёт для сдачи учебного курса', 'description': 'Составить отчёт по выполненной лабораторной работе и передать преподавателю. Без отчёта курс не будет зачтён.'}
    if task_id:
        context = await prepare(task_id)
        if context['status'] != 'ready':
            result = {'score': None, 'metrics': {
                'version': 4, 'status': 'needs_context', 'provider': 'gpt-6-luna',
                'reason': ' '.join(context['questions']), 'questions': context['questions'],
            }}
        else:
            state['context'] = context['summary']
            state['context_ready'] = True
            state['now'] = datetime.now(timezone.utc).isoformat()
            result = await call('jev', '/evaluate', {'state': state}, timeout=300)
    else:
        state['now'] = datetime.now(timezone.utc).isoformat()
        result = await call('jev', '/evaluate', {'state': state}, timeout=300)
    score, metrics = result['score'], result['metrics']
    metrics['version'] = 4
    if task_id:
        with Session(get_engine()) as session:
            task = session.get(Task, task_id)
            if task and snapshot(task) != original:
                raise HTTPException(409, 'Задача изменилась. Повторите оценку')
            if task:
                task.ai_score = score
                task.ai_details = json.dumps(metrics)
                task.ai_scored_at = datetime.now(timezone.utc)
                session.add(task)
                session.add(TaskAction(task_id=task_id, action='jev_score', details=json.dumps({'score': score})))
                session.commit()
    report('jev', success=True)
    return {'score': score, 'metrics': metrics}


