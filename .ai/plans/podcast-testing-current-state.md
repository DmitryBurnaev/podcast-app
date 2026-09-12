# Снимок исходного состояния тестов

Дата исследования: 2026-09-06
Ветка: `feature/10-stabilize-api-auth-structure`
HEAD: `9c380fa` — `[#10] API improvements and fixes: encapsulate database access`

Этот файл фиксирует состояние до миграции и не переписывается результатами
последующих этапов. Текущий прогресс ведётся в
[`podcast-testing-migration.md`](podcast-testing-migration.md).

## Сравнение с `podcast-service`

| Аспект | `podcast-service` | `podcast-app` |
| --- | --- | --- |
| Структура | 288 test-функций, 47 test-классов | 342 test-функции, 80 test-классов |
| Fixtures | 43 централизованных fixtures, реальные сущности PostgreSQL | 19 fixtures, detached model builders |
| Внешние границы | Общая библиотека классов-имитаторов YoutubeDL, Redis, S3, RQ, SMTP и HTTP | 4 общих mock-класса, локальные fakes, 351 строка с `SimpleNamespace` |
| API | Реальный HTTP и PostgreSQL, внешние сервисы подменены | HTTP есть, но repositories и UoW массово патчатся |
| Изоляция | Отдельная test DB, но session-scoped client/session и накопление данных | Function-scoped client, DB harness отсутствует |
| DI | Подмена конструкторов через общий `BaseMock` | DI только для settings/current user; UoW и внешние сервисы часто создаются внутри модулей |
| UI | Нет HTML/admin слоя | 23 view-теста и 30 admin-тестов, покрытие неравномерно |

Целевая архитектура сохраняет сильные стороны `podcast-service`: class-based
imitators, богатые fixtures и реальные доменные записи. Не переносится
глобальное патчирование `__init__`, общие изменяемые mocks и опасное удаление
БД.

## Baseline

| Команда | Результат |
| --- | --- |
| `uv run pytest --collect-only -q` | 385 сценариев собраны; collection error в `src/tests/utils/test_root_utils.py`: импортируется удалённый `log_message` |
| `coverage run -m pytest -q --ignore=src/tests/utils/test_root_utils.py` | 266 passed, 34 failed, 85 errors |
| Coverage отчёт обходного прогона | 69% line coverage; показатель недостоверен до зелёного запуска |

Разрез обходного прогона:

- API: 17 passed, 8 failed, 85 errors; общий app fixture патчит удалённый
  `src.main.get_current_user`.
- Views: 21 passed, 11 failed; тесты используют устаревшие зависимости и
  сигнатуры контроллеров.
- Admin: 31 passed.
- Services/tasks: 105 passed, 15 failed.
- Auth/utils/repositories: 92 passed.

## Наблюдаемые риски

- `src/tests` исключён из mypy; итоговый coverage gate отсутствует.
- Tests job в `.github/workflows/tests.yaml` закомментирован.
- `../testing/knowledge-base.md` уже требует реальный PostgreSQL для
  functional tests, class-based fakes для внешних границ и прямую проверку
  template/context для views; реализация отстаёт от этих правил.
- Наиболее низкое ориентировочное покрытие: admin dashboard, DB dependencies,
  repositories/session, API episodes/podcasts, views episodes, email и worker.
