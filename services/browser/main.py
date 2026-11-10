"""Отдельный браузер пользователя. Модель не получает cookie, пароли или shell."""
import asyncio
import base64
from collections import OrderedDict
from contextlib import asynccontextmanager
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

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx
from playwright.async_api import async_playwright, Error as BrowserError
from pydantic import BaseModel, Field, SecretStr
from typing import Literal

from common.http import private_app
from common.identity import user_id

contexts = {}
public_contexts = {}
locks = {}
screens = OrderedDict()
playwright = None


@asynccontextmanager
async def lifespan(app):
    global playwright
    playwright = await async_playwright().start()
    try:
        yield
    finally:
        for context in contexts.values():
            await context.close()
        for context in public_contexts.values():
            await context.browser.close()
        await playwright.stop()


# Используем токен context: браузер является частью этой функции.
app = private_app('context', lifespan)


@app.exception_handler(BrowserError)
async def browser_error(request, exc):
    from fastapi.responses import JSONResponse
    category = 'download_navigation' if 'Download is starting' in str(exc) else type(exc).__name__
    logging.getLogger('uvicorn.error').error('browser: %s', category)
    return JSONResponse(status_code=502, content={'detail': 'Браузер не выполнил действие. Обновите снимок страницы; при необходимости завершите вход вручную.'})


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


async def page():
    owner = user_id()
    context = contexts.get(owner)
    if context is None:
        context = await playwright.chromium.launch_persistent_context(
            str(root() / 'profile'), headless=True, viewport={'width': 1100, 'height': 750},
            accept_downloads=False, service_workers='block',
        )
        context.set_default_timeout(8000)
        context.set_default_navigation_timeout(25000)
        await context.route('**/*', guard)
        # WebSocket не должен обходить проверку внешних адресов HTTP.
        await context.route_web_socket('**/*', lambda ws: ws.close())
        contexts[owner] = context
    pages = [item for item in context.pages if not item.is_closed()]
    return pages[-1] if pages else await context.new_page()


async def public_page():
    owner = user_id()
    context = public_contexts.get(owner)
    if context is None:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={'width': 1100, 'height': 750},
                                            service_workers='block', accept_downloads=False)
        await context.route('**/*', guard)
        await context.route_web_socket('**/*', lambda ws: ws.close())
        context.set_default_timeout(8000)
        context.set_default_navigation_timeout(25000)
        public_contexts[owner] = context
    pages = [tab for tab in context.pages if not tab.is_closed()]
    return pages[-1] if pages else await context.new_page()


def lock():
    return locks.setdefault(user_id(), asyncio.Lock())


def safe_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


async def screenshot(tab):
    return {'url': safe_url(tab.url), 'image': base64.b64encode(await tab.screenshot(type='png')).decode(),
            'width': 1100, 'height': 750}


def watch_screen(tab):
    owner = user_id()
    screens[owner] = {'tab': tab, 'frame': screens.get(owner, {}).get('frame')}
    screens.move_to_end(owner)
    while len(screens) > 32:
        screens.popitem(last=False)


async def capture_screen(state):
    tab = state['tab']
    if tab is not None and not tab.is_closed():
        try:
            image = await tab.screenshot(type='jpeg', quality=65, timeout=1500,
                                         mask=[tab.locator('input[type="password"]')])
            state['frame'] = {'image': base64.b64encode(image).decode(), 'mime': 'image/jpeg',
                              'url': safe_url(tab.url), 'at': datetime.now(timezone.utc).isoformat()}
        except BrowserError:
            # Во время перехода временно показываем последний удачный снимок.
            pass
    return state['frame']


@app.get('/screen')
async def agent_screen():
    state = screens.get(user_id())
    return await capture_screen(state) if state else None


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


async def visual_frame(tab, full_page=False, image=None):
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
        content = await image.screenshot(type='jpeg', quality=85, timeout=8000)
    else:
        content = await tab.screenshot(type='jpeg', quality=85, full_page=full_page,
                                       mask=masks, timeout=8000)
    return {'image': base64.b64encode(content).decode(), 'mime': 'image/jpeg',
            'url': safe_url(tab.url), 'full_page': full_page, 'viewport': {'width': 1100, 'height': 750}}


