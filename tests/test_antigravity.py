"""Локальные тесты нативной интеграции Google Antigravity и встроенного Responses-моста."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Literal
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import httpx
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from common import antigravity, antigravity_bridge, codex_harness, database, settings as settings_mod
from common.agent_issues import ISSUE_TOOL
from common.agent_memory import MEMORY_TOOLS
from common.agent_questions import QUESTION_TOOL
from common.browser_tools import BROWSER_TOOLS
from common.file_tools import FILE_TOOLS
from common.identity import current_user_id
from common.models.integration import IntegrationSetting
from common.models.user import SystemSetting, User
from common.progress import NOTE_TOOL
from common.security import decrypt, encrypt
from common.settings import public_settings, read_settings, resolve_agent_auth, save_settings
from integrations import antigravity as backend_ag
from routers import integrations as integrations_router, users as users_router


class _StrausEventInput(BaseModel):
    model_config = {'extra': 'forbid'}
    id: str
    title: str
    summary: str
    task_id: int | None
    date: str
    start: str
    end: str
    kind: Literal['task', 'busy', 'sleep', 'meal', 'break', 'work']
    repeat_unit: Literal['none', 'day', 'week', 'month']
    repeat_interval: int
    repeat_count: int | None
    week_pattern: Literal['all', 'even', 'odd']
    parity_anchor: str | None


class _StrausSaveEvents(BaseModel):
    model_config = {'extra': 'forbid'}
    events: list[_StrausEventInput]


def _make_straus_tools() -> list[dict]:
    def _t(name, desc, props, req=None):
        return {
            'type': 'function',
            'name': name,
            'description': desc,
            'strict': True,
            'parameters': {
                'type': 'object',
                'properties': props,
                'required': list(props) if req is None else req,
                'additionalProperties': False,
            },
        }

    return [
        QUESTION_TOOL,
        NOTE_TOOL,
        ISSUE_TOOL,
        *MEMORY_TOOLS,
        *FILE_TOOLS,
        *BROWSER_TOOLS,
        _t(
            'calendar_finish',
            'Завершить поручение.',
            {'status': {'type': 'string', 'enum': ['changed', 'unchanged', 'blocked']}, 'reason': {'type': 'string'}},
            ['status', 'reason'],
        ),
        _t('calendar_list', 'Прочитать события календаря.', {}, []),
        {
            'type': 'function',
            'name': 'calendar_save_events',
            'strict': True,
            'description': 'Добавить или изменить события.',
            'parameters': _StrausSaveEvents.model_json_schema(),
        },
        _t('calendar_remove_events', 'Удалить события.', {'ids': {'type': 'array', 'items': {'type': 'string'}}}, ['ids']),
        _t('calendar_timezone', 'Изменить часовой пояс.', {'timezone': {'type': 'string'}}, ['timezone']),
    ]


class AntigravityOAuthAndUrlHardeningTests(unittest.TestCase):
    def test_pkce_state_and_strict_callback_url_validation(self):
        verifier, challenge = antigravity.generate_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertEqual(len(challenge), 43)
        state = antigravity.generate_state()
        url = antigravity.build_oauth_url('cid-123', state, challenge)
        q = parse_qs(urlsplit(url).query)
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertEqual(q['state'], [state])
        self.assertEqual(q['client_id'], ['cid-123'])

        code = antigravity.parse_oauth_callback(
            f'https://antigravity.google/oauth-callback?state={state}&code=auth-code-1',
            state,
        )
        self.assertEqual(code, 'auth-code-1')

        # Голый код авторизации без полного URL и state строго отклоняется.
        for bad in (
            '4/0AbUR2WN-bare-code-without-url',
            'https://antigravity.google/oauth-callback?code=missing-state',
            'https://antigravity.google/oauth-callback?state=wrong&code=auth-code-1',
            f'https://evil.example/oauth-callback?state={state}&code=auth-code-1',
            f'https://antigravity.google/other-path?state={state}&code=auth-code-1',
            f'https://antigravity.google/oauth-callback?state={state}&error=access_denied',
        ):
            with self.assertRaises(ValueError):
                antigravity.parse_oauth_callback(bad, state)

    def test_cloudcode_url_allowlist_and_redirect_rejection(self):
        self.assertEqual(
            antigravity.validate_cloudcode_url('https://daily-cloudcode-pa.googleapis.com/'),
            'https://daily-cloudcode-pa.googleapis.com',
        )
        self.assertEqual(
            antigravity.validate_cloudcode_url('https://cloudcode-pa.googleapis.com'),
            'https://cloudcode-pa.googleapis.com',
        )
        for bad_url in (
            'http://daily-cloudcode-pa.googleapis.com',
            'https://127.0.0.1',
            'https://localhost',
            'https://evil.example.com',
            'https://user:pass@daily-cloudcode-pa.googleapis.com',
            'https://daily-cloudcode-pa.googleapis.com:8443',
            'https://daily-cloudcode-pa.googleapis.com/v1?x=1',
            'https://daily-cloudcode-pa.googleapis.com/custom',
        ):
            with self.assertRaises(ValueError):
                antigravity.validate_cloudcode_url(bad_url)

        async def _check_no_redirect():
            seen_follow = []

            class DummyRedirectClient:
                def __init__(self, *args, **kwargs):
                    seen_follow.append(kwargs.get('follow_redirects'))

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def post(self, *args, **kwargs):
                    return httpx.Response(302, headers={'location': 'https://evil.example/leak'})

            with patch('common.antigravity.httpx.AsyncClient', DummyRedirectClient):
                with self.assertRaises(antigravity.AntigravityError):
                    await antigravity.cloudcode_post('/v1internal:loadCodeAssist', {}, 'tok-secret')
            self.assertEqual(seen_follow, [False])

        asyncio.run(_check_no_redirect())

    def test_oauth_error_redaction_and_revocation_detection(self):
        err_code, msg, revoked = antigravity.safe_oauth_error(
            400,
            {'error': 'invalid_grant', 'error_description': 'secret-user-token-xyz'},
            is_refresh=True,
        )
        self.assertEqual(err_code, 'invalid_grant')
        self.assertTrue(revoked)
        self.assertNotIn('secret-user-token-xyz', msg)

        err_code2, msg2, revoked2 = antigravity.safe_oauth_error(
            400,
            {'error': 'invalid_request', 'error_description': 'Missing required parameter: client_secret'},
            is_refresh=False,
        )
        self.assertEqual(err_code2, 'invalid_request')
        self.assertFalse(revoked2)
        self.assertIn('Client Secret', msg2)


class AntigravityCatalogAndSchemaTests(unittest.TestCase):
    def test_models_normalization_preserves_neon_claude_and_missing_quotas(self):
        body = {
            'models': {
                'gemini-4-neon-low': {
                    'displayName': 'Gemini 4 Neon (Low)',
                    'maxTokens': 1000000,
                    'maxOutputTokens': 65536,
                    'quotaInfo': {'remainingFraction': 0.72, 'resetTime': '2026-11-24T10:00:00Z'},
                },
                'gemini-4-neon-high': {
                    'displayName': 'Gemini 4 Neon (High)',
                    'maxInputTokens': 1000000,
                    'maxOutputTokens': 65536,
                },
                'claude-sonnet-4-5': {
                    'displayName': 'Claude Sonnet 4.5',
                    'quotaInfo': {'remainingFraction': 0.0, 'resetTime': '2026-11-24T05:00:00Z'},
                },
                'internal-hidden': {
                    'isInternal': True,
                    'displayName': 'Internal',
                },
            },
            'agentModelSorts': [
                {'groups': [{'modelIds': ['gemini-4-neon-high', 'gemini-4-neon-low', 'claude-sonnet-4-5']}]}
            ],
        }
        visible = antigravity.normalize_models(body, include_internal=False)
        self.assertEqual([m['id'] for m in visible], ['gemini-4-neon-high', 'gemini-4-neon-low', 'claude-sonnet-4-5'])
        self.assertIsNone(visible[0]['remaining_fraction'])
        self.assertIsNone(visible[0]['remaining_percent'])
        self.assertTrue(visible[0]['available'])
        self.assertEqual(visible[1]['remaining_percent'], 72)
        self.assertFalse(visible[2]['available'])

        all_models = antigravity.normalize_models(body, include_internal=True)
        self.assertEqual(len(all_models), 4)

    def test_sanitize_schema_handles_defs_nullable_and_additional_properties(self):
        schema = {
            '$defs': {
                'Item': {
                    'type': 'object',
                    'additionalProperties': False,
                    'properties': {
                        'count': {'type': ['integer', 'null'], 'minimum': 1},
                        'note': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
                    },
                    'required': ['count', 'note'],
                }
            },
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'items': {'type': 'array', 'items': {'$ref': '#/$defs/Item'}},
                'empty_obj': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            },
            'required': ['items'],
        }
        cleaned = antigravity.sanitize_schema_for_cloudcode(schema)
        self.assertEqual(cleaned['type'], 'OBJECT')
        self.assertNotIn('additionalProperties', cleaned)
        self.assertNotIn('$defs', cleaned)
        item_schema = cleaned['properties']['items']['items']
        self.assertEqual(item_schema['type'], 'OBJECT')
        self.assertTrue(item_schema['properties']['count']['nullable'])
        self.assertEqual(item_schema['properties']['count']['type'], 'INTEGER')
        self.assertTrue(item_schema['properties']['note']['nullable'])
        self.assertEqual(item_schema['properties']['note']['type'], 'STRING')
        self.assertNotIn('properties', cleaned['properties']['empty_obj'])

    def test_test_generation_direct_accepts_stop_and_rejects_bad_finish(self):
        async def ok_stream(tok, env, **kwargs):
            yield {'candidates': [{'content': {'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}

        async def max_tokens_stream(tok, env, **kwargs):
            yield {'candidates': [{'content': {'parts': [{'text': 'Partial'}]}, 'finishReason': 'MAX_TOKENS'}]}

        with patch('common.antigravity.fetch_backend_session', AsyncMock(return_value={
            'access_token': 'tok-1',
            'project_id': 'projects/demo',
            'subscription_tier': 'PRO',
            'cloudcode_url': antigravity.DEFAULT_CLOUDCODE_URL,
        })):
            with patch('common.antigravity.stream_generate_content', side_effect=ok_stream):
                res = asyncio.run(antigravity.test_generation_direct('gemini-2.5-flash'))
                self.assertTrue(res['ok'])
                self.assertEqual(res['finish_reason'], 'STOP')
            with patch('common.antigravity.stream_generate_content', side_effect=max_tokens_stream):
                with self.assertRaises(antigravity.AntigravityError):
                    asyncio.run(antigravity.test_generation_direct('gemini-2.5-flash'))

    def test_fetch_live_catalog_discovers_project_and_normalizes(self):
        tok = current_user_id.set(1)
        try:
            with patch('common.antigravity.fetch_backend_session', AsyncMock(return_value={
                'access_token': 'tok-1',
                'project_id': '',
                'subscription_tier': '',
                'cloudcode_url': antigravity.DEFAULT_CLOUDCODE_URL,
                'include_internal': False,
            })), patch('common.antigravity.load_code_assist', AsyncMock(return_value={
                'project_id': 'projects/auto-proj',
                'subscription_tier': 'PRO',
            })), patch('common.antigravity.save_backend_project', AsyncMock()), patch(
                'common.antigravity.fetch_available_models',
                AsyncMock(return_value={'models': {'gemini-2.5-flash': {'displayName': 'Gemini 2.5 Flash'}}}),
            ):
                cat = asyncio.run(antigravity.fetch_live_catalog())
                self.assertEqual(cat['project_id'], 'projects/auto-proj')
                self.assertEqual(cat['subscription_tier'], 'PRO')
                self.assertEqual(cat['models'][0]['id'], 'gemini-2.5-flash')
        finally:
            current_user_id.reset(tok)


class AntigravityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            'os.environ',
            {
                'INTEGRATION_ENCRYPTION_KEY': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
                'UCHEBA_BRIDGE_STATE_DIR': self.tmpdir.name,
            },
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def test_responses_bridge_streaming_multi_tool_fernet_thought_signature_and_sse_events(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo-proj'

        app = antigravity_bridge.create_bridge_app(session, 'bridge-secret', label='Ameba')
        client = TestClient(app)

        captured_envelopes = []

        async def fake_stream(token, envelope, **kwargs):
            captured_envelopes.append(envelope)
            if len(captured_envelopes) == 1:
                yield {
                    'candidates': [{
                        'content': {
                            'parts': [
                                {'thought': True, 'text': 'private thinking must stay hidden', 'thoughtSignature': 'sig-abc-1'},
                                {'functionCall': {'id': 'fc_srv_1', 'name': 'get_task_details', 'args': {'task_ids': [10], 'context_offset': None}}},
                                {'functionCall': {'id': 'fc_srv_2', 'name': 'read_user_memory', 'args': {}}},
                            ]
                        },
                        'finishReason': 'STOP',
                    }],
                    'usageMetadata': {'promptTokenCount': 120, 'candidatesTokenCount': 25, 'thoughtsTokenCount': 15, 'totalTokenCount': 160},
                }
            else:
                yield {
                    'candidates': [{
                        'content': {'parts': [{'text': 'План готов.'}]},
                        'finishReason': 'STOP',
                    }],
                    'usageMetadata': {'promptTokenCount': 210, 'candidatesTokenCount': 12, 'totalTokenCount': 222},
                }

        tools = [
            {
                'type': 'function',
                'name': 'get_task_details',
                'description': 'Get details',
                'parameters': {
                    'type': 'object',
                    'properties': {'task_ids': {'type': 'array', 'items': {'type': 'integer'}}, 'context_offset': {'type': ['integer', 'null']}},
                    'required': ['task_ids', 'context_offset'],
                    'additionalProperties': False,
                },
            },
            {
                'type': 'function',
                'name': 'read_user_memory',
                'description': 'Read memory',
                'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
            },
        ]

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=fake_stream):
            with client.stream(
                'POST',
                '/v1/responses',
                headers={'Authorization': 'Bearer bridge-secret'},
                json={
                    'model': 'gemini-4-neon-high',
                    'instructions': 'Ты Ameba.',
                    'input': [{'type': 'message', 'role': 'user', 'content': 'Составь план'}],
                    'tools': tools,
                    'stream': True,
                },
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                raw_sse = b''.join(resp.iter_bytes()).decode('utf-8')

        self.assertNotIn('private thinking must stay hidden', raw_sse)
        self.assertNotIn('sig-abc-1', raw_sse)
        self.assertIn('get_task_details', raw_sse)
        self.assertIn('read_user_memory', raw_sse)
        self.assertIn('response.completed', raw_sse)

        events = []
        for block in raw_sse.strip().split('\n\n'):
            data_lines = [ln[5:].strip() for ln in block.splitlines() if ln.startswith('data:')]
            if data_lines:
                events.append(json.loads(data_lines[0]))
        self.assertEqual([e['sequence_number'] for e in events], list(range(len(events))))
        args_done = [e for e in events if e.get('type') == 'response.function_call_arguments.done']
        self.assertEqual([e['name'] for e in args_done], ['get_task_details', 'read_user_memory'])

        completed_payload = [e for e in events if e.get('type') == 'response.completed'][-1]['response']
        self.assertEqual(completed_payload['usage']['input_tokens'], 120)
        self.assertEqual(completed_payload['usage']['output_tokens'], 40)

        reasoning_items = [item for item in completed_payload['output'] if item.get('type') == 'reasoning']
        self.assertTrue(reasoning_items)
        enc = reasoning_items[0]['encrypted_content']
        # Должен использоваться Fernet с привязкой к owner_id, а не plain Base64.
        self.assertNotEqual(antigravity_bridge.decode_thought_signature(enc, owner_id=2), 'sig-abc-1')
        self.assertEqual(antigravity_bridge.decode_thought_signature(enc, owner_id=1), 'sig-abc-1')

        next_input = [
            {'type': 'message', 'role': 'user', 'content': 'Составь план'},
            *completed_payload['output'],
            {'type': 'function_call_output', 'call_id': 'fc_srv_1', 'output': json.dumps({'tasks': [{'id': 10}]})},
            {'type': 'function_call_output', 'call_id': 'fc_srv_2', 'output': json.dumps([])},
        ]

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=fake_stream):
            resp2 = client.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer bridge-secret'},
                json={
                    'model': 'gemini-4-neon-high',
                    'instructions': 'Ты Ameba.',
                    'input': next_input,
                    'tools': tools,
                    'stream': False,
                },
            )
        self.assertEqual(resp2.status_code, 200)
        body2 = resp2.json()
        self.assertEqual(body2['output'][0]['content'][0]['text'], 'План готов.')

        env2 = captured_envelopes[1]
        model_parts = [p for c in env2['request']['contents'] if c['role'] == 'model' for p in c['parts']]
        self.assertEqual(model_parts[0]['thoughtSignature'], 'sig-abc-1')
        self.assertEqual(model_parts[0]['functionCall']['id'], 'fc_srv_1')
        user_parts = [p for c in env2['request']['contents'] if c['role'] == 'user' for p in c['parts']]
        fr_parts = [p['functionResponse'] for p in user_parts if 'functionResponse' in p]
        self.assertEqual(len(fr_parts), 2)
        self.assertEqual(fr_parts[0]['id'], 'fc_srv_1')

    def test_terminal_failures_never_emit_completed(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        app = antigravity_bridge.create_bridge_app(session, 'bridge-secret', label='Arrodes')
        client = TestClient(app)

        # MAX_TOKENS завершается как response.incomplete (никогда не response.completed).
        async def _stream_max_tokens(tok, env, **kwargs):
            yield {'candidates': [{'content': {'parts': [{'text': 'обрыв'}]}, 'finishReason': 'MAX_TOKENS'}]}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_stream_max_tokens):
            with client.stream(
                'POST',
                '/v1/responses',
                headers={'Authorization': 'Bearer bridge-secret'},
                json={'model': 'gemini-4-neon-low', 'input': 'test', 'stream': True},
            ) as resp:
                sse_inc = b''.join(resp.iter_bytes()).decode('utf-8')
            self.assertNotIn('response.completed', sse_inc)
            self.assertIn('response.incomplete', sse_inc)

        scenarios = [
            [{'candidates': [{'content': {'parts': []}, 'finishReason': 'SAFETY'}]}],
            [{'candidates': [{'content': {'parts': []}, 'finishReason': 'MALFORMED_FUNCTION_CALL'}]}],
            [{'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT'}, 'candidates': []}],
            [{'candidates': [{'content': {'parts': [{'text': 'часть без конца'}]}}]}],  # преждевременное закрытие без finishReason
            [],  # полностью пустой поток
        ]
        for events_seq in scenarios:
            async def _stream_seq(tok, env, seq=events_seq, **kwargs):
                for item in seq:
                    yield item

            with patch('common.antigravity_bridge.stream_generate_content', side_effect=_stream_seq):
                with client.stream(
                    'POST',
                    '/v1/responses',
                    headers={'Authorization': 'Bearer bridge-secret'},
                    json={'model': 'gemini-4-neon-low', 'input': 'test', 'stream': True},
                ) as resp:
                    sse = b''.join(resp.iter_bytes()).decode('utf-8')
                self.assertNotIn('response.completed', sse, f'Unexpected completed in {sse}')
                self.assertIn('response.failed', sse, f'Missing response.failed in {sse}')

                resp_sync = client.post(
                    '/v1/responses',
                    headers={'Authorization': 'Bearer bridge-secret'},
                    json={'model': 'gemini-4-neon-low', 'input': 'test', 'stream': False},
                )
                self.assertGreaterEqual(resp_sync.status_code, 400)

    def test_persistent_history_recovery_and_unknown_previous_response_id(self):
        s1 = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        s1.access_token = 'tok-1'
        s1.token_expires_at = time.time() + 3600
        s1.project_id = 'projects/demo'
        c1 = TestClient(antigravity_bridge.create_bridge_app(s1, 'sec-1', label='Arrodes'))

        async def _turn1(tok, env, **kwargs):
            yield {'candidates': [{'content': {'parts': [{'text': 'Первый шаг'}]}, 'finishReason': 'STOP'}]}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_turn1):
            r1 = c1.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer sec-1'},
                json={'model': 'gemini-4-neon-low', 'input': 'Начни', 'stream': False},
            )
        self.assertEqual(r1.status_code, 200)
        resp_id = r1.json()['id']

        # Воссоздаём bridge-сессию с нуля (имитация нового запуска/восстановления треда Arrodes).
        s2 = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        s2.access_token = 'tok-1'
        s2.token_expires_at = time.time() + 3600
        s2.project_id = 'projects/demo'
        c2 = TestClient(antigravity_bridge.create_bridge_app(s2, 'sec-2', label='Arrodes'))

        captured_env2 = []

        async def _turn2(tok, env, **kwargs):
            captured_env2.append(env)
            yield {'candidates': [{'content': {'parts': [{'text': 'Продолжение'}]}, 'finishReason': 'STOP'}]}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_turn2):
            r2 = c2.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer sec-2'},
                json={'model': 'gemini-4-neon-low', 'previous_response_id': resp_id, 'input': 'Дальше', 'stream': False},
            )
        self.assertEqual(r2.status_code, 200)
        texts = [p.get('text') for c in captured_env2[0]['request']['contents'] for p in c['parts']]
        self.assertEqual(texts, ['Начни', 'Первый шаг', 'Дальше'])

        # Чужой owner_id=2 не видит историю owner_id=1 и получает 400 previous_response_not_found.
        s_other = antigravity_bridge.AntigravityBridgeSession(owner_id=2)
        s_other.access_token = 'tok-2'
        s_other.token_expires_at = time.time() + 3600
        s_other.project_id = 'projects/demo'
        c_other = TestClient(antigravity_bridge.create_bridge_app(s_other, 'sec-other', label='Arrodes'))
        r_bad = c_other.post(
            '/v1/responses',
            headers={'Authorization': 'Bearer sec-other'},
            json={'model': 'gemini-4-neon-low', 'previous_response_id': resp_id, 'input': 'X', 'stream': False},
        )
        self.assertEqual(r_bad.status_code, 400)
        self.assertEqual(r_bad.json()['error']['code'], 'previous_response_not_found')

    def test_bridge_401_triggers_single_refresh_and_error_status_on_429(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'expired-tok'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        app = antigravity_bridge.create_bridge_app(session, 'bridge-secret', label='Monocle')
        client = TestClient(app)

        calls = []

        async def fake_stream_auth(tok, env, **kwargs):
            calls.append(tok)
            if tok == 'expired-tok':
                raise antigravity.AntigravityAuthError('expired', status_code=401, auth_expired=True)
            yield {'candidates': [{'content': {'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}

        with patch('common.antigravity_bridge.fetch_backend_session', AsyncMock(return_value={
            'access_token': 'fresh-tok',
            'expires_at': time.time() + 3600,
            'project_id': 'projects/demo',
            'cloudcode_url': antigravity.DEFAULT_CLOUDCODE_URL,
        })), patch('common.antigravity_bridge.stream_generate_content', side_effect=fake_stream_auth):
            resp = client.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer bridge-secret'},
                json={'model': 'gemini-4-neon-low', 'input': 'hi', 'stream': False},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, ['expired-tok', 'fresh-tok'])

        async def fake_stream_quota(tok, env, **kwargs):
            raise antigravity.AntigravityError('quota', status_code=429, retryable=False, quota_exceeded=True)
            yield {}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=fake_stream_quota):
            resp_err = client.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer bridge-secret'},
                json={'model': 'gemini-4-neon-low', 'input': 'hi', 'stream': True},
            )
        self.assertEqual(resp_err.status_code, 429)


class AntigravityStrausRegressionTests(unittest.TestCase):
    """Регрессионные тесты A–I для падения Straus с нативным Antigravity и gemini-4-neon-xhigh."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            'os.environ',
            {
                'INTEGRATION_ENCRYPTION_KEY': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
                'UCHEBA_BRIDGE_STATE_DIR': self.tmpdir.name,
            },
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def test_scenario_a_text_generation_gemini_4_neon_xhigh_streaming_context_safe(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=42)
        session.access_token = 'tok-a'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo-a'
        app = antigravity_bridge.create_bridge_app(session, 'bridge-secret', label='Straus')
        client = TestClient(app)

        async def _text_stream(tok, env, **kwargs):
            # Убеждаемся, что SSE-итерация не зависит от незакрытого ContextVar между задачами anyio.
            yield {'candidates': [{'content': {'parts': [{'text': 'Привет, '}]}}]}
            yield {
                'candidates': [{'content': {'parts': [{'text': 'календарь готов.'}]}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 5, 'totalTokenCount': 25},
            }

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_text_stream):
            with client.stream(
                'POST',
                '/v1/responses',
                headers={'Authorization': 'Bearer bridge-secret'},
                json={
                    'model': 'gemini-4-neon-xhigh',
                    'instructions': 'Ты Straus.',
                    'input': [{'type': 'message', 'role': 'user', 'content': 'Скажи привет'}],
                    'stream': True,
                },
            ) as resp:
                self.assertEqual(resp.status_code, 200)
                sse = b''.join(resp.iter_bytes()).decode('utf-8')
        self.assertIn('response.created', sse)
        self.assertIn('response.output_text.done', sse)
        self.assertIn('response.completed', sse)
        self.assertIn('Привет, календарь готов.', sse)

    def test_scenario_b_full_straus_namespace_tools_and_schema_sanitization(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        straus_tools = _make_straus_tools()
        raw_tools = [{'type': 'namespace', 'name': 'mcp__ucheba', 'description': 'Ucheba MCP', 'tools': straus_tools}]
        meta, _ = session.translate_request({
            'model': 'gemini-4-neon-xhigh',
            'input': 'Проверь календарь',
            'tools': raw_tools,
            'tool_choice': {'type': 'allowed_tools', 'mode': 'auto', 'tools': [{'type': 'function', 'namespace': 'mcp__ucheba', 'name': 'calendar_list'}]},
        })
        decls = meta['tools_payload'][0]['functionDeclarations']
        self.assertEqual(len(decls), len(straus_tools))
        self.assertEqual(meta['namespaces'], ['mcp__ucheba'])
        self.assertEqual(
            meta['tool_config'],
            {'functionCallingConfig': {'mode': 'AUTO', 'allowedFunctionNames': ['mcp__ucheba__calendar_list']}},
        )

        # Проверяем отсутствие запрещённых Gemini элементов во всех 49 схемах (вкл. SaveEvents, python_http_request, таблицы).
        def _walk(node, path, *, is_root=False):
            if not isinstance(node, dict):
                return
            forbidden = {'$defs', 'definitions', '$ref', 'additionalProperties', 'anyOf', 'oneOf', 'allOf', 'const'} & set(node)
            self.assertFalse(forbidden, f'Forbidden keys {forbidden} at {path}')
            if not is_root and node.get('type') == 'OBJECT':
                self.assertTrue(node.get('properties'), f'Nested empty OBJECT at {path}')
            if 'enum' in node:
                self.assertEqual(node.get('type'), 'STRING', f'Non-string enum at {path}')
                self.assertTrue(all(isinstance(v, str) and v != '' for v in node['enum']), f'Empty string in enum at {path}')
            for k, v in (node.get('properties') or {}).items():
                _walk(v, f'{path}.{k}', is_root=False)
            if 'items' in node:
                _walk(node['items'], f'{path}[]', is_root=False)

        for d in decls:
            if 'parameters' in d:
                _walk(d['parameters'], d['name'], is_root=True)

        http_decl = next(d for d in decls if d['name'].endswith('python_http_request'))
        ct_schema = http_decl['parameters']['properties']['content_type']
        self.assertNotIn('enum', ct_schema)
        self.assertIn('application/json', ct_schema.get('description', ''))

    def test_scenario_c_cloudcode_http_400_object_and_array_error(self):
        err_obj = antigravity._cloudcode_http_error(
            400,
            json.dumps({'error': {'code': 400, 'status': 'INVALID_ARGUMENT', 'message': 'Invalid function declaration'}}).encode('utf-8'),
            '/v1internal:streamGenerateContent',
        )
        self.assertEqual(err_obj.status_code, 400)
        self.assertEqual(err_obj.error_status, 'INVALID_ARGUMENT')
        self.assertIn('Invalid function declaration', err_obj.user_message)

        err_arr = antigravity._cloudcode_http_error(
            400,
            json.dumps([{'error': {'code': 400, 'status': 'INVALID_ARGUMENT', 'message': 'Bad field in schema Bearer secret-tok'}}]).encode('utf-8'),
            '/v1internal:streamGenerateContent',
        )
        self.assertEqual(err_arr.status_code, 400)
        self.assertEqual(err_arr.error_status, 'INVALID_ARGUMENT')
        self.assertNotIn('secret-tok', err_arr.user_message)

        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))

        async def _fail_400(tok, env, **kwargs):
            raise err_obj
            yield {}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_fail_400):
            resp = client.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer sec'},
                json={'model': 'gemini-4-neon-xhigh', 'input': 'test', 'stream': True},
            )
        self.assertEqual(resp.status_code, 400)
        payload = resp.json()
        self.assertEqual(payload['error']['code'], 'INVALID_ARGUMENT')
        msg = codex_harness.format_turn_error(
            'Straus',
            SimpleNamespace(error={'message': json.dumps(payload), 'codexErrorInfo': 'other'}),
        )
        self.assertIn('HTTP 400', msg)
        self.assertIn('Invalid function declaration', msg)

    def test_scenario_d_bridge_returns_response_failed_on_terminal_error(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))

        async def _safety_stream(tok, env, **kwargs):
            yield {'candidates': [{'content': {'parts': [{'text': 'partial'}]}, 'finishReason': 'SAFETY'}]}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_safety_stream):
            with client.stream(
                'POST',
                '/v1/responses',
                headers={'Authorization': 'Bearer sec'},
                json={'model': 'gemini-4-neon-xhigh', 'input': 'test', 'stream': True},
            ) as resp:
                sse = b''.join(resp.iter_bytes()).decode('utf-8')
        self.assertNotIn('response.completed', sse)
        self.assertIn('response.failed', sse)
        self.assertIn('SAFETY', sse)

    def test_scenario_e_run_codex_turn_preserves_first_error_before_turn_completed(self):
        class _FakeProgress:
            def __init__(self):
                self.record = {'events': []}
                self.events = []

            def begin_response(self): pass
            def update(self, **kw): self.record.update(kw)
            def add(self, msg, **kw): self.events.append(msg)
            def reasoning_delta(self, *a): pass
            def summary(self, *a): return ''

        class _FakeTurn:
            async def stream(self):
                yield SimpleNamespace(
                    method='error',
                    payload=SimpleNamespace(
                        will_retry=False,
                        error={
                            'message': '{"error":{"message":"Cloud Code HTTP 400: unsupported schema","code":"INVALID_ARGUMENT"}}',
                            'codex_error_info': 'other',
                        },
                    ),
                )
                yield SimpleNamespace(
                    method='turn/completed',
                    payload=SimpleNamespace(
                        turn=SimpleNamespace(
                            status='failed',
                            error={'message': '', 'codex_error_info': 'other'},
                        ),
                    ),
                )

        class _FakeThread:
            model = 'gemini-4-neon-xhigh'

            async def turn(self, *args, **kwargs):
                return _FakeTurn()

        outcome = asyncio.run(
            codex_harness.run_codex_turn(
                _FakeThread(),
                'Проверь календарь',
                label='Straus',
                progress=_FakeProgress(),
                effort='medium',
            )
        )
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.status, 'failed')
        self.assertNotEqual(outcome.error_detail, 'Straus: агент не завершил работу')
        self.assertIn('HTTP 400', outcome.error_detail)
        self.assertIn('INVALID_ARGUMENT', outcome.error_detail)
        self.assertIn('unsupported schema', outcome.error_detail)

    def test_scenario_f_calendar_list_then_calendar_finish_namespace_roundtrip(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))

        tools_ns = [{'type': 'namespace', 'name': 'mcp__ucheba', 'tools': _make_straus_tools()}]
        captured_envelopes = []

        async def _calendar_flow(tok, env, **kwargs):
            captured_envelopes.append(env)
            if len(captured_envelopes) == 1:
                yield {
                    'candidates': [{
                        'content': {
                            'parts': [{'functionCall': {'id': 'fc_cal_1', 'name': 'mcp__ucheba__calendar_list', 'args': {}}}]
                        },
                        'finishReason': 'STOP',
                    }]
                }
            else:
                yield {
                    'candidates': [{
                        'content': {
                            'parts': [{
                                'functionCall': {
                                    'id': 'fc_cal_2',
                                    'name': 'mcp__ucheba__calendar_finish',
                                    'args': {'status': 'unchanged', 'reason': 'Календарь уже актуален'},
                                }
                            }]
                        },
                        'finishReason': 'STOP',
                    }]
                }

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_calendar_flow):
            with client.stream(
                'POST',
                '/v1/responses',
                headers={'Authorization': 'Bearer sec'},
                json={'model': 'gemini-4-neon-xhigh', 'input': 'Проверь расписание', 'tools': tools_ns, 'stream': True},
            ) as resp1:
                self.assertEqual(resp1.status_code, 200)
                sse1 = b''.join(resp1.iter_bytes()).decode('utf-8')

        completed1 = None
        for block in sse1.strip().split('\n\n'):
            if block.startswith('event: response.completed'):
                data_ln = [ln[5:].strip() for ln in block.splitlines() if ln.startswith('data:')][0]
                completed1 = json.loads(data_ln)['response']
        self.assertIsNotNone(completed1)
        fc1 = completed1['output'][0]
        self.assertEqual(fc1['namespace'], 'mcp__ucheba')
        self.assertEqual(fc1['name'], 'calendar_list')

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_calendar_flow):
            with client.stream(
                'POST',
                '/v1/responses',
                headers={'Authorization': 'Bearer sec'},
                json={
                    'model': 'gemini-4-neon-xhigh',
                    'tools': tools_ns,
                    'stream': True,
                    'input': [
                        {'type': 'message', 'role': 'user', 'content': 'Проверь расписание'},
                        fc1,
                        {'type': 'function_call_output', 'call_id': fc1['call_id'], 'output': json.dumps({'events': [], 'timezone': 'UTC'})},
                    ],
                },
            ) as resp2:
                self.assertEqual(resp2.status_code, 200)
                sse2 = b''.join(resp2.iter_bytes()).decode('utf-8')

        completed2 = None
        for block in sse2.strip().split('\n\n'):
            if block.startswith('event: response.completed'):
                data_ln = [ln[5:].strip() for ln in block.splitlines() if ln.startswith('data:')][0]
                completed2 = json.loads(data_ln)['response']
        self.assertIsNotNone(completed2)
        fc2 = completed2['output'][0]
        self.assertEqual(fc2['namespace'], 'mcp__ucheba')
        self.assertEqual(fc2['name'], 'calendar_finish')
        self.assertEqual(json.loads(fc2['arguments'])['status'], 'unchanged')
        user_parts2 = [p for c in captured_envelopes[1]['request']['contents'] if c['role'] == 'user' for p in c['parts']]
        fr_parts2 = [p['functionResponse'] for p in user_parts2 if 'functionResponse' in p]
        self.assertEqual(fr_parts2[0]['name'], 'mcp__ucheba__calendar_list')

    def test_scenario_g_malformed_function_call(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))

        bad_sequences = [
            [{'candidates': [{'content': {'parts': [{'functionCall': {'name': '', 'args': {}}}]}, 'finishReason': 'STOP'}]}],
            [{'candidates': [{'content': {'parts': [{'functionCall': {'name': 'calendar_list', 'args': 'not-a-dict'}}]}, 'finishReason': 'STOP'}]}],
            [{'candidates': [{'content': {'parts': []}, 'finishReason': 'MALFORMED_FUNCTION_CALL'}]}],
        ]
        for seq in bad_sequences:
            async def _bad_fc(tok, env, items=seq, **kwargs):
                for item in items:
                    yield item

            with patch('common.antigravity_bridge.stream_generate_content', side_effect=_bad_fc):
                with client.stream(
                    'POST',
                    '/v1/responses',
                    headers={'Authorization': 'Bearer sec'},
                    json={'model': 'gemini-4-neon-xhigh', 'input': 'test', 'stream': True},
                ) as resp:
                    sse = b''.join(resp.iter_bytes()).decode('utf-8')
            self.assertNotIn('response.completed', sse)
            self.assertIn('response.failed', sse)
            self.assertIn('malformed_function_call', sse)

    def test_scenario_h_cloudcode_http_200_invalid_sse(self):
        class _FakeStreamResp:
            def __init__(self, lines):
                self.status_code = 200
                self.is_success = True
                self.headers = {}
                self._lines = lines

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def aiter_lines(self):
                for ln in self._lines:
                    yield ln

        class _FakeClient:
            def __init__(self, lines):
                self._lines = lines

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def stream(self, *args, **kwargs):
                return _FakeStreamResp(self._lines)

        for bad_lines in (['data: {broken json', ''], ['<html>not sse</html>']):
            with patch('common.antigravity.httpx.AsyncClient', return_value=_FakeClient(bad_lines)):
                async def _consume():
                    out = []
                    async for ev in antigravity.stream_generate_content('tok', {'request': {'contents': []}}):
                        out.append(ev)
                    return out

                with self.assertRaises(antigravity.AntigravityError) as ctx:
                    asyncio.run(_consume())
                self.assertEqual(ctx.exception.error_status, 'INVALID_SSE')

    def test_scenario_i_text_vs_tools_failure_localization(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-secret-never-logged'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))

        async def _ok_without_tools_fail_with_tools(tok, env, **kwargs):
            tools = env['request'].get('tools') or []
            if not tools:
                yield {'candidates': [{'content': {'parts': [{'text': 'Текст без инструментов'}]}, 'finishReason': 'STOP'}]}
                return
            raise antigravity.AntigravityError(
                'Cloud Code HTTP 400 (INVALID_ARGUMENT): bad tool schema',
                status_code=400,
                error_status='INVALID_ARGUMENT',
            )

        tools_ns = [{'type': 'namespace', 'name': 'mcp__ucheba', 'tools': _make_straus_tools()}]
        with self.assertLogs('uvicorn.error', level='INFO') as logs:
            with patch('common.antigravity_bridge.stream_generate_content', side_effect=_ok_without_tools_fail_with_tools):
                r_text = client.post(
                    '/v1/responses',
                    headers={'Authorization': 'Bearer sec'},
                    json={'model': 'gemini-4-neon-xhigh', 'input': 'Привет', 'stream': False},
                )
                r_tools = client.post(
                    '/v1/responses',
                    headers={'Authorization': 'Bearer sec'},
                    json={'model': 'gemini-4-neon-xhigh', 'input': 'Календарь', 'tools': tools_ns, 'stream': True},
                )
        self.assertEqual(r_text.status_code, 200)
        self.assertEqual(r_tools.status_code, 400)
        joined = '\n'.join(logs.output)
        self.assertNotIn('tok-secret-never-logged', joined)
        self.assertIn('stage=bridge_request_received', joined)
        self.assertIn('stage=cloudcode_envelope_built', joined)
        self.assertIn('decls=0', joined)
        self.assertIn('decls=49', joined)
        self.assertIn('stage=cloudcode_response model=gemini-4-neon-xhigh status=error http=400 code=INVALID_ARGUMENT', joined)

    def test_cloudcode_429_parses_quota_exhausted_vs_transient_capacity(self):
        quota_payload = {
            'error': {
                'code': 429,
                'status': 'RESOURCE_EXHAUSTED',
                'message': 'You have exhausted your capacity on this model. Your quota will reset after 3h15m.',
                'details': [{
                    '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
                    'reason': 'QUOTA_EXHAUSTED',
                    'metadata': {'quotaResetDelay': '3h15m'},
                }],
            }
        }
        err_q = antigravity._cloudcode_http_error(429, json.dumps(quota_payload).encode('utf-8'), '/v1internal:streamGenerateContent')
        self.assertEqual(err_q.status_code, 429)
        self.assertEqual(err_q.error_status, 'QUOTA_EXHAUSTED')
        self.assertFalse(err_q.retryable)
        self.assertIn('QUOTA_EXHAUSTED', err_q.user_message)
        self.assertIn('3h15m', err_q.user_message)

        cap_payload = {
            'error': {
                'code': 429,
                'status': 'RESOURCE_EXHAUSTED',
                'message': 'No capacity available for model gemini-4-neon-xhigh',
                'details': [
                    {'@type': 'type.googleapis.com/google.rpc.ErrorInfo', 'reason': 'MODEL_CAPACITY_EXHAUSTED'},
                    {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '2s'},
                ],
            }
        }
        err_c = antigravity._cloudcode_http_error(429, json.dumps(cap_payload).encode('utf-8'), '/v1internal:streamGenerateContent')
        self.assertEqual(err_c.status_code, 429)
        self.assertEqual(err_c.error_status, 'MODEL_CAPACITY_EXHAUSTED')
        self.assertTrue(err_c.retryable)
        self.assertEqual(err_c.retry_after, 2.0)

    def test_bridge_does_not_retry_quota_exhausted_and_populates_error_sink(self):
        sink = {}
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1, error_sink=sink)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))

        attempts = {'n': 0}

        async def _quota_fail(tok, env, **kwargs):
            attempts['n'] += 1
            raise antigravity.AntigravityError(
                'Превышен лимит запросов или квота Google Antigravity (HTTP 429, QUOTA_EXHAUSTED): сброс через 2h',
                status_code=429,
                error_status='QUOTA_EXHAUSTED',
                retryable=False,
                quota_exceeded=True,
            )
            yield {}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_quota_fail):
            resp = client.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer sec'},
                json={'model': 'gemini-4-neon-xhigh', 'input': 'test', 'stream': True},
            )
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(attempts['n'], 1)
        self.assertEqual(sink.get('status_code'), 429)
        self.assertEqual(sink.get('code'), 'QUOTA_EXHAUSTED')
        self.assertIn('QUOTA_EXHAUSTED', sink.get('message', ''))

    def test_run_codex_turn_enriches_codex_429_with_bridge_reason_and_zero_sdk_retries(self):
        tok = current_user_id.set(1)
        try:
            cfg, provider_id = codex_harness.build_codex_config('antigravity', base_url='http://127.0.0.1:9999/v1')
            self.assertEqual(provider_id, codex_harness.ANTIGRAVITY_PROVIDER_ID)
            self.assertIn('model_providers.ucheba_antigravity.stream_max_retries=0', cfg.launch_args_override)

            class _FakeProgress:
                def __init__(self):
                    self.record = {'events': []}
                def begin_response(self): pass
                def update(self, **kw): self.record.update(kw)
                def add(self, *a, **kw): pass
                def reasoning_delta(self, *a): pass
                def summary(self, *a): return ''

            class _FakeTurn:
                async def stream(self):
                    yield SimpleNamespace(
                        method='error',
                        payload=SimpleNamespace(
                            will_retry=False,
                            error={
                                'message': 'exceeded retry limit, last status: 429 Too Many Requests',
                                'codex_error_info': {'responseTooManyFailedAttempts': {'httpStatusCode': 429}},
                            },
                        ),
                    )
                    yield SimpleNamespace(
                        method='turn/completed',
                        payload=SimpleNamespace(
                            turn=SimpleNamespace(
                                status='failed',
                                error={
                                    'message': 'exceeded retry limit, last status: 429 Too Many Requests',
                                    'codex_error_info': {'responseTooManyFailedAttempts': {'httpStatusCode': 429}},
                                },
                            ),
                        ),
                    )

            fake_client = SimpleNamespace()
            sink = {'status_code': 429, 'code': 'QUOTA_EXHAUSTED', 'message': 'Превышен лимит запросов или квота Google Antigravity (HTTP 429, QUOTA_EXHAUSTED): quota reset after 2h'}
            codex_harness._attach_bridge_error_sink(fake_client, sink)

            class _FakeThread:
                model = 'gemini-4-neon-xhigh'
                async def turn(self, *args, **kwargs):
                    # Имитируем заполнение sink мостом уже во время выполнения хода (после begin_response).
                    sink.update({'status_code': 429, 'code': 'QUOTA_EXHAUSTED', 'message': 'Превышен лимит запросов или квота Google Antigravity (HTTP 429, QUOTA_EXHAUSTED): quota reset after 2h'})
                    return _FakeTurn()

            outcome = asyncio.run(
                codex_harness.run_codex_turn(
                    _FakeThread(),
                    'Проверь календарь',
                    label='Straus',
                    progress=_FakeProgress(),
                    client=fake_client,
                    effort='medium',
                )
            )
            self.assertFalse(outcome.completed)
            self.assertIn('QUOTA_EXHAUSTED', outcome.error_detail)
            self.assertIn('quota reset after 2h', outcome.error_detail)
            self.assertNotIn('exceeded retry limit', outcome.error_detail)
        finally:
            current_user_id.reset(tok)

    def test_bridge_strips_codex_boilerplate_xml_and_unused_harness_tools(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        straus_tools = _make_straus_tools()
        tools = [
            {'type': 'function', 'name': 'list_mcp_resources', 'description': 'List', 'parameters': {'type': 'object', 'properties': {}}},
            {'type': 'function', 'name': 'create_goal', 'description': 'Goal', 'parameters': {'type': 'object', 'properties': {'objective': {'type': 'string'}}}},
            {'type': 'namespace', 'name': 'multi_agent_v1', 'tools': [{'type': 'function', 'name': 'close_agent', 'parameters': {'type': 'object', 'properties': {}}}]},
            {'type': 'namespace', 'name': 'mcp__ucheba', 'tools': straus_tools},
        ]
        meta, _ = session.translate_request({
            'model': 'gemini-4-neon-xhigh',
            'tools': tools,
            'input': [
                {
                    'type': 'message',
                    'role': 'developer',
                    'content': [{'type': 'input_text', 'text': 'You are a coding agent running in the Codex CLI, led by OpenAI.'}],
                },
                {
                    'type': 'message',
                    'role': 'developer',
                    'content': [
                        {'type': 'input_text', 'text': 'Ты Straus, агент редактирования календаря.'},
                        {'type': 'input_text', 'text': '<skills_instructions>## Skills\nsecret harness</skills_instructions>'},
                        {'type': 'input_text', 'text': '<permissions instructions>sandbox_mode read-only</permissions instructions>'},
                    ],
                },
                {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<environment_context><cwd>/tmp</cwd></environment_context>'}]},
                {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Добавь пару в пятницу'}]},
            ],
        })
        sys_text = meta['system_instruction']['parts'][0]['text']
        self.assertEqual(meta['system_instruction']['role'], 'user')
        self.assertIn('Ты Straus', sys_text)
        self.assertNotIn('Codex CLI', sys_text)
        self.assertNotIn('<skills_instructions>', sys_text)
        self.assertNotIn('<permissions instructions>', sys_text)

        decls = meta['tools_payload'][0]['functionDeclarations']
        self.assertEqual(len(decls), len(straus_tools))
        names = {d['name'] for d in decls}
        self.assertNotIn('create_goal', names)
        self.assertNotIn('list_mcp_resources', names)
        self.assertNotIn('multi_agent_v1__close_agent', names)

        user_texts = [p.get('text', '') for c in meta['contents'] for p in c['parts']]
        self.assertEqual(user_texts, ['Добавь пару в пятницу'])
        env = antigravity.build_cloudcode_envelope(
            project_id='projects/demo',
            model=meta['model'],
            contents=meta['contents'],
            system_instruction=meta['system_instruction'],
            tools=meta['tools_payload'],
            tool_config=meta['tool_config'],
            session_id=session.session_id,
        )
        self.assertEqual(env['request']['sessionId'], session.session_id)

    def test_429_does_not_switch_endpoint_or_fallback_to_generate_content(self):
        # 1. stream_generate_content при 429 не вызывает generateContent и закрывает stream ровно один раз.
        exit_count = {'n': 0}

        class _Stream429:
            def __init__(self):
                self.status_code = 429
                self.is_success = False
                self.headers = {'Retry-After': '12'}
            async def __aenter__(self): return self
            async def __aexit__(self, *args):
                exit_count['n'] += 1
                return False
            async def aread(self):
                return json.dumps({'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED', 'message': 'Rate limit exceeded'}}).encode('utf-8')

        class _Client429:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def stream(self, *args, **kwargs): return _Stream429()

        cloudcode_post_mock = AsyncMock()
        with patch('common.antigravity.httpx.AsyncClient', return_value=_Client429()), \
             patch('common.antigravity.cloudcode_post', cloudcode_post_mock):
            async def _collect():
                return [ev async for ev in antigravity.stream_generate_content('tok', {'request': {'contents': []}})]
            with self.assertRaises(antigravity.AntigravityError) as exc:
                asyncio.run(_collect())
        self.assertEqual(exc.exception.status_code, 429)
        self.assertEqual(exc.exception.retry_after, 12.0)
        self.assertTrue(exc.exception.retryable)
        self.assertEqual(exit_count['n'], 1)
        cloudcode_post_mock.assert_not_called()

        # 2. 404 fallback на generateContent также закрывает stream ровно один раз.
        exit_404 = {'n': 0}

        class _Stream404:
            status_code = 404
            is_success = False
            headers = {}
            async def __aenter__(self): return self
            async def __aexit__(self, *args):
                exit_404['n'] += 1
                return False
            async def aread(self): return b'{}'

        class _Client404:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def stream(self, *args, **kwargs): return _Stream404()

        with patch('common.antigravity.httpx.AsyncClient', return_value=_Client404()), \
             patch('common.antigravity.cloudcode_post', AsyncMock(return_value={'response': {'candidates': [{'content': {'parts': [{'text': '404 fallback ok'}]}, 'finishReason': 'STOP'}]}})):
            async def _collect_404():
                return [ev async for ev in antigravity.stream_generate_content('tok', {'request': {'contents': []}})]
            chunks_404 = asyncio.run(_collect_404())
        self.assertEqual(exit_404['n'], 1)
        self.assertEqual(chunks_404[0]['candidates'][0]['content']['parts'][0]['text'], '404 fallback ok')

        # 3. Мост при transient 429 повторяет запрос на тот же cloudcode_url, не переключая хост.
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        session.access_token = 'tok-1'
        session.token_expires_at = time.time() + 3600
        session.project_id = 'projects/demo'
        client = TestClient(antigravity_bridge.create_bridge_app(session, 'sec', label='Straus'))
        seen_urls = []

        async def _same_host_retry(tok, env, *, cloudcode_url='', **kwargs):
            seen_urls.append(cloudcode_url)
            if len(seen_urls) == 1:
                raise antigravity.AntigravityError(
                    'Превышен лимит запросов (HTTP 429)',
                    status_code=429,
                    error_status='RATE_LIMIT',
                    retryable=True,
                    quota_exceeded=True,
                    retry_after=0.05,
                )
            yield {'candidates': [{'content': {'parts': [{'text': 'Успешно с того же хоста'}]}, 'finishReason': 'STOP'}]}

        with patch('common.antigravity_bridge.stream_generate_content', side_effect=_same_host_retry):
            resp = client.post(
                '/v1/responses',
                headers={'Authorization': 'Bearer sec'},
                json={'model': 'gemini-4-neon-xhigh', 'input': 'Привет', 'stream': False},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen_urls, ['https://daily-cloudcode-pa.googleapis.com', 'https://daily-cloudcode-pa.googleapis.com'])
        self.assertEqual(resp.json()['output'][0]['content'][0]['text'], 'Успешно с того же хоста')

    def test_orphan_external_message_function_call_output_becomes_user_text(self):
        session = antigravity_bridge.AntigravityBridgeSession(owner_id=1)
        payload = json.dumps({'instruction': 'Добавь пару', 'calendar': {'events': []}}, ensure_ascii=False)
        tools_ns = [{'type': 'namespace', 'name': 'mcp__ucheba', 'tools': _make_straus_tools()}]

        # 1. Ход с ExternalMessage(tool_name='task_manager') не должен порождать functionResponse без functionCall.
        meta1, _ = session.translate_request({
            'model': 'gemini-4-neon-xhigh',
            'tools': tools_ns,
            'input': [
                {
                    'type': 'function_call_output',
                    'call_id': 'ext_task_mgr_1',
                    'name': 'task_manager',
                    'output': payload,
                }
            ],
        })
        all_parts1 = [p for c in meta1['contents'] for p in c['parts']]
        self.assertFalse(any('functionResponse' in p for p in all_parts1))
        self.assertEqual(meta1['contents'][0]['role'], 'user')
        self.assertIn('Добавь пару', meta1['contents'][0]['parts'][0]['text'])

        # 2. На следующем ходу реальный function_call + function_call_output с тем же call_id даёт functionResponse.
        meta2, _ = session.translate_request({
            'model': 'gemini-4-neon-xhigh',
            'tools': tools_ns,
            'input': [
                {'type': 'function_call_output', 'call_id': 'ext_task_mgr_1', 'name': 'task_manager', 'output': payload},
                {'type': 'function_call', 'call_id': 'call_cal_1', 'namespace': 'mcp__ucheba', 'name': 'calendar_list', 'arguments': '{}'},
                {'type': 'function_call_output', 'call_id': 'call_cal_1', 'output': json.dumps({'events': []})},
            ],
        })
        self.assertEqual([c['role'] for c in meta2['contents']], ['user', 'model', 'user'])
        self.assertIn('functionCall', meta2['contents'][1]['parts'][0])
        self.assertIn('functionResponse', meta2['contents'][2]['parts'][0])
        self.assertEqual(meta2['contents'][2]['parts'][0]['functionResponse']['name'], 'mcp__ucheba__calendar_list')

        # 3. Неизвестный call_id обычного инструмента не превращается молча в текст, а даёт HTTP 400 invalid_tool_sequence.
        with self.assertRaises(antigravity.AntigravityError) as unknown_exc:
            session.translate_request({
                'model': 'gemini-4-neon-xhigh',
                'tools': tools_ns,
                'input': [
                    {'type': 'function_call_output', 'call_id': 'unknown_call_99', 'name': 'calendar_list', 'output': '{}'},
                ],
            })
        self.assertEqual(unknown_exc.exception.status_code, 400)
        self.assertEqual(unknown_exc.exception.error_status, 'invalid_tool_sequence')

    def test_audit_regressions_isolation_size_limit_sse_and_sanitizer(self):
        # 1. Изоляция истории 4 агентов и соблюдение UCHEBA_BRIDGE_STATE_DIR.
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict('os.environ', {'UCHEBA_BRIDGE_STATE_DIR': tmpdir}):
                paths = {}
                for agent in ('Monocle', 'Ameba', 'Straus', 'Arrodes'):
                    s = antigravity_bridge.AntigravityBridgeSession(
                        owner_id=1, agent_scope=agent, connection_id='conn-a',
                    )
                    s.responses_history[f'resp_{agent}'] = [{'type': 'message', 'role': 'user', 'content': f'msg {agent}'}]
                    self.assertTrue(s._save_persisted_state())
                    paths[agent] = s.state_path
                self.assertEqual(len(set(paths.values())), 4)
                for agent, p in paths.items():
                    self.assertTrue(str(p).startswith(tmpdir))
                    reloaded = antigravity_bridge.AntigravityBridgeSession(
                        owner_id=1, agent_scope=agent, connection_id='conn-a',
                    )
                    self.assertIn(f'resp_{agent}', reloaded.responses_history)
                    for other in ('Monocle', 'Ameba', 'Straus', 'Arrodes'):
                        if other != agent:
                            self.assertNotIn(f'resp_{other}', reloaded.responses_history)

                # 2. Смена Google-аккаунта (connection_id) полностью изолирует previous_response_id и encrypted_content.
                reloaded_b = antigravity_bridge.AntigravityBridgeSession(
                    owner_id=1, agent_scope='Arrodes', connection_id='conn-b',
                )
                self.assertNotIn('resp_Arrodes', reloaded_b.responses_history)
                enc_a = antigravity_bridge._encode_encrypted_meta({'call_sigs': {'c1': 'sig-a'}}, owner_id=1, connection_id='conn-a')
                self.assertEqual(antigravity_bridge.decode_thought_signature(enc_a, owner_id=1, connection_id='conn-a'), 'sig-a')
                self.assertEqual(antigravity_bridge.decode_thought_signature(enc_a, owner_id=1, connection_id='conn-b'), '')

                # 3. Конкурентное сохранение Responses history не теряет call_meta и записи.
                s1 = antigravity_bridge.AntigravityBridgeSession(owner_id=1, agent_scope='Straus', connection_id='conn-c')
                s2 = antigravity_bridge.AntigravityBridgeSession(owner_id=1, agent_scope='Straus', connection_id='conn-c')
                s1.responses_history['r1'] = [{'type': 'function_call', 'call_id': 'c1', 'name': 'calendar_list', 'arguments': '{}'}]
                s1.call_meta['c1'] = {'safe_name': 'mcp__ucheba__calendar_list', 'provider_id': 'p1'}
                s2.responses_history['r2'] = [{'type': 'function_call', 'call_id': 'c2', 'name': 'calendar_finish', 'arguments': '{}'}]
                s2.call_meta['c2'] = {'safe_name': 'mcp__ucheba__calendar_finish', 'provider_id': 'p2'}
                self.assertTrue(s1._save_persisted_state())
                self.assertTrue(s2._save_persisted_state())
                s3 = antigravity_bridge.AntigravityBridgeSession(owner_id=1, agent_scope='Straus', connection_id='conn-c')
                self.assertIn('r1', s3.responses_history)
                self.assertIn('r2', s3.responses_history)
                self.assertEqual(s3.call_meta.get('c1', {}).get('provider_id'), 'p1')
                self.assertEqual(s3.call_meta.get('c2', {}).get('provider_id'), 'p2')

                # 4. Строгий лимит _MAX_PERSISTED_BYTES соблюдается даже для 1 огромной записи.
                huge_session = antigravity_bridge.AntigravityBridgeSession(owner_id=1, agent_scope='Monocle', connection_id='conn-huge')
                huge_session.responses_history['resp_huge'] = [
                    {'type': 'function_call', 'call_id': 'c_huge', 'name': 'search', 'arguments': '{}'},
                    {'type': 'function_call_output', 'call_id': 'c_huge', 'output': 'X' * (800 * 1024)},
                ]
                self.assertTrue(huge_session._save_persisted_state())
                self.assertLessEqual(huge_session.state_path.stat().st_size, antigravity_bridge._MAX_PERSISTED_BYTES * 2)

                # 5. Диагностика ошибки записи истории вместо молчаливого pass.
                err_session = antigravity_bridge.AntigravityBridgeSession(owner_id=1, agent_scope='Ameba', connection_id='conn-err')
                err_session.responses_history['r_err'] = [{'type': 'message', 'role': 'user', 'content': 'hi'}]
                with patch('common.antigravity_bridge.os.replace', side_effect=PermissionError('read-only fs')):
                    ok = err_session._save_persisted_state()
                self.assertFalse(ok)
                self.assertIn('history_write_failed', err_session.last_persistence_error or '')

        # 6. Отсутствие finishReason при has_tool_calls=True MUST быть ошибкой (не ложный completed).
        st, _, err = antigravity_bridge._evaluate_terminal_outcome(
            finish_reason='',
            block_reason='',
            has_text=False,
            has_tool_calls=True,
        )
        self.assertEqual(st, 'failed')
        self.assertEqual(err['code'], 'premature_stream_close')

        # 7. _sanitize_system_text сохраняет permissions instructions при preserve_permissions=True (Arrodes).
        raw_prompt = (
            'Ты Arrodes, исполнитель Ucheba.\n'
            '<skills_instructions>\ncodex skills hidden\n</skills_instructions>\n'
            '<permissions instructions>\nsandbox workspace-write & shell policy\n</permissions instructions>'
        )
        san_arrodes = antigravity_bridge._sanitize_system_text(raw_prompt, has_custom_agent_prompt=True, preserve_permissions=True)
        self.assertIn('Ты Arrodes', san_arrodes)
        self.assertIn('sandbox workspace-write', san_arrodes)
        self.assertNotIn('codex skills hidden', san_arrodes)

        # 8. Проверка ID token claims: отклонён alg=none, expired, wrong aud; валиден корректный.
        import base64
        def _jwt(hdr, pay):
            h = base64.urlsafe_b64encode(json.dumps(hdr).encode()).decode().rstrip('=')
            p = base64.urlsafe_b64encode(json.dumps(pay).encode()).decode().rstrip('=')
            return f'{h}.{p}.sig'
        valid_pay = {'iss': 'https://accounts.google.com', 'sub': 'sub-1', 'aud': 'cid-1', 'exp': time.time() + 600, 'email': 'a@b.com'}
        self.assertEqual(antigravity.extract_id_token_claims(_jwt({'alg': 'none'}, valid_pay), expected_aud='cid-1'), {})
        self.assertEqual(antigravity.extract_id_token_claims(_jwt({'alg': 'RS256'}, {**valid_pay, 'exp': time.time() - 300}), expected_aud='cid-1'), {})
        self.assertEqual(antigravity.extract_id_token_claims(_jwt({'alg': 'RS256'}, {**valid_pay, 'aud': 'other'}), expected_aud='cid-1'), {})
        self.assertEqual(antigravity.extract_id_token_claims(_jwt({'alg': 'RS256'}, valid_pay), expected_aud='cid-1')['sub'], 'sub-1')

        # 9. Неожиданный Content-Type в HTTP 200 SSE отклоняется как INVALID_SSE.
        class _StreamHtml:
            status_code = 200
            is_success = True
            headers = {'content-type': 'text/html; charset=utf-8'}
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def aread(self): return b'<html>oops</html>'

        class _ClientHtml:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def stream(self, *args, **kwargs): return _StreamHtml()

        with patch('common.antigravity.httpx.AsyncClient', return_value=_ClientHtml()):
            async def _collect_html():
                return [ev async for ev in antigravity.stream_generate_content('tok', {'request': {'contents': []}})]
            with self.assertRaises(antigravity.AntigravityError) as html_exc:
                asyncio.run(_collect_html())
        self.assertEqual(html_exc.exception.error_status, 'INVALID_SSE')


class AntigravityAdminUserSeparationAndAgentsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db1 = create_engine(f'sqlite:///{self.tmpdir.name}/u1.db')
        self.db2 = create_engine(f'sqlite:///{self.tmpdir.name}/u2.db')
        for eng in (self.db1, self.db2):
            SQLModel.metadata.create_all(eng)
        with Session(self.db1) as s:
            s.add(User(id=1, username='admin', password_hash='h1', is_admin=True))
            s.add(User(id=2, username='user2', password_hash='h2', is_admin=False))
            s.commit()
        self.engines = {1: self.db1, 2: self.db2}
        feat_map = {
            'monocle': True, 'ameba': True, 'straus': True, 'arrodes': True,
            'context': True, 'codex': True, 'telegram': True, 'collector': True, 'notifications': True,
        }
        self.patches = [
            patch.dict('os.environ', {'INTEGRATION_ENCRYPTION_KEY': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}),
            patch.object(database, 'engine', self.db1),
            patch.object(settings_mod, 'engine', self.db1),
            patch.object(backend_ag, 'engine', self.db1),
            patch.object(users_router, 'engine', self.db1),
            patch.object(database, 'get_engine', side_effect=lambda: self.engines[current_user_id.get()]),
            patch.object(settings_mod, 'get_engine', side_effect=lambda: self.engines[current_user_id.get()]),
            patch('common.accounts.features', return_value=feat_map),
            patch.object(backend_ag, 'features', return_value=feat_map),
            patch.object(integrations_router, 'features', return_value=feat_map),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmpdir.cleanup()

    def test_admin_global_config_secret_redaction_and_non_admin_forbidden(self):
        admin_user = User(id=1, username='admin', password_hash='h1', is_admin=True)
        regular_user = User(id=2, username='user2', password_hash='h2', is_admin=False)

        app = FastAPI()
        app.include_router(users_router.admin)
        app.include_router(integrations_router.router)
        active = {'user': admin_user}

        @app.middleware('http')
        async def _auth_mw(request, call_next):
            u = active['user']
            tok = current_user_id.set(u.id)
            request.state.user = u
            try:
                return await call_next(request)
            finally:
                current_user_id.reset(tok)

        def _fake_owner(request: Request):
            if not request.state.user.is_admin:
                raise HTTPException(403, 'Доступ только для администратора')
            return request.state.user

        app.dependency_overrides[users_router.require_owner] = _fake_owner
        app.dependency_overrides[integrations_router.require_user] = lambda: active['user']
        client = TestClient(app)

        # 1. Админ сохраняет глобальную OAuth-конфигурацию.
        resp = client.patch('/api/admin/antigravity', json={
            'enabled': True,
            'client_id': 'admin-client-id.apps.googleusercontent.com',
            'client_secret': 'super-top-secret-value',
            'redirect_uri': 'https://antigravity.google/oauth-callback',
            'cloudcode_url': 'https://cloudcode-pa.googleapis.com',
            'user_agent': 'antigravity/test/1.0',
            'allowed_scopes': ['openid', 'https://www.googleapis.com/auth/cloud-platform'],
            'diagnostic_mode': True,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['client_secret_configured'])
        self.assertNotIn('client_secret', body)
        self.assertNotIn('super-top-secret-value', json.dumps(body))

        got_admin = client.get('/api/admin/antigravity').json()
        self.assertEqual(got_admin['client_id'], 'admin-client-id.apps.googleusercontent.com')
        self.assertNotIn('super-top-secret-value', json.dumps(got_admin))

        # 2. Обычный пользователь не может читать или менять админскую конфигурацию.
        active['user'] = regular_user
        self.assertEqual(client.get('/api/admin/antigravity').status_code, 403)
        self.assertEqual(client.patch('/api/admin/antigravity', json={'client_id': 'hacked'}).status_code, 403)
        self.assertEqual(client.patch('/api/integrations/antigravity', json={'client_id': 'hacked', 'client_secret': 'x'}).status_code, 403)

        user_status = client.get('/api/integrations/antigravity/status').json()
        self.assertTrue(user_status['system_enabled'])
        self.assertTrue(user_status['system_configured'])
        for secret_or_admin_key in ('client_id', 'client_secret', 'redirect_uri', 'cloudcode_url', 'user_agent'):
            self.assertNotIn(secret_or_admin_key, user_status)

    def test_legacy_migration_moves_global_fields_and_preserves_user_tokens(self):
        with Session(self.db1) as s:
            s.add(IntegrationSetting(
                name='antigravity',
                config=json.dumps({
                    'client_id': 'legacy-cid.apps.googleusercontent.com',
                    'redirect_uri': 'https://antigravity.google/oauth-callback',
                    'cloudcode_url': 'https://cloudcode-pa.googleapis.com',
                    'user_agent': 'legacy-ua/1.0',
                    'project_id': 'projects/legacy-proj',
                    'account_email': 'owner@example.com',
                    'subscription_tier': 'ULTRA',
                    'token_expires_at': time.time() + 3600,
                }),
                secrets=encrypt(json.dumps({
                    'client_secret': 'legacy-secret-123',
                    'access_token': 'legacy-access-tok',
                    'refresh_token': 'legacy-refresh-tok',
                })),
            ))
            s.commit()

        res = backend_ag.migrate_legacy_antigravity_config()
        self.assertTrue(res['migrated'])

        with Session(self.db1) as s:
            sys_row = s.get(SystemSetting, 'antigravity')
            user_row = s.get(IntegrationSetting, 'antigravity')
            self.assertIsNotNone(sys_row)
            sys_cfg = json.loads(sys_row.config)
            sys_sec = json.loads(decrypt(sys_row.secrets))
            self.assertEqual(sys_cfg['client_id'], 'legacy-cid.apps.googleusercontent.com')
            self.assertEqual(sys_cfg['cloudcode_url'], 'https://cloudcode-pa.googleapis.com')
            self.assertEqual(sys_sec['client_secret'], 'legacy-secret-123')

            u_cfg = json.loads(user_row.config)
            u_sec = json.loads(decrypt(user_row.secrets))
            self.assertEqual(u_cfg['discovered_project_id'], 'projects/legacy-proj')
            self.assertEqual(u_cfg['account_email'], 'owner@example.com')
            self.assertNotIn('client_id', u_cfg)
            self.assertNotIn('client_secret', u_sec)
            self.assertEqual(u_sec['access_token'], 'legacy-access-tok')
            self.assertEqual(u_sec['refresh_token'], 'legacy-refresh-tok')

    def test_per_user_isolation_refresh_revocation_disabled_and_all_agents_plus_compat(self):
        # Настраиваем глобальный Antigravity в админке.
        backend_ag.save_global_config({
            'enabled': True,
            'client_id': 'global-cid.apps.googleusercontent.com',
            'client_secret': 'global-secret-xyz',
            'cloudcode_url': 'https://daily-cloudcode-pa.googleapis.com',
        })

        tok1 = current_user_id.set(1)
        try:
            save_settings('antigravity', config={'account_email': 'u1@example.com'}, secrets={'access_token': 'a1', 'refresh_token': 'r1'})
            for agent_name in ('monocle', 'ameba', 'straus', 'arrodes'):
                save_settings(agent_name, config={'enabled': True, 'auth_mode': 'antigravity_gateway', 'model': 'gemini-4-neon-high'})
                eff, key = resolve_agent_auth(agent_name, require_enabled=True)
                self.assertEqual(eff['auth_mode'], 'antigravity')
                self.assertEqual(key, '')
                auth = codex_harness.normalize_auth(eff, key, label=agent_name)
                self.assertEqual(auth[0], 'antigravity')
                self.assertEqual(auth[3], 'gemini-4-neon-high')
            pub1 = public_settings()['antigravity']
            self.assertNotIn('access_token', pub1)
            self.assertNotIn('refresh_token', pub1)
            self.assertNotIn('client_secret', pub1['configured'])
            self.assertTrue(pub1['configured']['access_token'])

            # Проверка обратной совместимости API Key и OpenAI OAuth.
            save_settings('ai_provider', config={'base_url': 'https://lenec.tech/v1'}, secrets={'api_key': 'sk-test-123'})
            save_settings('monocle', config={'enabled': True, 'auth_mode': 'api_key', 'model': 'gpt-6-luna'})
            eff_api, key_api = resolve_agent_auth('monocle', require_enabled=True)
            self.assertEqual(eff_api['auth_mode'], 'api_key')
            self.assertEqual(key_api, 'sk-test-123')
            save_settings('arrodes', config={'enabled': True, 'auth_mode': 'oauth', 'model': ''})
            eff_oauth, key_oauth = resolve_agent_auth('arrodes', require_enabled=True)
            self.assertEqual(eff_oauth['auth_mode'], 'oauth')
            self.assertEqual(key_oauth, '')
        finally:
            current_user_id.reset(tok1)

        tok2 = current_user_id.set(2)
        try:
            pub2 = public_settings()['antigravity']
            self.assertFalse(pub2['configured']['access_token'])
            with self.assertRaises(HTTPException) as raised:
                resolve_agent_auth('monocle', require_enabled=True)
            self.assertEqual(raised.exception.status_code, 400)
        finally:
            current_user_id.reset(tok2)

        # Отключение Antigravity админом блокирует старт логина и агентов в режиме antigravity.
        backend_ag.save_global_config({'enabled': False})
        tok1 = current_user_id.set(1)
        try:
            with self.assertRaises(HTTPException) as disabled_exc:
                backend_ag.start_login()
            self.assertEqual(disabled_exc.exception.status_code, 403)
            with self.assertRaises(HTTPException) as disabled_agent:
                resolve_agent_auth('ameba', require_enabled=True)
            self.assertEqual(disabled_agent.exception.status_code, 403)
        finally:
            current_user_id.reset(tok1)

        # Включаем обратно и проверяем очистку сессии при invalid_grant.
        backend_ag.save_global_config({'enabled': True})
        tok1 = current_user_id.set(1)
        try:
            async def run_revoked():
                with patch.object(
                    backend_ag,
                    'refresh_oauth_token',
                    AsyncMock(side_effect=antigravity.AntigravityAuthError('revoked', status_code=400, revoked=True)),
                ):
                    with self.assertRaises(HTTPException) as exc:
                        await backend_ag.ensure_valid_session(force_refresh=True)
                    self.assertEqual(exc.exception.status_code, 401)
            asyncio.run(run_revoked())
            _, sec1 = read_settings('antigravity', apply_feature=False)
            self.assertEqual(sec1.get('access_token', ''), '')
            self.assertEqual(sec1.get('refresh_token', ''), '')
        finally:
            current_user_id.reset(tok1)

    def test_audit_oauth_lifecycle_refresh_preservation_and_config_versioning(self):
        backend_ag.save_global_config({
            'enabled': True,
            'client_id': 'cid-v1.apps.googleusercontent.com',
            'client_secret': 'secret-v1',
            'redirect_uri': 'https://antigravity.google/oauth-callback',
        })
        v1 = backend_ag.read_global_config()['config_version']
        tok1 = current_user_id.set(1)
        try:
            # 1. Параллельные start_login -> complete_login первого входа с userinfo и refresh_token.
            s1 = backend_ag.start_login()
            s2 = backend_ag.start_login()
            self.assertNotEqual(s1['state'], s2['state'])

            with patch.object(backend_ag, 'exchange_oauth_code', AsyncMock(return_value={
                'access_token': 'acc-a1', 'refresh_token': 'ref-a', 'expires_in': 3600,
            })), patch.object(backend_ag, 'fetch_userinfo', AsyncMock(return_value={
                'sub': 'google-sub-a', 'email': 'alice@example.com', 'name': 'Alice', 'email_verified': True,
            })), patch.object(backend_ag, 'call', AsyncMock(return_value={
                'project_id': 'proj-a', 'subscription_tier': 'PRO', 'models': [{'id': 'gemini-4-neon-xhigh', 'name': 'Neon'}],
            })):
                res1 = asyncio.run(backend_ag.complete_login(
                    f"https://antigravity.google/oauth-callback?state={s1['state']}&code=code-1",
                ))
            self.assertTrue(res1['oauth_authorized'])
            self.assertTrue(res1['has_refresh_token'])
            conn_a1 = res1['connection_id']
            self.assertTrue(bool(conn_a1))

            # State одноразовый: повторный вызов отклоняется.
            with self.assertRaises(HTTPException):
                asyncio.run(backend_ag.complete_login(
                    f"https://antigravity.google/oauth-callback?state={s1['state']}&code=code-1",
                ))

            # 2. Повторный вход того же Google-аккаунта и того же client_id БЕЗ refresh_token сохраняет ref-a, но меняет connection_id.
            s3 = backend_ag.start_login()
            with patch.object(backend_ag, 'exchange_oauth_code', AsyncMock(return_value={
                'access_token': 'acc-a2', 'expires_in': 3600,
            })), patch.object(backend_ag, 'fetch_userinfo', AsyncMock(return_value={
                'sub': 'google-sub-a', 'email': 'alice@example.com', 'name': 'Alice', 'email_verified': True,
            })), patch.object(backend_ag, 'call', AsyncMock(return_value={
                'project_id': 'proj-a', 'subscription_tier': 'PRO', 'models': [],
            })):
                res2 = asyncio.run(backend_ag.complete_login(
                    f"https://antigravity.google/oauth-callback?state={s3['state']}&code=code-2",
                ))
            self.assertTrue(res2['has_refresh_token'])
            self.assertNotEqual(res2['connection_id'], conn_a1)
            _, sec_a2 = read_settings('antigravity', apply_feature=False)
            self.assertEqual(sec_a2.get('refresh_token'), 'ref-a')

            # 3. Тест генерации привязан к config_version; смена cloudcode_url инвалидирует verified/catalog.
            with patch.object(backend_ag, 'call', AsyncMock(return_value={
                'ok': True, 'model': 'gemini-4-neon-xhigh', 'project_id': 'proj-a', 'subscription_tier': 'PRO',
            })):
                ver = asyncio.run(backend_ag.test_generation('gemini-4-neon-xhigh'))
            self.assertTrue(ver['status']['generation_verified'])

            backend_ag.save_global_config({'cloudcode_url': 'https://cloudcode-pa.googleapis.com'})
            v2 = backend_ag.read_global_config()['config_version']
            self.assertGreater(v2, v1)
            stale_st = asyncio.run(backend_ag.status(live=False))
            self.assertFalse(stale_st['generation_verified'])
            self.assertFalse(stale_st['catalog_accessible'])
            self.assertTrue(stale_st['config_stale'])

            # 4. Вход ДРУГОГО Google-аккаунта без refresh_token НЕ переносит ref-a и выставляет refresh_warning.
            s4 = backend_ag.start_login()
            with patch.object(backend_ag, 'exchange_oauth_code', AsyncMock(return_value={
                'access_token': 'acc-b1', 'expires_in': 3600,
            })), patch.object(backend_ag, 'fetch_userinfo', AsyncMock(return_value={
                'sub': 'google-sub-b', 'email': 'bob@example.com', 'name': 'Bob', 'email_verified': True,
            })), patch.object(backend_ag, 'call', AsyncMock(return_value={
                'project_id': 'proj-b', 'subscription_tier': 'FREE', 'models': [],
            })):
                res3 = asyncio.run(backend_ag.complete_login(
                    f"https://antigravity.google/oauth-callback?state={s4['state']}&code=code-3",
                ))
            self.assertFalse(res3['has_refresh_token'])
            self.assertTrue(bool(res3.get('refresh_warning')))
            _, sec_b1 = read_settings('antigravity', apply_feature=False)
            self.assertEqual(sec_b1.get('refresh_token', ''), '')

            # 5. Смена client_id админом помечает сессию oauth_client_mismatch без удаления токенов.
            backend_ag.save_global_config({'client_id': 'cid-v2.apps.googleusercontent.com'})
            mismatch_st = asyncio.run(backend_ag.status(live=False))
            self.assertTrue(mismatch_st['oauth_client_mismatch'])
            self.assertFalse(mismatch_st['oauth_authorized'])

            # 6. Logout сбрасывает connection_id и очищает токены.
            with patch.object(backend_ag, 'revoke_oauth_token', AsyncMock()):
                asyncio.run(backend_ag.logout())
            after_out = asyncio.run(backend_ag.status(live=False))
            self.assertEqual(after_out['connection_id'], '')
            self.assertFalse(after_out['oauth_authorized'])
        finally:
            current_user_id.reset(tok1)

        # 7. Идемпотентная доочистка частичной миграции, когда SystemSetting уже есть.
        with Session(self.db1) as s:
            row = s.get(IntegrationSetting, 'antigravity')
            cfg = json.loads(row.config)
            cfg['client_id'] = 'lingering-cid'
            row.config = json.dumps(cfg)
            s.add(row)
            s.commit()
        mig2 = backend_ag.migrate_legacy_antigravity_config()
        self.assertTrue(mig2['migrated'])
        self.assertTrue(mig2['cleaned_partial'])
        mig3 = backend_ag.migrate_legacy_antigravity_config()
        self.assertFalse(mig3['migrated'])


if __name__ == '__main__':
    unittest.main()
