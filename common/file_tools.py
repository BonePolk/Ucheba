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
    'Вход и SSO выполняй через браузер. Все новые загрузки автоматически сохраняются в Silo '
    'в папке Загрузки, видны владельцу в проводнике и доступны обоим агентам через '
    'list_downloads/read_download и silo_path. Не скачивай повторно уже сохранённый файл. '
    'Для поиска и фильтров таблицы используй search_table: source_task_id=0 для загрузок браузера, '
    'иначе номер задачи из list_saved_files. Сначала посмотри первые строки без фильтров, выбери лист '
    'и буквы нужных колонок. query ищет по всей строке; filters — условия по колонкам, объединяемые И. '
    'Например filters=[{column: B, operator: contains, value: нужная группа}]. '
    'Числовые сравнения gt/gte/lt/lte не подходят для нераспознанных дат Excel; '
    'страницы выбирай offset/limit, колонки — columns. Не переноси заголовки из одного листа на другой. '
    'Ты умеешь читать таблицы XLSX/ODS/CSV/TSV: сначала найди файл через list_downloads или '
    'list_user_files/list_saved_files, затем read_download(file_id,page=1), read_user_file или read_file '
    'с соответствующим ID. XLSX разбирается готовым Python-инструментом: листы, адреса ячеек, '
    'общие/встроенные строки, числа и сохранённые значения формул. Не пытайся читать XLSX как обычный текст '
    'или многократно разбирать hex; сначала используй штатное чтение файла. '
    'Сопоставляй колонки по заголовкам, сохраняй связь строки с листом/адресом ячейки, '
    'проверяй единицы измерения и пустые значения. Показывай результат компактной Markdown-таблицей '
    'с понятными заголовками, только нужными строками и кратким выводом. '
    'Формулы не пересчитываются, оформление и объединённые ячейки могут не сохраняться в тексте, '
    'даты Excel могут возвращаться числами: не угадывай даты без формата/подтверждения. '
    'Не утверждай, что прочитал весь большой файл, если результат усечён. Старый XLS не поддерживается: '
    'попроси XLSX/ODS/CSV, сообщи об ограничении. Новые результаты можно сохранять как CSV в Silo '
    'через export_table либо write_silo_file. Инструмента редактирования/создания XLSX сейчас нет: '
    'не обещай изменения файла, при необходимости сообщи missing_capability. '
    'Для больших таблиц не разбирай ZIP вручную: inspect_table показывает структуру всей таблицы, '
    'search_table ищет по всем строкам оригинала, summarize_table считает статистику/группы, '
    'export_table фильтрует, сортирует и сохраняет CSV без изменения исходника. '
    'Укажи header_row, чтобы исключить заголовки из сводки и экспорта. '
    'Источники таблиц: source_task_id=0/file_id загрузки браузера; source_task_id>0/file_id вложения; '
    'либо silo_path из list_user_files. Неиспользуемые file_id/silo_path пустые. '
    'Silo — постоянная общая библиотека текущего аккаунта: list_user_files показывает файлы, '
    'read_silo_file читает документ и возвращает version, save_file_to_silo сохраняет оригинал '
    'загрузки браузера, write_silo_file сохраняет текст/Markdown/CSV/JSON. Сохраняй полезные '
    'источники и результаты для повторного использования, затем сообщай путь. zip_files объединяет '
    'от 2 до 50 личных файлов по точным путям в новый ZIP, исходники остаются без изменений. '
    'Не сохраняй секреты. '
    'Удаляй через delete_silo_file только личные файлы по точному path и только по прямой просьбе владельца; '
    'файлы задач и память этим инструментом не удаляются. '
    'Для нового пути version пустая; существующий файл меняй только по просьбе владельца '
    'с актуальной версией после чтения, иначе используй новый путь. '
    'PDF можно смотреть постранично картинками. Бинарные данные разбирай python_binary: ZIP/Office XML '
    'или struct с известным форматом; не угадывай устройство неизвестного формата. '
    'Если ответ сообщает, что формат не поддерживается, не повторяй hex/struct без известной схемы; '
    'найди другой формат или сообщи владельцу о препятствии. '
    'Не отправляй изменяющие данные запросы в ЛК; POST допустим только для чтения/поиска. ')


