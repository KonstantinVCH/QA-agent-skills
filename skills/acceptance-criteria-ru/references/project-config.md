# Настройки проекта

Заполни один раз под свою команду. Скилл читает этот файл, чтобы не спрашивать одно и то же.
Секреты (токены, пароли) сюда НЕ писать — только в переменные окружения.

## Системы

| Что | Значение | Переменная окружения |
|---|---|---|
| Jira (Server/Data Center) | `{JIRA_URL}` | `JIRA_URL`, токен — `JIRA_TOKEN` |
| Confluence | `{CONFLUENCE_URL}` | `CONFLUENCE_URL`, токен — `CONFLUENCE_TOKEN` |
| Bitbucket Server | `{BITBUCKET_URL}` | `BITBUCKET_URL`, токен — `BITBUCKET_TOKEN` |
| Трейсинг (Jaeger и т.п.) | `{JAEGER_URL}` | — |
| Тест-менеджмент (Allure TestOps, TestRail) | `{ALLURE_URL}`, проект `{ALLURE_PROJECT_ID}` | — |

Где взять токены: Jira и Confluence — «Профиль → Personal Access Tokens»; Bitbucket Server —
«Manage account → HTTP access tokens» (права на чтение репозиториев).

## Jira

- Ключ проекта: `{PROJ}` (номера задач вида `PROJ-123`)
- Поле «Как проверить» / «Шаги проверки»: `{customfield_XXXXX}` → `JIRA_HOWTO_FIELD`
  (узнать id: `GET {JIRA_URL}/rest/api/2/field`, найти по name)
- Куда публиковать AC по умолчанию: описание задачи / комментарий
- Заголовок блока AC (если в команде принят свой): `h2. ✅ Критерии приёмки: …`

## Архитектура

- Сервисы и их роли: `{order-service — ядро заказов; shop-api — фасад для клиента; …}`
- Слои: фасад `{*-api}` → ядро `{*-service}` (где живёт обогащение данных)
- Брокер сообщений: `{RabbitMQ / Kafka}`, основные exchange/топики: `{…}`
- Feature-флаги: `{Togglz / Unleash / LaunchDarkly}`, где консоль: `{URL}`
- CI/CD: `{Bamboo / Jenkins / GitLab CI}`

## Соглашения команды

- Конвенция тестовой документации: `{ссылка на страницу}`
- Терминология (как в команде называют сущности): `{…}`
- База знаний QA (если есть): `{путь к vault / раздел wiki}`

## Проектные риски

Дополнение к `risk-signals.md`. Формат строки: область | риск | урок (номер задачи).

| Область | Риск | Урок |
|---|---|---|
| `{партнёр / флоу}` | `{что ломается}` | `{PROJ-123}` |
