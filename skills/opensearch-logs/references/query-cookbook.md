# Запросы к логам: транспорты, конструктор, ловушки

Читать, когда скрипта `k8s_logs.py` не хватает: нужен нестандартный фильтр, агрегация или
разбор «почему ноль». Содержание:

1. Транспорты — одно тело запроса, три обёртки
2. Конструктор запросов (блоки `bool.filter`)
3. Ловушки полей и регистра
4. «Ноль записей» — чек-лист, прежде чем писать «логов нет»
5. Ссылка в Discover для человека
6. Проверочный набор: 10 кейсов на новом кластере

---

## 1. Транспорты — одно тело запроса, три обёртки

Тело ES DSL (`query` / `aggs` / `_source` / `sort`) одинаково везде. Меняются URL, заголовки и
место ответа.

| | Kibana `/internal/search/es` | `/api/console/proxy` (Kibana и OpenSearch Dashboards) | Прямой REST кластера |
|---|---|---|---|
| вызов | `POST {OPENSEARCH_URL}/internal/search/es` | `POST {OPENSEARCH_URL}/api/console/proxy?path={index}%2F_search&method=POST` | `POST {ES_URL}/{index}/_search` |
| обёртка | `{"params":{"index":…,"body":{…}}}` | тело ES напрямую | тело ES напрямую |
| ответ | `rawResponse.hits` | `hits` | `hits` |
| заголовки | `kbn-xsrf: true`, `x-elastic-internal-origin: Kibana`, `elastic-api-version: 1` | `kbn-xsrf: true` (Kibana) или `osd-xsrf: true` (OpenSearch Dashboards) | авторизация кластера |

```bash
curl -sS -m 20 -X POST "$OPENSEARCH_URL/internal/search/es" \
  -H "Content-Type: application/json" -H "kbn-xsrf: true" \
  -H "x-elastic-internal-origin: Kibana" -H "elastic-api-version: 1" \
  -d '{"params":{"index":"'"$LOGS_INDEX"'","body":{
    "size":10,"sort":[{"@timestamp":"desc"}],
    "_source":["@timestamp","level","kubernetes.container.name","message","trace","traceId"],
    "query":{"bool":{"filter":[
      {"match_phrase":{"kubernetes.namespace.keyword":"testing-1"}},
      {"match_phrase":{"kubernetes.container.name.keyword":"order-service"}},
      {"terms":{"level":["error","warn"]}},
      {"range":{"@timestamp":{"gte":"now-24h"}}}]}}}}}'
```

Особенности, на которых теряют время:

- **`/internal/search/es` отвечает только на POST.** Открыть этот адрес в браузере — получить
  `{"statusCode":404}`. Это не «нет доступа» и не «нет эндпоинта».
- **`console/proxy` обрезает счётчик на 10000**: `hits.total` приходит как
  `{"value":10000,"relation":"gte"}`. Для подсчётов добавляй в тело `"track_total_hits": true`
  (тогда `relation: eq` и точное число). `/internal/search/es` Kibana подставляет это сама.
- Тем же `console/proxy` удобно смотреть состав и размер индексов:
  `POST /api/console/proxy?path=_cat%2Findices%2F{index}%3Fh%3Dindex%2Cdocs.count%2Cstore.size&method=GET`.
- На некоторых Kibana `console/proxy` из браузерной сессии отдаёт 403, а `/internal/search/es`
  работает — пробуй второй транспорт, прежде чем делать вывод о правах.
- Ответ ES с ошибкой приходит с HTTP 200 и полем `error`. **Проверяй `error` до чтения `hits`**,
  иначе вместо причины получишь «Cannot read properties of undefined» / `KeyError`.

## 2. Конструктор запросов

Собирай query из блоков `bool.filter`. Точные значения — по `.keyword`-полям; текст — по
анализируемому полю (`message` / `messagetext`).

