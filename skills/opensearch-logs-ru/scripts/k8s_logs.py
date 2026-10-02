#!/usr/bin/env python3
"""Логи приложений Kubernetes из Kibana / OpenSearch Dashboards одной командой — сводка + ссылка для человека.

Зачем скрипт: каждый разбор начинается с одного и того же набора запросов (сколько записей,
динамика по часам/дням, разрез по сервисам и классам, примеры со stacktrace). Здесь он собран
один раз и с учётом типовых ловушек полей: регистр level, агрегации только по keyword-полям,
track_total_hits для точного счёта, stacktrace в отдельном поле, текст события то в одном
поле, то в другом.

Ничего не меняет, только читает.

Примеры:
    python k8s_logs.py --check                                         # какие каналы доступны
    python k8s_logs.py --stand 2 --service order-service --level error --since 7d
    python k8s_logs.py --stand 3 --level error --since 2h              # кто сыплет ошибками
    python k8s_logs.py --trace 295cdd21a95ef038927980ec2c77ac5d        # логи всех сервисов по traceId
    python k8s_logs.py --stand 2 --text "not found" --since 24h        # поиск по тексту
    python k8s_logs.py --stand 1 --service db-migrator --since 24h     # контейнер с «сырыми» логами

Настройка — переменные окружения (всё, кроме OPENSEARCH_URL, имеет значение по умолчанию):
    OPENSEARCH_URL          базовый URL Kibana / OpenSearch Dashboards / самого кластера
    LOGS_API                kibana   — POST /internal/search/es (Kibana 7.10+/8.x, по умолчанию)
                            console  — POST /api/console/proxy (Kibana и OpenSearch Dashboards)
                            direct   — POST {OPENSEARCH_URL}/{index}/_search (прямой REST кластера)
    LOGS_INDEX              индексы через запятую (по умолчанию filebeat-*)
    LOGS_DATA_VIEWS         id data view / index-pattern для ссылки Discover: "префикс=id,префикс=id"
    LOGS_DISCOVER_TENANT    tenant OpenSearch Dashboards для ссылки (например global), если нужен
    LOGS_NAMESPACE_TEMPLATE как номер стенда превращается в namespace, по умолчанию "{stand}"
                            (пример: "testing-{stand}"); нецифровое значение --stand берётся как есть
    LOGS_NS_FIELD           kubernetes.namespace.keyword
    LOGS_CONTAINER_FIELD    kubernetes.container.name.keyword
    LOGS_LOGGER_FIELD       logger.keyword
    LOGS_LEVEL_FIELD        level
    LOGS_STACK_FIELD        trace            (поле с полным stacktrace; в ECS — error.stack_trace)
    LOGS_TEXT_FIELDS        message,messagetext   (где лежит текст события — ищем во всех)
    LOGS_TRACE_FIELDS       traceId,trace_id,trace.id
    LOGS_RAW_CONTAINERS     контейнеры, чьи логи не парсятся (текст только в message, без level)
    OPENSEARCH_TOKEN        Bearer-токен, если доступ не анонимный
    OPENSEARCH_USER / OPENSEARCH_PASSWORD   Basic-авторизация, если так устроен доступ
    OPENSEARCH_INSECURE=1   не проверять TLS-сертификат (самоподписанный у кластера)
    SSO_HOST                хост SAML/ADFS-входа: --check проверит, открыт ли он по TCP
    JAEGER_URL              --check заодно проверит доступность Jaeger

Каждый прогон печатает ссылку на Discover — открыть и посмотреть глазами.
"""
import argparse, base64, collections, json, os, socket, ssl, sys, urllib.error, urllib.parse, urllib.request

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def env_list(name, default):
    return tuple(x.strip() for x in os.environ.get(name, default).split(",") if x.strip())


