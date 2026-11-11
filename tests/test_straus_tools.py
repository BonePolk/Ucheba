"""Каждый инструмент Straus: схема, диспетчеризация и результат в истории модели."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import agent
from common.identity import current_user_id
from common.file_tools import FILE_NAMES


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
        body = {'api_key': 'fixture-key', 'config': {'model': 'fixture'}, 'instruction': 'Fixture instruction',
                'calendar': calendar or {'events': [], 'timezone': 'UTC'}, 'tasks': []}
        with patch.object(agent, 'AsyncOpenAI', return_value=client), patch.object(agent, 'browser_request', browser), patch.object(agent, 'memory_tool', new=AsyncMock(return_value={'ok': True})):
            result = await asyncio.wait_for(agent.run.__wrapped__(body), 3)
        return result, browser, client

    async def test_every_tool_routes_and_returns_results(self):
        arguments = {
            'agent_note': {'text': 'Fixture note'},
            'read_user_memory': {}, 'remember_user_fact': {'key': 'group', 'text': 'Fixture group'},
            'calendar_list': {},
            'browser_sites': {}, 'browser_login': {'site_id': 'fixture'}, 'browser_read': {'site_id': ''},
            'read_page': {'site_id': '', 'action': 'read', 'url': '', 'index': 0},
            'web_search': {'query': 'fixture'}, 'web_read': {'url': 'https://fixture.example'},
            'browser_screenshot': {'full_page': True},
            'browser_click': {'target': 'text', 'name': 'Button', 'role': '', 'selector': '', 'x': 0, 'y': 0},
            'browser_type': {'text': 'Fixture'}, 'browser_press': {'key': 'Enter'}, 'browser_scroll': {'delta': 100},
            'browser_navigate': {'action': 'open', 'url': 'https://fixture.example'},
            'browser_view_image': {'index': 0, 'url': ''},
            'download_file': {'url': 'https://fixture.example/file'},
            'python_http_request': {'url': 'https://fixture.example/api', 'method': 'GET', 'body': '', 'content_type': ''},
            'list_downloads': {}, 'read_download': {'file_id': 'fixture', 'page': 1},
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
                if name in agent.VISUAL_ACTIONS or name in FILE_NAMES or name in ('browser_login', 'browser_sites', 'browser_read', 'read_page', 'web_read', 'web_search'):
                    self.assertEqual(browser.await_count, 1)
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
