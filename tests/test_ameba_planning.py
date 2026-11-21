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
    load_type='mixed',
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
        'load_type': load_type,
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
            {
                'id': 42,
                'title': 'Переезд',
                'completed_work_minutes': 0,
                'owner_notes': 'Удобно заезжать с вещами по дороге после университета',
            },
            {'id': 43, 'title': 'Лабораторная', 'completed_work_minutes': 0},
        ]
        cand = proposal_payload(
            [42, 43],
            [
                assess(42, 150, 180, splittable=True, effort='medium', load_type='practical', reason='Можно перевозить постепенно'),
                assess(43, 210, 240, splittable=True, effort='high', load_type='cognitive', reason='Требуется спокойный день'),
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
        self.assertTrue(res['schedule']['valid'])
        self.assertFalse(res['schedule']['feasible'])
        self.assertFalse(res['schedule']['fully_scheduled'])
        self.assertGreater(res['schedule']['remaining'][0]['remaining_work_minutes'], 0)

    def test_feasible_vs_deadline_risk_separation(self):
        tasks = [{'id': 1, 'title': 'Курсовая', 'deadline': '2026-11-25T18:00:00Z', 'completed_work_minutes': 0}]
        cand = proposal_payload(
            [1],
            [assess(1, 180, 180, policy='finish_before', effort='medium', deadline_risk='Запас небольшой, лучше не откладывать')],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 90), ('2026-11-24', 90)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(cand, tasks, empty_week(), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(), NOW)
        self.assertTrue(res['schedule']['valid'])
        self.assertTrue(res['schedule']['feasible'])
        self.assertTrue(res['schedule']['fully_scheduled'])
        self.assertEqual(res['schedule']['remaining_minutes'], 0)
        self.assertEqual(len(res['schedule']['deadline_conflicts']), 1)
        self.assertFalse(res['schedule']['deadline_conflicts'][0]['infeasible'])
        self.assertEqual(len(res['schedule']['deadline_risks']), 1)

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

    def test_same_day_available_from_and_deadline_intersection_and_competition(self):
        # Открытие 15:00 и дедлайн 16:00 в один день -> доступно не больше 60 мин.
        tasks = [{
            'id': 1,
            'title': 'Короткое окно',
            'deadline': '2026-11-24T16:00:00+00:00',
            'completed_work_minutes': 0,
        }]
        too_long = proposal_payload(
            [1],
            [assess(1, 180, 180, splittable=True, policy='expires', available_from='2026-11-24T15:00:00+00:00')],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 180)])],
        )
        cand_prop, issues, _, _ = planning.validate_proposal(too_long, tasks, empty_week(), NOW)
        self.assertIsNotNone(cand_prop)
        self.assertTrue(any(i['code'] == 'deadline_day_over_capacity' for i in issues))

        # Две задачи делят общее окно 14:00..16:00 (120 мин) в один день -> суммарно 180 > 120 отклоняется.
        comp_tasks = [
            {'id': 1, 'title': 'Первая', 'deadline': '2026-11-24T16:00:00+00:00', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Вторая', 'deadline': '2026-11-24T16:00:00+00:00', 'completed_work_minutes': 0},
        ]
        comp = proposal_payload(
            [1, 2],
            [
                assess(1, 90, 90, policy='expires', available_from='2026-11-24T14:00:00+00:00'),
                assess(2, 90, 90, policy='expires', available_from='2026-11-24T14:00:00+00:00'),
            ],
            [
                work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 90)]),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 90)]),
            ],
        )
        _, comp_issues, _, _ = planning.validate_proposal(comp, comp_tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'deadline_day_over_capacity' for i in comp_issues))

    def test_refresh_rejects_invalidated_plan_when_calendar_shrinks(self):
        tasks = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}]
        cand = proposal_payload(
            [1],
            [assess(1, 240, 240, splittable=False)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 240)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(cand, tasks, empty_week(), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(), NOW)

        shrunk_events = [
            {'id': 'busy-1', 'kind': 'busy', 'title': 'Конференция', 'date': '2026-11-24', 'start': '08:00', 'end': '20:00'},
        ]
        with self.assertRaises(ValueError):
            planning.refresh_schedule(res, tasks, empty_week(events=shrunk_events), NOW)

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

    def test_hard_deadline_cannot_be_downgraded_to_soft(self):
        # 1. Будущий дедлайн из БД: модель называет его soft и ставит работу после срока -> отказ.
        tasks = [{'id': 1, 'title': 'Отчёт', 'deadline': '2026-11-25T18:00:00Z', 'completed_work_minutes': 0}]
        downgraded = proposal_payload(
            [1],
            [assess(1, 120, 120, policy='soft')],
            [work_period(1, '2026-11-26', '2026-11-26', [('2026-11-26', 120)])],
        )
        _, issues, _, _ = planning.validate_proposal(downgraded, tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'after_deadline' for i in issues))

        # 2. Просроченная задача с недоступностью после срока: soft не спасает -> deadline_passed.
        expired_tasks = [{
            'id': 1,
            'title': 'Тест в СДО',
            'deadline': '2026-11-22T18:00:00Z',
            'owner_notes': 'После дедлайна работа недоступна, форма закроется',
            'completed_work_minutes': 0,
        }]
        expired_prop = proposal_payload(
            [1],
            [assess(1, 60, 60, policy='soft')],
            [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 60)])],
        )
        _, exp_issues, _, _ = planning.validate_proposal(expired_prop, expired_tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'deadline_passed' for i in exp_issues))

        # 3. Явно подтверждённый мягкий срок в контексте задачи -> soft допускается.
        soft_tasks = [{
            'id': 1,
            'title': 'Пет-проект',
            'deadline': '2026-11-25T18:00:00Z',
            'owner_notes': 'Мягкий дедлайн, можно сдать позже без штрафа',
            'completed_work_minutes': 0,
        }]
        soft_prop = proposal_payload(
            [1],
            [assess(1, 120, 120, policy='soft')],
            [work_period(1, '2026-11-26', '2026-11-26', [('2026-11-26', 120)])],
        )
        _, soft_issues, _, _ = planning.validate_proposal(soft_prop, soft_tasks, empty_week(), NOW)
        self.assertEqual(soft_issues, [])

        # 4. Часовой пояс Europe/Moscow (UTC+3): 22:00Z -> 01:00 следующего дня (ночной сон 00:00..07:00).
        msk_tasks = [{'id': 1, 'title': 'Дедлайн ночью', 'deadline': '2026-11-24T22:00:00Z', 'completed_work_minutes': 0}]
        msk_late = proposal_payload(
            [1],
            [assess(1, 60, 60, policy='soft')],
            [work_period(1, '2026-11-25', '2026-11-25', [('2026-11-25', 60)])],
        )
        _, msk_issues, _, _ = planning.validate_proposal(msk_late, msk_tasks, empty_week(tz='Europe/Moscow'), NOW)
        self.assertTrue(any(i['code'] == 'deadline_day_over_capacity' for i in msk_issues))

        # 5. Отдельная подготовка заранее (до available_from основной задачи) допускается, но не после её дедлайна.
        prep_tasks = [
            {'id': 1, 'title': 'Подготовка к контрольной', 'completed_work_minutes': 0},
            {
                'id': 2,
                'title': 'Контрольная',
                'deadline': '2026-11-27T11:00:00+00:00',
                'available_from': '2026-11-26T14:00:00+00:00',
                'completed_work_minutes': 0,
            },
        ]
        prep_ok = proposal_payload(
            [1, 2],
            [
                assess(1, 120, 120, policy='soft'),
                assess(2, 90, 90, splittable=False, policy='expires', depends_on=[1], available_from=None),
            ],
            [
                work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 120)]),
                work_period(2, '2026-11-26', '2026-11-26', [('2026-11-26', 90)]),
            ],
        )
        _, prep_issues, _, _ = planning.validate_proposal(prep_ok, prep_tasks, empty_week(), NOW)
        self.assertEqual(prep_issues, [])

        # Модель не может обойти доверенный task['available_from'], передав available_from=None.
        early_main = proposal_payload(
            [1, 2],
            [
                assess(1, 60, 60, policy='soft'),
                assess(2, 90, 90, splittable=False, policy='expires', depends_on=[1], available_from=None),
            ],
            [
                work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 60)]),
                work_period(2, '2026-11-25', '2026-11-25', [('2026-11-25', 90)]),
            ],
        )
        _, early_issues, _, _ = planning.validate_proposal(early_main, prep_tasks, empty_week(), NOW)
        self.assertTrue(any(i['code'] == 'before_available_from' for i in early_issues))

    def test_progress_updates_blocked_on_schedule_requests_and_future_intent(self):
        tasks = [{'id': 17, 'title': 'Лабораторная', 'completed_work_minutes': 0}]
        bad_cases = [
            ('Освободи пятницу', 'set', 120, 'Освободи пятницу'),
            ('Перенеси лабораторную на субботу', 'add', 60, 'Перенеси лабораторную на субботу'),
            ('Завтра буду делать лабораторную два часа', 'add', 120, 'Завтра буду делать лабораторную два часа'),
            ('Освободи пятницу', 'add', 120, 'Я сделал два часа лабораторной'),
        ]
        for feedback_text, op, mins, quote in bad_cases:
            cand = proposal_payload(
                [17],
                [assess(17, 240, 240)],
                [work_period(17, '2026-11-28', '2026-11-28', [('2026-11-28', 120)])],
                progress_updates=[{'task_id': 17, 'operation': op, 'minutes': mins, 'evidence_quote': quote}],
            )
            _, issues, _, _ = planning.validate_proposal(
                cand, tasks, empty_week(), NOW, has_feedback=True, user_feedback=feedback_text,
            )
            self.assertTrue(
                any(i['code'] in ('unverified_progress_update', 'invalid_progress_set') for i in issues),
                msg=f'Expected rejection for {feedback_text!r}, got {issues}',
            )

    def test_progress_updates_explicit_add_set_and_ambiguous_fraction(self):
        tasks = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}]
        # 1. Явная добавка 2 часов принимается.
        add_ok = proposal_payload(
            [1],
            [assess(1, 300, 300)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 120)])],
            progress_updates=[{'task_id': 1, 'operation': 'add', 'minutes': 120, 'evidence_quote': 'Я сделал два часа лабораторной'}],
        )
        _, issues, _, budgets = planning.validate_proposal(
            add_ok, tasks, empty_week(), NOW, user_feedback='Я сделал два часа лабораторной',
        )
        self.assertEqual(issues, [])
        self.assertEqual(budgets[1]['confirmed'], 180)

        # 2. operation='set' без слов «всего/итого» отклоняется при наличии прогресса.
        bad_set = proposal_payload(
            [1],
            [assess(1, 300, 300)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 180)])],
            progress_updates=[{'task_id': 1, 'operation': 'set', 'minutes': 120, 'evidence_quote': 'Я сделал два часа лабораторной'}],
        )
        _, set_issues, _, _ = planning.validate_proposal(
            bad_set, tasks, empty_week(), NOW, user_feedback='Я сделал два часа лабораторной',
        )
        self.assertTrue(any(i['code'] == 'invalid_progress_set' for i in set_issues))

        # 3. Явный итог «всего сделал 3 часа» разрешает set.
        good_set = proposal_payload(
            [1],
            [assess(1, 300, 300)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 120)])],
            progress_updates=[{'task_id': 1, 'operation': 'set', 'minutes': 180, 'evidence_quote': 'Всего сделал 3 часа лабораторной'}],
        )
        _, ok_set_issues, _, set_budgets = planning.validate_proposal(
            good_set, tasks, empty_week(), NOW, user_feedback='Всего сделал 3 часа лабораторной',
        )
        self.assertEqual(ok_set_issues, [])
        self.assertEqual(set_budgets[1]['confirmed'], 180)

        # 4. «Примерно половину» при широкой/неизвестной оценке отклоняется как неоднозначное.
        amb_tasks = [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0, 'time_estimate': 'недостаточно данных'}]
        amb_prop = proposal_payload(
            [1],
            [assess(1, 60, 300)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 90)])],
            progress_updates=[{'task_id': 1, 'operation': 'add', 'minutes': 90, 'evidence_quote': 'Я сделал примерно половину лабораторной'}],
        )
        _, amb_issues, _, _ = planning.validate_proposal(
            amb_prop, amb_tasks, empty_week(), NOW, user_feedback='Я сделал примерно половину лабораторной',
        )
        self.assertTrue(any(i['code'] == 'ambiguous_progress_update' for i in amb_issues))

        # 5. Две лабораторные («Лабораторная 1» и «Лабораторная 2») + «сделал 2 часа лабораторной» -> неоднозначность.
        two_labs = [
            {'id': 1, 'title': 'Лабораторная 1', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Лабораторная 2', 'completed_work_minutes': 0},
        ]
        amb_two = proposal_payload(
            [1, 2],
            [assess(1, 180, 180), assess(2, 180, 180)],
            [
                work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 60)]),
                work_period(2, '2026-11-25', '2026-11-25', [('2026-11-25', 180)]),
            ],
            progress_updates=[{'task_id': 2, 'operation': 'add', 'minutes': 120, 'evidence_quote': 'Я сделал 2 часа лабораторной'}],
        )
        _, two_issues, _, _ = planning.validate_proposal(
            amb_two, two_labs, empty_week(), NOW, user_feedback='Я сделал 2 часа лабораторной',
        )
        self.assertTrue(any(i['code'] == 'ambiguous_progress_update' for i in two_issues))

        # Уточнение через ask_user снимает неоднозначность.
        _, ans_issues, _, ans_budgets = planning.validate_proposal(
            amb_two | {
                'periods': [
                    work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 180)]),
                    work_period(2, '2026-11-25', '2026-11-25', [('2026-11-25', 60)]),
                ],
                'progress_updates': [{'task_id': 2, 'operation': 'add', 'minutes': 120, 'evidence_quote': 'По лабораторной 2 сделал 2 часа'}],
            },
            two_labs,
            empty_week(),
            NOW,
            user_feedback='Я сделал 2 часа лабораторной',
            user_answers=[{'question': 'По какой лабораторной вы работали?', 'answer': 'По лабораторной 2 сделал 2 часа'}],
        )
        self.assertEqual(ans_issues, [])
        self.assertEqual(ans_budgets[2]['confirmed'], 120)

    def test_short_tasks_no_fake_remaining(self):
        self.assertEqual(planning.format_hours(1, 1), '1 мин')
        self.assertEqual(planning.format_hours(5, 5), '5 мин')
        self.assertEqual(planning.format_hours(9, 9), '9 мин')
        self.assertEqual(planning.format_hours(2, 7), '2–7 мин')

        cases = [
            (1, 1, 0, 1),
            (5, 5, 0, 5),
            (9, 9, 0, 9),
            (10, 10, 0, 10),
            (25, 25, 20, 5),
            (2, 7, 0, 5),
        ]
        for t_min, t_max, done, alloc in cases:
            tasks = [{'id': 1, 'title': 'Короткое дело', 'completed_work_minutes': done}]
            cand = proposal_payload(
                [1],
                [assess(1, t_min, t_max, effort='low', load_type='practical')],
                [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', alloc)])],
            )
            prop, issues, days, budgets = planning.validate_proposal(cand, tasks, empty_week(), NOW)
            self.assertEqual(issues, [], msg=f'Unexpected issues for {(t_min, t_max, done, alloc)}: {issues}')
            self.assertLessEqual(budgets[1]['target'], budgets[1]['rem_max'])
            res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(), NOW)
            self.assertEqual(res['schedule']['remaining'][0]['remaining_work_minutes'], 0)
            self.assertTrue(res['schedule']['fully_scheduled'])

        # Нулевой остаток: задача уже выполнена, работа не требуется и не создаётся фиктивный остаток.
        done_tasks = [{'id': 1, 'title': 'Уже готово', 'completed_work_minutes': 60}]
        done_cand = proposal_payload([1], [assess(1, 60, 60)], [])
        prop0, issues0, days0, budgets0 = planning.validate_proposal(done_cand, done_tasks, empty_week(), NOW)
        self.assertEqual(issues0, [])
        self.assertEqual(budgets0[1]['target'], 0)
        res0 = planning.materialize_plan(prop0, done_tasks, days0, budgets0, empty_week(), NOW)
        self.assertEqual(res0['schedule']['remaining_minutes'], 0)
        self.assertTrue(res0['schedule']['fully_scheduled'])

    def test_temporary_user_constraints_hard_vs_soft_and_conflict(self):
        tasks = [
            {'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Переезд', 'completed_work_minutes': 0},
        ]
        # 1. Прямой запрет пятницы не даёт разместить туда работу (даже без blocked_dates от модели).
        bad = proposal_payload(
            [1, 2],
            [assess(1, 90, 90), assess(2, 60, 60)],
            [
                work_period(1, '2026-11-27', '2026-11-27', [('2026-11-27', 90)]),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 60)]),
            ],
        )
        _, issues, _, _ = planning.validate_proposal(bad, tasks, empty_week(), NOW, user_feedback='Пятницу оставь свободной')
        self.assertTrue(any(i['code'] == 'blocked_by_user_constraint' for i in issues))

        # 2. Мягкое пожелание «желательно побольше отдыхать» не блокирует пятницу жёстко.
        days = planning.analyze_days(empty_week(), NOW)
        self.assertEqual(planning.extract_explicit_blocked_days('В пятницу желательно побольше отдыхать', days, tasks=tasks), [])
        soft_plan = proposal_payload(
            [1, 2],
            [assess(1, 60, 60), assess(2, 60, 60)],
            [
                work_period(1, '2026-11-27', '2026-11-27', [('2026-11-27', 60)]),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 60)]),
            ],
            preferences=[{
                'text': 'В пятницу желательно побольше отдыхать',
                'scope': 'once',
                'strength': 'hard',
                'blocked_dates': ['2026-11-27'],
                'task_id': None,
                'key': None,
            }],
        )
        _, soft_issues, _, _ = planning.validate_proposal(
            soft_plan, tasks, empty_week(), NOW, user_feedback='В пятницу желательно побольше отдыхать',
        )
        self.assertEqual(soft_issues, [])

        # 3. Ограничение scope='task' блокирует только целевую задачу на указанную дату.
        task_block = proposal_payload(
            [1, 2],
            [assess(1, 60, 60), assess(2, 60, 60)],
            [
                work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 60)]),
                work_period(2, '2026-11-27', '2026-11-27', [('2026-11-27', 60)]),
            ],
        )
        _, tb_issues, _, _ = planning.validate_proposal(
            task_block, tasks, empty_week(), NOW, user_feedback='Лабораторную не ставь на пятницу',
        )
        self.assertEqual(tb_issues, [])

        # 4. Конфликт закрепления на пятницу и запрета пятницы: запрет не нарушается, противоречие отражается явно.
        conflict_adjustments = {'task_pins': {'1': {'target_date': '2026-11-27', 'not_before': None}}}
        conflict_plan = proposal_payload(
            [1, 2],
            [assess(1, 90, 90), assess(2, 60, 60)],
            [
                work_period(1, '2026-11-26', '2026-11-26', [('2026-11-26', 90)]),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 60)]),
            ],
            preferences=[{
                'text': 'Пятницу оставить свободной',
                'scope': 'once',
                'strength': 'hard',
                'blocked_dates': ['2026-11-27'],
                'task_id': None,
                'key': None,
            }],
        )
        prop_c, c_issues, c_days, c_budgets = planning.validate_proposal(
            conflict_plan, tasks, empty_week(), NOW,
            adjustments=conflict_adjustments,
            user_feedback='Пятницу оставь свободной',
        )
        self.assertEqual(c_issues, [])
        res_c = planning.materialize_plan(prop_c, tasks, c_days, c_budgets, empty_week(), NOW)
        self.assertFalse(res_c['schedule']['feasible'])
        self.assertEqual(res_c['schedule']['blocked_dates'], ['2026-11-27'])
        self.assertTrue(any(c.get('kind') == 'constraint' for c in res_c['schedule']['deadline_conflicts']))
        with self.assertRaises(ValueError):
            planning.refresh_schedule(
                res_c,
                tasks,
                empty_week(),
                NOW,
                adjustments={'task_pins': {'1': {'target_date': '2026-11-27', 'shift_days': 1}}},
            )

    def test_contextual_workload_and_unverified_route_rejected(self):
        events = [
            {'id': 'uni-mon', 'kind': 'busy', 'title': 'Университет', 'date': '2026-11-23', 'start': '09:00', 'end': '15:00'},
        ]
        tasks = [
            {'id': 1, 'title': 'Посылка из ПВЗ', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Сложная лабораторная', 'completed_work_minutes': 0},
        ]
        # 1. Безусловный неподтверждённый маршрут в context_hint отклоняется.
        unverified = proposal_payload(
            [1, 2],
            [
                assess(1, 45, 45, effort='medium', load_type='practical'),
                assess(2, 120, 120, effort='high', load_type='cognitive'),
            ],
            [
                work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 45)], hint='После университета по дороге домой забрать посылку'),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 120)]),
            ],
        )
        _, unv_issues, _, _ = planning.validate_proposal(unverified, tasks, empty_week(events=events), NOW)
        self.assertTrue(any(i['code'] == 'unverified_context_hint' for i in unv_issues))

        # 2. Условная формулировка или факт из памяти принимается.
        conditional = proposal_payload(
            [1, 2],
            unverified['tasks'],
            [
                work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 45)], hint='Если по дороге после университета, можно зайти в ПВЗ'),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 120)]),
            ],
        )
        _, cond_issues, _, _ = planning.validate_proposal(conditional, tasks, empty_week(events=events), NOW)
        self.assertEqual(cond_issues, [])

        _, mem_issues, _, _ = planning.validate_proposal(
            unverified,
            tasks,
            empty_week(events=events),
            NOW,
            user_memory=[{'key': 'user.pvz', 'agent': 'Владелец', 'text': 'Пункт выдачи ПВЗ находится по дороге из университета'}],
        )
        self.assertEqual(mem_issues, [])

        # 3. Тяжёлая задача (effort='high', 120 мин) в плотный учебный день отклоняется при свободных днях.
        heavy_dense = proposal_payload(
            [1, 2],
            [
                assess(1, 45, 45, effort='medium', load_type='practical'),
                assess(2, 120, 120, effort='high', load_type='cognitive'),
            ],
            [
                work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 45)]),
                work_period(2, '2026-11-23', '2026-11-23', [('2026-11-23', 120)]),
            ],
        )
        _, heavy_issues, _, _ = planning.validate_proposal(heavy_dense, tasks, empty_week(events=events), NOW)
        self.assertTrue(any(i['code'] == 'heavy_day_overload' for i in heavy_issues))

        # 4. Практическая задача не может обойти общий лимит времени плотного дня.
        over_practical = proposal_payload(
            [1, 2],
            [
                assess(1, 240, 240, effort='low', load_type='practical'),
                assess(2, 120, 120, effort='high', load_type='cognitive'),
            ],
            [
                work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 240)]),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 120)]),
            ],
        )
        _, prac_issues, _, _ = planning.validate_proposal(over_practical, tasks, empty_week(events=events), NOW)
        self.assertTrue(any(i['code'] == 'over_capacity' for i in prac_issues))

    def test_eval_scenario_property_checker(self):
        move_scenario = next(s for s in EVAL_SCENARIOS if s['name'] == 'move_and_university')
        events = move_scenario['events']
        tasks = move_scenario['tasks']
        good = proposal_payload(
            [42, 43],
            [
                assess(42, 150, 180, splittable=True, effort='medium', load_type='practical'),
                assess(43, 210, 240, splittable=True, effort='high', load_type='cognitive'),
            ],
            [
                work_period(42, '2026-11-23', '2026-11-27', [('2026-11-23', 45), ('2026-11-25', 60), ('2026-11-27', 45)], hint='По дороге после университета'),
                work_period(43, '2026-11-24', '2026-11-26', [('2026-11-24', 120), ('2026-11-26', 100)]),
                rest_period('2026-11-28', '2026-11-29'),
            ],
        )
        prop, issues, days, budgets = planning.validate_proposal(good, tasks, empty_week(events=events), NOW)
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, tasks, days, budgets, empty_week(events=events), NOW)
        self.assertEqual(evaluate_plan_properties(move_scenario, res), [])


