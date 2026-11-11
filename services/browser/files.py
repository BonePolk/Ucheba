"""Сохранённые загрузки пользователя. Python читает данные, но не исполняет их."""
import base64
import io
import json
from pathlib import Path
import re
import struct
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree

import httpx
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
import pypdfium2 as pdfium

from common.identity import user_id
from common.office import spreadsheet_text

LIMIT = 10 * 1024 * 1024


def root():
    path = Path('/browser/users') / str(user_id()) / 'downloads'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save(name, content):
    file_id = uuid4().hex
    name = name.replace('\\', '/').split('/')[-1][:200] or 'document'
    (root() / (file_id + '.bin')).write_bytes(content)
    info = {'file_id': file_id, 'name': name, 'size': len(content)}
    (root() / (file_id + '.json')).write_text(json.dumps(info, ensure_ascii=False))
    return info


def load(file_id):
    if not re.fullmatch('[a-f0-9]{32}', file_id):
        raise ValueError('Неверный ID файла')
    try:
        info = json.loads((root() / (file_id + '.json')).read_text())
        return info, (root() / (file_id + '.bin')).read_bytes()
    except FileNotFoundError:
        raise ValueError('Файл не найден') from None


def archive(content):
    result = ZipFile(io.BytesIO(content))
    if sum(item.file_size for item in result.infolist()) > 20 * 1024 * 1024 or len(result.infolist()) > 2000:
        result.close()
        raise ValueError('Слишком большой распакованный архив')
    return result



def read(file_id, page=1):
    info, content = load(file_id)
    suffix = Path(info['name']).suffix.lower()
    result = dict(info)
    if suffix == '.pdf' or content.startswith(b'%PDF'):
        reader = PdfReader(io.BytesIO(content))
        if page < 1 or page > len(reader.pages):
            raise ValueError('Нет такой страницы PDF')
        result.update(pages=len(reader.pages), page=page, text=(reader.pages[page - 1].extract_text() or '')[:40000])
        # Вызывается без await в одном event loop: PDFium не выполняется параллельно.
        with pdfium.PdfDocument(content) as document:
            sheet = document[page - 1]
            try:
                width, height = sheet.get_size()
                scale = min(2, 2000 / max(width, height))
                bitmap = sheet.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    stream = io.BytesIO()
                    image.save(stream, format='JPEG')
                    result.update(image=base64.b64encode(stream.getvalue()).decode(), mime='image/jpeg')
                finally:
                    bitmap.close()
            finally:
                sheet.close()
    elif suffix in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff'):
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 20000000:
                raise ValueError('Слишком большое изображение')
            image.thumbnail((2000, 2000))
            stream = io.BytesIO()
            image.convert('RGB').save(stream, format='JPEG')
            result.update(image=base64.b64encode(stream.getvalue()).decode(), mime='image/jpeg')
    elif suffix in ('.docx', '.xlsx', '.pptx', '.zip') or content.startswith(b'PK\x03\x04'):
        with archive(content) as zipped:
            result['members'] = zipped.namelist()[:200]
            if suffix == '.xlsx' or 'xl/workbook.xml' in zipped.namelist():
                result['text'] = spreadsheet_text(zipped)
                result['notice'] = 'Формулы не выполняются; показаны сохранённые значения. Числа дат/времени могут быть в исходном представлении Excel.'
            else:
                texts = []
                for member in zipped.namelist():
                    if re.fullmatch(r'word/(document|header[0-9]+|footer[0-9]+|footnotes|endnotes)\.xml|ppt/slides/slide[0-9]+\.xml', member):
                        texts.append(member + '\n' + ' '.join(ElementTree.fromstring(zipped.read(member)).itertext()))
                        if sum(map(len, texts)) > 40000:
                            break
                result['text'] = '\n'.join(texts)[:40000]
    elif content.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        result['notice'] = ('Старый бинарный Office OLE (например DOC/XLS). Этот формат здесь не декодируется. '
                            'Не пытайся разбирать его повторными hex/struct-вызовами: найди PDF/DOCX/XLSX '
                            'либо сообщи владельцу, что требуется преобразование файла.')
        result['format'] = 'ole_office'
    else:
        try:
            if b'\x00' in content[:4096]:
                raise UnicodeDecodeError('utf-8', content, 0, 1, 'binary NUL')
            result['text'] = content.decode('utf-8-sig')[:40000]
        except UnicodeDecodeError:
            result.update(hex=content[:512].hex(' '), notice='Бинарный формат: используй python_binary с известной схемой. Произвольные программы из файла не запускаются.')
    return result


async def request_file(args, tab, validate_url, safe_url):
    url = args['url']
    method = args.get('method', 'GET')
    if method not in ('GET', 'POST'):
        raise ValueError('Доступны GET и POST')
    body = args.get('body', '')
    mime = args.get('content_type', '')
    if len(body) > 40000 or mime not in ('', 'application/json', 'application/x-www-form-urlencoded', 'text/plain'):
        raise ValueError('Недопустимые параметры HTTP')
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
        for _ in range(6):
            await validate_url(url)
            cookies = await tab.context.cookies(url)
            headers = {'Cookie': '; '.join(f"{item['name']}={item['value']}" for item in cookies)}
            if mime:
                headers['Content-Type'] = mime
            async with client.stream(method, url, headers=headers, content=body.encode()) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get('location', ''))
                    if response.status_code == 303 or response.status_code in (301, 302) and method == 'POST':
                        method, body, mime = 'GET', '', ''
                    continue
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > LIMIT:
                        raise ValueError('Ответ больше 10 МБ')
                content = bytes(content)
                kind = response.headers.get('content-type', '').split(';')[0]
                if args['action'] == 'python_http_request' and (kind.startswith('text/') or kind in ('application/json', 'application/xml')):
                    return {'http_status': response.status_code, 'url': safe_url(url), 'text': content.decode(response.encoding or 'utf-8', errors='replace')[:40000]}
                if not response.is_success:
                    return {'http_status': response.status_code, 'error': 'Сайт не отдал файл'}
                from email.message import Message
                from urllib.parse import unquote, urlsplit
                header = Message()
                header['Content-Disposition'] = response.headers.get('content-disposition', '')
                name = header.get_filename() or unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or 'document'
                if not Path(name).suffix:
                    name += {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg',
                             'image/webp': '.webp', 'image/gif': '.gif', 'application/zip': '.zip',
                             'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
                             'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx'}.get(kind, '')
                info = save(name, content)
                return info | {'http_status': response.status_code, 'url': safe_url(url), 'notice': 'Оригинал сохранён. Открой через read_download.'}
    raise ValueError('Слишком много перенаправлений')


def binary(args):
    info, content = load(args['file_id'])
    offset = args['offset']
    if args['mode'] == 'hex':
        return info | {'offset': offset, 'hex': content[offset:offset + 512].hex(' ')}
    if args['mode'] == 'struct':
        fmt = args['format']
        if not re.fullmatch(r'[<>=!@]?[0-9xcbB?hHiIlLqQnNefdspP ]{1,100}', fmt) or struct.calcsize(fmt) > 4096:
            raise ValueError('Формат struct слишком большой или неверный')
        values = struct.unpack_from(fmt, content, offset)
        return {'values': [value.hex() if isinstance(value, bytes) else value for value in values]}
    if args['mode'] == 'zip':
        with archive(content) as zipped:
            if not args['member']:
                return {'members': zipped.namelist()[:200]}
            data = zipped.read(args['member'])
            return {'text': data[:40000].decode('utf-8', errors='replace'), 'hex': data[:256].hex(' ')}
    raise ValueError('Неизвестный режим разбора')
