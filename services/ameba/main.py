import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from openai_codex import ApprovalMode, ExternalMessage, Sandbox
from pydantic import ValidationError

from common.agent_api import agent_routes
from common.agent_history import with_history
from common.agent_issues import ISSUE_INSTRUCTION, ISSUE_TOOL, report_problem
from common.agent_memory import MEMORY_INSTRUCTION, MEMORY_NAMES, MEMORY_TOOLS, memory_request, memory_tool
from common.agent_questions import QUESTION_INSTRUCTION, QUESTION_TOOL, ask_user
from common.codex_harness import (
    CONTAINER_SANDBOX,
    McpToolAdapter,
    append_custom_instructions,
    check_connection,
    normalize_auth,
    normalize_effort,
    open_codex,
    prepare_codex_root,
    run_codex_turn,
    scratch_env,
    serve_mcp,
    start_codex_thread,
)
from common.dates import deadline_facts
from common.http import private_app
from common.identity import user_id
from common.progress import NOTE_INSTRUCTION, NOTE_TOOL, Progress, get_progress, get_work_progress
import planning


@asynccontextmanager
async def lifespan(app):
    prepare_codex_root()
    yield


app = private_app('ameba', lifespan)
agent_routes(app, 'Ameba')


@app.post('/check')
async def check_model(body: dict):
    await check_connection(
        'Ameba',
        config=body.get('config'),
        api_key=body.get('api_key', ''),
        default_mode='api_key',
    )
    return {'ok': True, 'comparisons': 0}


