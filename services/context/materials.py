"""Файлы конкретной задачи. Секреты и браузерные cookie здесь не хранятся."""
import base64
import io
import hashlib
import json
from pathlib import PurePath
import re
from threading import Lock
from uuid import uuid4
from zipfile import ZipFile

from docx import Document
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
import pypdfium2 as pdfium
from minio.error import S3Error

MAX_SIZE = 10 * 1024 * 1024
MAX_FILES = 5
TEXT_LIMIT = 40000
pdf_lock = Lock()  # PDFium нельзя вызывать одновременно из разных потоков.


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
            file_id = item.object_name.rsplit('/', 1)[-1][:-5]
            info = get_info(memory, task_id, file_id)
            if not info.get('detached', False):
                files.append(public_info(info))
    return files


def public_info(info):
    return {key: info[key] for key in ('id', 'name', 'size', 'kind', 'pages') if key in info} | {
        'preview': ' '.join(info['text'].split())[:400],
        'has_text': bool(info['text'].strip()),
    }


def get_info(memory, task_id, file_id):
    key = file_key(task_id, file_id)
    info = json.loads(memory.read_bytes(key + '.json'))
    # Старые PDF тоже доступны как картинки, без изменения оригинала.
    if info['mime'] == 'application/pdf' and ('pages' not in info or info['kind'] != 'pdf'):
        info['kind'] = 'pdf'
        info['pages'] = len(PdfReader(io.BytesIO(memory.read_bytes(key + '.bin'))).pages)
        memory.put_bytes(key + '.json', json.dumps(info, ensure_ascii=False).encode(), 'application/json')
    return info


def saved_files(memory, query=''):
    files = []
    seen = set()
    for item in memory.client.list_objects(memory.bucket, prefix=memory.prefix + 'attachments/', recursive=True):
        match = re.fullmatch(r'attachments/task-([1-9][0-9]*)/([a-f0-9]{32})\.json', item.object_name[len(memory.prefix):])
        if not match:
            continue
        task_id, file_id = int(match[1]), match[2]
        if file_id in seen:
            continue
        info = get_info(memory, task_id, file_id)
        if query.casefold() in (info['name'] + '\n' + info['text']).casefold():
            files.append(public_info(info) | {'task_id': task_id})
            seen.add(file_id)
        if len(files) == 100:
            break
    return files


def reuse(memory, task_id, source_task_id, file_id):
    info = get_info(memory, source_task_id, file_id)
    content = memory.read_bytes(file_key(source_task_id, file_id) + '.bin')
    return upload(memory, task_id, info['name'], content, deduplicate=True)