class AgentLoop(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, actions, *, user_feedback='', memory_items=None, memory_mock=None, tasks=None, weekly_schedule=None):
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
                'tasks': tasks or [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}],
                'now': NOW,
                'weekly_schedule': weekly_schedule or empty_week(),
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
            preferences=[{
                'text': 'Эту пятницу оставить свободной',
                'scope': 'once',
                'strength': 'hard',
                'blocked_dates': ['2026-11-27'],
                'task_id': None,
                'key': None,
            }],
        )
        res = await self.run_agent(
            [[('propose_plan', cand)]],
            user_feedback='Эту пятницу оставь свободной',
            memory_mock=mem_mock,
        )
        self.assertEqual(res['schedule']['version'], 3)
        self.assertEqual(res['memory_saved_count'], 0)
        mem_mock.assert_not_called()

    async def test_scenario_e_persistent_habit_filtered_and_prepared_for_backend(self):
        mem_mock = AsyncMock(return_value=[])
        existing = [{'key': 'user.1', 'agent': 'Владелец', 'text': 'Учусь по утрам'}]
        cand = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-28', '2026-11-28', [('2026-11-28', 120)])],
            preferences=[
                {'text': 'Учусь по утрам', 'scope': 'persistent', 'strength': 'soft', 'blocked_dates': [], 'task_id': None, 'key': 'user.1'},  # дубль факта владельца
                {'text': 'Обычно сложную работу предпочитаю делать в свободные дни', 'scope': 'persistent', 'strength': 'soft', 'blocked_dates': [], 'task_id': None, 'key': 'hard_on_free_days'},
            ],
        )
        res = await self.run_agent(
            [[('propose_plan', cand)]],
            user_feedback='Обычно сложную работу делаю в свободные дни',
            memory_items=existing,
            memory_mock=mem_mock,
        )
        # Сервис Ameba возвращает подготовленные привычки, но не пишет в память до commit в backend.
        mem_mock.assert_not_called()
        self.assertEqual(res['memory_saved_count'], 0)
        self.assertEqual(len(res['persistent_preferences']), 1)
        self.assertEqual(res['persistent_preferences'][0]['key'], 'ameba.hard_on_free_days')

    async def test_repair_plan_preserves_untouched_tasks_and_assessments(self):
        tasks = [
            {'id': 1, 'title': 'Конспект', 'completed_work_minutes': 0},
            {'id': 2, 'title': 'Переезд', 'completed_work_minutes': 0},
        ]
        bad = proposal_payload(
            [1, 2],
            [
                assess(1, 90, 90, reason='Первая оценка'),
                assess(2, 120, 120, reason='Вторая оценка'),
            ],
            [
                work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 90)], goal='Сделать конспект'),
                work_period(2, '2026-11-24', '2026-11-24', [('2026-11-24', 500)], goal='Перевезти всё сразу'),
            ],
            explanation='Исходное объяснение плана.',
        )
        repair = {
            'task_ids': None,
            'task_replacements': [],
            'period_replacements': [
                {
                    'index': 1,
                    'period': work_period(
                        2,
                        '2026-11-24',
                        '2026-11-25',
                        [('2026-11-24', 60), ('2026-11-25', 60)],
                        goal='Перевезти постепенно',
                    ),
                },
            ],
            'remove_period_indices': [],
            'add_periods': [],
            'preferences': None,
            'progress_updates': None,
            'explanation': None,
        }
        res = await self.run_agent([[('propose_plan', bad), ('repair_plan', repair)]], tasks=tasks)
        self.assertEqual(res['task_ids'], [1, 2])
        self.assertEqual(res['explanation'], 'Исходное объяснение плана.')
        self.assertEqual([t['id'] for t in res['timeline']], [1, 2])
        self.assertEqual(res['schedule']['periods'][0]['goal'], 'Сделать конспект')
        self.assertEqual(res['schedule']['periods'][1]['minutes'], 120)

    async def test_repair_plan_without_candidate_returns_error(self):
        repair = {
            'task_ids': None,
            'task_replacements': [],
            'period_replacements': [],
            'remove_period_indices': [],
            'add_periods': [],
            'preferences': None,
            'progress_updates': None,
            'explanation': None,
        }
        with self.assertRaisesRegex(Exception, 'исходного предложения|не предложила корректный план'):
            await self.run_agent([[('repair_plan', repair)]])

    async def test_double_invalid_repair_stops_and_preserves_previous_plan(self):
        bad = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 500)])],
        )
        still_bad_repair = {
            'task_ids': None,
            'task_replacements': [],
            'period_replacements': [
                {'index': 0, 'period': work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 480)])},
            ],
            'remove_period_indices': [],
            'add_periods': [],
            'preferences': None,
            'progress_updates': None,
            'explanation': None,
        }
        with self.assertRaisesRegex(Exception, 'после исправления'):
            await self.run_agent([[('propose_plan', bad), ('repair_plan', still_bad_repair)]])

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
            preferences=[{'text': 'Удобно перевозить после университета', 'scope': 'task', 'strength': 'soft', 'blocked_dates': [], 'task_id': 1, 'key': None}],
            progress_updates=[{'task_id': 1, 'operation': 'add', 'minutes': 60, 'evidence_quote': 'уже перевёз треть'}],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand,
            [{'id': 1, 'title': 'Переезд', 'completed_work_minutes': 0}],
            empty_week(),
            NOW,
            has_feedback=True,
            user_feedback='уже перевёз треть, остальное после пар',
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

    def test_save_plan_feedback_without_session(self):
        ameba.save_plan_feedback('оставить пятницу свободной')
        self.assertEqual(ameba.plan_feedback(), 'оставить пятницу свободной')

    def test_progress_update_add_and_no_double_apply_on_retry(self):
        task = Task(id=1, title='Лабораторная', status='in_progress', completed_work_minutes=120)
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

        cand = proposal_payload(
            [1],
            [assess(1, 300, 300)],
            [work_period(1, '2026-11-23', '2026-11-24', [('2026-11-23', 90), ('2026-11-24', 60)])],
            progress_updates=[{'task_id': 1, 'operation': 'add', 'minutes': 30, 'evidence_quote': 'позанимался ещё 30 минут'}],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand,
            [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 120}],
            empty_week(),
            NOW,
            has_feedback=True,
            user_feedback='позанимался ещё 30 минут',
        )
        self.assertEqual(issues, [])
        materialized = planning.materialize_plan(prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 120}], days, budgets, empty_week(), NOW)
        stale_retry = json.loads(json.dumps(materialized))

        calendar.reset_ameba_plan('run_add')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=materialized | {'plan_run_id': 'run_add', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]):
            ameba.save_plan_feedback('позанимался ещё 30 минут')
            out = asyncio.run(ameba.rank_all())
        self.assertEqual(out['progress_updated_count'], 1)
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 150)

        # Повторное применение устаревшего результата с base=120 отклоняется и не даёт 180.
        calendar.reset_ameba_plan('run_retry')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=stale_retry | {'plan_run_id': 'run_retry', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]):
            with self.assertRaisesRegex(Exception, 'Прогресс задачи изменился'):
                asyncio.run(ameba.rank_all(force=True))
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 150)

    def test_persistent_memory_not_written_when_commit_fails(self):
        task = Task(id=1, title='Учёба', status='todo', completed_work_minutes=0)
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

        cand = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 120)])],
            preferences=[{'text': 'Сложные задачи делаю с утра', 'scope': 'persistent', 'strength': 'soft', 'blocked_dates': [], 'task_id': None, 'key': 'ameba.morning'}],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand, [{'id': 1, 'title': 'Учёба', 'completed_work_minutes': 0}], empty_week(), NOW, has_feedback=True,
        )
        self.assertEqual(issues, [])
        materialized = planning.materialize_plan(prop, [{'id': 1, 'title': 'Учёба', 'completed_work_minutes': 0}], days, budgets, empty_week(), NOW)

        saved_facts = []

        def fake_save_fact(fact, agent):
            saved_facts.append((fact, agent))
            return fact

        # Сбой commit/устаревшего run_id -> save_ameba_plan бросает ValueError до commit, память не пишется.
        calendar.reset_ameba_plan('other_run')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=json.loads(json.dumps(materialized)) | {'plan_run_id': 'stale_run', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]), \
             patch('agent_memory.save', side_effect=fake_save_fact):
            with self.assertRaises(ValueError):
                asyncio.run(ameba.rank_all())
        self.assertEqual(saved_facts, [])

        # Успешный commit -> persistent привычка сохраняется один раз.
        calendar.reset_ameba_plan('good_run')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=materialized | {'plan_run_id': 'good_run', 'calendar_revision': 'r1'})), \
             patch('agent_memory.read', return_value=[]), \
             patch('agent_memory.save', side_effect=fake_save_fact):
            out = asyncio.run(ameba.rank_all())
        self.assertEqual(out['memory_saved_count'], 1)
        self.assertEqual(len(saved_facts), 1)
        self.assertEqual(saved_facts[0][0]['key'], 'ameba.morning')

    def test_manual_shift_applies_or_marks_needs_replan(self):
        task = Task(id=1, title='Лабораторная', status='todo', completed_work_minutes=0)
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

        cand = proposal_payload(
            [1],
            [assess(1, 120, 120)],
            [work_period(1, '2026-11-23', '2026-11-23', [('2026-11-23', 120)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}], empty_week(), NOW,
        )
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}], days, budgets, empty_week(), NOW)
        with Session(self.engine) as session:
            session.add(TaskRanking(snapshot='initial', result=json.dumps(res | {'plan_run_id': 'r0'})))
            session.commit()

        async def fake_run_ranking(body, endpoint='/rank'):
            if endpoint != '/refresh':
                raise AssertionError('Expected /refresh')
            try:
                updated = planning.refresh_schedule(
                    body['previous_plan'],
                    body['tasks'],
                    planning.restore_week(body.get('weekly_schedule'), body['timezone']),
                    NOW,
                    adjustments=body.get('manual_adjustments'),
                )
            except ValueError as exc:
                raise ameba.HTTPException(409, str(exc)) from None
            return updated | {'plan_run_id': 'r_ref', 'calendar_revision': 'rev1'}

        calendar.reset_ameba_plan('r_ref')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(scheduler, 'run_ranking', side_effect=fake_run_ranking), \
             patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC', 'events': [], 'revision': 'rev1'}), \
             patch.object(ameba, 'read_week', return_value=empty_week()), \
             patch('agent_memory.read', return_value=[]):
            moved = asyncio.run(ameba.adjust_plan(task_pin={'task_id': 1, 'shift_days': 1}))
            self.assertFalse(moved['needs_replan'])
            self.assertEqual(moved['schedule']['periods'][0]['start_date'], '2026-11-24')

            # Если целевой день помечен как free, перенос туда невозможен без перепланирования -> needs_replan=True, старая дата 24 остаётся.
            calendar.reset_ameba_plan('r_fail')
            blocked = asyncio.run(ameba.adjust_plan(
                day_overrides={'2026-11-25': 'free'},
                task_pin={'task_id': 1, 'target_date': '2026-11-25'},
            ))
            self.assertTrue(blocked['needs_replan'])
            self.assertTrue(blocked['needs_refresh'])
            self.assertEqual(blocked['schedule']['periods'][0]['start_date'], '2026-11-24')

    def test_refresh_saved_plan_rejects_calendar_shrink_and_preserves_old(self):
        task = Task(id=1, title='Лабораторная', status='todo', completed_work_minutes=0)
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.commit()

        cand = proposal_payload(
            [1],
            [assess(1, 240, 240, splittable=False)],
            [work_period(1, '2026-11-24', '2026-11-24', [('2026-11-24', 240)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}], empty_week(), NOW,
        )
        self.assertEqual(issues, [])
        res = planning.materialize_plan(prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 0}], days, budgets, empty_week(), NOW)
        with Session(self.engine) as session:
            session.add(TaskRanking(snapshot='initial', result=json.dumps(res | {'plan_run_id': 'r0'})))
            session.commit()

        shrunk_week = empty_week(events=[
            {'id': 'busy-1', 'kind': 'busy', 'title': 'Занято весь день', 'date': '2026-11-24', 'start': '08:00', 'end': '20:00'},
        ])

        async def fake_run_ranking(body, endpoint='/rank'):
            try:
                return planning.refresh_schedule(
                    body['previous_plan'],
                    body['tasks'],
                    shrunk_week,
                    NOW,
                    adjustments=body.get('manual_adjustments'),
                ) | {'plan_run_id': 'r_shrink', 'calendar_revision': 'rev2'}
            except ValueError as exc:
                raise ameba.HTTPException(409, str(exc)) from None

        calendar.reset_ameba_plan('r_shrink')
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(scheduler, 'run_ranking', side_effect=fake_run_ranking), \
             patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC', 'events': shrunk_week['calendar_events'], 'revision': 'rev2'}), \
             patch.object(ameba, 'read_week', return_value=shrunk_week), \
             patch('agent_memory.read', return_value=[]):
            refreshed = asyncio.run(ameba.refresh_saved_plan())
        self.assertIsNone(refreshed)
        status = ameba.ranking_status()
        self.assertTrue(status['needs_refresh'])
        self.assertEqual(status['schedule']['periods'][0]['start_date'], '2026-11-24')

    def test_scenario_g_and_plan_expiry_uses_local_date(self):
        today_local = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        with patch.object(ameba, 'read_calendar', return_value={'timezone': 'UTC'}):
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 2, 'generated_date': today_local}}))
            self.assertTrue(ameba.plan_expired({'schedule': {'version': 3, 'generated_date': yesterday}}))
            self.assertFalse(ameba.plan_expired({'schedule': {'version': 3, 'generated_date': today_local}}))

    def test_backend_rejects_unverified_progress_and_preserves_previous_plan(self):
        task = Task(id=1, title='Лабораторная', status='in_progress', completed_work_minutes=60)
        previous = {'task_ids': [1], 'schedule': {'version': 3, 'start_at': NOW, 'periods': [{'id': 'old', 'kind': 'work', 'task_id': 1}]}}
        old_plan = {'run_id': 'old_run', 'schedule': previous['schedule'], 'revision': 'r1'}
        with Session(self.engine) as session:
            session.add(task)
            session.add(TaskContext(task_id=1, subject='', status='ready', task_snapshot=monocle_context.snapshot(task),
                                    usage=json.dumps({'context_policy': 5, 'full_markdown': 'ok'})))
            session.add(TaskRanking(snapshot='old_snap', result=json.dumps(previous)))
            session.add(IntegrationSetting(name='ameba_plan', config=json.dumps(old_plan)))
            session.commit()

        cand = proposal_payload(
            [1],
            [assess(1, 180, 180)],
            [work_period(1, '2026-11-28', '2026-11-28', [('2026-11-28', 120)])],
        )
        prop, issues, days, budgets = planning.validate_proposal(
            cand, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}], empty_week(), NOW,
        )
        self.assertEqual(issues, [])
        materialized = planning.materialize_plan(
            prop, [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}], days, budgets, empty_week(), NOW,
        )
        tampered = materialized | {
            'plan_run_id': 'bad_progress_run',
            'calendar_revision': 'r1',
            'progress_updates': [{'task_id': 1, 'operation': 'set', 'minutes': 120, 'evidence_quote': 'Перенеси лабораторную на субботу'}],
        }
        with patch.object(ameba, 'check_feature'), \
             patch.object(ameba, 'read_config', return_value={'enabled': True}), \
             patch.object(ameba, 'read_settings', return_value=({'base_url': 'u'}, {})), \
             patch.object(ameba, 'context_status', return_value='ready'), \
             patch.object(ameba, 'request_ranking', AsyncMock(return_value=tampered)), \
             patch('agent_memory.read', return_value=[]):
            ameba.save_plan_feedback('Перенеси лабораторную на субботу')
            with self.assertRaisesRegex(Exception, 'неподтверждённое изменение прогресса'):
                asyncio.run(ameba.rank_all(force=True))
        with Session(self.engine) as session:
            self.assertEqual(session.get(Task, 1).completed_work_minutes, 60)
            self.assertEqual(json.loads(session.get(TaskRanking, 1).result), previous)
        self.assertEqual(calendar.read_ameba_plan()['run_id'], 'old_run')

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


