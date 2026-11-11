import asyncio
import base64
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

import httpx
from PIL import Image
from playwright.async_api import async_playwright
from pydantic import ValidationError
from pypdf import PdfWriter

import main as browser
import files
from common.identity import current_user_id
from integrations import scheduler


class BrowserTools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = current_user_id.set(900001)
        self.directory = tempfile.TemporaryDirectory()
        self.patch_root = patch.object(files, 'root', return_value=Path(self.directory.name))
        self.patch_root.start()
        self.patch_vault = patch.object(browser, 'vault', return_value={})
        self.patch_vault.start()
        self.patch_url = patch.object(browser, 'public_url', new=AsyncMock())
        self.patch_url.start()
        self.playwright = await async_playwright().start()
        self.engine = await self.playwright.chromium.launch()
        self.context = await self.engine.new_context(accept_downloads=True, viewport={'width': 1100, 'height': 750})
        browser.capture_downloads(self.context)
        self.context.on('page', lambda tab: browser.watch_screen(tab))
        self.contexts = patch.dict(browser.contexts, {900001: self.context})
        self.contexts.start()
        async def route(request):
            if request.request.url.endswith('/file'):
                await request.fulfill(body=b'file contents', content_type='application/octet-stream', headers={'Content-Disposition': 'attachment; filename=example.txt'})
            else:
                await request.fulfill(body='''<html><body><h1>Fixture</h1><input aria-label="Search"><button onclick="document.querySelector('h1').textContent='Clicked'">Press me</button><a href="/second">Next</a><button onclick="const a=document.createElement('a');a.href=window.URL.createObjectURL(new Blob(['blob contents']));a.download='blob.txt';document.body.appendChild(a);a.click()">Download blob</button><a href="/file" target="_blank">Popup file</a><img alt="picture" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6X1cAAAAASUVORK5CYII="><a href="https://fixture.example/search-result"><h3>Fixture result</h3></a><div style="height:1800px"></div></body></html>''', content_type='text/html')
        await self.context.route('**/*', route)
        self.tab = await self.context.new_page()
        self.page_errors = []
        self.tab.on('pageerror', lambda error: self.page_errors.append(str(error)))
        await self.tab.goto('https://fixture.example/start')
        browser.watch_screen(self.tab)

    async def asyncTearDown(self):
        tasks = [item['task'] for item in browser.browser_downloads.get(900001, []) if item.get('task')]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.engine.close()
        await self.playwright.stop()
        self.contexts.stop()
        for value in (self.patch_url, self.patch_vault, self.patch_root):
            value.stop()
        self.directory.cleanup()
        browser.screens.pop(900001, None)
        browser.browser_downloads.pop(900001, None)
        current_user_id.reset(self.owner)

    async def visual(self, action, **values):
        return await browser.run_visual_action(browser.VisualAction(action=action, **values))

    async def test_screenshots(self):
        for full in (False, True):
            with self.subTest(full=full):
                result = await self.visual('screenshot', full_page=full)
                self.assertTrue(base64.b64decode(result['image']))
                self.assertIn('Fixture', result['text'])

    async def test_click_all_targets(self):
        for target, values in [('role', {'role': 'button', 'name': 'Press me'}), ('text', {'name': 'Press me'}), ('selector', {'selector': 'button:first-of-type'}), ('css', {}), ('xpath', {}), ('selector', {'selector': 'xpath=//button[1]'}), ('element', {}), ('coordinates', {})]:
            with self.subTest(target=target):
                await self.tab.goto('https://fixture.example/start')
                if target in ('css', 'xpath'):
                    values['selector'] = next(item[target] for item in await browser.elements(self.tab) if item['name'] == 'Press me')
                if target == 'element':
                    values['name'] = next(item['id'] for item in await browser.elements(self.tab) if item['name'] == 'Press me')
                if target == 'coordinates':
                    bounds = await self.tab.get_by_role('button', name='Press me', exact=True).bounding_box()
                    values.update(x=int(bounds['x'] + bounds['width']/2), y=int(bounds['y'] + bounds['height']/2))
                result = await self.visual('click', target=target, **values)
                self.assertIn('Clicked', result['text'])
                self.assertEqual(result['clicked'], 'Press me')

    async def test_text_click_uses_parent_button(self):
        await self.tab.set_content('''<button onclick="this.dataset.clicked='yes'" style="position:relative;width:150px;height:40px;overflow:hidden"><div style="position:absolute;left:145px;width:300px">SSO sign in</div></button>''')
        result = await self.visual('click', target='text', name='SSO sign in')
        self.assertEqual(await self.tab.locator('button').get_attribute('data-clicked'), 'yes')
        self.assertEqual(result['clicked'], 'SSO sign in')

    async def test_click_before_page_has_finished_loading(self):
        gate = asyncio.Event()
        requested = asyncio.Event()
        async def slow_script(route):
            requested.set()
            await gate.wait()
            await route.fulfill(body='window.fixtureLoaded = true', content_type='application/javascript')
        async def slow_page(route):
            await route.fulfill(body='''<button onclick="this.dataset.clicked='yes'">Early button</button><script src="/slow.js"></script>''', content_type='text/html')
        await self.context.route('**/slow.js', slow_script)
        await self.context.route('**/slow-page', slow_page)
        try:
            result = await asyncio.wait_for(self.visual('open', url='https://fixture.example/slow-page'), 2)
            await asyncio.wait_for(requested.wait(), 1)
            self.assertEqual(await self.tab.evaluate('document.readyState'), 'loading')
            await asyncio.wait_for(self.visual('click', target='role', role='button', name='Early button'), 2)
            self.assertEqual(await self.tab.locator('button').get_attribute('data-clicked'), 'yes')
            self.assertFalse(gate.is_set())
        finally:
            gate.set()

    async def test_type_press_scroll_navigate_image(self):
        await self.visual('click', target='selector', selector='input')
        await self.visual('type', text='query')
        self.assertEqual(await self.tab.locator('input').input_value(), 'query')
        await self.visual('press', text='Control+A')
        await self.visual('scroll', delta=500)
        self.assertGreater(await self.tab.evaluate('scrollY'), 0)
        await self.visual('open', url='https://fixture.example/second')
        await self.visual('back')
        self.assertTrue(self.tab.url.endswith('/start'))
        await self.visual('forward')
        self.assertTrue(self.tab.url.endswith('/second'))
        self.assertIn('image', await self.visual('image', index=0))

    async def test_javascript_is_rejected(self):
        with self.assertRaises(ValidationError):
            browser.VisualAction(action='javascript', script='document.title')

    async def test_read_current_without_site(self):
        result = await browser.agent_action(browser.Action(site_id='', action='read'))
        self.assertIn('Fixture', result['text'])
        self.assertIn('ready_state', result)
        self.assertIs(await browser.page(), await browser.public_page())

    async def test_blob_and_popup_download(self):
        for button in ['Download blob', 'Popup file']:
            with self.subTest(button=button):
                browser.watch_screen(self.tab)
                await self.visual('click', target='text', name=button)
                for _ in range(50):
                    if browser.download_status() and browser.download_status()[-1]['status'] != 'downloading':
                        break
                    await asyncio.sleep(.05)
                self.assertTrue(browser.download_status(), f'No download; page errors: {self.page_errors}; onclick: {await self.tab.get_by_text(button, exact=True).get_attribute("onclick")}')
                record = browser.download_status()[-1]
                self.assertEqual(record['status'], 'saved')
                self.assertIn('contents', files.read(record['file_id'])['text'])

    async def test_file_tools_and_binary(self):
        result = await browser.run_file_action({'action': 'save_download', 'name': 'numbers.bin', 'content': base64.b64encode(struct.pack('<II', 7, 42)).decode()})
        file_id = result['file_id']
        self.assertIn('hex', await browser.run_file_action({'action': 'read_download', 'file_id': file_id, 'page': 1}))
        result = await browser.run_file_action({'action': 'python_binary', 'file_id': file_id, 'mode': 'struct', 'format': '<II', 'member': '', 'offset': 0})
        self.assertEqual(result['values'], [7, 42])
        self.assertTrue((await browser.run_file_action({'action': 'python_binary', 'file_id': file_id, 'mode': 'hex', 'format': '', 'member': '', 'offset': 0}))['hex'])
        self.assertEqual(len((await browser.run_file_action({'action': 'list_downloads'}))['files']), 1)

    async def test_pdf_office_zip_images(self):
        stream = io.BytesIO()
        pdf = PdfWriter(); pdf.add_blank_page(width=100, height=100); pdf.write(stream)
        result = files.read(files.save('sample.pdf', stream.getvalue())['file_id'])
        self.assertIn('image', result)
        image = io.BytesIO(); Image.new('RGB', (10, 10)).save(image, format='PNG')
        self.assertIn('image', files.read(files.save('sample.png', image.getvalue())['file_id']))
        for extension, member in [('docx', 'word/document.xml'), ('xlsx', 'xl/sharedStrings.xml'), ('pptx', 'ppt/slides/slide1.xml'), ('zip', 'notes.txt')]:
            stream = io.BytesIO()
            with ZipFile(stream, 'w') as zipped:
                zipped.writestr(member, '<root>Content</root>')
            file_id = files.save('sample.'+extension, stream.getvalue())['file_id']
            self.assertIn(member, files.read(file_id)['members'])
            result = files.binary({'file_id': file_id, 'mode': 'zip', 'member': member, 'offset': 0, 'format': ''})
            self.assertIn('Content', result['text'])

    async def test_login_read_follow_share_profile(self):
        site = {'url': 'https://fixture.example/start', 'username': '', 'password': ''}
        with patch.object(browser, 'get_site', return_value=site):
            await browser.agent_action(browser.Action(site_id='saved-login', action='login'))
        await self.context.add_cookies([{'name': 'fixture_session', 'value': 'fixture-value', 'url': 'https://fixture.example'}])
        result = await browser.browse_web(browser.WebAction(action='read', url='https://fixture.example/second'))
        self.assertIn('Fixture', result['text'])
        self.assertEqual((await browser.public_page()).context, self.context)
        result = await browser.agent_action(browser.Action(site_id='', action='follow', index=0))
        self.assertIn('Fixture', result['text'])
        self.assertEqual((await self.context.cookies())[0]['name'], 'fixture_session')

    async def test_search_uses_same_browser(self):
        result = await browser.browse_web(browser.WebAction(action='search', query='fixture'))
        self.assertTrue((await browser.page()).url.startswith('https://www.google.com/search?'))
        self.assertEqual(result['status'], 'search')

    async def test_file_rejections_and_legacy_office(self):
        with self.assertRaises(ValueError):
            files.load('../other-user/file')
        file_id = files.save('old.xls', bytes.fromhex('d0cf11e0a1b11ae1') + b'fixture')['file_id']
        result = files.read(file_id)
        self.assertEqual(result['format'], 'ole_office')
        self.assertNotIn('text', result)
        with self.assertRaises(Exception):
            files.binary({'file_id': file_id, 'mode': 'struct', 'format': '9999999s', 'offset': 0})

    async def test_python_http_and_download(self):
        real_client = httpx.AsyncClient
        def handler(request):
            return httpx.Response(200, content=b'HTTP contents', headers={'content-type': 'text/plain'})
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(files.httpx, 'AsyncClient', side_effect=client):
            for method in ('GET', 'POST'):
                result = await browser.run_file_action({'action': 'python_http_request', 'url': 'https://fixture.example/api', 'method': method, 'body': '', 'content_type': ''})
                self.assertEqual(result['text'], 'HTTP contents')
            result = await browser.run_file_action({'action': 'download_file', 'url': 'https://fixture.example/document.txt'})
            self.assertEqual(files.read(result['file_id'])['text'], 'HTTP contents')