async def action_frame(tab, full_page=False, image=None):
    try:
        return await visual_frame(tab, full_page, image)
    except BrowserError:
        # Ошибка снимка не должна отменять уже прочитанный текст или скачанный файл.
        return {'url': safe_url(tab.url), 'visual_error': 'Снимок пока не готов. Повторите browser_screenshot.'}


class VisualAction(BaseModel):
    action: Literal['screenshot', 'click', 'type', 'press', 'scroll', 'open', 'back', 'forward', 'image', 'javascript']
    full_page: bool = False
    x: int = Field(default=0, ge=0, lt=1100)
    y: int = Field(default=0, ge=0, lt=750)
    delta: int = Field(default=500, ge=-1500, le=1500)
    text: SecretStr = SecretStr('')
    url: str = Field(default='', max_length=2000)
    index: int = Field(default=0, ge=0, lt=100)
    target: Literal['coordinates', 'role', 'text', 'selector', 'element'] = 'coordinates'
    role: str = Field(default='button', max_length=40)
    name: str = Field(default='', max_length=500)
    selector: str = Field(default='', max_length=1000)
    script: SecretStr = SecretStr('')


async def elements(tab):
    result = await tab.locator('a, button, input:not([type="hidden"]), textarea, select, [role="button"], [role="link"]').evaluate_all('''items => {
        document.querySelectorAll('[data-luna-element]').forEach(el => el.removeAttribute('data-luna-element'));
        return items.filter(el => el.getClientRects().length).slice(0, 150).map((el, index) => {
            const id = String(index);
            el.setAttribute('data-luna-element', id);
            return {id, role: el.getAttribute('role') || el.tagName.toLowerCase(),
                name: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') || '').slice(0, 250)};
        });
    }''')
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


async def javascript(tab, script):
    if not script or len(script) > 20000:
        raise HTTPException(422, 'JavaScript должен содержать от 1 до 20000 символов')
    secrets = {site['password'] for site in vault().values() if site['password']}
    secrets.update(cookie['value'] for cookie in await tab.context.cookies() if cookie['value'])
    try:
        value = await tab.evaluate(script)
    except BrowserError:
        return {'error': 'JavaScript завершился ошибкой. Проверь код; подробности с данными страницы скрыты.'}
    secrets.update(cookie['value'] for cookie in await tab.context.cookies() if cookie['value'])
    value = redact_result(value, sorted(secrets, key=len, reverse=True))
    text = json.dumps(value, ensure_ascii=False, default=str)
    return {'value': value} if len(text) <= 12000 else {'preview': text[:12000], 'truncated': True}


