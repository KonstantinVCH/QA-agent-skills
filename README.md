# QA Agent Skills

Набор из 10 скиллов на русском для AI-агента. С ними агент ведёт работу QA-инженера: от задачи в Jira
до баг-репорта, отчёта о тестировании и тест-кейсов в Allure TestOps.

*Russian-language agent skills for QA engineers. English summary below.*

```bash
npx skills add KonstantinVCH/QA-agent-skills
```

## Что можно поручить агенту

| Вы пишете | Агент делает | Скилл |
|---|---|---|
| «Протестируй PROJ-123» | Читает задачу и документацию, находит все PR и составляет матрицу проверок. Присылает план и ждёт вашего «go». Только после этого прогоняет проверки на стенде и откатывает свои тестовые данные. | `qa-task-testing` |
| «Напиши критерии приёмки к PROJ-123» | Собирает таблицу критериев по задаче, коду и документации, отмечает риски, публикует комментарий в Jira после вашего подтверждения. | `acceptance-criteria` |
| «Оформи баг» | Пишет баг-репорт в два слоя: короткий для аналитика и подробный для разработчика с местом в коде, трейсом и шагами. | `bug-report` |
| «Оформи итоги тестирования» | Собирает отчёт о сессии для комментария в задаче: стенд и версии, что проверено и как, найденные баги, проверки после выкатки на прод. | `test-session-report` |
| «Заведи тест-кейс в Allure» | Создаёт мануальный тест-кейс через REST TestOps: шаги деревом, ожидаемые результаты, общие шаги, связь с задачей. | `allure-testops-cases` |
| «Выкати задачу на стенд 2» | Создаёт релизы в Bamboo и выкатывает сервисы параллельно. На прод-окружения не катит. | `bamboo-deploy` |
| «Найди ошибку в логах по traceId» | Ищет записи в OpenSearch/Kibana, сверяет с трейсом Jaeger, разбирает стектрейс. | `opensearch-logs` |
| «Сделай мок партнёра на ошибку 500» | Пишет WireMock-стаб по контракту из кода и спецификации, проверяет по журналу запросов, что сервис ходит в мок. | `wiremock-stubs` |

