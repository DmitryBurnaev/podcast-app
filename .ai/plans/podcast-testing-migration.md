# Поэтапная миграция тестов

Статус актуален на 2026-09-06. Этот файл — рабочий журнал миграции;
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

- [ ] **P1. Изолированная PostgreSQL-инфраструктура.** Безопасный
  `TEST_DB_NAME`, отдельная БД на run/worker, migrations, очистка таблиц и
  typed entity fixtures; проверить повторный и xdist-запуски.

- [ ] **P2. Providers и class-based fakes.** `AppProviders` в `make_app()`,
  Protocol-интерфейсы и function-scoped storage/Redis/queue/mail/HTTP/media
  fakes с contract tests.

- [ ] **P3. Podcasts API на PostgreSQL.** CRUD, ownership, pagination,
  image/RSS и fake side effects; доменный тест запускается дважды.

- [ ] **P4. Episodes, media и cookies API на PostgreSQL.** URL/upload creation,
  transitions, delete/cancel/download, cookie conflicts и media failures.

- [ ] **P5. Auth, profile и system API на PostgreSQL.** Users/sessions/invites/
  tokens, profile/IP/access-token flows, roles, expiration и fake SMTP/health.

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
| P0 | 2026-09-06 | `pytest --collect-only -q`: 399 collected; API: 110 passed; views: 32 passed; services/tasks: 120 passed | `coverage run -m pytest -q --disable-warnings`: 399 passed; total coverage 79% | подготовлен: `[#10] API improvements and fixes: stabilize existing test suite` |