HOST = os.environ.get("OPENSEARCH_URL", "").rstrip("/")
API_MODE = os.environ.get("LOGS_API", "kibana").strip().lower()
INDEX = os.environ.get("LOGS_INDEX", "filebeat-*")
DATA_VIEWS = dict(x.split("=", 1) for x in env_list("LOGS_DATA_VIEWS", "") if "=" in x)
TENANT = os.environ.get("LOGS_DISCOVER_TENANT", "")
NS_TEMPLATE = os.environ.get("LOGS_NAMESPACE_TEMPLATE", "{stand}")
NS_FIELD = os.environ.get("LOGS_NS_FIELD", "kubernetes.namespace.keyword")
CONTAINER_FIELD = os.environ.get("LOGS_CONTAINER_FIELD", "kubernetes.container.name.keyword")
LOGGER_FIELD = os.environ.get("LOGS_LOGGER_FIELD", "logger.keyword")
LEVEL_FIELD = os.environ.get("LOGS_LEVEL_FIELD", "level")
STACK_FIELD = os.environ.get("LOGS_STACK_FIELD", "trace")
TEXT_FIELDS = env_list("LOGS_TEXT_FIELDS", "message,messagetext")
TRACE_FIELDS = env_list("LOGS_TRACE_FIELDS", "traceId,trace_id,trace.id")
RAW_CONTAINERS = env_list("LOGS_RAW_CONTAINERS", "")
SSO_HOST = os.environ.get("SSO_HOST", "")
JAEGER_URL = os.environ.get("JAEGER_URL", "").rstrip("/")


def ssl_context():
    if os.environ.get("OPENSEARCH_INSECURE") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def auth_headers():
    tok = os.environ.get("OPENSEARCH_TOKEN")
    if tok:
        return {"Authorization": "Bearer " + tok}
    user, pwd = os.environ.get("OPENSEARCH_USER"), os.environ.get("OPENSEARCH_PASSWORD")
    if user and pwd:
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()}
    return {}


def plain(field):
    """Имя поля без .keyword — для KQL и фильтров Discover."""
    return field[:-8] if field.endswith(".keyword") else field


def get_path(src, dotted):
    """Достать значение по пути с точками: и вложенный объект, и плоский ключ."""
    if dotted in src:
        return src[dotted]
    cur = src
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def es(body, index=None):
    """Один поисковый запрос. Тело ES DSL одинаковое для всех транспортов — меняется обёртка."""
    idx = index or INDEX
    headers = {"Content-Type": "application/json", **auth_headers()}
    if API_MODE == "kibana":
        url = HOST + "/internal/search/es"
        data = {"params": {"index": idx, "body": body}}
        headers.update({"kbn-xsrf": "true", "x-elastic-internal-origin": "Kibana",
                        "elastic-api-version": "1"})
    elif API_MODE == "console":
        url = (HOST + "/api/console/proxy?path=" + urllib.parse.quote(f"{idx}/_search", safe="")
               + "&method=POST")
        data = body
        headers.update({"kbn-xsrf": "true", "osd-xsrf": "true"})
    else:
        url = f"{HOST}/{urllib.parse.quote(idx, safe=',*')}/_search"
        data = body
    req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method="POST")
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=60, context=ssl_context()))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"HTTP {e.code} на {url}: {detail}")
        if e.code in (401, 403):
            print("  доступ закрыт: нужен токен (OPENSEARCH_TOKEN / OPENSEARCH_USER+PASSWORD) "
                  "или браузерная SSO-сессия — см. references/sso-browser-fallback.md")
        if e.code == 404 and API_MODE == "kibana":
            print("  /internal/search/es есть только в Kibana 7.10+ и отвечает только на POST; "
                  "попробуй LOGS_API=console")
        raise SystemExit(1)
    except urllib.error.URLError as e:
        print(f"{HOST} недоступен ({e.reason}) — включены ли VPN/доступ к сети логов?")
        raise SystemExit(1)
    if isinstance(resp, dict) and resp.get("error"):
        # ES-ошибку проверять до чтения hits, иначе вместо причины — невнятный KeyError
        print("ES вернул ошибку:", json.dumps(resp["error"], ensure_ascii=False)[:400])
        raise SystemExit(1)
    return resp.get("rawResponse", resp)


