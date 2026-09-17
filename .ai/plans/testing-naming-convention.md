# Соглашение об именовании API-тестов

Источник подхода: `podcast-service/src/tests/api/` и его общие fixtures. Это
соглашение фиксирует полезную часть его структуры для переноса в новый проект;
оно не зависит от Starlette, Litestar или pytest-плагинов.

## Классы

Один test-класс соответствует одной публичной HTTP-операции или одной тесной
паре операций одного route resource. Имя имеет вид
`Test<Domain><Endpoint>API`.

| Граница | Имя класса |
| --- | --- |
| `POST /api/auth/sign-in/` | `TestAuthSignInAPI` |
| `GET, POST /api/podcasts/` | `TestPodcastListCreateAPI` |
| `GET, PATCH, DELETE /api/episodes/{episode_id}/` | `TestEpisodeDetailsAPI` |
| `POST /api/media/upload/audio/` | `TestMediaAudioUploadAPI` |

Составные классы допустимы только когда HTTP-маршруты образуют единый CRUD
контракт (`ListCreate`, `Details`, `RUD`). Не использовать широкие классы
вроде `TestAuthAPI` или `TestEpisodeAPI`, в которых смешаны независимые
маршруты: они скрывают пробелы покрытия и делают fixture-набор неочевидным.

`API` здесь намеренно framework-neutral. В проекте, где тестовый код следует
терминологии Django REST Framework, его можно последовательно заменить на
`APIView`, но не смешивать оба суффикса в одной suite.

## Тестовые методы

Имя всегда описывает наблюдаемое правило, а не вызванный internal method:

```text
test_<action>__<condition>__<outcome>
```

- `<action>` — действие клиента: `get_list`, `create`, `update`, `delete`,
  `sign_in`, `refresh_token`, `upload`.
- `<condition>` — существенное состояние или вход: `episode_from_another_user`,
  `invalid_request`, `session_inactive`, `linked_episodes`, `with_chapters`.
- `<outcome>` — наблюдаемый результат: `ok`, `fail`, `persists`,
  `enqueues_task`, `filters_by_owner`.

Примеры:

```python
async def test_create__ok(...): ...
async def test_delete__cookie_from_another_user__fail(...): ...
async def test_get_list__filter_by_current_user(...): ...
async def test_refresh_token__session_inactive__fail(...): ...
async def test_upload__storage_failure__does_not_persist_file(...): ...
```

Для обычного success-path сокращение `test_<action>__ok` является нормой.
Если тест доказывает важный side effect или ограничение видимости, outcome
должен быть назван, а не спрятан за `ok`. Не использовать номера, общие слова
`case`, `works`, `correctly`, `test_foo`, имена production-функций или
несколько сценариев в одном имени. Варианты одного контракта объединяются
параметризацией; имя теста остаётся одним, а `ids` параметров называют
условия (`missing-title`, `too-long-title`).

## Fixtures

Fixture называется существительным или устойчивым noun phrase и сообщает,
что именно получит тест, а не как объект собран.

| Назначение | Шаблон | Примеры |
| --- | --- | --- |
| Базовая доменная сущность | имя модели в `snake_case` | `user`, `podcast`, `episode`, `cookie`, `user_session` |
| Альтернативная сущность для прав/фильтра | уточнение владельца/состояния | `other_user`, `foreign_podcast`, `published_episode`, `inactive_user` |
| Входной payload | `<subject>_data` | `user_data`, `podcast_data`, `episode_data`, `uploaded_episode_data` |
| Реальная функциональная DB-инфраструктура | `functional_<resource>` | `functional_engine`, `functional_session_factory`, `functional_session` |
| Явное подключение DB harness | глагольная capability | `use_functional_session_factory` |
| HTTP-приложение и клиент | короткое имя продукта | `app`, `client`, `auth_required_app`, `auth_required_client` |
| Подменённая внешняя граница | `mocked_<dependency>` | `mocked_storage`, `mocked_rq_queue`, `mocked_redis`, `mocked_mailer`, `mocked_media_source` |
| Временный ресурс | `<purpose>_<resource>` | `tmp_file`, `audio_file`, `image_file`, `rss_file` |

`mocked_<dependency>` означает именно fixture, которая устанавливает patch и
возвращает stateful fake для assertions. Сам класс называется по поведению,
например `FakeStorage` или `FakeTaskQueue`, а не `Mock` без контекста.
Fixture не должна называться `data`, `obj`, `result`, `fixture1`,
`repository_mock` или кодировать неустойчивые детали реализации.

## Область действия и создание данных

- Доменные fixtures (`user`, `podcast`, `episode`, `cookie`, `*_file`) имеют
  function scope и создают согласованные реальные записи.
- Инфраструктурные fixtures могут быть session scoped и несут явный префикс:
  `test_database`, `functional_engine`, `functional_session_factory`.
- Factory fixture принимает overrides и сохраняет тот же словарь имён:
  `podcast_factory(name="...")`, `episode_factory(status=...)`; если factory
  не fixture, это helper `make_podcast(...)`/`create_episode(...)`.
- `db_` допустим только когда важно подчеркнуть, что объект уже сохранён в
  БД (`db_user`, `db_podcast`), но не смешивается с обычными `user`/`podcast`
  в одном слое без причины.

## Минимальный образец

```python
class TestPodcastListCreateAPI:
    async def test_create__ok(
        self,
        client: TestClient,
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        ...

    @pytest.mark.parametrize("payload", [{}, {"name": ""}], ids=["missing-name", "empty-name"])
    async def test_create__invalid_request__does_not_persist(
        self,
        client: TestClient,
        functional_session: AsyncSession,
        payload: dict[str, object],
    ) -> None:
        ...
```

Итоговая проверка для нового проекта: по имени класса видно endpoint, по имени
теста — вход и наблюдаемое правило, по списку fixture — доменные данные,
инфраструктура и внешние границы без чтения тела теста.
