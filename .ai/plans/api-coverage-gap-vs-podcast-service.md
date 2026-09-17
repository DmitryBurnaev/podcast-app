# Чеклист до-покрытия API относительно `podcast-service`

Дата сравнения: 2026-09-16.

Правила именования test-классов, методов и fixtures для реализации этого
чеклиста зафиксированы в
[`testing-naming-convention.md`](testing-naming-convention.md).

## Границы сравнения

Проверены HTTP API-тесты из `../podcast-service/src/tests/api/` и актуальные
functional API-тесты этого репозитория (`src/tests/api/*_api.py`). В
`podcast-app` уже перенесены основные CRUD-сценарии и реальные PostgreSQL
fixtures для podcasts, episodes, cookies, uploads и auth. Однако ряд
контрактов из более дробной suite `podcast-service` пока объединён в один
happy-path сценарий либо не проверяется.

Этот чеклист относится только к существующим HTTP-маршрутам `podcast-app`.
Не переносить механически снятые с продукта контракты: WebSocket `/progress/`
и Sentry endpoint были в `podcast-service`, но отсутствуют в текущем app.
Token media URLs `/m/` и `/r/` сохранены как HTML/view-маршруты, поэтому их
детальные проверки относятся к P6 (views), а не к API functional suite.

## Принципы выполнения

- [ ] Каждый новый API-сценарий выполняется через реальный маршрут и
  PostgreSQL (`use_functional_session_factory`), с function-scoped данными.
- [ ] Для RQ, S3, Redis, SMTP, yt-dlp и ffmpeg использовать имеющиеся
  stateful fakes; не подменять repositories или UoW.
- [ ] В каждом негативном сценарии проверять код, API-конверт ошибки и
  отсутствие нежелательных записей/side effects.
- [ ] После закрытия группы актуализировать `.ai/testing/plan.md` и рабочий
  журнал `podcast-testing-migration.md` с результатом профильного прогона.

## P3 follow-up — podcasts

- [ ] `POST /api/podcasts/`: параметризовать невалидные payload (нет имени,
  пустые/недопустимые поля); удостовериться, что podcast не создан.
- [ ] `GET /api/podcasts/`: проверить агрегированное число episodes в
  response, оба направления разрешённой сортировки и граничные значения
  `limit`/`offset`.
- [ ] `GET|PATCH|DELETE /api/podcasts/{id}/`: дополнить not-found для
  несуществующего id и проверку каскада при удалении podcast: его episodes и
  связанные файлы удаляются, записи другого podcast того же владельца остаются.
- [ ] `POST /api/podcasts/{id}/upload-image/`: проверить замену прежней
  картинки и cleanup старого файла, отсутствующий/не-image multipart и отказ
  storage без изменения `image_id`.
- [ ] `PUT /api/podcasts/{id}/generate-rss/`: проверить повторный запрос и
  устойчивость job id/числа queued задач, а также чужой podcast (не только
  несуществующий).

## P4 follow-up — episodes, cookies и uploads

### Episodes

- [ ] `GET /api/podcasts/{podcast_id}/episodes/`: покрыть пустой список,
  pagination/order, отказ для чужого podcast и отсутствие чужих episodes.
- [ ] `POST /api/podcasts/{podcast_id}/episodes/`: проверить невалидный URL,
  чужой/missing podcast, ошибку source extractor, отсутствие очереди и записей
  при ошибке, а также уже существующий episode того же владельца.
- [ ] Проверить варианты метаданных uploaded episode: без cover, nullable и
  неполные поля, неподходящий hash и повторную загрузку с отличающимися
  данными. Идемпотентный happy path уже покрыт.
- [ ] `GET /api/podcasts/{podcast_id}/episodes/uploaded/{hash}/`: добавить
  existing hash, несуществующий hash и проверку ownership.
- [ ] `GET /api/episodes/`: покрыть flat-list pagination, сортировку и
  исключение чужих records. Поиск по title и фильтры status/podcast отсутствуют
  в текущем публичном контракте и не входят в этот test plan.
- [ ] `GET /api/episodes/{id}/`: проверить response с chapters, missing id и
  чужую сущность.
- [ ] `PATCH /api/episodes/{id}/`: параметризовать невалидные изменения и
  удостовериться, что update чужого/missing episode не меняет БД.
- [ ] `DELETE /api/episodes/{id}/`: добавить успешное удаление готового
  episode с media cleanup, чужой/missing id, in-progress conflict и отсутствие
  cancel task там, где его быть не должно.
