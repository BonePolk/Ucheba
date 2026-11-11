"""Каждый инструмент Straus: схема, диспетчеризация и результат в истории модели."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import agent
from common.identity import current_user_id
from common.file_tools import FILE_NAMES, LIBRARY_NAMES


EVENT = {'id': '', 'title': 'Fixture event', 'summary': 'Fixture', 'task_id': None,
         'date': '2026-11-11', 'start': '10:00', 'end': '11:00', 'kind': 'busy',
         'repeat_unit': 'none', 'repeat_interval': 1, 'repeat_count': None,
         'week_pattern': 'all', 'parity_anchor': None}


class Stream:
    def __init__(self, name, arguments):
        self.response = SimpleNamespace(status='completed', usage=None, output=[SimpleNamespace(
            type='function_call', name=name, arguments=json.dumps(arguments), call_id=name)])
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def __aiter__(self): return self.events()
    async def events(self):
        yield SimpleNamespace(type='response.completed', response=self.response)


class Client:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.inputs = []
        self.responses = self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def stream(self, **kwargs):
        self.inputs.append(list(kwargs['input']))
        return Stream(*next(self.calls))


class StrausTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = current_user_id.set(900002)
    async def asyncTearDown(self):
        current_user_id.reset(self.token)

    async def execute(self, calls, calendar=None, remote=None):
        client = Client(calls)
        browser = AsyncMock(return_value=remote or {'status': 'page', 'text': 'Fixture text',
                            'image': 'aW1hZ2U=', 'mime': 'image/png', 'elements': [{'id': '1', 'name': 'Button'}]})
        library = AsyncMock(return_value={'output': {'status': 'read_file'}, 'content': [{'type': 'input_image', 'image_url': 'data:image/png;base64,aW1hZ2U='}]})
        browser.library_client = library
        body = {'api_key': 'fixture-key', 'config': {'model': 'fixture'}, 'instruction': 'Fixture instruction',
                'calendar': calendar or {'events': [], 'timezone': 'UTC'}, 'tasks': []}
        with patch.object(agent, 'AsyncOpenAI', return_value=client), patch.object(agent, 'browser_request', browser), patch.object(agent, 'memory_tool', new=AsyncMock(return_value={'ok': True})), patch.object(agent, 'report_problem', new=AsyncMock(return_value={'reported': True})), patch('common.library_client.library_request', library):
            result = await asyncio.wait_for(agent.run.__wrapped__(body), 3)
        return result, browser, client

    async def test_every_tool_routes_and_returns_results(self):
        arguments = {
            'agent_note': {'text': 'Fixture note'},
            'report_tool_problem': {'tool_name': 'read_download', 'category': 'unsupported_file'},
            'read_user_memory': {}, 'remember_user_fact': {'key': 'group', 'text': 'Fixture group'},
            'calendar_list': {},
            'browser_sites': {},
            'browser_follow': {'site_id': '', 'index': 0}, 'browser_download': {'site_id': '', 'index': 0}, 'browser_login': {'site_id': 'fixture'}, 'browser_read': {'site_id': ''},
            'read_page': {'site_id': '', 'action': 'read', 'url': '', 'index': 0},
            'web_search': {'query': 'fixture'}, 'web_read': {'url': 'https://fixture.example'},
            'browser_screenshot': {'full_page': True},
            'browser_click': {'target': 'text', 'name': 'Button', 'role': '', 'selector': '', 'x': 0, 'y': 0},
            'browser_type': {'text': 'Fixture'}, 'browser_press': {'key': 'Enter'}, 'browser_scroll': {'delta': 100},
            'browser_navigate': {'action': 'open', 'url': 'https://fixture.example'},
            'browser_view_image': {'index': 0, 'url': ''},
            'download_file': {'url': 'https://fixture.example/file'},
            'python_http_request': {'url': 'https://fixture.example/api', 'method': 'GET', 'body': '', 'content_type': ''},
            'list_downloads': {},
            'list_user_files': {}, 'read_user_file': {'path': 'notes.txt'},
            'list_memory': {}, 'read_memory': {'filename': 'task-1.json'},
            'list_saved_files': {'query': ''},
            'read_file': {'source_task_id': 1, 'file_id': 'fixture'},
            'view_pdf_page': {'source_task_id': 1, 'file_id': 'fixture', 'page': 1}, 'read_download': {'file_id': 'fixture', 'page': 1},
            'python_binary': {'file_id': 'fixture', 'mode': 'hex', 'format': '', 'member': '', 'offset': 0},
            'calendar_save_events': {'events': [EVENT]}, 'calendar_remove_events': {'ids': ['existing']},
            'calendar_timezone': {'timezone': 'Europe/Moscow'},
            'calendar_finish': {'status': 'unchanged', 'reason': 'Already matches'},
        }
        self.assertEqual(set(arguments), {tool['name'] for tool in agent.TOOLS})
        for name, args in arguments.items():
            with self.subTest(tool=name):
                calendar = None
                if name == 'calendar_remove_events':
                    calendar = {'events': [EVENT | {'id': 'existing'}], 'timezone': 'UTC'}
                changed = name in ('calendar_save_events', 'calendar_remove_events', 'calendar_timezone')
                calls = [(name, args)]
                if name != 'calendar_finish':
                    calls.append(('calendar_finish', {'status': 'changed' if changed else 'unchanged', 'reason': 'Fixture result'}))
                result, browser, client = await self.execute(calls, calendar)
                self.assertEqual(result['status'], 'changed' if changed else 'unchanged')
                if name in agent.VISUAL_ACTIONS or name in FILE_NAMES or name in ('browser_login', 'browser_sites', 'browser_read', 'browser_follow', 'browser_download', 'read_page', 'web_read', 'web_search'):
                    self.assertEqual(browser.library_client.await_count if name in LIBRARY_NAMES else browser.await_count, 1)
                    self.assertTrue(any(isinstance(item, dict) and item.get('type') == 'function_call_output' for item in client.inputs[-1]))
                    self.assertTrue(any(isinstance(item, dict) and item.get('role') == 'user' and isinstance(item.get('content'), list) and item['content'][0].get('type') == 'input_image' for item in client.inputs[-1]))

    async def test_finish_cannot_claim_change_without_mutation(self):
        result, _, client = await self.execute([
            ('calendar_finish', {'status': 'changed', 'reason': 'Not actually changed'}),
            ('calendar_finish', {'status': 'blocked', 'reason': 'Missing facts'})])
        self.assertEqual(result['status'], 'blocked')
        output = next(item for item in client.inputs[-1] if isinstance(item, dict) and item.get('type') == 'function_call_output')
        self.assertIn('error', json.loads(output['output']))

    async def test_blocked_discards_draft(self):
        result, _, _ = await self.execute([
            ('calendar_save_events', {'events': [EVENT]}),
            ('calendar_finish', {'status': 'blocked', 'reason': 'Unconfirmed data'})])
        self.assertEqual(result['calendar']['events'], [])

    async def test_file_from_read_page_is_preserved(self):
        remote = AsyncMock(side_effect=[{'status': 'file', 'name': 'fixture.txt', 'content': 'dGV4dA=='}, {'file_id': 'saved'}])
        client = Client([('read_page', {'site_id': '', 'action': 'read', 'url': '', 'index': 0}),
                         ('calendar_finish', {'status': 'unchanged', 'reason': 'Fixture'})])
        with patch.object(agent, 'AsyncOpenAI', return_value=client), patch.object(agent, 'browser_request', remote):
            await agent.run.__wrapped__({'api_key': 'fixture', 'config': {}, 'instruction': 'Fixture', 'calendar': {'events': []}, 'tasks': []})
        self.assertEqual(remote.call_args_list[-1].args[0], '/files')
        self.assertEqual(remote.call_args_list[-1].args[1]['action'], 'save_download')

    async def test_no_javascript_tool(self):
        self.assertNotIn('browser_javascript', {item['name'] for item in agent.TOOLS})


class SharedBrowserContract(unittest.TestCase):
    def test_straus_uses_shared_tools_and_dispatch(self):
        from common.browser_tools import BROWSER_TOOLS, BROWSER_NAMES, run_browser_tool
        self.assertIs(agent.run_browser_tool, run_browser_tool)
        self.assertEqual([tool for tool in agent.TOOLS if tool['name'] in BROWSER_NAMES], BROWSER_TOOLS)
        from common.file_tools import FILE_TOOLS, FILE_NAMES, run_file_tool
        self.assertIs(agent.run_file_tool, run_file_tool)
        self.assertEqual([tool for tool in agent.TOOLS if tool['name'] in FILE_NAMES], FILE_TOOLS)


class ModelReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_retries_connection_error_once(self):
        import httpx
        from common.agent_api import stream_model_response
        from common.progress import Progress
        class RetryClient(Client):
            def __init__(self):
                super().__init__([('calendar_list', {})])
                self.attempts = 0
            def stream(self, **kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise httpx.ReadError('fixture connection failure')
                return super().stream(**kwargs)
        token = current_user_id.set(900002)
        try:
            client = RetryClient()
            with Progress('Straus') as progress:
                progress.begin_response()
                result = await stream_model_response(client, progress, input=[])
                self.assertEqual(result.status, 'completed')
                self.assertEqual(client.attempts, 2)
                self.assertTrue(any('повторяет' in event['message'] for event in progress.record['events']))
        finally:
            current_user_id.reset(token)


class ModelReconnectLimit(unittest.IsolatedAsyncioTestCase):
    async def test_connection_failure_does_not_retry_forever(self):
        import httpx
        from fastapi import HTTPException
        from common.agent_api import stream_model_response
        from common.progress import Progress
        class Unavailable:
            def __init__(self):
                self.responses = self
                self.attempts = 0
            def stream(self, **arguments):
                self.attempts += 1
                raise httpx.ConnectError('fixture unavailable')
        token = current_user_id.set(900002)
        try:
            client = Unavailable()
            with self.assertRaises(HTTPException), Progress('Straus') as progress:
                await stream_model_response(client, progress, input=[])
            self.assertEqual(client.attempts, 2)
        finally:
            current_user_id.reset(token)


class ModelSilence(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, behavior, retry='completed'):
        from common import agent_api
        from common.progress import Progress
        response = SimpleNamespace(status='completed', usage=None)
        streams = []
        arguments = []

        class FixtureStream:
            closed = False
            def __init__(self, mode): self.mode = mode
            async def __aenter__(self):
                if self.mode == 'opening':
                    await asyncio.sleep(10)
                return self
            async def __aexit__(self, *args): self.closed = True
            def __aiter__(self): return self.events()
            async def events(self):
                if self.mode in ('silent', 'opening'):
                    await asyncio.sleep(10)
                elif self.mode == 'created':
                    for _ in range(100):
                        yield SimpleNamespace(type='response.created')
                        await asyncio.sleep(0.01)
                elif self.mode == 'partial':
                    yield SimpleNamespace(type='response.output_text.delta', delta='fixture')
                    await asyncio.sleep(10)
                elif self.mode == 'active':
                    for _ in range(8):
                        await asyncio.sleep(0.015)
                        yield SimpleNamespace(type='response.function_call_arguments.delta', delta='fixture')
                elif self.mode == 'cancelled':
                    raise asyncio.CancelledError()
                yield SimpleNamespace(type='response.completed', response=response)

        def stream(**kwargs):
            arguments.append(kwargs)
            current = FixtureStream(behavior if not streams else retry)
            streams.append(current)
            return current

        token = current_user_id.set(900002)
        try:
            with patch.multiple(agent_api, MODEL_FIRST_DATA_TIMEOUT=0.06, MODEL_IDLE_TIMEOUT=0.06), Progress('Straus') as progress:
                progress.begin_response()
                try:
                    result = await agent_api.stream_model_response(SimpleNamespace(responses=SimpleNamespace(stream=stream)), progress,
                        input=[{'role': 'user', 'content': 'fixture'}], tools=[])
                finally:
                    self.calls, self.streams, self.events = arguments, streams, list(progress.record['events'])
                return result
        finally:
            current_user_id.reset(token)

    async def test_silence_retries_same_request(self):
        result = await self.run_stream('silent')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], self.calls[1])
        self.assertTrue(self.streams[0].closed)
        self.assertTrue(any('3 минуты' in event['message'] for event in self.events))

    async def test_opening_connection_has_deadline(self):
        await self.run_stream('opening')
        self.assertEqual(len(self.calls), 2)

    async def test_created_events_do_not_hide_silence(self):
        await self.run_stream('created')
        self.assertEqual(len(self.calls), 2)

    async def test_pause_after_partial_text_retries(self):
        await self.run_stream('partial')
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.streams[0].closed)

    async def test_active_stream_can_take_longer_than_deadline(self):
        result = await self.run_stream('active')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(len(self.calls), 1)

    async def test_second_silence_is_reported(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            await self.run_stream('silent', 'silent')
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(len(self.calls), 2)

    async def test_user_cancellation_is_not_retried(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.run_stream('cancelled')
        self.assertEqual(len(self.calls), 1)
