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
