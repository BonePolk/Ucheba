"""Инструменты Python для HTTP и сохранённых файлов общего браузера."""
FILE_INSTRUCTION = ('Скачивание кнопкой/JavaScript перехватывается браузером, включая новые вкладки '
    'и blob-ссылки. Ответ действия содержит downloads со статусом downloading/saved/failed. '
    'Если downloading, проверь list_downloads позже, не нажимай кнопку повторно. '
    'При saved используй file_id в read_download. '
    'HTML-тег a означает ссылку, ARIA-роль — link; name — точная подпись, не тег. '
    'При status=choose_element используй свежий список elements из ответа, не повторяй прежние параметры. '
    'Кнопки входа и SSO нажимай по role=button, element или CSS/XPath из elements. '
    'Координаты используй только по свежему снимку видимой области: во время загрузки '
    'вёрстка может смещаться. Не угадывай подпись кнопки, бери точное name из elements. '
    'Чтение страницы может вернуть ready_state/notice о незавершённой загрузке. '
    'Используй доступные данные; если их мало, перечитай текущую страницу без повторного перехода. '
    'Произвольное выполнение JavaScript запрещено; взаимодействуй через кнопки, ввод и переходы. '
    'Основной способ работы с сайтами — браузер. python_http_request и download_file используй '
    'только когда уже известны точный URL, метод и необходимые параметры из прочитанной страницы, '
    'готовой ссылки или документации и очевидно, что достаточно простого прямого запроса. '
    'Не угадывай endpoint, параметры, идентификаторы, форму входа или способ авторизации. '
    'Не пробуй HTTP наугад ради обхода сложной страницы. При сомнении используй браузер '
    'и скачивание кнопкой. После неудачного прямого запроса вернись к браузеру; '
    'не перебирай варианты URL/параметров без новых подтверждённых данных. '
    'Вход и SSO выполняй через браузер. Файлы сохраняются и доступны обоим агентам через list_downloads/read_download. '
    'PDF можно смотреть постранично картинками. Бинарные данные разбирай python_binary: ZIP/Office XML '
    'или struct с известным форматом; не угадывай устройство неизвестного формата. '
    'Если ответ сообщает, что формат не поддерживается, не повторяй hex/struct без известной схемы; '
    'найди другой формат или сообщи владельцу о препятствии. '
    'Не отправляй изменяющие данные запросы в ЛК; POST допустим только для чтения/поиска. ')


def tool(name, description, properties):
    return {'type': 'function', 'name': name, 'description': description, 'strict': True,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}}


