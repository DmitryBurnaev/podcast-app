# План миграции тестов

Статус актуален на 2026-09-06. Этот документ является живым: после завершения
этапа меняется его отметка, фиксируются проверки и commit. Полный baseline и
рабочий журнал этапов находятся в `.ai/plans/`.

## Условные обозначения

- `[x]` — выполнено.
- `[ ]` — запланировано.
- `[~]` — выполняется.

## Выполненные шаги

- [x] **T0. Исследовать текущие тесты и сформулировать целевую стратегию.**
  Подтверждён приоритет API-функциональных тестов, классов, типизированных
  entity fixtures, заглушек внешних границ и DI.
- [x] **T1. Зафиксировать постоянные правила тестирования.**
  Правила находятся в `docs/testing/knowledge-base.md` и не зависят от
  устройства других кодовых баз.

## Текущий этап

- [x] **D0. Зафиксировать исследование и исполнимый план.**
  - Создать snapshot исходного состояния и живой журнал в `.ai/plans/`.
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

- [x] **P2. Ввести тестируемые providers и class-based fakes.** `AppProviders`
  владеет queue и lifecycle DB/Redis/S3; SQLAdmin получает их от app. Добавлены
  Protocol-интерфейсы и stateful fakes storage/Redis/queue/mail/HTTP/media.
  Проверки: 14 профильных и 408 полных passed, coverage 80%.
- [x] **P3. Мигрировать Podcasts API на реальную БД.** CRUD, ownership,
  pagination, image/RSS и fake side effects через class-based fakes. Проверки:
  два domain-прогона по 6 passed; полный набор 388 passed, coverage 81%.
- [ ] **P4. Мигрировать Episodes, media и cookies API на реальную БД.**
- [ ] **P5. Мигрировать Auth, profile и system API на реальную БД.**
- [ ] **P6. Полностью покрыть HTML views.** Template/context, auth, ownership,
  errors и episodes views без HTML snapshots.
- [ ] **P7. Расширить SQLAdmin coverage.** Dashboard, роли и custom workflows.
- [ ] **P8. Закрыть services, tasks, repositories и lifecycle.**
- [ ] **P9. Включить типизацию, coverage >= 90% и CI gates.**

## Порядок выполнения

D0 → P0 → P1 → P2 обязательны для появления первого эталонного functional test.
P3–P5 идут доменами API, затем P6–P8 закрывают HTML/admin и изолированный
слой. P9 включается после стабилизации всех ключевых доменов.
