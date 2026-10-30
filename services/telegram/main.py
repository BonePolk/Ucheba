import asyncio
from contextlib import asynccontextmanager

from fastapi.responses import JSONResponse
from telethon.errors import RPCError

from common.http import private_app
from common.settings import read_settings, report
import client
import pipeline


async def monitor():
    next_analysis = 0
    while True:
        try:
            config, secret = read_settings('telegram')
            gemini, _ = read_settings('gemini')
            now = asyncio.get_running_loop().time()
            if secret.get('session') and not client.connected and now >= next_analysis:
                next_analysis = now + 60
                await client.get_client()
                if not client.connected:
                    report('telegram', error='Авторизация Telegram истекла. Подключите аккаунт заново.')
            if config['enabled'] and gemini['enabled'] and client.connected and now >= next_analysis:
                next_analysis = now + gemini['interval_seconds']
                await pipeline.process_messages()
        except Exception:
            next_analysis = asyncio.get_running_loop().time() + 60
            report('telegram', error='Проверьте авторизацию Telegram, Gemini и соединение')
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    background = asyncio.create_task(monitor())
    try:
        yield
    finally:
        background.cancel()
        await asyncio.gather(background, return_exceptions=True)
        await client.close()


app = private_app('telegram', lifespan)


@app.exception_handler(RPCError)
async def telegram_error(request, exc):
    message = client.error_message(exc)
    report('telegram', error=message)
    return JSONResponse(status_code=502, content={'detail': message})


@app.get('/status')
async def status():
    return {'connected': client.connected, 'analyzing': pipeline.analysis_lock.locked()}


@app.post('/code')
async def code():
    return await client.send_code()


@app.post('/login')
async def login(body: dict):
    return await client.sign_in(**body)


@app.post('/close')
async def close():
    async with pipeline.analysis_lock:
        await client.close()
    return {'ok': True}


@app.post('/disconnect')
async def disconnect():
    async with pipeline.analysis_lock:
        await client.disconnect()
    return {'ok': True}


@app.get('/chats')
async def chats():
    return await client.dialogs()


@app.post('/chats')
async def select_chats(body: dict):
    await pipeline.select_chats(body['chat_ids'])
    return {'ok': True}


@app.post('/analyze')
async def analyze():
    return await pipeline.process_messages(manual=True)