FILE_TOOLS = [
    tool('download_file', 'Скачать через Python только точный готовый URL файла из источника, когда достаточно простого запроса. Не угадывай адрес; при сомнении нажми кнопку скачивания в браузере. Оригинал сохраняется.', {'url': {'type': 'string'}}),
    tool('python_http_request', 'Прямой HTTP-запрос через Python, только если точные URL, метод и параметры уже подтверждены источником и простой запрос заведомо подходит. Не для проб и подбора API. GET или POST для чтения/поиска. Тело — строка, content_type — MIME. Пустые строки для GET. Возвращает текст или сохраняет бинарный файл. Cookie подставляются без передачи модели.',
         {'url': {'type': 'string'}, 'method': {'type': 'string', 'enum': ['GET', 'POST']}, 'body': {'type': 'string'}, 'content_type': {'type': 'string', 'enum': ['', 'application/json', 'application/x-www-form-urlencoded', 'text/plain']}}),
    tool('list_downloads', 'Показать сохранённые файлы текущего пользователя, общие для Straus и Monocle.', {}),
    tool('read_download', 'Прочитать сохранённый файл через Python. Для PDF page начинается с 1, возвращается также картинка страницы. Для других файлов page=1.', {'file_id': {'type': 'string'}, 'page': {'type': 'integer', 'minimum': 1}}),
    tool('python_binary', 'Разобрать сохранённый бинарный файл через Python. mode=hex — байты; struct — struct.unpack по format; zip — список архива, либо чтение member. Для неиспользуемых строк передай пустые. offset — смещение байтов.',
         {'file_id': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['hex', 'struct', 'zip']}, 'format': {'type': 'string'}, 'member': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}}),
]
LIBRARY_TOOLS = [
{'type': 'function', 'name': 'list_user_files',
     'description': 'Дерево личных папок и текстовых файлов владельца. Выбери подходящие документы по имени и пути и прочитай через read_user_file.',
     'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, 'strict': True},
{'type': 'function', 'name': 'read_user_file',
     'description': 'Прочитать личный текстовый файл владельца по path из list_user_files.',
     'parameters': {'type': 'object', 'properties': {'path': {'type': 'string', 'maxLength': 500}}, 'required': ['path'], 'additionalProperties': False}, 'strict': True},
{'type': 'function', 'name': 'list_memory',
     'description': 'Список файлов памяти текущего пользователя. Файлы названы по номерам задач.',
     'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
     'strict': True},
{'type': 'function', 'name': 'read_memory',
     'description': 'Прочитать файл памяти текущего пользователя.',
     'parameters': {'type': 'object', 'properties': {'filename': {'type': 'string'}},
                    'required': ['filename'], 'additionalProperties': False},
     'strict': True},
{'type': 'function', 'name': 'list_saved_files',
     'description': 'Найти сохранённые файлы текущего пользователя во всех его задачах по имени или тексту. Пустой query показывает список. Возвращает task_id, id, имя, краткий preview, наличие текста и число страниц PDF. По этим данным выбери нужные документы для read_file.',
     'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'maxLength': 200}}, 'required': ['query'], 'additionalProperties': False}, 'strict': True},
{'type': 'function', 'name': 'read_file',
     'description': 'Открыть выбранный файл текущего пользователя из каталога вложений или list_saved_files. source_task_id — task_id из каталога. Вернёт текст, изображение либо первую страницу PDF. Прикреплять сохранённый файл к текущей задаче вручную не требуется.',
     'parameters': {'type': 'object', 'properties': {'source_task_id': {'type': 'integer', 'minimum': 1}, 'file_id': {'type': 'string'}},
                    'required': ['source_task_id', 'file_id'], 'additionalProperties': False}, 'strict': True},
{'type': 'function', 'name': 'view_pdf_page',
     'description': 'Посмотреть выбранную страницу любого сохранённого PDF пользователя. source_task_id и file_id берутся из каталога. page начинается с 1. Используй для сканов, таблиц, схем и формул; прикрепление к текущей задаче не требуется.',
     'parameters': {'type': 'object', 'properties': {'source_task_id': {'type': 'integer', 'minimum': 1}, 'file_id': {'type': 'string'}, 'page': {'type': 'integer', 'minimum': 1}},
                    'required': ['source_task_id', 'file_id', 'page'], 'additionalProperties': False}, 'strict': True}
]
LIBRARY_NAMES = {item['name'] for item in LIBRARY_TOOLS}
FILE_TOOLS.extend(LIBRARY_TOOLS)
FILE_NAMES = {item['name'] for item in FILE_TOOLS}


async def run_file_tool(name, args, browser_request, progress):
    if name in LIBRARY_NAMES:
        from common.library_client import library_request
        progress.add({'list_user_files': 'Просматривает личные папки и файлы', 'read_user_file': 'Читает личный файл',
                      'list_memory': 'Просматривает сохранённые записи задач', 'read_memory': 'Читает сохранённую запись задачи',
                      'list_saved_files': 'Ищет сохранённые документы', 'read_file': 'Читает выбранный документ',
                      'view_pdf_page': 'Просматривает страницу PDF'}[name])
        result = await library_request(name, args)
        return result['output'], result.get('content', [])
    progress.add({'download_file': 'Скачивает файл через Python', 'python_http_request': 'Отправляет HTTP-запрос через Python',
                  'read_download': 'Читает сохранённый файл', 'list_downloads': 'Просматривает сохранённые загрузки',
                  'python_binary': 'Разбирает бинарный файл через Python'}[name])
    output = await browser_request('/files', {'action': name, **args})
    image = output.pop('image', None)
    content = [{'type': 'input_image', 'image_url': f"data:{output.get('mime', 'image/jpeg')};base64,{image}", 'detail': 'high'}] if image else []
    return output, content
