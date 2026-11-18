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
from xml.etree import ElementTree
from common.office import spreadsheet_text, table_text

from docx import Document
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
import pypdfium2 as pdfium
from minio.error import S3Error

MAX_SIZE = 25 * 1024 * 1024
MAX_FILES = 5
TEXT_LIMIT = 40000
pdf_lock = Lock()  # PDFium нельзя вызывать одновременно из разных потоков.
TEXT_EXTENSIONS = {
    '.txt', '.md', '.csv', '.tsv', '.json', '.yaml', '.yml', '.toml', '.ini',
    '.cfg', '.conf', '.xml', '.html', '.htm', '.tex', '.rst', '.log',
    '.py', '.js', '.ts', '.tsx', '.jsx', '.c', '.cpp', '.h', '.hpp', '.cs',
    '.java', '.kt', '.go', '.rs', '.php', '.rb', '.sql', '.sh', '.bat',
    '.ps1', '.r', '.m', '.lua', '.scala',
}


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


def _pdf_page_count(raw):
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            reader.decrypt('')
        return len(reader.pages)
    except Exception:
        with pdf_lock:
            document = pdfium.PdfDocument(raw)
            try:
                return len(document)
            finally:
                document.close()


def get_info(memory, task_id, file_id):
    key = file_key(task_id, file_id)
    info = json.loads(memory.read_bytes(key + '.json'))
    # Старые PDF тоже доступны как картинки, без изменения оригинала.
    if info['mime'] == 'application/pdf' and ('pages' not in info or info['kind'] != 'pdf'):
        info['kind'] = 'pdf'
        info['pages'] = _pdf_page_count(memory.read_bytes(key + '.bin'))
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
            try:
                reader = PdfReader(stream)
                if reader.is_encrypted and reader.decrypt('') == 0:
                    raise HTTPException(422, 'PDF защищён паролем открытия')
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
            except HTTPException:
                raise
            except Exception:
                # Резервная проверка через PDFium для нестандартных PDF.
                with pdf_lock:
                    document = pdfium.PdfDocument(content)
                    try:
                        if len(document) <= 0:
                            raise ValueError()
                    finally:
                        document.close()
                return 'pdf', '', 'application/pdf'
        if suffix == '.docx':
            with ZipFile(stream) as archive:
                if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
                    raise ValueError()
            stream.seek(0)
            document = Document(stream)
            parts = [paragraph.text for paragraph in document.paragraphs]
            for table in document.tables:
                parts.extend(' | '.join(cell.text for cell in row.cells) for row in table.rows)
            text = '\n'.join(parts)[:TEXT_LIMIT]
            if not text.strip():
                text = '[Документ без текстового слоя; содержит таблицы, схемы или изображения]'
            return 'text', text, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if suffix in ('.xlsx', '.ods', '.pptx', '.odt'):
            with ZipFile(stream) as archive:
                if len(archive.infolist()) > 2000 or sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
                    raise ValueError()
                if suffix == '.ods':
                    text = table_text(name, content)
                    mime = 'application/vnd.oasis.opendocument.spreadsheet'
                elif suffix == '.xlsx':
                    text = spreadsheet_text(archive)
                    mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                elif suffix == '.odt':
                    text = ' '.join(ElementTree.fromstring(archive.read('content.xml')).itertext())[:TEXT_LIMIT]
                    mime = 'application/vnd.oasis.opendocument.text'
                else:
                    parts = []
                    for member in sorted(archive.namelist()):
                        if re.fullmatch(r'ppt/slides/slide[0-9]+\.xml', member):
                            parts.append(' '.join(ElementTree.fromstring(archive.read(member)).itertext()))
                    text = '\n'.join(parts)[:TEXT_LIMIT]
                    mime = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            if not text.strip():
                text = '[Документ без текстового слоя; содержит таблицы, схемы или изображения]'
            return 'text', text, mime
        if suffix in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'):
            with Image.open(stream) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP', 'GIF', 'BMP') or image.width * image.height > 20000000:
                    raise ValueError()
                image.verify()
                mime = {
                    'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp',
                    'GIF': 'image/gif', 'BMP': 'image/bmp',
                }[image.format]
            return 'image', '', mime
        if suffix == '.ipynb':
            notebook = json.loads(content.decode('utf-8-sig'))
            cells = []
            for cell in notebook.get('cells', []):
                ctype = cell.get('cell_type', '')
                src = cell.get('source', '')
                src_text = ''.join(src) if isinstance(src, list) else str(src)
                if src_text.strip():
                    cells.append(f'[{ctype}]\n{src_text}')
            text = '\n\n'.join(cells)[:TEXT_LIMIT] or '[Пустой ноутбук Jupyter]'
            return 'text', text, 'application/x-ipynb+json'
        if suffix in TEXT_EXTENSIONS:
            if b'\x00' in content[:4096]:
                raise ValueError()
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = content.decode('cp1251')
            if not text.strip():
                raise HTTPException(422, 'Текстовый файл не содержит текста')
            return 'text', text[:TEXT_LIMIT], 'text/plain'
        if suffix == '.zip':
            with ZipFile(stream) as archive:
                names = archive.namelist()[:200]
            listing = '\n'.join(names) if names else '(пустой архив)'
            return 'archive', f'ZIP-архив, файлы:\n{listing}'[:TEXT_LIMIT], 'application/zip'
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'Не удалось прочитать файл. Проверьте, не повреждён ли он.') from None
    return 'binary', f'[Бинарный файл {name}, {len(content)} байт]', 'application/octet-stream'


def upload(memory, task_id, name, content, deduplicate=False):
    if not content:
        raise HTTPException(422, 'Файл пустой (0 байт)')
    if len(content) > MAX_SIZE:
        raise HTTPException(413, 'Максимальный размер файла — 25 МБ')
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
        raw = memory.read_bytes(file_key(task_id, file_id) + '.bin')
        if info['mime'] in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
            encoded = base64.b64encode(raw).decode()
            mime_url = info['mime']
        else:
            with Image.open(io.BytesIO(raw)) as image:
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='PNG')
                encoded = base64.b64encode(buffer.getvalue()).decode()
                mime_url = 'image/png'
        content.append({'type': 'input_image', 'image_url': f"data:{mime_url};base64,{encoded}"})
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
