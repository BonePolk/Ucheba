"""Локальные тесты новой адаптивной Ameba: периоды, ёмкость, отдых, персонализация, стабильность."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
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

NOW = '2026-11-16T08:00:00+00:00'  # Понедельник


def empty_week(tz='UTC', events=None):
    return {'timezone': tz, 'days': [[] for _ in range(7)], 'events': [], 'calendar_events': events or []}


def make_plan(task_id, work_min, work_max=None, policy='soft', effort='medium', importance='medium',
              splittable=True, available_from=None, depends_on=None, steps=None):
    work_max = work_max if work_max is not None else work_min
    return {
        'task_id': task_id,
        'available_from': available_from,
        'depends_on': depends_on or [],
        'deadline_policy': policy,
        'importance': importance,
        'effort': effort,
        'splittable': splittable,
        'steps': steps or [{'title': 'Основная работа', 'min_minutes': work_min, 'max_minutes': work_max, 'wait_minutes': 0}],
        'assumptions': [],
        'expected_result': 'Задача выполнена',
        'reward': None,
        'reward_unit': '',
    }


def calc_plans(items, completed=None, profile=None, tasks=None, adjustments=None):
    return planning.calculate(
        json.dumps({'plans': items}),
        [item['task_id'] for item in items],
        completed=completed,
        profile=profile,
        tasks=tasks,
        adjustments=adjustments,
    )


def rest_args(**overrides):
    base = {
        'sleep_hours': 8,
        'preferred_daily_minutes': None,
        'prefer_free_evenings': False,
        'max_heavy_days_in_row': 2,
        'preferred_weekdays': [],
        'light_weekdays': [],
        'day_overrides': [],
        'assumption': 'Базовый режим для тестов',
    }
    return json.dumps(base | overrides)


def order_args(task_ids, keep=True, period_notes=None):
    return json.dumps({'task_ids': task_ids, 'keep_previous_plan': keep, 'period_notes': period_notes or []})


class PlanningScenarios(unittest.TestCase):
    def test_scenario_1_busy_university_week(self):
        # Пн и Вт: по 6 ч обязательных пар (09:00-12:00 и 13:00-16:00), Ср и Чт: свободные дни.
        events = []
        for d in ('2026-11-16', '2026-11-17'):
            events.append({'id': f'lec1-{d}', 'kind': 'busy', 'title': 'Лекции', 'date': d, 'start': '09:00', 'end': '12:00'})
            events.append({'id': f'lec2-{d}', 'kind': 'busy', 'title': 'Практика', 'date': d, 'start': '13:00', 'end': '16:00'})
        week = planning.with_rest(empty_week(events=events), NOW, rest_args())
        days = planning.analyze_days(week, NOW, horizon_days=7)

        # После 6 ч пар при ~5 ч физически свободного вечера реалистичная ёмкость существенно ограничена.
        self.assertGreater(days[0]['physical_free_minutes'], 200)
        self.assertLessEqual(days[0]['realistic_capacity_minutes'], 90)
        self.assertGreaterEqual(days[2]['realistic_capacity_minutes'], 240)

        plans = calc_plans([make_plan(1, 300, 360, effort='medium')])
        tasks = [{'id': 1, 'title': 'Лабораторная'}]
        schedule = planning.reorder(order_args([1]), plans, [1], week, NOW, tasks=tasks)
        alloc_by_date = {d['date']: d['allocated_minutes'] for d in schedule['daily_allocations']}
        # Основная нагрузка НЕ идёт на загруженные Пн/Вт, а уходит на свободные Ср/Чт.
        self.assertGreater(alloc_by_date['2026-11-18'] + alloc_by_date['2026-11-19'],
                           alloc_by_date['2026-11-16'] + alloc_by_date['2026-11-17'])

    def test_scenario_2_competing_deadlines(self):
        week = planning.with_rest(empty_week(), NOW, rest_args())
        plans = calc_plans([
            make_plan(1, 240, 300, policy='soft', effort='medium', splittable=True),
            make_plan(2, 120, 150, policy='finish_before', effort='high', splittable=False),
            make_plan(3, 180, 210, policy='expires', effort='medium', splittable=True),
        ])
        tasks = [
            {'id': 1, 'title': 'Большой проект без срока'},
            {'id': 2, 'title': 'Срочный тест', 'deadline': '2026-11-17T20:00:00Z'},
            {'id': 3, 'title': 'Эссе к среде', 'deadline': '2026-11-18T22:00:00Z'},
        ]
        # Порядок передан с мягкой задачей на 1 месте — алгоритм всё равно защитит конкурирующие дедлайны.
        schedule = planning.reorder(order_args([1, 2, 3]), plans, [1, 2, 3], week, NOW, tasks=tasks)
        self.assertTrue(schedule['feasible'])
        self.assertFalse([c for c in schedule['deadline_conflicts'] if c['infeasible']])
        self.assertEqual([r['remaining_work_minutes'] for r in schedule['remaining']], [0, 0, 0])
        alloc = schedule['daily_allocations']
        t2_last = max(d['date'] for d in alloc if any(t['task_id'] == 2 for t in d['tasks']))
        t3_last = max(d['date'] for d in alloc if any(t['task_id'] == 3 for t in d['tasks']))
        self.assertLessEqual(t2_last, '2026-11-17')
        self.assertLessEqual(t3_last, '2026-11-18')

    def test_scenario_3_very_large_project(self):
        week = planning.with_rest(empty_week(), NOW, rest_args())
        steps = [
            {'title': 'Изучить теорию', 'min_minutes': 240, 'max_minutes': 300, 'wait_minutes': 0},
            {'title': 'Написать код', 'min_minutes': 480, 'max_minutes': 600, 'wait_minutes': 0},
            {'title': 'Оформить отчёт', 'min_minutes': 240, 'max_minutes': 300, 'wait_minutes': 0},
        ]
        plans = calc_plans([
            make_plan(1, 960, 1200, policy='soft', effort='high', splittable=True, steps=steps),
            make_plan(2, 90, 90, policy='finish_before', effort='low', splittable=False),
        ])
        tasks = [
            {'id': 1, 'title': 'Курсовой проект'},
            {'id': 2, 'title': 'Короткий отчёт', 'deadline': '2026-11-18T18:00:00Z'},
        ]
        schedule = planning.reorder(order_args([2, 1]), plans, [1, 2], week, NOW, tasks=tasks)
        project_periods = [p for p in schedule['periods'] if any(t['task_id'] == 1 for t in p['tasks'])]
        self.assertGreaterEqual(len(project_periods), 2)
        self.assertEqual(len(schedule['task_ids']), 2)  # Не плодит отдельные подзадачи
        self.assertTrue(all(d['allocated_minutes'] <= d['realistic_capacity_minutes'] for d in schedule['daily_allocations']))
        self.assertTrue(any('теори' in t['goal'].lower() or 'код' in t['goal'].lower()
                            for p in project_periods for t in p['tasks'] if t['task_id'] == 1))

    def test_scenario_4_missed_day_replanning(self):
        week = planning.with_rest(empty_week(), NOW, rest_args())
        plans = calc_plans([make_plan(1, 420, 480)])
        tasks = [{'id': 1, 'title': 'Большая задача'}]
        yesterday_now = '2026-11-15T08:00:00+00:00'
        prev = planning.reorder(order_args([1]), plans, [1], week, yesterday_now, tasks=tasks)
        # На следующий день пользователь ничего не подтвердил (completed_work_minutes=0) — работа не сваливается целиком на сегодня.
        today_plan = planning.reorder(order_args([1]), plans, [1], week, NOW, previous=prev, tasks=tasks)
        today_alloc = today_plan['daily_allocations'][0]
        self.assertLessEqual(today_alloc['allocated_minutes'], today_alloc['plannable_minutes'])
        self.assertEqual(today_plan['remaining'][0]['remaining_work_minutes'], 0)

    def test_scenario_5_fatigue_and_recovery(self):
        week = planning.with_rest(empty_week(), NOW, rest_args(max_heavy_days_in_row=2))
        plans = calc_plans([make_plan(1, 900, 1080, effort='high', splittable=True)])
        tasks = [{'id': 1, 'title': 'Длинная подготовка'}]
        schedule = planning.reorder(order_args([1]), plans, [1], week, NOW, tasks=tasks)
        # После тяжёлых дней присутствуют облегчённые дни или периоды восстановления с соответствующими рекомендациями.
        has_recovery_or_light = any(
            p['kind'] in ('light', 'recovery', 'free') or d['allocated_minutes'] <= 150
            for p in schedule['periods'] for d in p['days_summary']
        )
        self.assertTrue(has_recovery_or_light)

    def test_scenario_6_infeasible_deadline_protects_rest(self):
        week = planning.with_rest(empty_week(), NOW, rest_args())
        plans = calc_plans([
            make_plan(1, 1500, 1800, policy='expires', effort='high'),
            make_plan(2, 90, 90, policy='finish_before', effort='low'),
        ])
        tasks = [
            {'id': 1, 'title': 'Невозможный диплом за 2 дня', 'deadline': '2026-11-17T20:00:00Z'},
            {'id': 2, 'title': 'Реалистичная задача', 'deadline': '2026-11-17T20:00:00Z'},
        ]
        schedule = planning.reorder(order_args([1, 2]), plans, [1, 2], week, NOW, tasks=tasks)
        self.assertFalse(schedule['feasible'])
        infeasible = [c for c in schedule['deadline_conflicts'] if c['infeasible']]
        self.assertEqual([c['task_id'] for c in infeasible], [1])
        self.assertGreater(infeasible[0]['deficit_minutes'], 0)
        self.assertTrue(infeasible[0]['reason'])
        self.assertEqual(infeasible[0]['options'], [])
        # Сон/отдых и достижимая задача 2 защищены.
        self.assertTrue(all(d['allocated_minutes'] <= d['realistic_capacity_minutes'] for d in schedule['daily_allocations']))
        t2_assigned = sum(t['minutes'] for d in schedule['daily_allocations'] for t in d['tasks'] if t['task_id'] == 2)
        self.assertEqual(t2_assigned, 90)

    def test_scenario_7_personalization_adaptation(self):
        # Одно наблюдение меняет прогноз минимально (консервативное сглаживание).
        single = planning.derive_adaptation({'observations': [
            {'outcome': 'done', 'planned_minutes': 60, 'actual_minutes': 120, 'category': 'Программирование', 'effort': 'high', 'busy_day': True, 'weekday': 0},
        ]})
        self.assertLess(single['global_factor'], 1.25)

        # Серия подтверждённых наблюдений адаптирует оценки категории, ёмкость загруженных дней и резерв.
        series = [
            {'outcome': 'done', 'planned_minutes': 100, 'actual_minutes': 170, 'category': 'Программирование', 'effort': 'high', 'busy_day': True, 'weekday': 0},
            {'outcome': 'done', 'planned_minutes': 120, 'actual_minutes': 190, 'category': 'Программирование', 'effort': 'high', 'busy_day': False, 'weekday': 5},
            {'outcome': 'too_hard', 'planned_minutes': 90, 'actual_minutes': 140, 'category': 'Программирование', 'effort': 'high', 'busy_day': True, 'weekday': 0},
            {'outcome': 'missed', 'planned_minutes': 90, 'actual_minutes': None, 'category': 'Программирование', 'effort': 'medium', 'busy_day': True, 'weekday': 1},
            {'outcome': 'done', 'planned_minutes': 60, 'actual_minutes': 60, 'category': 'Чтение', 'effort': 'low', 'busy_day': False, 'weekday': 5},
        ]
        profile = {'observations': series}
        adapt = planning.derive_adaptation(profile)
        self.assertGreater(adapt['category_factors']['программирование'], 1.2)
        self.assertLess(adapt['busy_day_factor'], 0.92)
        self.assertGreater(adapt['reserve_ratio'], 0.24)

        plans = calc_plans(
            [make_plan(1, 100, 100, effort='high'), make_plan(2, 100, 100, effort='low')],
            profile=profile,
            tasks=[{'id': 1, 'title': 'Код', 'category': 'Программирование'}, {'id': 2, 'title': 'Статья', 'category': 'Чтение'}],
        )
        self.assertGreater(plans[1]['work_max_minutes'], plans[2]['work_max_minutes'])

        # Явное предпочтение пользователя перекрывает статистический потолок.
        explicit = planning.derive_adaptation({'preferences': {'preferred_daily_minutes': 240, 'reserve_ratio': 0.2}, 'observations': series})
        self.assertEqual(explicit['comfortable_daily_minutes'], 240)
        self.assertEqual(explicit['reserve_ratio'], 0.2)

    def test_scenario_8_calendar_changes_and_special_events(self):
        # 1. Событие на весь день с маркером дедлайна НЕ блокирует день.
        # 2. Пересекающиеся события не удваиваются.
        # 3. available_from и depends_on соблюдаются строго.
        events = [
            {'id': 'dl-marker', 'kind': 'busy', 'title': 'Дедлайн отчёта', 'date': '2026-11-16', 'start': '00:00', 'end': '24:00'},
            {'id': 'm1', 'kind': 'busy', 'title': 'Пара 1', 'date': '2026-11-17', 'start': '10:00', 'end': '12:00'},
            {'id': 'm2', 'kind': 'busy', 'title': 'Совпадающая встреча', 'date': '2026-11-17', 'start': '11:00', 'end': '13:00'},
        ]
        week = planning.with_rest(empty_week(events=events), NOW, rest_args())
        days = planning.analyze_days(week, NOW, horizon_days=7)
        self.assertEqual(days[0]['busy_minutes'], 0)
        self.assertEqual(len(days[0]['markers']), 1)
        self.assertEqual(days[1]['busy_minutes'], 180)  # 10:00-13:00 объединённо, а не 240

        plans = calc_plans([
            make_plan(1, 120, 120, available_from='2026-11-19T00:00:00+00:00'),
            make_plan(2, 90, 90, depends_on=[1]),
        ])
        tasks = [{'id': 1, 'title': 'Открывается 19-го'}, {'id': 2, 'title': 'После 1'}]
        schedule = planning.reorder(order_args([1, 2]), plans, [1, 2], week, NOW, tasks=tasks)
        t1_dates = [d['date'] for d in schedule['daily_allocations'] if any(t['task_id'] == 1 for t in d['tasks'])]
        t2_dates = [d['date'] for d in schedule['daily_allocations'] if any(t['task_id'] == 2 for t in d['tasks'])]
        self.assertTrue(all(date >= '2026-11-19' for date in t1_dates))
        self.assertGreaterEqual(min(t2_dates), max(t1_dates))

        # Добавление обязательного события снижает доступную ёмкость дня.
        extra = deepcopy(week)
        extra['calendar_events'].append({'id': 'm3', 'kind': 'busy', 'title': 'Доп. семинар', 'date': '2026-11-17', 'start': '14:00', 'end': '18:00'})
        days_after = planning.analyze_days(extra, NOW, horizon_days=7)
        self.assertLess(days_after[1]['realistic_capacity_minutes'], days[1]['realistic_capacity_minutes'])

    def test_scenario_9_plan_stability(self):
        week = planning.with_rest(empty_week(), NOW, rest_args())
        tasks = [{'id': 1, 'title': 'Первая'}, {'id': 2, 'title': 'Вторая'}, {'id': 3, 'title': 'Третья'}]
        raw_plans = [make_plan(1, 180, 210), make_plan(2, 180, 210), make_plan(3, 180, 210)]
        first = planning.reorder(order_args([1, 2, 3]), calc_plans(raw_plans), [1, 2, 3], week, NOW, tasks=tasks)
        # Пользователь выполнил 45 мин по первой задаче.
        updated_plans = calc_plans(raw_plans, completed={1: 45})
        second = planning.reorder(order_args([1, 2, 3], keep=True), updated_plans, [1, 2, 3], week, NOW, previous=first, tasks=tasks)
        self.assertEqual(second['replan_mode'], 'minor_adjustment')
        first_t3_days = {d['date'] for d in first['daily_allocations'] if any(t['task_id'] == 3 for t in d['tasks'])}
        second_t3_days = {d['date'] for d in second['daily_allocations'] if any(t['task_id'] == 3 for t in d['tasks'])}
        self.assertEqual(first_t3_days, second_t3_days)

    def test_scenario_10_flexibility_no_exact_hours(self):
        week = planning.with_rest(empty_week(), NOW, rest_args())
        plans = calc_plans([make_plan(1, 180, 240)])
        schedule = planning.reorder(order_args([1]), plans, [1], week, NOW, tasks=[{'id': 1, 'title': 'ЛР'}])
        self.assertEqual(schedule['version'], 2)
        self.assertNotIn('blocks', schedule)
        self.assertTrue(schedule['periods'])
        for period in schedule['periods']:
            self.assertIn('start_date', period)
            self.assertIn('end_date', period)
            self.assertIn('hours_label', period)
            self.assertNotIn('start_time', period)
        self.assertEqual(calendar.ameba_events(datetime(2026, 11, 16).date(), datetime(2026, 11, 23).date()), [])

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

        for tool in (planning.TOOL, planning.REST_TOOL, planning.ORDER_TOOL, planning.READ_TOOL, ameba_service.CONTEXT_TOOL):
            self.assertTrue(tool['strict'])
            check_strict(tool['parameters'])


class AgentLoop(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, actions):
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

        async def fake_turn(thread_arg, prompt_input, *, adapter=None, **kwargs):
            for idx, (name, arguments) in enumerate(actions, start=1):
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
                text=json.dumps({
                    'task_ids': [1],
                    'explanation': 'Гибкий план периодов',
                    'reasons': [{
                        'task_id': 1,
                        'reason': 'Приоритет',
                        'time_estimate': '3–4 ч',
                        'effort': 'Средняя',
                        'impact': 'Готовая работа',
                        'deadline_risk': None,
                    }],
                }),
                completed=True,
                status='completed',
                error_detail=None,
                usage={'input_tokens': 0, 'output_tokens': 0},
                terminal_result=adapter.terminal_result,
            )

        with patch.object(ameba_service, 'datetime', Clock), \
             patch.object(ameba_service, 'Progress', MagicMock()), \
             patch.object(ameba_service, 'memory_tool', AsyncMock(return_value=[])), \
             patch.object(ameba_service, 'open_codex', fake_open_codex), \
             patch.object(ameba_service, 'scratch_env', fake_scratch), \
             patch.object(ameba_service, 'serve_mcp', fake_serve), \
             patch.object(ameba_service, 'run_codex_turn', side_effect=fake_turn):
            return await ameba_service.rank.__wrapped__({
                'api_key': 'synthetic', 'config': {'model': 'fixture'}, 'tasks': [{'id': 1, 'title': 'Лабораторная'}],
                'now': NOW, 'weekly_schedule': empty_week(),
            })

    async def test_agent_produces_flexible_periods_and_refresh_works_without_llm(self):
        plan_input = make_plan(1, 200, 240)
        result = await self.run_agent([
            ('read_user_memory', {}),
            ('read_current_plan', {}),
            ('plan_tasks', {'plans': [plan_input]}),
            ('plan_rest', json.loads(rest_args())),
            ('reorder_tasks', json.loads(order_args([1]))),
        ])
        self.assertEqual(result['schedule']['version'], 2)
        self.assertTrue(result['schedule']['periods'])

        with patch.object(ameba_service, 'open_codex', side_effect=AssertionError('LLM must not run')):
            refreshed = await ameba_service.refresh_plan({
                'previous_plan': result,
                'weekly_schedule': empty_week(),
                'timezone': 'UTC',
                'tasks': [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}],
                'now': NOW,
            })
        self.assertEqual(refreshed['timeline'][0]['plan']['work_max_minutes'], 180)
        self.assertEqual(refreshed['schedule']['version'], 2)

    async def test_identical_failed_action_stops_after_three_attempts(self):
        with self.assertRaisesRegex(Exception, 'трижды'):
            await self.run_agent([('read_user_memory', {}), ('read_current_plan', {})] +
                                 [('plan_tasks', {'plans': []})] * 3)


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
        old = {'run_id': 'old', 'schedule': {'version': 2, 'start_at': NOW, 'periods': []}, 'revision': 'r1'}
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
        result = {'schedule': {'version': 2, 'start_at': NOW, 'periods': []}}
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
        previous = {'task_ids': [1], 'schedule': {'version': 2, 'start_at': NOW, 'periods': []}}
        with Session(self.engine) as session:
            session.add(Task(id=1, title='Работа'))
            session.add(TaskRanking(snapshot='old', result=json.dumps(previous)))
            session.commit()
        invalid = {'task_ids': [1], 'schedule': {'version': 2, 'start_at': NOW, 'periods': [], 'timeline_error': 'invalid'}}
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'task_state', return_value=('new', [{'id': 1}], [])), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=invalid)):
            with self.assertRaisesRegex(Exception, 'Предыдущий результат сохранён'):
                asyncio.run(ameba.rank_all())
        with Session(self.engine) as session:
            self.assertEqual(json.loads(session.get(TaskRanking, 1).result), previous)

    def test_feedback_and_adjustments_persist_and_refresh(self):
        task = Task(id=1, title='Лаба', category='Программирование', status='todo')
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='cs', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

        plan = calc_plans([make_plan(1, 180, 180)])[1]
        saved_result = {
            'task_ids': [1],
            'timeline': [{'id': 1, 'title': 'Лаба', 'plan': plan}],
            'reasons': [{'task_id': 1, 'reason': 'ok', 'time_estimate': '3 ч', 'effort': 'Средняя', 'impact': 'Зачёт', 'deadline_risk': None}],
            'schedule': planning.reorder(order_args([1]), {1: plan}, [1], empty_week(), NOW, tasks=[{'id': 1, 'title': 'Лаба'}]),
        }
        with Session(self.engine) as session:
            session.add(TaskRanking(snapshot='snap', result=json.dumps(saved_result)))
            session.commit()

        async def fake_call(service, endpoint, body, timeout=None):
            return await ameba_service.refresh_plan(body)

        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True, 'model': 'm', 'auth_mode': 'api_key'}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'resolve_agent_auth', return_value=({'enabled': True, 'model': 'm', 'base_url': 'u', 'auth_mode': 'api_key'}, '')), \
             patch.object(scheduler, 'call', side_effect=fake_call), \
             patch('agent_memory.read', return_value=[]):
            status = asyncio.run(ameba.submit_feedback(1, 'partial', actual_minutes=60, period_id=saved_result['schedule']['periods'][0]['id']))
            self.assertTrue(status['feedback_saved'])
            self.assertEqual(status['observations_count'], 1)
            with Session(self.engine) as session:
                self.assertEqual(session.get(Task, 1).completed_work_minutes, 60)
                self.assertEqual(session.get(Task, 1).status, 'in_progress')

            today_iso = datetime.now(timezone.utc).date().isoformat()
            adjusted = asyncio.run(ameba.adjust_plan(day_overrides={today_iso: 'free'}))
            self.assertEqual(adjusted['manual_adjustments']['day_overrides'][today_iso], 'free')
            first_day = next(d for d in adjusted['schedule']['daily_allocations'] if d['date'] == today_iso)
            self.assertEqual(first_day['allocated_minutes'], 0)

    def test_plan_expiry_uses_local_date_not_hour(self):
        today_local = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC'}):
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 1, 'generated_date': today_local}}))
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 2, 'generated_date': yesterday}}))
            self.assertFalse(ameba.plan_expired({'schedule': {'version': 2, 'generated_date': today_local}}))

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
