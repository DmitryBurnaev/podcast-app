# План: устранение ownership- и архитектурных проблем

Дата исследования: 2026-09-11
Статус: выполняется

## Цель

Запретить cross-owner reuse, сделать область доступа явной частью repository
API, закрепить матрицу публичных endpoints и убрать оставшиеся инфраструктурные
обходы. HTTP-контракты и схема БД остаются совместимыми.

## Правила выполнения

- Каждый этап выполняется отдельным набором изменений и завершается
  профильными тестами, полным pytest/coverage, Ruff, Mypy, xdist и
  `git diff --check`.
- PostgreSQL остаётся реальным; внешние S3, Redis, RQ, SMTP и media boundaries
  остаются function-scoped fakes.
- `SystemScope` разрешён только в доверенных auth, admin, worker и maintenance
  путях. Пользовательские API и HTML controllers используют `OwnerScope`.

## Этапы

- [x] **D0. План и baseline.** Зафиксировать этот чек-лист; исходный baseline:
  343 passed, coverage 82%, Ruff/Mypy/xdist зелёные.
- [~] **R1. Cross-owner reuse.** Ограничить поиск одинакового source и fallback
  media текущим owner; чужой source не создаёт записи или queue effects.
- [~] **R2. Явный repository scope.** Ввести `OwnerScope`/`SystemScope`,
  централизовать owner-column и применить scope ко всем custom read/update/delete
  путям.
- [~] **R3. Authentication matrix.** Явные controller lists, tests для публичных
  и закрытых routes, строгая media-token validation и AdminAuth boundary.
- [ ] **R4. Imports и DB обходы.** Удалить `exceptions` alias и неиспользуемые
  DB dependency generators, сохранив module-level import rule.
- [ ] **R5. Чистые ORM-модели.** Вынести URLs, filenames и cookie file I/O в
  helpers с явными settings, сохранив API, RSS и templates.

## Критерии готовности

- Два пользователя не могут читать, изменять, удалять или переиспользовать
  Podcast, Episode, Cookie, File, IP или AccessToken друг друга.
- Публичными остаются auth flow, `/login`, system info/health, OpenAPI/static и
  capability media/RSS URLs; остальные business endpoints требуют auth.
- В ORM нет `get_app_settings()` или файлового I/O; runtime imports остаются в
  заголовках модулей.
- Полный PostgreSQL pytest, coverage не ниже 82%, повторный functional run,
  `pytest -n 2`, Ruff, Mypy и `git diff --check` проходят.

## Текущая граница коммита

Реализация R1 и R2 вынесена в отдельный коммит: введены scope-объекты,
пользовательские пути переведены на `OwnerScope`, а доверенные worker/admin/auth
пути — на явный `SystemScope`. Добавлен функциональный regression test для
чужого `source_id`. R1/R2 остаются в состоянии проверки до успешного запуска
PostgreSQL pytest: песочница запрещает чтение
`.venv/.../redis/credentials.py`, поэтому import проявляется как
`ModuleNotFoundError: redis.credentials` ещё до collection.

R3: динамическая регистрация контроллеров заменена явными `API_CONTROLLERS` и
`VIEW_CONTROLLERS`; capability media проверяет точный формат token до DB lookup.
Добавлены проверки публичных и закрытых маршрутов. Полный функциональный запуск
нужно выполнить вне текущего ограничения песочницы на `credentials.py`.
