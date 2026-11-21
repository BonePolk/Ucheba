# Ucheba — Инструкции для coding agents

## Обзор проекта

Ucheba — персональный менеджер задач с AI-агентами и многопользовательской изоляцией:
- Веб-клиент (React SPA) -> Nginx (`:8080`) -> `backend` (FastAPI) -> внутренние сервисы агентов и ботов.
- Основные агенты: **Monocle** (сбор контекста), **Ameba** (планирование приоритетов и периодов), **Straus** (работа с постоянным календарём), **Arrodes** (исполнитель на `openai-codex` SDK).
- Поддерживаемые языки: Python (backend, common, services) и JavaScript без TypeScript (frontend). Отвечай пользователю по-русски, кратко и по существу.

## Структура репозитория

- `frontend/` — единый React-интерфейс (`src/App.jsx`, `TaskBoard.jsx`, `TaskCard.jsx`, `TaskForm.jsx`, `TaskAutomation.jsx`, `AmebaWorkspace.jsx`, `AgentTimeline.jsx`, `AgentProgress.jsx`, `Calendar.jsx`, `Files.jsx`, `Account.jsx`, `Admin.jsx`, `Integrations.jsx`, `Inbox.jsx`, `api.js`, `components/ui/`, `lib/`).
- `backend/` — публичный HTTP API (`main.py`, `routers/tasks.py`, `routers/calendar.py`, `routers/integrations.py`, `routers/users.py`), фоновый worker и оркестрация (`integrations/ameba.py`, `context.py`, `straus.py`, `arrodes.py`, `scheduler.py`, `jobs.py`, `worker.py`, `subtasks.py`, `telegram.py`).
- `common/` — общий слой для backend и сервисов: модели (`models/user.py`, `task.py`, `integration.py`, `context.py`), изоляция БД и миграции (`database.py`, `identity.py`, `migrate.py`, `migrations/`), настройки и шифрование (`settings.py`, `security.py`, `accounts.py`), межсервисный RPC/HTTP (`rpc.py`, `http.py`), Codex harness (`codex_harness.py`), общий прогресс и память (`progress.py`, `agent_memory.py`, `agent_history.py`, `agent_issues.py`, `agent_questions.py`), календарь и файлы (`calendar.py`, `schedule.py`, `browser_tools.py`, `file_tools.py`, `office.py`).
- `services/` — изолированные FastAPI-сервисы: `monocle`, `ameba`, `straus`, `arrodes`, `browser`, `telegram`, `collector`, `notifications`.
- `nginx/` — gateway (`nginx.conf`, проксирование `/` -> `frontend`, `/api` -> `backend`, сервисных путей -> `backend`).
- `tests/` — локальные unittest-наборы (`test_ameba_planning.py`, `test_browser_tools.py`, `test_monocle_tools.py`, `test_straus_tools.py`, `run_agent_tests.py`).

## Архитектура

- Единственный внешний порт: `8080` в `nginx`. Сервисы общаются по внутренней сети Docker Compose; межсервисные запросы подписываются HMAC `INTERNAL_SECRET` + `X-User-ID` (`common/rpc.py`, `common/http.py`).
- `common/runtime.for_users` запускает фоновые циклы (`worker.run`, `telegram.monitor`, `collector`, `notifications`) в контексте каждого пользователя через `ContextVar` `current_user_id`.
- `backend/integrations/scheduler.py`: Monocle, Straus и Arrodes работают через взаимное исключение браузерных/рабочих слотов своего аккаунта (`WORK_PROVIDERS`); Ameba ждёт их завершения и прерывается при новом рабочем запросе своего владельца (`interrupt_ranking`). Чужие аккаунты независимы.
- `common/progress.py` ведёт журнал в RAM сервиса и отдаёт события через SSE (`GET /api/integrations/progress/stream`); `AgentProgress.jsx` хранит полученные события в TanStack Query cache по `run.id`.
- `backend/integrations/jobs.py` запускает долгие UI-операции с `background=true` (`agentRequest` в `api.js` опрашивает `/api/integrations/jobs/{job_id}`).

## AI-агенты

