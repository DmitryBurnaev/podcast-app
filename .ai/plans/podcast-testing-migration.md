# Поэтапная миграция тестов

Статус актуален на 2026-09-10. Этот файл — рабочий журнал миграции;
канонический краткий план поддерживается в `docs/testing/plan.md`.

## Условные обозначения

- `[x]` — завершён: профильные и полные проверки прошли, результат записан.
- `[~]` — выполняется.
- `[ ]` — запланирован.

## Правило завершения этапа

Для каждого этапа: установить `[~]`, выполнить только его изменения, запустить
профильные тесты и полный coverage-прогон, обновить этот журнал и
`docs/testing/plan.md`, проверить `ruff`, применимый `mypy` и `git diff --check`.
После этого подготовить commit message в стиле `[#10] API improvements and fixes:`
и запросить явное согласование. Commit и push без согласования запрещены.

## Этапы

- [x] **D0. Зафиксировать исследование и исполнимый план.**
  - Создать текущий snapshot и этот живой чек-лист в `.ai/plans`.
  - Синхронизировать `docs/testing/plan.md` с этапами D0–P9 и включить
    views/admin.
  - Проверка: повторить collection baseline.
  - Предварительный commit message: `[#10] API improvements and fixes: document test migration baseline`.

- [x] **P0. Восстановить зелёный исходный набор.** Исправлены устаревшие imports,
  auth/app fixtures, сигнатуры views, job IDs, UoW, repository filters и сервисные
  контракты; collection, профильные тесты и полный coverage зелёные.

- [x] **P1. Изолированная PostgreSQL-инфраструктура.** Обязательный
  `TEST_DB_NAME`, отдельная БД на worker, migrations, очистка таблиц и typed
  entity fixtures; последовательный и xdist-запуски проверены.

- [x] **P2. Providers и class-based fakes (superseded).** Исторический этап
  добавил stateful fakes; app-level container и adapters заменяются P2R.

- [x] **P3. Podcasts API на PostgreSQL.** CRUD, ownership, pagination,
  image/RSS и fake side effects; доменный тест повторно зелёный.

- [x] **P4. Episodes, media и cookies API на PostgreSQL.** URL/upload creation,
  transitions, delete/cancel/download, cookie conflicts и media failures через
  PostgreSQL и stateful queue/Redis/storage/media-source fakes.

- [x] **P5. Auth, profile и system API на PostgreSQL.** Users/sessions/invites/
  tokens, profile/IP/access-token flows, admin role, refresh rotation и fake SMTP/health.

- [x] **P2R. Удаление app-level providers.** `make_app()` и production-код
  напрямую используют реальные constructors/functions; тестовая БД выбирается
  патчем session factory, внешние границы — BaseMock/monkeypatch fixtures.

- [ ] **P6. HTML views.** Template name и полный context без snapshots,
  episodes views, auth/ownership/errors/task effects и route smoke tests.

- [ ] **P7. SQLAdmin.** Dashboard, auth/roles, custom model workflows,
  readonly fields, redirects, alerts и partial storage cleanup.

- [ ] **P8. Services, tasks, repositories и lifecycle.** Убрать loose
  `SimpleNamespace`, закрыть retries/cancel/idempotency/transactions,
  PostgreSQL repository branches, email и worker.

- [ ] **P9. Типизация, coverage gate и CI.** Убрать тесты из mypy exclude,
  добавить markers, CI jobs и `fail_under=90`; проверить pytest, xdist, mypy,
  ruff и Docker/CI-equivalent запуск.

## Журнал результатов

| Этап | Дата | Профильный прогон | Полный прогон / coverage | Commit |
| --- | --- | --- | --- | --- |
| D0 | 2026-09-06 | `uv run pytest --collect-only -q`: 385 collected, 1 expected collection error | Baseline совпал со snapshot; coverage не запускался, потому что этап документационный | `222274d` — `[#10] API improvements and fixes: document test migration baseline` |
| P0 | 2026-09-06 | `pytest --collect-only -q`: 399 collected; API: 110 passed; views: 32 passed; services/tasks: 120 passed | `coverage run -m pytest -q --disable-warnings`: 399 passed; total coverage 79% | `39f0b59` — `[#10] API improvements and fixes: stabilize existing test suite` |
| P1 | 2026-09-06 | Docker PostgreSQL smoke: 2 passed; повторный `pytest -n 2`: 2 passed | `coverage run -m pytest -q --disable-warnings`: 401 passed, total coverage 80% | `d494bfd` — `[#10] API improvements and fixes: add isolated PostgreSQL test harness` |
| P2 | 2026-09-06 | `pytest src/tests/test_providers.py src/tests/admin/test_admin.py`: 14 passed; `mypy` providers/app/admin и `ruff` зелёные | Docker: `coverage run -m pytest -q --disable-warnings`: 408 passed, total coverage 80% | `df02774` — `[#10] API improvements and fixes: add typed test providers and service fakes` |
| P3 | 2026-09-07 | Docker: `pytest src/tests/functional/test_podcasts_api.py`: 6 passed; повторный прогон: 6 passed | Docker: `coverage run -m pytest -q --disable-warnings`: 388 passed, total coverage 81%; `ruff`, `mypy`, `git diff --check` зелёные | `b12b112` — `[#10] API improvements and fixes: migrate podcast API tests to PostgreSQL` |
| P4 | 2026-09-07 | Docker: `pytest src/tests/functional/test_episode_media_cookie_api.py`: 8 passed; повторный прогон: 8 passed | Docker: `coverage run -m pytest -q --disable-warnings`: 350 passed, total coverage 81%; `ruff`, `mypy`, `git diff --check` зелёные | подготовлен: `[#10] API improvements and fixes: migrate episode media and cookie API tests` |
| P5 | 2026-09-07 | Docker: `pytest src/tests/functional/test_auth_system_api.py`: 5 passed; повторный прогон: 5 passed | Docker: `coverage run -m pytest -q --disable-warnings`: 334 passed, total coverage 82%; `ruff`, `mypy`, `git diff --check` зелёные | подготовлен: `[#10] API improvements and fixes: migrate auth and system API tests` |
| P2R | 2026-09-10 | `test_mocks.py`: 14 passed; без functional: 322 passed, повторно 322 passed; `pytest -n 2`: 322 passed | PostgreSQL/full coverage: 343 passed, 82%; `ruff`, `mypy`, `git diff --check` и структурный поиск зелёные | — |
