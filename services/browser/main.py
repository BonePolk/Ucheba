"""Отдельный браузер пользователя. Модель не получает cookie, пароли или shell."""
import asyncio
import base64
from contextlib import asynccontextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
from playwright.async_api import async_playwright, Error as BrowserError
from pydantic import BaseModel, Field, SecretStr

from common.http import private_app
from common.identity import user_id

contexts = {}
locks = {}
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
        await playwright.stop()


# Используем токен context: браузер является частью этой функции.
app = private_app('context', lifespan)


@app.exception_handler(BrowserError)
async def browser_error(request, exc):
    from fastapi.responses import JSONResponse
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
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise HTTPException(400, 'Используйте адрес сайта без логина и пароля в URL')
    if parts.port not in (None, 80, 443):
        raise HTTPException(400, 'Поддерживаются обычные HTTP/HTTPS сайты')
    host = parts.hostname.lower()
    if ':' in host:
        host = f'[{host}]'
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


def lock():
    return locks.setdefault(user_id(), asyncio.Lock())


def safe_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


async def screenshot(tab):
    return {'url': safe_url(tab.url), 'image': base64.b64encode(await tab.screenshot(type='png')).decode(),
            'width': 1100, 'height': 750}


@app.get('/sites')
async def sites():
    async with lock():
        return [{'id': key, 'name': value['name'], 'url': safe_url(value['url']),
                 'has_credentials': bool(value['password'])} for key, value in vault().items()]


class Site(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=2000)
    username: SecretStr = Field(max_length=256)
    password: SecretStr = Field(max_length=2000)


@app.post('/sites')
async def add_site(body: Site):
    await public_url(body.url)
    if urlsplit(body.url).scheme != 'https':
        raise HTTPException(400, 'Для сохранения пароля используйте HTTPS')
    if not body.username.get_secret_value() or not body.password.get_secret_value():
        raise HTTPException(422, 'Укажите логин и пароль личного кабинета')
    async with lock():
        saved = vault()
        site_id = uuid4().hex
        saved[site_id] = {'name': body.name, 'url': body.url, 'username': body.username.get_secret_value(),
                          'password': body.password.get_secret_value()}
        save_vault(saved)
    return {'id': site_id, 'name': body.name, 'has_credentials': True}


@app.delete('/sites/{site_id}')
async def delete_site(site_id: str):
    async with lock():
        saved = vault()
        saved.pop(site_id, None)
        save_vault(saved)
        context = contexts.pop(user_id(), None)
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
    if origin(tab.url) != origin(site['url']):
        return {'status': 'needs_user', 'reason': 'Откройте выбранный личный кабинет или завершите вход.'}
    if await tab.locator('input[type="password"]:visible').count():
        return {'status': 'needs_login', 'reason': 'Сайт показывает форму входа.'}
    # inner_text не возвращает значения полей ввода, cookie или storage.
    text = (await tab.locator('body').inner_text())[:40000]
    text = text.replace(site['password'], '[скрыто]')
    links = await tab.locator('a[href]').evaluate_all('(items) => items.map(a => ({text: a.innerText.slice(0, 150), href: a.href}))')
    links = [item for item in links if item['text'].strip() and item['href'].startswith(origin(site['url']) + '/')][:100]
    return {'status': 'page', 'url': safe_url(tab.url), 'text': text,
            'links': [{'index': index, 'text': item['text']} for index, item in enumerate(links)]}


class Action(BaseModel):
    site_id: str = Field(max_length=32)
    action: str = Field(max_length=20)
    text: SecretStr = SecretStr('')
    x: int = Field(default=0, ge=0, lt=1100)
    y: int = Field(default=0, ge=0, lt=750)
    delta: int = Field(default=500, ge=-1500, le=1500)
    index: int = Field(default=0, ge=0, lt=100)


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
        site = get_site(body.site_id)
        tab = await page()
        if body.action == 'login':
            return await login(tab, site)
        if body.action == 'follow':
            if tab.url == 'about:blank' or origin(tab.url) != origin(site['url']):
                return {'status': 'needs_login', 'reason': 'Сначала войдите в выбранный личный кабинет.'}
            links = await tab.locator('a[href]').evaluate_all('(items) => items.map(a => ({text: a.innerText.slice(0, 150), href: a.href}))')
            links = [item for item in links if item['text'].strip() and item['href'].startswith(origin(site['url']) + '/')][:100]
            if body.index >= len(links):
                raise HTTPException(400, 'Ссылка больше не доступна. Перечитайте страницу.')
            await public_url(links[body.index]['href'])
            await tab.goto(links[body.index]['href'], wait_until='domcontentloaded')
        elif body.action != 'read':
            raise HTTPException(400, 'Агенту доступны вход, чтение и переход по ссылке')
        return await read_page(tab, site)
