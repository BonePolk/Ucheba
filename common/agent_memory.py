"""Одни и те же инструменты памяти доступны всем трём агентам."""
import httpx

from common.identity import user_id
from common.rpc import internal_token

MEMORY_INSTRUCTION = ('user_memory — общие сохранённые факты о владельце от всех агентов. '
    'Используй их, но свежие слова владельца важнее старой памяти. '
    'Через remember_user_fact сохраняй только прямо сообщённые или подтверждённые устойчивые '
    'факты и предпочтения о владельце: группа, вуз, цели, расписание, особенности работы. '
    'Не сохраняй догадки, временные ошибки инструментов, пароли, cookie, токены и API-ключи. '
    'Сначала проверь существующие ключи, обновляй факт с тем же ключом без дублей. '
    'Факты видны и редактируются владельцем в профиле. ')
MEMORY_TOOLS = [
    {'type': 'function', 'name': 'read_user_memory', 'description': 'Прочитать общую память всех агентов о владельце.',
     'strict': True, 'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}},
    {'type': 'function', 'name': 'remember_user_fact', 'description': 'Запомнить подтверждённый факт о владельце или обновить существующий. key — короткий постоянный ключ латиницей (например university_group). text — сам факт по-русски.',
     'strict': True, 'parameters': {'type': 'object', 'properties': {'key': {'type': 'string', 'pattern': '^[a-zA-Z0-9_.-]+$', 'maxLength': 100},
                    'text': {'type': 'string', 'minLength': 1, 'maxLength': 1500}}, 'required': ['key', 'text'], 'additionalProperties': False}},
]
MEMORY_NAMES = {tool['name'] for tool in MEMORY_TOOLS}


async def memory_request(method='GET', body=None):
    async with httpx.AsyncClient(timeout=15, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
        response = await client.request(method, 'http://backend:8000/api/auth/agent-memory/internal', json=body,
            headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())})
        response.raise_for_status()
        return response.json()


async def memory_tool(name, arguments, agent):
    return await memory_request('POST', arguments | {'agent': agent}) if name == 'remember_user_fact' else await memory_request()