@app.post('/refresh')
async def refresh_plan(body: dict):
    """Детерминированно обновляет сохранённый план v3 без вызова LLM."""
    previous = body['previous_plan']
    now = body.get('now') or datetime.now(timezone.utc).isoformat()
    tasks = body.get('tasks', [])
    tz = body.get('timezone', 'UTC')
    week = planning.restore_week(body.get('weekly_schedule'), tz)
    adjustments = body.get('manual_adjustments')
    style = (body.get('config') or {}).get('planning_style') or 'balanced'
    try:
        return planning.refresh_schedule(
            previous,
            tasks,
            week,
            now,
            planning_style=style,
            adjustments=adjustments,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/ranking/progress')
def ranking_progress(task_id: int = 0):
    return get_progress(task_id, namespace='ranking') if task_id else get_work_progress('ranking')


active_runs = {}
cancelled_runs = OrderedDict()


@app.post('/cancel')
async def cancel_rank(body: dict):
    key = (user_id(), str(body.get('run_id', '')))
    cancelled_runs[key] = True
    while len(cancelled_runs) > 256:
        cancelled_runs.popitem(last=False)
    task = active_runs.get(key)
    if task:
        if not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return {'ok': True}


@app.post('/rank')
async def rank_tasks(body: dict):
    key = (user_id(), body.pop('run_id'))
    if key in cancelled_runs:
        raise HTTPException(409, 'Сортировка отменена')
    task = asyncio.create_task(rank(body))
    active_runs[key] = task
    try:
        return await task
    except asyncio.CancelledError:
        raise HTTPException(409, 'Сортировка отменена') from None
    finally:
        active_runs.pop(key, None)


CONTEXT_TOOL = {
    'type': 'function',
    'name': 'request_context_check',
    'description': 'Остановить планирование и попросить Monocle перепроверить устаревший или противоречивый контекст выбранных задач.',
    'strict': True,
    'parameters': {
        'type': 'object',
        'properties': {
            'task_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'reason': {'type': 'string'},
        },
        'required': ['task_ids', 'reason'],
        'additionalProperties': False,
    },
}

READ_MEMORY_TOOL = next(tool for tool in MEMORY_TOOLS if tool['name'] == 'read_user_memory')

AMEBA_TOOLS = [
    planning.PROPOSE_TOOL,
    QUESTION_TOOL,
    CONTEXT_TOOL,
    READ_MEMORY_TOOL,
    NOTE_TOOL,
    ISSUE_TOOL,
]

AMEBA_INSTRUCTIONS = (
    'Ты Ameba — персональный интеллектуальный планировщик '
    '(всегда говори о себе в женском роде: «я прочитала», «учла», «запланировала», без мужского рода; '
    'Monocle и Straus — мужской род).\n'
    'Твоя задача — помочь человеку выполнить важные дела с минимальными неудобствами, реалистичной нагрузкой и достаточным отдыхом.\n'
    'Правила планирования:\n'
    '1. Не стремись заполнить все свободные часы. Полноценный отдых и спокойные дни — часть хорошего плана (можешь явно отмечать их периодом kind="rest").\n'
    '2. Учитывай смысл задач, дедлайны, обязательные события календаря из calendar_days, пользовательскую память (user_memory), привычки, ресурс внимания и физическую нагрузку.\n'
    '3. Ищи удобные контекстные сочетания: если задачу из контекста или памяти удобно совместить с уже запланированным событием (например, после занятий по дороге) — размещай её в подходящие дни и поясняй это в context_hint.\n'
    '4. Различай делимость: объёмные дела, подготовку, уборку или переезд разумно вести постепенно (splittable=true) по нескольким подходящим дням; неделимые события вроде контрольной или встречи держи цельным подходом в один день (splittable=false).\n'
    '5. Оценивай реалистично: в total_minutes_min/max указывай полный объём активной работы с нуля — Python один раз вычтет подтверждённый completed_work_minutes. Прошедшие дни или часы без подтверждения не уменьшают объём.\n'
    '6. Предлагай гибкие периоды (start_date..end_date + примерные внутренние allocations по дням для проверки ёмкости), а не вымышленные почасовые слоты. Постоянный календарь принадлежит владельцу и Straus — не меняй его и не дублируй.\n'
    '7. Отличай подтверждённые факты от предположений: не придумывай маршруты, расположение пунктов выдачи, события, дедлайны и привычки. При неопределённости формулируй условную возможность («если по дороге…»). Если от неизвестного обстоятельства критически зависит весь план — задай один точный вопрос через ask_user (но не спрашивай о мелочах).\n'
    '8. Уважай явно заданные ограничения (manual_adjustments: task_pins, day_overrides, estimate_overrides, task_notes) и сохраняй разумную часть previous_plan, не переставляя неделю без причины.\n'
    '9. Если доступного времени до жёсткого дедлайна объективно недостаточно — никогда не урезай отдых и не занижай оценку ради видимости успеха. Предложи честный частичный план с главным фокусом и укажи причину риска в deadline_risk.\n'
    '10. Обработка user_feedback в preferences:\n'
    '   - scope="once": разовое пожелание на текущий план («эту пятницу оставь свободной»);\n'
    '   - scope="task": уточнение или привязка для конкретной задачи («этот переезд делай после пар», обязательно укажи task_id);\n'
    '   - scope="persistent": только устойчивая долгосрочная привычка («обычно сложную работу делаю в свободные дни»).\n'
    '   Если сомневаешься между once и persistent — выбирай once. Если владелец явно сообщил о выполненном объёме работы по задаче, передай новый суммарный completed_work_minutes в progress_updates.\n'
    '11. Сверяйся с current_clock (формат дат день.месяц.год: 12.10 = 12 октября), deadlines, available_from и зависимостями. Если контекст задачи устарел или противоречив — можешь один раз остановить расчёт через request_context_check (не повторяя ID из checked_contexts).\n'
    '12. Вызови propose_plan с готовым предложением. Программный валидатор проверит инварианты; при обнаружении ошибок аккуратно исправь только указанные нарушения, сохраняя остальные корректные решения.'
)


def _compact_previous(previous_plan: dict | None) -> dict | None:
    if not previous_plan or not isinstance(previous_plan, dict):
        return None
    sched = previous_plan.get('schedule') or {}
    periods = []
    for p in sched.get('periods', [])[:16]:
        periods.append({
            'kind': p.get('kind', 'work'),
            'task_id': p.get('task_id'),
            'title': p.get('title'),
            'start_date': p.get('start_date'),
            'end_date': p.get('end_date'),
            'goal': p.get('goal'),
            'context_hint': p.get('context_hint'),
            'allocations': p.get('allocations'),
        })
    return {
        'task_ids': previous_plan.get('task_ids', []),
        'explanation': previous_plan.get('explanation', ''),
        'periods': periods,
    }


def _save_persistent_preferences(existing_memory: list[dict], items: list[dict]) -> list[tuple[str, str]]:
    """Подготавливает неповторяющиеся устойчивые привычки к сохранению."""
    owner_texts = {
        (f.get('text') or '').strip().casefold()
        for f in existing_memory
        if f.get('agent') in ('Владелец', 'user')
    }
    all_texts = {(f.get('text') or '').strip().casefold() for f in existing_memory}
    owner_keys = {f.get('key') for f in existing_memory if f.get('agent') in ('Владелец', 'user')}
    to_save: list[tuple[str, str]] = []
    for item in items:
        text = (item.get('text') or '').strip()
        if not text:
            continue
        norm = text.casefold()
        if norm in all_texts or norm in owner_texts:
            continue
        raw_key = (item.get('key') or '').strip()
        if not raw_key or raw_key in owner_keys or not all(c.isalnum() or c in '_.-' for c in raw_key):
            raw_key = f'ameba.pref_{uuid4().hex[:8]}'
        elif not raw_key.startswith('ameba.'):
            raw_key = f'ameba.{raw_key}'[:100]
        all_texts.add(norm)
        to_save.append((raw_key, text))
    return to_save


class AmebaTools:
    def __init__(self, body, planning_week, planning_style, progress, has_feedback):
        self.body = body
        self.tasks = body.get('tasks', [])
        self.expected = [task['id'] for task in self.tasks]
        self.adjustments = body.get('manual_adjustments')
        self.planning_week = planning_week
        self.planning_style = planning_style
        self.progress = progress
        self.has_feedback = has_feedback
        self.proposal_attempts = 0
        self.validated = None
        self.adapter = None

    async def call(self, name, args):
        raw_args = json.dumps(args, ensure_ascii=False)
        try:
            if name == 'agent_note':
                return self.progress.note(raw_args), []
            if name == 'report_tool_problem':
                self.progress.add('Сообщает администратору о технической проблеме')
                return await report_problem(args, 'Ameba'), []
            if name == 'read_user_memory':
                self.progress.add('Сверяется с памятью владельца')
                output = await memory_tool(name, args, 'Ameba')
                self.body['user_memory'] = output
                return output, []
            if name == 'ask_user':
                return await ask_user(raw_args, self.progress), []
            if name == 'request_context_check':
                ids = args['task_ids']
                checked = self.body.get('checked_contexts', [])
                if not ids or len(ids) != len(set(ids)) or any(value not in self.expected or value in checked for value in ids):
                    raise ValueError('Некорректные ID задач для перепроверки контекста')
                titles = [task['title'] for task in self.tasks if task['id'] in ids]
                self.progress.add('Просит Monocle перепроверить: ' + ', '.join(titles), description=str(args['reason'])[:400])
                self.progress.update(stage='context', context_requests=ids)
                if self.adapter is not None:
                    self.adapter.terminal_result = {'context_requests': ids, 'context_reason': str(args['reason'])[:1000]}
                return {'status': 'accepted'}, []
            if name == 'propose_plan':
                self.proposal_attempts += 1
                self.progress.add('Проверяет предложенный план')
                proposal, issues, days, budgets = planning.validate_proposal(
                    args,
                    self.tasks,
                    self.planning_week,
                    self.body['now'],
                    planning_style=self.planning_style,
                    adjustments=self.adjustments,
                    has_feedback=self.has_feedback,
                )
                if issues:
                    logging.getLogger('uvicorn.error').info(
                        'Ameba proposal issues (attempt %d): %s',
                        self.proposal_attempts,
                        [i.get('code') for i in issues[:6]],
                    )
                    if self.proposal_attempts >= 2:
                        summary = '; '.join(i.get('message', i.get('code', 'ошибка')) for i in issues[:3])
                        if self.adapter is not None:
                            self.adapter.fatal_error = HTTPException(
                                502,
                                f'План не прошёл проверку после исправления: {summary}. Предыдущий план сохранён.',
                            )
                        return {'valid': False, 'issues': issues[:12]}, []
                    self.progress.add('Уточняет план по замечаниям проверки')
                    return {
                        'valid': False,
                        'issues': issues[:12],
                        'instruction': (
                            'Исправь указанные нарушения, сохраняя остальные удачные решения. '
                            'Если задача объективно не помещается к жёсткому сроку, оставь остаток незапланированным и укажи причину в deadline_risk.'
                        ),
                    }, []

                materialized = planning.materialize_plan(
                    proposal,
                    self.tasks,
                    days,
                    budgets,
                    self.planning_week,
                    self.body['now'],
                )
                self.validated = materialized
                self.progress.update(
                    stage='done',
                    total=len(self.expected),
                    completed=len(self.expected),
                    schedule=materialized['schedule'],
                    timeline=materialized['timeline'],
                )
                self.progress.add(materialized['explanation'])
                if self.adapter is not None:
                    self.adapter.terminal_result = {'plan_ready': True}
                return {'valid': True, 'periods_count': len(materialized['schedule']['periods'])}, []
            raise ValueError('Unknown tool')
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                errors = [
                    {'field': '.'.join(map(str, item['loc'])), 'type': item['type'], 'message': item['msg']}
                    for item in exc.errors(include_input=False, include_context=False, include_url=False)[:8]
                ]
                reason = '; '.join(f"{item['field']}: {item['type']}" for item in errors)
            else:
                reason = str(exc)
                errors = [{'message': reason}]
            logging.getLogger('uvicorn.error').warning('Ameba tool %s: %s', name, reason)
            self.progress.add(f'Исправляет параметры {name}: {reason}')
            return {'valid': False, 'error': reason, 'details': errors, 'expected_task_ids': self.expected}, []
        except httpx.HTTPError as exc:
            if name not in MEMORY_NAMES:
                raise
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            self.progress.add('Не удалось прочитать память')
            return {'error': 'Память временно недоступна.', 'status': status}, []


@with_history('Ameba')
async def rank(body: dict):
    key = body.pop('api_key', None) or ''
    config = body.pop('config', {})
    auth_mode, api_key, base_url, model = normalize_auth(config, key, default_mode='api_key', label='Ameba')
    reasoning_effort = normalize_effort(config.get('reasoning_effort'))
    planning_style = config.get('planning_style') or 'balanced'
    allow_context_recheck = bool(config.get('allow_context_recheck', True))
    custom_instructions = config.get('custom_instructions') or ''

    tasks = body.get('tasks', [])
    expected = [task['id'] for task in tasks]
    tz = body.get('timezone', 'UTC')
    planning_week = planning.restore_week(body.get('weekly_schedule'), tz)
    adjustments = body.get('manual_adjustments')
    has_feedback = bool((body.get('user_feedback') or '').strip())

    # Предзагружаем общую память владельца до старта модели (ошибки чтения не ломают планирование).
    try:
        user_memory = await memory_request()
    except httpx.HTTPError:
        user_memory = []

    now = datetime.now(timezone.utc)
    zone = ZoneInfo(tz)
    now_local = now.astimezone(zone)
    horizon_days = planning.compute_horizon(tasks, now_local.date(), zone)
    days = planning.analyze_days(
        planning_week,
        body.get('now') or now.isoformat(),
        horizon_days=horizon_days,
        adjustments=adjustments,
        planning_style=planning_style,
    )

    payload = {
        'tasks': tasks,
        'calendar_days': planning.calendar_overview(days),
        'timezone': tz,
        'user_memory': user_memory,
        'user_feedback': body.get('user_feedback') or '',
        'manual_adjustments': adjustments or {},
        'recent_observations': body.get('recent_observations', []),
        'previous_plan': _compact_previous(body.get('previous_plan')),
        'checked_contexts': body.get('checked_contexts', []),
        'current_clock': {
            'notice': 'Актуальное время сервера; старые даты проверки в контексте не являются сегодняшней датой.',
            'now_utc': now.isoformat(),
            'now_local': now_local.isoformat(),
            'deadlines': [{'task_id': task['id'], **deadline_facts(task.get('deadline'), now)} for task in tasks],
        },
    }
    if body.get('agent_history') or body.get('history_notice'):
        payload['reference_history'] = {
            'notice': body.get('history_notice', ''),
            'items': body.get('agent_history', []),
        }

    prompt_text = (
        f'Составь интеллектуальный план на ближайшие дни для всех {len(expected)} задач (ID: {expected}). '
        'Учитывай calendar_days, user_memory, user_feedback и ограничения; вызови propose_plan.\n'
        + json.dumps(payload, ensure_ascii=False, default=str)
    )

    active_tools = [tool for tool in AMEBA_TOOLS if allow_context_recheck or tool['name'] != 'request_context_check']
    instructions = ISSUE_INSTRUCTION + MEMORY_INSTRUCTION + QUESTION_INSTRUCTION + NOTE_INSTRUCTION + AMEBA_INSTRUCTIONS
    if planning_style == 'gentle':
        instructions += '\nРежим нагрузки владельца: ЩАДЯЩИЙ — закладывай больше восстановления и спокойных дней.'
    elif planning_style == 'intense':
        instructions += '\nРежим нагрузки владельца: ИНТЕНСИВНЫЙ — допустимы более плотные рабочие дни при сохранении сна и базового отдыха.'
    if not allow_context_recheck:
        instructions += '\nПерепроверка задач через Monocle (request_context_check) отключена: планируй по текущему контексту с явными допущениями.'
    instructions = append_custom_instructions(instructions, custom_instructions)
    sandbox = Sandbox.full_access if CONTAINER_SANDBOX else Sandbox.read_only

    with Progress('Ameba', body.get('task_id', 0), namespace='ranking') as progress:
        progress.update(
            stage='planning',
            total=len(tasks),
            completed=0,
            timeline=[{'id': task['id'], 'title': task['title']} for task in tasks],
        )
        progress.add('Изучает задачи, календарь и привычки владельца')
        tools_handler = AmebaTools(body, planning_week, planning_style, progress, has_feedback)
        adapter = McpToolAdapter(active_tools, tools_handler.call, progress)
        tools_handler.adapter = adapter

        async with scratch_env(writable=False) as (work_dir, shell_policy), serve_mcp(adapter) as mcp_config, open_codex(
            'Ameba',
            auth_mode=auth_mode,
            api_key=api_key,
            base_url=base_url,
            allow_shell=False,
            web_search='disabled',
        ) as (client, thread_provider):
            thread = await start_codex_thread(
                client,
                'Ameba',
                cwd=str(work_dir),
                sandbox=sandbox,
                ephemeral=True,
                approval_mode=ApprovalMode.deny_all,
                model=model or None,
                model_provider=thread_provider,
                developer_instructions=instructions,
                config=mcp_config | shell_policy,
            )
            outcome = await run_codex_turn(
                thread,
                ExternalMessage(tool_name='task_manager', content=prompt_text),
                label='Ameba',
                progress=progress,
                adapter=adapter,
                effort=reasoning_effort,
                client=client,
            )

            for _ in range(1):
                if outcome.terminal_result and outcome.terminal_result.get('context_requests'):
                    return outcome.terminal_result
                if tools_handler.validated is not None:
                    break
                if outcome.status == 'failed':
                    raise HTTPException(502, outcome.error_detail or 'Ameba не завершила планирование. Предыдущий план сохранён.')
                progress.add('Запрашивает вызов propose_plan')
                outcome = await run_codex_turn(
                    thread,
                    'Заверши планирование вызовом инструмента propose_plan для всех задач.',
                    label='Ameba',
                    progress=progress,
                    adapter=adapter,
                    effort=reasoning_effort,
                    client=client,
                )

            if outcome.terminal_result and outcome.terminal_result.get('context_requests'):
                return outcome.terminal_result
            if outcome.status == 'failed':
                raise HTTPException(502, outcome.error_detail or 'Ameba не завершила планирование. Предыдущий план сохранён.')

    if tools_handler.validated is None:
        raise HTTPException(502, 'Ameba не предложила корректный план. Предыдущий план сохранён.')

    result = tools_handler.validated
    current_memory = body.get('user_memory') if isinstance(body.get('user_memory'), list) else user_memory
    memory_saved = 0
    for mem_key, mem_text in _save_persistent_preferences(current_memory, result.pop('persistent_preferences', [])):
        try:
            await memory_tool('remember_user_fact', {'key': mem_key, 'text': mem_text}, 'Ameba')
            memory_saved += 1
        except httpx.HTTPError:
            pass

    result['comparisons'] = 0
    result['uncertain_comparisons'] = 0
    result['memory_saved_count'] = memory_saved
    return result