def any_of(fields, value):
    """Одно значение в нескольких возможных полях: message/messagetext, traceId/trace_id."""
    return {"bool": {"should": [{"match_phrase": {f: value}} for f in fields], "minimum_should_match": 1}}


def level_filter(level):
    """level бывает анализируемым (term работает только в lowercase) и keyword (регистр как в логе).

    Перебираем оба регистра: так фильтр не отдаёт молчаливый ноль ни на одной из схем.
    """
    vals = sorted({level, level.lower(), level.upper()})
    return {"bool": {"should": [{"term": {LEVEL_FIELD: v}} for v in vals], "minimum_should_match": 1}}


def data_view_for(index_names):
    """id data view по именам индексов, в которых нашлись записи (самый длинный префикс)."""
    for name in index_names:
        for prefix in sorted(DATA_VIEWS, key=len, reverse=True):
            if name.startswith(prefix):
                return prefix, DATA_VIEWS[prefix]
    if DATA_VIEWS:
        prefix = next(iter(DATA_VIEWS))
        return prefix, DATA_VIEWS[prefix]
    return None, None


def check_channels():
    """Быстрый preflight: какой канал доступен прямо сейчас. Секунда вместо минут в браузере."""
    lax = ssl.create_default_context(); lax.check_hostname = False; lax.verify_mode = ssl.CERT_NONE

    def http(url, headers=None):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=8, context=lax) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception as e:                                     # noqa: BLE001
            return type(e).__name__

    def tcp(host, port=443):
        s = socket.socket(); s.settimeout(5)
        try:
            s.connect((host, port)); return "открыт"
        except Exception as e:                                     # noqa: BLE001
            return type(e).__name__.replace("Error", "")
        finally:
            s.close()

    ok = False
    if not HOST:
        print("  OPENSEARCH_URL не задан — заполни references/project-config.md и выставь переменные")
    else:
        probe = HOST + ("/api/status" if API_MODE in ("kibana", "console") else "/")
        code = http(probe, auth_headers())
        ok = code == 200
        print(f"  логи   {HOST:50} {code}")
        if ok:
            print("    → API отвечает: работаем скриптом/curl, браузер не нужен")
        elif code in (401, 403):
            print("    → доступ только с авторизацией: токен или SSO-сессия браузера")
        else:
            print("    → нет ответа: включён ли VPN / есть ли сетевой доступ?")
    if SSO_HOST:
        state = tcp(SSO_HOST)
        print(f"  SSO    {SSO_HOST:50} {state}")
        if state != "открыт":
            print("    → вход через SSO невозможен из этой сети: браузер и профиль входа поднимать "
                  "бессмысленно, сначала нужен доступ к IdP (обычно отдельный VPN)")
    if JAEGER_URL:
        print(f"  Jaeger {JAEGER_URL:50} {http(JAEGER_URL + '/api/services')}")
    return 0 if ok else 1


def phrase_filter(field, value, data_view):
    """Фильтр в формате, который Discover показывает как обычную «плашку» фильтра."""
    idx = f"index:'{data_view}'," if data_view else ""
    return (f"('$state':(store:appState),meta:(alias:!n,disabled:!f,{idx}"
            f"key:{field},negate:!f,params:(query:'{value}'),type:phrase),"
            f"query:(match_phrase:({field}:'{value}')))")


