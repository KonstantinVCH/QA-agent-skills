# Настройка под проект

Шаблон: заполни один раз под свой проект и держи рядом со скиллом (или в базе знаний команды).
Всё, что ниже в фигурных скобках, — плейсхолдеры. Секреты сюда не писать: только имена переменных
окружения.

## Доступы (переменные окружения)

| Переменная | Значение | Где взять |
|---|---|---|
| `ALLURE_URL` | `{ALLURE_URL}`, напр. `https://allure.example.com` | адрес инстанса TestOps |
| `ALLURE_TOKEN` | API-токен | `{ALLURE_URL}` → аватар → API tokens → создать |
| `ALLURE_PROJECT_ID` | `{PROJECT_ID}` | число в URL проекта: `{ALLURE_URL}/project/<id>/…` |
| `ALLURE_TREE_ID` | `{TREE_ID}` | открыть дерево кейсов → id в адресной строке или в запросе `testcasetree/entity` в DevTools |
| `ALLURE_JIRA_INTEGRATION_ID` | `{JIRA_INTEGRATION_ID}` | `GET /api/testcase/<id>/issue` у кейса, уже связанного с Jira → поле `integrationId` |
| `JIRA_URL` | `{JIRA_URL}` | адрес Jira, без `/browse` |
| `CONFLUENCE_URL`, `CONFLUENCE_TOKEN` | `{CONFLUENCE_URL}` и PAT | для чтения спецификаций; профиль Confluence → Personal Access Tokens |
| `ALLURE_DOC_LINK_MARKER` | подстрока ссылки на документацию | по умолчанию `CONFLUENCE_URL`; поменять, если спеки живут в другом месте |
| `ALLURE_NAME_STOP_WORDS` | `позитивный,негативный,проверка` | стоп-слова в названии по конвенции команды |
| `ALLURE_NAME_MAX_LEN` | `120` | порог длины названия для `audit` |
| `ALLURE_STATUS_DRAFT_ID` / `ALLURE_WORKFLOW_MANUAL_ID` | `-1` / `1` | поля `status.id` и `workflow.id` в карточке любого черновика |

Проверка доступа: `python scripts/allure_tc.py show <id любого кейса>`.

Если токена нет — попроси пользователя создать его самостоятельно и сохранить в переменную
окружения. Не проси вставлять токен в чат и не вводи его в формы сам.

## Статусы

| Что | Id | Как перевести |
|---|---|---|
| Черновик (создание) | `{STATUS_DRAFT_ID}`, обычно `-1` | ставится при `create` |
| На ревью (после создания) | `{STATUS_REVIEW_ID}` | `PATCH /api/testcase/{id} {"statusId": {STATUS_REVIEW_ID}}` |

Канал, куда ссылку на кейс публикуют для ревью: `{REVIEW_CHANNEL}`. Публикует пользователь.

## Конвенция тестовой документации

Регламент команды: `{CONFLUENCE_URL}/pages/viewpage.action?pageId={CONVENTION_PAGE_ID}`.
Если регламента нет, по умолчанию действует формат из раздела «Формат ТК» в SKILL.md.

Эталонные кейсы проекта (few-shot: структуру брать с них):

| Id | Чем хорош как образец |
|---|---|
| `{ETALON_TC_1}` | кейс на API-метод: curl во вложении, ОР с кодом и полями |
| `{ETALON_TC_2}` | UI-сценарий со скриншотами в ОР |
| `{ETALON_TC_3}` | кейс с общим шагом (shared step) |

## Схема дерева (custom fields)

| Уровень | Custom field | Id поля | Пример значения |
|---|---|---|---|
| 1 | Feature | `{CF_FEATURE_ID}` | `Services` |
| 2 | Story | `{CF_STORY_ID}` | `order-service` |
| 3 | Layer | `{CF_LAYER_ID}` | `[GET] /orders/{id}/status - Метод получения статуса заказа` |

Вторая схема для сценарных кейсов (не про конкретную ручку): Feature = `{SCENARIO_FEATURE}`
(напр. `Клиентский путь`), Story = функциональность, Layer не заполняется.

## Где живут спецификации методов

- Пространство Confluence: `{CONFLUENCE_SPACE}`.
- Формат заголовка страницы метода: `{SPEC_TITLE_PATTERN}` (напр. `[GET] /orders/{id}/status - …`).
- Список описанных методов (если ведётся): `{METHODS_INDEX_PAGE}`.

## Окружения для проверок в шагах

| Что | Значение |
|---|---|
| Стенды | `{STAND}`: перечень тестовых стендов, напр. `test-1 … test-5` |
| БД стендов | хост `{DB_HOST}:{DB_PORT}`, базы `{DB_NAME_PATTERN}`; креды — в переменных окружения |
| Схемы, чаще всего нужные в ОР | `{DB_SCHEMAS}`, напр. `orders`, `payments`, `customers` |
| Трейсы | `{JAEGER_URL}` |
| Логи | `{OPENSEARCH_URL}` |

Отметь, какие подключения только на чтение (MCP-серверы БД часто read-only): для записи фикстур
нужен драйвер напрямую с тем же DSN.

## Связанные скиллы

- `qa-task-testing` — прогон проверок по задаче, тест-данные на стенде;
- `bamboo-deploy` — деплой на стенд;
- `bug-report` — оформление дефекта, когда ОР по спеке не сходится с поведением.