class QueueIsolation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scheduler.condition = asyncio.Condition()
        scheduler.waiting.clear(); scheduler.running.clear(); scheduler.planned.clear()
        scheduler.active_users.clear(); scheduler.jeba_users.clear(); scheduler.ranking_requests.clear()

    async def test_same_user_serial_other_user_parallel(self):
        release = asyncio.Event(); first = asyncio.Event(); second = asyncio.Event(); other = asyncio.Event()
        async def work(owner, entered):
            token = current_user_id.set(owner)
            try:
                async with scheduler.slot('context'):
                    entered.set()
                    await release.wait()
            finally:
                current_user_id.reset(token)
        tasks = [asyncio.create_task(work(1, first))]
        await first.wait()
        tasks += [asyncio.create_task(work(1, second)), asyncio.create_task(work(2, other))]
        await asyncio.wait_for(other.wait(), 1)
        self.assertFalse(second.is_set())
        token = current_user_id.set(3)
        self.assertFalse(scheduler.queue_state()['busy'])
        current_user_id.reset(token)
        release.set()
        await asyncio.gather(*tasks)
        self.assertTrue(second.is_set())

    async def test_cannot_interrupt_other_users_ameba(self):
        event = asyncio.Event()
        task = asyncio.create_task(event.wait())
        scheduler.ranking_requests[2] = {'owner': 2, 'task': task}
        token = current_user_id.set(1)
        await scheduler.interrupt_ranking()
        self.assertFalse(task.cancelled())
        current_user_id.reset(token)
        event.set(); await task


