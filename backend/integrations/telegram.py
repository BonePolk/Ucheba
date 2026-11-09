from common.rpc import call


async def send_code():
    return await call('telegram', '/code')


async def sign_in(code=None, password=None):
    return await call('telegram', '/login', {'code': code, 'password': password})


async def dialogs():
    return await call('telegram', '/chats', method='GET')


async def close():
    return await call('telegram', '/close')


async def disconnect():
    return await call('telegram', '/disconnect')


async def status():
    return await call('telegram', '/status', method='GET')
