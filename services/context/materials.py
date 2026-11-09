"""Файлы конкретной задачи. Секреты и браузерные cookie здесь не хранятся."""
import base64
import io
import json
from pathlib import PurePath
import re
from uuid import uuid4
from zipfile import ZipFile

from docx import Document
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
from minio.error import S3Error

MAX_SIZE = 10 * 1024 * 1024
MAX_FILES = 5
TEXT_LIMIT = 40000


def browser_site(memory, task_id):
    task_prefix(task_id)
    try:
        return json.loads(memory.read_bytes(f'browser-task-{task_id}.json'))['site_id']
    except S3Error as exc:
        if exc.code != 'NoSuchKey':
            raise
        return None


def select_site(memory, task_id, site_id):
    task_prefix(task_id)
    memory.put_bytes(f'browser-task-{task_id}.json', json.dumps({'site_id': site_id}).encode(), 'application/json')


def task_prefix(task_id):
    if task_id <= 0:
        raise HTTPException(422, 'Некорректный номер задачи')
    return f'attachments/task-{task_id}/'


def file_key(task_id, file_id):
    if not re.fullmatch(r'[a-f0-9]{32}', file_id):
        raise HTTPException(404, 'Файл не найден')
    return task_prefix(task_id) + file_id


def list_files(memory, task_id):
    files = []
    prefix = memory.prefix + task_prefix(task_id)
    for item in memory.client.list_objects(memory.bucket, prefix=prefix, recursive=True):
        if item.object_name.endswith('.json'):
            info = json.loads(memory.read_bytes(item.object_name[len(memory.prefix):]))
            files.append({key: info[key] for key in ('id', 'name', 'size', 'kind')})
    return files


def extract(name, content):
    suffix = PurePath(name).suffix.lower()
    stream = io.BytesIO(content)
    try:
        if suffix == '.pdf':
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise ValueError()
            text = ''
            for page in reader.pages[:50]:
                text += (page.extract_text() or '') + '\n'
                if len(text) >= TEXT_LIMIT:
                    break
            return 'text', text[:TEXT_LIMIT], 'application/pdf'
        if suffix == '.docx':
            with ZipFile(stream) as archive:
                if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
                    raise ValueError()
            stream.seek(0)
            document = Document(stream)
            parts = [paragraph.text for paragraph in document.paragraphs]
            for table in document.tables:
                parts.extend(' | '.join(cell.text for cell in row.cells) for row in table.rows)
            return 'text', '\n'.join(parts)[:TEXT_LIMIT], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if suffix in ('.png', '.jpg', '.jpeg', '.webp'):
            with Image.open(stream) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 20000000:
                    raise ValueError()
                image.verify()
                mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[image.format]
            return 'image', '', mime
        if suffix in ('.txt', '.md', '.csv', '.json', '.py', '.js', '.c', '.cpp', '.h', '.java', '.sql'):
            return 'text', content.decode('utf-8-sig')[:TEXT_LIMIT], 'text/plain'
    except Exception:
        raise HTTPException(422, 'Не удалось прочитать файл. Для сканов используйте PNG или JPG; текст сохраните в UTF-8.') from None
    raise HTTPException(422, 'Поддерживаются PDF, DOCX, текст, PNG, JPG и WEBP')


def upload(memory, task_id, name, content):
    if not content or len(content) > MAX_SIZE:
        raise HTTPException(413, 'Размер файла должен быть от 1 байта до 10 МБ')
    if len(list_files(memory, task_id)) >= MAX_FILES:
        raise HTTPException(400, 'К задаче можно прикрепить до пяти файлов')
    name = name.replace('\\', '/').split('/')[-1][:200]
    kind, text, mime = extract(name, content)
    if kind == 'text' and not text.strip():
        raise HTTPException(422, 'В файле нет доступного текста. Прикрепите скан как изображение.')
    file_id = uuid4().hex
    key = file_key(task_id, file_id)
    info = {'id': file_id, 'name': name, 'size': len(content), 'kind': kind, 'mime': mime, 'text': text}
    memory.put_bytes(key + '.bin', content, mime)
    memory.put_bytes(key + '.json', json.dumps(info, ensure_ascii=False).encode(), 'application/json')
    return {key: info[key] for key in ('id', 'name', 'size', 'kind')}


def remove(memory, task_id, file_id):
    key = memory.prefix + file_key(task_id, file_id)
    memory.client.remove_object(memory.bucket, key + '.json')
    memory.client.remove_object(memory.bucket, key + '.bin')


def model_input(memory, task_id):
    content = []
    for file in list_files(memory, task_id):
        key = file_key(task_id, file['id'])
        info = json.loads(memory.read_bytes(key + '.json'))
        content.append({'type': 'input_text', 'text': f"Прикреплённый файл: {info['name']}\n{info['text']}"})
        if info['kind'] == 'image':
            encoded = base64.b64encode(memory.read_bytes(key + '.bin')).decode()
            content.append({'type': 'input_image', 'image_url': f"data:{info['mime']};base64,{encoded}"})
    return content
