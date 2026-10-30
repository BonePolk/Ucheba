import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session

from common.database import engine
from common.rpc import call
from common.settings import read_settings, report
from common.models.integration import TaskAction
from common.models.task import Task


async def evaluate(task_id=None):
    config, secrets = read_settings('jev')
    if not secrets.get('api_token') or not config['account_id']:
        raise HTTPException(400, 'Сохраните Account ID и API-токен Cloudflare')
    with Session(engine) as session:
        task = session.get(Task, task_id) if task_id else None
        if task_id and not task:
            raise HTTPException(404, 'Задача не найдена')
        state = task.model_dump(include={'title', 'description', 'deadline', 'priority'}) if task else {'title': 'Проверка подключения Jev'}
    state['now'] = datetime.now(timezone.utc).isoformat()
    result = await call('jev', '/evaluate', {'config': config, 'secrets': secrets, 'state': state})
    score, metrics = result['score'], result['metrics']
    if task_id:
        with Session(engine) as session:
            task = session.get(Task, task_id)
            if task:
                task.ai_score = score
                task.ai_details = json.dumps(metrics)
                task.ai_scored_at = datetime.now(timezone.utc)
                session.add(task)
                session.add(TaskAction(task_id=task_id, action='jev_score', details=json.dumps({'score': score})))
                session.commit()
    report('jev', success=True)
    return {'score': score, 'metrics': metrics}