| Что ищем | Блок запроса |
|---|---|
| Стенд | `{"match_phrase":{"kubernetes.namespace.keyword":"testing-3"}}` |
| Один сервис | `{"match_phrase":{"kubernetes.container.name.keyword":"order-service"}}` |
| Группа сервисов | `bool.should` из `match_phrase` по контейнерам + `"minimum_should_match":1` |
| Метод / фраза | `{"match_phrase":{"message":"createOrder"}}` |
| Клиент / id сущности | `{"match_phrase":{"message":"orderId: 21340429"}}` или просто `"21340429"` |
| Сквозной трейс | `{"match_phrase":{"traceId":"<32hex>"}}` — свяжет все сервисы одного запроса |
| Регэксп по тексту | `{"regexp":{"message":".*create_order.*"}}` — **только lowercase** |
| Только ошибки | `{"term":{"level":"error"}}` — регистр см. §3 |
| Период | `{"range":{"@timestamp":{"gte":"now-1h"}}}` или явные ISO-границы |
| Топ виновников | `"size":0, "aggs":{"c":{"terms":{"field":"kubernetes.container.name.keyword","size":5}}}` |
| Класс-источник | `{"term":{"logger.keyword":"com.example.order.service.OrderService"}}` — **полное имя** |
| Класс по хвосту имени | `{"wildcard":{"logger.keyword":"*OrderService"}}` |
| Какие контейнеры вообще живы | `"size":0`, `aggs terms kubernetes.container.name.keyword` по namespace за 1 ч |
| Где сервис пишет | `"size":0`, `aggs terms kubernetes.namespace.keyword` с фильтром по контейнеру |

Поле, в котором лежит текст события и traceId, у разных сервисов бывает разным
(`message`/`messagetext`, `traceId`/`trace_id`). Ищи сразу по всем вариантам через
`bool.should` — `k8s_logs.py` делает именно так.

## 3. Ловушки полей и регистра

- **Анализируемые поля хранятся в нижнем регистре.** Если `level` и текст — тип `text`,
  `term {"level":"ERROR"}` и `regexp ".*CREATE_ORDER.*"` возвращают 0 без единой ошибки.
  Пиши в нижнем регистре. Если `level` — `keyword`, наоборот: значение должно совпасть с логом
  буква в букву (`ERROR`). Не знаешь тип — перечисли оба регистра в `terms`.
- **`level.keyword` может быть пустым** даже при живом `level` — зависит от маппинга. Сначала
  проверь, что фильтр вообще что-то возвращает на заведомо живом сервисе.
- **terms-агрегация — только по keyword-полям.** `aggs terms {"field":"level"}` по text-полю
  падает с «Fielddata is disabled». Агрегируй по `*.keyword` или по полю типа keyword.
- **`logger` не анализируется**: `{"match_phrase":{"logger":"OrderService"}}` даёт 0, хотя
  класс в логах есть. Нужно полное имя через `term` по `logger.keyword` либо `wildcard`.
  Имена классов удобнее сначала получить агрегацией `terms logger.keyword`, потом фильтровать
  по готовому значению.
- **`_source` с несуществующим полем — не ошибка, а пустота в выводе.** Если весь столбец пуст,
  поле у этого контейнера другое: запроси одну запись без `_source` и посмотри реальные ключи.
- **Stacktrace — в отдельном поле** (`trace`, `error.stack_trace`, `stack_trace`). Текст события
  — только первая строка. `Caused by` ищи в поле stacktrace.
- **Контейнеры с неразобранными логами** (джобы миграций, сайдкары, нестандартные логгеры):
  текст лежит в `message`, полей `level` и stacktrace нет, stacktrace приходит построчно
  отдельными документами. Типовой запрос по `level` вернёт ноль при живых логах. Читай такие
  контейнеры по поду, отсортировав по `@timestamp` и `log.offset`.
- **Наборов индексов может быть несколько** (приложения отдельно, инфраструктура или «чужие»
  контуры отдельно). Ищи по всем сразу: `index: "a-*,b-*"` стоит миллисекунды, а поиск в одном
  наборе отдаёт ноль, неотличимый от «сервис молчал».
- **Имена индексов теста и прода могут совпадать.** Отличаются хосты. Перед выводом о проде
  сверяй хост, а не имя индекса.
