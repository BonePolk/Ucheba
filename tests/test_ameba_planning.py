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
import common.codex_harness as codex_harness
from common.codex_harness import (
    ModelCapabilities,
    TurnOutcome,
    clear_model_capability_cache,
    detect_model_capabilities,
    extract_model_limits,
)
from common.database import init_db
from common.identity import current_user_id
from common.models.integration import IntegrationSetting, TaskContext, TaskRanking
from common.models.task import Task
from integrations import ameba, context as monocle_context, scheduler
from fastapi import HTTPException
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

    def test_estimate_override_tracks_progress_and_repeated_refresh(self):
        # Сценарий A: ручной остаток 120 мин с базой 0 (и с базой 30) уменьшается после +60 мин прогресса.
        override_from_0 = {'estimate_overrides': {'1': {'remaining_minutes': 120, 'baseline_completed_minutes': 0}}}
        override_from_30 = {'estimate_overrides': {'1': {'remaining_minutes': 120, 'baseline_completed_minutes': 30}}}
        cand = proposal_payload([1], [task_item(1, 200)], [work_period(1, '2026-11-23', '2026-11-27', 120)])

        tasks_at_0 = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}]
        prop0, issues0, days0 = planning.validate_proposal(cand, tasks_at_0, empty_week(), NOW, adjustments=override_from_0)
        self.assertEqual(issues0, [])
        res0 = planning.materialize_plan(prop0, tasks_at_0, days0, empty_week(), NOW, adjustments=override_from_0)
        self.assertEqual(res0['plans'][1]['remaining_minutes'], 120)

        tasks_after_60 = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}]
        prop1, issues1, days1 = planning.validate_proposal(
            proposal_payload([1], [task_item(1, 200)], [work_period(1, '2026-11-23', '2026-11-27', 60)]),
            tasks_after_60, empty_week(), NOW, adjustments=override_from_0,
        )
        self.assertEqual(issues1, [])
        mat1 = planning.materialize_plan(prop1, tasks_after_60, days1, empty_week(), NOW, adjustments=override_from_0)
        self.assertEqual(mat1['plans'][1]['remaining_minutes'], 60)

        refreshed = planning.refresh_schedule(res0, tasks_after_60, empty_week(), NOW, adjustments=override_from_0)
        self.assertEqual(refreshed['plans'][1]['remaining_minutes'], 60)
        self.assertEqual(refreshed['schedule']['periods'][0]['minutes'], 60)

        # Ненулевой baseline (было 30, поставили 120, потом дошли до 90) -> осталось 60.
        tasks_at_90 = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 90}]
        ref_nonzero = planning.refresh_schedule(res0, tasks_at_90, empty_week(), NOW, adjustments=override_from_30)
        self.assertEqual(ref_nonzero['plans'][1]['remaining_minutes'], 60)

        # Откат выполненного ниже baseline никогда не увеличивает остаток выше override.
        ref_below = planning.refresh_schedule(res0, tasks_at_0, empty_week(), NOW, adjustments=override_from_30)
        self.assertEqual(ref_below['plans'][1]['remaining_minutes'], 120)

        # Сценарий B: повторные /refresh без нового прогресса не вычитают прогресс дважды.
        again1 = planning.refresh_schedule(refreshed, tasks_after_60, empty_week(), NOW, adjustments=override_from_0)
        again2 = planning.refresh_schedule(again1, tasks_after_60, empty_week(), NOW, adjustments=override_from_0)
        self.assertEqual(again1['plans'][1]['remaining_minutes'], 60)
        self.assertEqual(again2['plans'][1]['remaining_minutes'], 60)

        # Без override повторный /refresh тоже идемпотентен.
        plain = planning.refresh_schedule(res0, tasks_after_60, empty_week(), NOW)
        plain_again = planning.refresh_schedule(plain, tasks_after_60, empty_week(), NOW)
        self.assertEqual(plain['plans'][1]['remaining_minutes'], 60)
        self.assertEqual(plain_again['plans'][1]['remaining_minutes'], 60)

        # Полное завершение через override + прогресс даёт 0 остатка.
        tasks_done = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 120}]
        done_ref = planning.refresh_schedule(refreshed, tasks_done, empty_week(), NOW, adjustments=override_from_0)
        self.assertEqual(done_ref['plans'][1]['remaining_minutes'], 0)
        self.assertEqual(done_ref['schedule']['periods'], [])

    def test_completed_task_clears_deadline_risk(self):
        # Сценарий E: при нулевом остатке устаревшее предупреждение deadline_risk не сохраняется.
        dl_task = [{'id': 1, 'title': 'Диплом', 'deadline': '2026-11-24T10:00:00+00:00', 'completed_work_minutes': 0}]
        cand = proposal_payload(
            [1],
            [task_item(1, 120, deadline_risk='Не хватает времени до 24.11 в 10:00.')],
            [work_period(1, '2026-11-23', '2026-11-24', 120)],
        )
        prop, issues, days = planning.validate_proposal(cand, dl_task, empty_week(), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, dl_task, days, empty_week(), NOW)
        self.assertTrue(res['reasons'][0]['deadline_risk'])
        self.assertTrue(res['schedule']['deadline_conflicts'])

        # Частичный прогресс оставляет предупреждение.
        partial = planning.refresh_schedule(
            res,
            [{'id': 1, 'title': 'Диплом', 'deadline': '2026-11-24T10:00:00+00:00', 'completed_work_minutes': 60}],
            empty_week(), NOW,
        )
        self.assertTrue(partial['reasons'][0]['deadline_risk'])
        self.assertTrue(partial['schedule']['deadline_conflicts'])

        # После полного выполнения предупреждение о риске срока исчезает.
        completed = planning.refresh_schedule(
            partial,
            [{'id': 1, 'title': 'Диплом', 'deadline': '2026-11-24T10:00:00+00:00', 'completed_work_minutes': 120}],
            empty_week(), NOW,
        )
        self.assertIsNone(completed['reasons'][0]['deadline_risk'])
        self.assertEqual(completed['schedule']['deadline_conflicts'], [])
        self.assertEqual(completed['schedule']['deadline_risks'], [])
        self.assertEqual(completed['schedule']['feasibility_status'], 'no_objective_conflicts')
        self.assertEqual(completed['schedule']['periods'], [])

        # Если effective remaining == 0 прямо в materialize_plan (например через override=0), риск тоже не выводится.
        zero_mat = planning.materialize_plan(
            prop, dl_task, days, empty_week(), NOW,
            adjustments={'estimate_overrides': {'1': {'remaining_minutes': 0, 'baseline_completed_minutes': 0}}},
        )
        self.assertIsNone(zero_mat['reasons'][0]['deadline_risk'])
        self.assertEqual(zero_mat['schedule']['deadline_conflicts'], [])

    def test_compact_overview_and_large_task_details(self):
        long_ctx = 'Детальный разбор Monocle: ' + ('Очень подробные требования. ' * 600)
        long_notes = 'Важная заметка владельца: ' + ('дополнительные детали и ограничения, ' * 15)
        tasks = [
            {
                'id': 1,
                'title': 'Большой проект',
                'priority': 'high',
                'deadline': '2026-11-27T18:00:00Z',
                'completed_work_minutes': 45,
                'summary': 'Краткая суть большого проекта.',
                'description': 'Краткая суть большого проекта.',
                'owner_notes': long_notes,
                'full_context': long_ctx,
                'time_estimate': '~4 часа',
                'subtasks': [
                    {'title': 'Шаг 1', 'done': True, 'estimated_minutes': 30},
                    {'title': 'Шаг 2', 'done': False, 'estimated_minutes': 90},
                ],
            },
            {
                'id': 2,
                'title': 'Короткая задача',
                'priority': 'low',
                'completed_work_minutes': 0,
            },
        ]
        now_dt = datetime.fromisoformat(NOW)
        zone = ZoneInfo('UTC')
        overview = ameba_service._compact_tasks(tasks, now_dt, zone)
        self.assertEqual([t['id'] for t in overview], [1, 2])
        first = overview[0]
        self.assertNotIn('full_context', first)
        self.assertNotIn('subtasks', first)
        self.assertEqual(first['brief_context'], 'Краткая суть большого проекта.')
        self.assertEqual(first['subtasks_summary'], {'total': 2, 'done': 1, 'remaining_estimated_minutes': 90})
        self.assertTrue(first['has_more_details'])
        self.assertLess(len(json.dumps(overview, ensure_ascii=False)), 1500)

        details = ameba_service._task_details(
            tasks[0],
            now_dt,
            zone,
            adjustments={
                'estimate_overrides': {'1': {'remaining_minutes': 120, 'baseline_completed_minutes': 15}},
                'task_pins': {'1': {'mode': 'date', 'target_date': '2026-11-25'}},
            },
        )
        self.assertEqual(details['full_context'], long_ctx)
        self.assertNotIn('summary', details)
        self.assertNotIn('description', details)
        self.assertEqual(len(details['subtasks']), 2)
        self.assertEqual(details['effective_remaining_override'], 90)
        self.assertEqual(details['task_pin'], {'mode': 'date', 'target_date': '2026-11-25'})

    def test_previous_plan_preserves_more_than_16_periods_and_blocked_dates(self):
        periods = [
            work_period(tid, f'2026-11-{23 + (tid % 5):02d}', f'2026-11-{24 + (tid % 5):02d}', 60, goal=f'Этап {tid}')
            for tid in range(1, 31)
        ] + [rest_period('2026-11-28', '2026-11-28')]
        prev_plan = {
            'task_ids': list(range(1, 31)),
            'explanation': 'Предыдущий подробный план.',
            'schedule': {'version': 3, 'blocked_dates': ['2026-11-29'], 'periods': periods},
        }
        full_prev, by_task, truncated = ameba_service._serialize_previous_plan(prev_plan, max_tokens=200_000)
        self.assertFalse(truncated)
        self.assertEqual(len(full_prev['periods']), 31)
        self.assertEqual(full_prev['blocked_dates'], ['2026-11-29'])
        self.assertIn(25, by_task)

        short_prev, short_by_task, short_trunc = ameba_service._serialize_previous_plan(
            prev_plan, max_tokens=350, pinned_ids={29},
        )
        self.assertTrue(short_trunc)
        self.assertEqual(short_prev['blocked_dates'], ['2026-11-29'])
        self.assertEqual(short_prev['task_ids'], list(range(1, 31)))
        self.assertTrue(any(p.get('kind') == 'rest' for p in short_prev['periods']))
        self.assertTrue(any(p.get('task_id') == 29 for p in short_prev['periods']))
        self.assertIn('get_task_details', short_prev['note'])
        self.assertEqual(len(short_by_task), 30)

    def test_extract_model_limits_distinguishes_theoretical_and_provider_limit(self):
        one_m = extract_model_limits({'id': 'gemini-pro', 'context_length': 1_000_000, 'max_output_tokens': 65_536})
        self.assertEqual((one_m['context_window'], one_m['theoretical_context_window'], one_m['max_output_tokens']), (1_000_000, 1_000_000, 65_536))

        capped = extract_model_limits({
            'id': 'gemini-pro',
            'context_length': 1_000_000,
            'top_provider': {'context_length': 32_000, 'max_completion_tokens': 8_192},
        })
        self.assertEqual((capped['context_window'], capped['theoretical_context_window'], capped['max_output_tokens']), (32_000, 1_000_000, 8_192))

        io_only = extract_model_limits({'id': 'custom-m', 'input_token_limit': 1_048_576, 'output_token_limit': 32_768})
        caps = ModelCapabilities(
            model='custom-m',
            context_window=io_only['context_window'],
            max_input_tokens=io_only['max_input_tokens'],
            max_output_tokens=io_only['max_output_tokens'],
            source='provider',
        )
        self.assertEqual(caps.effective_limit, 1_048_576)


class CapabilityDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_metadata_falls_back_safely_without_repeated_probes(self):
        clear_model_capability_cache()
        calls = 0

        class FakeResp:
            is_success = True
            status_code = 200

            def json(self):
                return {'data': [{'id': 'opaque-model'}]}

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, **kwargs):
                nonlocal calls
                calls += 1
                return FakeResp()

        with patch.object(codex_harness.httpx, 'AsyncClient', FakeAsyncClient):
            caps1 = await detect_model_capabilities(
                auth_mode='api_key', base_url='https://example.invalid/v1', api_key='k', model='opaque-model',
            )
            caps2 = await detect_model_capabilities(
                auth_mode='api_key', base_url='https://example.invalid/v1', api_key='k', model='opaque-model',
            )
        self.assertIsNone(caps1.effective_limit)
        self.assertEqual(caps1.source, 'unknown')
        self.assertIsNone(caps2.effective_limit)
        self.assertEqual(calls, 1)

    async def test_context_compaction_and_token_usage_stream_events(self):
        class FakeTurn:
            async def stream(self):
                for ev in (
                    MagicMock(method='thread/tokenUsage/updated', payload={
                        'token_usage': {
                            'total': {'input_tokens': 12000, 'output_tokens': 400},
                            'last': {'input_tokens': 9500, 'output_tokens': 350, 'total_tokens': 9850},
                            'model_context_window': 128000,
                        },
                    }),
                    MagicMock(method='item/started', payload=MagicMock(item=MagicMock(type='contextCompaction', phase=None, id='c1'))),
                    MagicMock(method='item/completed', payload=MagicMock(item=MagicMock(type='contextCompaction', phase=None, id='c1'))),
                    MagicMock(method='turn/completed', payload=MagicMock(turn=MagicMock(status='completed', error=None))),
                ):
                    yield ev

            async def interrupt(self):
                pass

        thread = MagicMock()
        thread.turn = AsyncMock(return_value=FakeTurn())
        adapter = codex_harness.McpToolAdapter([], AsyncMock(), MagicMock())
        outcome = await codex_harness.run_codex_turn(thread, 'hi', label='Ameba', progress=MagicMock(), adapter=adapter)
        self.assertTrue(outcome.compacted)
        self.assertEqual(outcome.context_window, 128000)
        self.assertEqual(outcome.last_usage['input_tokens'], 9500)
        self.assertEqual(adapter.compaction_count, 1)
        self.assertEqual(adapter.context_window, 128000)


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_agent(
        self,
        actions,
        *,
        user_feedback='',
        memory_items=None,
        memory_mock=None,
        ask_answer='Подтвердить',
        ask_mock=None,
        tasks=None,
        adjustments=None,
        persist_progress_mock=None,
        captured_prompts=None,
        captured_tools=None,
        config=None,
        previous_plan=None,
        provider_metadata=None,
        codex_models=None,
        turn_outcomes=None,
        keep_capability_cache=False,
    ):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.fromisoformat(NOW)

        if not keep_capability_cache:
            clear_model_capability_cache()

        codex_client = MagicMock()
        codex_client.thread_start = AsyncMock(return_value=MagicMock())
        if codex_models is not None:
            codex_client.models = AsyncMock(return_value=MagicMock(data=codex_models))

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
        outcome_iter = iter(turn_outcomes or ())

        async def fake_turn(thread_arg, prompt_input, *, adapter=None, **kwargs):
            if captured_prompts is not None:
                captured_prompts.append(prompt_input.content if hasattr(prompt_input, 'content') else prompt_input)
            forced_outcome = next(outcome_iter, None)
            if forced_outcome is not None and not forced_outcome.completed:
                if getattr(forced_outcome, 'compacted', False) and adapter is not None:
                    adapter.compaction_count += 1
                    if callable(adapter.on_compaction):
                        adapter.on_compaction()
                return forced_outcome
            for idx, (name, arguments) in enumerate(next(step_iter, []), start=1):
                resp = await adapter.respond({
                    'jsonrpc': '2.0',
                    'id': idx,
                    'method': 'tools/call',
                    'params': {'name': name, 'arguments': arguments},
                })
                if captured_tools is not None and isinstance(resp, dict) and 'result' in resp:
                    content = (resp['result'] or {}).get('content') or []
                    if content and 'text' in content[0]:
                        captured_tools.append((name, json.loads(content[0]['text'])))
                if adapter.fatal_error is not None or adapter.terminal_result is not None:
                    break
            if adapter.fatal_error is not None:
                raise adapter.fatal_error
            if forced_outcome is not None:
                if getattr(forced_outcome, 'compacted', False) and adapter is not None:
                    adapter.compaction_count += 1
                    if callable(adapter.on_compaction):
                        adapter.on_compaction()
                return TurnOutcome(
                    text=forced_outcome.text,
                    completed=forced_outcome.completed,
                    status=forced_outcome.status,
                    error_detail=forced_outcome.error_detail,
                    usage=forced_outcome.usage,
                    terminal_result=adapter.terminal_result,
                    last_usage=forced_outcome.last_usage,
                    context_window=forced_outcome.context_window,
                    compacted=forced_outcome.compacted,
                    error_kind=forced_outcome.error_kind,
                )
            return TurnOutcome(
                text='done', completed=True, status='completed', error_detail=None,
                usage={'input_tokens': 0, 'output_tokens': 0}, terminal_result=adapter.terminal_result,
            )

        mem_fn = memory_mock or AsyncMock(return_value=memory_items or [])
        ask_fn = ask_mock or AsyncMock(return_value={'answer': ask_answer})
        persist_fn = persist_progress_mock or AsyncMock(
            side_effect=lambda entry: {
                'applied': True,
                'duplicate': False,
                'task_id': entry['task_id'],
                'completed_work_minutes': entry['base_completed_minutes'] + entry['add_minutes'],
            },
        )
        run_body = {
            'run_id': 'test-run',
            'api_key': 'synthetic',
            'config': config or {'model': 'fixture'},
            'tasks': tasks or [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}],
            'now': NOW,
            'weekly_schedule': empty_week(),
            'user_feedback': user_feedback,
        }
        if adjustments is not None:
            run_body['manual_adjustments'] = adjustments
        if previous_plan is not None:
            run_body['previous_plan'] = previous_plan
        with patch.object(ameba_service, 'datetime', Clock), \
             patch.object(ameba_service, 'Progress', MagicMock()), \
             patch.object(ameba_service, 'memory_request', AsyncMock(return_value=memory_items or [])), \
             patch.object(ameba_service, 'memory_tool', mem_fn), \
             patch.object(ameba_service, 'ask_user', ask_fn), \
             patch.object(ameba_service, 'persist_progress', persist_fn), \
             patch.object(ameba_service, 'open_codex', fake_open_codex), \
             patch.object(ameba_service, 'scratch_env', fake_scratch), \
             patch.object(ameba_service, 'serve_mcp', fake_serve), \
             patch.object(codex_harness, 'fetch_provider_model_metadata', AsyncMock(return_value=provider_metadata)), \
             patch.object(ameba_service, 'run_codex_turn', side_effect=fake_turn):
            return await ameba_service.rank.__wrapped__(run_body)

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
        persisted_calls = []
        persist_mock = AsyncMock(side_effect=lambda entry: persisted_calls.append(entry) or {
            'applied': True,
            'duplicate': False,
            'task_id': entry['task_id'],
            'completed_work_minutes': entry['base_completed_minutes'] + entry['add_minutes'],
        })
        confirmed = await self.run_agent(
            actions,
            user_feedback='Я сделал 30 и потом ещё 45 минут',
            ask_answer='Подтвердить',
            persist_progress_mock=persist_mock,
        )
        self.assertEqual(len(persisted_calls), 2)
        self.assertTrue(all(call.get('confirmed_by_user') is True and call.get('confirmation_id') for call in persisted_calls))
        self.assertEqual([(c['task_id'], c['add_minutes'], c['base_completed_minutes'], c['persisted']) for c in confirmed['confirmed_progress']], [
            (1, 30, 0, True),
            (1, 45, 30, True),
        ])
        self.assertEqual(confirmed['plans'][1]['completed_work_minutes'], 75)

        cancel_persist = AsyncMock()
        cancelled = await self.run_agent(
            actions,
            user_feedback='Я сделал 30 и потом ещё 45 минут',
            ask_answer='Отменить',
            persist_progress_mock=cancel_persist,
        )
        cancel_persist.assert_not_called()
        self.assertEqual(cancelled['confirmed_progress'], [])
        self.assertEqual(cancelled['plans'][1]['completed_work_minutes'], 0)

    async def test_record_progress_persisted_when_planning_fails(self):
        bad = proposal_payload([1], [task_item(1, 120)], [work_period(1, '2026-11-20', '2026-11-23', 120)])
        persisted = []
        persist_mock = AsyncMock(side_effect=lambda entry: persisted.append(entry) or {
            'applied': True,
            'duplicate': False,
            'task_id': entry['task_id'],
            'completed_work_minutes': entry['base_completed_minutes'] + entry['add_minutes'],
        })
        with self.assertRaisesRegex(Exception, 'не прошёл проверку'):
            await self.run_agent(
                [[
                    ('record_progress', {'task_id': 1, 'add_minutes': 45, 'reason': 'Сделал часть'}),
                    ('propose_plan', bad),
                    ('propose_plan', bad),
                ]],
                ask_answer='Подтвердить',
                persist_progress_mock=persist_mock,
            )
        self.assertEqual(len(persisted), 1)
        self.assertEqual((persisted[0]['task_id'], persisted[0]['add_minutes'], persisted[0]['base_completed_minutes']), (1, 45, 0))

    async def test_fourteen_tasks_retrieve_details_in_batches_without_asking_user(self):
        # Сценарий A: 14 задач с длинным контекстом; все ID видны в обзоре, детали догружаются батчами без ask_user.
        ids14 = [16, 14, 27, 7, 15, 21, 17, 18, 19, 25, 1, 2, 3, 4]
        tasks14 = [
            {
                'id': tid,
                'title': f'Задача #{tid}',
                'priority': 'high' if tid in (16, 14) else 'medium',
                'deadline': '2026-11-29T18:00:00Z',
                'completed_work_minutes': 15 if tid == 16 else 0,
                'summary': f'Кратко о задаче {tid}.',
                'full_context': f'Детальный текст задачи #{tid}: ' + ('Подробности и ссылки. ' * 160),
                'time_estimate': '1 ч',
            }
            for tid in ids14
        ]
        plan14 = proposal_payload(
            ids14,
            [task_item(tid, 60) for tid in ids14],
            [work_period(tid, '2026-11-23', '2026-11-28', 60) for tid in ids14],
        )
        prompts = []
        tool_results = []
        ask_mock = AsyncMock(return_value={'answer': 'Подтвердить'})
        res = await self.run_agent(
            [[
                ('get_task_details', {'task_ids': [16, 14, 27, 7, 15]}),
                ('get_task_details', {'task_ids': [21, 17, 18, 19, 25]}),
                ('propose_plan', plan14),
            ]],
            tasks=tasks14,
            ask_mock=ask_mock,
            captured_prompts=prompts,
            captured_tools=tool_results,
        )
        ask_mock.assert_not_called()
        self.assertEqual(res['task_ids'], ids14)
        self.assertEqual(len(prompts), 1)
        for tid in ids14:
            self.assertIn(f'"id":{tid}', prompts[0])
            self.assertIn(f'Задача #{tid}', prompts[0])
        self.assertNotIn('Детальный текст задачи #', prompts[0])
        batch1 = tool_results[0][1]
        batch2 = tool_results[1][1]
        self.assertEqual([t['id'] for t in batch1['tasks']], [16, 14, 27, 7, 15])
        self.assertEqual([t['id'] for t in batch2['tasks']], [21, 17, 18, 19, 25])
        self.assertEqual(batch1['missing_task_ids'], [])
        self.assertEqual(batch2['missing_task_ids'], [])
        self.assertTrue(all('Детальный текст задачи #' in t['full_context'] for t in batch1['tasks'] + batch2['tasks']))

    async def test_get_task_details_batches_large_payload_with_deferred_ids(self):
        # Сценарий B (tool-side): при запросе множества длинных задач возвращается первая порция и deferred_task_ids.
        ids = list(range(1, 13))
        big_tasks = [
            {
                'id': tid,
                'title': f'Крупная #{tid}',
                'completed_work_minutes': 0,
                'summary': f'Кратко {tid}',
                'full_context': f'Контекст #{tid}: ' + ('x' * 20000 if tid <= 4 else 'короткий'),
            }
            for tid in ids
        ]
        tool_results = []
        plan = proposal_payload(ids, [task_item(tid, 30) for tid in ids], [work_period(tid, '2026-11-23', '2026-11-25', 30) for tid in ids])
        await self.run_agent(
            [[
                ('get_task_details', {'task_ids': ids}),
                ('propose_plan', plan),
            ]],
            tasks=big_tasks,
            captured_tools=tool_results,
        )
        details_out = tool_results[0][1]
        self.assertGreaterEqual(len(details_out['tasks']), 1)
        self.assertLess(len(details_out['tasks']), len(ids))
        returned_ids = [t['id'] for t in details_out['tasks']]
        self.assertEqual(returned_ids + details_out['deferred_task_ids'], ids)
        self.assertIn('get_task_details', details_out['note'])

    async def test_missing_real_estimate_allows_estimation_or_ask_user(self):
        # Сценарий C: если оценки объёма реально нет в данных Ucheba, Ameba может уточнить через ask_user.
        tasks = [{'id': 1, 'title': 'Подготовить доклад', 'completed_work_minutes': 0, 'summary': 'Тема ещё не оценена'}]
        tool_results = []
        ask_mock = AsyncMock(return_value={'answer': 'Около 2 часов'})
        res = await self.run_agent(
            [[
                ('get_task_details', {'task_ids': [1]}),
                ('ask_user', {'question': 'Сколько примерно времени займёт подготовка доклада?', 'options': []}),
                ('propose_plan', proposal_payload([1], [task_item(1, 120)], [work_period(1, '2026-11-24', '2026-11-25', 120)])),
            ]],
            tasks=tasks,
            ask_mock=ask_mock,
            captured_tools=tool_results,
        )
        ask_mock.assert_awaited_once()
        self.assertNotIn('time_estimate', tool_results[0][1]['tasks'][0])
        self.assertEqual(res['plans'][1]['remaining_minutes'], 120)

    async def test_get_task_details_invalid_and_mixed_ids(self):
        # Сценарий D: несуществующие ID возвращают понятную ограниченную ошибку без чужих данных.
        tasks = [
            {'id': 1, 'title': 'Первая', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Вторая', 'completed_work_minutes': 0},
        ]
        tool_results = []
        plan = proposal_payload(
            [1, 2],
            [task_item(1, 60), task_item(2, 60)],
            [work_period(1, '2026-11-23', '2026-11-23', 60), work_period(2, '2026-11-24', '2026-11-24', 60)],
        )
        await self.run_agent(
            [[
                ('get_task_details', {'task_ids': [99999]}),
                ('get_task_details', {'task_ids': [1, 99999]}),
                ('get_task_details', {'task_ids': list(range(9000, 9030))}),
                ('get_task_details', {'task_ids': []}),
                ('propose_plan', plan),
            ]],
            tasks=tasks,
            captured_tools=tool_results,
        )
        only_missing = tool_results[0][1]
        self.assertFalse(only_missing['valid'])
        self.assertEqual(only_missing['tasks'], [])
        self.assertEqual(only_missing['missing_task_ids'], [99999])
        self.assertEqual(only_missing['available_task_ids'], [1, 2])

        mixed = tool_results[1][1]
        self.assertEqual([t['id'] for t in mixed['tasks']], [1])
        self.assertEqual(mixed['missing_task_ids'], [99999])
        self.assertEqual(mixed['available_task_ids'], [1, 2])

        many_missing = tool_results[2][1]
        self.assertFalse(many_missing['valid'])
        self.assertEqual(len(many_missing['missing_task_ids']), 20)

        empty_req = tool_results[3][1]
        self.assertFalse(empty_req['valid'])
        self.assertEqual(empty_req['expected_task_ids'], [1, 2])

    async def test_context_compaction_recovers_task_details_on_followup_turn(self):
        # Сценарий E: после потери/сжатия контекста между ходами get_task_details восстанавливает актуальные данные задач.
        tasks = [
            {'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0, 'full_context': 'Полное условие лабораторной работы'},
            {'id': 2, 'title': 'Отчёт', 'completed_work_minutes': 0, 'full_context': 'Требования к отчёту'},
        ]
        prompts = []
        tool_results = []
        plan = proposal_payload(
            [1, 2],
            [task_item(1, 90), task_item(2, 60)],
            [work_period(1, '2026-11-23', '2026-11-24', 90), work_period(2, '2026-11-24', '2026-11-25', 60)],
        )
        res = await self.run_agent(
            [
                [('record_progress', {'task_id': 1, 'add_minutes': 30, 'reason': 'Сделал начало'})],
                [('get_task_details', {'task_ids': [1, 2]}), ('propose_plan', plan)],
            ],
            tasks=tasks,
            ask_answer='Подтвердить',
            captured_prompts=prompts,
            captured_tools=tool_results,
        )
        self.assertEqual(len(prompts), 2)
        self.assertIn('get_task_details', prompts[1])
        self.assertIn('[1, 2]', prompts[1])
        recovered = tool_results[1][1]['tasks']
        self.assertEqual([(t['id'], t['completed_work_minutes']) for t in recovered], [(1, 30), (2, 0)])
        self.assertEqual(recovered[0]['full_context'], 'Полное условие лабораторной работы')
        self.assertEqual(res['plans'][1]['completed_work_minutes'], 30)

    async def test_verified_1m_model_sends_full_context_and_30_previous_periods(self):
        # Сценарий A и E: 1M-модель получает ~80K токенов контекста 14 задач и 30 периодов без искусственной обрезки.
        ids14 = list(range(1, 15))
        block = 'Detailed requirements and schedule notes for task planning. ' * 380  # ~22.8K ASCII chars -> ~5.7K tok * 14 ~= 80K tok
        tasks14 = [
            {
                'id': tid,
                'title': f'Задача #{tid}',
                'priority': 'high' if tid <= 4 else 'medium',
                'status': 'in_progress' if tid == 1 else 'todo',
                'deadline': '2026-11-29T18:00:00Z',
                'completed_work_minutes': 15 if tid == 1 else 0,
                'full_context': f'Уникальный контекст задачи #{tid}: {block}',
            }
            for tid in ids14
        ]
        periods30 = [
            work_period((i % 14) + 1, f'2026-11-{23 + (i % 5):02d}', f'2026-11-{24 + (i % 5):02d}', 45, goal=f'Этап плана #{i + 1}')
            for i in range(30)
        ]
        prev30 = {
            'task_ids': ids14,
            'explanation': 'Предыдущий план с 30 периодами.',
            'schedule': {'version': 3, 'blocked_dates': ['2026-11-29'], 'periods': periods30},
        }
        plan14 = proposal_payload(
            ids14,
            [task_item(tid, 60) for tid in ids14],
            [work_period(tid, '2026-11-23', '2026-11-28', 60) for tid in ids14],
        )
        prompts = []
        res = await self.run_agent(
            [[('propose_plan', plan14)]],
            tasks=tasks14,
            previous_plan=prev30,
            provider_metadata={'model': 'gemini-pro', 'context_window': 1_000_000, 'theoretical_context_window': 1_000_000, 'max_input_tokens': None, 'max_output_tokens': 65_536},
            config={'model': 'gemini-pro'},
            captured_prompts=prompts,
        )
        self.assertEqual(res['task_ids'], ids14)
        self.assertEqual(len(prompts), 1)
        payload = json.loads(prompts[0].split('\n', 1)[1])
        self.assertEqual(len(payload['tasks']), 14)
        self.assertTrue(all('full_context' in t and 'has_more_details' not in t for t in payload['tasks']))
        self.assertEqual(payload['tasks'][0]['status'], 'in_progress')
        self.assertEqual(len(payload['previous_plan']['periods']), 30)
        self.assertEqual(payload['previous_plan']['blocked_dates'], ['2026-11-29'])
        self.assertNotIn('truncated', payload['previous_plan'])

    async def test_small_context_model_stays_within_budget_and_uses_retrieval(self):
        # Сценарий B: модель 32K удерживает первый промпт в реальном бюджете, сохраняя все ID и детали в get_task_details.
        ids14 = list(range(1, 15))
        block = 'Detailed requirements and schedule notes for task planning. ' * 380
        tasks14 = [
            {
                'id': tid,
                'title': f'Задача #{tid}',
                'priority': 'medium',
                'completed_work_minutes': 0,
                'summary': f'Кратко #{tid}',
                'full_context': f'Подробности #{tid}: {block}',
            }
            for tid in ids14
        ]
        plan14 = proposal_payload(
            ids14, [task_item(tid, 45) for tid in ids14],
            [work_period(tid, '2026-11-23', '2026-11-28', 45) for tid in ids14],
        )
        prompts = []
        tool_results = []
        await self.run_agent(
            [[('get_task_details', {'task_ids': [1, 2], 'context_offset': None}), ('propose_plan', plan14)]],
            tasks=tasks14,
            provider_metadata={'model': 'small-model', 'context_window': 32_000, 'theoretical_context_window': 32_000, 'max_input_tokens': None, 'max_output_tokens': 4_096},
            config={'model': 'small-model'},
            captured_prompts=prompts,
            captured_tools=tool_results,
        )
        self.assertLessEqual(ameba_service._estimate_tokens(prompts[0]), 16_000)
        payload = json.loads(prompts[0].split('\n', 1)[1])
        self.assertEqual([t['id'] for t in payload['tasks']], ids14)
        self.assertTrue(all(t.get('has_more_details') for t in payload['tasks']))
        self.assertEqual([t['id'] for t in tool_results[0][1]['tasks']], [1, 2])
        self.assertTrue(all('Подробности #' in t['full_context'] for t in tool_results[0][1]['tasks']))

    async def test_large_individual_task_chunked_losslessly_and_recovered(self):
        # Сценарий D: гигантский отчёт Monocle (~180K символов) разбивается на чанки без потери данных.
        giant_text = '\n'.join(f'Строка {i:04d}: ' + ('подробный текст отчёта Monocle ' * 4) for i in range(1300))
        self.assertGreater(len(giant_text), 170_000)
        tasks = [{'id': 1, 'title': 'Гигантская задача', 'completed_work_minutes': 0, 'summary': 'Кратко', 'full_context': giant_text}]
        plan = proposal_payload([1], [task_item(1, 180)], [work_period(1, '2026-11-23', '2026-11-27', 180)])
        prompts = []
        tool_results = []
        await self.run_agent(
            [[
                ('get_task_details', {'task_ids': [1], 'context_offset': 0}),
                ('propose_plan', plan),
            ]],
            tasks=tasks,
            provider_metadata={'model': 'gemini-pro', 'context_window': 1_000_000, 'theoretical_context_window': 1_000_000, 'max_input_tokens': None, 'max_output_tokens': None},
            captured_prompts=prompts,
            captured_tools=tool_results,
        )
        # В начальном промпте огромная задача не раздувает сообщение сверх per-task лимита, а инструмент режет на чанки.
        first_chunk = tool_results[0][1]['tasks'][0]
        self.assertIn('full_context_chunk', first_chunk)
        meta1 = first_chunk['full_context_chunk']
        self.assertTrue(meta1['has_more'])
        self.assertEqual(meta1['total_chars'], len(giant_text))
        next_off = meta1['next_offset']
        self.assertIn(f'context_offset={next_off}', tool_results[0][1]['note'])

        # Проверяем сборку всех оставшихся чанков напрямую через handler на реальном AmebaTools.
        chunks = [first_chunk['full_context']]
        handler = ameba_service.AmebaTools({'tasks': tasks, 'now': NOW}, empty_week(), MagicMock())
        while next_off is not None:
            out, _ = await handler.call('get_task_details', {'task_ids': [1], 'context_offset': next_off})
            c = out['tasks'][0]
            chunks.append(c['full_context'])
            next_off = c['full_context_chunk']['next_offset']
        self.assertEqual(''.join(chunks), giant_text)

    async def test_compaction_signals_recovery_without_asking_user(self):
        # Сценарий F: при событии сжатия контекста второй ход явно инструктируется догрузить детали и прошлый план без ask_user.
        tasks = [
            {'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0, 'full_context': 'Полное условие'},
            {'id': 2, 'title': 'Отчёт', 'completed_work_minutes': 0, 'full_context': 'Детали отчёта'},
        ]
        prev = {
            'task_ids': [1, 2],
            'explanation': 'Старый план.',
            'schedule': {'version': 3, 'blocked_dates': [], 'periods': [work_period(1, '2026-11-23', '2026-11-24', 60)]},
        }
        plan = proposal_payload([1, 2], [task_item(1, 60), task_item(2, 60)], [work_period(1, '2026-11-23', '2026-11-24', 60), work_period(2, '2026-11-25', '2026-11-26', 60)])
        prompts = []
        tool_results = []
        ask_mock = AsyncMock(return_value={'answer': 'Подтвердить'})
        compacted_first = TurnOutcome(
            text='', completed=True, status='completed', error_detail=None,
            usage={'input_tokens': 5000, 'output_tokens': 100}, compacted=True,
        )
        await self.run_agent(
            [[], [('get_task_details', {'task_ids': [1], 'context_offset': None}), ('propose_plan', plan)]],
            tasks=tasks,
            previous_plan=prev,
            ask_mock=ask_mock,
            turn_outcomes=[compacted_first, None],
            captured_prompts=prompts,
            captured_tools=tool_results,
        )
        ask_mock.assert_not_called()
        self.assertEqual(len(prompts), 2)
        self.assertIn('сжат', prompts[1].lower())
        self.assertIn('get_task_details', prompts[1])
        self.assertEqual(tool_results[0][1]['tasks'][0]['previous_periods'][0]['start_date'], '2026-11-23')

    async def test_provider_smaller_effective_limit_respected_and_clamped_on_overflow(self):
        # Сценарий G: (1) top_provider ограничивает 1M модель до 32K, (2) ошибка contextWindowExceeded переключает в компактный режим и запоминает лимит.
        ids14 = list(range(1, 15))
        block = 'Detailed requirements and schedule notes for task planning. ' * 380
        tasks14 = [
            {'id': tid, 'title': f'Задача #{tid}', 'priority': 'medium', 'completed_work_minutes': 0, 'full_context': f'Контекст #{tid}: {block}'}
            for tid in ids14
        ]
        plan14 = proposal_payload(ids14, [task_item(tid, 60) for tid in ids14], [work_period(tid, '2026-11-23', '2026-11-28', 60) for tid in ids14])

        # G1: проактивный учёт меньшего эффективного лимита провайдера
        g1_prompts = []
        await self.run_agent(
            [[('propose_plan', plan14)]],
            tasks=tasks14,
            provider_metadata={'model': 'gemini-pro', 'context_window': 32_000, 'theoretical_context_window': 1_000_000, 'max_input_tokens': None, 'max_output_tokens': None},
            config={'model': 'gemini-pro'},
            captured_prompts=g1_prompts,
        )
        g1_payload = json.loads(g1_prompts[0].split('\n', 1)[1])
        self.assertTrue(all(t.get('has_more_details') for t in g1_payload['tasks']))

        # G2: модель заявляет 1M, первый ход падает с contextWindowExceeded -> сразу переключение на компактный поток и память на следующий запуск
        overflow = TurnOutcome(
            text='', completed=False, status='failed', error_detail='Ameba: превышен контекст модели',
            usage={'input_tokens': 0, 'output_tokens': 0}, error_kind='contextWindowExceeded',
        )
        g2_prompts = []
        res = await self.run_agent(
            [[('get_task_details', {'task_ids': [1], 'context_offset': None}), ('propose_plan', plan14)]],
            tasks=tasks14,
            provider_metadata={'model': 'gemini-proxy', 'context_window': 1_000_000, 'theoretical_context_window': 1_000_000, 'max_input_tokens': None, 'max_output_tokens': None},
            config={'model': 'gemini-proxy'},
            turn_outcomes=[overflow, None],
            captured_prompts=g2_prompts,
        )
        self.assertEqual(res['task_ids'], ids14)
        self.assertEqual(len(g2_prompts), 2)
        self.assertGreater(len(g2_prompts[0]), len(g2_prompts[1]) * 3)
        second_payload = json.loads(g2_prompts[1].split('\n', 1)[1])
        self.assertTrue(all(t.get('has_more_details') for t in second_payload['tasks']))

        # Повторный запуск с тем же кэшем больше не шлёт огромный 80K промпт
        g2_repeat_prompts = []
        await self.run_agent(
            [[('propose_plan', plan14)]],
            tasks=tasks14,
            provider_metadata={'model': 'gemini-proxy', 'context_window': 1_000_000, 'theoretical_context_window': 1_000_000, 'max_input_tokens': None, 'max_output_tokens': None},
            config={'model': 'gemini-proxy'},
            captured_prompts=g2_repeat_prompts,
            keep_capability_cache=True,
        )
        repeat_payload = json.loads(g2_repeat_prompts[0].split('\n', 1)[1])
        self.assertLess(len(g2_repeat_prompts[0]), len(g2_prompts[0]) // 2)
        self.assertTrue(any(t.get('has_more_details') for t in repeat_payload['tasks']))
        clear_model_capability_cache()


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

    def test_estimate_override_baseline_across_progress_and_legacy_int(self):
        self._seed_task(title='Курсовая', completed=30)
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, '_trigger_refresh', AsyncMock(return_value='needs_replan')), \
             patch('agent_memory.read', return_value=[]):
            status0 = asyncio.run(ameba.adjust_plan(estimate_override={'task_id': 1, 'remaining_minutes': 120}))
            self.assertEqual(ameba.read_adjustments()['estimate_overrides']['1'], {
                'remaining_minutes': 120,
                'baseline_completed_minutes': 30,
            })
            self.assertEqual(status0['manual_adjustments']['estimate_overrides']['1'], 120)

            applied = ameba.apply_confirmed_progress(
                1, 60, 30, confirmed_by_user=True, confirmation_id='cid-ov-1',
            )
            self.assertEqual(applied['completed_work_minutes'], 90)
            status1 = ameba.ranking_status()
            self.assertEqual(status1['manual_adjustments']['estimate_overrides']['1'], 60)

            # Совместимость со старым целочисленным форматом override (без baseline).
            with Session(self.engine) as session:
                saved = TaskRanking(
                    id=1,
                    snapshot='old',
                    result=json.dumps({
                        'task_ids': [1],
                        'timeline': [{'id': 1, 'title': 'Курсовая', 'plan': {'remaining_minutes': 120, 'completed_work_minutes': 30}}],
                        'schedule': {'version': 3, 'start_at': NOW, 'periods': []},
                    }),
                )
                session.add(saved)
                adj = ameba.read_adjustments(session)
                adj['estimate_overrides'] = {'1': 120}
                ameba.save_adjustments(adj, session=session)
                session.commit()
            legacy_status = ameba.ranking_status()
            self.assertEqual(legacy_status['manual_adjustments']['estimate_overrides']['1'], 60)

    def test_persisted_progress_survives_failed_planning_and_allows_success(self):
        self._seed_task(title='Лабораторная', completed=0)
        cand = proposal_payload([1], [task_item(1, 120)], [work_period(1, '2026-11-23', '2026-11-25', 120)])
        prop, _, days = planning.validate_proposal(cand, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}], empty_week(), NOW)
        prev = planning.materialize_plan(prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}], days, empty_week(), NOW) | {
            'plan_run_id': 'prev-run',
            'calendar_revision': 'r1',
        }
        with Session(self.engine) as session:
            session.add(TaskRanking(id=1, snapshot='prev-snap', result=json.dumps(prev)))
            session.add(IntegrationSetting(name='ameba_plan', config=json.dumps({'run_id': 'prev-run', 'schedule': prev['schedule']})))
            session.commit()

        async def fail_after_progress(ready, task_id=0, checked_contexts=(), refresh=False):
            if refresh:
                raise HTTPException(409, 'Требуется перепланирование')
            ameba.apply_confirmed_progress(1, 45, 0, confirmed_by_user=True, confirmation_id='cid-fail-1')
            raise HTTPException(502, 'План не прошёл проверку')

        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'request_ranking', side_effect=fail_after_progress), \
             patch('agent_memory.read', return_value=[]):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(ameba.rank_all(force=True))
            self.assertEqual(ctx.exception.status_code, 502)

            with Session(self.engine) as session:
                task_row = session.get(Task, 1)
                self.assertEqual(task_row.completed_work_minutes, 45)
                self.assertEqual(task_row.status, 'in_progress')
                self.assertEqual(json.loads(session.get(TaskRanking, 1).result)['plan_run_id'], 'prev-run')
            self.assertTrue(ameba.ranking_status()['needs_refresh'])

        async def succeed_after_progress(ready, task_id=0, checked_contexts=(), refresh=False):
            ameba.apply_confirmed_progress(1, 15, 45, confirmed_by_user=True, confirmation_id='cid-ok-1')
            mat = planning.materialize_plan(prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}], days, empty_week(), NOW)
            return mat | {
                'plan_run_id': 'run-ok',
                'calendar_revision': 'r1',
                'confirmed_progress': [{
                    'task_id': 1, 'add_minutes': 15, 'base_completed_minutes': 45,
                    'confirmed_by_user': True, 'confirmation_id': 'cid-ok-1', 'persisted': True,
                }],
            }

        calendar.reset_ameba_plan('run-ok')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'request_ranking', side_effect=succeed_after_progress), \
             patch('agent_memory.read', return_value=[]):
            out = asyncio.run(ameba.rank_all(force=True))
        self.assertEqual(out['progress_updated_count'], 1)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 60)

        # Постороннее изменение прогресса во время планирования по-прежнему отклоняется с HTTP 409.
        async def concurrent_mutation(ready, task_id=0, checked_contexts=(), refresh=False):
            with Session(self.engine) as local:
                row = local.get(Task, 1)
                row.completed_work_minutes = 99
                local.add(row)
                local.commit()
            return prev | {'plan_run_id': 'run-conflict', 'calendar_revision': 'r1'}

        calendar.reset_ameba_plan('run-conflict')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'request_ranking', side_effect=concurrent_mutation), \
             patch('agent_memory.read', return_value=[]):
            with self.assertRaises(HTTPException) as ctx409:
                asyncio.run(ameba.rank_all(force=True))
            self.assertEqual(ctx409.exception.status_code, 409)

    def test_duplicate_progress_confirmation_is_idempotent(self):
        self._seed_task(title='Лабораторная', completed=0)
        first = ameba.apply_confirmed_progress(
            1, 45, 0, confirmed_by_user=True, confirmation_id='dup-cid-1',
        )
        second = ameba.apply_confirmed_progress(
            1, 45, 0, confirmed_by_user=True, confirmation_id='dup-cid-1',
        )
        self.assertEqual((first['applied'], first['duplicate'], first['completed_work_minutes']), (True, False, 45))
        self.assertEqual((second['applied'], second['duplicate'], second['completed_work_minutes']), (False, True, 45))
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 45)

        # Дубликат с тем же confirmation_id внутри confirmed_progress в rank_all применяется только один раз.
        cand = proposal_payload([1], [task_item(1, 75)], [work_period(1, '2026-11-23', '2026-11-24', 75)])
        prop, _, days = planning.validate_proposal(cand, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 45}], empty_week(), NOW)
        mat = planning.materialize_plan(prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 45}], days, empty_week(), NOW)
        dup_plan = mat | {
            'plan_run_id': 'run-dup',
            'calendar_revision': 'r1',
            'confirmed_progress': [
                {'task_id': 1, 'add_minutes': 15, 'base_completed_minutes': 45, 'confirmed_by_user': True, 'confirmation_id': 'dup-in-plan'},
                {'task_id': 1, 'add_minutes': 15, 'base_completed_minutes': 45, 'confirmed_by_user': True, 'confirmation_id': 'dup-in-plan'},
            ],
        }
        calendar.reset_ameba_plan('run-dup')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=dup_plan)), \
             patch('agent_memory.read', return_value=[]):
            out = asyncio.run(ameba.rank_all(force=True))
        self.assertEqual(out['progress_updated_count'], 1)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 60)

    def test_memory_persistence_failure_keeps_committed_plan(self):
        self._seed_task(title='Учёба', completed=0)
        cand = proposal_payload(
            [1], [task_item(1, 120)], [work_period(1, '2026-11-23', '2026-11-23', 120)],
            preferences=[{'text': 'Сложные задачи делаю с утра', 'scope': 'persistent', 'task_id': None, 'key': 'ameba.morning'}],
        )
        prop, _, days = planning.validate_proposal(cand, [{'id': 1, 'title': 'Учёба', 'completed_work_minutes': 0}], empty_week(), NOW)
        mat = planning.materialize_plan(prop, [{'id': 1, 'title': 'Учёба', 'completed_work_minutes': 0}], days, empty_week(), NOW)

        calendar.reset_ameba_plan('mem-fail')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=mat | {'plan_run_id': 'mem-fail', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]), \
             patch('agent_memory.save', side_effect=RuntimeError('memory failed')):
            out = asyncio.run(ameba.rank_all())

        self.assertEqual(out['memory_saved_count'], 0)
        self.assertEqual(out['memory_failed_count'], 1)
        self.assertTrue(out.get('memory_warning'))
        with Session(self.engine) as session:
            saved = session.get(TaskRanking, 1)
            self.assertEqual(json.loads(saved.result)['plan_run_id'], 'mem-fail')
            cfg = session.get(IntegrationSetting, 'ameba')
            self.assertIsNotNone(cfg.last_success)
            self.assertEqual(cfg.last_error, out['memory_warning'])

        with patch('agent_memory.read', side_effect=RuntimeError('read failed')):
            self.assertEqual(ameba._commit_persistent_preferences([{'key': 'k', 'text': 'Запомнить'}]), (0, 1))


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