- **Род агентов**: `Ameba` — женский род («проверила», «спланировала»); `Monocle`, `Straus`, `Arrodes` — мужской род («проверил», «нашёл»).
- **Monocle** (`services/monocle`): исследует задачу (файлы Silo, `browser`, `web_search`), задаёт вопросы (`ask_user`), обновляет `TaskContext` и подзадачи.
- **Ameba** (`services/ameba`): LLM-first планировщик. Модель сама оценивает приоритеты, оставшийся объём (`remaining_minutes`), гибкие периоды (`work`/`rest`, `start_date..end_date`, `estimated_minutes`), явные `blocked_dates`, привязки к контексту и область пожеланий (`once`/`task`/`persistent`) через единственный инструмент планирования `propose_plan` (плюс `record_progress` для инкрементального обновления подтверждённого прогресса только после явного подтверждения владельца через `ask_user`, `get_task_details` с адаптивным размером выдачи и `context_offset` для длинных отчётов, `request_context_check`, `read_user_memory`, `agent_note`, `report_tool_problem`); объём передаваемых деталей задач и предыдущего плана в первом сообщении адаптируется к подтверждённому контекстному окну модели и эффективному лимиту провайдера (`common/codex_harness.py:detect_model_capabilities`, `config.context_window`, безопасный компактный fallback при неизвестном окне и переключении после `contextWindowExceeded`). Python в `validate_proposal` проверяет только объективные ограничения (валидность ID, дат, периоды целиком на закрытых днях, реальные будущие сроки `Task.deadline`, `task_pins`), с максимум одной повторной попыткой исправления и сохраняет план `schedule.version == 3` без искусственного разбиения по дням (`daily_allocations: []`) и с честным статусом `no_objective_conflicts` / `feasibility_status`. Детерминированный `/refresh` адаптирует план под новый прогресс, явные сдвиги (`mode='shift'`) и пины и повторно проверяет ограничения через `validate_proposal` (HTTP 409 при конфликте) без порчи сохранённого результата. Не меняет постоянный календарь Straus и не завершает задачи автоматически.
- **Straus** (`services/straus`): редактирует постоянный `CalendarConfig` (`calendar_list`, `calendar_save_events`, `calendar_remove_events`, `calendar_timezone`, `calendar_finish`) с атомарной проверкой `revision`.
- **Arrodes** (`services/arrodes`): запускает `openai-codex` SDK в sandbox от имени `UID = 10000 + user_id` с MCP stdio-сервером Ucheba; переводит задачу в `review`, никогда автоматически в `done`.
- **Удалённые интеграции**: Gemini и старый локальный сервис Jev/Jeba удалены, автоматический AI-разбор Telegram удалён. **Не восстанавливай их.**
- Все интеграции подчиняются общим переключателям администратора (`common/accounts.py`) и персональным настройкам пользователя.

## Изоляция данных и пользователей

- `./data/tasks.db` хранит реестр `User`, общие `Feature`, `BotTokenClaim`, `AgentIssue` и данные аккаунта `admin` (`id=1`).
- Остальные аккаунты получают отдельные SQLite-файлы `./data/users/<id>.db` (или схемы `user_<id>` в PostgreSQL).
- Весь код запросов и фоновых задач обязан работать в контексте владельца через `common.identity.user_id()`, `common.database.get_engine()` и `common.runtime.for_users()`. Не допускай чтения/записи данных другого пользователя.
- Пароли хешируются Argon2; сессия — подписанная `HttpOnly`, `SameSite=Strict` cookie `user_session` с проверкой `session_version`.
- Секреты (API-ключи, токены ботов, сессия Telegram) шифруются Fernet (`INTEGRATION_ENCRYPTION_KEY`) и никогда не возвращаются в API открытым текстом (только `configured.<field>`). `LUNA_API_KEY` используется только для одноразового импорта аккаунту `admin`.
- Файлы в Silo изолированы префиксами `users/<id>/`; браузерные профили и Codex home разделены по `user_id` (`10000 + user_id`).

## Правила разработки

- **KISS**: не вводи лишние классы, слои абстракций, брокеры, очереди или внешние зависимости без прямой необходимости.
- Перед изменениями сверяйся с реальным кодом, а не с устаревшими описаниями.
- Не переписывай исправные посторонние подсистемы.
- Сохраняй совместимость существующих SQLite-баз; любые изменения моделей должны корректно работать со старыми данными через Alembic-миграции (`common/migrations`) без потери записей.
- Проект использует Compose Watch; изменения исходников применяются пользователем. Сообщай, если правка требует пересборки образа, обновления Compose или ручной миграции.

## Запрещённые операции

- **Не запускай Docker / `docker compose` и не перезапускай сервисы.** Пользователь управляет контейнерами самостоятельно.
- **Не запускай сборки (`npm run build`, `vite build`) и тесты (`unittest`, `pytest`) без прямого разрешения пользователя.** Не добавляй новую тестовую инфраструктуру.
- **Не читай `.env` и пользовательские БД ради диагностики** и не выводи секреты/токены/пароли/сырые ответы провайдера в ответы, логи, комментарии или документацию.
- **Не предлагай `docker compose down -v`**, удаление `./data`, `volumes` или пересоздание баз.
- **Не подключай внешние AI/Telegram-интеграции без запроса.**
- **Не изменяй `docker-compose.yml` без крайней необходимости.**
- **Не возвращай `browser_javascript`** или обход CAPTCHA/2FA.
- **Не добавляй Redux, TypeScript, отдельный календарный движок или Mantine** (в UI используются Tailwind CSS v4 + локальные примитивы на Radix UI + TanStack Query).
- **Не заявляй о прохождении тестов, сборок или визуальных проверок, если они не выполнялись.**

## Интеграция с LLM и инструментами

