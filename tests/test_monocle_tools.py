"""Проверка форматов вложений Monocle без Silo и без модели."""
import io
import unittest
from zipfile import ZipFile

import materials
from common.office import spreadsheet_text


class MonocleAttachments(unittest.TestCase):
    def test_xlsx_attachment_reads_values(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as zipped:
            zipped.writestr('xl/sharedStrings.xml', '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Учебная группа</t></si></sst>')
            zipped.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><v>513</v></c></row></sheetData></worksheet>')
        kind, text, mime = materials.extract('sample.xlsx', stream.getvalue())
        self.assertEqual(kind, 'text')
        self.assertIn('A1: Учебная группа', text)
        self.assertIn('B1: 513', text)
        self.assertIn('spreadsheetml', mime)

    def test_pptx_attachment_reads_slides(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as zipped:
            zipped.writestr('ppt/slides/slide1.xml', '<root>Slide contents</root>')
        self.assertIn('Slide contents', materials.extract('slides.pptx', stream.getvalue())[1])

    def test_docx_text_pdf_and_image(self):
        from docx import Document
        from PIL import Image
        from pypdf import PdfWriter
        stream = io.BytesIO(); document = Document(); document.add_paragraph('DOCX contents'); document.save(stream)
        self.assertIn('DOCX contents', materials.extract('sample.docx', stream.getvalue())[1])
        self.assertEqual(materials.extract('sample.txt', 'Текст'.encode())[1], 'Текст')
        stream = io.BytesIO(); Image.new('RGB', (10,10)).save(stream, format='PNG')
        self.assertEqual(materials.extract('sample.png', stream.getvalue())[0], 'image')
        stream = io.BytesIO(); pdf = PdfWriter(); pdf.add_blank_page(width=100, height=100); pdf.write(stream)
        self.assertEqual(materials.extract('sample.pdf', stream.getvalue())[0], 'pdf')


class SharedBrowserContract(unittest.TestCase):
    def test_monocle_uses_shared_tools_and_dispatch(self):
        import main
        from common.browser_tools import BROWSER_TOOLS, run_browser_tool
        self.assertIs(main.BROWSER_TOOLS, BROWSER_TOOLS)
        self.assertIs(main.run_browser_tool, run_browser_tool)
        from common.file_tools import FILE_TOOLS, run_file_tool
        self.assertIs(main.FILE_TOOLS, FILE_TOOLS)
        self.assertIs(main.run_file_tool, run_file_tool)


class SharedLibrary(unittest.IsolatedAsyncioTestCase):
    async def test_all_shared_library_operations(self):
        from unittest.mock import Mock, patch
        import library
        from common.file_tools import LIBRARY_NAMES
        fake = Mock()
        fake.list_files.return_value = ['task-1.json']
        fake.read.return_value = '{"task_id":1}'
        info = {'id': 'fixture', 'name': 'fixture.pdf', 'size': 10, 'kind': 'pdf', 'text': 'Document contents', 'pages': 1}
        cases = {
            'list_user_files': {}, 'read_user_file': {'path': 'notes.txt'},
            'list_memory': {}, 'read_memory': {'filename': 'task-1.json'},
            'list_saved_files': {'query': ''}, 'read_file': {'source_task_id': 1, 'file_id': 'fixture'},
            'view_pdf_page': {'source_task_id': 1, 'file_id': 'fixture', 'page': 1},
        }
        self.assertEqual(set(cases), LIBRARY_NAMES)
        with patch.object(library, 'Memory', return_value=fake), \
             patch.object(library.workspace, 'tree', return_value=[]), \
             patch.object(library.workspace, 'read', return_value={'content': 'Personal notes'}), \
             patch.object(library.materials, 'saved_files', return_value=[info]), \
             patch.object(library.materials, 'get_info', return_value=info), \
             patch.object(library.materials, 'file_input', return_value=[{'type': 'input_text', 'text': 'Document contents'}]), \
             patch.object(library.materials, 'pdf_page', return_value=[{'type': 'input_image', 'image_url': 'data:image/png;base64,aW1hZ2U='}]):
            for name, arguments in cases.items():
                with self.subTest(tool=name):
                    result = await library.run(name, arguments)
                    self.assertIn('output', result)
                    if name in ('read_file', 'view_pdf_page'):
                        self.assertTrue(result['content'])
                    if name == 'read_user_file':
                        self.assertEqual(result['output']['content'], 'Personal notes')


class LibraryReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_library_retry_keeps_owner(self):
        import os
        import httpx
        from unittest.mock import patch
        from common.identity import current_user_id
        from common.library_client import library_request
        real_client = httpx.AsyncClient
        calls = []
        def handler(request):
            calls.append(request.headers['x-user-id'])
            if len(calls) == 1:
                raise httpx.ReadError('fixture connection failure')
            return httpx.Response(200, json={'output': 'document', 'content': []})
        def client(**kwargs):
            kwargs['transport'] = httpx.MockTransport(handler)
            return real_client(**kwargs)
        token = current_user_id.set(900004)
        try:
            with patch.dict(os.environ, {'INTERNAL_SECRET': 'fixture-secret'}), patch('common.library_client.httpx.AsyncClient', side_effect=client):
                result = await library_request('read_memory', {'filename': 'task-1.json'})
            self.assertEqual(result['output'], 'document')
            self.assertEqual(calls, ['900004', '900004'])
        finally:
            current_user_id.reset(token)


class MonocleStructuredOutput(unittest.IsolatedAsyncioTestCase):
    VALID_CONTEXT = {
        'status': 'ready',
        'summary': 'Сделать лабораторную работу по сетям до пятницы.',
        'time_estimate': '3–4 ч',
        'effort': 'Средняя: настройка топологии и отчёт',
        'impact': 'Зачёт по лабораторной работе',
        'full_markdown': '## Условия\nСделать лабораторную.\n\n## Время\n3–4 ч\n\n## Силы\nСредние\n\n## Эффект результата\nЗачёт',
        'task_update': {'title': None},
        'questions': [],
    }

    async def asyncSetUp(self):
        from common.identity import current_user_id
        self.token = current_user_id.set(900005)

    async def asyncTearDown(self):
        from common.identity import current_user_id
        current_user_id.reset(self.token)

    async def run_monocle(self, outcomes):
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock, patch
        import common.codex_harness as codex_harness
        from common.progress import Progress
        import main

        captured = []
        thread = MagicMock()
        client = MagicMock()
        client.thread_start = AsyncMock(return_value=thread)

        @asynccontextmanager
        async def fake_open_codex(*args, **kwargs):
            yield client, 'ucheba_api'

        @asynccontextmanager
        async def fake_scratch(*args, **kwargs):
            yield '/tmp/work', {}

        @asynccontextmanager
        async def fake_serve(adapter):
            yield {'mcp_servers': {}}

        async def fake_turn(thread_arg, prompt_input, **kwargs):
            captured.append(kwargs | {'prompt_input': prompt_input})
            return outcomes[len(captured) - 1]

        memory = MagicMock()
        body = {'task_id': 1, 'task': {'title': 'Лаба'}, 'subject': '', 'owner_notes': ''}
        auth = ('api_key', 'fixture-key', 'https://example.test/v1', 'gpt-6-luna')
        with Progress('Monocle', 1) as progress, \
             patch.object(main.materials, 'model_input', return_value=[]), \
             patch.object(main.materials, 'browser_site', return_value=None), \
             patch.object(codex_harness, 'fetch_provider_model_metadata', AsyncMock(return_value=None)), \
             patch.object(main, 'open_codex', fake_open_codex), \
             patch.object(main, 'scratch_env', fake_scratch), \
             patch.object(main, 'serve_mcp', fake_serve), \
             patch.object(main, 'run_codex_turn', side_effect=fake_turn):
            result = await main.run_agent(auth, memory, 1, body, progress)
        return result, captured, memory

    def test_strict_context_result_schema(self):
        import main

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

        check_strict(main.ContextResult.model_json_schema())

    async def test_empty_output_after_tool_recovers_structured_json(self):
        import json
        from common.codex_harness import TurnOutcome

        outcomes = [
            TurnOutcome(text='', completed=True, status='completed', error_detail=None, usage={'input_tokens': 10, 'output_tokens': 5}),
            TurnOutcome(text=json.dumps(self.VALID_CONTEXT, ensure_ascii=False), completed=True, status='completed', error_detail=None, usage={'input_tokens': 5, 'output_tokens': 8}),
        ]
        result, captured, memory = await self.run_monocle(outcomes)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(captured[-1]['effort'], 'low')
        self.assertEqual(result['usage']['input_tokens'], 15)
        self.assertEqual(result['usage']['output_tokens'], 13)
        memory.save.assert_called_once()

    async def test_mcp_tool_adapter_dispatches_and_tracks_sources(self):
        import json
        from unittest.mock import AsyncMock, MagicMock, patch
        from common.codex_harness import McpToolAdapter
        from common.progress import Progress
        import main

        memory = MagicMock()
        browser = AsyncMock(return_value={
            'status': 'page',
            'url': 'https://example.test/task',
            'title': 'Task Page',
            'text': 'Body',
            'elements': [{'id': '1', 'name': 'Open', 'role': 'button'}],
            'image': 'aW1hZ2U=',
            'mime': 'image/png',
        })
        with Progress('Monocle', 1) as progress, \
             patch.object(main, 'browser_request', browser), \
             patch.object(main.materials, 'browser_images', return_value=[
                 {'type': 'input_text', 'text': 'Area'},
                 {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,aW1hZ2U='},
             ]):
            handler = main.MonocleTools(memory, 1, progress)
            adapter = McpToolAdapter(main.ALL_MONOCLE_TOOLS, handler.call, progress)
            reply = await adapter.respond({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'browser_read', 'arguments': {'site_id': 's1'}},
            })
        self.assertFalse(reply['isError'])
        self.assertEqual(json.loads(reply['content'][0]['text'])['status'], 'page')
        self.assertTrue(any(part.get('type') == 'image' for part in reply['content']))
        self.assertIn('https://example.test/task', handler.sources)

    async def test_prose_fenced_json_and_edge_fields_normalized(self):
        import json
        from common.codex_harness import TurnOutcome

        fenced_data = dict(self.VALID_CONTEXT, time_estimate='  ', task_update={'title': ''}, questions=[' ', 'Уточните номер группы?'])
        text = 'Вот итоговый разбор задачи:\n```json\n' + json.dumps(fenced_data, ensure_ascii=False) + '\n```\nГотово.'
        outcomes = [TurnOutcome(text=text, completed=True, status='completed', error_detail=None, usage={'input_tokens': 0, 'output_tokens': 0})]
        result, _, _ = await self.run_monocle(outcomes)
        self.assertEqual(result['status'], 'needs_context')
        self.assertEqual(result['questions'], ['Уточните номер группы?'])
        self.assertEqual(result['time_estimate'], 'недостаточно данных')
        self.assertIsNone(result['task_update']['title'])

    async def test_plain_text_triggers_repair_and_succeeds(self):
        import json
        from common.codex_harness import TurnOutcome

        outcomes = [
            TurnOutcome(text='Нужно уточнить формат сдачи у преподавателя.', completed=True, status='completed', error_detail=None, usage={'input_tokens': 0, 'output_tokens': 0}),
            TurnOutcome(text=json.dumps(self.VALID_CONTEXT, ensure_ascii=False), completed=True, status='completed', error_detail=None, usage={'input_tokens': 0, 'output_tokens': 0}),
        ]
        result, captured, _ = await self.run_monocle(outcomes)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[1]['effort'], 'low')
        self.assertEqual(result['status'], 'ready')

    async def test_incomplete_response_retries_with_low_reasoning_and_succeeds(self):
        import json
        from common.codex_harness import TurnOutcome

        outcomes = [
            TurnOutcome(text='', completed=False, status='incomplete', error_detail='Monocle: агент не завершил работу', usage={'input_tokens': 0, 'output_tokens': 0}),
            TurnOutcome(text=json.dumps(self.VALID_CONTEXT, ensure_ascii=False), completed=True, status='completed', error_detail=None, usage={'input_tokens': 0, 'output_tokens': 0}),
        ]
        result, captured, _ = await self.run_monocle(outcomes)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(captured[1]['effort'], 'low')

    async def test_persistent_empty_output_raises_502(self):
        from fastapi import HTTPException
        from common.codex_harness import TurnOutcome

        empty = TurnOutcome(text='', completed=True, status='completed', error_detail=None, usage={'input_tokens': 0, 'output_tokens': 0})
        with self.assertRaises(HTTPException) as exc:
            await self.run_monocle([empty, empty, empty])
        self.assertEqual(exc.exception.status_code, 502)
        self.assertIn('не вернул структурированный контекст', exc.exception.detail)

    async def test_failed_turn_and_zero_usage_tokens(self):
        import json
        from fastapi import HTTPException
        from common.codex_harness import TurnOutcome

        failed = TurnOutcome(text='', completed=False, status='failed', error_detail='Monocle: шлюз или OpenAI отклонил авторизацию (401)', usage={'input_tokens': 0, 'output_tokens': 0})
        with self.assertRaises(HTTPException) as exc:
            await self.run_monocle([failed])
        self.assertEqual(exc.exception.status_code, 502)

        zero_usage = TurnOutcome(
            text=json.dumps(self.VALID_CONTEXT, ensure_ascii=False),
            completed=True,
            status='completed',
            error_detail=None,
            usage={'input_tokens': 0, 'output_tokens': 0},
        )
        result, _, _ = await self.run_monocle([zero_usage])
        self.assertEqual(result['usage']['input_tokens'], 0)
        self.assertEqual(result['usage']['output_tokens'], 0)