- **Глубину ретеншна меряй гистограммой**, а не `min(@timestamp)`: редкие старые записи создают
  иллюзию длинной истории. `date_histogram` по дням за 14 суток покажет реальный обрыв.

## 4. «Ноль записей» — чек-лист, прежде чем писать «логов нет»

По порядку, каждый пункт — один запрос:

1. **Регистр** `level` и регэкспов (§3).
2. **Поле текста у этого контейнера**: неразобранные логи — в `message`.
3. **Namespace существует?** Агрегация `terms` по namespace за то же окно.
4. **Сервис пишет на этом стенде?** Сервис может жить не на всех стендах: агрегация по
   namespace с фильтром по контейнеру.
5. **Все наборы индексов** включены в поиск.
6. **Ретеншн**: `_count` по namespace за нужный день. 0 = логи вычищены — так и пиши в отчёт.
7. **Окно**: для упавшего деплоя — `started − 5 мин … finished + 10 мин`, а не `now-15m`.
8. **Фильтр по уровню**: падение бывает без единой ERROR-записи — сними фильтр `level`.

Только после этого «логов нет» становится фактом, а не предположением.

## 5. Ссылка в Discover для человека

Каждый ответ с логами заканчивай ссылкой: человек открывает её и видит те же записи глазами.
Проверка качества ссылки — число hits в Discover совпадает с числом из API.

```
{OPENSEARCH_URL}/app/discover#/?_g=(filters:!(),time:(from:now-7d,to:now))
&_a=(columns:!(kubernetes.container.name,message,level,traceId),
     filters:!(<phrase-фильтры по namespace и container>),
     index:'{DATA_VIEW_ID}',
     query:(language:kuery,query:'level:"error"'),sort:!(!('@timestamp',desc)))
```

- `index` — id data view того набора индексов, **где реально нашлись записи**. С чужим id
  фильтры не встанут.
- В KQL регистр `level` не важен (это match по анализируемому полю) — в отличие от `term`.
- **OpenSearch Dashboards с мультитенантностью** требует tenant перед якорем:
  `{OPENSEARCH_URL}/app/discover?security_tenant=global#/?_g=(...)&_a=(...)`. Без него ссылка
  открывает не данные, а страницу управления index pattern.
- Короткие ссылки `/goto/<id>` через REST не резолвятся: открой их в браузерной сессии, возьми
  итоговый URL после редиректа и распарси фильтры из него. В короткой ссылке может не быть
  namespace — добавь его из соседней полной ссылки.
- KQL-примеры для человека: `kubernetes.container.name:"payment-service" AND message:"code=861"`,
  `message:"10277160"` (поиск по id сущности).

## 6. Проверочный набор: 10 кейсов на новом кластере

Прогони один раз при подключении нового кластера — так ловушки §3 всплывают сразу, а не
посреди разбора инцидента. Запиши результаты в `project-config.md`.

| # | Кейс | Что подтверждает |
|---|---|---|
| 1 | Логи стенда за 15 мин (фильтр по namespace) | namespace-поле и шаблон стенда верны |
| 2 | Группа сервисов через `bool.should`, 1 ч | `minimum_should_match` работает |
| 3 | `regexp` по событию в lowercase и в UPPERCASE | анализатор: UPPERCASE даст 0 |
| 4 | Поиск по имени метода, 24 ч | текстовое поле выбрано верно |
| 5 | Поиск по id сущности из живого лога | точечный поиск работает |
| 6 | Сквозной поиск по traceId | связка сервисов одного запроса, поля traceId |
| 7 | Топ сервисов по ошибкам за 24 ч | регистр `level`, агрегация по keyword |
| 8 | Логи джобы миграций (сырой контейнер), 7 д | поле `message` у неразобранных логов |
| 9 | Класс ошибки, который заведомо устранён | механика поиска верна и на нуле |
| 10 | Свежая ошибка с полным stacktrace | поле stacktrace и `Caused by` |

Для запасного браузерного канала добавь 11-й кейс: истёкшая сессия → автоматический перелогин
→ 200 без участия человека.
