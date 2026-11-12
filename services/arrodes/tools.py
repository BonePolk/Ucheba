"""Run-scoped MCP adapter over the existing Ucheba tools, in the trusted process."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import socket
import struct
import tempfile
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from common.agent_history import history_request
from common.agent_memory import MEMORY_TOOLS, MEMORY_NAMES, memory_tool
from common.agent_questions import QUESTION_TOOL, ask_user
from common.browser_client import browser_request, browser_run_id
from common.browser_tools import BROWSER_TOOLS, BROWSER_NAMES, run_browser_tool, visual_tool
from common.file_tools import FILE_TOOLS, FILE_NAMES, run_file_tool
from common.identity import current_user_id, user_id
from common.library_client import library_request
from common.rpc import internal_token

TOOLS = BROWSER_TOOLS + FILE_TOOLS + MEMORY_TOOLS + [QUESTION_TOOL,
    visual_tool('list_tasks', 'Список и поиск всех своих задач по названию, описанию, предмету, заметкам и контексту Monocle. query пустой — все; status пустой — любой. Архив включён. Результат постраничный.',
                {'query': {'type': 'string'}, 'status': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}}),
    visual_tool('list_task_files', 'Вложения конкретной своей задачи без ограничения общего поиска.', {'task_id': {'type': 'integer', 'minimum': 1}}),
    visual_tool('read_task', 'Прочитать полное описание, метаданные и контекст любой своей задачи. Вложения доступны через list_saved_files/read_file.', {'task_id': {'type': 'integer', 'minimum': 1}}),
    visual_tool('read_agent_history', 'Прочитать ещё 20 записей общей истории всех агентов. before=0 — последние; далее наименьший id предыдущей страницы.', {'before': {'type': 'integer', 'minimum': 0}}),
    visual_tool('create_silo_folder', 'Создать личную папку в Silo.', {'path': {'type': 'string'}}),
    visual_tool('import_silo_file', 'Скачать оригинал Silo или вложение @tasks/<task_id>/<file_id> в рабочую директорию для вычислений. filename — новое имя без папок.', {'path': {'type': 'string'}, 'filename': {'type': 'string'}}),
    visual_tool('publish_file', 'Сохранить готовый локальный файл в Silo. local_path относительно рабочей директории. version пустая для нового файла, для замены — версия Silo. Возвращает ссылку, добавляемую в итог задачи.', {'local_path': {'type': 'string'}, 'path': {'type': 'string'}, 'version': {'type': 'string'}}),
]
SCHEMAS = {tool['name']: tool for tool in TOOLS}


async def tasks_request(arguments):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post('http://backend:8000/api/auth/arrodes/tasks/internal', json=arguments,
            headers={'X-Service-Token': internal_token('arrodes'), 'X-User-ID': str(user_id())})
        response.raise_for_status()
        return response.json()


def open_local(root, name, flags):
    """Open beneath workspace without following any agent-controlled symlinks."""
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Укажите относительный путь внутри рабочей директории')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    finally:
        os.close(directory)


class Tools:
    def __init__(self, path, progress):
        self.path, self.progress = path, progress
        self.elements = {}
        self.files = {}
        self.turn = None
        self.lock = asyncio.Lock()

    async def call(self, name, args):
        if name not in SCHEMAS:
            raise ValueError('Неизвестный инструмент')
        if name == 'ask_user':
            return await ask_user(json.dumps(args, ensure_ascii=False), self.progress), []
        if name == 'delete_silo_file':
            answer = await ask_user(json.dumps({'question': 'Удалить без восстановления файл Silo «' + args['path'] + '»?', 'options': ['Удалить', 'Отмена']}), self.progress)
            if answer['answer'] != 'Удалить':
                return {'status': 'cancelled'}, []
        if name in BROWSER_NAMES:
            result = await run_browser_tool(name, args, browser_request, self.progress, self.elements)
            if not isinstance(result, dict):
                return result, []
            if result.get('status') == 'file' and result.get('content'):
                result = await browser_request('/files', {'action': 'save_download', 'name': result['name'], 'content': result['content']})
            for element in result.get('elements', []):
                self.elements[str(element.get('id'))] = element.get('name', '')
            # Browser images are content blocks, never base64 text in model context.
            content = []
            image = result.pop('image', None)
            if image:
                content.append({'type': 'input_image', 'image_url': 'data:' + result.get('mime', 'image/jpeg') + ';base64,' + image})
            return result, content
        if name in FILE_NAMES:
            return await run_file_tool(name, args, browser_request, self.progress)
        if name in MEMORY_NAMES:
            return await memory_tool(name, args, 'Arrodes'), []
        self.progress.add(SCHEMAS[name]['description'].split('.')[0])
        if name == 'list_task_files':
            await tasks_request({'task_id': args['task_id']})
            return (await library_request(name, args))['output'], []
        if name in ('list_tasks', 'read_task'):
            result = await tasks_request(args)
            if name == 'read_task':
                result['attachments'] = (await library_request('list_task_files', args))['output']
            return result, []
        if name == 'read_agent_history':
            return await history_request('GET', before=args['before']), []
        if name == 'create_silo_folder':
            return (await library_request(name, args))['output'], []
        if name == 'import_silo_file':
            result = await library_request('read_silo_file', {'path': args['path']})
            data = base64.b64decode(result['file']['content'], validate=True)
            if '/' in args['filename'] or len(data) > 10 * 1024 * 1024:
                raise ValueError('Имя без папок; размер до 10 МБ')
            fd = open_local(self.path, args['filename'], os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, 'wb') as file:
                os.fchown(file.fileno(), 10000 + user_id(), 10000 + user_id())
                file.write(data)
            return result['output'] | {'local_path': args['filename']}, []
        if name == 'publish_file':
            import stat
            with os.fdopen(open_local(self.path, args['local_path'], os.O_RDONLY), 'rb') as file:
                if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                    raise ValueError('Можно сохранять только обычный файл')
                data = file.read(10 * 1024 * 1024 + 1)
            if not data or len(data) > 10 * 1024 * 1024:
                raise ValueError('Пустой файл или размер больше 10 МБ')
            result = await library_request('save_file_to_silo', {'path': args['path'], 'version': args['version'], 'content': base64.b64encode(data).decode()})
            self.files[args['path']] = '/api/integrations/monocle/workspace/download?path=' + quote(args['path'], safe='')
            return result['output'] | {'url': self.files[args['path']]}, []

    async def respond(self, message):
        method = message['method']
        if method == 'initialize':
            return {'protocolVersion': message['params']['protocolVersion'], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'ucheba', 'version': '1'}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': [{'name': item['name'], 'description': item['description'], 'inputSchema': item['parameters']} for item in TOOLS]}
        if method != 'tools/call':
            raise ValueError('Метод не поддерживается')
        try:
            async with self.lock:
                output, images = await self.call(message['params']['name'], message['params'].get('arguments', {}))
            content = [{'type': 'text', 'text': json.dumps(output, ensure_ascii=False, default=str)}]
            for image in images:
                if image.get('type') == 'input_text':
                    content.append({'type': 'text', 'text': image['text']})
                elif image.get('type') == 'input_image':
                    url = image['image_url']
                    header, data = url.split(',', 1)
                    content.append({'type': 'image', 'data': data, 'mimeType': header[5:].split(';')[0]})
            return {'content': content, 'isError': False}
        except (HTTPException, httpx.HTTPError, ValueError, OSError, KeyError, TypeError) as exc:
            detail = str(exc.detail) if isinstance(exc, HTTPException) else 'Ошибка инструмента: ' + type(exc).__name__
            self.progress.add(detail)
            return {'content': [{'type': 'text', 'text': detail}], 'isError': True}


@asynccontextmanager
async def serve(adapter):
    owner = user_id()
    run_id = browser_run_id.get()
    handlers = set()
    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        token = current_user_id.set(owner)
        browser_token = browser_run_id.set(run_id)
        try:
            credentials = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            if struct.unpack('3i', credentials)[1] != 10000 + owner:
                return
            while line := await reader.readline():
                message = json.loads(line)
                if 'id' not in message:
                    continue
                try:
                    result = {'result': await adapter.respond(message)}
                except (ValueError, KeyError):
                    result = {'error': {'code': -32602, 'message': 'Некорректный MCP-запрос'}}
                except Exception as exc:
                    # Keep the connection usable; expose no arguments or private exception text.
                    logging.getLogger('uvicorn.error').error('Arrodes MCP handler: %s', type(exc).__name__)
                    result = {'error': {'code': -32603, 'message': 'Внутренняя ошибка инструмента: ' + type(exc).__name__}}
                writer.write((json.dumps({'jsonrpc': '2.0', 'id': message['id'], **result}, ensure_ascii=False) + '\n').encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            browser_run_id.reset(browser_token)
            current_user_id.reset(token)
            handlers.discard(task)
    with tempfile.TemporaryDirectory(prefix='arrodes-') as directory:
        os.chmod(directory, 0o711)
        path = str(Path(directory) / 'tools.sock')
        server = await asyncio.start_unix_server(handle, path, limit=2 * 1024 * 1024)
        os.chown(path, 10000 + owner, 10000 + owner)
        os.chmod(path, 0o600)
        try:
            async with server:
                yield {'mcp_servers': {'ucheba': {'command': '/usr/local/bin/python', 'args': ['/app/mcp_stdio.py', path],
                       'tool_timeout_sec': 2147483647, 'required': True,
                       'default_tools_approval_mode': 'approve'}}}
        finally:
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)
