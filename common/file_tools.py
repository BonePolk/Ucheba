"""Инструменты Python для HTTP и сохранённых файлов общего браузера."""
FILE_INSTRUCTION = ('Скачивание кнопкой/JavaScript перехватывается браузером, включая новые вкладки '
    'и blob-ссылки. Ответ действия содержит downloads со статусом downloading/saved/failed. '
    'Если downloading, проверь list_downloads позже, не нажимай кнопку повторно. '
    'При saved используй file_id в read_download. '
    'Кнопки входа и SSO нажимай по role=button, element или CSS/XPath из elements. '
    'Координаты используй только по свежему снимку видимой области: во время загрузки '
    'вёрстка может смещаться. Не угадывай подпись кнопки, бери точное name из elements. '
    'Чтение страницы может вернуть ready_state/notice о незавершённой загрузке. '
    'Используй доступные данные; если их мало, перечитай текущую страницу без повторного перехода. '
    'Произвольное выполнение JavaScript запрещено; взаимодействуй через кнопки, ввод и переходы. '
    'Для скачивания и чтения HTTP/API предпочитай python_http_request или download_file: '
    'прямые Python-запросы обычно быстрее и стабильнее управления браузером, если сайт их поддерживает. '
    'Для JS/SSO используй браузер. Файлы сохраняются и доступны обоим агентам через list_downloads/read_download. '
    'PDF можно смотреть постранично картинками. Бинарные данные разбирай python_binary: ZIP/Office XML '
    'или struct с известным форматом; не угадывай устройство неизвестного формата. '
    'Если ответ сообщает, что формат не поддерживается, не повторяй hex/struct без известной схемы; '
    'найди другой формат или сообщи владельцу о препятствии. '
    'Не отправляй изменяющие данные запросы в ЛК; POST допустим только для чтения/поиска. ')


def tool(name, description, properties):
    return {'type': 'function', 'name': name, 'description': description, 'strict': True,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}}


FILE_TOOLS = [
    tool('download_file', 'Скачать URL через Python с подходящими cookie браузера. Сохранить оригинал для повторного чтения.', {'url': {'type': 'string'}}),
    tool('python_http_request', 'Прямой HTTP-запрос через Python. GET или POST для чтения/поиска. Тело — строка, content_type — MIME. Пустые строки для GET. Возвращает текст или сохраняет бинарный файл. Cookie подставляются без передачи модели.',
         {'url': {'type': 'string'}, 'method': {'type': 'string', 'enum': ['GET', 'POST']}, 'body': {'type': 'string'}, 'content_type': {'type': 'string', 'enum': ['', 'application/json', 'application/x-www-form-urlencoded', 'text/plain']}}),
    tool('list_downloads', 'Показать сохранённые файлы текущего пользователя, общие для Straus и Lucheba.', {}),
    tool('read_download', 'Прочитать сохранённый файл через Python. Для PDF page начинается с 1, возвращается также картинка страницы. Для других файлов page=1.', {'file_id': {'type': 'string'}, 'page': {'type': 'integer', 'minimum': 1}}),
    tool('python_binary', 'Разобрать сохранённый бинарный файл через Python. mode=hex — байты; struct — struct.unpack по format; zip — список архива, либо чтение member. Для неиспользуемых строк передай пустые. offset — смещение байтов.',
         {'file_id': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['hex', 'struct', 'zip']}, 'format': {'type': 'string'}, 'member': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}}),
]
FILE_NAMES = {item['name'] for item in FILE_TOOLS}