EVAL_SCENARIOS = [
    {
        'name': 'move_and_university',
        'description': 'Университет Пн/Ср/Пт, переезд по дороге, лабораторная с дедлайном, отдых на выходных.',
        'events': [
            {'id': f'uni-{d}', 'kind': 'busy', 'title': 'Университет', 'date': d, 'start': '09:00', 'end': '15:00'}
            for d in ('2026-11-23', '2026-11-25', '2026-11-27')
        ],
        'tasks': [
            {
                'id': 42,
                'title': 'Переезд',
                'completed_work_minutes': 0,
                'owner_notes': 'Удобно заезжать в новую квартиру по дороге после университета',
            },
            {
                'id': 43,
                'title': 'Лабораторная',
                'deadline': '2026-11-27T20:00:00Z',
                'completed_work_minutes': 0,
            },
        ],
        'user_feedback': '',
        'forbidden_dates': (),
        'prefer_days_for_task': {42: ('2026-11-23', '2026-11-25', '2026-11-27')},
        'prefer_free_days_for_task': {43: ('2026-11-24', '2026-11-26')},
        'require_rest': True,
        'expected_completed': {42: 0, 43: 0},
    },
    {
        'name': 'blocked_friday_and_reschedule',
        'description': 'Явный запрет пятницы и перенос без изменения подтверждённого прогресса.',
        'events': [],
        'tasks': [{'id': 1, 'title': 'Лабораторная', 'completed_work_minutes': 60}],
        'user_feedback': 'Пятницу оставь свободной, лабораторную перенеси на субботу',
        'forbidden_dates': ('2026-11-27',),
        'prefer_days_for_task': {},
        'prefer_free_days_for_task': {},
        'require_rest': False,
        'expected_completed': {1: 60},
    },
    {
        'name': 'prep_before_open_window',
        'description': 'Контрольная с узким окном и отдельная подготовка заранее.',
        'events': [],
        'tasks': [
            {'id': 1, 'title': 'Подготовка к контрольной', 'completed_work_minutes': 0},
            {
                'id': 2,
                'title': 'Контрольная',
                'deadline': '2026-11-27T11:00:00+00:00',
                'available_from': '2026-11-26T14:00:00+00:00',
                'completed_work_minutes': 0,
            },
        ],
        'user_feedback': '',
        'forbidden_dates': (),
        'prefer_days_for_task': {},
        'prefer_free_days_for_task': {},
        'require_rest': False,
        'expected_completed': {1: 0, 2: 0},
    },
    {
        'name': 'short_five_minute_task',
        'description': 'Короткая задача на 5 минут без фиктивного незапланированного остатка.',
        'events': [],
        'tasks': [{'id': 1, 'title': 'Отправить справку', 'completed_work_minutes': 0, 'owner_notes': 'Займёт ровно 5 минут'}],
        'user_feedback': '',
        'forbidden_dates': (),
        'prefer_days_for_task': {},
        'prefer_free_days_for_task': {},
        'require_rest': False,
        'expected_completed': {1: 0},
        'require_fully_scheduled': True,
    },
]


