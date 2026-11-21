"""Локальные тесты новой интеллектуальной Ameba: валидатор, сценарии A–L, память, изоляция и /refresh."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ameba'), str(ROOT / 'backend')]

import main as ameba_service
import planning
from common import calendar
from common.database import init_db
from common.identity import current_user_id
from common.models.integration import IntegrationSetting, TaskContext, TaskRanking
from common.models.task import Task
from integrations import ameba, context as monocle_context, scheduler
from sqlalchemy import create_engine, text
from sqlmodel import Session

NOW = '2026-11-23T08:00:00+00:00'  # Понедельник


def empty_week(tz='UTC', events=None):
    return {'timezone': tz, 'days': [[] for _ in range(7)], 'events': [], 'calendar_events': events or []}


def assess(
    task_id,
    total_min,
    total_max=None,
    *,
    splittable=True,
    policy='soft',
    available_from=None,
    depends_on=None,
    effort='medium',
    reason='По приоритету и срокам',
    impact='Выполненная задача',
    deadline_risk=None,
):
    return {
        'task_id': task_id,
        'total_minutes_min': total_min,
        'total_minutes_max': total_max if total_max is not None else total_min,
        'wait_minutes': 0,
        'splittable': splittable,
        'deadline_policy': policy,
        'available_from': available_from,
        'depends_on': depends_on or [],
        'effort': effort,
        'reason': reason,
        'impact': impact,
        'deadline_risk': deadline_risk,
    }


def work_period(task_id, start_date, end_date, allocs, goal='Выполнить работу', hint=None, reason='Удобный период'):
    return {
        'kind': 'work',
        'task_id': task_id,
        'start_date': start_date,
        'end_date': end_date,
        'allocations': [{'date': d, 'minutes': m} for d, m in allocs],
        'goal': goal,
        'context_hint': hint,
        'reason': reason,
    }


def rest_period(start_date, end_date, goal='Свободный день для восстановления', reason='Без лишней нагрузки'):
    return {
        'kind': 'rest',
        'task_id': None,
        'start_date': start_date,
        'end_date': end_date,
        'allocations': [],
        'goal': goal,
        'context_hint': None,
        'reason': reason,
    }


def proposal_payload(task_ids, tasks, periods, preferences=None, progress_updates=None, explanation='Согласованный план недели.'):
    return {
        'task_ids': task_ids,
        'tasks': tasks,
        'periods': periods,
        'preferences': preferences or [],
        'progress_updates': progress_updates or [],
        'explanation': explanation,
    }


class ValidationAndScenarios(unittest.TestCase):
    def test_scenario_a_move_after_university(self):
        events = []
        for d in ('2026-11-23', '2026-11-25', '2026-11-27'):
            events.append({'id': f'uni-{d}', 'kind': 'busy', 'title': 'Университет', 'date': d, 'start': '09:00', 'end': '15:00'})
        tasks = [
            {'id': 42, 'title': 'Переезд', 'completed_work_minutes': 0},
            {'id': 43, 'title': 'Лабораторная', 'completed_work_minutes': 0},
        ]
        cand = proposal_payload(
            [42, 43],
            [
                assess(42, 150, 180, splittable=True, effort='medium', reason='Можно перевозить постепенно'),
                assess(43, 210, 240, splittable=True, effort='high', reason='Требуется спокойный день'),
            ],
            [
                work_period(
                    42, '2026-11-23', '2026-11-27',
                    [('2026-11-23', 45), ('2026-11-25', 60), ('2026-11-27', 45)],
                    goal='Постепенно перевезти основные вещи',
                    hint='После университета в дни, когда поездка по дороге',
                ),
                work_period(
                    43, '2026-11-24', '2026-11-26',
                    [('2026-11-24', 120), ('2026-11-26', 100)],
                    goal='Сделать основную часть лабораторной в свободные дни',
                    hint=None,
                ),
                rest_period('2026-11-28', '2026-11-29', goal='Свободные выходные'),
            ],
        )
        prop, issues, days, budgets = planning.validate_proposal(cand, tasks, empty_week(events=events), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(events=events), NOW)
        self.assertEqual(res['schedule']['version'], 3)
        move_period = next(p for p in res['schedule']['periods'] if p['task_id'] == 42)
        self.assertIn('После университета', move_period['context_hint'])

    def test_scenario_b_heavy_lab_on_free_days(self):
        events = [
            {'id': f'u-{d}', 'kind': 'busy', 'title': 'Пары', 'date': d, 'start': '09:00', 'end': '17:00'}
            for d in ('2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26', '2026-11-27')
        ]
        tasks = [{'id': 1, 'title': 'Тяжёлая лабораторная', 'deadline': '2026-11-30T18:00:00Z', 'completed_work_minutes': 0}]
        # Попытка назначить 4 часа в плотный учебный день отклоняется валидатором.
        bad = proposal_payload(
            [1],
            [assess(1, 300, 360, policy='finish_before', effort='high')],
            [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 240)])],
        )
        _, bad_issues, _, _ = planning.validate_proposal(bad, tasks, empty_week(events=events), NOW)
        self.assertTrue(any(i['code'] == 'over_capacity' for i in bad_issues))

        # Размещение основной части на свободные выходные принимается.
        good = proposal_payload(
            [1],
            [assess(1, 300, 360, policy='finish_before', effort='high')],
            [work_period(1, '2026-11-28', '2026-11-29', [('2026-11-28', 180), ('2026-11-29', 150)])],
        )
        prop, issues, _, _ = planning.validate_proposal(good, tasks, empty_week(events=events), NOW)
        self.assertEqual(issues, [])
        self.assertIsNotNone(prop)

    def test_scenario_c_overload_requires_honest_risk(self):
        tasks = [{'id': 1, 'title': 'Подготовка к экзамену', 'deadline': '2026-11-24T22:00:00Z', 'completed_work_minutes': 0}]
        # Если помещено меньше минимума до дедлайна, но deadline_risk=null -> отклоняется.
        dishonest = proposal_payload(
            [1],
            [assess(1, 720, 720, policy='expires', effort='high', deadline_risk=None)],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 180), ('2026-11-24', 180)])],
        )
        _, issues, _, _ = planning.validate_proposal(dishonest, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'missing_deadline_risk' for i in issues))

        honest = proposal_payload(
            [1],
            [assess(1, 720, 720, policy='expires', effort='high', deadline_risk='Доступно ~6 ч из 12 ч; фокус на основных темах')],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 180), ('2026-11-24', 180)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(honest, tasks, empty_week(), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(), NOW)
        self.assertFalse(res['schedule']['feasible'])
        self.assertGreater(res['schedule']['remaining'][0]['remaining_work_minutes'], 0)

    def test_scenario_f_progress_subtracted_once_and_refresh(self):
        tasks = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 120}]
        cand = proposal_payload(
            [1],
            [assess(1, 360, 360)],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 120), ('2026-11-24', 120)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(cand, tasks, empty_week(), NOW)
        self.assertEqual(issues, [])
        self.assertEqual(budgets[1]['rem_max'], 240)
        res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(), NOW)
        self.assertEqual(res['timeline'][0]['plan']['work_max_minutes'], 240)

        # Попытка назначить 300 мин при остатке 240 мин отклоняется.
        over = proposal_payload(
            [1],
            [assess(1, 360, 360)],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 150), ('2026-11-24', 150)])],
        )
        _, over_issues, _, _ = planning.validate_proposal(over, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'overallocated_task' for i in over_issues))

        # /refresh без LLM уменьшает аллокации при новом подтверждённом прогрессе, сохраняя выбранные дни.
        refreshed = planning.refresh_schedule(
            res,
            [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 240}],
            empty_week(),
            NOW,
        )
        self.assertEqual(refreshed['timeline'][0]['plan']['work_max_minutes'], 120)
        self.assertEqual(refreshed['schedule']['periods'][0]['minutes'], 120)

    def test_scenario_h_validator_catches_model_errors(self):
        tasks = [
            {'id': 1, 'title': 'Неделимая контрольная', 'deadline': '2026-11-24T20:00:00Z', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Вторая задача', 'completed_work_minutes': 0},
        ]
        bad = proposal_payload(
            [1, 2, 999],
            [
                assess(1, 90, 90, splittable=False, policy='expires'),
                assess(2, 60, 60, depends_on=[1]),
            ],
            [
                # Дробление неделимой задачи + работа после дедлайна + конфликт с rest.
                work_period(1, '2026-11-24', '2026-11-25', [('2026-11-24', 45), ('2026-11-25', 45)]),
                # Зависимая задача раньше базовой.
                work_period(2, '2026-11-23', '2026-11-23', [('2026-11-23', 60)]),
                rest_period('2026-11-25', '2026-11-25'),
            ],
        )
        _, issues, _, _ = planning.validate_proposal(bad, tasks, empty_week(), NOW)
        codes = {i['code'] for i in issues}
        self.assertIn('unknown_task', codes)
        bad['task_ids'] = [1, 2]
        _, issues2, _, _ = planning.validate_proposal(bad, tasks, empty_week(), NOW)
        codes2 = {i['code'] for i in issues2}
        self.assertTrue({'indivisible_split', 'after_deadline', 'rest_day_conflict', 'dependency_order_violation'} <= codes2)

    def test_scenario_i_manual_pins_enforced(self):
        tasks = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}]
        adjustments = {'task_pins': {'1': {'target_date': '2026-11-28', 'not_before': '2026-11-28'}}}
        wrong = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 120)])],
        )
        _, issues, _, _ = planning.validate_proposal(wrong, tasks, empty_week(), NOW, adjustments=adjustments)
        codes = {i['code'] for i in issues}
        self.assertIn('violates_not_before', codes)
        self.assertIn('violates_task_pin', codes)

        ok = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-28', '2026-11-28', [('2026-11-28', 120)])],
        )
        _, ok_issues, _, _ = planning.validate_proposal(ok, tasks, empty_week(), NOW, adjustments=adjustments)
        self.assertEqual(ok_issues, [])

    def test_scenario_l_available_from_and_morning_deadline(self):
        # Контрольная открывается Чт 14:00, закрывается Пт 11:00.
        tasks = [{
            'id': 1,
            'title': 'Контрольная',
            'deadline': '2026-11-27T11:00:00+00:00',
            'completed_work_minutes': 0,
        }]
        early = proposal_payload(
            [1],
            [assess(1, 90, 90, splittable=False, policy='expires', available_from='2026-11-26T14:00:00+00:00')],
            [work_period(1, '2026-11-25', '2026-11-25', [('2026-11-25', 90)])],
        )
        _, early_issues, _, _ = planning.validate_proposal(early, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'before_available_from' for i in early_issues))

        # Слишком много в пятницу до 11:00 (с 07:00 до 11:00 всего 240 мин).
        morning_overflow = proposal_payload(
            [1],
            [assess(1, 300, 300, splittable=False, policy='expires', available_from='2026-11-26T14:00:00+00:00')],
            [work_period(1, '2026-11-27', '2026-11-27', [('2026-11-27', 300)])],
        )
        _, ovf_issues, _, _ = planning.validate_proposal(morning_overflow, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'deadline_day_over_capacity' for i in ovf_issues))

        valid_test = proposal_payload(
            [1],
            [assess(1, 90, 90, splittable=False, policy='expires', available_from='2026-11-26T14:00:00+00:00')],
            [work_period(1, '2026-11-26', '2026-11-26', [('2026-11-26', 90)])],
        )
        _, valid_issues, _, _ = planning.validate_proposal(valid_test, tasks, empty_week(), NOW)
        self.assertEqual(valid_issues, [])

    def test_strict_openai_tool_schemas(self):
        def check_strict(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    self.assertFalse(node.get('additionalProperties', True))
                    props = set(node.get('properties', {}))
                    req = set(node.get('required', []))
                    self.assertEqual(props, req)
                for value in node.values():
                    check_strict(value)
            elif isinstance(node, list):
                for item in node:
                    check_strict(item)

        for tool in ameba_service.AMEBA_TOOLS:
            self.assertTrue(tool['strict'])
            check_strict(tool['parameters'])


class AgentLoop(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, actions, *, user_feedback='', memory_items=None, memory_mock=None):
        from contextlib import asynccontextmanager
        from common.codex_harness import TurnOutcome

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(NOW)

        thread = MagicMock()
        codex_client = MagicMock()
        codex_client.thread_start = AsyncMock(return_value=thread)

        @asynccontextmanager
        async def fake_open_codex(*args, **kwargs):
            yield codex_client, 'ucheba_api'

        @asynccontextmanager
        async def fake_scratch(*args, **kwargs):
            yield '/tmp/work', {}

        @asynccontextmanager
        async def fake_serve(adapter):
            yield {'mcp_servers': {}}

        step_iter = iter(actions)

        async def fake_turn(thread_arg, prompt_input, *, adapter=None, **kwargs):
            turn_actions = next(step_iter, [])
            for idx, (name, arguments) in enumerate(turn_actions, start=1):
                await adapter.respond({
                    'jsonrpc': '2.0',
                    'id': idx,
                    'method': 'tools/call',
                    'params': {'name': name, 'arguments': arguments},
                })
                if adapter.fatal_error is not None or adapter.terminal_result is not None:
                    break
            if adapter.fatal_error is not None:
                raise adapter.fatal_error
            return TurnOutcome(
                text='done',
                completed=True,
                status='completed',
                error_detail=None,
                usage={'input_tokens': 0, 'output_tokens': 0},
                terminal_result=adapter.terminal_result,
            )

        mem_fn = memory_mock or AsyncMock(return_value=memory_items or [])
        with patch.object(ameba_service, 'datetime', Clock), \
             patch.object(ameba_service, 'Progress', MagicMock()), \
             patch.object(ameba_service, 'memory_request', AsyncMock(return_value=memory_items or [])), \
             patch.object(ameba_service, 'memory_tool', mem_fn), \
             patch.object(ameba_service, 'open_codex', fake_open_codex), \
             patch.object(ameba_service, 'scratch_env', fake_scratch), \
             patch.object(ameba_service, 'serve_mcp', fake_serve), \
             patch.object(ameba_service, 'run_codex_turn', side_effect=fake_turn):
            return await ameba_service.rank.__wrapped__({
                'api_key': 'synthetic',
                'config': {'model': 'fixture'},
                'tasks': [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}],
                'now': NOW,
                'weekly_schedule': empty_week(),
                'user_feedback': user_feedback,
            })

    async def test_scenario_d_once_preference_does_not_write_memory(self):
        mem_mock = AsyncMock(return_value=[])
        cand = proposal_payload(
            [1],
            [assess(1, 180, 180)],
            [
                work_period(1, '2026-11-24', '2026-11-25', [('2026-11-24', 90), ('2026-11-25', 90)]),
                rest_period('2026-11-27', '2026-11-27', goal='Пятница свободна по пожеланию'),
            ],
            preferences=[{'text': 'Эту пятницу оставить свободной', 'scope': 'once', 'task_id': None, 'key': None}],
        )
        res = await self.run_agent(
            [[('propose_plan', cand)]],
            user_feedback='Эту пятницу оставь свободной',
            memory_mock=mem_mock,
        )
        self.assertEqual(res['schedule']['version'], 3)
        self.assertEqual(res['memory_saved_count'], 0)
        mem_mock.assert_not_called()

    async def test_scenario_e_persistent_habit_saved_without_duplicates(self):
        mem_mock = AsyncMock(return_value={'ok': True})
        existing = [{'key': 'user.1', 'agent': 'Владелец', 'text': 'Учусь по утрам'}]
        cand = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-28', '2026-11-28', [('2026-11-28', 120)])],
            preferences=[
                {'text': 'Учусь по утрам', 'scope': 'persistent', 'task_id': None, 'key': 'user.1'},  # дубль факта владельца
                {'text': 'Обычно сложную работу предпочитаю делать в свободные дни', 'scope': 'persistent', 'task_id': None, 'key': 'hard_on_free_days'},
            ],
        )
        res = await self.run_agent(
            [[('propose_plan', cand)]],
            user_feedback='Обычно сложную работу делаю в свободные дни',
            memory_items=existing,
            memory_mock=mem_mock,
        )
        self.assertEqual(res['memory_saved_count'], 1)
        mem_mock.assert_awaited_once()
        saved_args = mem_mock.await_args.args[1]
        self.assertTrue(saved_args['key'].startswith('ameba.'))

    async def test_scenario_h_one_repair_succeeds_and_second_failure_stops(self):
        bad = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 500)])],  # over_capacity
        )
        good = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 60), ('2026-11-24', 60)])],
        )
        res = await self.run_agent([[('propose_plan', bad), ('propose_plan', good)]])
        self.assertEqual(res['schedule']['version'], 3)

        with self.assertRaisesRegex(Exception, 'после исправления'):
            await self.run_agent([[('propose_plan', bad), ('propose_plan', bad)]])

    async def test_refresh_works_without_llm(self):
        cand = proposal_payload(
            [1],
            [assess(1, 240, 240)],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 120), ('2026-11-24', 120)])],
        )
        result = await self.run_agent([[('propose_plan', cand)]])
        with patch.object(ameba_service, 'open_codex', side_effect=AssertionError('LLM must not run')):
            refreshed = await ameba_service.refresh_plan({
                'previous_plan': result,
                'weekly_schedule': empty_week(),
                'timezone': 'UTC',
                'tasks': [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}],
                'now': NOW,
            })
        self.assertEqual(refreshed['timeline'][0]['plan']['work_max_minutes'], 180)
        self.assertEqual(refreshed['schedule']['version'], 3)


class Storage(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f'sqlite:///{self.directory.name}/test.db')
        init_db(self.engine)
        self.owner = current_user_id.set(1)
        self.engine_patch = patch('common.database.get_engine', return_value=self.engine)
        self.settings_engine_patch = patch('common.settings.get_engine', return_value=self.engine)
        self.ameba_engine_patch = patch.object(ameba, 'get_engine', return_value=self.engine)
        self.engine_patch.start()
        self.settings_engine_patch.start()
        self.ameba_engine_patch.start()

    def tearDown(self):
        self.ameba_engine_patch.stop()
        self.settings_engine_patch.stop()
        self.engine_patch.stop()
        current_user_id.reset(self.owner)
        self.engine.dispose()
        self.directory.cleanup()

    def test_failed_run_retains_previous_plan(self):
        old = {'run_id': 'old', 'schedule': {'version': 3, 'start_at': NOW, 'periods': []}, 'revision': 'r1'}
        with Session(self.engine) as session:
            session.add(IntegrationSetting(name='ameba_plan', config=json.dumps(old)))
            session.commit()
        with patch.object(calendar, 'read_config', return_value={'revision': 'r1'}), \
             patch.object(scheduler, 'call', AsyncMock(side_effect=RuntimeError('synthetic'))):
            with self.assertRaises(RuntimeError):
                asyncio.run(scheduler.run_ranking({}))
        self.assertEqual(calendar.read_ameba_plan()['schedule'], old['schedule'])
        self.assertEqual(calendar.read_ameba_plan()['run_id'], 'old')

    def test_plan_and_ranking_rollback_together_and_late_run_is_rejected(self):
        calendar.reset_ameba_plan('new')
        result = {'schedule': {'version': 3, 'start_at': NOW, 'periods': []}}
        with Session(self.engine) as session:
            session.add(TaskRanking(result='new'))
            calendar.save_ameba_plan('new', result, 'r2', session)
            session.rollback()
        self.assertNotIn('schedule', calendar.read_ameba_plan())
        with Session(self.engine) as session:
            self.assertIsNone(session.get(TaskRanking, 1))
            with self.assertRaises(ValueError):
                calendar.save_ameba_plan('late', result, 'r2', session)

    def test_backend_rejects_timeline_error_without_replacing_saved_result(self):
        previous = {'task_ids': [1], 'schedule': {'version': 3, 'start_at': NOW, 'periods': []}}
        with Session(self.engine) as session:
            session.add(Task(id=1, title='Работа'))
            session.add(TaskRanking(snapshot='old', result=json.dumps(previous)))
            session.commit()
        invalid = {'task_ids': [1], 'schedule': {'version': 3, 'start_at': NOW, 'periods': [], 'timeline_error': 'invalid'}}
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'task_state', return_value=('new', [{'id': 1}], [])), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=invalid)):
            with self.assertRaisesRegex(Exception, 'Предыдущий результат сохранён'):
                asyncio.run(ameba.rank_all())
        with Session(self.engine) as session:
            self.assertEqual(json.loads(session.get(TaskRanking, 1).result), previous)

    def test_backend_applies_task_preferences_and_progress_without_touching_memory(self):
        task = Task(id=1, title='Переезд', status='todo', completed_work_minutes=0)
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

        cand = proposal_payload(
            [1],
            [assess(1, 180, 180)],
            [work_period(1, '2026-11-23', '2026-11-25', [('2026-11-23', 60), ('2026-11-25', 60)])],
            preferences=[{'text': 'Удобно перевозить после университета', 'scope': 'task', 'task_id': 1, 'key': None}],
            progress_updates=[{'task_id': 1, 'completed_work_minutes': 60}],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand, [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}], empty_week(), NOW, has_feedback=True,
        )
        self.assertEqual(issues, [])
        materialized = planning.materialize_plan(prop, [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}], days, budgets, empty_week(), NOW)
        fake_res = materialized | {'plan_run_id': 'run1', 'calendar_revision': 'rev1', 'memory_saved_count': 0}

        calendar.reset_ameba_plan('run1')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=fake_res)), \
             patch('agent_memory.read', return_value=[]):
            ameba.save_plan_feedback('уже перевёз треть, остальное после пар')
            out = asyncio.run(ameba.rank_all())
        self.assertEqual(out['task_notes_saved_count'], 1)
        self.assertEqual(out['progress_updated_count'], 1)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 60)
            self.assertEqual(session.get(Task, 1).status, 'in_progress')
            self.assertEqual(ameba.read_adjustments(session)['task_notes']['1'], 'Удобно перевозить после университета')

    def test_scenario_g_and_plan_expiry_uses_local_date(self):
        today_local = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC'}):
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 2, 'generated_date': today_local}}))
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 3, 'generated_date': yesterday}}))
            self.assertFalse(ameba.plan_expired({'schedule': {'version': 3, 'generated_date': today_local}}))

    def test_migration_preserves_existing_task(self):
        with Session(self.engine) as session:
            session.add(Task(title='Сохранить'))
            session.commit()
        with self.engine.begin() as connection:
            connection.execute(text('ALTER TABLE task DROP COLUMN completed_work_minutes'))
            connection.execute(text("UPDATE alembic_version SET version_num='0001_initial'"))
        init_db(self.engine)
        with Session(self.engine) as session:
            task = session.get(Task, 1)
            self.assertEqual((task.title, task.completed_work_minutes), ('Сохранить', 0))


if __name__ == '__main__':
    unittest.main()
