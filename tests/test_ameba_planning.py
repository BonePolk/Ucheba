"""Локальные регрессии Ameba: синтетические задачи, временная SQLite, без модели."""
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

import blocks
import planning
import main as ameba_service
from common import calendar
from common.database import init_db
from common.identity import current_user_id
from common.models.integration import IntegrationSetting, TaskRanking
from common.models.task import Task
from common.schedule import default_week
from integrations import ameba, scheduler
from sqlalchemy import create_engine, text
from sqlmodel import Session

NOW = '2026-11-12T08:00:00+00:00'


def estimate(work=3000, policy='soft', completed=0):
    return planning.calculate(json.dumps({'plans': [{
        'task_id': 1, 'available_from': None, 'deadline_policy': policy, 'focus_minutes': 10,
        'steps': [{'title': 'Работа', 'min_minutes': work, 'max_minutes': work, 'wait_minutes': 0}],
        'assumptions': [], 'effort': 'medium', 'expected_result': 'Готово', 'reward': None, 'reward_unit': '',
    }]}), [1], {1: completed})


class Planning(unittest.TestCase):
    def setUp(self):
        self.week = default_week()
        self.order = json.dumps({'task_ids': [1], 'keep_previous_times': True})
        self.draft = {'start_at': NOW, 'timezone': 'UTC', 'blocks': []}

    def test_partial_work_is_preserved(self):
        schedule = planning.reorder(self.order, estimate(), [1], self.week, NOW)
        self.assertIsNone(schedule['timeline_error'])
        self.assertGreater(schedule['remaining'][0]['remaining_work_minutes'], 0)
        self.assertTrue(any(b['kind'] == 'task' for b in schedule['blocks']))

    def test_week_overflow_keeps_blocks_and_remaining(self):
        schedule = planning.reorder(self.order, estimate(), [1], self.week, NOW)
        self.assertIsNone(schedule['timeline_error'])
        self.assertTrue(any(b['kind'] == 'task' for b in schedule['blocks']))
        self.assertGreater(schedule['remaining'][0]['remaining_work_minutes'], 0)

    def test_incomplete_hard_deadline_without_blocks_is_reported(self):
        schedule = planning.reorder(self.order, estimate(policy='finish_before'), [1], self.week, NOW,
                                    tasks=[{'id': 1, 'title': 'Обязательная', 'deadline': '2026-11-12T10:00:00Z'}])
        self.assertTrue(schedule['deadline_conflicts'][0]['infeasible'])

    def test_partial_hard_deadline_keeps_useful_work(self):
        plans = estimate(policy='finish_before')
        schedule = planning.reorder(self.order, plans, [1], self.week, NOW,
                                    tasks=[{'id': 1, 'title': 'Обязательная', 'deadline': '2026-11-12T10:00:00Z'}])
        self.assertTrue(any(b['kind'] == 'task' for b in schedule['blocks']))
        self.assertEqual(len(schedule['deadline_conflicts']), 1)

    def test_automatic_blocks_never_extend_past_hard_deadline(self):
        schedule = planning.reorder(self.order, estimate(policy='expires'), [1], self.week, NOW,
                                    tasks=[{'id': 1, 'title': 'Сдача', 'deadline': '2026-11-12T10:00:00Z'}])
        end = max(b['start'] + b['minutes'] for b in schedule['blocks'] if b['kind'] == 'task')
        self.assertLessEqual(end, 120)
        self.assertTrue(schedule['deadline_conflicts'])

    def test_permanent_overlap_is_allowed_but_temporary_conflict_is_not(self):
        occupied = [{'id': str(i), 'kind': 'busy', 'start': i * 10, 'minutes': 60, 'permanent': True} for i in range(2)]
        planning.validate_blocks(occupied)
        with self.assertRaises(ValueError):
            planning.validate_blocks(occupied + [{'kind': 'task', 'start': 20, 'minutes': 10}])

    def test_rest_required_on_each_date(self):
        week = default_week()
        week['days'][4] = [slot for slot in week['days'][4] if slot['kind'] != 'meal']
        with self.assertRaisesRegex(ValueError, '2026-11-13'):
            planning.with_rest(week, NOW, json.dumps({'patterns': [], 'assumption': 'Из календаря', 'load_limits': None}))

    def test_confirmed_work_is_subtracted_once(self):
        plan = estimate(200, completed=80)[1]
        self.assertEqual(plan['work_max_minutes'], 120)
        self.assertEqual(plan['max_minutes'], 130)
        self.assertEqual(estimate(200, completed=200)[1]['max_minutes'], 0)

    def test_past_blocks_are_not_confirmed_work(self):
        previous = deepcopy(self.draft)
        previous['blocks'] = [{'id': 'past', 'kind': 'task', 'task_id': 1, 'start': 0, 'minutes': 60}]
        draft = blocks.initial(previous, [1], '2026-11-12T10:00:00Z', 'UTC')
        self.assertEqual(blocks.remaining(draft, estimate(200), [1])[0]['remaining_work_minutes'], 200)

    def test_free_windows_subtract_tasks(self):
        placed = [{'kind': 'task', 'start': 60, 'minutes': 60}]
        windows = planning.free_windows(self.week, NOW, placed)
        first = [w for w in windows if w['date'] == '2026-11-12'][0]
        self.assertEqual((first['start'], first['end']), ('10:00', '12:00'))

    def test_future_block_keeps_id_and_absolute_time(self):
        previous = deepcopy(self.draft)
        previous['blocks'] = [{'id': 'keep', 'kind': 'task', 'task_id': 1, 'start': 120, 'minutes': 60}]
        draft = blocks.initial(previous, [1], '2026-11-12T09:00:00Z', 'UTC')
        self.assertEqual(draft['blocks'][0]['id'], 'keep')
        origin = datetime.fromisoformat(draft['start_at'])
        self.assertEqual(origin + timedelta(minutes=draft['blocks'][0]['start']), datetime.fromisoformat(NOW) + timedelta(minutes=120))

    def test_refresh_uses_saved_estimates_without_model(self):
        previous = {'task_ids': [1], 'timeline': [{'id': 1, 'title': 'Работа', 'plan': estimate()[1]}],
                    'schedule': self.draft, 'reasons': [{'task_id': 1, 'reason': 'Исходный приоритет'}]}
        with patch.object(ameba_service, 'AsyncOpenAI', side_effect=AssertionError('LLM must not run')):
            result = asyncio.run(ameba_service.refresh_plan({'previous_plan': previous, 'weekly_schedule': self.week,
                                                            'timezone': 'UTC', 'tasks': [{'id': 1}]}))
        self.assertEqual(result['reasons'], previous['reasons'])
        self.assertEqual(result['task_ids'], [1])
        self.assertGreater(result['schedule']['remaining'][0]['remaining_work_minutes'], 0)
        self.assertTrue(result['schedule']['blocks'])

    def test_refresh_preserves_future_block_and_replans_missed_work(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(NOW)
        previous = {'task_ids': [1], 'timeline': [{'id': 1, 'title': 'Работа', 'plan': estimate(200)[1]}],
                    'schedule': self.draft | {'blocks': [
                        {'id': 'missed', 'kind': 'task', 'task_id': 1, 'start': -120, 'minutes': 60, 'focus_minutes': 10},
                        {'id': 'keep', 'kind': 'task', 'task_id': 1, 'start': 120, 'minutes': 60, 'focus_minutes': 10}]}}
        with patch.object(ameba_service, 'datetime', Clock):
            result = asyncio.run(ameba_service.refresh_plan({'previous_plan': previous, 'weekly_schedule': self.week,
                                                            'timezone': 'UTC', 'tasks': [{'id': 1}]}))
        schedule = result['schedule']
        placed = [b for b in schedule['blocks'] if b['kind'] == 'task']
        kept = next(b for b in placed if b['id'] == 'keep')
        self.assertEqual(datetime.fromisoformat(schedule['start_at']) + timedelta(minutes=kept['start']),
                         datetime.fromisoformat(NOW) + timedelta(minutes=120))
        self.assertEqual(sum(b['minutes'] - b['focus_minutes'] for b in placed), 200)
        self.assertFalse(any(b['id'] == 'missed' for b in placed))

    def test_shared_hard_deadline_capacity(self):
        plans = estimate(50, policy='finish_before')
        plans[2] = plans[1] | {'task_id': 2}
        result = planning.reorder(json.dumps({'task_ids': [1, 2], 'keep_previous_times': True}), plans, [1, 2], self.week, NOW,
            tasks=[{'id': i, 'title': str(i), 'deadline': '2026-11-12T10:00:00Z'} for i in (1, 2)])
        self.assertTrue(all(item['infeasible'] for item in result['deadline_conflicts']))

    def test_user_session_limit_overrides_default(self):
        self.week['load_limits'] = {'max_session_minutes': 130, 'break_minutes': 5, 'max_daily_minutes': 360}
        schedule = planning.reorder(self.order, estimate(120), [1], self.week, NOW)
        self.assertEqual([b['minutes'] for b in schedule['blocks'] if b['kind'] == 'task'], [130])

    def test_several_hard_deadlines_precede_large_low_urgency_task(self):
        plans = estimate(1500)
        plans[2] = estimate(50, 'finish_before')[1] | {'task_id': 2}
        plans[3] = estimate(80, 'finish_before')[1] | {'task_id': 3}
        schedule = planning.reorder(json.dumps({'task_ids': [1, 3, 2], 'keep_previous_times': True}), plans, [1, 2, 3], self.week, NOW,
            tasks=[{'id': 1, 'title': 'Большая'}, {'id': 2, 'title': 'Первый срок', 'deadline': '2026-11-12T10:00:00Z'},
                   {'id': 3, 'title': 'Второй срок', 'deadline': '2026-11-12T12:00:00Z'}])
        self.assertFalse(schedule['deadline_conflicts'])
        self.assertEqual([b['task_id'] for b in schedule['blocks'] if b['kind'] == 'task'][:2], [2, 3])

    def test_long_task_sessions_daily_budget_and_balance(self):
        plans = estimate(900)
        plans[2] = estimate(50)[1] | {'task_id': 2}
        self.week['load_limits'] = {'max_session_minutes': 80, 'break_minutes': 20, 'max_daily_minutes': 180}
        schedule = planning.reorder(json.dumps({'task_ids': [1, 2], 'keep_previous_times': True}), plans, [1, 2], self.week, NOW)
        placed = [b for b in schedule['blocks'] if b['kind'] == 'task']
        daily = {}
        for block in placed:
            self.assertLessEqual(block['minutes'], 80)
            day = block['start'] // 1440
            daily[day] = daily.get(day, 0) + block['minutes']
        self.assertTrue(all(value <= 180 for value in daily.values()))
        self.assertGreaterEqual(len(daily), 5)
        self.assertLess(next(b['start'] for b in placed if b['task_id'] == 2), max(b['start'] for b in placed if b['task_id'] == 1))
        planning.validate_load(placed, self.week, NOW)

    def test_replanning_good_plan_preserves_every_future_block(self):
        plans = estimate(300)
        first = planning.reorder(self.order, plans, [1], self.week, NOW)
        second = planning.reorder(self.order, plans, [1], self.week, NOW, previous=first)
        self.assertEqual(first['blocks'], second['blocks'])

    def test_calendar_change_moves_only_conflicting_block(self):
        plans = estimate(300)
        first = planning.reorder(self.order, plans, [1], self.week, NOW)
        old_tasks = [b for b in first['blocks'] if b['kind'] == 'task']
        blocked = old_tasks[0]
        changed = deepcopy(self.week)
        changed['calendar_events'] = [{'id': 'new-meeting', 'kind': 'busy', 'title': 'Занято', 'date': '2026-11-12', 'start': '09:00', 'end': '10:30'}]
        second = planning.reorder(self.order, plans, [1], changed, NOW, previous=first)
        ids = {b['id'] for b in second['blocks'] if b['kind'] == 'task'}
        self.assertNotIn(blocked['id'], ids)
        self.assertTrue(all(b['id'] in ids for b in old_tasks[1:]))
        self.assertEqual(second['remaining'][0]['remaining_work_minutes'], 0)

    def test_new_urgent_task_releases_soft_blocks_but_keeps_later_days(self):
        plans = estimate(80)
        plans[2] = estimate(50, 'finish_before')[1] | {'task_id': 2}
        plans[3] = estimate(40)[1] | {'task_id': 3}
        previous = self.draft | {'blocks': [
            {'id': 'soft-blocker', 'kind': 'task', 'task_id': 1, 'start': 60, 'minutes': 90, 'focus_minutes': 10},
            {'id': 'unaffected', 'kind': 'task', 'task_id': 3, 'start': 60 + 1440 * 2, 'minutes': 50, 'focus_minutes': 10}]}
        schedule = planning.reorder(json.dumps({'task_ids': [1, 2, 3], 'keep_previous_times': True}), plans, [1, 2, 3], self.week, NOW,
            previous=previous, tasks=[{'id': 1}, {'id': 2, 'title': 'Срочная', 'deadline': '2026-11-12T10:00:00Z'}, {'id': 3}])
        self.assertFalse(schedule['deadline_conflicts'])
        ids = {b['id'] for b in schedule['blocks'] if b['kind'] == 'task'}
        self.assertIn('unaffected', ids)
        self.assertNotIn('soft-blocker', ids)

    def test_impossible_large_task_does_not_destroy_achievable_deadline(self):
        plans = estimate(5000, 'finish_before')
        plans[2] = estimate(50, 'finish_before')[1] | {'task_id': 2}
        result = planning.reorder(json.dumps({'task_ids': [1, 2], 'keep_previous_times': True}), plans, [1, 2], self.week, NOW,
            tasks=[{'id': 1, 'title': 'Не помещается', 'deadline': '2026-11-12T12:00:00Z'},
                   {'id': 2, 'title': 'Можно успеть', 'deadline': '2026-11-12T12:00:00Z'}])
        self.assertEqual([c['task_id'] for c in result['deadline_conflicts']], [1])
        self.assertTrue(any(b['task_id'] == 1 for b in result['blocks'] if b['kind'] == 'task'))

    def test_day_extension_requires_exact_owner_confirmation(self):
        week = planning.with_rest(self.week, NOW)
        arguments = json.dumps({'date': '2026-11-12', 'slots': [
            {'kind': 'work', 'title': 'Дополнительное время', 'start': '17:00', 'end': '18:00', 'task_id': None}], 'reason': 'Перед сроком'})
        unchanged, result = planning.plan_day(arguments, week, [1], NOW)
        self.assertEqual(unchanged, week)
        self.assertEqual(result['status'], 'needs_confirmation')
        approved, result = planning.plan_day(arguments, week, [1], NOW, confirmed=True)
        self.assertTrue(any(s['start'] == '17:00' for s in planning.day_slots(approved, datetime.fromisoformat(NOW).date()) if s['kind'] == 'work'))
        self.assertIn('2026-11-12', approved['approved_work_windows'])
        unapproved = deepcopy(approved)
        unapproved['approved_work_windows'] = {}
        self.assertFalse(any(s['kind'] == 'work' for s in planning.day_slots(unapproved, datetime.fromisoformat(NOW).date())))

    def test_work_slot_over_meeting_does_not_need_extension_consent(self):
        week = planning.with_rest(self.week, NOW)
        week['calendar_events'] = [{'id': 'meeting', 'kind': 'busy', 'title': 'Занятие', 'date': '2026-11-12', 'start': '10:00', 'end': '11:00'}]
        _, result = planning.plan_day(json.dumps({'date': '2026-11-12', 'slots': [
            {'kind': 'work', 'title': 'Работа', 'start': '09:00', 'end': '12:00', 'task_id': None}], 'reason': 'Обычный режим'}), week, [1], NOW)
        self.assertNotEqual(result.get('status'), 'needs_confirmation')
        self.assertEqual(result['work_minutes'], 120)

    def test_calendar_validation_error_is_not_a_successful_empty_schedule(self):
        with patch.object(planning, 'validate_blocks', side_effect=ValueError('synthetic invalid calendar')):
            with self.assertRaisesRegex(ValueError, 'synthetic invalid calendar'):
                planning.reorder(self.order, estimate(100), [1], self.week, NOW)

    def test_refresh_and_initial_placement_are_identical(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(NOW)
        plans = estimate(400)
        previous = {'task_ids': [1], 'timeline': [{'id': 1, 'title': 'Работа', 'plan': plans[1]}]}
        first = planning.reorder(self.order, plans, [1], self.week, NOW)
        with patch.object(ameba_service, 'datetime', Clock):
            refreshed = asyncio.run(ameba_service.refresh_plan({'previous_plan': previous, 'weekly_schedule': self.week,
                                                                'tasks': [{'id': 1}], 'timezone': 'UTC'}))['schedule']
        self.assertEqual([(b['task_id'], b['start'], b['minutes']) for b in first['blocks']],
                         [(b['task_id'], b['start'], b['minutes']) for b in refreshed['blocks']])

    def test_small_task_is_not_needlessly_stretched_over_days(self):
        schedule = planning.reorder(self.order, estimate(120), [1], self.week, NOW)
        self.assertEqual(schedule['remaining'][0]['remaining_work_minutes'], 0)
        self.assertTrue(all(b['start'] < 1440 for b in schedule['blocks'] if b['kind'] == 'task'))

    def test_short_user_sessions_allow_useful_work(self):
        self.week['load_limits'] = {'max_session_minutes': 30, 'break_minutes': 5, 'max_daily_minutes': 90}
        schedule = planning.reorder(self.order, estimate(100), [1], self.week, NOW)
        self.assertEqual(schedule['remaining'][0]['remaining_work_minutes'], 0)
        self.assertTrue(all(b['minutes'] <= 30 for b in schedule['blocks'] if b['kind'] == 'task'))

    def test_control_opening_on_sixteenth_cannot_be_scheduled_on_thirteenth(self):
        now = '2026-11-13T08:00:00+00:00'
        opening = '2026-11-16T00:00:00+00:00'
        plans = estimate(100)
        plans[1]['available_from'] = opening
        schedule = planning.reorder(self.order, plans, [1], self.week, now)
        placed = [b for b in schedule['blocks'] if b['kind'] == 'task']
        self.assertTrue(placed)
        self.assertTrue(all(datetime.fromisoformat(schedule['start_at']) + timedelta(minutes=b['start']) >= datetime.fromisoformat(opening) for b in placed))

    def test_refresh_drops_previous_block_before_opening(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat('2026-11-13T08:00:00+00:00')
        opening = '2026-11-16T13:30:00+03:00'
        plans = estimate(100)
        plans[1]['available_from'] = opening
        previous = {'task_ids': [1], 'timeline': [{'id': 1, 'plan': plans[1]}],
                    'schedule': {'start_at': Clock.now().isoformat(), 'timezone': 'UTC', 'blocks': [
                        {'id': 'too-early', 'kind': 'task', 'task_id': 1, 'start': 60, 'minutes': 60, 'focus_minutes': 10}]}}
        with patch.object(ameba_service, 'datetime', Clock):
            schedule = asyncio.run(ameba_service.refresh_plan({'previous_plan': previous, 'weekly_schedule': self.week, 'tasks': [{'id': 1}]}))['schedule']
        placed = [b for b in schedule['blocks'] if b['kind'] == 'task']
        self.assertTrue(placed)
        self.assertFalse(any(b['id'] == 'too-early' for b in placed))
        self.assertTrue(all(datetime.fromisoformat(schedule['start_at']) + timedelta(minutes=b['start']) >= datetime.fromisoformat(opening) for b in placed))

    def test_deadline_before_opening_is_infeasible(self):
        plans = estimate(50, 'finish_before')
        plans[1]['available_from'] = '2026-11-16T00:00:00+00:00'
        result = planning.reorder(self.order, plans, [1], self.week, '2026-11-13T08:00:00+00:00',
                                  tasks=[{'id': 1, 'title': 'Закрытая контрольная', 'deadline': '2026-11-15T12:00:00Z'}])
        self.assertFalse(any(b['kind'] == 'task' for b in result['blocks']))
        self.assertTrue(result['deadline_conflicts'][0]['infeasible'])


class AgentLoop(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, actions):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(NOW)
        responses = [SimpleNamespace(status='completed', output=[SimpleNamespace(type='function_call',
                     name=name, arguments=json.dumps(arguments), call_id=str(i))], output_text='')
                     for i, (name, arguments) in enumerate(actions)]
        responses.append(SimpleNamespace(status='completed', output=[], output_text=json.dumps({
            'task_ids': [1], 'explanation': 'Частичный план', 'reasons': [{'task_id': 1, 'reason': 'Приоритет',
            'time_estimate': 'Оценка', 'effort': 'Средняя', 'impact': 'Результат'}]})))
        with patch.object(ameba_service, 'datetime', Clock), patch.object(ameba_service, 'Progress', MagicMock()), patch.object(ameba_service, 'AsyncOpenAI', MagicMock()), patch.object(ameba_service, 'memory_tool', AsyncMock(return_value=[])), patch.object(ameba_service, 'stream_model_response', AsyncMock(side_effect=responses)):
            return await ameba_service.rank.__wrapped__({'api_key': 'synthetic', 'config': {}, 'tasks': [{'id': 1, 'title': 'Большая'}],
                                                        'now': NOW, 'weekly_schedule': default_week()})

    async def test_agent_accepts_partial_final_result(self):
        plan = estimate()[1]
        parameters = planning.Plan.model_validate({key: plan[key] for key in planning.Plan.model_fields}).model_dump()
        result = await self.run_agent([
            ('read_user_memory', {}), ('read_current_plan', {}), ('plan_tasks', {'plans': [parameters]}),
            ('plan_rest', {'patterns': [], 'assumption': 'Постоянный режим', 'load_limits': None}),
            ('reorder_tasks', {'task_ids': [1], 'keep_previous_times': True})])
        self.assertGreater(result['schedule']['remaining'][0]['remaining_work_minutes'], 0)

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
        self.engine_patch = patch.object(calendar, 'get_engine', return_value=self.engine)
        self.engine_patch.start()

    def tearDown(self):
        self.engine_patch.stop()
        current_user_id.reset(self.owner)
        self.engine.dispose()
        self.directory.cleanup()

    def test_failed_run_retains_previous_calendar(self):
        old = {'run_id': 'old', 'schedule': {'start_at': NOW}, 'revision': 2}
        with Session(self.engine) as session:
            session.add(IntegrationSetting(name='ameba_plan', config=json.dumps(old)))
            session.commit()
        with patch.object(calendar, 'read_config', return_value={'revision': 2}), patch.object(scheduler, 'call', AsyncMock(side_effect=RuntimeError('synthetic'))):
            with self.assertRaises(RuntimeError):
                asyncio.run(scheduler.run_ranking({}))
        self.assertEqual(calendar.read_ameba_plan()['schedule'], old['schedule'])
        self.assertEqual(calendar.read_ameba_plan()['run_id'], 'old')

    def test_plan_and_ranking_rollback_together_and_late_run_is_rejected(self):
        calendar.reset_ameba_plan('new')
        result = {'schedule': {'start_at': NOW, 'blocks': []}}
        with Session(self.engine) as session:
            session.add(TaskRanking(result='new'))
            calendar.save_ameba_plan('new', result, 2, session)
            session.rollback()
        self.assertNotIn('schedule', calendar.read_ameba_plan())
        with Session(self.engine) as session:
            self.assertIsNone(session.get(TaskRanking, 1))
            with self.assertRaises(ValueError):
                calendar.save_ameba_plan('late', result, 2, session)

    def test_backend_rejects_timeline_error_without_replacing_saved_result(self):
        previous = {'task_ids': [1], 'schedule': {'start_at': NOW, 'blocks': []}}
        with Session(self.engine) as session:
            session.add(Task(id=1, title='Работа'))
            session.add(TaskRanking(snapshot='old', result=json.dumps(previous)))
            session.commit()
        invalid = {'task_ids': [1], 'schedule': {'start_at': NOW, 'blocks': [], 'timeline_error': 'invalid'}}
        with patch.object(ameba, 'get_engine', return_value=self.engine), patch.object(ameba, 'check_feature'), patch.object(ameba, 'read_config', return_value={'enabled': True}), patch.object(ameba, 'task_state', return_value=('new', [{'id': 1}], [])), patch.object(ameba, 'context_status', return_value='ready'), patch.object(ameba, 'request_ranking', AsyncMock(return_value=invalid)):
            with self.assertRaisesRegex(Exception, 'Предыдущий результат сохранён'):
                asyncio.run(ameba.rank_all())
        with Session(self.engine) as session:
            self.assertEqual(json.loads(session.get(TaskRanking, 1).result), previous)

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

    def test_model_check_does_not_enter_scheduler(self):
        with patch.object(ameba, 'read_settings', return_value=({'model': 'synthetic'}, {'api_key': 'synthetic'})), patch.object(ameba, 'call', AsyncMock(return_value={'ok': True})) as call, patch.object(scheduler, 'run_ranking', AsyncMock()) as rank:
            self.assertTrue(asyncio.run(ameba.check_model())['ok'])
            rank.assert_not_called()
            self.assertEqual(call.call_args.args[:2], ('ameba', '/check'))

    def test_time_alone_marks_plan_stale_without_changing_priority_inputs(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC'}):
            self.assertTrue(ameba.plan_expired({'schedule': {'start_at': yesterday, 'blocks': []}}))
            current = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            self.assertFalse(ameba.plan_expired({'schedule': {'start_at': current, 'blocks': []}}))


if __name__ == '__main__':
    unittest.main()
