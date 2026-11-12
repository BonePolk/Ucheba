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
        'task_id': 1, 'deadline_policy': policy, 'focus_minutes': 10,
        'steps': [{'title': 'Работа', 'min_minutes': work, 'max_minutes': work, 'wait_minutes': 0}],
        'assumptions': [], 'effort': 'medium', 'expected_result': 'Готово', 'reward': None, 'reward_unit': '',
    }]}), [1], {1: completed})


class Planning(unittest.TestCase):
    def setUp(self):
        self.week = default_week()
        self.order = json.dumps({'task_ids': [1], 'keep_previous_times': True})
        self.draft = {'start_at': NOW, 'timezone': 'UTC', 'blocks': []}

    def test_partial_work_is_preserved(self):
        plans = estimate()
        draft, result = blocks.edit(json.dumps({'remove_ids': [], 'add': [
            {'task_id': 1, 'date': '2026-11-12', 'start': '09:00', 'end': '10:00'}]}),
            self.draft, self.week, plans, [1], [{'id': 1}], NOW)
        self.assertEqual(result['remaining'][0]['remaining_work_minutes'], 2950)
        schedule = planning.reorder(self.order, plans, [1], self.week, NOW, placed_blocks=blocks.calendar_blocks(draft, self.week))
        self.assertIsNone(schedule['timeline_error'])
        self.assertEqual(schedule['remaining'][0]['remaining_work_minutes'], 2950)
        self.assertTrue(blocks.read(draft, self.week, plans, [1])['free_windows'])

    def test_week_overflow_keeps_blocks_and_remaining(self):
        schedule = planning.reorder(self.order, estimate(), [1], self.week, NOW)
        self.assertIsNone(schedule['timeline_error'])
        self.assertTrue(any(b['kind'] == 'task' for b in schedule['blocks']))
        self.assertGreater(schedule['remaining'][0]['remaining_work_minutes'], 0)

    def test_incomplete_hard_deadline_without_blocks_is_reported(self):
        schedule = planning.reorder(self.order, estimate(policy='finish_before'), [1], self.week, NOW,
                                    tasks=[{'id': 1, 'title': 'Обязательная', 'deadline': '2026-11-12T10:00:00Z'}], placed_blocks=[])
        self.assertTrue(schedule['deadline_conflicts'][0]['infeasible'])

    def test_partial_hard_deadline_keeps_useful_work(self):
        plans = estimate(policy='finish_before')
        placed = [{'id': 'one', 'kind': 'task', 'task_id': 1, 'start': 60, 'minutes': 60, 'focus_minutes': 10}]
        schedule = planning.reorder(self.order, plans, [1], self.week, NOW,
                                    tasks=[{'id': 1, 'title': 'Обязательная', 'deadline': '2026-11-12T10:00:00Z'}], placed_blocks=placed)
        self.assertEqual(schedule['blocks'], placed)
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
            planning.with_rest(week, NOW, json.dumps({'patterns': [], 'assumption': 'Из календаря'}))

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
            tasks=[{'id': i, 'title': str(i), 'deadline': '2026-11-12T10:00:00Z'} for i in (1, 2)], placed_blocks=[])
        self.assertTrue(all(item['infeasible'] for item in result['deadline_conflicts']))

    def test_user_work_window_has_no_arbitrary_ninety_minute_limit(self):
        plans = estimate(120)
        draft, _ = blocks.edit(json.dumps({'remove_ids': [], 'add': [
            {'task_id': 1, 'date': '2026-11-12', 'start': '09:00', 'end': '11:10'}]}),
            self.draft, self.week, plans, [1], [{'id': 1}], NOW)
        self.assertEqual(blocks.remaining(draft, plans, [1])[0]['remaining_work_minutes'], 0)


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
            ('plan_rest', {'patterns': [], 'assumption': 'Постоянный режим'}),
            ('edit_task_blocks', {'remove_ids': [], 'add': [{'task_id': 1, 'date': '2026-11-12', 'start': '09:00', 'end': '10:00'}]}),
            ('reorder_tasks', {'task_ids': [1], 'keep_previous_times': True})])
        self.assertEqual(result['schedule']['remaining'][0]['remaining_work_minutes'], 2950)

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
