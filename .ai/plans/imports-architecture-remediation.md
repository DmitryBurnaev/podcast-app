# План: устранение импортов вне заголовка модуля

Дата исследования: 2026-09-07
Статус: реализация завершена; запуск Ruff/pytest ожидает доступного project runtime

## Правило

Все runtime-импорты располагаются в заголовке модуля. Импорты, нужные только
для аннотаций, допускаются только внутри `if TYPE_CHECKING:`. Локальный импорт
не используется как способ отсрочить и скрыть циклическую зависимость.

## Результаты исследования

Проверены все Python-модули в `../../src`: поиск вложенных `import`/`from`, а также
проверка импортов верхнего уровня после исполняемого кода. Второй класс проблем
не найден. `TYPE_CHECKING`-блоки в `../../src/exceptions.py`, `../../src/modules/auth/types.py`,
`../../src/modules/utils/ffmpeg.py`, `../../src/modules/db/models/media.py` и admin-модулях
соответствуют правилу и не требуют изменения.

| Приоритет | Место | Наблюдение | Причина / целевое решение |
| --- | --- | --- | --- |
| P0 | `src/providers.py:119-127` | `AppProviders.from_production()` импортирует RQ, Redis, БД, Redis/S3 и ffmpeg. | Вынести production-сборку адаптеров в отдельный composition-root модуль, который может импортировать реализации сверху. `providers.py` должен оставить только контракты и контейнер. |
| P1 | `src/providers.py:158` | `_ProductionMailer.send()` локально импортирует `send_email`. | После переноса `Mailer` в dependency-neutral contracts-модуль сделать явный верхнеуровневый импорт адаптера либо перенести адаптер рядом с production composition root. Это разрывает текущую дугу `services.email -> providers`. |
| P1 | `src/providers.py:173` | `_ProductionMediaSource.get_source_media_info()` локально импортирует утилиту. | Перенести адаптер к production-сборке и импортировать утилиту в заголовке нового модуля. |
| P0 | `src/modules/db/models/media.py:128` | ORM-модель импортирует `StorageS3` в `File.fetch_presigned_url()`. | Убрать инфраструктурный вызов из модели. Сервис/адаптер хранения, зависящий от `File`, должен получать storage boundary и возвращать URL; модели не должны зависеть от services. |
| P2 | `src/tests/api/test_misc.py:182` | Тестовый fake импортирует `yt_dlp` внутри `extract_info()`. | Импортировать модуль в заголовке теста и вызывать его через уже существующий module alias. |

## Целевая зависимость

```text
contracts / protocols  <-  providers container  <-  main (composition root)
          ^                         ^
          |                         |
  service adapters ------------------+

ORM models  <-  storage service / adapter  <-  controllers and admin views
```

`providers` не должен импортироваться прикладными адаптерами ради одного
протокола; общие типы живут в нейтральном contracts-модуле. ORM-модели не
создают инфраструктурные реализации.

## Шаги реализации

1. [x] Вынести протоколы в dependency-neutral
   `../../src/modules/common/contracts.py`, а `_ProductionMailer`,
   `_ProductionMediaSource` и сборку providers — в `../../src/composition.py`.
   `main.py` вызывает публичную factory, а все конкретные реализации
   импортируются в заголовке composition root.
2. [x] Заменить `File.fetch_presigned_url()` на
   `get_file_presigned_url(file, storage=...)` в storage-сервисе. Обновить
   web/admin callers, сохранив ответы и обработку `NotSupportedError`.
3. [x] Исправить тестовый fake в `../../src/tests/api/test_misc.py`, добавив module
   alias в заголовок тестового файла.
4. [~] Включить в Ruff правило `PLC0415` (`import-outside-toplevel`) без
   `noqa` или per-file исключений для production-кода. Type-only imports
   остаются под `TYPE_CHECKING`; фактический запуск Ruff ожидает доступного
   project runtime.
5. [x] Обновить узкие unit-тесты: web/admin callers проверяют service boundary,
   а storage-service проверяется с injected fake. Перед этим прочитаны
   `../../docs/testing/knowledge-base.md` и `../../docs/testing/plan.md`; этапы миграции
   тестов не завершались и не меняли scope, поэтому `../../docs/testing/plan.md`
   не требует обновления.

## Критерии готовности

- По `rg -n '^(    |\\t)+(import|from) ' src --glob '*.py'` остаются только
  type-only импорты непосредственно внутри `if TYPE_CHECKING:`.
- `uv run ruff check src` проходит с включённым `PLC0415` без `noqa`/per-file
  исключений для production-кода.
- `uv run pytest -q` проходит; дополнительно проверены затронутые view/admin
  сценарии и factory application providers.
- В `../../src/modules/db/models` отсутствуют runtime-импорты из
  `src.modules.services`.

## Выполненная проверка и ограничение среды

- Поиск вложенных импортов подтверждает, что в `src` остались только imports
  непосредственно в `if TYPE_CHECKING:`.
- `uv run ruff check …` и профильный `uv run pytest …` не стартовали: binary
  `/Users/dmitry/work/bin/uv` возвращает `Operation not permitted`; системный
  Python также требует отсутствующие Xcode Command Line Tools. После
  восстановления runtime нужно выполнить команды из критериев готовности.