def tool(name, description, properties):
    return {'type': 'function', 'name': name, 'description': description, 'strict': True,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}}


TABLE_TOOL = tool('search_table', 'Искать и фильтровать XLSX/ODS/CSV/TSV. source_task_id=0 — загрузка браузера, иначе вложение задачи из list_saved_files. sheet пустой — все листы. query — поиск подстроки во всей строке без учёта регистра. filters объединены И; column — буквы Excel (A, B, AA), operator — сравнение; value пустой для empty/not_empty. columns=[] — первые 50 колонок. offset/limit — страницы совпадений. Для знакомства вызови без фильтров, limit=5: ответ содержит листы и первые строки с заголовками.', {
    'source_task_id': {'type': 'integer', 'minimum': 0}, 'file_id': {'type': 'string'},
    'sheet': {'type': 'string', 'maxLength': 200}, 'query': {'type': 'string', 'maxLength': 500},
    'filters': {'type': 'array', 'maxItems': 20, 'items': {'type': 'object', 'properties': {
        'column': {'type': 'string', 'pattern': '^[A-Z]{1,3}$'},
        'operator': {'type': 'string', 'enum': ['contains', 'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'empty', 'not_empty']},
        'value': {'type': 'string', 'maxLength': 500}}, 'required': ['column', 'operator', 'value'], 'additionalProperties': False}},
    'columns': {'type': 'array', 'maxItems': 50, 'items': {'type': 'string', 'pattern': '^[A-Z]{1,3}$'}},
    'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000000}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
})


TABLE_SOURCE = {key: TABLE_TOOL['parameters']['properties'][key] for key in ('source_task_id', 'file_id')}
TABLE_SOURCE['silo_path'] = {'type': 'string', 'maxLength': 500}
TABLE_TOOL['parameters']['properties']['silo_path'] = TABLE_SOURCE['silo_path']
TABLE_TOOL['parameters']['required'].append('silo_path')
TABLE_SELECTION = {**TABLE_SOURCE, **{key: TABLE_TOOL['parameters']['properties'][key] for key in ('sheet', 'query', 'filters')},
                   'header_row': {'type': 'integer', 'minimum': 0}}
TABLE_TOOLS = [TABLE_TOOL,
    tool('inspect_table', 'Структура всей таблицы: листы, число строк, последний номер, колонки, первые строки. Без усечения файла до первых 200 строк. Источник: silo_path или source_task_id/file_id; неиспользуемые строки пустые, source_task_id=0 для загрузки браузера.', TABLE_SOURCE),
    tool('summarize_table', 'Сводка по отфильтрованным строкам: количество, пустые, числовые сумма/среднее/минимум/максимум. column — буквы нужного столбца; group_by — колонка группировки или пустая строка. header_row — номер заголовка, его и предыдущие строки пропустить. До 200 групп.', {
        **TABLE_SELECTION, 'column': {'type': 'string', 'pattern': '^[A-Z]{1,3}$'}, 'group_by': {'type': 'string', 'pattern': '^([A-Z]{1,3})?$'}}),
    tool('export_table', 'Отобрать строки и колонки, отсортировать и сохранить результат CSV в Silo отдельным новым файлом. source_sheet/source_row сохраняют происхождение строк. columns=[] — все, sort_by пустой — исходный порядок, numeric_sort — сортировать числа. header_row пропускает заголовок/преамбулу. output_path должен оканчиваться .csv; существующий файл не заменяется. Исходный XLSX не изменяется.', {
        **TABLE_SELECTION, 'columns': TABLE_TOOL['parameters']['properties']['columns'],
        'sort_by': {'type': 'string', 'pattern': '^([A-Z]{1,3})?$'}, 'numeric_sort': {'type': 'boolean'}, 'descending': {'type': 'boolean'},
        'output_path': {'type': 'string', 'maxLength': 500, 'pattern': '[.][cC][sS][vV]$'}}),
]
TABLE_NAMES = {item['name'] for item in TABLE_TOOLS}


FILE_TOOLS = [
    *TABLE_TOOLS,
    tool('download_file', 'Скачать через Python только точный готовый URL файла из источника, когда достаточно простого запроса. Не угадывай адрес; при сомнении нажми кнопку скачивания в браузере. Оригинал сохраняется.', {'url': {'type': 'string'}}),
    tool('python_http_request', 'Прямой HTTP-запрос через Python, только если точные URL, метод и параметры уже подтверждены источником и простой запрос заведомо подходит. Не для проб и подбора API. GET или POST для чтения/поиска. Тело — строка, content_type — MIME. Пустые строки для GET. Возвращает текст или сохраняет бинарный файл. Cookie подставляются без передачи модели.',
         {'url': {'type': 'string'}, 'method': {'type': 'string', 'enum': ['GET', 'POST']}, 'body': {'type': 'string'}, 'content_type': {'type': 'string', 'enum': ['', 'application/json', 'application/x-www-form-urlencoded', 'text/plain']}}),
    tool('list_downloads', 'Показать сохранённые файлы текущего пользователя, общие для Straus и Monocle.', {}),
    tool('read_download', 'Прочитать сохранённый файл через Python. Для PDF page начинается с 1, возвращается также картинка страницы. Для других файлов page=1.', {'file_id': {'type': 'string'}, 'page': {'type': 'integer', 'minimum': 1}}),
    tool('python_binary', 'Разобрать сохранённый бинарный файл через Python. mode=hex — байты; struct — struct.unpack по format; zip — список архива, либо чтение member. Для неиспользуемых строк передай пустые. offset — смещение байтов.',
         {'file_id': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['hex', 'struct', 'zip']}, 'format': {'type': 'string'}, 'member': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0}}),
]
LIBRARY_TOOLS = [
    tool('zip_files', 'Создать новый ZIP-архив из 2–50 личных файлов Silo по точным путям из list_user_files. Архив сохранится в output_path; исходники не изменяются. Суммарный размер исходников до 20 МБ, готовый архив до 10 МБ. Укажи новый путь с расширением .zip.', {
        'paths': {'type': 'array', 'minItems': 2, 'maxItems': 50, 'items': {'type': 'string', 'maxLength': 500}},
        'output_path': {'type': 'string', 'maxLength': 500, 'pattern': '[.][zZ][iI][pP]$'},
    }),
    tool('read_silo_file', 'Прочитать личный файл Silo по path из list_user_files: текст/Office, изображение или страница PDF. Возвращает version для безопасного сохранения. page начинается с 1.', {'path': {'type': 'string', 'maxLength': 500}, 'page': {'type': 'integer', 'minimum': 1}}),
    tool('delete_silo_file', 'Удалить личный файл Silo по точному path из list_user_files. Удаляет файл без возможности восстановления; не принимает папки, вложения задач или файлы памяти. Используй только по прямой просьбе владельца.', {'path': {'type': 'string', 'maxLength': 500}}),
    tool('write_silo_file', 'Создать или изменить личный текстовый файл в Silo: заметку, Markdown, CSV, JSON. version пустая для нового файла, иначе версия из read_silo_file/read_user_file. Существующий файл без правильной версии не заменяется.', {'path': {'type': 'string', 'maxLength': 500}, 'text': {'type': 'string', 'maxLength': 100000}, 'version': {'type': 'string', 'pattern': '^([a-f0-9]{64})?$'}}),
    tool('save_file_to_silo', 'Сохранить оригинал загрузки браузера в личную папку Silo для следующих поручений. file_id из list_downloads. version пустая для нового пути; для замены нужна версия из read_silo_file. Байты передаются между сервисами без участия модели.', {'file_id': {'type': 'string'}, 'path': {'type': 'string', 'maxLength': 500}, 'version': {'type': 'string', 'pattern': '^([a-f0-9]{64})?$'}}),
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
    if name in TABLE_NAMES:
        from common.library_client import library_request
        progress.add({'search_table': 'Ищет и фильтрует строки таблицы', 'inspect_table': 'Проверяет структуру и размер таблицы',
                      'summarize_table': 'Считает сводку по таблице', 'export_table': 'Отбирает и сортирует строки в отдельный файл Silo'}[name])
        if args.get('silo_path') or args.get('source_task_id'):
            result = await library_request(name, args)
            return result['output'], []
        output = await browser_request('/files', {'action': name, **args})
        if name == 'export_table':
            result = await library_request('save_file_to_silo', {'path': args['output_path'], 'content': output.pop('content'), 'version': ''})
            return result['output'] | {'rows': output['rows']}, []
        return output, []
    if name == 'save_file_to_silo':
        from common.library_client import library_request
        progress.add('Сохраняет оригинал файла в Silo')
        file = await browser_request('/files', {'action': 'export_download', 'file_id': args['file_id']})
        result = await library_request(name, {'path': args['path'], 'version': args['version'], 'content': file['content']})
        return result['output'], []
    if name == 'read_silo_file':
        from common.library_client import library_request
        progress.add('Читает файл из Silo')
        result = await library_request(name, args)
        output = await browser_request('/read-content', result['file'] | {'page': args['page']})
        return await file_result(name, output | result['output'], progress)
    if name in LIBRARY_NAMES:
        from common.library_client import library_request
        progress.add({'zip_files': 'Собирает выбранные файлы в ZIP-архив', 'write_silo_file': 'Сохраняет текстовый файл в Silo', 'delete_silo_file': 'Удаляет личный файл из Silo', 'list_user_files': 'Просматривает личные папки и файлы', 'read_user_file': 'Читает личный файл',
                      'list_memory': 'Просматривает сохранённые записи задач', 'read_memory': 'Читает сохранённую запись задачи',
                      'list_saved_files': 'Ищет сохранённые документы', 'read_file': 'Читает выбранный документ',
                      'view_pdf_page': 'Просматривает страницу PDF'}[name])
        result = await library_request(name, args)
        return result['output'], result.get('content', [])
    progress.add({'download_file': 'Скачивает файл через Python', 'python_http_request': 'Отправляет HTTP-запрос через Python',
                  'read_download': 'Читает сохранённый файл', 'list_downloads': 'Просматривает сохранённые загрузки',
                  'python_binary': 'Разбирает бинарный файл через Python'}[name])
    output = await browser_request('/files', {'action': name, **args})
    return await file_result(name, output, progress)


async def file_result(name, output, progress):
    if output.get('status') == 'unsupported_file':
        from common.agent_issues import report_problem
        reported = getattr(progress, 'unsupported_formats', set())
        format_name = output.get('format', 'unknown_binary')
        if format_name not in reported:
            result = await report_problem({'tool_name': name, 'category': 'unsupported_file',
                'description': 'Штатный читатель не поддерживает формат ' + format_name}, progress.record['agent'])
            if result.get('reported'):
                reported.add(format_name)
                progress.unsupported_formats = reported
                progress.add('Неподдерживаемый формат: сообщение передано администратору')
            else:
                progress.add('Не удалось сообщить администратору о неподдерживаемом формате')
    image = output.pop('image', None)
    content = [{'type': 'input_image', 'image_url': f"data:{output.get('mime', 'image/jpeg')};base64,{image}", 'detail': 'high'}] if image else []
    return output, content
