"""Отдельный браузер пользователя. Модель не получает cookie, пароли или shell."""
import asyncio
import base64
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ipaddress
import json
import logging
import os
from email.message import Message
from pathlib import Path
import re
import socket
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4
from weakref import WeakKeyDictionary

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from playwright.async_api import async_playwright, Error as BrowserError, TimeoutError as BrowserTimeoutError
from pydantic import BaseModel, Field, SecretStr
from typing import Literal

from common.http import private_app
from common.identity import user_id, current_user_id

contexts = {}
locks = {}
user_controls = {}
screens = OrderedDict()
schedule_pages = {}
active_actions = {}
cancelled_actions = OrderedDict()
browser_downloads = {}
agent_runs = {}
deferred_browser_close = set()
browser_queues = {}
playwright = None
BROWSER_ACTION_TIMEOUT = 90
BROWSER_CLOSE_TIMEOUT = 15
BROWSER_ACQUIRE_POLL_SECONDS = 20
BROWSER_WAITER_TTL = 45

# Chromium не рисует системный курсор на screenshots. Маркер не перехватывает
# события и не добавляет текст в представление страницы для агента.
CURSOR_SCRIPT = """(() => {
    if (window.__uchebaCursorReady) return window.__uchebaCursorReady;
    window.__uchebaCursorReady = (async () => {
        let marker;
        function move(x, y) {
            if (!Number.isFinite(x) || !Number.isFinite(y)) return;
            if (window !== window.top) {
                window.parent.postMessage({ uchebaCursor: { x, y } }, '*');
                return;
            }
            x = Math.max(0, Math.min(innerWidth - 1, x));
            y = Math.max(0, Math.min(innerHeight - 1, y));
            window.__uchebaCursor = { x, y };
            window.__uchebaCursorPosition({ x, y });
            if (!document.documentElement) return;
            if (!marker) {
                marker = document.createElement('div');
                marker.setAttribute('aria-hidden', 'true');
                marker.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:none;overflow:hidden;contain:strict;';
                const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                icon.setAttribute('viewBox', '0 0 20 26');
                icon.style.cssText = 'position:absolute;width:20px;height:26px;pointer-events:none;';
                const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                for (const [name, value] of Object.entries({d: 'M1 1 L1 20 L6 15 L10 24 L14 22 L10 14 L18 14 Z', fill: '#7f49b4', stroke: 'white', 'stroke-width': '2', 'stroke-linejoin': 'round'})) arrow.setAttribute(name, value);
                icon.append(arrow);
                marker.append(icon);
            }
            if (!marker.isConnected) document.documentElement.append(marker);
            marker.firstElementChild.style.left = (x - 1) + 'px';
            marker.firstElementChild.style.top = (y - 1) + 'px';
        }
        window.__uchebaMoveCursor = ({ x, y }) => move(x, y);
        addEventListener('mousemove', (event) => move(event.clientX, event.clientY), true);
        addEventListener('message', (event) => {
            const point = event.data?.uchebaCursor;
            if (!point) return;
            const frame = Array.from(document.querySelectorAll('iframe, frame')).find((item) => item.contentWindow === event.source);
            if (!frame) return;
            const bounds = frame.getBoundingClientRect();
            const scaleX = frame.offsetWidth ? bounds.width / frame.offsetWidth : 1;
            const scaleY = frame.offsetHeight ? bounds.height / frame.offsetHeight : 1;
            move(bounds.left + (frame.clientLeft + point.x) * scaleX, bounds.top + (frame.clientTop + point.y) * scaleY);
        });
        if (window === window.top) {
            const point = await window.__uchebaCursorPosition();
            addEventListener('DOMContentLoaded', () => {
                const current = window.__uchebaCursor ?? point;
                move(current.x, current.y);
            }, { once: true });
            move(point.x, point.y);
        }
    })();
    return window.__uchebaCursorReady;
})()"""


@asynccontextmanager
async def lifespan(app):
    global playwright
    playwright = await async_playwright().start()
    try:
        yield
    finally:
        for context in contexts.values():
            await context.close()
        await playwright.stop()


# Используем токен context: браузер является частью этой функции.
app = private_app('context', lifespan)


def browser_failure(exc):
    # Полный текст Playwright может содержать URL с токенами и поля формы.
    text = str(exc)
    if isinstance(exc, BrowserTimeoutError):
        return 'timeout', 'Действие браузера не завершилось. Проверьте точный элемент и его доступность; полная загрузка страницы не требуется.'
    if 'Download is starting' in text:
        return 'download_navigation', 'Ссылка открывает скачивание файла вместо страницы.'
    network = re.search(r'net::(ERR_[A-Z_]+)', text)
    if network:
        code = network.group(1)
        return code, f'Сайт не открылся: {code}. Это ошибка соединения, а не подтверждение неверного пароля.'
    if changing_document(exc):
        return 'document_changed', 'Сайт перенаправил страницу во время чтения. Повторите чтение после перехода.'
    if 'closed' in text.lower():
        return 'browser_closed', 'Вкладка или браузер закрылись. Откройте личный кабинет заново.'
    return 'action_failed', 'Не удалось выполнить действие браузера. Попробуйте открыть страницу заново.'


def changing_document(exc):
    return any(value in str(exc) for value in (
        'Execution context was destroyed', 'Cannot find context with specified id',
        'Frame was detached', 'Frame has been detached',
    ))


@app.exception_handler(BrowserError)
async def browser_error(request, exc):
    from fastapi.responses import JSONResponse
    category, detail = browser_failure(exc)
    if category == 'download_navigation':
        return JSONResponse(status_code=200, content={'status': 'downloading', 'downloads': download_status(), 'notice': 'Началось скачивание. Проверь list_downloads; повторять нажатие не нужно.'})
    logging.getLogger('uvicorn.error').error('browser: %s (%s)', category, request.url.path)
    return JSONResponse(status_code=502, content={'detail': detail})


async def open_page(tab, url):
    try:
        await tab.goto(url, wait_until='domcontentloaded', timeout=15000)
        return True
    except BrowserTimeoutError:
        return False


async def schedule_snapshot(tab):
    for attempt in range(2):
        try:
            # Один вызов вместо отдельных чтений текста, ссылок и заголовка:
            # все данные относятся к одному документу.
            snapshot = await tab.evaluate("""() => ({
                url: location.href, title: document.title, ready_state: document.readyState,
                text: (document.body?.innerText || "").slice(0, 12000),
                headings: [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
                    .filter(el => el.getClientRects().length).map(el => el.innerText.trim()).filter(Boolean).slice(0, 40),
                images: [...document.querySelectorAll('img')].slice(0,100).map((el,index)=>({index,alt:el.alt})),
                needs_login: [...document.querySelectorAll('input[type="password"]')]
                    .some(input => input.getClientRects().length > 0),
                links: [...document.querySelectorAll('a[href]')]
                    .map(a => ({text: a.innerText.slice(0, 150), href: a.href}))
                    .filter(a => a.text.trim() && /^https?:/.test(a.href)).slice(0, 100)
            })""")
            for frame in tab.frames[1:]:
                try:
                    content = await frame.evaluate("""() => ({
                        text: (document.body?.innerText || '').slice(0, 12000),
                        headings: [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
                            .filter(el => el.getClientRects().length).map(el => el.innerText.trim()).filter(Boolean),
                        needs_login: [...document.querySelectorAll('input[type="password"]')]
                            .some(input => input.getClientRects().length > 0)
                    })""")
                    if content['text'] and len(snapshot['text']) < 12000:
                        snapshot['text'] = (snapshot['text'] + '\n[iframe]\n' + content['text'])[:12000]
                    snapshot['headings'].extend(content['headings'])
                    snapshot['needs_login'] |= content['needs_login']
                except BrowserError as exc:
                    if not changing_document(exc):
                        raise
            snapshot['headings'] = snapshot['headings'][:40]
            return snapshot
        except BrowserError as exc:
            if not changing_document(exc):
                raise
            if attempt:
                return {'url': tab.url, 'title': '', 'text': '', 'headings': [], 'ready_state': 'loading',
                        'needs_login': False, 'links': [], 'images': []}
            logging.getLogger('uvicorn.error').warning('browser: schedule_redirect_retry')


