"""Личные файлы и вложения задач внутри префикса текущего пользователя."""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
import re
from threading import Lock

from fastapi import HTTPException
from minio.error import S3Error

import materials

MAX_TEXT = 1024 * 1024
write_lock = Lock()


def personal_path(path):
    if not isinstance(path, str) or not path or len(path) > 500:
        raise HTTPException(422, 'Укажите путь длиной до 500 символов')
    parts = path.split('/')
    if any(not part or part in ('.', '..') or len(part) > 200 or
           any(ord(char) < 32 or char in '\\' for char in part) for part in parts) or parts[0].startswith('@'):
        raise HTTPException(422, 'Недопустимый путь файла или папки')
    return path


def attachment(path):
    match = re.fullmatch(r'@tasks/([1-9][0-9]*)/([a-f0-9]{32})', path)
    if not match:
        raise HTTPException(422, 'Некорректный путь вложения')
    return int(match[1]), match[2]


def optional_read(memory, key):
    try:
        return memory.read_bytes(key)
    except S3Error as exc:
        if exc.code not in ('NoSuchKey', 'NoSuchObject'):
            raise
        return None


def tree(memory, include_attachments=True):
    result = []
    for item in memory.client.list_objects(memory.bucket, prefix=memory.prefix + 'files/', recursive=True):
        path = item.object_name[len(memory.prefix + 'files/'):]
        if not path:
            continue
        result.append({'path': path.rstrip('/'), 'name': path.rstrip('/').rsplit('/', 1)[-1],
                       'folder': path.endswith('/'), 'editable': not path.endswith('/') and item.size <= MAX_TEXT and Path(path).suffix.lower() not in ('.pdf', '.xlsx', '.xls', '.docx', '.doc', '.pptx', '.zip', '.png', '.jpg', '.jpeg', '.gif', '.webp'), 'size': item.size,
                       'url': '/api/integrations/context/workspace/download?path=' + quote(path, safe='')})
    if not include_attachments:
        return result
    # Показываем все вложения, включая убранные из задачи, без лимита поиска Monocle.
    for item in memory.client.list_objects(memory.bucket, prefix=memory.prefix + 'attachments/', recursive=True):
        match = re.fullmatch(r'attachments/task-([1-9][0-9]*)/([a-f0-9]{32})\.json', item.object_name[len(memory.prefix):])
        if not match:
            continue
        task_id, file_id = int(match[1]), match[2]
        info = materials.get_info(memory, task_id, file_id)
        result.append({'path': f'@tasks/{task_id}/{file_id}', 'name': info['name'], 'folder': False,
                       'size': info['size'], 'editable': info['mime'] == 'text/plain',
                       'url': f'/api/integrations/context/files/{task_id}/{file_id}'})
    return result


def read(memory, path):
    if path.startswith('@tasks/'):
        task_id, file_id = attachment(path)
        info = materials.get_info(memory, task_id, file_id)
        if info['mime'] != 'text/plain':
            raise HTTPException(415, 'В редакторе доступны только текстовые файлы')
        key = materials.file_key(task_id, file_id) + '.bin'
    else:
        key = 'files/' + personal_path(path)
    content = optional_read(memory, key)
    if content is None:
        raise HTTPException(404, 'Файл не найден')
    if len(content) > MAX_TEXT:
        raise HTTPException(413, 'В редакторе доступны файлы до 1 МБ')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(415, 'Для редактирования нужен текст в UTF-8') from None
    return {'content': text, 'version': hashlib.sha256(content).hexdigest()}


def check_parents(memory, path):
    parts = path.split('/')
    for length in range(1, len(parts)):
        if optional_read(memory, 'files/' + '/'.join(parts[:length])) is not None:
            raise HTTPException(409, 'Вместо родительской папки существует файл')


def folder(memory, path):
    path = personal_path(path)
    with write_lock:
        check_parents(memory, path)
        if optional_read(memory, 'files/' + path) is not None:
            raise HTTPException(409, 'Файл с таким именем уже существует')
        memory.put_bytes('files/' + path + '/', b'', 'application/x-directory')
    return {'ok': True}


def save(memory, path, text, version):
    content = text.encode('utf-8')
    if len(content) > MAX_TEXT:
        raise HTTPException(413, 'Максимальный размер текста — 1 МБ')
    task_id = None
    with write_lock:
        if path.startswith('@tasks/'):
            task_id, file_id = attachment(path)
            info = materials.get_info(memory, task_id, file_id)
            if info['mime'] != 'text/plain':
                raise HTTPException(415, 'Можно редактировать только текстовые файлы')
            key = materials.file_key(task_id, file_id) + '.bin'
        else:
            path = personal_path(path)
            check_parents(memory, path)
            key = 'files/' + path
            if next(memory.client.list_objects(memory.bucket, prefix=memory.prefix + key + '/', recursive=True), None):
                raise HTTPException(409, 'Папка с таким именем уже существует')
        previous = optional_read(memory, key)
        current_version = hashlib.sha256(previous).hexdigest() if previous is not None else None
        if current_version != version:
            raise HTTPException(409, 'Файл изменился. Откройте его заново перед сохранением')
        memory.put_bytes(key, content, 'text/plain; charset=utf-8')
        if task_id is not None:
            info.update(size=len(content), text=text[:materials.TEXT_LIMIT])
            memory.put_bytes(materials.file_key(task_id, file_id) + '.json',
                             json.dumps(info, ensure_ascii=False).encode(), 'application/json')
    return {'version': hashlib.sha256(content).hexdigest(), 'task_id': task_id}


def read_binary(memory, path):
    path = personal_path(path)
    content = optional_read(memory, 'files/' + path)
    if content is None:
        raise HTTPException(404, 'Файл в Silo не найден')
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Файл больше 10 МБ')
    return content, hashlib.sha256(content).hexdigest()


def save_binary(memory, path, content, version=None):
    path = personal_path(path)
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Размер файла должен быть от 1 байта до 10 МБ')
    with write_lock:
        check_parents(memory, path)
        key = 'files/' + path
        if next(memory.client.list_objects(memory.bucket, prefix=memory.prefix + key + '/', recursive=True), None):
            raise HTTPException(409, 'Папка с таким именем уже существует')
        previous = optional_read(memory, key)
        current = hashlib.sha256(previous).hexdigest() if previous is not None else None
        if current != (version or None):
            raise HTTPException(409, 'Файл уже существует или изменился. Прочитайте его версию перед заменой')
        memory.put_bytes(key, content, 'application/octet-stream')
    return {'path': path, 'size': len(content), 'version': hashlib.sha256(content).hexdigest(), 'status': 'saved'}