def evaluate_plan_properties(scenario: dict, result: dict) -> list[str]:
    """Оценивает ключевые свойства готового плана Ameba для сценария без жёсткой привязки к одному JSON."""
    failures: list[str] = []
    sched = (result or {}).get('schedule') or {}
    if not sched.get('valid'):
        failures.append('План не отмечен как valid=True')
    periods = sched.get('periods') or []
    forbidden = set(scenario.get('forbidden_dates') or ())
    task_allocs: dict[int, list[tuple[str, int]]] = {}
    has_rest = False
    for p in periods:
        if p.get('kind') == 'rest':
            has_rest = True
            continue
        tid = p.get('task_id')
        for alloc in p.get('allocations') or []:
            d_iso = alloc.get('date')
            mins = int(alloc.get('minutes') or 0)
            if d_iso in forbidden and mins > 0:
                failures.append(f'Нарушен запрет на дату {d_iso}')
            if tid is not None:
                task_allocs.setdefault(tid, []).append((d_iso, mins))
    if scenario.get('require_rest') and not has_rest:
        failures.append('В плане отсутствует период отдыха')
    if scenario.get('require_fully_scheduled') and not sched.get('fully_scheduled'):
        failures.append('Ожидалось полное размещение короткой задачи без остатка')
    for tid, expected_done in (scenario.get('expected_completed') or {}).items():
        plan_item = (result.get('plans') or {}).get(tid) or (result.get('plans') or {}).get(str(tid)) or {}
        if int(plan_item.get('completed_work_minutes', expected_done)) != expected_done:
            failures.append(f'Самовольно изменён подтверждённый прогресс задачи {tid}')
    for tid, preferred_days in (scenario.get('prefer_days_for_task') or {}).items():
        allocs = task_allocs.get(tid) or []
        pref_mins = sum(m for d, m in allocs if d in preferred_days)
        total_mins = sum(m for _, m in allocs)
        if total_mins == 0 or pref_mins * 2 < total_mins:
            failures.append(f'Задача {tid} недостаточно размещена в удобные контекстные дни {preferred_days}')
    for tid, free_days in (scenario.get('prefer_free_days_for_task') or {}).items():
        allocs = task_allocs.get(tid) or []
        free_mins = sum(m for d, m in allocs if d in free_days)
        total_mins = sum(m for _, m in allocs)
        if total_mins == 0 or free_mins * 2 < total_mins:
            failures.append(f'Сложная задача {tid} недостаточно размещена в свободные дни {free_days}')
    return failures


if __name__ == '__main__':
    unittest.main()
