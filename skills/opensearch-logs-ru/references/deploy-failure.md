# Разбор упавшего деплоя (Bamboo → Kubernetes)

Читать, когда на входе `deploymentResultId` или ссылка вида
`{BAMBOO_URL}/deploy/viewDeploymentResult.action?deploymentResultId=…`. Сам деплой и его
перезапуск — скилл `bamboo-deploy-ru`; здесь только диагноз.

Разбирай сразу после падения: ретеншн логов короткий (обычно 1–10 суток), через неделю
останется только лог Bamboo.

## 1. Забрать результат деплоя

```bash
curl -sS -H "Authorization: Bearer $BAMBOO_TOKEN" -H "Accept: application/json" \
  "$BAMBOO_URL/rest/api/latest/deploy/result/{ID}?includeLogs=true&max-results=5000" -o deploy.json
```

Из JSON взять:
- `deploymentState`, `deploymentVersionName`, `startedDate` / `finishedDate` (epoch ms) —
  **окно для запросов логов: started − 5 мин … finished + 10 мин**, не `now-15m`;
- лог = склейка `logEntries.logEntry[].unstyledLog`;
- причина: строки `fatal:`, `TASK [Error message]`, `ProgressDeadlineExceeded`,
  `Application startup failed`, `Resource creation timed out`, `BackoffLimitExceeded`;
- ссылки на логи приложения и миграций, если деплой-скрипт их печатает
  (`Application logs https://…`, `Migration logs https://…`).

Кодировка: лог Bamboo с Windows-агентов или от скриптов с кириллицей может прийти в cp1251 —
русские строки читаются мусором. Английские строки и трейсы не страдают; перекодируй, только
если нужен русский текст.

## 2. Сначала решить, кто упал (иначе получишь ноль записей)

Финальное сообщение вида `Application startup failed, see application log for details`
печатается **и** когда не стартовал сервис, **и** когда упала джоба миграций. Само по себе оно
диагнозом не является. Смотри, какая таска упала выше:

| В логе деплоя | Кто упал | Чей лог читать |
|---|---|---|
| `TASK [Database migration job]` (или аналог), `Resource creation timed out`, в манифесте Job `reason: BackoffLimitExceeded`, `failed: N` | Job миграций; сервис не стартовал вообще | контейнер мигратора, под `{service}-migrations-*` → §3 |
| `ProgressDeadlineExceeded`, у Deployment `readyReplicas` < `replicas` | новый под сервиса не прошёл health-check (старый жив) | контейнер = имя сервиса → обычный запрос ошибок |

Классы важно не путать: у них разные виновники. Миграция обычно падает от **данных стенда**
(лечится SQL за минуту, релиз пересобирать не нужно), старт сервиса — от **кода или конфига**
(нужен фикс разработчика или правка конфигурации).

Ссылка «Application logs» из лога деплоя годится только для второго класса: её фильтр стоит по
имени сервиса, а миграции пишет другой контейнер.

## 3. Логи джобы миграций — другие поля

Логи мигратора часто не парсятся в структуру, и типовой запрос по `level` возвращает ноль при
живых логах — это легко принять за ретеншн.

| Поле | Обычный сервис | Мигратор (сырые логи) |
|---|---|---|
| текст события | `message` / `messagetext` | `message` |
| `level` | есть | нет |
| stacktrace | отдельное поле | построчно, отдельными документами |

```js
// 1) найти поды джобы (попыток обычно backoffLimit + 1)
{size:0, query:{bool:{filter:[
   {match_phrase:{"kubernetes.namespace.keyword":"{namespace}"}},
   {match_phrase:{"kubernetes.container.name.keyword":"{migrator-container}"}},
   {range:{"@timestamp":{gte:"<started-5м>",lte:"<finished+10м>"}}}]}},
 aggs:{p:{terms:{field:"kubernetes.pod.name.keyword",size:10}}}}

// 2) прочитать один под целиком, по порядку строк
{size:300, sort:[{"@timestamp":"asc"},{"log.offset":"asc"}],
 _source:["@timestamp","message"],
 query:{bool:{filter:[
   {match_phrase:{"kubernetes.pod.name.keyword":"<pod>"}},
   {range:{"@timestamp":{gte:"…",lte:"…"}}}]}}}
```

Склей `message` через `\n` и читай как обычный лог. Для Flyway там будет блок с `SQL State`,
`Message`, `Location` и текстом упавшего `Statement`. Скриптом:
`python scripts/k8s_logs.py --stand N --service {migrator-container} --since 24h`
(контейнер должен быть в `LOGS_RAW_CONTAINERS`).

Что означают типовые коды PostgreSQL (это уже вердикт, не догадка):
- `23503` — FK не встаёт: в таблице есть строки без родителя (часто мусор тестовых прогонов) →
  **данные стенда**;
- `23505` — уникальный индекс на данных с дублями → **данные стенда**;
- `42701` / `already exists` — объект в схеме есть, записи в истории миграций нет → **окружение**;
- `checksum mismatch` / `missing migration` / `applied migration not resolved locally` —
  расхождение образа мигратора и `flyway_schema_history` → **окружение**;