- Все AI-агенты (`Monocle`, `Ameba`, `Straus`, `Arrodes`) используют общий Codex harness (`common/codex_harness.py`: `open_codex`, `start_codex_thread`, `run_codex_turn`, `resolve_session_capabilities`, `detect_model_capabilities`, `McpToolAdapter`, `serve_mcp`) и независимую настройку `context_window` (`None` = автоопределение с приоритетом подтверждённых ограничений провайдера/рантайма над более высоким ручным значением, `POST /api/integrations/agents/{name}/capabilities`).
- Все схемы инструментов (`strict: True`) обязаны иметь `additionalProperties: False`, все свойства перечислены в `required`, опциональные значения представляются как `type | null` без значений по умолчанию, мешающих JSON Schema.
- Учитывай особенность шлюза: не используй именованный `tool_choice={'type': 'function', 'name': ...}`.
- Скрытые рассуждения не передаются в UI; для публичных пояснений используется `NOTE_TOOL` (`agent_note`).
- `QUESTION_TOOL` (`ask_user`) приостанавливает агентский цикл на `asyncio.Future` до ответа владельца без лишних вызовов модели.
- `ISSUE_TOOL` (`report_tool_problem`) передаёт только имя инструмента, категорию и короткое описание без секретов/URL/личных данных.
- Логи ошибок (`uvicorn.error`) не должны содержать ключи, тела запросов или сырые тексты задач/ответов.

## Соглашения frontend

- Язык: JavaScript (JSX), без TypeScript.
- Стили и компоненты: тёмная тема (`#090909` фон, `#111111` карточки, `#303030` границы, `#EAEAEA`/`#929292` текст, фиолетовый акцент `#7F49B4`, прямые углы `rounded-none` / `--radius: 0px`), локальные компоненты из `@/components/ui/*` и анимации из `@/lib/motion` с учётом `useReducedMotion()`.
- Состояние сервера и кэш: `@tanstack/react-query` + `request()` / `agentRequest()` из `frontend/src/api.js`.
- Любые изменения UI должны быть удобны на узких мобильных экранах (без горизонтального скролла, доступные зоны касания).

## Соглашения backend

- Все `POST`/`PUT`/`PATCH`/`DELETE` запросы от клиента проверяются `UserSessionMiddleware` на `Content-Type: application/json`.
- Алиасы путей (`context`/`lucheba` -> `monocle`, `codex` -> `arrodes`) нормализуются в `UserSessionMiddleware` и `common.rpc.service_name()`.
- Обновление `CalendarConfig` защищено оптимистичной блокировкой по полю `revision` (HTTP 409 при устаревшей вкладке).
- Ameba хранит результат в `TaskRanking(id=1)` и временный срез в `IntegrationSetting(name='ameba_plan')`. Невалидный результат отклоняется до записи; при ошибке или отмене предыдущий корректный план сохраняется.

## Требования совместимости

- Сохранять старые ключи `IntegrationSetting` (`context`, `codex`, `jev` при наследовании), таблицу `codexrun` (`ArrodesRun`), путь `/home/codex` и имена volumes (`codex-home`, `codex-work`, `jev-models`, `silo-data`, `browser-data`).
- Сохранять совместимость API-алиасов `/context`, `/lucheba`, `/codex` и нормализацию старых имён агентов (`Lucheba`, `Codex`) при отображении.
- Поддерживать безопасное чтение старых планов `TaskRanking` и `TaskContext` без падений и без принудительного удаления пользовательских данных.

## Проверка изменений

Разрешённые статические проверки без запуска сервисов, тестов и сборок:
- `git diff --check`, `git status --short`, `git diff --stat`
- Проверка Python-синтаксиса через `ast.parse` без исполнения модулей
- Статическая проверка JS-импортов и структуры JSX

Тесты (`tests/run_agent_tests.py` или `unittest`) и сборки запускаются только по явному разрешению пользователя.

## Важные детали реализации

- `Task.completed_work_minutes` — единственный подтверждённый прогресс выполнения; прошедшее календарное время не уменьшает оставшийся объём работы автоматически.
- `common.calendar.ameba_events()` возвращает пустой список: гибкие периоды Ameba не записываются как почасовые встречи в постоянный календарь Straus.
- `AgentFact` (`remember_user_fact`) защищает записи владельца (`agent in ('Владелец', 'user')`) от перезаписи агентами (HTTP 422). В Ameba разовые пожелания (`scope='once'`) применяются только к текущему расчёту, задачные (`scope='task'`) пишутся в `IntegrationSetting('ameba_feedback').task_notes`, а устойчивые привычки (`scope='persistent'`) передаются в `backend/integrations/ameba.py` и сохраняются в память строго после успешного `commit` плана в БД.
- Актуальная сигнатура кэша Ameba в `backend/integrations/ameba.py`: `version=46`, формат плана `schedule.version == 3` (`AgentTimeline.jsx` и `TaskAutomation.jsx` сохраняют обратную совместимость чтения для `version == 2`, а `/refresh` обновляет только `version == 3`).
- `AgentProgress` поддерживает необязательные props `autoOpenOnActive`, `compactRunning` и `hideErrorNotice` (в `AmebaWorkspace.jsx` журнал по умолчанию закрыт, в работе отображается компактной строкой, а предыдущий план остаётся видимым при пересчёте).