@app.post('/visual')
async def visual(body: VisualAction):
    async with lock():
        state = screens.get(user_id())
        tab = state['tab'] if state else None
        if tab is None or tab.is_closed():
            raise HTTPException(400, 'Сначала откройте страницу через web_read или browser_login')
        clicked = None
        script_result = None
        if body.action == 'click':
            if body.target == 'coordinates':
                clicked = await tab.evaluate('''({x, y}) => {
                    const el = document.elementFromPoint(x, y)?.closest('button,a,input,[role="button"],[role="link"]');
                    return el ? (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || el.tagName).slice(0, 250) : 'Элемент без подписи';
                }''', {'x': body.x, 'y': body.y})
                await tab.mouse.click(body.x, body.y)
            else:
                if body.target == 'role':
                    target = tab.get_by_role(body.role, name=body.name, exact=True)
                elif body.target == 'text':
                    target = tab.get_by_text(body.name, exact=True)
                elif body.target == 'element':
                    if not body.name.isdecimal():
                        raise HTTPException(422, 'Укажите id из последнего списка elements')
                    target = tab.locator(f'[data-luna-element="{body.name}"]')
                else:
                    target = tab.locator(body.selector)
                if await target.count() > 1:
                    raise HTTPException(409, 'Найдено несколько элементов. Уточни selector или id из elements')
                clicked = await target.evaluate('(el) => (el.getAttribute("aria-label") || el.innerText || el.getAttribute("title") || el.tagName).slice(0, 250)')
                # Locator сам прокручивает, ждёт видимость и проверяет перекрытие.
                await target.click()
            clicked = redact_result(clicked, [site['password'] for site in vault().values() if site['password']])
        elif body.action == 'javascript':
            script_result = await javascript(tab, body.script.get_secret_value())
        elif body.action == 'type':
            secret_field = await tab.locator('input[type="password"]:focus, input[autocomplete="one-time-code"]:focus').count()
            if secret_field:
                raise HTTPException(400, 'Для пароля используйте сохранённый вход, для 2FA — ручной браузер')
            await tab.keyboard.insert_text(body.text.get_secret_value()[:2000])
        elif body.action == 'press':
            key = body.text.get_secret_value()
            if key not in ('Enter', 'Tab', 'Escape', 'Backspace', 'Control+A', 'ArrowDown', 'ArrowUp'):
                raise HTTPException(422, 'Недопустимая клавиша')
            await tab.keyboard.press(key)
        elif body.action == 'scroll':
            await tab.mouse.wheel(0, body.delta)
        elif body.action == 'open' or body.action == 'image' and body.url:
            await public_url(body.url)
            await tab.goto(body.url, wait_until='domcontentloaded')
        elif body.action == 'back':
            await tab.go_back(wait_until='domcontentloaded')
        elif body.action == 'forward':
            await tab.go_forward(wait_until='domcontentloaded')
        if body.action in ('click', 'press', 'scroll'):
            await tab.wait_for_timeout(300)
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
        if frame is None:
            frame = await action_frame(tab, body.full_page, image)
        text = (await tab.locator('body').inner_text())[:40000]
        for site in vault().values():
            if site['password']:
                text = text.replace(site['password'], '[скрыто]')
        images = await tab.locator('img').evaluate_all('(items) => items.slice(0, 100).map((image, index) => ({index, alt: image.alt}))')
        return frame | {'status': 'page', 'title': await tab.title(), 'text': text, 'images': images,
                        'elements': await elements(tab), 'clicked': clicked, 'javascript_result': script_result}


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
    site_id: str
    url: str = Field(default='', max_length=2000)
    index: int | None = Field(default=None, ge=0, lt=100)


@app.post('/schedule/read')
async def schedule_page(body: SchedulePage):
    get_site(body.site_id)
    async with lock():
        tab = await page()
        watch_screen(tab)
        url = body.url
        if body.index is not None:
            links = await schedule_links(tab)
            if body.index >= len(links):
                raise HTTPException(400, 'Ссылка изменилась. Перечитайте страницу расписания')
            url = links[body.index]['href']
        await public_url(url)
        await tab.goto(url, wait_until='domcontentloaded')
        if await tab.locator('input[type="password"]:visible').count():
            return {'status': 'needs_login', 'reason': 'Завершите вход через основной ЛК в этом браузере'}
        text = (await tab.locator('body').inner_text())[:40000]
        for site in vault().values():
            if site['password']:
                text = text.replace(site['password'], '[скрыто]')
        links = await schedule_links(tab)
        return {'status': 'page', 'url': safe_url(tab.url), 'title': await tab.title(), 'text': text,
                'links': [{'index': index, 'text': item['text']} for index, item in enumerate(links)]}


async def schedule_links(tab):
    links = await tab.locator('a[href]').evaluate_all(
        '(items) => items.map(a => ({text: a.innerText.slice(0,150), href: a.href}))')
    return [item for item in links if item['text'].strip()
            and urlsplit(item['href']).scheme in ('http', 'https')][:100]


@app.delete('/sites/{site_id}')
async def delete_site(site_id: str):
    async with lock():
        saved = vault()
        saved.pop(site_id, None)
        save_vault(saved)
        context = contexts.pop(user_id(), None)
        screens.pop(user_id(), None)
        if context:
            await context.clear_cookies()
            await context.close()
        # Удаляем только профиль браузера: в нём могут оставаться localStorage/токены.
        import shutil
        shutil.rmtree(root() / 'profile', ignore_errors=True)
    return {'ok': True}


async def login(tab, site):
    await public_url(site['url'])
    await tab.goto(site['url'], wait_until='domcontentloaded')
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
        await submit.click()
        await tab.wait_for_timeout(1000)
    return {'status': 'check_page', 'reason': 'Проверьте страницу: сайт может запросить 2FA или CAPTCHA.'}


