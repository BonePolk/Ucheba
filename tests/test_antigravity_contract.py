"""Контрактные тесты реального Codex CLI app-server + Responses-мост + сквозные циклы 4 агентов + многопроцессная история."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from fastapi import HTTPException
from openai_codex import ApprovalMode, AsyncCodex, ExternalMessage, Sandbox

from common import antigravity, antigravity_bridge, codex_harness
from common.identity import current_user_id

CODEX_BIN = shutil.which('codex') or '/usr/bin/codex'


def _mp_worker_save(state_dir: str, owner_id: int, scope: str, connection_id: str, idx: int, err_q) -> None:
    """Дочерний процесс для проверки межпроцессной блокировки flock и сохранения call_meta."""
    try:
        os.environ['UCHEBA_BRIDGE_STATE_DIR'] = state_dir
        os.environ['INTEGRATION_ENCRYPTION_KEY'] = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
        session = antigravity_bridge.AntigravityBridgeSession(
            owner_id=owner_id,
            agent_scope=scope,
            connection_id=connection_id,
        )
        rid = f'resp_mp_{idx}'
        cid = f'call_mp_{idx}'
        session.call_meta[cid] = {
            'safe_name': 'mcp__ucheba__calendar_list',
            'provider_id': f'pid_{idx}',
            'thought_signature': f'sig_{idx}',
        }
        session.responses_history[rid] = [
            {'type': 'message', 'role': 'user', 'content': f'turn {idx}'},
            {'type': 'function_call', 'call_id': cid, 'namespace': 'mcp__ucheba', 'name': 'calendar_list', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': cid, 'output': '{"events":[]}'},
        ]
        if not session._save_persisted_state():
            err_q.put(f'worker {idx}: save returned False ({session.last_persistence_error})')
    except BaseException as exc:
        err_q.put(f'worker {idx}: {type(exc).__name__}: {exc}')


class _CodexRuntimeHarness:
    """Запускает реальный /usr/bin/codex app-server с изолированным CODEX_HOME и локальным stdio MCP."""

    def __init__(self, work_root: Path):
        self.root = work_root
        self.codex_home = work_root / 'codex_home'
        self.codex_home.mkdir(parents=True, exist_ok=True)
        self.state_dir = work_root / 'bridge_state'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.uid = os.getuid()
        self._orig_unpack = struct.unpack
        self._orig_build = codex_harness.build_codex_config
        self._orig_serve_mcp = codex_harness.serve_mcp

    def _fake_unpack(self, fmt, buf):
        res = self._orig_unpack(fmt, buf)
        if fmt == '3i' and len(res) == 3 and res[1] == self.uid:
            return (res[0], 10000 + (current_user_id.get() or 1), res[2])
        return res

    def _build_codex_config(self, auth_mode: str, **kwargs):
        cfg, provider = self._orig_build(auth_mode, **kwargs)
        args = list(cfg.launch_args_override)
        # Заменяем wrapper `codex-worker <uid> <mode>` на реальный `/usr/bin/codex` на хосте, сохранив все `-c` флаги.
        assert args[0] == codex_harness.WORKER_BIN
        new_args = (CODEX_BIN, *args[3:])
        env = dict(cfg.env or {})
        env['CODEX_HOME'] = str(self.codex_home)
        env['PATH'] = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
        cfg.launch_args_override = new_args
        cfg.env = env
        return cfg, provider

    @asynccontextmanager
    async def _serve_mcp(self, adapter):
        async with self._orig_serve_mcp(adapter) as mcp_cfg:
            mcp_cfg['mcp_servers']['ucheba']['command'] = sys.executable
            yield mcp_cfg

    @asynccontextmanager
    async def _scratch_env(self, writable: bool = False):
        d = self.root / ('work_rw' if writable else 'work_ro')
        d.mkdir(parents=True, exist_ok=True)
        yield d, {'allow_login_shell': False}

    def patches(self, targets=()):
        (self.root / '.codex' / 'users' / '1').mkdir(parents=True, exist_ok=True)
        stack = [
            patch.dict(os.environ, {
                'UCHEBA_BRIDGE_STATE_DIR': str(self.state_dir),
                'INTEGRATION_ENCRYPTION_KEY': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
            }),
            patch.object(codex_harness, 'HOME', self.root),
            patch.object(codex_harness, 'MCP_STDIO_SCRIPT', str(ROOT / 'common' / 'mcp_stdio.py')),
            patch.object(codex_harness, 'prepare_codex_user', lambda: None),
            patch.object(codex_harness.os, 'chown', lambda *a, **kw: None),
            patch.object(codex_harness.struct, 'unpack', side_effect=self._fake_unpack),
            patch.object(codex_harness, 'build_codex_config', side_effect=self._build_codex_config),
            patch.object(codex_harness, 'serve_mcp', side_effect=self._serve_mcp),
            patch.object(codex_harness, 'scratch_env', side_effect=self._scratch_env),
        ]
        for mod in targets:
            if hasattr(mod, 'serve_mcp'):
                stack.append(patch.object(mod, 'serve_mcp', side_effect=self._serve_mcp))
            if hasattr(mod, 'scratch_env'):
                stack.append(patch.object(mod, 'scratch_env', side_effect=self._scratch_env))
        return stack


class _DummyProgress:
    def __init__(self, *args, **kwargs):
        self.record = {'events': []}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def begin_response(self): pass
    def finish_response(self, *args, **kwargs): pass
    def update(self, **kw): self.record.update(kw)
    def add(self, msg, *a, **kw): self.record['events'].append({'message': msg})
    def note(self, msg): return {'ok': True}
    def model_event(self, ev): pass
    def reasoning_delta(self, *a): pass
    def summary(self, *a): return ''


@unittest.skipUnless(Path(CODEX_BIN).exists(), 'codex binary not available')
class CodexContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_contract_all_core_scenarios(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = _CodexRuntimeHarness(Path(tmpdir))
            captured_requests = []
            turn_mode = {'kind': 'multi_tool'}

            async def mock_stream(tok, env, **kwargs):
                captured_requests.append(env)
                req_idx = len(captured_requests)
                if turn_mode['kind'] == 'multi_tool':
                    if req_idx == 1:
                        yield {
                            'candidates': [{
                                'content': {'parts': [
                                    {'thoughtSignature': 'sig-codex-1', 'functionCall': {'name': 'mcp__ucheba__calendar_list', 'args': {}, 'id': 'g-1'}},
                                    {'functionCall': {'name': 'mcp__ucheba__calendar_save_events', 'args': {'events': [{'id': 'e1'}]}, 'id': 'g-2'}},
                                ]},
                                'finishReason': 'STOP',
                            }],
                            'usageMetadata': {'promptTokenCount': 60, 'candidatesTokenCount': 25, 'totalTokenCount': 85},
                        }
                    else:
                        yield {
                            'candidates': [{
                                'content': {'parts': [{'text': 'Два инструмента выполнены'}]},
                                'finishReason': 'STOP',
                            }],
                            'usageMetadata': {'promptTokenCount': 90, 'candidatesTokenCount': 12, 'totalTokenCount': 102},
                        }
                elif turn_mode['kind'] == 'max_tokens':
                    yield {
                        'candidates': [{'content': {'parts': [{'text': 'Обрезанный текст'}]}, 'finishReason': 'MAX_TOKENS'}],
                        'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 32, 'totalTokenCount': 52},
                    }
                elif turn_mode['kind'] == 'premature_eof':
                    yield {
                        'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__calendar_list', 'args': {}, 'id': 'g-eof'}}]}}],
                    }
                elif turn_mode['kind'] == 'quota_429':
                    raise antigravity.AntigravityError(
                        'Превышен лимит запросов или квота Google Antigravity (HTTP 429, QUOTA_EXHAUSTED): quota reset after 2h',
                        status_code=429,
                        error_status='QUOTA_EXHAUSTED',
                        retryable=False,
                        quota_exceeded=True,
                    )
                    yield {}
                elif turn_mode['kind'] == 'slow_cancel':
                    await asyncio.sleep(30)
                    yield {'candidates': [{'content': {'parts': [{'text': 'late'}]}, 'finishReason': 'STOP'}]}

            executed_tools = []
            async def tool_handler(name, args):
                executed_tools.append((name, args))
                return {'ok': True, 'tool': name}, []

            tools = [
                {'name': 'calendar_list', 'description': 'List', 'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}},
                {'name': 'calendar_save_events', 'description': 'Save', 'parameters': {'type': 'object', 'properties': {'events': {'type': 'array', 'items': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id'], 'additionalProperties': False}}}, 'required': ['events'], 'additionalProperties': False}},
            ]

            tok = current_user_id.set(1)
            session_mock = AsyncMock(return_value={
                'access_token': 'tok-c', 'expires_at': 9999999999.0,
                'cloudcode_url': 'https://daily-cloudcode-pa.googleapis.com',
                'project_id': 'proj-c', 'connection_id': 'cid-contract-1',
                'user_agent': 'antigravity/test',
            })
            patches = harness.patches() + [
                patch.object(antigravity_bridge, 'fetch_backend_session', session_mock),
                patch.object(antigravity_bridge, 'stream_generate_content', side_effect=mock_stream),
                patch.object(codex_harness, 'fetch_live_catalog', AsyncMock(return_value={'models': [], 'connection_id': 'cid-contract-1', 'config_version': 1})),
            ]
            for p in patches:
                p.start()
            try:
                adapter = codex_harness.McpToolAdapter(tools, tool_handler, _DummyProgress())
                # Сценарии 1-6 + 9: ExternalMessage -> MCP namespace tools -> 2 functionCalls -> MCP execution -> thoughtSignature -> STOP
                async with codex_harness.serve_mcp(adapter) as mcp_cfg, codex_harness.open_codex(
                    'Straus', auth_mode='antigravity', base_url='https://daily-cloudcode-pa.googleapis.com',
                    allow_shell=False, connection_id='cid-contract-1',
                ) as (client, provider_id):
                    thread = await codex_harness.start_codex_thread(
                        client, 'Straus', cwd=str(harness.root), ephemeral=False,
                        model='gemini-4-neon-xhigh', model_provider=provider_id,
                        approval_mode=ApprovalMode.deny_all, sandbox=Sandbox.read_only,
                        developer_instructions='Ты Straus.', config=mcp_cfg,
                    )
                    outcome = await codex_harness.run_codex_turn(
                        thread,
                        ExternalMessage(tool_name='task_manager', content='{"instruction":"Проверь календарь"}'),
                        label='Straus', progress=_DummyProgress(), adapter=adapter, client=client, effort='medium',
                    )
                    self.assertTrue(outcome.completed)
                    self.assertEqual(outcome.status, 'completed')
                    self.assertEqual(outcome.text, 'Два инструмента выполнены')
                    self.assertEqual([c[0] for c in executed_tools], ['calendar_list', 'calendar_save_events'])

                    # Проверяем реальный формат первого и второго Cloud Code envelope от Codex 0.169.0
                    env0 = captured_requests[0]['request']
                    decls0 = {d['name'] for d in env0['tools'][0]['functionDeclarations']}
                    self.assertEqual(decls0, {'mcp__ucheba__calendar_list', 'mcp__ucheba__calendar_save_events'})
                    self.assertEqual(env0['contents'][0]['role'], 'user')
                    self.assertIn('Проверь календарь', env0['contents'][0]['parts'][0]['text'])
                    self.assertFalse(any('functionResponse' in p for c in env0['contents'] for p in c['parts']))

                    env1 = captured_requests[1]['request']
                    model_parts = [p for c in env1['contents'] if c['role'] == 'model' for p in c['parts']]
                    self.assertEqual(model_parts[0].get('thoughtSignature'), 'sig-codex-1')
                    resp_parts = [p['functionResponse'] for c in env1['contents'] if c['role'] == 'user' for p in c['parts'] if 'functionResponse' in p]
                    self.assertEqual([r['id'] for r in resp_parts], ['g-1', 'g-2'])

                    # Сценарий 10: обрыв SSE без finishReason (даже с functionCall) -> НЕ completed.
                    turn_mode['kind'] = 'premature_eof'
                    eof_out = await codex_harness.run_codex_turn(
                        thread, 'Продолжи', label='Straus', progress=_DummyProgress(),
                        adapter=adapter, client=client, effort='medium',
                    )
                    self.assertFalse(eof_out.completed)
                    self.assertEqual(eof_out.status, 'failed')

                    # Сценарий 11: MAX_TOKENS -> не completed.
                    turn_mode['kind'] = 'max_tokens'
                    max_out = await codex_harness.run_codex_turn(
                        thread, 'Продолжи', label='Straus', progress=_DummyProgress(),
                        adapter=adapter, client=client, effort='medium',
                    )
                    self.assertFalse(max_out.completed)

                    # Сценарий 12: HTTP 429 QUOTA_EXHAUSTED -> реальная причина в error_detail через error_sink.
                    turn_mode['kind'] = 'quota_429'
                    q_out = await codex_harness.run_codex_turn(
                        thread, 'Повтори', label='Straus', progress=_DummyProgress(),
                        adapter=adapter, client=client, effort='medium',
                    )
                    self.assertFalse(q_out.completed)
                    self.assertIn('QUOTA_EXHAUSTED', q_out.error_detail or '')

                    # Сценарий 15: отмена агентского хода (CancelledError) чисто прерывает и закрывает соединение.
                    turn_mode['kind'] = 'slow_cancel'
                    cancel_task = asyncio.create_task(
                        codex_harness.run_codex_turn(
                            thread, 'Долгий запрос', label='Straus', progress=_DummyProgress(),
                            adapter=adapter, client=client, effort='medium',
                        )
                    )
                    await asyncio.sleep(0.35)
                    cancel_task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await cancel_task

                # Сценарии 7 & 13: previous_response_id после перезапуска моста работает при том же connection_id,
                # и не используется при другом connection_id.
                turn_mode['kind'] = 'multi_tool'
                captured_requests.clear()
                async with antigravity_bridge.antigravity_responses_bridge(
                    label='Straus', connection_id='cid-contract-1',
                ) as (url2, key2):
                    saved_file = antigravity_bridge._bridge_state_file(1, agent_scope='Straus', connection_id='cid-contract-1')
                    sess_loaded = antigravity_bridge.AntigravityBridgeSession(
                        owner_id=1, agent_scope='Straus', connection_id='cid-contract-1', state_path=saved_file,
                    )
                    prev_rid = next(iter(sess_loaded.responses_history))
                    import httpx
                    async with httpx.AsyncClient() as http:
                        r_ok = await http.post(
                            f'{url2}/responses',
                            headers={'Authorization': f'Bearer {key2}'},
                            json={'model': 'gemini-4-neon-xhigh', 'previous_response_id': prev_rid, 'input': 'Новый вопрос', 'stream': False},
                        )
                        self.assertEqual(r_ok.status_code, 200)
                async with antigravity_bridge.antigravity_responses_bridge(
                    label='Straus', connection_id='cid-contract-2',
                ) as (url3, key3):
                    import httpx
                    async with httpx.AsyncClient() as http:
                        r_denied = await http.post(
                            f'{url3}/responses',
                            headers={'Authorization': f'Bearer {key3}'},
                            json={'model': 'gemini-4-neon-xhigh', 'previous_response_id': prev_rid, 'input': 'Чужой вопрос', 'stream': False},
                        )
                        self.assertEqual(r_denied.status_code, 400)
                        self.assertEqual(r_denied.json()['error']['code'], 'previous_response_not_found')
            finally:
                for p in reversed(patches):
                    p.stop()
                current_user_id.reset(tok)


@unittest.skipUnless(Path(CODEX_BIN).exists(), 'codex binary not available')
class FourAgentsE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_straus_monocle_ameba_arrodes_and_compat_modes(self):
        sys.path[:0] = [
            str(ROOT / 'services' / 'straus'),
            str(ROOT / 'services' / 'monocle'),
            str(ROOT / 'services' / 'ameba'),
            str(ROOT / 'services' / 'arrodes'),
        ]
        import importlib.util

        def _load_service_mod(name: str, rel: str):
            mod_dir = str((ROOT / rel).parent)
            sys.path.insert(0, mod_dir)
            try:
                spec = importlib.util.spec_from_file_location(name, ROOT / rel)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
            finally:
                if mod_dir in sys.path:
                    sys.path.remove(mod_dir)

        straus_agent = _load_service_mod('e2e_straus_agent', 'services/straus/agent.py')
        monocle_main = _load_service_mod('e2e_monocle_main', 'services/monocle/main.py')
        ameba_main = _load_service_mod('e2e_ameba_main', 'services/ameba/main.py')
        arrodes_runner = _load_service_mod('e2e_arrodes_runner', 'services/arrodes/runner.py')

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            harness = _CodexRuntimeHarness(root)
            active_agent = {'name': 'straus', 'step': 0, 'cid': 'cid-e2e-1'}

            async def mock_stream(tok, env, **kwargs):
                active_agent['step'] += 1
                step = active_agent['step']
                kind = active_agent['name']
                if kind == 'straus':
                    if step == 1:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__calendar_list', 'args': {}, 'id': 's1'}}]}, 'finishReason': 'STOP'}]}
                    elif step == 2:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__calendar_save_events', 'args': {'events': [{
                            'id': '', 'title': 'Пара математика', 'summary': 'Ауд. 101', 'task_id': None,
                            'date': '2026-11-24', 'start': '10:00', 'end': '11:30', 'kind': 'busy',
                            'repeat_unit': 'none', 'repeat_interval': 1, 'repeat_count': None,
                            'week_pattern': 'all', 'parity_anchor': None,
                        }]}, 'id': 's2'}}]}, 'finishReason': 'STOP'}]}
                    elif step == 3:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__calendar_finish', 'args': {'status': 'changed', 'reason': 'Добавлена пара'}, 'id': 's3'}}]}, 'finishReason': 'STOP'}]}
                    else:
                        yield {'candidates': [{'content': {'parts': [{'text': 'Готово'}]}, 'finishReason': 'STOP'}]}
                elif kind == 'monocle':
                    if step == 1:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__read_user_memory', 'args': {}, 'id': 'm1'}}]}, 'finishReason': 'STOP'}]}
                    else:
                        yield {'candidates': [{'content': {'parts': [{'text': json.dumps({
                            'status': 'ready',
                            'summary': 'Контекст собран по памяти',
                            'time_estimate': '1 ч',
                            'effort': 'Низкая',
                            'impact': 'Зачёт',
                            'full_markdown': '## Разбор\nПрочитана память.',
                            'task_update': {'title': None},
                            'subtasks': [],
                            'questions': [],
                        }, ensure_ascii=False)}]}, 'finishReason': 'STOP'}]}
                elif kind == 'ameba':
                    if step == 1:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__get_task_details', 'args': {'task_ids': [1], 'mode': 'full', 'context_offset': 0, 'context_limit': 4000}, 'id': 'a1'}}]}, 'finishReason': 'STOP'}]}
                    elif step == 2:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__propose_plan', 'args': {
                            'task_ids': [1],
                            'tasks': [{
                                'task_id': 1,
                                'remaining_minutes': 60,
                                'reason': 'Важная задача',
                                'deadline_risk': None,
                            }],
                            'periods': [{
                                'kind': 'work',
                                'task_id': 1,
                                'start_date': '2026-11-24',
                                'end_date': '2026-11-24',
                                'estimated_minutes': 60,
                                'goal': 'Сделать задачу #1',
                                'context_hint': None,
                                'reason': 'Подходящее окно',
                            }],
                            'blocked_dates': [],
                            'preferences': [],
                            'explanation': 'Запланировано на 2026-11-24.',
                        }, 'id': 'a2'}}]}, 'finishReason': 'STOP'}]}
                    else:
                        yield {'candidates': [{'content': {'parts': [{'text': 'План готов'}]}, 'finishReason': 'STOP'}]}
                elif kind == 'arrodes':
                    if step == 1:
                        yield {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'mcp__ucheba__list_tasks', 'args': {'query': '', 'status': '', 'offset': 0}, 'id': 'r1'}}]}, 'finishReason': 'STOP'}]}
                    else:
                        yield {'candidates': [{'content': {'parts': [{'text': 'Выполнено по задаче #1.'}]}, 'finishReason': 'STOP'}]}

            async def _fake_session(*a, **kw):
                return {
                    'access_token': 'tok-e2e', 'expires_at': 9999999999.0,
                    'cloudcode_url': 'https://daily-cloudcode-pa.googleapis.com',
                    'project_id': 'proj-e2e', 'connection_id': active_agent['cid'],
                    'user_agent': 'antigravity/test',
                }

            tok = current_user_id.set(1)
            arrodes_work = root / 'arrodes_work'
            arrodes_home = root / 'arrodes_home'
            arrodes_work.mkdir(exist_ok=True)
            arrodes_home.mkdir(exist_ok=True)
            patches = harness.patches((straus_agent, monocle_main, ameba_main, arrodes_runner)) + [
                patch.object(antigravity_bridge, 'fetch_backend_session', side_effect=_fake_session),
                patch.object(antigravity_bridge, 'stream_generate_content', side_effect=mock_stream),
                patch.object(codex_harness, 'fetch_live_catalog', AsyncMock(return_value={
                    'models': [{'id': 'gemini-4-neon-xhigh', 'context_window': 128000, 'max_input_tokens': 128000, 'max_output_tokens': 8192}],
                    'connection_id': 'cid-e2e-1',
                    'config_version': 1,
                })),
                patch('common.agent_history.history_request', AsyncMock(return_value={'id': 1, 'history': []})),
                patch.object(straus_agent, 'Progress', _DummyProgress),
                patch.object(monocle_main, 'Progress', _DummyProgress),
                patch.object(ameba_main, 'Progress', _DummyProgress),
                patch.object(straus_agent, 'memory_tool', AsyncMock(return_value=[])),
                patch.object(monocle_main, 'memory_tool', AsyncMock(return_value={'facts': []})),
                patch.object(ameba_main, 'memory_request', AsyncMock(return_value=[])),
                patch.object(ameba_main, 'memory_tool', AsyncMock(return_value=[])),
                patch.object(arrodes_runner, 'WORK', arrodes_work),
                patch.object(arrodes_runner, 'HOME', arrodes_home),
                patch.object(arrodes_runner, 'sandbox_available', AsyncMock(return_value=True)),
                patch.object(arrodes_runner.os, 'chown', lambda *a, **kw: None),
                patch.object(arrodes_runner.os, 'fchown', lambda *a, **kw: None),
                patch.object(arrodes_runner, 'tasks_request', AsyncMock(return_value={'tasks': [{'id': 1, 'title': 'Лаба'}]})),
                patch.object(arrodes_runner, 'open_local', side_effect=lambda p, name, flags: os.open(p / name, flags, 0o600)),
            ]
            for p in patches:
                p.start()
            try:
                # 1. Straus E2E
                active_agent.update(name='straus', step=0)
                st_res = await straus_agent.run.__wrapped__({
                    'config': {'auth_mode': 'antigravity', 'model': 'gemini-4-neon-xhigh', 'connection_id': 'cid-e2e-1', 'allow_browser': False, 'ask_clarifications': False},
                    'instruction': 'Добавь пару',
                    'calendar': {'timezone': 'Europe/Moscow', 'events': []},
                    'tasks': [],
                })
                self.assertEqual(st_res['status'], 'changed')
                self.assertEqual(len(st_res['calendar']['events']), 1)

                # 2. Monocle E2E
                active_agent.update(name='monocle', step=0)
                with patch.object(monocle_main.materials, 'model_input', return_value=[]), \
                     patch.object(monocle_main.materials, 'browser_site', return_value=None):
                    mo_res = await monocle_main.run_agent(
                        ('antigravity', '', 'https://daily-cloudcode-pa.googleapis.com', 'gemini-4-neon-xhigh'),
                        MagicMock(),
                        1,
                        {'task_id': 1, 'task': {'title': 'Лаба'}, 'subject': '', 'owner_notes': ''},
                        _DummyProgress(),
                        options={'allow_browser': False, 'ask_clarifications': False},
                    )
                self.assertEqual(mo_res['status'], 'ready')
                self.assertIn('Контекст собран', mo_res['summary'])

                # 3. Ameba E2E
                active_agent.update(name='ameba', step=0)
                am_res = await ameba_main.rank.__wrapped__({
                    'run_id': 'e2e-ameba-1',
                    'config': {'auth_mode': 'antigravity', 'model': 'gemini-4-neon-xhigh', 'connection_id': 'cid-e2e-1', 'planning_style': 'balanced'},
                    'tasks': [{'id': 1, 'title': 'Задача 1', 'priority_hint': 'high', 'completed_work_minutes': 0, 'deadline': '2026-11-28'}],
                    'now': '2026-11-24T10:00:00+03:00',
                    'weekly_schedule': {'timezone': 'Europe/Moscow', 'events': []},
                })
                self.assertEqual(am_res['schedule']['version'], 3)
                self.assertEqual(am_res['task_ids'], [1])

                # 4. Arrodes E2E + metadata + resume vs account change
                active_agent.update(name='arrodes', step=0, cid='cid-e2e-1')
                resume_calls = []
                orig_start_thread = arrodes_runner.start_codex_thread
                async def spy_start_thread(client, label, *, resume_id=None, **kw):
                    resume_calls.append(resume_id)
                    return await orig_start_thread(client, label, resume_id=resume_id, **kw)

                with patch.object(arrodes_runner, 'start_codex_thread', side_effect=spy_start_thread), \
                     patch.object(arrodes_runner.Tools, 'call', AsyncMock(side_effect=[
                         ({'tasks': [{'id': 1}]}, []),
                         ({'status': 'saved'}, []),
                         ({'tasks': [{'id': 1}]}, []),
                         ({'status': 'saved'}, []),
                         ({'tasks': [{'id': 1}]}, []),
                         ({'status': 'saved'}, []),
                     ])):
                    arr_auth = {'auth_mode': 'antigravity', 'base_url': 'https://daily-cloudcode-pa.googleapis.com'}
                    arr_opts_c1 = {'connection_id': 'cid-e2e-1', 'allow_shell': False, 'allow_browser': False, 'ask_clarifications': False}
                    run1_res = await arrodes_runner.execute(
                        'run_e2e_1',
                        {'run_id': 'run_e2e_1', 'model': 'gemini-4-neon-xhigh', 'instruction': 'Выполни задачу #1'},
                        _DummyProgress(),
                        auth=arr_auth,
                        options=arr_opts_c1,
                    )
                    self.assertIn('Выполнено', run1_res['summary'])
                    saved_meta = json.loads(arrodes_runner.metadata('run_e2e_1').read_text())
                    self.assertEqual(saved_meta['antigravity_connection_id'], 'cid-e2e-1')

                    # Повторное возобновление тем же connection_id -> передает resume_id!
                    active_agent.update(name='arrodes', step=0, cid='cid-e2e-1')
                    await arrodes_runner.execute(
                        'run_e2e_2',
                        {'run_id': 'run_e2e_2', 'resume_run_id': 'run_e2e_1', 'model': 'gemini-4-neon-xhigh', 'instruction': 'Продолжи'},
                        _DummyProgress(),
                        auth=arr_auth,
                        options=arr_opts_c1,
                    )
                    self.assertEqual(resume_calls[-1], saved_meta['thread_id'])

                    # Возобновление ПОСЛЕ смены Google connection_id -> НЕ использует старый thread_id!
                    active_agent.update(name='arrodes', step=0, cid='cid-e2e-2')
                    arr_opts_c2 = {'connection_id': 'cid-e2e-2', 'allow_shell': False, 'allow_browser': False, 'ask_clarifications': False}
                    await arrodes_runner.execute(
                        'run_e2e_3',
                        {'run_id': 'run_e2e_3', 'resume_run_id': 'run_e2e_1', 'model': 'gemini-4-neon-xhigh', 'instruction': 'Продолжи на новом аккаунте'},
                        _DummyProgress(),
                        auth=arr_auth,
                        options=arr_opts_c2,
                    )
                    self.assertIsNone(resume_calls[-1])

                # 5. Совместимость конфигов OpenAI OAuth и API Key без реальных провайдеров.
                cfg_oauth, prov_oauth = codex_harness.build_codex_config('oauth')
                self.assertIsNone(prov_oauth)
                cfg_api, prov_api = codex_harness.build_codex_config('api_key', api_key='sk-test', base_url='https://lenec.tech/v1')
                self.assertEqual(prov_api, codex_harness.API_PROVIDER_ID)
                self.assertEqual(cfg_api.env['UCHEBA_API_KEY'], 'sk-test')
            finally:
                for p in reversed(patches):
                    p.stop()
                current_user_id.reset(tok)


class HistoryHardeningAndMultiprocessTests(unittest.TestCase):
    def test_multiprocess_history_writes_corruption_leftover_tmp_and_logical_truncation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {
                'UCHEBA_BRIDGE_STATE_DIR': tmpdir,
                'INTEGRATION_ENCRYPTION_KEY': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
            }):
                # 1. Многопроцессная конкурентная запись в один state-файл.
                ctx = multiprocessing.get_context('spawn')
                err_q = ctx.Queue()
                procs = [
                    ctx.Process(target=_mp_worker_save, args=(tmpdir, 7, 'Straus', 'cid-mp-1', i, err_q))
                    for i in range(6)
                ]
                for p in procs:
                    p.start()
                for p in procs:
                    p.join(timeout=15)
                    self.assertEqual(p.exitcode, 0)
                self.assertTrue(err_q.empty(), f'MP errors: {[err_q.get() for _ in range(err_q.qsize())]}')

                reader = antigravity_bridge.AntigravityBridgeSession(owner_id=7, agent_scope='Straus', connection_id='cid-mp-1')
                for i in range(6):
                    self.assertIn(f'resp_mp_{i}', reader.responses_history)
                    self.assertEqual(reader.call_meta.get(f'call_mp_{i}', {}).get('provider_id'), f'pid_{i}')

                # 2. Разные agent_scope одного пользователя не смешиваются.
                other_scope = antigravity_bridge.AntigravityBridgeSession(owner_id=7, agent_scope='Ameba', connection_id='cid-mp-1')
                self.assertEqual(other_scope.responses_history, {})

                # 3. Повреждённый файл и остаток прерванного .tmp после сбоя записи не ломают восстановление и очищаются при новой записи.
                corrupt_path = reader.state_path
                leftover_tmp = corrupt_path.parent / f'{corrupt_path.name}.tmp.99999.deadbeef'
                leftover_tmp.write_text('partial-crash-data', encoding='ascii')
                corrupt_path.write_text('not-a-valid-fernet-blob', encoding='ascii')
                corrupted = antigravity_bridge.AntigravityBridgeSession(owner_id=7, agent_scope='Straus', connection_id='cid-mp-1')
                self.assertEqual(corrupted.responses_history, {})
                self.assertTrue(bool(corrupted.last_persistence_error))
                corrupted.responses_history['resp_clean'] = [{'type': 'message', 'role': 'user', 'content': 'after crash'}]
                self.assertTrue(corrupted._save_persisted_state())
                self.assertIsNone(corrupted.last_persistence_error)
                self.assertTrue(leftover_tmp.exists())  # Уникальный tmp другого упавшего процесса не мешает атомарной замене.

                # 4. Усечение истории при >512 KiB сохраняет логическую целостность пары functionCall/functionResponse для Gemini.
                big_sess = antigravity_bridge.AntigravityBridgeSession(owner_id=7, agent_scope='Monocle', connection_id='cid-big-1')
                history = [{'type': 'message', 'role': 'user', 'content': 'Стартовый запрос'}]
                for i in range(120):
                    cid = f'call_big_{i}'
                    big_sess.call_meta[cid] = {'safe_name': 'read_file', 'provider_id': f'p_{i}'}
                    history.append({'type': 'function_call', 'call_id': cid, 'name': 'read_file', 'arguments': json.dumps({'path': f'f_{i}', 'pad': 'A' * 6000})})
                    history.append({'type': 'function_call_output', 'call_id': cid, 'output': json.dumps({'content': 'B' * 6000})})
                history.append({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Финал'}]})
                big_sess.responses_history['resp_big'] = history
                self.assertTrue(big_sess._save_persisted_state())

                reloaded_big = antigravity_bridge.AntigravityBridgeSession(owner_id=7, agent_scope='Monocle', connection_id='cid-big-1')
                self.assertIn('resp_big', reloaded_big.responses_history)
                # После усечения previous_response_id НЕ должен падать с invalid_tool_sequence!
                meta, _ = reloaded_big.translate_request({
                    'model': 'gemini-4-neon-xhigh',
                    'previous_response_id': 'resp_big',
                    'tools': [{'type': 'function', 'name': 'read_file', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}],
                    'input': 'Уточни ответ',
                })
                self.assertEqual(meta['contents'][-1]['role'], 'user')
                # Каждая functionResponse имеет предшествующую model part.
                for idx_c, content in enumerate(meta['contents']):
                    if any('functionResponse' in p for p in content['parts']):
                        self.assertGreater(idx_c, 0)
                        self.assertEqual(meta['contents'][idx_c - 1]['role'], 'model')


if __name__ == '__main__':
    unittest.main()