Полный список из 10 скиллов — [ниже](#все-скиллы).

## Как выглядит результат

Первый слой баг-репорта из `bug-report` (пример из скилла, домен вымышленный):

```text
Даты публикации товара уходят партнёру без часового пояса

Что вижу: при публикации товара партнёр X получает даты без часового пояса и в двух разных форматах.
Чем плохо: партнёр трактует время как своё локальное — расхождение до нескольких часов в витрине.
Как должно быть: дата с поясом +03:00, единый формат — по спеке партнёра X.
Масштаб и что делать: каждая публикация через новый адаптер; фикс в рамках задачи.
```

Под ним идёт слой для разработчика: предусловия, шаги с запросами, фактическое и ожидаемое тело,
ссылка на строку кода, трейс.

## Правила, по которым работают скиллы

Скиллами каждый день пользуется их автор, QA-инженер продуктовой команды. Правила ниже записаны
после ошибок на реальных прогонах:

- План тестирования согласуется с человеком до первого действия на стенде.
- Негативная проверка засчитывается, только если рядом прошла такая же позитивная.
- Тестовые данные, которые агент добавил в базу, он удаляет и доказывает это запросом.
- Перед оформлением бага агент проходит чеклист «не ложный ли это баг».
- Вывод без доказательства (трейс, запрос к базе, ответ API) помечается как гипотеза.
- Комментарии в Jira и тест-кейсы в Allure публикуются только после явного «да».

## Подойдёт ли вам

- **Трекер и вики.** Скрипты рассчитаны на Jira, Confluence и Bitbucket Server / Data Center с
  персональным токеном. С Jira Cloud и Confluence Cloud работают не все скрипты.
- **CI.** Скилл деплоя написан под Atlassian Bamboo.
- **Агент.** Формат [Agent Skills](https://agentskills.io) понимают Claude Code, Codex, Cursor и OpenCode.
  `qa-autopilot` экспериментальный и работает только в Claude Code: ему нужны субагенты и хуки.
- **Окружение.** Нужны curl и python3. MCP-серверы Jira, Confluence и баз данных подключаются по желанию.

Методика проверена в работе. Скрипты после удаления привязки к исходному проекту прогнаны на моках;
на ваших Jira, Bamboo и OpenSearch их ещё никто не запускал. Если что-то не сработает, откройте issue.

## Установка и первый запуск

1. Поставьте все скиллы или один:

   ```bash
   npx skills add KonstantinVCH/QA-agent-skills
   npx skills add KonstantinVCH/QA-agent-skills --skill qa-task-testing
   ```

   В Claude Code можно подключить репозиторий как маркетплейс плагинов:

   ```bash
   claude plugin marketplace add KonstantinVCH/QA-agent-skills
   claude plugin install qa-skills@qa-agent-skills
   ```

   Ещё один плагин — `obsidian-llm-wiki`.

2. Задайте переменные окружения один раз. Имена общие для всех скиллов:
   `JIRA_URL`, `JIRA_TOKEN`, `CONFLUENCE_URL`, `CONFLUENCE_TOKEN`, `BITBUCKET_URL`, `BITBUCKET_TOKEN`.
   Остальные (`BAMBOO_URL`, `ALLURE_URL`, `JAEGER_URL`, `OPENSEARCH_URL`, `WIREMOCK_URL`) нужны
   только соответствующим скиллам.

3. Заполните `references/project-config.md` в скиллах, которыми пользуетесь: ключ проекта, стенды,
   список сервисов, соглашения команды. Токены в этот файл не пишите.

## Все скиллы

### Тестирование

| Скилл | Что делает |
|---|---|
| [qa-task-testing](skills/qa-task-testing) | Полный цикл по задаче: задача и документация, карта изменений по PR, матрица проверок, план на согласование, прогон на стенде, откат тестовых данных, баги и тест-кейсы. |
| [qa-autopilot](skills/qa-autopilot) | **Экспериментальный:** публичная версия целиком ещё не запускалась. Тот же цикл силами шести субагентов: план проверяет агент-ревьюер, баги и тест-кейсы уходят наружу после одобрения человека. Хуки блокируют запись в Jira и Confluence, деплой вне списка стендов и `git push`. |
| [acceptance-criteria](skills/acceptance-criteria) | Критерии приёмки по задаче: таблица, риски по сигналам из кода, сценарии Given/When/Then. |
| [bug-report](skills/bug-report) | Баг-репорт в два слоя: для аналитика и для разработчика. |
| [test-session-report](skills/test-session-report) | Итоговый отчёт о сессии тестирования для комментария в задаче. |
| [allure-testops-cases](skills/allure-testops-cases) | Мануальные тест-кейсы в Allure TestOps через REST, с описанием 17 ловушек API. |
| [wiremock-stubs](skills/wiremock-stubs) | WireMock-стабы для интеграций: контракт, негативные сценарии, вебхуки, проверка по журналу. |
| [opensearch-logs](skills/opensearch-logs) | Логи Kubernetes-сервисов в OpenSearch/Kibana, связка с трейсами Jaeger, разбор упавшего деплоя. |
| [bamboo-deploy](skills/bamboo-deploy) | Релизы в Bamboo и параллельная выкатка на тестовый стенд, пре-чек миграций Flyway. |

### База знаний

| Скилл | Что делает |
|---|---|
| [obsidian-llm-wiki](skills/obsidian-llm-wiki) | База знаний команды в Obsidian по схеме [LLM Wiki Андрея Карпатого](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): агент читает её перед работой и дописывает находки после. |

## Как скиллы связаны

`qa-task-testing` вызывает остальные по ходу работы. Баг оформляет через `bug-report`, итог — через
`test-session-report`, тест-кейсы — через `allure-testops-cases`. На стенде он пользуется
`bamboo-deploy`, `wiremock-stubs` и `opensearch-logs`. Каждый скилл можно поставить отдельно.

## Обратная связь

Ошибки и предложения — в [issues](https://github.com/KonstantinVCH/QA-agent-skills/issues).
Если адаптировали скилл под GitHub, GitLab или Jira Cloud, присылайте PR.

## English

Ten agent skills for QA engineers, written in Russian. They cover testing a Jira
ticket end to end: PR diff map, test matrix, plan approval before any action on staging, checks with SQL
fixtures and traces, cleanup. They also cover acceptance criteria, two-layer bug reports, test session
reports, Allure TestOps test cases via REST, WireMock stubs, OpenSearch/Jaeger logs, Bamboo deployments
and an Obsidian LLM wiki.
Install: `npx skills add KonstantinVCH/QA-agent-skills`.

## Лицензия

[MIT](LICENSE)
