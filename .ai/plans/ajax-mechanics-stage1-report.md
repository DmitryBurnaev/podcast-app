# AJAX-механики: отчёт по этапу 1

Дата: 2026-10-14. Этап 1 выполнен; переход к этапу 2 ожидает подтверждения пользователя.

## Реализовано

- Добавлены внутренние `/web-api/*` controllers, использующие настоящую cookie-сессию.
  Bearer API `/api/*` сохранён. Браузерные controllers исключены из публичной OpenAPI-схемы.
- Подкасты и эпизоды используют общие application services. Старый POST `/episodes/`
  также передан общему сервису с сохранением ответа `{id}`.
- Cookie-мутации требуют `Origin`, совпадающий со схемой и хостом запроса,
  и `X-Requested-With: XMLHttpRequest`. Чужой, отсутствующий и `null` Origin запрещены.
- Отсутствующая/истёкшая/неактивная сессия возвращает JSON 401;
  `fragment=1` при отсутствии авторизации также не перенаправляет на страницу входа.
- Реализованы scoped CRUD, автозагрузка, download/cancel, прогресс и RSS status.
  Повторное и одновременное создание по одной ссылке возвращает один эпизод и
  не ставит его задачи повторно. Используются блокировки строк PostgreSQL.
- Удаление подкаста блокирует его и эпизоды; наличие активной загрузки приводит
  к 409 до удаления данных. Проверены откат и отсутствие частичного удаления.
- Отказ очереди при старте загрузки переводит сохранённый эпизод в ERROR и
  возвращает безопасный JSON 503. Отказ запроса отмены восстанавливает DOWNLOADING
  и допускает повтор. Ошибка запуска обложки не сбрасывает уже запущенную загрузку.
- Прогресс не делит на ноль, ограничивает процент диапазоном 0–100 и отдаёт
  конечный статус БД вместо устаревшего Redis-события.
- Исправлены episode filters: отдельные display/repository поля, pending → NEW,
  десятичные MB → байты (1 MB = 1024²), валидация диапазонов и общая фильтрация
  paginated/all запросов. Фильтры эпизодов больше не передаются PodcastRepository.
- Добавлены fragment templates для dashboard, podcast list/detail и episode
  list/detail. Общие Jinja blocks экспортируют именованные области данных;
  формы, модалки и плеер не включаются в fragment response. Подготовлен общий
  фрагмент статистики dashboard.
- Восстановлена тестовая инфраструктура: PostgreSQL требуется только functional
  fixtures; `TEST_DB_NAME` обязателен и не может совпадать с рабочей/системной БД;
  xdist workers получают разные имена. Убраны устаревший package import и
  несовместимые с SQLAlchemy 2.1 типы результатов. В Redis fake добавлена mget-граница.

## Контракт для этапа 2

Все изменяющие браузерные запросы отправляются с cookie (`credentials: same-origin`)
и заголовком `X-Requested-With: XMLHttpRequest`. Origin устанавливает браузер.
Схемы payload и entity responses совпадают с существующими API schemas.

| Метод | Маршрут | Назначение |
|---|---|---|
| GET, POST | `/web-api/podcasts/` | Список и создание подкаста |
| GET, PATCH, DELETE | `/web-api/podcasts/{id}/` | Детали, редактирование, удаление |
| GET, POST | `/web-api/podcasts/{id}/episodes/` | Эпизоды подкаста и создание по `sourceURL` |
| GET | `/web-api/episodes/` | Список эпизодов |
| GET, PATCH, DELETE | `/web-api/episodes/{id}/` | Детали, редактирование, удаление |
| PUT | `/web-api/episodes/{id}/download/` | Запуск загрузки |
| PUT | `/web-api/episodes/{id}/cancel-downloading/` | Запрос отмены |
| GET | `/web-api/progress/` | Активные загрузки, ответ `{progressItems: [...]}` |
| GET | `/web-api/progress/?episode_id={id}` | Включая конечное состояние конкретного эпизода |
| PUT | `/web-api/podcasts/{id}/generate-rss/` | Запуск RSS, ответ `{job_id}` |
| GET | `/web-api/podcasts/{id}/rss-task/` | `{job_id, status, error}` |

Создание — 201; удаление — 204 без тела; обычные ответы — 200.
Ошибки — `{error: {code, message, details}}`. Отказ постановки задач — 503.
Если создание сохранило эпизод, но задачи не запустились, `details.episode_id`
позволяет показать существующий объект и предложить повтор запуска загрузки.

RSS status принимает `queued`, `running`, `success`, `error`, `unknown`.
Отсутствующий/истёкший job или несовпадающие ID/аргументы дают `unknown`, а не успех.
RQ `finished` с результатом ERROR даёт `error`; traceback пользователю не возвращается.

`?fragment=1` поддерживается для `/`, `/podcasts/`, `/podcasts/{id}/`,
`/episodes/` и `/episodes/{id}/`. Фильтры сохраняются в query string.
Области помечены `data-refresh-region`: `overview`, `podcasts`, `podcast-summary`,
`episodes`, `episode-title`, `episode-state`, `episode-metadata`, `app-statistics`.
Клиент должен обновлять только подходящие области. `episode-state` содержит
`data-episode-id`, `data-status`, `data-audio-url`; создание/обновление плеера
и сохранение его позиции остаются задачей этапа 2.

## Проверки

Внешние сети, S3 и очередь заменяются fakes; PostgreSQL реальная и выделенная.
Тестовая база: `podcast_ajax_test`. Пароль в файлы проекта не записывался.

- Расширенный регрессионный набор API, views, services, tasks, utils: **393 passed**.
  Команда: `uv run pytest src/tests/api src/tests/views src/tests/services src/tests/tasks src/tests/utils -q`.
- Новые functional browser contracts: **52 passed**; новые view/fragment tests: **16 passed**.
- Views без PostgreSQL и без `TEST_DB_NAME`: **50 passed**.
- `uv run ruff check .`: passed.
- `uv run mypy .`: passed, 107 source files.
- `uv run mypy src/tests/api/test_web_api.py src/tests/views/test_episodes.py`: passed.
- `git diff --check`: passed.

Все uv-команды запускались через `/opt/homebrew/bin/uv` с
`UV_CACHE_DIR=/private/tmp/codex-uv-cache`, отдельными shell invocations.
Существующие auth tests выдают два предупреждения PyJWT о длине тестового ключа;
тесты проходят. Полный suite, admin и браузерная проверка остаются для этапа 3.

Отдельный запуск `uv run python` для компиляции шаблонов был отклонён
автоматической проверкой доступа: entry point не входит в lint/test allowlist.
Компиляция Jinja без рендера HTML проверена обычным тестом синтаксиса и блоков.

## Что осталось

- Этап 2: подключить формы/кнопки к `/web-api/*`, CSS-индикаторы, диалоги ошибок,
  DOM refresh, polling, плеер и первичную проверку во встроенном браузере.
- Этап 3: полный тестовый набор и пользовательские сценарии в двух браузерах.
- P6 выполнен частично; полное покрытие всех HTML views не заявляется.
- Миграции не создавались. Commit и push не выполнялись.
- Существовавшие до работы `.DS_Store` не изменялись и не входят в commit message.

## Commit message

```text
[#14] Add session-authenticated podcast and episode operations

- share application services between Bearer API and browser JSON routes
- handle queue failures, concurrent creation and RSS task results
- prepare refresh fragments and fix episode filtering and progress
- cover browser contracts with isolated PostgreSQL tests
```