def root():
    path = Path('/browser/users') / str(user_id())
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def vault():
    path = root() / 'sites.enc'
    if not path.exists():
        return {}
    return json.loads(Fernet(os.environ['INTERNAL_SECRET'].encode()).decrypt(path.read_bytes()))


def save_vault(sites):
    encrypted = Fernet(os.environ['INTERNAL_SECRET'].encode()).encrypt(json.dumps(sites).encode())
    temporary = root() / 'sites.tmp'
    temporary.write_bytes(encrypted)
    temporary.chmod(0o600)
    temporary.replace(root() / 'sites.enc')


def get_site(site_id):
    site = vault().get(site_id)
    if not site:
        raise HTTPException(404, 'Личный кабинет не найден')
    return site


def origin(url):
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise HTTPException(400, 'Некорректный адрес или порт сайта') from None
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise HTTPException(400, 'Используйте адрес сайта без логина и пароля в URL')
    if port == 0:
        raise HTTPException(400, 'Некорректный порт сайта')
    host = parts.hostname.lower()
    if ':' in host:
        host = f'[{host}]'
    default_port = 443 if parts.scheme == 'https' else 80
    if port is not None and port != default_port:
        host += f':{port}'
    return f'{parts.scheme}://{host}'


async def public_url(url):
    origin(url)
    host = urlsplit(url).hostname
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise HTTPException(400, 'Не удалось найти адрес сайта') from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise HTTPException(400, 'Браузеру доступны только внешние сайты')


async def guard(route):
    try:
        await public_url(route.request.url)
    except (HTTPException, ValueError):
        await route.abort()
    else:
        await route.continue_()


def capture_downloads(context):
    owner = user_id()

    async def save_download(download, record):
        import files
        token = current_user_id.set(owner)
        try:
            path = await download.path()
            if path is None:
                record.update(status='failed', error='Файл пустой или больше 25 МБ')
                return
            content = await asyncio.to_thread(Path(path).read_bytes)
            if not 0 < len(content) <= files.LIMIT:
                record.update(status='failed', error='Файл пустой или больше 25 МБ')
                return
            info = await asyncio.to_thread(files.save, download.suggested_filename, content)
            record.update(info, status='saved')
        except asyncio.CancelledError:
            record.update(status='cancelled')
            await download.cancel()
            raise
        except HTTPException as exc:
            record.update(status='failed', error=str(exc.detail)[:500])
            logging.getLogger('uvicorn.error').warning('browser download: HTTP %s (%s)', exc.status_code, type(exc).__name__)
        except ValueError as exc:
            record.update(status='failed', error=str(exc)[:500] or 'Некорректный файл загрузки')
            logging.getLogger('uvicorn.error').warning('browser download: %s', type(exc).__name__)
        except Exception as exc:
            record.update(status='failed', error=f'Не удалось сохранить загрузку браузера ({type(exc).__name__})')
            logging.getLogger('uvicorn.error').warning('browser download: %s', type(exc).__name__)
        finally:
            record.pop('task', None)
            try:
                await download.delete()
            except BrowserError:
                pass
            current_user_id.reset(token)

    def started(download):
        record = {'status': 'downloading', 'name': download.suggested_filename}
        entries = browser_downloads.setdefault(owner, [])
        entries.append(record)
        # Не теряем незавершённые загрузки при очистке истории в RAM.
        entries[:] = [entry for entry in entries[:-20] if entry.get('task')] + entries[-20:]
        record['task'] = asyncio.create_task(save_download(download, record))

    context.on('page', lambda tab: tab.on('download', started))
    for tab in context.pages:
        tab.on('download', started)


def download_status():
    return [{key: value for key, value in entry.items() if key != 'task'}
            for entry in browser_downloads.get(user_id(), [])]


async def page():
    owner = user_id()
    context = contexts.get(owner)
    if context is None:
        context = await playwright.chromium.launch_persistent_context(
            str(root() / 'profile'), headless=True, viewport={'width': 1100, 'height': 750},
            accept_downloads=True, service_workers='block',
        )
        cursor_positions = WeakKeyDictionary()

        def cursor_position(source, point=None):
            tab = source['page']
            if point is not None:
                cursor_positions[tab] = point
            return cursor_positions.get(tab, {'x': 0, 'y': 0})

        await context.expose_binding('__uchebaCursorPosition', cursor_position)
        await context.add_init_script(script=CURSOR_SCRIPT)
        capture_downloads(context)
        context.set_default_timeout(8000)
        context.set_default_navigation_timeout(25000)
        await context.route('**/*', guard)
        # WebSocket не должен обходить проверку внешних адресов HTTP.
        await context.route_web_socket('**/*', lambda ws: ws.close())
        contexts[owner] = context
    pages = [item for item in context.pages if not item.is_closed()]
    return pages[-1] if pages else await context.new_page()


async def public_page():
    # Поиск, ЛК и скачивания используют один профиль владельца.
    return await page()


def lock():
    return locks.setdefault(user_id(), asyncio.Lock())


def safe_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


async def screenshot(tab):
    state = screens.get(user_id(), {'tab': tab, 'frame': None})
    loading = (await schedule_snapshot(tab))['ready_state'] == 'loading'
    # Ответ управления не ждёт картинку; SSE продолжает присылать свежие кадры.
    frame = state.get('frame') if loading else await capture_screen(state)
    return {'url': safe_url(tab.url), 'image': frame['image'] if frame else '',
            'mime': frame['mime'] if frame else 'image/jpeg',
            'width': 1100, 'height': 750, 'loading': loading}


def watch_screen(tab):
    owner = user_id()
    state = screens.get(owner)
    if state:
        state['tab'] = tab
    else:
        state = screens[owner] = {'tab': tab, 'frame': None, 'frame_time': 0, 'capture_lock': asyncio.Lock()}
    screens.move_to_end(owner)
    while len(screens) > 32:
        screens.popitem(last=False)


async def capture_screen(state):
    if state is None:
        return None
    tab = state['tab']
    if tab is not None and not tab.is_closed():
        async with state['capture_lock']:
            if asyncio.get_running_loop().time() - state['frame_time'] >= 0.25:
                try:
                    async with asyncio.timeout(3):
                        await tab.evaluate(CURSOR_SCRIPT)
                        image = await tab.screenshot(type='jpeg', quality=65, timeout=1500,
                                                     mask=[tab.locator('input[type="password"]')])
                        state['frame'] = {'image': base64.b64encode(image).decode(), 'mime': 'image/jpeg',
                                          'url': safe_url(tab.url), 'at': datetime.now(timezone.utc).isoformat(),
                                          'width': 1100, 'height': 750,
                                          'cursor': await tab.evaluate('window.__uchebaCursor')}
                        state['frame_time'] = asyncio.get_running_loop().time()
                except (BrowserError, TimeoutError):
                    pass
    return state['frame']


@app.get('/screen')
async def agent_screen():
    state = screens.get(user_id())
    return await capture_screen(state) if state else None