class SharedMemory(unittest.TestCase):
    def test_agents_share_facts_but_users_do_not(self):
        import agent_memory
        from sqlmodel import create_engine
        with tempfile.TemporaryDirectory() as folder:
            engines = {owner: create_engine(f'sqlite:///{folder}/{owner}.db') for owner in (1, 2)}
            token = current_user_id.set(1)
            try:
                with patch.object(agent_memory, 'get_engine', side_effect=lambda: engines[browser.user_id()]):
                    agent_memory.save({'key': 'group', 'text': 'Group A'}, 'Straus')
                    self.assertEqual(agent_memory.read()[0]['agent'], 'Straus')
                    agent_memory.save({'key': 'group', 'text': 'Group B'}, 'Lucheba')
                    self.assertEqual(len(agent_memory.read()), 1)
                    self.assertEqual(agent_memory.read()[0]['text'], 'Group B')
                    agent_memory.save({'key': 'work', 'text': 'Evening'}, 'Ameba')
                    self.assertEqual(len(agent_memory.read()), 2)
                    current_user_id.set(2)
                    self.assertEqual(agent_memory.read(), [])
                    agent_memory.delete('group')
                    current_user_id.set(1)
                    self.assertEqual(len(agent_memory.read()), 2)
                    agent_memory.delete('group')
                    self.assertEqual(agent_memory.read()[0]['key'], 'work')
            finally:
                current_user_id.reset(token)
