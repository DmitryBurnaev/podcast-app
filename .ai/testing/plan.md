# План миграции тестов

Статус актуален на 2026-09-10. Этот документ является живым: после завершения
этапа меняется его отметка, фиксируются проверки и commit. Полный baseline и
рабочий журнал этапов находятся в `../plans`.

## Условные обозначения

- `[x]` — выполнено.
- `[ ]` — запланировано.
- `[~]` — выполняется.

## Выполненные шаги

- [x] **T0. Исследовать текущие тесты и сформулировать целевую стратегию.**
  Подтверждён приоритет API-функциональных тестов, классов, типизированных
  entity fixtures, заглушек внешних границ и DI.
- [x] **T1. Зафиксировать постоянные правила тестирования.**
  Правила находятся в `se.md` и не зависят от
  устройства других кодовых баз.

## Текущий этап

- [x] **D0. Зафиксировать исследование и исполнимый план.**
  - Создать snapshot исходного состояния и живой журнал в `../plans`.
  - Обновить этот план для самостоятельных этапов API, views, admin и CI.
  - Проверка: `uv run pytest --collect-only -q` — 385 сценариев, один
    зафиксированный collection error в legacy `test_root_utils.py`.

## Выполненные этапы

- [x] **P0. Восстановить зелёный исходный набор.** Исправлены устаревшие
  imports, auth/app fixtures, сигнатуры views, ожидания repository/UoW и
  task job IDs. Проверки: 399 collected, 399 passed, total coverage 79%.

## Текущий этап

- [x] **P1. Подготовить изолированную PostgreSQL-инфраструктуру.** Обязательный
  `TEST_DB_NAME`, отдельная DB на worker, Alembic, `TRUNCATE … CASCADE`,
  typed `user`/`podcast` fixtures. Проверки: 401 passed, coverage 80%,
  PostgreSQL smoke и `pytest -n 2` зелёные.

## Дальнейшие этапы

- [x] **P2. Ввести тестируемые providers и class-based fakes (superseded).**
  Исторический этап дал stateful fakes, но app-level `AppProviders` и
  composition root оказались избыточными и заменяются этапом P2R.
- [x] **P3. Мигрировать Podcasts API на реальную БД.** CRUD, ownership,
  pagination, image/RSS и fake side effects через class-based fakes. Проверки:
  два domain-прогона по 6 passed; полный набор 388 passed, coverage 81%.
- [x] **P4. Мигрировать Episodes, media и cookies API на реальную БД.** URL и
  uploaded creation, ownership, update/delete/download/cancel, cookies и media
  uploads проверяются через PostgreSQL и class-based queue/Redis/storage/media fakes.
  Проверки: два доменных прогона по 8 passed; полный набор 350 passed, coverage 81%.
- [x] **P5. Мигрировать Auth, profile и system API на реальную БД.** Signup,
  signin/refresh rotation, profile/IP/access-token lifecycle, admin invites,
  reset mailer и health проверяются через PostgreSQL и fakes. Проверки: два
  доменных прогона по 5 passed; полный набор 334 passed, coverage 82%.
- [x] **P2R. Убрать app-level providers.** Production-код снова напрямую
  собирает реальные зависимости; PostgreSQL остаётся реальной, а RQ/S3/Redis/
  SMTP/media подменяются function-scoped BaseMock/monkeypatch fixtures.
  Проверки: 343 passed, coverage 82%; повторный изолированный и xdist-прогоны —
  по 322 passed; `ruff`, `mypy`, `git diff --check` зелёные.
- [ ] **P6. Полностью покрыть HTML views.** Template/context, auth, ownership,
  errors и episodes views без HTML snapshots.
- [ ] **P7. Расширить SQLAdmin coverage.** Dashboard, роли и custom workflows.
- [ ] **P8. Закрыть services, tasks, repositories и lifecycle.**
- [ ] **P9. Включить типизацию, coverage >= 90% и CI gates.**

## Порядок выполнения

D0 → P0 → P1 → P2 дали первый эталонный functional test; после P3–P5 выполняется
P2R, заменяющий app-level DI на тестовые monkeypatch-границы. Затем P6–P8
закрывают HTML/admin и изолированный слой, P9 включает финальные CI gates.
