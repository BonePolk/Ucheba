from common.rpc import call
from common.settings import read_settings
from common.models.integration import Analysis


async def analyze(messages, context, existing, chat_title, owner):
    config, secrets = read_settings('gemini')
    result = await call('gemini', '/analyze', {'config': config, 'secrets': secrets,
        'messages': messages, 'context': context, 'existing': existing,
        'chat_title': chat_title, 'owner': owner}, timeout=90)
    return Analysis.model_validate(result)


async def models():
    _, secrets = read_settings('gemini')
    return await call('gemini', '/models', {'secrets': secrets})


