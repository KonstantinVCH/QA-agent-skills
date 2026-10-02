# Настройка автопилота под проект

Заполни этот файл под свою команду. Всё, что ниже в фигурных скобках, — плейсхолдеры. Роли
(`assets/agents/qa-*.md`) читают его, когда им нужны адреса систем и соглашения команды. Скрипты
настраиваются **переменными окружения**: они перечислены в таблицах; задай их в профиле shell или
в блоке `env` файла `~/.claude/settings.json`.

Секреты (токены, пароли, DSN с паролем) в этот файл не пиши: только в переменные окружения.

## 1. Системы и доступы

| Система | Переменные | Где взять | Обязательна? |
|---|---|---|---|
| Трекер задач (Jira Server/DC) | `JIRA_URL={JIRA_URL}`, `JIRA_TOKEN` | профиль Jira → Personal Access Tokens | да |
| Вики (Confluence) | `CONFLUENCE_URL={CONFLUENCE_URL}`, `CONFLUENCE_TOKEN` | профиль Confluence → Personal Access Tokens | если спеки в вики |
| Git-хостинг (Bitbucket Server) | `BITBUCKET_URL={BITBUCKET_URL}`, `BITBUCKET_TOKEN` | профиль Bitbucket → HTTP access tokens (чтение; запись — только для тестовых веток) | для карты PR |
| Деплой (Bamboo) | `BAMBOO_URL={BAMBOO_URL}`, `BAMBOO_TOKEN` | профиль Bamboo → Personal access tokens | без `--assume-deployed` |
| Трейсы (Jaeger) | `JAEGER_URL={JAEGER_URL}` | UI Jaeger, обычно без авторизации во внутренней сети | очень желательна |
| Логи (OpenSearch/Kibana) | `OPENSEARCH_URL={OPENSEARCH_URL}` | адрес Dashboards/Kibana | для разбора деплоя |
| Моки (WireMock) | `WIREMOCK_URL={WIREMOCK_URL}` | адрес общего WireMock | если есть моки |
| Тест-кейсы (Allure TestOps) | `ALLURE_URL={ALLURE_URL}`, токен — по `allure-testops-cases-ru` | профиль Allure → API tokens | для фазы PUBLISH |
| БД стенда (запись фикстур) | `QA_DB_DSN_<STAND>` (например `QA_DB_DSN_TEST_3`) | у DevOps или администратора стенда | для фикстур и откатов |
| База знаний (Obsidian Local REST API) | `OBSIDIAN_HOST`, `OBSIDIAN_PORT` (по умолчанию 27124) | плагин Local REST API в Obsidian | нет |
| API-коллекции (Bruno, Postman) | `QA_API_COLLECTIONS_REPO` — путь к локальному клону | репозиторий команды | нет |

Внутренние системы на самоподписанных сертификатах: `QA_AUTOPILOT_INSECURE_TLS=1` отключает
проверку TLS в `preflight.py`. Включай осознанно.

MCP-серверы вместо REST. Роли сначала пробуют MCP-инструменты, если подключены, иначе REST через
`curl` с токенами из окружения. Популярные публичные серверы: `mcp-atlassian` (Jira и
Confluence), MCP-сервер Allure TestOps, Jaeger MCP, PostgreSQL MCP в режиме только чтения.
Названия инструментов своей установки впиши в поле `tools:` файлов агентов (см. `install.md`).

| Назначение | Имя MCP-инструмента в твоей установке |
|---|---|
| чтение задачи | `{mcp__<server>__jira_get_issue}` |
| поиск задач | `{mcp__<server>__jira_search}` |
| чтение страницы вики | `{mcp__<server>__confluence_get_page}` |
| трейс по id | `{mcp__<server>__get_trace}` |
| read-only SQL | `{mcp__<server>__query}` |
| поиск в базе знаний | `{mcp__<server>__simple_search}` |

## 2. Проект и стенды

| Что | Значение | Переменная |
|---|---|---|
| Ключ проекта в трекере | `{PROJ}` (задачи вида `PROJ-123`) | `QA_AUTOPILOT_TASK_RX` — regex ключа, по умолчанию `[A-Z][A-Z0-9]+-\d+` |
| Разрешённые тестовые стенды | `{test-1,test-2,test-3}` | `QA_AUTOPILOT_STANDS` — **обязательно** |
| Шаблон адреса сервиса на стенде | `{http://<service>.<stand>.example.com}` | — |
| Запрещённые окружения | `prod,production,stable` (+ свои: `staging`, `preprod`) | `QA_AUTOPILOT_FORBIDDEN_ENVS` |
| Prod-хосты (логи, БД, приложение) | `{logs.prod.example.com, db-prod.example.com}` | `QA_AUTOPILOT_PROD_HOSTS` |
| Каталог прогонов | `~/qa-autopilot-runs` | `QA_AUTOPILOT_ROOT` |
| Лимит токенов прогона по умолчанию | 4 млн | флаг `--budget-mtok` |
| Ставка для оценки в валюте | `{N}` за миллион токенов | `QA_AUTOPILOT_USD_PER_MTOK` |

Имя стенда должно быть различимым в командах деплоя: гард требует, чтобы команда деплоя называла
разрешённый стенд. Имя `3` встречается где угодно, имя `test-3` — нет.

## 3. Инфраструктура и деплой

| Что | Значение | Переменная |
|---|---|---|
| Инфраструктурный репозиторий (конфигурация сервисов под стенды) | `{INFRA_REPO}` | — |
| Репозитории, где человек может разрешить тестовую ветку | `{INFRA_REPO}` | `QA_AUTOPILOT_BRANCH_REPOS` |
| Как распознать запрос на деплой | по умолчанию очередь деплоя Bamboo (`/rest/api/latest/queue/deployment`, `/deploy/`) | `QA_AUTOPILOT_DEPLOY_RX` |
| С каким деплоем едут миграции каждой схемы | `{схема → сервис или мигратор}` | — |
| Проектные hard-stop команды (например, переключение общего ингресса партнёров) | `{regex}` | `QA_AUTOPILOT_EXTRA_DENY` |
| Проектные запрещённые MCP-инструменты | `{regex}` | `QA_AUTOPILOT_EXTRA_MCP_DENY` |

## 4. Сервисы и данные (для ролей)

| Сервис | Репозиторий | Схема БД | Что делает |
|---|---|---|---|
| `{order-service}` | `{repo}` | `{orders}` | `{приём заказов}` |
| `{payment-service}` | `{repo}` | `{payments}` | `{оплата через внешнего провайдера}` |
| `{notification-service}` | `{repo}` | — | `{письма и пуши}` |

Внешние интеграции (партнёры, адаптеры, моки): `{partner → adapter → мок/реальный, порт мока}`.

Где лежат эталонные E2E-кейсы: `{проект в Allure TestOps, тег E2E, поле фичи/Story}`.

## 5. Соглашения команды

- Куда публикуется отчёт сессии: `{подзадача «Тестирование» в задаче | комментарий в задаче}`.
- Формат баг-репорта: `{bug-report-ru по умолчанию | ссылка на регламент}`.
- Тест-кейсы: первое предложение description — `{«Тест-кейс позволяет проверить …»}`; теги —
  `{ключ задачи}`; обязательные поля — `{…}`.
- Как помечать тестовые данные: `run_tag` в `external_id` и комментариях SQL; имена стабов
  WireMock начинаются с `run_tag`.
- База знаний: `{нет | Obsidian, путь к vault, протокол obsidian-llm-wiki-ru}`.
