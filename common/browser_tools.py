"""Одинаковые инструменты и вызовы браузера для Monocle и Straus."""
VISUAL_ACTIONS = {
    'browser_screenshot': 'screenshot', 'browser_click': 'click',
    'browser_type': 'type', 'browser_press': 'press', 'browser_scroll': 'scroll',
    'browser_navigate': 'open', 'browser_view_image': 'image',
}

# Небольшие отдельные инструменты проще вызывать, чем один с десятком лишних полей.
def visual_tool(name, description, properties):
    return {'type': 'function', 'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(properties),
                           'additionalProperties': False}, 'strict': True}


BROWSER_TOOLS = [
    visual_tool('browser_wait_for_user', 'Попросить владельца пройти CAPTCHA/2FA и передать ему браузер. message — понятная просьба: что мешает, что нужно сделать в увеличенном браузере и нажать «Готово — продолжить работу агента». Просьба попадёт в журнал. Вызов ждёт владельца без таймера; после возврата перечитай страницу. Не обходи капчу сам.', {'message': {'type': 'string', 'minLength': 1, 'maxLength': 500}}),
    visual_tool('browser_screenshot', 'Получить снимок текущего браузера. full_page=true показывает всю страницу, false — видимую область 1100×750.',
                {'full_page': {'type': 'boolean'}}),
    visual_tool('browser_click', 'Нажать элемент. Предпочитай target=element по id из последнего elements (id передай в name), либо role + точное name. text — точный текст. css — CSS-селектор HTML-элемента, например #submit, button.download или a[href$=".pdf"]. xpath — XPath, например //button[@type="submit"]. Выражение передай в selector. selector — прежний CSS/Playwright-вариант. coordinates — запасной вариант. Для неиспользуемых строк передай пустую строку, x/y=0.',
                {'target': {'type': 'string', 'enum': ['element', 'role', 'text', 'css', 'xpath', 'selector', 'coordinates']},
                 'role': {'type': 'string', 'maxLength': 40}, 'name': {'type': 'string', 'maxLength': 500},
                 'selector': {'type': 'string', 'maxLength': 1000},
                 'x': {'type': 'integer', 'minimum': 0, 'maximum': 1099}, 'y': {'type': 'integer', 'minimum': 0, 'maximum': 749}}),
    visual_tool('browser_type', 'Ввести обычный текст в выбранное поле, например поиск. Пароли вводит browser_login, коды 2FA — владелец.',
                {'text': {'type': 'string', 'maxLength': 2000}}),
    visual_tool('browser_press', 'Нажать клавишу в браузере.',
                {'key': {'type': 'string', 'enum': ['Enter', 'Tab', 'Escape', 'Backspace', 'Control+A', 'ArrowDown', 'ArrowUp']}}),
    visual_tool('browser_scroll', 'Прокрутить текущую страницу и получить новый снимок. Положительное delta — вниз.',
                {'delta': {'type': 'integer', 'minimum': -1500, 'maximum': 1500}}),
    visual_tool('browser_navigate', 'Открыть URL либо перейти назад/вперёд. Для back/forward передай пустой url.',
                {'action': {'type': 'string', 'enum': ['open', 'back', 'forward']}, 'url': {'type': 'string', 'maxLength': 2000}}),
    visual_tool('browser_view_image', 'Посмотреть изображение целиком: index из списка images на странице. Либо открой прямой URL изображения (index=0). Для текущей страницы url пустой. Скачанные файлы и PDF доступны через read_download.',
                {'index': {'type': 'integer', 'minimum': 0, 'maximum': 99}, 'url': {'type': 'string', 'maxLength': 2000}}),
    {'type': 'function', 'name': 'browser_sites', 'description': 'Список сохранённых личных кабинетов текущего пользователя: номера, названия и адреса без паролей. Сам выбери подходящий ЛК для задачи.',
     'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, 'strict': True},
    {'type': 'function', 'name': 'browser_login', 'description': 'Войти в личный кабинет по site_id из browser_sites. Браузер сам подставляет сохранённые данные, пароли модели недоступны.',
     'parameters': {'type': 'object', 'properties': {'site_id': {'type': 'string'}}, 'required': ['site_id'], 'additionalProperties': False}, 'strict': True},
    {'type': 'function', 'name': 'browser_read', 'description': 'Прочитать текущую страницу личного кабинета по site_id из browser_sites и список ссылок с номерами. Если требуется вход, используй browser_login.',
     'parameters': {'type': 'object', 'properties': {'site_id': {'type': 'string'}}, 'required': ['site_id'], 'additionalProperties': False}, 'strict': True},
    {'type': 'function', 'name': 'browser_follow', 'description': 'Прочитать страницу по номеру ссылки из последнего browser_read. Только чтение условий задач; не выбирай выход, удаление, отправку или изменение данных.',
     'parameters': {'type': 'object', 'properties': {'site_id': {'type': 'string'}, 'index': {'type': 'integer', 'minimum': 0, 'maximum': 99}},
                    'required': ['site_id', 'index'], 'additionalProperties': False}, 'strict': True},
    {'type': 'function', 'name': 'browser_download', 'description': 'Скачать документ по номеру ссылки из последнего browser_read с авторизацией выбранного ЛК. Скачать оригинал в текущей сессии для сохранения и чтения. До 10 МБ; PDF, DOCX, XLSX, PPTX, текст и изображения.',
     'parameters': {'type': 'object', 'properties': {'site_id': {'type': 'string'}, 'index': {'type': 'integer', 'minimum': 0, 'maximum': 99}},
                    'required': ['site_id', 'index'], 'additionalProperties': False}, 'strict': True},
    {'type': 'function', 'name': 'web_search', 'description': 'Поиск общедоступной информации через Google. Не включай в запрос пароли, личные переписки и приватное содержимое ЛК.',
     'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'maxLength': 500}}, 'required': ['query'], 'additionalProperties': False}, 'strict': True},
    {'type': 'function', 'name': 'web_read', 'description': 'Открыть страницу в общем браузере владельца и прочитать текст. Сессии ЛК сохраняются.',
     'parameters': {'type': 'object', 'properties': {'url': {'type': 'string', 'maxLength': 2000}}, 'required': ['url'], 'additionalProperties': False}, 'strict': True},
]


BROWSER_TOOLS.append(visual_tool('read_page',
    'Прочитать URL или перейти по index предыдущего ответа. Пустые site_id/url — текущий браузер. Для action=read index=0; для follow url пустой.',
    {'site_id': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['read', 'follow']},
     'url': {'type': 'string'}, 'index': {'type': 'integer', 'minimum': 0, 'maximum': 99}}))
BROWSER_NAMES = {tool['name'] for tool in BROWSER_TOOLS}


async def run_browser_tool(name, args, request, progress, known_elements):
    if name == 'browser_wait_for_user':
        import json
        message = args.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise ValueError('Укажите message: что нужно сделать владельцу, до 500 символов')
        progress.note(json.dumps({'text': message}, ensure_ascii=False))
        progress.add('Ждёт, пока владелец завершит CAPTCHA или вход в браузере')
        return await request('/wait-user', {})
    if name == 'browser_sites':
        progress.add('Выбирает сохранённый вход')
        return await request('/sites', method='GET')
    if name in ('browser_login', 'browser_read', 'browser_follow', 'browser_download'):
        action = {'browser_login': 'login', 'browser_read': 'read', 'browser_follow': 'follow', 'browser_download': 'download'}[name]
        progress.add({'login': 'Входит через сохранённый вход', 'read': 'Читает текущую страницу',
                      'follow': 'Переходит по выбранной ссылке', 'download': 'Скачивает документ из браузера'}[action])
        return await request('/agent', {'site_id': args.get('site_id', ''), 'action': action, 'index': args.get('index', 0)})
    if name == 'read_page':
        action = args['action']
        if action not in ('read', 'follow'):
            raise ValueError('Для read_page доступны read и follow')
        values = {'site_id': args.get('site_id', '')}
        values.update({'url': args.get('url', '')} if action == 'read' else {'index': args['index']})
        progress.add('Читает страницу' if action == 'read' else 'Переходит по выбранной ссылке')
        return await request('/schedule/read', values)
    if name in ('web_search', 'web_read'):
        progress.add('Ищет в Google' if name == 'web_search' else 'Читает страницу')
        return await request('/web', {'action': 'search' if name == 'web_search' else 'read',
                                     'query': args.get('query', ''), 'url': args.get('url', '')})
    values = dict(args)
    action = values.pop('action', VISUAL_ACTIONS[name])
    if name == 'browser_press':
        values['text'] = values.pop('key')
    if name == 'browser_click':
        mode = args['target']
        if mode == 'element':
            label = known_elements.get(args.get('name'), 'элемент #' + str(args.get('name')))
        elif mode in ('css', 'xpath', 'selector'):
            label = args.get('selector', '')
        elif mode == 'coordinates':
            label = f"({args.get('x')}, {args.get('y')})"
        else:
            label = args.get('name', '')
        role = ' ' + args.get('role', '') if mode == 'role' else ''
        progress.add(f'Нажимает ({mode}{role}): {label[:500]}')
    else:
        labels = {'screenshot': 'Смотрит снимок браузера', 'type': 'Вводит текст', 'press': 'Нажимает клавишу',
                  'scroll': 'Прокручивает страницу', 'open': 'Открывает страницу', 'back': 'Возвращается назад',
                  'forward': 'Переходит вперёд', 'image': 'Рассматривает изображение'}
        progress.add(labels[action])
    output = await request('/visual', {'action': action, **values})
    if output.get('clicked'):
        progress.add('Нажат элемент: ' + output['clicked'])
    if output.get('status') == 'choose_element':
        progress.add('Уточняет элемент: ' + output['reason'])
    return output