async def read_page(tab, site):
    if tab.url == 'about:blank':
        return {'status': 'needs_login', 'reason': 'Используйте browser_login, чтобы открыть личный кабинет.'}
    if await tab.locator('input[type="password"]:visible').count():
        return {'status': 'needs_login', 'reason': 'Сайт показывает форму входа.'}
    # inner_text не возвращает значения полей ввода, cookie или storage.
    text = (await tab.locator('body').inner_text())[:40000]
    text = text.replace(site['password'], '[скрыто]')
    links = await tab.locator('a[href]').evaluate_all('(items) => items.map(a => ({text: a.innerText.slice(0, 150), href: a.href}))')
    links = [item for item in links if item['text'].strip() and urlsplit(item['href']).scheme in ('http', 'https')][:100]
    return {'status': 'page', 'url': safe_url(tab.url), 'text': text,
            'title': await tab.title(),
            'links': [{'index': index, 'text': item['text']} for index, item in enumerate(links)]}


class Action(BaseModel):
    site_id: str = Field(max_length=32)
    action: str = Field(max_length=20)
    text: SecretStr = SecretStr('')
    x: int = Field(default=0, ge=0, lt=1100)
    y: int = Field(default=0, ge=0, lt=750)
    delta: int = Field(default=500, ge=-1500, le=1500)
    index: int = Field(default=0, ge=0, lt=100)


async def download_link(tab, site, url, image_mode=False):
    max_size = 10 * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            for _ in range(6):
                await public_url(url)
                # Cookie используются только здесь и только для подходящего домена.
                cookies = await tab.context.cookies(url)
                headers = {'Cookie': '; '.join(f"{item['name']}={item['value']}" for item in cookies)}
                if origin(url) == origin(site['url']):
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
                            raise HTTPException(413, 'Файл из ЛК больше 10 МБ')
                    header = Message()
                    header['Content-Disposition'] = response.headers.get('content-disposition', '')
                    name = header.get_filename() or unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or 'document'
                    if not Path(name).suffix:
                        name += {'application/pdf': '.pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
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


@app.post('/manual')
async def manual(body: Action):
    async with lock():
        site = get_site(body.site_id)
        tab = await page()
        notice = None
        if body.action == 'open':
            await public_url(site['url'])
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


@app.post('/agent')
async def agent(body: Action):
    async with lock():
        result = await agent_action(body)
        tab = await page()
        return result | await action_frame(tab) | {'elements': await elements(tab)}


async def agent_action(body):
    site = get_site(body.site_id)
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
        await public_url(url)
        if body.action == 'download' or Path(urlsplit(url).path).suffix.lower() in ('.pdf', '.docx', '.txt', '.md', '.csv', '.png', '.jpg', '.jpeg', '.webp'):
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
async def web(body: WebAction):
    async with lock():
        result = await browse_web(body)
        tab = await public_page()
        return result | await action_frame(tab) | {'elements': await elements(tab)}


async def browse_web(body):
    if body.action == 'search':
        if not body.query.strip():
            raise HTTPException(400, 'Укажите поисковый запрос')
        url = 'https://www.google.com/search?' + urlencode({'q': body.query, 'hl': 'ru', 'num': 5})
    elif body.action == 'read':
        url = body.url
    else:
        raise HTTPException(400, 'Неизвестное действие поиска')
    await public_url(url)
    tab = await public_page()
    watch_screen(tab)
    tab.set_default_timeout(8000)
    response = await tab.goto(url, wait_until='domcontentloaded', timeout=25000)
    if response and response.status >= 400:
        return {'status': 'unavailable', 'reason': f'Сайт вернул HTTP {response.status}'}
    if body.action == 'search':
        consent = tab.get_by_role('button', name=re.compile(r'^(Accept all|Принять все|Alle akzeptieren)$', re.I)).first
        if await consent.count():
            await consent.click()
            await tab.wait_for_load_state('domcontentloaded')
    text = (await tab.locator('body').inner_text())[:40000]
    if body.action == 'read':
        return {'status': 'page', 'title': await tab.title(), 'url': tab.url, 'text': text}
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