def discover_link(ns=None, service=None, level=None, text=None, trace=None, since="24h", raw=False,
                  data_view=None):
    filters = []
    if ns:
        filters.append(phrase_filter(plain(NS_FIELD), ns, data_view))
    if service:
        filters.append(phrase_filter(plain(CONTAINER_FIELD), service, data_view))
    kql = []
    if level:
        kql.append(f'{plain(LEVEL_FIELD)}:"{level}"')      # в KQL регистр level не важен (match)
    if trace:
        kql.append("(" + " or ".join(f'{f}:"{trace}"' for f in TRACE_FIELDS) + ")")
    if text:
        fields = ("message",) if raw else TEXT_FIELDS
        kql.append("(" + " or ".join(f'{f}:"{text}"' for f in fields) + ")")
    cols = ([plain(CONTAINER_FIELD), "message"] if raw else
            [plain(CONTAINER_FIELD), *TEXT_FIELDS[:1], plain(LEVEL_FIELD), *TRACE_FIELDS[:1]])
    idx = f"index:'{data_view}'," if data_view else ""
    tenant = f"?security_tenant={TENANT}" if TENANT else ""
    url = (f"{HOST}/app/discover{tenant}#/?_g=(filters:!(),refreshInterval:(pause:!t,value:60000),"
           f"time:(from:now-{since},to:now))"
           f"&_a=(columns:!({','.join(cols)}),filters:!({','.join(filters)}),{idx}"
           f"interval:auto,query:(language:kuery,query:'{' and '.join(kql)}'),sort:!(!('@timestamp',desc)))")
    return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=~-._%")


def total_of(hits):
    t = hits["total"]
    return t["value"] if isinstance(t, dict) else t


def first_of(src, fields):
    return next((get_path(src, f) for f in fields if get_path(src, f)), "")


def to_namespace(stand):
    v = stand.strip()
    return NS_TEMPLATE.format(stand=v) if v.isdigit() else v