@app.get('/screen/stream')
async def screen_events(request: Request):
    owner = user_id()

    async def frames():
        while not await request.is_disconnected():
            state = screens.get(owner)
            frame = await capture_screen(state) if state else None
            yield 'data: ' + json.dumps(frame, ensure_ascii=False) + '\n\n'
            await asyncio.sleep(0.3)

    return StreamingResponse(frames(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@app.get('/stream')
async def screen_stream(request: Request):
    owner = user_id()

    async def frames():
        while not await request.is_disconnected():
            state = screens.get(owner)
            frame = await capture_screen(state) if state else None
            if frame:
                content = base64.b64decode(frame['image'])
                yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                       str(len(content)).encode() + b'\r\n\r\n' + content + b'\r\n')
            else:
                # Multipart допускает пустой пролог до первого кадра.
                yield b'\r\n'
            await asyncio.sleep(0.3)

    return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


async def visual_frame(tab, full_page=False, image=None, timeout=8000):
    await tab.evaluate(CURSOR_SCRIPT)
    # Парольные поля не передаются модели даже на снимке страницы входа.
    masks = [tab.locator('input[type="password"], input[autocomplete="one-time-code"], '
                         'input[autocomplete="current-password"], input[autocomplete="new-password"]')]
    for site in vault().values():
        if site['password']:
            masks.append(tab.get_by_text(site['password'], exact=False))
    if full_page:
        width, height = await tab.evaluate('[Math.max(document.documentElement.scrollWidth, 1100), Math.max(document.documentElement.scrollHeight, 750)]')
        if height > 30000 or width * height > 33000000:
            raise HTTPException(413, 'Страница слишком большая. Просматривайте её частями через прокрутку.')
    if image is not None:
        bounds = await image.bounding_box()
        if not bounds or bounds['width'] * bounds['height'] > 33000000:
            raise HTTPException(413, 'Изображение слишком большое или скрыто')
        content = await image.screenshot(type='jpeg', quality=85, timeout=timeout)
    else:
        content = await tab.screenshot(type='jpeg', quality=85, full_page=full_page,
                                       mask=masks, timeout=timeout)
    return {'image': base64.b64encode(content).decode(), 'mime': 'image/jpeg',
            'url': safe_url(tab.url), 'full_page': full_page, 'viewport': {'width': 1100, 'height': 750},
            'cursor': await tab.evaluate('window.__uchebaCursor') if image is None else None}


async def action_frame(tab, full_page=False, image=None):
    try:
        snapshot = await schedule_snapshot(tab)
        if snapshot['ready_state'] == 'loading' and image is None:
            return {'url': safe_url(tab.url), 'visual_error': 'Документ ещё загружается; текст доступен без снимка.'}
        return await visual_frame(tab, full_page, image, timeout=1500)
    except BrowserError:
        # Ошибка снимка не должна отменять уже прочитанный текст или скачанный файл.
        return {'url': safe_url(tab.url), 'visual_error': 'Снимок пока не готов. Повторите browser_screenshot.'}


class VisualAction(BaseModel):
    action: Literal['screenshot', 'move', 'click', 'type', 'press', 'scroll', 'open', 'back', 'forward', 'image']
    full_page: bool = False
    x: int = Field(default=0, ge=0, lt=1100)
    y: int = Field(default=0, ge=0, lt=750)
    delta: int = Field(default=500, ge=-1500, le=1500)
    text: SecretStr = SecretStr('')
    url: str = Field(default='', max_length=2000)
    index: int = Field(default=0, ge=0, lt=100)
    target: Literal['coordinates', 'cursor', 'role', 'text', 'selector', 'css', 'xpath', 'element'] = 'coordinates'
    element: str = Field(default='', max_length=16)
    role: str = Field(default='button', max_length=40)
    name: str = Field(default='', max_length=500)
    selector: str = Field(default='', max_length=1000)


async def elements(tab):
    result = []
    for frame_index, frame in enumerate(tab.frames):
        try:
            items = await frame.locator('a, button, input:not([type="hidden"]), textarea, select, [role="button"], [role="link"], [contenteditable="true"]').evaluate_all('''(items, frame) => {
                document.querySelectorAll('[data-ucheba-ref]').forEach(el => el.removeAttribute('data-ucheba-ref'));
                return items.filter(el => el.getClientRects().length).slice(0, 100).map(el => {
                    const id = 'f' + frame + '-' + Math.random().toString(36).slice(2, 10);
                    el.setAttribute('data-ucheba-ref', id);
                    const type = el.type || '';
                    return {id, tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || ({A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox'}[el.tagName]) ||
                            (el.tagName === 'INPUT' ? ({submit:'button',button:'button',reset:'button',checkbox:'checkbox',radio:'radio',number:'spinbutton',range:'slider'}[type] || 'textbox') : ''),
                        name: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '').trim().slice(0, 160)};
                });
            }''', frame_index)
            result.extend(items)
        except BrowserError as exc:
            if not changing_document(exc):
                raise
    result = result[:150]
    return redact_result(result, [site['password'] for site in vault().values() if site['password']])


def redact_result(value, secrets):
    if isinstance(value, dict):
        return {key: bool(item) if re.search(r'password|passwd|cookie|token|secret|authorization|session|storage', key, re.I)
                else redact_result(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_result(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[скрыто]')
    return value


async def click_issue(tab, reason):
    snapshot = await schedule_snapshot(tab)
    result = {'status': 'choose_element', 'reason': reason,
              'notice': 'Не повторяй тот же вызов. Выбери точный element/css/xpath из свежего elements. Если нужного элемента пока нет, перечитай страницу.',
              'text': snapshot['text'], 'ready_state': snapshot['ready_state'],
              'elements': await elements(tab)}
    return redact_result(result, [site['password'] for site in vault().values() if site['password']])


@app.post('/visual')
async def visual(body: VisualAction, request: Request):
    return await tracked_action(request, lambda: run_visual_action(body))


async def run_visual_action(body):
    async with agent_browser_lock():
        state = screens.get(user_id())
        tab = state['tab'] if state else None
        if tab is None or tab.is_closed():
            raise HTTPException(400, 'Сначала откройте страницу через web_read или browser_login')
        await tab.evaluate(CURSOR_SCRIPT)
        before_url = safe_url(tab.url)
        before = await schedule_snapshot(tab) if body.action in ('click', 'type', 'press', 'scroll') else None
        clicked = None
        if body.action == 'move':
            await tab.mouse.move(body.x, body.y)
            await tab.evaluate(CURSOR_SCRIPT)
            await tab.evaluate('''async point => {
                window.__uchebaMoveCursor(point);
                await window.__uchebaCursorPosition(point);
            }''', {'x': body.x, 'y': body.y})
        elif body.action == 'click':
            if body.target in ('coordinates', 'cursor'):
                point = await tab.evaluate('window.__uchebaCursor') if body.target == 'cursor' else {'x': body.x, 'y': body.y}
                x, y = point['x'], point['y']
                clicked = await tab.evaluate('''({x, y}) => {
                    const el = document.elementFromPoint(x, y)?.closest('button,a,input,[role="button"],[role="link"]');
                    return el ? (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || el.tagName).slice(0, 250) : 'Элемент без подписи';
                }''', {'x': x, 'y': y})
                await tab.mouse.click(x, y)
            else:
                if body.target == 'role':
                    role = {'a': 'link', 'textarea': 'textbox', 'select': 'combobox'}.get(body.role, body.role)
                    target = tab.get_by_role(role, name=body.name, exact=True)
                elif body.target == 'text':
                    target = tab.get_by_text(body.name, exact=True)
                    # Надпись может быть вложенным div/span, смещённым внутри кнопки.
                    if await target.count() == 1:
                        control = target.locator('xpath=ancestor-or-self::*[self::button or self::a or @role="button" or @role="link"][1]')
                        if await control.count():
                            target = control
                elif body.target == 'element':
                    if not re.fullmatch(r'f\d+-[a-z0-9]{8}', body.name):
                        raise HTTPException(422, 'Укажите актуальный ID из elements')
                    target = None
                    for frame in tab.frames:
                        candidate = frame.locator(f'[data-ucheba-ref="{body.name}"]')
                        if await candidate.count():
                            target = candidate
                            break
                    if target is None:
                        raise HTTPException(409, 'Ссылка на элемент устарела. Обновите представление страницы и выберите актуальный ID.')
                elif body.target in ('css', 'xpath'):
                    if not body.selector.strip():
                        raise HTTPException(422, 'Укажите selector для CSS или XPath')
                    expression = body.selector.removeprefix(body.target + '=')
                    target = tab.locator(body.target + '=' + expression)
                else:
                    target = tab.locator(body.selector)
                count = await target.count()
                if count == 0:
                    return await click_issue(tab, 'Элемент с такими параметрами не найден. Возможно, перепутаны тег, роль и название либо устарел селектор.')
                if count > 1:
                    return await click_issue(tab, 'Селектор совпал с несколькими элементами. Выбери конкретный ID или селектор.')
                if not await target.is_visible():
                    return await click_issue(tab, 'Найденный элемент скрыт. Выбери видимую кнопку или раскрой нужный раздел.')
                clicked = await target.evaluate('(el) => (el.getAttribute("aria-label") || el.innerText || el.getAttribute("title") || el.tagName).slice(0, 250)')
                # Locator сам прокручивает, ждёт видимость и проверяет перекрытие.
                try:
                    await target.click(no_wait_after=True)
                except BrowserTimeoutError:
                    return await click_issue(tab, 'Не удалось нажать: элемент перекрыт, движется или недоступен. Обнови выбор элемента; не повторяй тот же клик.')
            clicked = redact_result(clicked, [site['password'] for site in vault().values() if site['password']])
        elif body.action == 'type':
            target = None
            if body.element:
                if not re.fullmatch(r'f\d+-[a-z0-9]{8}', body.element):
                    raise HTTPException(422, 'Укажите актуальный ID поля из elements')
                for frame in tab.frames:
                    candidate = frame.locator(f'[data-ucheba-ref="{body.element}"]')
                    if await candidate.count():
                        target = candidate
                        break
                if target is None:
                    raise HTTPException(409, 'Ссылка на поле устарела. Обновите представление страницы и выберите актуальный ID.')
            secret_field = await tab.locator('input[type="password"]:focus, input[autocomplete="one-time-code"]:focus').count()
            if secret_field:
                raise HTTPException(400, 'Для пароля используйте сохранённый вход, для 2FA — ручной браузер')
            if target:
                if await target.get_attribute('type') == 'password' or await target.get_attribute('autocomplete') == 'one-time-code':
                    raise HTTPException(400, 'Для пароля используйте сохранённый вход, для 2FA — ручной браузер')
                await target.fill(body.text.get_secret_value()[:2000])
            else:
                await tab.keyboard.insert_text(body.text.get_secret_value()[:2000])
        elif body.action == 'press':
            key = body.text.get_secret_value()
            if key not in ('Enter', 'Tab', 'Escape', 'Backspace', 'Control+A', 'ArrowDown', 'ArrowUp'):
                raise HTTPException(422, 'Недопустимая клавиша')
            await tab.keyboard.press(key)
        elif body.action == 'scroll':
            await tab.mouse.wheel(0, body.delta)
        elif body.action == 'open' or body.action == 'image' and body.url:
            origin(body.url)
            await tab.goto(body.url, wait_until='domcontentloaded')
        elif body.action == 'back':
            await tab.go_back(wait_until='domcontentloaded')
        elif body.action == 'forward':
            await tab.go_forward(wait_until='domcontentloaded')
        tabs = [item for item in tab.context.pages if not item.is_closed()]
        if tabs and tabs[-1] != tab:
            tab = tabs[-1]
        watch_screen(tab)
        image = None
        if body.action == 'image':
            image = tab.locator('img').nth(body.index)
            if not await image.count():
                raise HTTPException(404, 'Изображение с таким номером не найдено')
        frame = None
        if image is not None:
            source = await image.evaluate('(element) => element.currentSrc || element.src')
            if urlsplit(source).scheme in ('http', 'https'):
                try:
                    original = await download_link(tab, {'url': tab.url}, source, image_mode=True)
                    if original.get('status') == 'image':
                        frame = original
                except HTTPException:
                    pass  # Если оригинал не отдаётся, рассматриваем отрисованное изображение.
        if frame is None and body.action in ('screenshot', 'image', 'move'):
            frame = await action_frame(tab, body.full_page, image)
        snapshot = await schedule_snapshot(tab)
        text = snapshot['text']
        for site in vault().values():
            if site['password']:
                text = text.replace(site['password'], '[скрыто]')
        current_url = safe_url(tab.url)
        result = {'status': 'success', 'action': body.action, 'url': current_url,
                  'navigation': {'from': before_url, 'to': current_url} if before_url != current_url else None,
                  'clicked': clicked, 'downloads': download_status(),
                  'cursor': await tab.evaluate('window.__uchebaCursor')}
        changed = before is None or before_url != current_url or before['title'] != snapshot['title'] or before['text'] != snapshot['text']
        if changed or body.action in ('open', 'back', 'forward', 'screenshot', 'image'):
            result.update({'title': snapshot['title'], 'text': text, 'headings': snapshot['headings'],
                           'images': snapshot['images'], 'ready_state': snapshot['ready_state'],
                           'elements': await elements(tab)})
        else:
            result['page_unchanged'] = True
        return redact_result(result | (frame or {}), [site['password'] for site in vault().values() if site['password']])


@app.get('/sites')
async def sites():
    async with lock():
        return [{'id': key, 'name': value['name'], 'url': safe_url(value['url']),
                 'description': value.get('description', ''), 'group': value.get('group', ''),
                 'color': value.get('color', '#7f49b4'),
                 'has_credentials': bool(value['password'])} for key, value in vault().items()]


class Site(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2000)
    username: SecretStr = Field(max_length=256)
    password: SecretStr = Field(max_length=2000)
    description: str = Field(default='', max_length=300)
    group: str = Field(default='', max_length=80)
    color: str = Field(default='#7f49b4', pattern=r'^#[0-9a-fA-F]{6}$')


@app.post('/sites')
async def add_site(body: Site):
    await public_url(body.url)
    if not body.username.get_secret_value() or not body.password.get_secret_value():
        raise HTTPException(422, 'Укажите логин и пароль личного кабинета')
    async with lock():
        saved = vault()
        site_id = uuid4().hex
        saved[site_id] = {'name': body.name, 'url': body.url, 'username': body.username.get_secret_value(),
                          'password': body.password.get_secret_value(), 'description': body.description,
                          'group': body.group, 'color': body.color}
        save_vault(saved)
    return {'id': site_id, 'name': body.name, 'has_credentials': True}


@app.patch('/sites/{site_id}')
async def edit_site(site_id: str, body: Site):
    await public_url(body.url)
    async with lock():
        saved = vault()
        if site_id not in saved:
            raise HTTPException(404, 'Личный кабинет не найден')
        site = saved[site_id]
        site.update(name=body.name, url=body.url, description=body.description,
                    group=body.group, color=body.color)
        for name in ('username', 'password'):
            value = getattr(body, name).get_secret_value()
            if value:
                site[name] = value
        save_vault(saved)
    return {'id': site_id, 'has_credentials': bool(site['password'])}


class SchedulePage(BaseModel):
    site_id: str = ''
    url: str = Field(default='', max_length=2000)
    index: int | None = Field(default=None, ge=0, lt=100)


def browser_ready():
    owner = user_id()
    if owner not in user_controls:
        user_controls[owner] = asyncio.Event()
        user_controls[owner].set()
    return user_controls[owner]


@asynccontextmanager
async def agent_browser_lock(timeout=BROWSER_ACTION_TIMEOUT):
    # Ручной режим не держит lock: пользователь может нажимать, агент ждёт.
    while True:
        await browser_ready().wait()
        async with lock():
            if browser_ready().is_set():
                if timeout is None:
                    yield
                    return
                try:
                    async with asyncio.timeout(timeout):
                        yield
                        return
                except TimeoutError:
                    logging.getLogger('uvicorn.error').warning('browser: action timed out after %ss', timeout)
                    raise HTTPException(504, 'Действие браузера превысило время ожидания. Перечитайте текущую страницу перед повтором.') from None


@dataclass
class BrowserQueue:
    owner_run: str | None = None
    owner_agent: str = ''
    owner_task_id: int = 0
    last_owner_run: str | None = None
    used_runs: set[str] = field(default_factory=set)
    stale_runs: set[str] = field(default_factory=set)
    waiters: list[dict] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


def browser_queue(owner: int) -> BrowserQueue:
    return browser_queues.setdefault(owner, BrowserQueue())


def _agent_meta(request: Request) -> tuple[str, int]:
    agent = (request.headers.get('x-agent-name') or '').strip()[:40]
    raw_task = (request.headers.get('x-agent-task') or '').strip()
    try:
        task_id = max(0, int(raw_task)) if raw_task else 0
    except ValueError:
        task_id = 0
    return agent, task_id


def _holder_info(q: BrowserQueue) -> dict | None:
    if not q.owner_run:
        return None
    return {'run_id': q.owner_run, 'agent': q.owner_agent, 'task_id': q.owner_task_id}


def _public_waiters(q: BrowserQueue) -> list[dict]:
    return [{'run_id': item['run_id'], 'agent': item['agent'], 'task_id': item['task_id']} for item in q.waiters]


def _prune_waiters(owner: int, q: BrowserQueue) -> bool:
    now = asyncio.get_running_loop().time()
    before = len(q.waiters)
    q.waiters[:] = [
        item for item in q.waiters
        if item['expires_at'] > now and (owner, item['run_id']) not in cancelled_actions
    ]
    return len(q.waiters) != before


def _grant_next(owner: int, q: BrowserQueue) -> str | None:
    _prune_waiters(owner, q)
    if q.owner_run is not None or not q.waiters:
        return None
    next_waiter = q.waiters.pop(0)
    run_id = next_waiter['run_id']
    q.owner_run = run_id
    q.owner_agent = next_waiter['agent']
    q.owner_task_id = next_waiter['task_id']
    if q.last_owner_run != run_id:
        schedule_pages.pop(owner, None)
        if run_id in q.used_runs:
            q.stale_runs.add(run_id)
    q.used_runs.add(run_id)
    return run_id


async def _release_lease(owner: int, q: BrowserQueue, run_id: str, *, end_session: bool = False) -> bool:
    async with q.condition:
        was_owner = q.owner_run == run_id
        q.waiters[:] = [item for item in q.waiters if item['run_id'] != run_id]
        if was_owner:
            q.last_owner_run = run_id
            q.owner_run = None
            q.owner_agent = ''
            q.owner_task_id = 0
        if end_session:
            q.used_runs.discard(run_id)
            q.stale_runs.discard(run_id)
        _grant_next(owner, q)
        q.condition.notify_all()
        return was_owner


async def queue_snapshot(owner: int) -> dict:
    q = browser_queue(owner)
    async with q.condition:
        changed = _prune_waiters(owner, q)
        if _grant_next(owner, q) is not None or changed:
            q.condition.notify_all()
        return {'holder': _holder_info(q), 'waiting': _public_waiters(q)}


async def _acquire_or_wait(owner: int, run_id: str, agent: str, task_id: int, poll_timeout: float, request: Request | None = None) -> dict:
    q = browser_queue(owner)
    async with q.condition:
        if (owner, run_id) in cancelled_actions:
            raise HTTPException(409, 'Действие остановлено')
        if (
            q.owner_run is not None
            and q.owner_run != run_id
            and agent
            and q.owner_agent == agent
            and (agent != 'Monocle' or q.owner_task_id == task_id)
            and (owner, q.owner_run) not in active_actions
        ):
            stale_owner = q.owner_run
            q.last_owner_run = stale_owner
            q.owner_run = None
            q.owner_agent = ''
            q.owner_task_id = 0
            q.used_runs.discard(stale_owner)
            q.stale_runs.discard(stale_owner)
            runs = agent_runs.get(owner)
            if runs:
                runs.discard(stale_owner)
                if not runs:
                    agent_runs.pop(owner, None)
        _grant_next(owner, q)
        if q.owner_run is None:
            if q.last_owner_run != run_id:
                schedule_pages.pop(owner, None)
                if run_id in q.used_runs:
                    q.stale_runs.add(run_id)
            q.owner_run = run_id
            q.owner_agent = agent
            q.owner_task_id = task_id
            q.used_runs.add(run_id)
            q.condition.notify_all()
        elif q.owner_run == run_id:
            if agent:
                q.owner_agent = agent
            if task_id:
                q.owner_task_id = task_id
            q.used_runs.add(run_id)
        if q.owner_run == run_id:
            stale = run_id in q.stale_runs
            q.stale_runs.discard(run_id)
            return {'acquired': True, 'stale': stale, 'holder': _holder_info(q), 'waiting': _public_waiters(q)}
        now = asyncio.get_running_loop().time()
        expires_at = now + max(BROWSER_WAITER_TTL, poll_timeout + 15)
        existing = next((item for item in q.waiters if item['run_id'] == run_id), None)
        if existing is None:
            existing = {'run_id': run_id, 'agent': agent, 'task_id': task_id, 'expires_at': expires_at}
            q.waiters.append(existing)
        else:
            if agent:
                existing['agent'] = agent
            if task_id:
                existing['task_id'] = task_id
            existing['expires_at'] = expires_at
        q.condition.notify_all()
        position = next((i + 1 for i, item in enumerate(q.waiters) if item['run_id'] == run_id), len(q.waiters))
        if poll_timeout <= 0:
            return {'acquired': False, 'position': position, 'holder': _holder_info(q), 'waiting': _public_waiters(q)}
        deadline = now + poll_timeout
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    existing['expires_at'] = asyncio.get_running_loop().time() + BROWSER_WAITER_TTL
                    position = next((i + 1 for i, item in enumerate(q.waiters) if item['run_id'] == run_id), len(q.waiters))
                    return {'acquired': False, 'position': position, 'holder': _holder_info(q), 'waiting': _public_waiters(q)}
                try:
                    await asyncio.wait_for(q.condition.wait(), timeout=min(1.0, remaining))
                except TimeoutError:
                    pass
                if (owner, run_id) in cancelled_actions:
                    q.waiters[:] = [item for item in q.waiters if item['run_id'] != run_id]
                    _grant_next(owner, q)
                    q.condition.notify_all()
                    raise HTTPException(409, 'Действие остановлено')
                _grant_next(owner, q)
                if q.owner_run == run_id:
                    stale = run_id in q.stale_runs
                    q.stale_runs.discard(run_id)
                    q.condition.notify_all()
                    return {'acquired': True, 'stale': stale, 'holder': _holder_info(q), 'waiting': _public_waiters(q)}
                if request is not None and await request.is_disconnected():
                    q.waiters[:] = [item for item in q.waiters if item['run_id'] != run_id]
                    _grant_next(owner, q)
                    q.condition.notify_all()
                    raise HTTPException(409, 'Клиент отключился')
                existing['expires_at'] = asyncio.get_running_loop().time() + BROWSER_WAITER_TTL
        except BaseException:
            q.waiters[:] = [item for item in q.waiters if item['run_id'] != run_id]
            _grant_next(owner, q)
            q.condition.notify_all()
            raise


@app.post('/handoff')
async def browser_handoff(body: dict):
    action = body.get('action', 'status')
    if action == 'pause':
        browser_ready().clear()
        # Дождаться уже начатого действия, прежде чем отдать управление.
        async with lock():
            pass
    elif action == 'resume':
        browser_ready().set()
        owner = user_id()
        if owner in deferred_browser_close:
            async with lock():
                deferred_browser_close.discard(owner)
                await close_browser_locked(owner)
    elif action != 'status':
        if browser_ready().is_set():
            raise HTTPException(409, 'Сначала возьмите управление браузером')
        if action not in ('click', 'type', 'press', 'scroll', 'state'):
            raise HTTPException(400, 'Недопустимое ручное действие')
        return await _run_manual(Action.model_validate({'site_id': '', **body}))
    return {'paused': not browser_ready().is_set()} | await queue_snapshot(user_id())


@app.post('/wait-user')
async def wait_for_browser_user(request: Request):
    async def wait():
        browser_ready().clear()
        try:
            await browser_ready().wait()
        except asyncio.CancelledError:
            browser_ready().set()
            raise
        async with agent_browser_lock():
            tab = await page()
            result = {'status': 'resumed', 'notice': 'Пользователь вернул управление. Проверь актуальную страницу и продолжи с неё.',
                      'page': await read_page(tab, {'url': ''}),
                      'elements': await elements(tab), 'downloads': download_status()}
            return redact_result(result, [site['password'] for site in vault().values() if site['password']])
    return await tracked_action(request, wait)


async def tracked_action(request, operation, require_lease=True):
    run_id = request.headers.get('x-agent-run')
    if not run_id:
        return await operation()
    owner = user_id()
    key = (owner, run_id)
    if key in cancelled_actions:
        raise HTTPException(409, 'Действие остановлено')
    if require_lease:
        agent, task_id = _agent_meta(request)
        while True:
            lease = await _acquire_or_wait(owner, run_id, agent, task_id, BROWSER_ACQUIRE_POLL_SECONDS, request=request)
            if lease['acquired']:
                break
        async with lock():
            agent_runs.setdefault(owner, set()).add(run_id)
    task = asyncio.create_task(operation())
    active_actions[key] = task
    try:
        return await task
    except asyncio.CancelledError:
        raise HTTPException(409, 'Действие остановлено') from None
    finally:
        active_actions.pop(key, None)


@app.post('/cancel')
async def cancel_action(body: dict):
    owner = user_id()
    run_id = body['run_id']
    key = (owner, run_id)
    cancelled_actions[key] = True
    while len(cancelled_actions) > 256:
        cancelled_actions.popitem(last=False)
    q = browser_queue(owner)
    async with q.condition:
        was_owner = q.owner_run == run_id
        had_waiter = any(item['run_id'] == run_id for item in q.waiters)
        if had_waiter:
            q.waiters[:] = [item for item in q.waiters if item['run_id'] != run_id]
        if had_waiter or was_owner:
            q.condition.notify_all()
    task = active_actions.get(key)
    if task:
        if not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if was_owner:
        context = contexts.get(owner)
        if context:
            for tab in context.pages:
                if not tab.is_closed():
                    try:
                        async with asyncio.timeout(5):
                            session = await context.new_cdp_session(tab)
                            try:
                                await session.send('Page.stopLoading')
                            finally:
                                await session.detach()
                    except Exception:
                        pass
    await _release_lease(owner, q, run_id, end_session=True)
    async with lock():
        runs = agent_runs.get(owner)
        if runs:
            runs.discard(run_id)
            if not runs:
                agent_runs.pop(owner, None)
    return {'ok': True}


async def close_browser_locked(owner):
    q = browser_queue(owner)
    if q.owner_run is not None or q.waiters or agent_runs.get(owner) or not browser_ready().is_set():
        return False
    context = contexts.get(owner)
    if context is None:
        return False
    tasks = [entry['task'] for entry in browser_downloads.get(owner, []) if entry.get('task')]
    if tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30)
        except asyncio.TimeoutError:
            logging.getLogger('uvicorn.error').warning('browser download did not finish before session cleanup')
    if q.owner_run is not None or q.waiters or agent_runs.get(owner) or not browser_ready().is_set():
        return False
    context = contexts.pop(owner, None)
    if context is None:
        return False
    screens.pop(owner, None)
    schedule_pages.pop(owner, None)
    try:
        async with asyncio.timeout(BROWSER_CLOSE_TIMEOUT):
            await context.close()
    except Exception as exc:
        logging.getLogger('uvicorn.error').warning('browser context close failed: %s', type(exc).__name__)
    return True


class SessionAcquire(BaseModel):
    timeout: float = Field(default=BROWSER_ACQUIRE_POLL_SECONDS, ge=0, le=BROWSER_ACQUIRE_POLL_SECONDS)


@app.get('/session/status')
async def session_status():
    return await queue_snapshot(user_id())


@app.post('/session/acquire')
async def acquire_agent_session(request: Request, body: SessionAcquire | None = None):
    run_id = request.headers.get('x-agent-run')
    if not run_id:
        raise HTTPException(400, 'Не указан запуск агента')
    agent, task_id = _agent_meta(request)
    timeout = body.timeout if body is not None else BROWSER_ACQUIRE_POLL_SECONDS
    return await _acquire_or_wait(user_id(), run_id, agent, task_id, timeout, request=request)


@app.post('/session/release')
async def release_agent_session(request: Request):
    run_id = request.headers.get('x-agent-run')
    if not run_id:
        raise HTTPException(400, 'Не указан запуск агента')
    owner = user_id()
    await _release_lease(owner, browser_queue(owner), run_id, end_session=False)
    return {'ok': True}


@app.post('/session/start')
async def start_agent_session(request: Request):
    run_id = request.headers.get('x-agent-run')
    if not run_id:
        raise HTTPException(400, 'Не указан запуск агента')
    return {'ok': True}


@app.post('/session/end')
async def end_agent_session(request: Request):
    run_id = request.headers.get('x-agent-run')
    if not run_id:
        raise HTTPException(400, 'Не указан запуск агента')
    owner = user_id()
    q = browser_queue(owner)
    await _release_lease(owner, q, run_id, end_session=True)
    async with lock():
        runs = agent_runs.get(owner)
        if runs:
            runs.discard(run_id)
            if not runs:
                agent_runs.pop(owner, None)
        if not browser_ready().is_set():
            if q.owner_run is None and not q.waiters and not agent_runs.get(owner):
                deferred_browser_close.add(owner)
            closed = False
        elif q.owner_run is not None or q.waiters:
            closed = False
        else:
            closed = await close_browser_locked(owner)
    return {'ok': True, 'closed': closed}


@app.post('/schedule/read')
async def schedule_page(body: SchedulePage, request: Request):
    return await tracked_action(request, lambda: read_schedule_page(body))


async def read_schedule_page(body):
    if body.site_id:
        get_site(body.site_id)
    async with agent_browser_lock():
        tab = await page()
        watch_screen(tab)
        url = body.url
        notice = None
        if body.index is not None:
            # Номер относится к списку, который видел агент, а не к новому DOM.
            previous = schedule_pages.get(user_id())
            if previous and previous['url'] == tab.url and body.index < len(previous['links']):
                url = previous['links'][body.index]['href']
            else:
                url = None
                notice = 'Список ссылок обновлён. Выбери нужную ссылку из этого ответа заново.'
        if url:
            origin(url)
            if not await open_page(tab, url):
                notice = 'Страница ещё загружается. Если данных мало, перечитай её без повторного открытия URL.'
        snapshot = await schedule_snapshot(tab)
        if not notice and not snapshot['text'].strip():
            notice = 'Страница открылась без текста. Возможно, приложение ещё загружает данные; перечитай страницу без повторного перехода.'
        if snapshot['needs_login']:
            return {'status': 'needs_login', 'reason': 'Завершите вход через основной ЛК в этом браузере'}
        text = snapshot['text']
        for site in vault().values():
            if site['password']:
                text = text.replace(site['password'], '[скрыто]')
        links = snapshot['links']
        schedule_pages[user_id()] = {'url': snapshot['url'], 'links': links}
        return {'status': 'page', 'url': safe_url(snapshot['url']), 'title': snapshot['title'], 'text': text,
                'headings': snapshot['headings'],
                'notice': notice,
                'links': [{'index': index, 'text': item['text']} for index, item in enumerate(links)]}



@app.delete('/sites/{site_id}')
async def delete_site(site_id: str):
    q = browser_queue(user_id())
    if q.owner_run is not None or q.waiters:
        raise HTTPException(409, 'Дождитесь завершения работы агентов в браузере')
    async with lock():
        saved = vault()
        saved.pop(site_id, None)
        save_vault(saved)
        context = contexts.pop(user_id(), None)
        screens.pop(user_id(), None)
        if context:
            try:
                async with asyncio.timeout(BROWSER_CLOSE_TIMEOUT):
                    await context.clear_cookies()
                    await context.close()
            except Exception as exc:
                logging.getLogger('uvicorn.error').warning('browser context close failed: %s', type(exc).__name__)
        # Удаляем только профиль браузера: в нём могут оставаться localStorage/токены.
        import shutil
        shutil.rmtree(root() / 'profile', ignore_errors=True)
    return {'ok': True}


async def login(tab, site):
    origin(site['url'])
    await open_page(tab, site['url'])
    # Ждём форму штатным locator ожиданием после DOMContentLoaded.
    try:
        await tab.locator('input[type="password"]:visible, input[name*="login" i]:visible, input[name*="user" i]:visible, input[autocomplete="username"]:visible').first.wait_for(timeout=8000)
    except BrowserTimeoutError:
        pass  # Форма не появилась — возможно, уже выполнен вход или нужен ручной режим.
    for _ in range(2):
        if origin(tab.url) != origin(site['url']):
            return {'status': 'needs_user', 'reason': 'Вход перенаправлен на другой сайт. Завершите его в браузере.'}
        password = tab.locator('input[type="password"]:visible').first
        username = tab.locator('input[autocomplete="username"]:visible, input[type="email"]:visible, input[name*="login" i]:visible, input[name*="user" i]:visible').first
        if await password.count():
            if not await username.count():
                username = tab.locator('input[type="text"]:visible').first
            if await username.count():
                await username.fill(site['username'])
            await password.fill(site['password'])
        elif await username.count() and not _:
            await username.fill(site['username'])
        else:
            break
        submit = tab.get_by_role('button', name=re.compile(r'^(войти|вход|далее|продолжить|sign in|log in|login|next|continue)$', re.I)).first
        if not await submit.count():
            submit = tab.locator('button[type="submit"]:visible, input[type="submit"]:visible').first
        if not await submit.count():
            return {'status': 'needs_user', 'reason': 'Не найдена кнопка входа. Продолжите в браузере.'}
        await submit.click(no_wait_after=True)
        await tab.wait_for_timeout(1000)
    return {'status': 'check_page', 'reason': 'Проверьте страницу: сайт может запросить 2FA или CAPTCHA.'}


async def read_page(tab, site):
    if tab.url == 'about:blank':
        return {'status': 'needs_login', 'reason': 'Используйте browser_login, чтобы открыть личный кабинет.'}
    snapshot = await schedule_snapshot(tab)
    if snapshot['needs_login']:
        return {'status': 'needs_login', 'reason': 'Сайт показывает форму входа.'}
    text = snapshot['text']
    for saved_site in vault().values():
        if saved_site['password']:
            text = text.replace(saved_site['password'], '[скрыто]')
    return {'status': 'page', 'url': safe_url(snapshot['url']), 'text': text,
            'headings': snapshot['headings'],
            'title': snapshot['title'], 'ready_state': snapshot['ready_state'],
            'notice': 'Страница ещё загружается. При нехватке данных перечитай её без перехода.'
            if snapshot['ready_state'] != 'complete' or not text.strip() else '',
            'links': [{'index': index, 'text': item['text']} for index, item in enumerate(snapshot['links'])]}



class Action(BaseModel):
    site_id: str = Field(max_length=32)
    action: str = Field(max_length=20)
    text: SecretStr = SecretStr('')
    x: int = Field(default=0, ge=0, lt=1100)
    y: int = Field(default=0, ge=0, lt=750)
    delta: int = Field(default=500, ge=-1500, le=1500)
    index: int = Field(default=0, ge=0, lt=100)


async def download_link(tab, site, url, image_mode=False):
    max_size = 25 * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            for _ in range(6):
                await public_url(url)
                # Cookie используются только здесь и только для подходящего домена.
                cookies = await tab.context.cookies(url)
                headers = {'Cookie': '; '.join(f"{item['name']}={item['value']}" for item in cookies)}
                if site.get('url') and origin(url) == origin(site['url']):
                    headers['Referer'] = safe_url(tab.url)
                async with client.stream('GET', url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get('location')
                        if not location:
                            raise HTTPException(502, 'Сайт не указал адрес перенаправления файла')
                        url = urljoin(url, location)
                        continue
                    if not response.is_success:
                        raise HTTPException(502, f'ЛК не отдал файл (HTTP {response.status_code})')
                    mime = response.headers.get('content-type', '').split(';')[0].lower()
                    if mime in ('text/html', 'application/xhtml+xml'):
                        return {'status': 'needs_user', 'reason': 'Вместо файла сайт вернул HTML-страницу. Возможно, требуется повторный вход или подтверждение скачивания.'}
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_size:
                            raise HTTPException(413, 'Файл из ЛК больше 25 МБ')
                    header = Message()
                    header['Content-Disposition'] = response.headers.get('content-disposition', '')
                    name = header.get_filename() or unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or 'document'
                    if not Path(name).suffix:
                        name += {'application/pdf': '.pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                                 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
                                 'application/vnd.oasis.opendocument.spreadsheet': '.ods',
                                 'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
                                 'text/plain': '.txt', 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}.get(mime, '')
                    logging.getLogger('uvicorn.error').info('browser: file downloaded (%d bytes)', len(content))
                    if image_mode and mime in ('image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp', 'image/tiff'):
                        return {'status': 'image', 'image': base64.b64encode(content).decode(),
                                'mime': mime, 'url': safe_url(url), 'image_source': 'original'}
                    return {'status': 'file', 'name': name, 'content': base64.b64encode(content).decode(), 'url': safe_url(url)}
    except httpx.HTTPError as exc:
        logging.getLogger('uvicorn.error').error('browser download: %s', type(exc).__name__)
        raise HTTPException(502, 'Не удалось скачать файл из ЛК: ошибка соединения или таймаут') from None
    raise HTTPException(502, 'Слишком много перенаправлений при скачивании файла')


async def _run_manual(body: Action):
    async with lock():
        site = get_site(body.site_id) if body.action in ('open', 'login') else None
        state = screens.get(user_id())
        tab = state['tab'] if state and not state['tab'].is_closed() else await page()
        watch_screen(tab)
        notice = None
        if body.action == 'open':
            origin(site['url'])
            await tab.goto(site['url'], wait_until='domcontentloaded')
        elif body.action == 'login':
            notice = await login(tab, site)
        elif body.action == 'click':
            await tab.mouse.click(body.x, body.y)
        elif body.action == 'type':
            await tab.keyboard.insert_text(body.text.get_secret_value()[:2000])
        elif body.action == 'press':
            key = body.text.get_secret_value()
            if key not in ('Enter', 'Tab', 'Backspace', 'Control+A', 'Escape'):
                raise HTTPException(400, 'Недопустимая клавиша')
            await tab.keyboard.press(key)
        elif body.action == 'scroll':
            await tab.mouse.wheel(0, body.delta)
        elif body.action == 'back':
            await tab.go_back(wait_until='domcontentloaded')
        elif body.action != 'state':
            raise HTTPException(400, 'Неизвестное действие браузера')
        return await screenshot(tab) | {'notice': notice}


@app.post('/manual')
async def manual(body: Action):
    q = browser_queue(user_id())
    if q.owner_run is not None or q.waiters:
        raise HTTPException(409, 'Дождитесь завершения работы агентов в браузере')
    return await _run_manual(body)


@app.post('/agent')
async def agent(body: Action, request: Request):
    return await tracked_action(request, lambda: run_browser_action(body))


async def run_browser_action(body):
    async with agent_browser_lock():
        result = await agent_action(body)
        tab = await page()
        result.update(elements=await elements(tab), downloads=download_status())
        return redact_result(result, [site['password'] for site in vault().values() if site['password']])


async def agent_action(body):
    site = get_site(body.site_id) if body.action == 'login' else {'url': ''}
    tab = await page()
    watch_screen(tab)
    if body.action == 'login':
        return await login(tab, site)
    if body.action in ('follow', 'download'):
        if tab.url == 'about:blank':
            return {'status': 'needs_login', 'reason': 'Сначала войдите в выбранный личный кабинет.'}
        links = await tab.locator('a[href]').evaluate_all('(items) => items.map(a => ({text: a.innerText.slice(0, 150), href: a.href}))')
        links = [item for item in links if item['text'].strip() and urlsplit(item['href']).scheme in ('http', 'https')][:100]
        if body.index >= len(links):
            raise HTTPException(400, 'Ссылка больше не доступна. Перечитайте страницу.')
        url = links[body.index]['href']
        origin(url)
        if body.action == 'download' or Path(urlsplit(url).path).suffix.lower() in ('.pdf', '.docx', '.xlsx', '.pptx', '.txt', '.md', '.csv', '.json', '.zip', '.png', '.jpg', '.jpeg', '.webp'):
            return await download_link(tab, site, url)
        try:
            await tab.goto(url, wait_until='domcontentloaded')
        except BrowserError as exc:
            if 'Download is starting' not in str(exc):
                raise
            return await download_link(tab, site, url)
    elif body.action != 'read':
        raise HTTPException(400, 'Агенту доступны вход, чтение, переход и скачивание по ссылке')
    return await read_page(tab, site)


class WebAction(BaseModel):
    action: str = Field(max_length=10)
    query: str = Field(default='', max_length=500)
    url: str = Field(default='', max_length=2000)


@app.post('/web')
async def web(body: WebAction, request: Request):
    return await tracked_action(request, lambda: run_web_action(body))


async def run_web_action(body):
    async with agent_browser_lock():
        result = await browse_web(body)
        tab = await public_page()
        result['elements'] = await elements(tab)
        return redact_result(result, [site['password'] for site in vault().values() if site['password']])


async def browse_web(body):
    if body.action == 'search':
        if not body.query.strip():
            raise HTTPException(400, 'Укажите поисковый запрос')
        url = 'https://www.google.com/search?' + urlencode({'q': body.query, 'hl': 'ru', 'num': 5})
    elif body.action == 'read':
        url = body.url
    else:
        raise HTTPException(400, 'Неизвестное действие поиска')
    origin(url)
    tab = await public_page()
    watch_screen(tab)
    tab.set_default_timeout(8000)
    response = await tab.goto(url, wait_until='domcontentloaded', timeout=25000)
    if response and response.status >= 400:
        return {'status': 'unavailable', 'reason': f'Сайт вернул HTTP {response.status}'}
    if body.action == 'search':
        consent = tab.get_by_role('button', name=re.compile(r'^(Accept all|Принять все|Alle akzeptieren)$', re.I)).first
        if await consent.count():
            await consent.click(no_wait_after=True)
    snapshot = await schedule_snapshot(tab)
    text = snapshot['text']
    if body.action == 'read':
        return {'status': 'page', 'title': snapshot['title'], 'url': safe_url(snapshot['url']),
                'text': text, 'headings': snapshot['headings'], 'ready_state': snapshot['ready_state'],
                'notice': 'Страница ещё загружается. При нехватке данных перечитай её без перехода.'
                if snapshot['ready_state'] != 'complete' or not text.strip() else ''}
    links = await tab.locator('a:has(h3)').evaluate_all('(items) => items.map(a => ({title: a.innerText.slice(0, 250), url: a.href}))')
    results = []
    for link in links:
        parts = urlsplit(link['url'])
        if parts.path == '/url' and parts.hostname and parts.hostname.endswith('google.com'):
            params = parse_qs(parts.query)
            link['url'] = (params.get('url') or params.get('q') or [''])[0]
        if urlsplit(link['url']).scheme in ('http', 'https'):
            results.append(link)
        if len(results) == 5:
            break
    if not results:
        return {'status': 'unavailable', 'reason': 'Google не вернул доступную выдачу. Возможно, требуется CAPTCHA или изменился формат страницы.'}
    return {'status': 'search', 'results': results, 'text': text}


@app.post('/downloads/migrate')
async def migrate_old_downloads():
    import files
    return {'migrated': await asyncio.to_thread(files.migrate_legacy)}


@app.post('/read-content')
async def read_file_content(body: dict):
    import files
    try:
        content = base64.b64decode(body['content'], validate=True)
        return files.read_content(body['name'], content, body.get('page', 1))
    except (ValueError, KeyError):
        raise HTTPException(422, 'Не удалось прочитать файл. Проверьте формат и страницу') from None


@app.post('/file-preview')
async def preview_file(body: dict):
    import files
    try:
        content = base64.b64decode(body['content'], validate=True)
        result = {'name': body['name'], 'size': len(content)}
        if Path(body['name']).suffix.lower() in ('.xlsx', '.ods', '.csv', '.tsv'):
            from common.office import search_table, table_rows
            selected_sheet = body.get('sheet') or next((row[0] for row in table_rows(body['name'], content)), '')
            result['table'] = await asyncio.to_thread(search_table, body['name'], content,
                {'sheet': selected_sheet, 'offset': body.get('offset', 0), 'limit': 40})
        else:
            result = files.read_content(body['name'], content, body.get('page', 1), include_document=True)
        return result
    except (ValueError, KeyError):
        raise HTTPException(422, 'Не удалось открыть файл. Проверьте формат и номер страницы') from None


@app.post('/files')
async def agent_files(body: dict, request: Request):
    return await tracked_action(request, lambda: run_file_action(body), require_lease=False)


async def get_cookies(target_url: str):
    owner = user_id()
    async with lock():
        context = contexts.get(owner)
        if context is not None:
            return await context.cookies(target_url)
        await page()
        try:
            return await contexts[owner].cookies(target_url)
        finally:
            q = browser_queue(owner)
            if q.owner_run is None and not q.waiters and not agent_runs.get(owner) and browser_ready().is_set():
                await close_browser_locked(owner)


async def run_file_action(body):
    import files
    try:
        action = body['action']
        if action in ('download_file', 'python_http_request'):
            result = await files.request_file(body, get_cookies, public_url, safe_url)
        elif action == 'save_download':
            content = base64.b64decode(body['content'], validate=True)
            if not content or len(content) > files.LIMIT:
                raise ValueError('Размер файла должен быть от 1 байта до 10 МБ')
            result = await asyncio.to_thread(files.save, body['name'], content)
        elif action == 'list_downloads':
            result = {'downloads': download_status(), 'files': await asyncio.to_thread(files.list_files)}
        elif action == 'read_download':
            info, content = await asyncio.to_thread(files.load, body['file_id'])
            result = files.read_content(info['name'], content, body['page'], info)
        elif action in ('search_table', 'inspect_table', 'summarize_table', 'export_table'):
            from common.office import search_table, inspect_table, summarize_table, export_table
            info, content = await asyncio.to_thread(files.load, body['file_id'])
            if action == 'export_table':
                if not body['output_path'].lower().endswith('.csv'):
                    raise ValueError('output_path должен оканчиваться .csv')
                data, count = await asyncio.to_thread(export_table, info['name'], content, body)
                result = {'content': base64.b64encode(data).decode(), 'rows': count}
            else:
                operation = {'search_table': search_table, 'inspect_table': inspect_table, 'summarize_table': summarize_table}[action]
                result = await asyncio.to_thread(operation, info['name'], content, body)
        elif action == 'export_download':
            info, content = await asyncio.to_thread(files.load, body['file_id'])
            result = {'name': info['name'], 'content': base64.b64encode(content).decode()}
        elif action == 'python_binary':
            result = await asyncio.to_thread(files.binary, body)
        else:
            raise ValueError('Неизвестное действие с файлом')
        # Редактируем известные пароли в тексте/результатах, но не в изображении.
        image = result.pop('image', None)
        result = redact_result(result, [site['password'] for site in vault().values() if site['password']])
        if image:
            result['image'] = image
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        logging.getLogger('uvicorn.error').warning('browser files: %s', type(exc).__name__)
        raise HTTPException(422, str(exc)[:500]) from None
    except Exception as exc:
        logging.getLogger('uvicorn.error').warning('browser files: %s', type(exc).__name__)
        raise HTTPException(422, 'Не удалось прочитать файл или выполнить запрос. Проверьте ID, формат, страницу и доступность URL.') from None
