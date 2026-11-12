import httpx
from common.identity import user_id
from common.rpc import internal_token

ISSUE_TOOLS = ['plan_rest', 'inspect_table', 'summarize_table', 'export_table', 'read_silo_file', 'write_silo_file', 'delete_silo_file', 'save_file_to_silo', 'search_table', 'browser_wait_for_user', 'browser_click', 'browser_move', 'browser_read', 'browser_login', 'browser_sites', 'browser_follow', 'browser_download', 'browser_screenshot', 'browser_navigate', 'browser_type', 'browser_press', 'browser_scroll', 'browser_view_image', 'read_page', 'web_read', 'web_search', 'download_file', 'python_http_request', 'read_download', 'list_downloads', 'python_binary', 'read_file', 'view_pdf_page', 'reuse_file', 'read_user_file', 'list_saved_files', 'calendar_list', 'calendar_save_events', 'calendar_remove_events', 'calendar_timezone', 'calendar_finish', 'plan_tasks', 'reorder_tasks', 'request_context_check', 'context_analysis', 'calendar_edit', 'list_user_files', 'list_memory', 'read_memory', 'other']

ISSUE_TOOL = {'type': 'function', 'name': 'report_tool_problem', 'strict': True,
    'description': 'Сообщить администратору о техническом препятствии. Передай инструмент, категорию и краткое техническое описание проблемы или недостающей возможности. Без документов, паролей, URL и других личных данных. Не сообщает об обычной нехватке контекста.',
    'parameters': {'type': 'object', 'properties': {'tool_name': {'type': 'string', 'enum': ISSUE_TOOLS},
        'category': {'type': 'string', 'enum': ['timeout', 'element_not_found', 'login_failed', 'unsupported_file', 'invalid_format', 'service_unavailable', 'missing_capability', 'other']}, 'description': {'type': 'string', 'maxLength': 500}},
        'required': ['tool_name', 'category', 'description'], 'additionalProperties': False}}
ISSUE_INSTRUCTION = ('Если работа упёрлась в техническую ошибку или неподдерживаемый формат, '
    'вызови report_tool_problem, чтобы администратор увидел проблему. '
    'Если для поручения не хватает функции, обязательно сообщи category=missing_capability, '
    'tool_name ближайшего инструмента или other, description — какая возможность нужна и зачем, '
    'обобщённо, без личных данных и содержимого страниц. Для ошибки опиши конкретное ограничение. '
    'Сообщай один раз за проблему, не вместо разумной попытки исправления. '
    'Сначала объясни владельцу, что не получилось и как он может помочь. '
    'Обычный запрос 2FA, уточнение группы или необходимость приложить файл не требуют '
    'обращения к администратору. Технический отчёт не заменяет пояснение владельцу '
    'и не означает, что ошибка исправлена. ')


async def report_problem(arguments, agent):
    try:
        async with httpx.AsyncClient(timeout=15, transport=httpx.AsyncHTTPTransport(retries=1)) as client:
            response = await client.post('http://backend:8000/api/auth/agent-issues/internal',
                json=arguments | {'agent': agent}, headers={'X-Service-Token': internal_token('context'), 'X-User-ID': str(user_id())})
            if not response.is_success:
                return {'error': 'Не удалось передать проблему администратору'}
            return response.json()
    except httpx.HTTPError:
        return {'error': 'Не удалось подключиться к администраторскому журналу'}
