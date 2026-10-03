# Настройки проекта

Заполни один раз под свою команду. Секреты (токены, пароли) сюда НЕ писать — только в
переменные окружения.

## Системы

| Что | Значение | Переменная окружения |
|---|---|---|
| Трекер задач | `{JIRA_URL}`, ключ проекта `{PROJ}` | `JIRA_URL` |
| Спецификации | `{CONFLUENCE_URL}` | `CONFLUENCE_URL` |
| Git-хостинг | `{BITBUCKET_URL}` / GitLab / GitHub | `BITBUCKET_URL`, токен — `BITBUCKET_TOKEN` |
| Трейсинг | `{JAEGER_URL}` | `JAEGER_URL` (для `scripts/trace_warnings.py`) |
| Логи | `{OPENSEARCH_URL}` (Kibana / OpenSearch Dashboards), index pattern `{…}` | — |
| Моки | `{WIREMOCK_URL}` | — |
| Стенды | `{STAND}`: адреса сервисов по шаблону `{http://{service}.{N}.example.com}` | — |
| Чат команды | `{ссылка на канал}` | — |

Если API трейсинга закрыто авторизацией — добавь заголовок через `JAEGER_TOKEN` (скрипт
передаст его как `Authorization: Bearer`).

## Шаблоны ссылок на git (ветка обязательна)

Bitbucket Server / Data Center:
- файл на строке: `{BITBUCKET_URL}/projects/{PROJECT}/repos/{repo}/browse/{path}?at=refs%2Fheads%2F{branch}#{line}`
- коммит: `{BITBUCKET_URL}/projects/{PROJECT}/repos/{repo}/commits/{hash}`

GitLab:
- файл на строке: `{GITLAB_URL}/{group}/{repo}/-/blob/{branch}/{path}#L{line}`
- коммит: `{GITLAB_URL}/{group}/{repo}/-/commit/{hash}`

GitHub:
- файл на строке: `https://github.com/{owner}/{repo}/blob/{branch}/{path}#L{line}`
- коммит: `https://github.com/{owner}/{repo}/commit/{hash}`

## Шаблоны ссылок на трейсы и логи

- Jaeger, сразу на спан: `{JAEGER_URL}/trace/{traceId}?uiFind={spanId}`
- Kibana / OpenSearch Dashboards, одна запись: `{OPENSEARCH_URL}/app/discover#/doc/{index-pattern-id}/{index}?id={_id}`
- Выборка: discover с фильтрами namespace + pod/container, запрос `level:"ERROR"`, абсолютное
  время `_g=(time:(from:'2026-01-01T10:00:00Z',to:'2026-01-01T11:00:00Z'))`

## Соглашения команды

- Как называются сущности в речи команды (термины для слоя 1): `{…}`
- Кому адресовать вопросы по спеке (роль, не имя): `{системный аналитик фичи}`
- Где принято публиковать баги: `{подзадача / отдельный тип Bug / комментарий}`
