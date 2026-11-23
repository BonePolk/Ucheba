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

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from common import antigravity, antigravity_bridge, codex_harness, database, settings as settings_mod
from common.identity import current_user_id
from common.models.integration import IntegrationSetting
from common.settings import public_settings, read_settings, resolve_agent_auth, save_settings
from integrations import antigravity as backend_ag


class AntigravityOAuthTests(unittest.TestCase):
    def test_pkce_state_and_callback_validation(self):
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

        with self.assertRaises(ValueError):
            antigravity.parse_oauth_callback(
                'https://antigravity.google/oauth-callback?state=wrong&code=auth-code-1',
                state,
            )
        with self.assertRaises(ValueError):
            antigravity.parse_oauth_callback(
                f'https://evil.example/oauth-callback?state={state}&code=auth-code-1',
                state,
            )

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


class AntigravityBridgeTests(unittest.TestCase):
    def test_responses_bridge_streaming_multi_tool_and_thought_signature(self):
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
                        }
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
        self.assertIn('get_task_details', raw_sse)
        self.assertIn('read_user_memory', raw_sse)
        self.assertIn('response.completed', raw_sse)

        # Verify second turn carries tool responses and preserved thoughtSignature.
        completed_payload = None
        for block in raw_sse.strip().split('\n\n'):
            if block.startswith('event: response.completed'):
                data_line = [ln for ln in block.splitlines() if ln.startswith('data:')][0]
                completed_payload = json.loads(data_line[5:].strip())['response']
        self.assertIsNotNone(completed_payload)
        self.assertEqual(completed_payload['usage']['input_tokens'], 120)
        self.assertEqual(completed_payload['usage']['output_tokens'], 40)

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


class AntigravityUserIsolationAndAgentsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db1 = create_engine(f'sqlite:///{self.tmpdir.name}/u1.db')
        db2 = create_engine(f'sqlite:///{self.tmpdir.name}/u2.db')
        for eng in (db1, db2):
            database.SQLModel.metadata.create_all(eng)
        self.engines = {1: db1, 2: db2}
        self.patches = [
            patch.dict('os.environ', {'INTEGRATION_ENCRYPTION_KEY': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}),
            patch.object(database, 'get_engine', side_effect=lambda: self.engines[current_user_id.get()]),
            patch.object(settings_mod, 'get_engine', side_effect=lambda: self.engines[current_user_id.get()]),
            patch('common.accounts.features', return_value={
                'monocle': True, 'ameba': True, 'straus': True, 'arrodes': True,
                'context': True, 'codex': True, 'telegram': True, 'collector': True, 'notifications': True,
            }),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmpdir.cleanup()

    def test_per_user_isolation_refresh_revocation_and_all_agents(self):
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
            self.assertTrue(pub1['configured']['access_token'])
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

        # Revoked refresh_token clears user 1 session on ensure_valid_session.
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
