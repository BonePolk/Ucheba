"""Тесты упрощённой LLM-first Ameba: объективные ограничения, /refresh, прогресс с подтверждением, БД и eval-набор."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date as calendar_date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'services/ameba'), str(ROOT / 'backend')]

import main as ameba_service
import planning
from common import calendar
from common.codex_harness import TurnOutcome
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


def task_item(task_id, remaining_minutes, reason='По приоритету и срокам', deadline_risk=None):
    return {'task_id': task_id, 'remaining_minutes': remaining_minutes, 'reason': reason, 'deadline_risk': deadline_risk}


def work_period(task_id, start_date, end_date, minutes, goal='Выполнить работу', hint=None, reason='Удобный период'):
    return {
        'kind': 'work',
        'task_id': task_id,
        'start_date': start_date,
        'end_date': end_date,
        'estimated_minutes': minutes,
        'goal': goal,
        'context_hint': hint,
        'reason': reason,
    }


def rest_period(start_date, end_date, goal='День отдыха', reason='Восстановление'):
    return {
        'kind': 'rest',
        'task_id': None,
        'start_date': start_date,
        'end_date': end_date,
        'estimated_minutes': 0,
        'goal': goal,
        'context_hint': None,
        'reason': reason,
    }


def proposal_payload(task_ids, tasks, periods, *, blocked_dates=None, preferences=None, explanation='Согласованный план.'):
    return {
        'task_ids': task_ids,
        'tasks': tasks,
        'periods': periods,
        'blocked_dates': blocked_dates or [],
        'preferences': preferences or [],
        'explanation': explanation,
    }


class ValidationTests(unittest.TestCase):
    def test_valid_flexible_plan_and_materialize(self):
        events = [
            {'id': f'uni-{d}', 'kind': 'busy', 'title': 'Университет', 'date': d, 'start': '09:00', 'end': '15:00'}
            for d in ('2026-11-23', '2026-11-25', '2026-11-27')
        ]
        tasks = [
            {'id': 42, 'title': 'Переезд', 'completed_work_minutes': 0},
            {'id': 43, 'title': 'Лабораторная', 'completed_work_minutes': 0},
        ]
        cand = proposal_payload(
            [42, 43],
            [task_item(42, 150), task_item(43, 220)],
            [
                work_period(42, '2026-11-23', '2026-11-27', 150, hint='После университета по дороге'),
                work_period(43, '2026-11-24', '2026-11-24', 220),
                rest_period('2026-11-28', '2026-11-29'),
            ],
        )
        prop, issues, days = planning.validate_proposal(cand, tasks, empty_week(events=events), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, empty_week(events=events), NOW)
        self.assertEqual(res['schedule']['version'], 3)
        self.assertTrue(res['schedule']['fully_scheduled'])
        self.assertTrue(res['schedule']['no_objective_conflicts'])
        self.assertEqual(res['schedule']['feasibility_status'], 'no_objective_conflicts')
        # Никаких искусственных дневных аллокаций; гибкий и однодневный периоды сохраняются как есть.
        self.assertEqual(res['schedule']['daily_allocations'], [])
        by_tid = {p['task_id']: p for p in res['schedule']['periods'] if p['kind'] == 'work'}
        self.assertEqual((by_tid[42]['start_date'], by_tid[42]['end_date'], by_tid[42]['minutes']), ('2026-11-23', '2026-11-27', 150))
        self.assertEqual((by_tid[43]['start_date'], by_tid[43]['end_date'], by_tid[43]['minutes']), ('2026-11-24', '2026-11-24', 220))

    def test_task_id_integrity(self):
        tasks = [{'id': 1, 'title': 'Первая'}, {'id': 2, 'title': 'Вторая'}]
        bad = proposal_payload([1, 1, 99], [task_item(1, 60)], [work_period(1, '2026-11-23', '2026-11-23', 60)])
        _, issues, _ = planning.validate_proposal(bad, tasks, empty_week(), NOW)
        codes = {i['code'] for i in issues}
        self.assertTrue({'unknown_task', 'duplicate_task', 'missing_task'} <= codes)

    def test_date_and_timezone_boundaries(self):
        tasks = [{'id': 1, 'title': 'Ночной дедлайн', 'deadline': '2026-11-24T22:00:00Z', 'completed_work_minutes': 0}]
        # 22:00Z == 2026-11-25T01:00+03:00 в Europe/Moscow.
        ok = proposal_payload([1], [task_item(1, 60)], [work_period(1, '2026-11-25', '2026-11-25', 60)])
        _, ok_issues, _ = planning.validate_proposal(ok, tasks, empty_week(tz='Europe/Moscow'), NOW)
        self.assertEqual(ok_issues, [])

        late = proposal_payload([1], [task_item(1, 60)], [work_period(1, '2026-11-26', '2026-11-26', 60)])
        _, late_issues, _ = planning.validate_proposal(late, tasks, empty_week(tz='Europe/Moscow'), NOW)
        self.assertTrue(any(i['code'] == 'after_deadline' for i in late_issues))

        bad_dates = proposal_payload(
            [1], [task_item(1, 60)],
            [work_period(1, '2026-11-20', '2026-11-20', 60), work_period(1, '2026-12-20', '2026-12-20', 60)],
        )
        _, bd_issues, _ = planning.validate_proposal(bad_dates, [{'id': 1, 'title': 'Без срока'}], empty_week(), NOW)
        self.assertEqual({i['code'] for i in bd_issues}, {'date_in_past', 'outside_horizon'})

    def test_deadline_source_consistency_and_early_deadline(self):
        # Реальные поля Task/_collect_task_state (без выдуманных available_from/deadline_type).
        early_dl_task = [{'id': 1, 'title': 'Сдача проекта', 'deadline': '2026-11-27T01:00:00+00:00', 'completed_work_minutes': 0}]
        after_dl = proposal_payload([1], [task_item(1, 90)], [work_period(1, '2026-11-28', '2026-11-28', 90)])
        _, dl_issues, _ = planning.validate_proposal(after_dl, early_dl_task, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'after_deadline' for i in dl_issues))

        # Дедлайн Пт 01:00 + гибкий период Чт–Пт допустим, но не заявляется как гарантированно выполнимый.
        thu_fri = proposal_payload([1], [task_item(1, 90)], [work_period(1, '2026-11-26', '2026-11-27', 90)])
        prop_tf, issues_tf, days_tf = planning.validate_proposal(thu_fri, early_dl_task, empty_week(), NOW)
        self.assertEqual(issues_tf, [])
        res_tf = planning.materialize_plan(prop_tf, early_dl_task, days_tf, empty_week(), NOW)
        self.assertIsNone(res_tf['schedule']['feasible'])
        self.assertTrue(res_tf['schedule']['no_objective_conflicts'])
        self.assertEqual(res_tf['schedule']['feasibility_status'], 'uncertain')
        self.assertEqual(res_tf['schedule']['deadline_conflicts'][0]['kind'], 'deadline_timing')
        self.assertFalse(res_tf['schedule']['deadline_conflicts'][0]['infeasible'])
        self.assertTrue(res_tf['schedule']['periods'][0]['deadline_risk'])

        # Завершение до дня срока не создаёт риска точного времени.
        wed_thu = proposal_payload([1], [task_item(1, 90)], [work_period(1, '2026-11-25', '2026-11-26', 90)])
        prop_wt, issues_wt, days_wt = planning.validate_proposal(wed_thu, early_dl_task, empty_week(), NOW)
        self.assertEqual(issues_wt, [])
        res_wt = planning.materialize_plan(prop_wt, early_dl_task, days_wt, empty_week(), NOW)
        self.assertEqual(res_wt['schedule']['feasibility_status'], 'no_objective_conflicts')
        self.assertEqual(res_wt['schedule']['deadline_conflicts'], [])

        # Просроченная задача не отбрасывается эвристикой в validate_proposal, а отмечается как просроченная в materialize_plan.
        overdue = [{'id': 1, 'title': 'Тест', 'deadline': '2026-11-22T18:00:00+00:00', 'completed_work_minutes': 0}]
        prop_ov, ov_issues, days_ov = planning.validate_proposal(
            proposal_payload([1], [task_item(1, 60)], [work_period(1, '2026-11-23', '2026-11-23', 60)]),
            overdue, empty_week(), NOW,
        )
        self.assertEqual(ov_issues, [])
        res_ov = planning.materialize_plan(prop_ov, overdue, days_ov, empty_week(), NOW)
        self.assertFalse(res_ov['schedule']['no_objective_conflicts'])
        self.assertEqual(res_ov['schedule']['feasibility_status'], 'conflict')

        compact = ameba_service._compact_tasks(early_dl_task, datetime.fromisoformat(NOW), ZoneInfo('UTC'))[0]
        self.assertEqual((compact['deadline_date_local'], compact['deadline_time_local'], compact['deadline_passed']), ('2026-11-27', '01:00', False))
        self.assertNotIn('available_from', compact)

    def test_blocked_dates_day_overrides_and_task_pins(self):
        tasks = [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}]
        # Гибкий период Пн–Пт со средой-отдыхом (blocked_dates + day_overrides=free) валиден.
        flex_with_rest = proposal_payload(
            [1], [task_item(1, 150)],
            [work_period(1, '2026-11-23', '2026-11-27', 150), rest_period('2026-11-25', '2026-11-25')],
            blocked_dates=['2026-11-25'],
        )
        _, flex_issues, _ = planning.validate_proposal(
            flex_with_rest, tasks, empty_week(), NOW, adjustments={'day_overrides': {'2026-11-25': 'free'}},
        )
        self.assertEqual(flex_issues, [])

        # Явная работа только в закрытый день или целиком закрытый период — реальный конфликт.
        single_blocked = proposal_payload([1], [task_item(1, 60)], [work_period(1, '2026-11-25', '2026-11-25', 60)], blocked_dates=['2026-11-25'])
        _, sb_issues, _ = planning.validate_proposal(single_blocked, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'blocked_day_violation' for i in sb_issues))

        whole_blocked = proposal_payload(
            [1], [task_item(1, 90)], [work_period(1, '2026-11-28', '2026-11-29', 90)],
            blocked_dates=['2026-11-28', '2026-11-29'],
        )
        _, wb_issues, _ = planning.validate_proposal(whole_blocked, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'blocked_day_violation' for i in wb_issues))

        # task_pins: нарушение и конфликт явного пина с blocked_dates.
        pin_adj = {'task_pins': {'1': {'mode': 'date', 'target_date': '2026-11-28', 'not_before': '2026-11-28'}}}
        wrong_pin = proposal_payload([1], [task_item(1, 60)], [work_period(1, '2026-11-24', '2026-11-24', 60)])
        _, pin_issues, _ = planning.validate_proposal(wrong_pin, tasks, empty_week(), NOW, adjustments=pin_adj)
        self.assertTrue({'violates_not_before', 'violates_task_pin'} <= {i['code'] for i in pin_issues})

        conflict_adj = {'task_pins': {'1': {'mode': 'date', 'target_date': '2026-11-25', 'not_before': None}}}
        prop_c, c_issues, c_days = planning.validate_proposal(flex_with_rest, tasks, empty_week(), NOW, adjustments=conflict_adj)
        self.assertEqual(c_issues, [])
        res_c = planning.materialize_plan(prop_c, tasks, c_days, empty_week(), NOW, adjustments=conflict_adj)
        self.assertFalse(res_c['schedule']['feasible'])
        self.assertFalse(res_c['schedule']['no_objective_conflicts'])
        self.assertTrue(any(c['kind'] == 'constraint' for c in res_c['schedule']['deadline_conflicts']))

    def test_short_tasks_no_fake_remaining(self):
        for mins in (1, 5, 9):
            self.assertEqual(planning.format_hours(mins), f'{mins} мин')
            tasks = [{'id': 1, 'title': 'Короткая', 'completed_work_minutes': 0}]
            cand = proposal_payload([1], [task_item(1, mins)], [work_period(1, '2026-11-23', '2026-11-23', mins)])
            prop, issues, days = planning.validate_proposal(cand, tasks, empty_week(), NOW)
            self.assertEqual(issues, [])
            res = planning.materialize_plan(prop, tasks, days, empty_week(), NOW)
            self.assertEqual(res['schedule']['remaining_minutes'], 0)
            self.assertTrue(res['schedule']['fully_scheduled'])

        self.assertEqual(planning.format_hours(0), 'выполнено')
        done_cand = proposal_payload([1], [task_item(1, 0)], [])
        prop0, issues0, days0 = planning.validate_proposal(done_cand, [{'id': 1, 'title': 'Готово'}], empty_week(), NOW)
        self.assertEqual(issues0, [])
        res0 = planning.materialize_plan(prop0, [{'id': 1, 'title': 'Готово'}], days0, empty_week(), NOW)
        self.assertEqual(res0['schedule']['remaining_minutes'], 0)

    def test_strict_openai_tool_schemas(self):
        def check_strict(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    self.assertFalse(node.get('additionalProperties', True))
                    self.assertEqual(set(node.get('properties', {})), set(node.get('required', [])))
                for value in node.values():
                    check_strict(value)
            elif isinstance(node, list):
                for item in node:
                    check_strict(item)

        for tool in ameba_service.AMEBA_TOOLS:
            self.assertTrue(tool['strict'])
            check_strict(tool['parameters'])

    def test_refresh_without_llm(self):
        tasks = [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}]
        cand = proposal_payload(
            [1], [task_item(1, 240)], [work_period(1, '2026-11-23', '2026-11-27', 240)],
            blocked_dates=['2026-11-29'],
        )
        prop, issues, days = planning.validate_proposal(cand, tasks, empty_week(), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, empty_week(), NOW)

        refreshed = planning.refresh_schedule(res, [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 60}], empty_week(), NOW)
        self.assertEqual(refreshed['timeline'][0]['plan']['work_max_minutes'], 180)
        self.assertEqual(refreshed['schedule']['periods'][0]['minutes'], 180)

        # Явный сдвиг «+1 день» на гибком периоде Пн–Пт реально сдвигает границы в Вт–Сб и не дублируется повторно.
        shift_plus = {'task_pins': {'1': {'mode': 'shift', 'target_date': '2026-11-24', 'not_before': '2026-11-24'}}}
        shifted = planning.refresh_schedule(res, tasks, empty_week(), NOW, adjustments=shift_plus)
        p_shifted = shifted['schedule']['periods'][0]
        self.assertEqual((p_shifted['start_date'], p_shifted['end_date']), ('2026-11-24', '2026-11-28'))
        again = planning.refresh_schedule(shifted, tasks, empty_week(), NOW, adjustments=shift_plus)
        self.assertEqual((again['schedule']['periods'][0]['start_date'], again['schedule']['periods'][0]['end_date']), ('2026-11-24', '2026-11-28'))

        # Сдвиг назад (-1 день) возвращает Пн–Пт; ручной сдвиг сохраняется при последующем перепланировании LLM.
        back = planning.refresh_schedule(shifted, tasks, empty_week(), NOW, adjustments={'task_pins': {'1': {'mode': 'shift', 'target_date': '2026-11-23', 'not_before': None}}})
        self.assertEqual((back['schedule']['periods'][0]['start_date'], back['schedule']['periods'][0]['end_date']), ('2026-11-23', '2026-11-27'))
        _, replan_issues, _ = planning.validate_proposal(cand, tasks, empty_week(), NOW, adjustments=shift_plus)
        self.assertTrue(any(i['code'] == 'violates_not_before' for i in replan_issues))

        with self.assertRaises(ValueError):
            planning.refresh_schedule(res, tasks, empty_week(), NOW, adjustments={'task_pins': {'1': {'mode': 'date', 'target_date': '2026-11-29'}}})


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, actions, *, user_feedback='', memory_items=None, memory_mock=None, ask_answer='Подтвердить', tasks=None):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(NOW)

        codex_client = MagicMock()
        codex_client.thread_start = AsyncMock(return_value=MagicMock())

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
            for idx, (name, arguments) in enumerate(next(step_iter, []), start=1):
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
                text='done', completed=True, status='completed', error_detail=None,
                usage={'input_tokens': 0, 'output_tokens': 0}, terminal_result=adapter.terminal_result,
            )

        mem_fn = memory_mock or AsyncMock(return_value=memory_items or [])
        ask_fn = AsyncMock(return_value={'answer': ask_answer})
        with patch.object(ameba_service, 'datetime', Clock), \
             patch.object(ameba_service, 'Progress', MagicMock()), \
             patch.object(ameba_service, 'memory_request', AsyncMock(return_value=memory_items or [])), \
             patch.object(ameba_service, 'memory_tool', mem_fn), \
             patch.object(ameba_service, 'ask_user', ask_fn), \
             patch.object(ameba_service, 'open_codex', fake_open_codex), \
             patch.object(ameba_service, 'scratch_env', fake_scratch), \
             patch.object(ameba_service, 'serve_mcp', fake_serve), \
             patch.object(ameba_service, 'run_codex_turn', side_effect=fake_turn):
            return await ameba_service.rank.__wrapped__({
                'api_key': 'synthetic',
                'config': {'model': 'fixture'},
                'tasks': tasks or [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}],
                'now': NOW,
                'weekly_schedule': empty_week(),
                'user_feedback': user_feedback,
            })

    async def test_single_retry_succeeds_and_second_failure_stops(self):
        bad = proposal_payload([1], [task_item(1, 120)], [work_period(1, '2026-11-20', '2026-11-23', 120)])
        good = proposal_payload([1], [task_item(1, 120)], [work_period(1, '2026-11-23', '2026-11-24', 120)])
        res = await self.run_agent([[('propose_plan', bad), ('propose_plan', good)]])
        self.assertEqual(res['schedule']['version'], 3)
        with self.assertRaisesRegex(Exception, 'не прошёл проверку'):
            await self.run_agent([[('propose_plan', bad), ('propose_plan', bad)]])

    async def test_once_vs_persistent_preferences(self):
        mem_mock = AsyncMock(return_value=[])
        cand = proposal_payload(
            [1], [task_item(1, 120)], [work_period(1, '2026-11-28', '2026-11-28', 120)],
            blocked_dates=['2026-11-27'],
            preferences=[
                {'text': 'Пятницу оставить свободной', 'scope': 'once', 'task_id': None, 'key': None},
                {'text': 'Сложные задачи делаю в свободные дни', 'scope': 'persistent', 'task_id': None, 'key': 'free_days'},
            ],
        )
        res = await self.run_agent([[('propose_plan', cand)]], user_feedback='Пятницу оставь свободной', memory_mock=mem_mock)
        mem_mock.assert_not_called()
        self.assertEqual(res['memory_saved_count'], 0)
        self.assertEqual(res['persistent_preferences'], [{'key': 'free_days', 'text': 'Сложные задачи делаю в свободные дни'}])

    async def test_record_progress_requires_user_confirmation(self):
        self.assertNotIn('progress_updates', planning.PlanProposal.model_fields)
        actions = [[
            ('record_progress', {'task_id': 1, 'add_minutes': 30, 'reason': 'Сделаны первые 30 минут'}),
            ('record_progress', {'task_id': 1, 'add_minutes': 45, 'reason': 'Сделаны ещё 45 минут'}),
            ('propose_plan', proposal_payload([1], [task_item(1, 120)], [work_period(1, '2026-11-24', '2026-11-24', 120)])),
        ]]
        confirmed = await self.run_agent(actions, user_feedback='Я сделал 30 и потом ещё 45 минут', ask_answer='Подтвердить')
        self.assertEqual(confirmed['confirmed_progress'], [
            {'task_id': 1, 'add_minutes': 30, 'base_completed_minutes': 0, 'confirmed_by_user': True},
            {'task_id': 1, 'add_minutes': 45, 'base_completed_minutes': 30, 'confirmed_by_user': True},
        ])
        self.assertEqual(confirmed['plans'][1]['completed_work_minutes'], 75)

        cancelled = await self.run_agent(actions, user_feedback='Я сделал 30 и потом ещё 45 минут', ask_answer='Отменить')
        self.assertEqual(cancelled['confirmed_progress'], [])
        self.assertEqual(cancelled['plans'][1]['completed_work_minutes'], 0)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_engine(f'sqlite:///{self.directory.name}/test.db')
        init_db(self.engine)
        self.owner = current_user_id.set(1)
        self.patches = [
            patch('common.database.get_engine', return_value=self.engine),
            patch('common.settings.get_engine', return_value=self.engine),
            patch.object(ameba, 'get_engine', return_value=self.engine),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        current_user_id.reset(self.owner)
        self.engine.dispose()
        self.directory.cleanup()

    def _seed_task(self, title='Лабораторная', completed=0):
        task = Task(id=1, title=title, status='todo', completed_work_minutes=completed)
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

    def test_failed_run_and_timeline_error_preserve_previous_plan(self):
        old = {'run_id': 'old', 'schedule': {'version': 3, 'start_at': NOW, 'periods': []}, 'revision': 'r1'}
        previous = {'task_ids': [1], 'schedule': {'version': 3, 'start_at': NOW, 'periods': []}}
        with Session(self.engine) as session:
            session.add(IntegrationSetting(name='ameba_plan', config=json.dumps(old)))
            session.add(Task(id=1, title='Работа'))
            session.add(TaskRanking(snapshot='old', result=json.dumps(previous)))
            session.commit()
        with patch.object(calendar, 'read_config', return_value={'revision': 'r1'}), \
             patch.object(scheduler, 'call', AsyncMock(side_effect=RuntimeError('synthetic'))):
            with self.assertRaises(RuntimeError):
                asyncio.run(scheduler.run_ranking({}))
        self.assertEqual(calendar.read_ameba_plan()['run_id'], 'old')

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

    def test_plan_and_ranking_rollback_together_and_late_run_is_rejected(self):
        calendar.reset_ameba_plan('new')
        result = {'schedule': {'version': 3, 'start_at': NOW, 'periods': []}}
        with Session(self.engine) as session:
            session.add(TaskRanking(result='new'))
            calendar.save_ameba_plan('new', result, 'r2', session)
            session.rollback()
        self.assertNotIn('schedule', calendar.read_ameba_plan())
        with Session(self.engine) as session:
            with self.assertRaises(ValueError):
                calendar.save_ameba_plan('late', result, 'r2', session)

    def test_backend_ignores_plan_progress_and_applies_only_confirmed(self):
        self._seed_task(title='Переезд', completed=0)
        cand = proposal_payload(
            [1], [task_item(1, 120)], [work_period(1, '2026-11-23', '2026-11-25', 120)],
            preferences=[{'text': 'Перевозить после пар', 'scope': 'task', 'task_id': 1, 'key': None}],
        )
        prop, issues, days = planning.validate_proposal(cand, [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}], empty_week(), NOW)
        self.assertEqual(issues, [])
        mat = planning.materialize_plan(prop, [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}], days, empty_week(), NOW)

        # Несколько отдельно подтверждённых инкрементов по одной задаче применяются последовательно; replay/устаревшие отклоняются.
        res1 = mat | {
            'plan_run_id': 'run1',
            'calendar_revision': 'r1',
            'progress_updates': [{'task_id': 1, 'operation': 'set', 'minutes': 999}],
            'confirmed_progress': [
                {'task_id': 1, 'add_minutes': 30, 'base_completed_minutes': 0, 'confirmed_by_user': True},
                {'task_id': 1, 'add_minutes': 45, 'base_completed_minutes': 30, 'confirmed_by_user': True},
                {'task_id': 1, 'add_minutes': 20, 'base_completed_minutes': 75, 'confirmed_by_user': False},
            ],
        }
        calendar.reset_ameba_plan('run1')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=res1)), \
             patch('agent_memory.read', return_value=[]):
            out = asyncio.run(ameba.rank_all())
        self.assertEqual(out['progress_updated_count'], 2)
        self.assertEqual(out['task_notes_saved_count'], 1)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 75)
            self.assertEqual(session.get(Task, 1).status, 'in_progress')

        # Повтор (replay) одинаковой записи или устаревшая база вызывает 409 без порчи БД.
        replay = mat | {
            'plan_run_id': 'run_replay',
            'calendar_revision': 'r1',
            'confirmed_progress': [
                {'task_id': 1, 'add_minutes': 15, 'base_completed_minutes': 75, 'confirmed_by_user': True},
                {'task_id': 1, 'add_minutes': 15, 'base_completed_minutes': 75, 'confirmed_by_user': True},
            ],
        }
        calendar.reset_ameba_plan('run_replay')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=replay)), \
             patch('agent_memory.read', return_value=[]):
            with self.assertRaisesRegex(Exception, 'Прогресс задачи изменился'):
                asyncio.run(ameba.rank_all(force=True))
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 75)

    def test_persistent_memory_committed_only_after_db_commit(self):
        self._seed_task(title='Учёба', completed=0)
        cand = proposal_payload(
            [1], [task_item(1, 120)], [work_period(1, '2026-11-23', '2026-11-23', 120)],
            preferences=[{'text': 'Сложные задачи делаю с утра', 'scope': 'persistent', 'task_id': None, 'key': 'ameba.morning'}],
        )
        prop, _, days = planning.validate_proposal(cand, [{'id': 1, 'title': 'Учёба', 'completed_work_minutes': 0}], empty_week(), NOW)
        mat = planning.materialize_plan(prop, [{'id': 1, 'title': 'Учёба', 'completed_work_minutes': 0}], days, empty_week(), NOW)
        saved_facts = []

        calendar.reset_ameba_plan('other')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=json.loads(json.dumps(mat)) | {'plan_run_id': 'stale', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]), \
             patch('agent_memory.save', side_effect=lambda f, a: saved_facts.append((f, a)) or f):
            with self.assertRaises(ValueError):
                asyncio.run(ameba.rank_all())
        self.assertEqual(saved_facts, [])

        calendar.reset_ameba_plan('good')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=mat | {'plan_run_id': 'good', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]), \
             patch('agent_memory.save', side_effect=lambda f, a: saved_facts.append((f, a)) or f):
            out = asyncio.run(ameba.rank_all())
        self.assertEqual(out['memory_saved_count'], 1)

    def test_plan_expiry_and_migration(self):
        today_local = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC'}):
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 2, 'generated_date': today_local}}))
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 3, 'generated_date': yesterday}}))
            self.assertFalse(ameba.plan_expired({'schedule': {'version': 3, 'generated_date': today_local}}))

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


# --- Набор сценариев для оценки реальных решений LLM (без привязки к одному JSON) ---

EVAL_SCENARIOS = [
    {
        'name': 'move_after_university',
        'events': [{'id': f'uni-{d}', 'kind': 'busy', 'title': 'Университет', 'date': d, 'start': '09:00', 'end': '15:00'}
                   for d in ('2026-11-23', '2026-11-25', '2026-11-27')],
        'tasks': [
            {'id': 42, 'title': 'Переезд', 'completed_work_minutes': 0, 'owner_notes': 'Квартира по дороге из университета, можно перевозить постепенно'},
            {'id': 43, 'title': 'Лабораторная', 'deadline': '2026-11-29T20:00:00Z', 'completed_work_minutes': 0},
        ],
        'prefer_overlap_dates': {42: ('2026-11-23', '2026-11-25', '2026-11-27')},
        'require_rest': True,
    },
    {
        'name': 'heavy_academic_workload',
        'events': [{'id': 'u1', 'kind': 'busy', 'title': 'Пары', 'date': '2026-11-23', 'start': '09:00', 'end': '17:00'}],
        'tasks': [{'id': 1, 'title': 'Сложный проект', 'deadline': '2026-11-26T20:00:00Z', 'completed_work_minutes': 0}],
        'avoid_heavy_on_date': {1: '2026-11-23'},
    },
    {
        'name': 'insufficient_time',
        'events': [],
        'tasks': [{'id': 1, 'title': 'Огромный диплом', 'deadline': '2026-11-24T12:00:00Z', 'completed_work_minutes': 0, 'owner_notes': 'Около 30 часов работы'}],
        'expect_risk_or_infeasible': True,
    },
    {
        'name': 'temporary_preference',
        'events': [],
        'tasks': [{'id': 1, 'title': 'Отчёт', 'completed_work_minutes': 0}],
        'user_feedback': 'Завтра ничего не планируй',
        'forbidden_dates': ('2026-11-24',),
        'expect_no_persistent_memory': True,
    },
    {
        'name': 'unknown_route',
        'events': [{'id': 'u1', 'kind': 'busy', 'title': 'Университет', 'date': '2026-11-23', 'start': '09:00', 'end': '15:00'}],
        'tasks': [{'id': 1, 'title': 'Забрать посылку', 'completed_work_minutes': 0}],
        'forbid_unconditional_route_claim': True,
    },
    {
        'name': 'partial_confirmed_progress',
        'events': [],
        'tasks': [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 120, 'owner_notes': 'Всего около 4 часов'}],
        'max_remaining_for_task': {1: 180},
    },
]


def evaluate_plan_properties(scenario: dict, result: dict) -> list[str]:
    failures: list[str] = []
    sched = (result or {}).get('schedule') or {}
    if not sched.get('valid'):
        failures.append('План не валиден')
    periods = sched.get('periods') or []
    forbidden = set(scenario.get('forbidden_dates') or ())
    has_rest = any(p.get('kind') == 'rest' for p in periods)
    if scenario.get('require_rest') and not has_rest:
        failures.append('Нет периода отдыха')
    if scenario.get('expect_no_persistent_memory') and (result.get('persistent_preferences') or []):
        failures.append('Разовое пожелание попало в persistent_preferences')
    if scenario.get('expect_risk_or_infeasible') and sched.get('feasibility_status') == 'no_objective_conflicts' and not sched.get('deadline_conflicts'):
        failures.append('Не отражён риск нехватки времени')
    for p in periods:
        if p.get('kind') != 'work':
            continue
        start_d, end_d = calendar_date.fromisoformat(p['start_date']), calendar_date.fromisoformat(p['end_date'])
        if forbidden and all((start_d + timedelta(days=o)).isoformat() in forbidden for o in range((end_d - start_d).days + 1)):
            failures.append('Работа целиком приходится на запрещённые дни')
        if scenario.get('forbid_unconditional_route_claim'):
            hint = (p.get('context_hint') or '').casefold()
            if 'по дороге' in hint and 'если' not in hint:
                failures.append('Выдуман неподтверждённый маршрут в context_hint')
    for tid, max_rem in (scenario.get('max_remaining_for_task') or {}).items():
        plan = (result.get('plans') or {}).get(tid) or {}
        if int(plan.get('remaining_minutes', 0)) > max_rem:
            failures.append(f'Не учтён подтверждённый прогресс задачи {tid}')
    return failures


class EvalPropertyTests(unittest.TestCase):
    def test_eval_property_checker(self):
        sc = EVAL_SCENARIOS[0]
        prop, _, days = planning.validate_proposal(
            proposal_payload([42, 43], [task_item(42, 150), task_item(43, 180)], [
                work_period(42, '2026-11-23', '2026-11-27', 150, hint='После университета по дороге'),
                work_period(43, '2026-11-24', '2026-11-26', 180),
                rest_period('2026-11-28', '2026-11-29'),
            ]),
            sc['tasks'], empty_week(events=sc['events']), NOW,
        )
        res = planning.materialize_plan(prop, sc['tasks'], days, empty_week(events=sc['events']), NOW)
        self.assertEqual(evaluate_plan_properties(sc, res), [])


if __name__ == '__main__':
    unittest.main()