def main():
    global INDEX
    ap = argparse.ArgumentParser(description="Сводка логов k8s из Kibana/OpenSearch + ссылка на Discover")
    ap.add_argument("--stand", help="номер стенда (через LOGS_NAMESPACE_TEMPLATE) или полное имя namespace")
    ap.add_argument("--service", help="имя контейнера, напр. order-service")
    ap.add_argument("--level", help="error / warn")
    ap.add_argument("--text", help="подстрока в тексте сообщения")
    ap.add_argument("--trace", help="traceId — логи всех сервисов одного запроса")
    ap.add_argument("--since", default="24h", help="окно: 30m, 2h, 24h, 7d (по умолчанию 24h)")
    ap.add_argument("--limit", type=int, default=5, help="сколько групп сообщений показать")
    ap.add_argument("--index", default=None, help=f"индексы через запятую (по умолчанию {INDEX})")
    ap.add_argument("--check", action="store_true", help="preflight: какие каналы доступны — и ничего больше")
    a = ap.parse_args()
    if a.check:
        print("КАНАЛЫ:")
        sys.exit(check_channels())
    if not HOST:
        print("OPENSEARCH_URL не задан. Пример: export OPENSEARCH_URL=https://kibana.example.com")
        sys.exit(2)
    if a.index:
        INDEX = a.index

    ns = to_namespace(a.stand) if a.stand else None
    raw = bool(a.service and a.service in RAW_CONTAINERS)

    flt = [{"range": {"@timestamp": {"gte": f"now-{a.since}"}}}]
    if ns:
        flt.append({"match_phrase": {NS_FIELD: ns}})
    if a.service:
        flt.append({"match_phrase": {CONTAINER_FIELD: a.service}})
    if a.level:
        flt.append(level_filter(a.level))
    if a.text:
        flt.append(any_of(("message",) if raw else TEXT_FIELDS, a.text))
    if a.trace:
        flt.append(any_of(TRACE_FIELDS, a.trace))

    scope = " · ".join(x for x in [ns, a.service, a.level, f"за {a.since}"] if x)
    interval = "hour" if a.since[-1] in "hm" else "day"
    r = es({"size": 0, "track_total_hits": True, "query": {"bool": {"filter": flt}},
            "aggs": {"d": {"date_histogram": {"field": "@timestamp", "calendar_interval": interval}},
                     "c": {"terms": {"field": CONTAINER_FIELD, "size": 8}},
                     "lg": {"terms": {"field": LOGGER_FIELD, "size": 8}},
                     "idx": {"terms": {"field": "_index", "size": 5}}}})
    aggs = r.get("aggregations", {})
    idx_buckets = aggs.get("idx", {}).get("buckets", [])
    prefix, data_view = data_view_for([b["key"] for b in idx_buckets])
    families = {p for b in idx_buckets for p in DATA_VIEWS if b["key"].startswith(p)}
    total = total_of(r["hits"])
    print(f"ЗАПИСЕЙ: {total}   ({scope})")
    if total == 0:
        print(f"  0 — прежде чем писать «логов нет», проверь: namespace ({ns or 'не задан'} — есть ли "
              "такой), имя контейнера, регистр level, поле текста у контейнера (сырые логи — в message), "
              "все ли индексы в LOGS_INDEX и окно")
        if ns:
            chk = es({"size": 0, "track_total_hits": True,
                      "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": f"now-{a.since}"}}}]}},
                      "aggs": {"ns": {"terms": {"field": NS_FIELD, "size": 20}}}})
            live = [b["key"] for b in chk.get("aggregations", {}).get("ns", {}).get("buckets", [])]
            if ns not in live:
                print(f"  ⚠️ namespace '{ns}' в индексе не встречается. Живые: {', '.join(live[:8])}")
    buckets = [b for b in aggs.get("d", {}).get("buckets", []) if b["doc_count"]]
    if buckets:
        print("  динамика:", " ".join(f"{b.get('key_as_string', b['key'])[:16].replace('T', ' ')}={b['doc_count']}"
                                      for b in buckets[-12:]))
    if not a.service and aggs.get("c", {}).get("buckets"):
        print("  по сервисам:", ", ".join(f"{b['key']}={b['doc_count']}" for b in aggs["c"]["buckets"]))
    if aggs.get("lg", {}).get("buckets"):
        print("  по классам :", ", ".join(f"{str(b['key']).split('.')[-1]}={b['doc_count']}"
                                          for b in aggs["lg"]["buckets"][:6]))

    if total:
        src = ["@timestamp", plain(LEVEL_FIELD), plain(CONTAINER_FIELD), plain(LOGGER_FIELD), STACK_FIELD,
               *TEXT_FIELDS, *TRACE_FIELDS]
        r2 = es({"size": min(max(a.limit, 50), 500), "sort": [{"@timestamp": "desc"}],
                 "_source": src, "query": {"bool": {"filter": flt}}})
        groups, sample = collections.Counter(), {}
        for h in r2["hits"]["hits"]:
            s = h["_source"]
            key = " ".join(str(first_of(s, TEXT_FIELDS)).split()[:8])
            groups[key] += 1
            sample.setdefault(key, s)
        print(f"\nТИПЫ СООБЩЕНИЙ (из последних {len(r2['hits']['hits'])}):")
        for key, n in groups.most_common(a.limit):
            s = sample[key]
            cont = get_path(s, plain(CONTAINER_FIELD)) or ""
            print(f"  [{n}] {cont} · {key[:90]}")
            tr = str(get_path(s, STACK_FIELD) or "")
            if tr:
                caused = [l.strip() for l in tr.splitlines() if "Caused by" in l]
                print(f"       {caused[0][:120] if caused else tr.splitlines()[0][:120]}")
            tid = first_of(s, TRACE_FIELDS)
            print(f"       {s.get('@timestamp')}" + (f" · traceId={tid}" if tid else ""))

    print("\nСМОТРЕТЬ В DISCOVER:")
    if len(families) > 1:
        # ссылка строится под один data view — честно сказать, какая часть в неё не попала
        counts = ", ".join(f"{b['key']} — {b['doc_count']}" for b in idx_buckets)
        print(f"  записи в нескольких наборах индексов ({counts}); ссылка ниже покажет только {prefix}*. "
              "Для остальных повтори с --index <нужный набор>")
    if not data_view:
        print("  (LOGS_DATA_VIEWS не задан — Discover откроет data view по умолчанию)")
    print(" ", discover_link(ns=ns, service=a.service, level=a.level, text=a.text,
                             trace=a.trace, since=a.since, raw=raw, data_view=data_view))


if __name__ == "__main__":
    main()
