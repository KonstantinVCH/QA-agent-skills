# Настройка под проект

Шаблон: заполни один раз под свой проект. Всё в фигурных скобках — плейсхолдеры. Секреты сюда не
писать: только имена переменных окружения.

## Доступы (переменные окружения)

| Переменная | Значение | Кому нужна |
|---|---|---|
| `WIREMOCK_URL` | `{WIREMOCK_URL}`, напр. `http://wiremock.example.com` или `http://localhost:8080` | `wm.py`, `jaeger_urls.py` (метка мока) |
| `WIREMOCK_MODE` | `studio` или `plain`; пусто = автоопределение | `wm.py` |
| `JAEGER_URL` | `{JAEGER_URL}` | `jaeger_urls.py` |
| `JAEGER_SERVICE_TEMPLATE` | как сервис называется в Jaeger, напр. `test-{stand}.{service}` или `{service}` | `jaeger_urls.py` |
| `MOCK_HOST_MARKER` | подстрока хоста мока в `http.url`; по умолчанию хост из `WIREMOCK_URL` | `jaeger_urls.py` |
| `BITBUCKET_URL`, `BITBUCKET_TOKEN` | `{BITBUCKET_URL}` и PAT с правом Read | `adapter_paths.py <slug>` |
| `BITBUCKET_PROJECTS` | ключи проектов через запятую, где лежат репозитории сервисов | `adapter_paths.py <slug>` |
| `ADAPTER_INTERNAL_CLIENTS` | внутренние клиенты платформы через запятую — их не мокать | `adapter_paths.py` |
| `CONFLUENCE_URL`, `CONFLUENCE_TOKEN` | для чтения спецификаций методов | поиск контракта (шаг 5) |

Токены пользователь создаёт сам в профиле соответствующей системы и кладёт в переменные окружения.
Не проси вставлять их в чат.

## Сервер моков

| Что | Значение |
|---|---|
| Продукт и версия | `{WireMock Studio / WireMock X.Y / WireMock Cloud}` — от версии зависят доступные фичи (`subEvents`, лог вебхуков, `mappings/import`) |
| Один мок на все стенды или по моку на стенд | `{SHARED_OR_PER_STAND}` — от этого зависит, насколько опасна правка стаба |
| Защита admin-API | `{нет / basic auth / токен}` |
| UI | `{WIREMOCK_URL}/mock-apis/<id>/stubs` (Studio) |

## Стенды и сервисы

| Что | Значение |
|---|---|
| Тестовые стенды | `{STAND}`: напр. `test-1 … test-5`, предпрод `{PREPROD}`; prod — моки запрещены |
| Адрес сервиса на стенде | `{SERVICE_URL_PATTERN}`, напр. `http://{service}.{stand}.example.test` |
| Сервисы, ходящие к внешним партнёрам | `{ADAPTERS}`, напр. `payment-gateway-adapter`, `delivery-adapter`, `notification-service` |
| Где объявлены пути клиентов | `{CLIENTS_CONFIG}`, напр. `application.yml` → `app.clients.<партнёр>.path` (для `adapter_paths.py --clients-key`) |
| Внутренние клиенты (не мокать) | `{INTERNAL_CLIENTS}` |
| Заведомо валидные тестовые данные | `{TEST_DATA}`: номера карт провайдера в тест-режиме, ИНН/реквизиты с верной контрольной суммой |

## Соглашения команды

- Имя мока: `{MOCK_NAME_PATTERN}`, напр. как репозиторий сервиса-потребителя — тогда порт
  находится по имени сервиса без реестра.
- Имя стаба: `{STUB_NAME_PATTERN}`, напр. `N. Бизнес-описание (вариант)` — по-русски, без ключа задачи.
- Где лежат спецификации методов: пространство `{CONFLUENCE_SPACE}`, заголовок вида
  `{SPEC_TITLE_PATTERN}`.
- Регламент создания мока: `{MOCK_REGULATION_URL}`; регламент переключения стенда:
  `{SWITCH_REGULATION_URL}`.
- Задача на добавление нового мока: шаблон `{MOCK_TICKET_TEMPLATE}`.

## Переключение сервиса на мок

| Что | Значение |
|---|---|
| Репозиторий конфигов деплоя | `{DEPLOY_CONFIG_REPO}` |
| Где лежит мок-профиль | `{MOCK_PROFILE_PATH}`, напр. `mocks/<slug>` |
| Где лежит конфиг стенда | `{STAND_CONFIG_PATH}`, напр. `group_vars/<slug>/<slug_underscored>_test_<N>` |
| Условие включения мок-профиля | `{MOCK_ENABLE_CONDITION}` — найти в скриптах деплоя и записать сюда |
| Как катить | `{DEPLOY_HOW}` — см. скилл `bamboo-deploy-ru`, если деплой через Bamboo |
| Как вернуть | `{ROLLBACK_HOW}` |

## Версионирование

- Эталон стабов: `{STUBS_REPO}/{STUBS_PATH}` — выгрузка `wm.py get <мок> > имя.json`.
- Конфиг переключения: PR в `{DEPLOY_CONFIG_REPO}`.

## Связанные скиллы

- `bamboo-deploy-ru` — релизы и деплой на стенд;
- `opensearch-logs-ru` — логи пода, результат вебхука на приёмнике;
- `qa-task-testing-ru` — прогон проверок по задаче.