- `relation … does not exist`, синтаксическая ошибка → **код миграции**.

Как снимать такие блокеры — `bamboo-deploy-ru`, файл `references/migrations.md`.

## 4. Вердикт «данные, а не код» — только с двумя фактами

1. SQL по стенду: найти сами строки-нарушители.
2. `flyway_schema_history` другого стенда, где та же версия применена успешно.

И проверь строку `Successfully validated N migrations`: если N равно числу файлов миграций в
основной ветке, а миграция задачи живёт только в ветке задачи — на стенд она не поедет даже
после фикса данных (образ мигратора собран из основной ветки).

## 5. Старт сервиса упал, а app-логов нет

Если логи уже вычищены или под не успел ничего написать:
1. Коммиты билда: `GET {BAMBOO_URL}/rest/api/latest/result/{planKey}-{N}?expand=changes` →
   в Bitbucket `commits/{hash}/changes`, `diff/pom.xml` (или другой файл зависимостей).
2. Новая зависимость → скачать её артефакт из репозитория артефактов (Nexus/Artifactory) и
   посмотреть автоконфигурации (`META-INF/spring.factories` или `…AutoConfiguration.imports`):
   они грузятся при каждом старте.
3. Сверить `<scope>` в pom сервиса: `scope=test` у нужного в runtime артефакта →
   `ClassNotFoundException` при зелёных тестах и зелёном билде.
4. Второй частый класс — новый код требует проперть, которой нет в отрендеренном конфиге:
   сравнить шаблон конфигурации сервиса в ветке задачи и в основной ветке.
5. Трейсы Jaeger: span logs содержат level/logger/message и часто заменяют логи пода.

## 6. Логов нет (ретеншн) → база знаний проекта

Root cause старого падения часто уже записан командой. Если у проекта есть база знаний
(`project-config.md`, раздел 6) — поищи по номеру задачи (из имени релиза) и по имени сервиса:
упоминания падения, `Could not resolve placeholder`, `Caused by`, раздел про конфиги.

- Если база лежит в git на Bitbucket Server — страница читается через
  `GET {BITBUCKET_URL}/rest/api/1.0/projects/{KEY}/repos/{repo}/raw/{path}`.
  Code search на Bitbucket Server бывает выключен: пустой результат поиска ≠ отсутствие.
  Надёжнее скачать архив ветки (`/archive?at=refs%2Fheads%2F{branch}&format=zip`) и grep.
- 404 на странице — нормально: пиши в отчёт «в базе знаний не найдено».
- Найденное помечай источником: «из базы знаний, страница …» — это ранее зафиксированный факт,
  а не текущий лог.

## 7. Отчёт по падению

Класс падения (§2: сервис или Job миграций) → причина из лога Bamboo (упавшая таска, `fatal:`)
→ ошибки логов нужного контейнера (timestamp, level/`message`, `Caused by` из stacktrace) →
вердикт: код / конфиг / **данные стенда** (SQL-подтверждение + сравнение со стендом, где
прошло) / логов нет из-за ретеншна. Ссылки: результат деплоя + ссылки на логи + Discover.

Для класса «миграции» в отчёт обязательно: имя упавшей миграции (`V###__*.sql`), SQL State,
конкретные строки-нарушители из БД, состояние `flyway_schema_history` на этом и на «здоровом»
стенде и ответ на вопрос, поедет ли вообще миграция задачи (`Successfully validated N migrations`).
Без этого отчёт не отвечает на главный вопрос — чинить данные или ждать разработчика.

## 8. Примеры разборов (обезличенные)

**Падение старта: конфиг, а не код.** Деплой `order-service` релиза `PROJ-123-2` на стенд 3 —
FAILED, `ProgressDeadlineExceeded`. Логи за окно деплоя уже вычищены (`_count = 0` за тот день),
живой поток namespace при этом есть — значит, ретеншн, а не сломанный поиск. Root cause нашёлся
в базе знаний по номеру задачи: `Could not resolve placeholder 'order.payment.default-return-url'`
— под уехал с конфигом основной ветки без пропертей задачи. Вердикт: конфиг, не код.

**Падение на джобе миграций: данные стенда.** Деплой `catalog-service` релиза `PROJ-456-1` на
стенд 1 — FAILED, в логе тот же `Application startup failed`, но выше —
`TASK [Database migration job]`: `Resource creation timed out`, Job `BackoffLimitExceeded`.
Первый запрос по `level` дал ноль; агрегация по контейнерам показала сотни записей мигратора —
поле оказалось `message`. В логе пода: Flyway `V129__add_fk_product_partner.sql`,
`SQL State 23503`, `Key (partner_code)=(2315) is not present in table "partner"`. SQL по стенду —
две строки-сироты; на стендах 2 и 3 та же V129 применена → вердикт «данные стенда, не код».
Попутно `Successfully validated 129 migrations` показал, что `V130` из ветки задачи в образе нет.