def pdf_page(memory, task_id, file_id, page_number):
    info = get_info(memory, task_id, file_id)
    if info['kind'] != 'pdf':
        raise HTTPException(400, 'Выбранный файл не является PDF')
    if type(page_number) is not int or not 1 <= page_number <= info['pages']:
        raise HTTPException(400, f"В PDF {info['pages']} страниц. Номер начинается с 1.")
    original = memory.read_bytes(file_key(task_id, file_id) + '.bin')
    digest = hashlib.sha256(original).hexdigest()
    cache_key = f'pdf-pages/v1/{digest}/{page_number}.jpg'
    try:
        image = memory.read_bytes(cache_key)
    except S3Error as exc:
        if exc.code != 'NoSuchKey':
            raise
        try:
            with pdf_lock:
                document = pdfium.PdfDocument(original)
                try:
                    page = document[page_number - 1]
                    width, height = page.get_size()
                    bitmap = page.render(scale=1600 / max(width, height))
                    try:
                        image_buffer = io.BytesIO()
                        pil_image = bitmap.to_pil().convert('RGB')
                        try:
                            pil_image.save(image_buffer, format='JPEG', quality=85)
                        finally:
                            pil_image.close()
                        image = image_buffer.getvalue()
                    finally:
                        bitmap.close()
                finally:
                    document.close()
        except Exception:
            raise HTTPException(422, 'Не удалось отрисовать страницу PDF') from None
        memory.put_bytes(cache_key, image, 'image/jpeg')
    return [
        {'type': 'input_text', 'text': f"PDF {info['name']}, task_id={task_id}, file_id={file_id}, страница {page_number} из {info['pages']}."},
        {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' + base64.b64encode(image).decode(), 'detail': 'high'},
    ]


def extract(name, content):
    suffix = PurePath(name).suffix.lower()
    stream = io.BytesIO(content)
    try:
        if suffix == '.pdf':
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise ValueError()
            if not len(reader.pages):
                raise ValueError()
            text = ''
            for page in reader.pages[:50]:
                try:
                    text += (page.extract_text() or '') + '\n'
                except Exception:
                    # Содержимое всё ещё можно прочитать по изображению страницы.
                    continue
                if len(text) >= TEXT_LIMIT:
                    break
            return 'pdf', text[:TEXT_LIMIT], 'application/pdf'
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
        raise HTTPException(422, 'Не удалось прочитать файл. Проверьте документ; текст сохраните в UTF-8.') from None
    raise HTTPException(422, 'Поддерживаются PDF, DOCX, текст, PNG, JPG и WEBP')


def upload(memory, task_id, name, content, deduplicate=False):
    if not content or len(content) > MAX_SIZE:
        raise HTTPException(413, 'Размер файла должен быть от 1 байта до 10 МБ')
    file_id = hashlib.sha256(content).hexdigest()[:32] if deduplicate else uuid4().hex
    existing = list_files(memory, task_id)
    if deduplicate:
        for file in existing:
            if file['id'] == file_id:
                if memory.read_bytes(file_key(task_id, file_id) + '.bin') == content:
                    return file
                # Отредактированный файл сохраняем, скачанную версию кладём отдельно.
                file_id = uuid4().hex
                break
    if len(existing) >= MAX_FILES:
        raise HTTPException(400, 'К задаче можно прикрепить до пяти файлов')
    name = name.replace('\\', '/').split('/')[-1][:200]
    kind, text, mime = extract(name, content)
    if kind == 'text' and not text.strip():
        raise HTTPException(422, 'В файле нет доступного текста. Прикрепите скан как изображение.')
    key = file_key(task_id, file_id)
    info = {'id': file_id, 'name': name, 'size': len(content), 'kind': kind, 'mime': mime, 'text': text}
    memory.put_bytes(key + '.bin', content, mime)
    memory.put_bytes(key + '.json', json.dumps(info, ensure_ascii=False).encode(), 'application/json')
    return public_info(get_info(memory, task_id, file_id))


def remove(memory, task_id, file_id):
    info = get_info(memory, task_id, file_id)
    info['detached'] = True
    memory.put_bytes(file_key(task_id, file_id) + '.json', json.dumps(info, ensure_ascii=False).encode(), 'application/json')


def file_input(memory, task_id, file_id):
    info = get_info(memory, task_id, file_id)
    content = [{'type': 'input_text', 'text': f"Файл: {info['name']}; task_id={task_id}; file_id={file_id}\n{info['text']}"}]
    if info['kind'] == 'image':
        encoded = base64.b64encode(memory.read_bytes(file_key(task_id, file_id) + '.bin')).decode()
        content.append({'type': 'input_image', 'image_url': f"data:{info['mime']};base64,{encoded}"})
    elif info['kind'] == 'pdf':
        content.extend(pdf_page(memory, task_id, file_id, 1))
    return content


def model_input(memory, task_id):
    files = [file | {'task_id': task_id} for file in list_files(memory, task_id)]
    return [{'type': 'input_text', 'text': 'Каталог прикреплённых файлов. Выбери нужные и открой через read_file; '
             'это только сведения о документах, полное содержимое ещё не прочитано.\n' + json.dumps(files, ensure_ascii=False)}]


def browser_images(output):
    """Снимок идёт в image-input, а длинная страница — читаемыми полосами сверху вниз."""
    content = base64.b64decode(output.pop('image'), validate=True)
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > 33000000:
            raise HTTPException(413, 'Снимок слишком большой, просматривайте страницу прокруткой')
        parts = []
        for top in range(0, image.height, 1400):
            for left in range(0, image.width, 1100):
                right, bottom = min(left + 1100, image.width), min(top + 1400, image.height)
                with image.crop((left, top, right, bottom)) as piece:
                    data = io.BytesIO()
                    with piece.convert('RGB') as rgb:
                        rgb.save(data, format='JPEG', quality=85)
                parts.append({'type': 'input_text', 'text':
                    f"Снимок {output.get('url', '')}: область x={left}..{right}, y={top}..{bottom}. "
                    'Клики используют координаты видимой области 1100×750, не всей страницы; сначала прокрути к нужному месту.'})
                parts.append({'type': 'input_image', 'image_url': 'data:image/jpeg;base64,' +
                              base64.b64encode(data.getvalue()).decode(), 'detail': 'high'})
    return parts