- [ ] `PUT /api/episodes/{id}/download/` и
  `PUT /api/episodes/{id}/cancel-downloading/`: дополнить чужой/missing id,
  недопустимое исходное состояние, повторные вызовы и точный набор
  queued/cancelled Redis/RQ side effects.

### Cookies

- [ ] `GET /api/cookies/`: проверить правило «последний cookie каждого
  source type», детерминированный порядок и изоляцию другого пользователя.
- [ ] `POST|PUT /api/cookies/`: покрыть missing file/source type,
  неподдерживаемый source type, неверный multipart и отсутствие изменений при
  validation error.
- [ ] `GET|PUT|DELETE /api/cookies/{id}/`: добавить missing id для всех
  операций; для update проверить чужой cookie отдельно от delete/get. Связанный
  episode conflict и чужой get/delete уже покрыты.

### Media uploads

- [ ] Для `POST /api/media/upload/audio/` и `/image/` параметризовать missing
  file, пустой файл, превышение лимита и неподходящий content type.
- [ ] Проверить duplicate upload: ожидаемая семантика hash/path и отсутствие
  лишних объектов в fake storage.
- [ ] Добавить ошибки `ffmpeg`/cover extraction, upload audio без cover и
  ошибки presigned URL; проверять контракт ошибки и cleanup временных/remote
  объектов в рамках текущей реализации.

## P5 follow-up — auth, profile и system

- [ ] `POST /api/auth/sign-in/`: неверный пароль, неизвестный и inactive user,
  невалидный body; отдельно проверить reuse существующей и создание новой
  session.
- [ ] `POST /api/auth/sign-up/`: duplicate email, отсутствующий/истёкший или
  уже применённый invite, email не совпадает с invite и невалидные password
  пары; при отказе user/default podcast/session не создаются.
- [ ] `POST /api/auth/sign-out/`: success, отсутствующая session и наличие
  другой session пользователя.
- [ ] `POST /api/auth/refresh-token/`: кроме invalid JSON покрыть inactive
  user/session, access token вместо refresh token, token/session mismatch и
  поддельный JWT; удостовериться, что rotation не оставляет неверное состояние.
- [ ] `POST /api/auth/reset-password/` и `/change-password/`: неавторизованный
  и невалидный запросы, неизвестный/non-admin пользователь для reset,
  expired/invalid token и inactive/missing user для change password; SMTP
  проверяется через fake без утечки token.
- [ ] `GET|POST /api/auth/invites/`: list, validation, unauthenticated и
  non-admin access, duplicate user и повторный invite (обновление срока без
  дубликата), плюс факт отправки fake mail.
- [ ] `GET|PATCH /api/auth/me/`: невалидный update и неизменность профиля при
  ошибке; дополнить expired, wrong-type, invalid token, inactive и deleted
  current user.
- [ ] Регистрация IP через `GET /api/auth/me/`, `GET /api/auth/user-ips/` и
  `POST /api/auth/user-ips/delete/`: несколько IP, idempotent регистрация,
  отсутствующий IP header и delete с сохранением ожидаемых записей.
- [ ] `GET|POST|PATCH|DELETE /api/auth/access-tokens/`: пустой/пагинированный
  list, фильтрация владельца, включённые и выключенные tokens, missing/foreign
  id и validation; secret показывается только при создании.
- [ ] Аутентификация access token: корректный, неверный, истёкший и disabled
  token на защищённом маршруте.
- [ ] `GET /api/system/health/`: сбой Redis возвращает договорённую ошибку;
  `GET /api/system/info/` сверяет полный публичный контракт, а не только
  happy-path status.

## P4/P5 follow-up — playlist и progress

- [ ] `GET /api/playlist/`: добавить поддерживаемый второй source, выбор
  подходящего cookie, игнорирование cookie другого владельца и выбор самого
  нового cookie. Успех, parser error, non-playlist и yt-dlp error уже покрыты
  unit-style тестами; перенести критичные варианты на functional DB там, где
  проверяется ownership cookie.
- [ ] `GET /api/progress/`: покрыть пустое состояние, только episodes в
  допустимых in-progress status, изоляцию владельца, конкретный `episode_id`
  (missing/foreign), отсутствие Redis progress data и response без удалённого
  podcast. Для этого app сохраняет HTTP polling; WebSocket сценарии из
  `podcast-service` не являются gap.

## Вне текущего API-чеклиста

- [ ] P6 views: детальные проверки `/m/{token}/` и `/r/{token}/` — корректный
  тип/availability, неверный token, presigned redirect и ошибка storage.
- [ ] Не добавлять тест на service-only Sentry endpoint или WebSocket progress,
  пока соответствующий маршрут не вернётся в product contract.
