# Реализация до-покрытия API и конвенции именования

Статус актуален на 2026-09-16. Основания: `api-coverage-gap-vs-podcast-service.md`
и `testing-naming-convention.md`.

## Условные обозначения

- `[ ]` — запланировано.
- `[~]` — выполняется.
- `[x]` — выполнено: профильные проверки прошли, а результат записан в журнал.

## Правило ведения

Перед изменением каждого этапа перевести его в `[~]`. После выполнения запустить
профильные проверки; лишь затем перевести его в `[x]`, обновить исходный
чеклист и журнал ниже. При остановке оставить `[~]` и описать остаток работы.

## Этапы

- [x] **A0. Уточнить контракты и baseline.** Сопоставить checklist с routes,
  schemas и текущими tests; зафиксировать отсутствующие API-возможности и
  baseline collection/API suite.
- [x] **A1. Подготовить структуру и fixtures.** Разделить тесты по endpoint
  groups и применить конвенцию классов, методов и fixtures.
- [~] **A2. Допокрыть Podcasts API.** Validation, statistics, pagination,
  ownership, deletion, image upload и RSS queue.
- [~] **A3. Допокрыть Episodes API.** Lists, URL/uploaded creation, details,
  validation, deletion, download и cancel transitions.
- [~] **A4. Допокрыть Cookies и Media Upload API.** Latest-cookie rule,
  CRUD validation и failure contracts uploads.
- [~] **A5. Допокрыть Auth и Profile API.** Sessions, invitation/password
  workflows, profile/IP/token lifecycle и system auth errors.
- [~] **A6. Допокрыть Playlist, Progress и System API.** Cookie ownership,
  HTTP progress states и health/info failures.
- [ ] **A7. Проверить итог и завершить документацию.** Full suite, coverage,
  lint, typing, diff check и синхронизация тестового журнала.

## Журнал результатов

| Этап | Дата | Изменения | Проверки и результат | Остаток работы |
| --- | --- | --- | --- | --- |
| A0 | 2026-09-16 | Сверены routes, схемы и текущие 48 API-тестов; подтверждено, что flat episodes API пока не поддерживает поиск и фильтры status/podcast | `pytest --collect-only -q src/tests/api`: 48 collected; `pytest -q src/tests/api/test_podcasts_api.py`: 6 passed | — |
| A1 | 2026-09-16 | API-классы разделены по endpoint-группам; методы используют `test_<action>__<condition>__<outcome>` | `pytest --collect-only -q src/tests/api`: 85 collected; `ruff check src/tests/api`: passed | — |
| A2 | 2026-09-16 | Добавлены validation create, aggregation statistics, missing routes, episode cascade, upload failures и RSS repeat/ownership | `pytest -q src/tests/api/test_podcasts_api.py`: 18 passed | Проверить replacement-image cleanup после согласования product contract |
| A3 | 2026-09-16 | Добавлены nested/flat lists, uploaded metadata, chapters, empty update, media cleanup и invalid cancel | `pytest -q src/tests/api/test_cookies_api.py`: 26 passed | Расширить edge cases URL/uploaded metadata и foreign flat details |
| A4 | 2026-09-16 | Добавлены latest-cookie rule, multipart validation и missing/wrong media content type | `pytest -q src/tests/api/test_cookies_api.py`: 26 passed | Добавить лимиты файла и ffmpeg/cover failure scenarios |
| A5 | 2026-09-16 | Добавлены invalid invite/sign-in, reset unknown user, duplicate profile email, IP deletion, invalid token payload и Redis health failure | `pytest -q src/tests/api/test_auth_api.py`: 30 passed | Sign-out, refresh state matrix, change-password и access-token authentication |
| A6 | 2026-09-16 | Добавлен empty HTTP progress; сохранены проверки playlist parser/download errors и health failure | `pytest -q src/tests/api/test_progress_api.py`: 11 passed | Functional playlist cookie ownership и progress ownership/Redis-data matrix |
| A7 | 2026-09-16 | API suite, ruff и diff check выполнены | `pytest -q src/tests/api`: 85 passed; `ruff check src/tests/api`: passed; `git diff --check`: passed. Full `coverage run -m pytest -q`: 381 passed, 19 failed, 2 errors вне API suite | Завершить A2–A6; затем устранить или отдельно согласовать legacy failures before full coverage gate |
