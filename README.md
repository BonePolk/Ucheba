# Task Manager

Минимальный персональный менеджер задач на React, Mantine, TanStack Query, FastAPI и SQLModel.

## Запуск

Нужны Docker и Docker Compose:

```sh
docker compose up -d --build
```

Откройте http://localhost:8080. Наружу опубликован только Nginx; он отдаёт frontend и проксирует `/api` на backend.

```sh
docker compose down
```

SQLite хранится в volume `task-data` и сохраняется после остановки контейнеров. Команда `docker compose down -v` удаляет также базу.

## Структура

- `backend/main.py` — REST API, фильтры, сортировка и создание таблиц при запуске.
- `backend/models.py` — таблица задач и схемы валидации.
- `frontend/src/App.jsx` — список задач и фильтры.
- `frontend/src/TaskForm.jsx` — общая форма создания и редактирования.
- `frontend/src/api.js` — запросы к API.
- `frontend/nginx.conf` — раздача сборки и проксирование.
- `docker-compose.yml` — два контейнера и volume SQLite.

## API

`GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks`, `PATCH /api/tasks/{id}`, `DELETE /api/tasks/{id}`.

Фильтры списка: `status=todo|in_progress|done`, `priority=low|medium|high`, `category=Название`.
Сортировка: `sort_by=deadline|priority|created_at`, `order=asc|desc`.
При сортировке по приоритету `asc` означает сначала высокий. Задачи без дедлайна всегда в конце.

Дедлайн передаётся как `YYYY-MM-DD`, временные метки создания и обновления сохраняются автоматически в UTC.
# Ucheba
