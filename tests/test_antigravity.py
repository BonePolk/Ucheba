"""Локальные тесты нативной интеграции Google Antigravity и встроенного Responses-моста."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import create_engine
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from common import antigravity, antigravity_bridge, codex_harness, database, settings as settings_mod
from common.identity import current_user_id
from common.models.integration import IntegrationSetting
from common.models.user import SystemSetting, User
from common.security import decrypt, encrypt
from common.settings import public_settings, read_settings, resolve_agent_auth, save_settings
from integrations import antigravity as backend_ag
from routers import integrations as integrations_router, users as users_router


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

        scenarios = [
            [{'candidates': [{'content': {'parts': [{'text': 'обрыв'}]}, 'finishReason': 'MAX_TOKENS'}]}],
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


class AntigravityAdminUserSeparationAndAgentsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db1 = create_engine(f'sqlite:///{self.tmpdir.name}/u1.db')
        self.db2 = create_engine(f'sqlite:///{self.tmpdir.name}/u2.db')
        for eng in (self.db1, self.db2):
            database.SQLModel.metadata.create_all(eng)
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

        def _fake_owner(request):
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
                with patch(
                    'backend.integrations.antigravity.refresh_oauth_token',
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


if __name__ == '__main__':
    unittest.main()
