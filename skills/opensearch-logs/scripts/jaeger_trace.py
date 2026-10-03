#!/usr/bin/env python3
"""Трейсы Jaeger одной командой — разбор трейса, поиск ошибок, ссылка на UI.

Логи и трейсы — соседние источники одного разбора: в логе берёшь traceId, в трейсе видишь
путь запроса по сервисам, коды ответов и (если сервисы их пишут) тела запросов в span logs.
Нужен только HTTP-доступ к Jaeger Query API — MCP не обязателен.

    python jaeger_trace.py --trace 09007e25ab7e4060a57dbd947b422f6a
    python jaeger_trace.py --stand 5 --service order-service --errors --since 2h
    python jaeger_trace.py --stand 3 --service payment-service --operation POST --limit 5
    python jaeger_trace.py --services --stand 5          # какие сервисы вообще шлют трейсы

Переменные окружения:
    JAEGER_URL               базовый URL Jaeger UI/Query, напр. https://jaeger.example.com (обязательно)
    JAEGER_SERVICE_TEMPLATE  как имя сервиса выглядит в Jaeger, по умолчанию "{service}".
                             Если сервисы регистрируются с префиксом стенда — например
                             "testing-{stand}.{service}" — скрипт подставит его по --stand.
    JAEGER_TOKEN             Bearer-токен, если Jaeger закрыт авторизацией

Каждый прогон печатает ссылку на Jaeger UI — открыть и посмотреть глазами.
"""
import argparse, collections, json, os, sys, urllib.error, urllib.parse, urllib.request

BASE = os.environ.get("JAEGER_URL", "").rstrip("/")
API = BASE + "/api"
TEMPLATE = os.environ.get("JAEGER_SERVICE_TEMPLATE", "{service}")

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def get(path, **params):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {}
    if os.environ.get("JAEGER_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["JAEGER_TOKEN"]
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"data": []}                 # несуществующий трейс/сервис — не повод падать
        print(f"Jaeger ответил HTTP {e.code} на {url}")
        raise SystemExit(1)
    except urllib.error.URLError as e:
        print(f"Jaeger недоступен ({e.reason}) — есть ли сетевой доступ/VPN? "
              f"проверка: curl {API}/services")
        raise SystemExit(1)


def stand_prefix(stand):
    """Префикс имени сервиса для стенда: всё, что шаблон ставит перед {service}."""
    if "{stand}" not in TEMPLATE or not stand:
        return ""
    return TEMPLATE.split("{service}")[0].format(stand=stand)


def svc_name(stand, service):
    """Полное имя сервиса в Jaeger. Переданное полное имя (с точкой) берётся как есть."""
    if not service:
        return None
    if "." in service or "{stand}" not in TEMPLATE or not stand:
        return service
    return TEMPLATE.format(stand=stand, service=service)


def span_problems(trace):
    """WARN/ERROR из логов спанов и ошибочные HTTP-коды — то, что нельзя пропускать в отчёте."""
    proc = {k: v["serviceName"] for k, v in trace["processes"].items()}
    out = []
    for sp in trace["spans"]:
        service = proc.get(sp["processID"], "?")
        tags = {t["key"]: t["value"] for t in sp.get("tags", [])}
        code = tags.get("http.status_code") or tags.get("http.response.status_code")
        try:
            code_num = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_num = None
        if tags.get("error") in (True, "true") or (code_num is not None and code_num >= 400):
            out.append((service, sp["operationName"], f"HTTP {code}" if code else "error=true", ""))
        for lg in sp.get("logs", []):
            f = {x["key"]: x["value"] for x in lg["fields"]}
            if str(f.get("level", "")).upper() in ("WARN", "ERROR") or "error.object" in f:
                msg = str(f.get("error.object") or f.get("message") or "")[:160]
                out.append((service, sp["operationName"], str(f.get("level", "ERROR")).upper(),
                            f"{str(f.get('logger', '')).split('.')[-1]}: {msg}"))
    return out


def short(name):
    """Имя сервиса без префикса стенда — для компактной таблицы."""
    return name.split(".")[-1] if "{stand}" in TEMPLATE else name


def show_trace(tid):
    data = get(f"traces/{tid}").get("data", [])
    if not data:
        print(f"трейс {tid} не найден (истёк ретеншн Jaeger или опечатка в id)"); return 1
    t = data[0]
    proc = {k: v["serviceName"] for k, v in t["processes"].items()}
    spans = sorted(t["spans"], key=lambda s: s["startTime"])
    t0 = spans[0]["startTime"]
    per_svc = collections.Counter(proc.get(s["processID"], "?") for s in spans)
    total = (max(s["startTime"] + s["duration"] for s in spans) - t0) / 1000

    print(f"трейс {tid}: {len(spans)} спанов, {total:.0f} мс, сервисов {len(per_svc)}")
    print("  по сервисам:", ", ".join(f"{short(k)}={v}" for k, v in per_svc.most_common()))
    print("\n  цепочка (первые 12 спанов):")
    for s in spans[:12]:
        tags = {x["key"]: x["value"] for x in s.get("tags", [])}
        code = tags.get("http.status_code") or tags.get("http.response.status_code") or ""
        url = str(tags.get("http.url") or tags.get("url.full") or "")[:70]
        print(f"    +{(s['startTime'] - t0) / 1000:7.0f} мс  {s['duration'] / 1000:7.1f} мс  "
              f"{short(proc.get(s['processID'], '?')):24} {s['operationName'][:28]:30}"
              f"{(' ' + str(code)) if code else ''} {url}")

    problems = span_problems(t)
    print(f"\n  проблемные места: {len(problems)}" if problems else "\n  WARN/ERROR в спанах нет")
    for service, op, lvl, msg in problems[:10]:
        print(f"    {lvl:5} {short(service):24} {op[:24]:26} {msg}")
    print(f"\nСМОТРЕТЬ В JAEGER:\n  {BASE}/trace/{tid}")
    return 0


def find(stand, service, operation, errors, since, limit):
    name = svc_name(stand, service)
    if not name:
        print("нужен --service (и --stand, если в JAEGER_SERVICE_TEMPLATE есть {stand})"); return 1
    params = dict(service=name, limit=limit, lookback=since)
    if operation:
        params["operation"] = operation
    if errors:
        params["tags"] = json.dumps({"error": "true"})
    data = get("traces", **params).get("data", [])
    print(f"{name}: найдено трейсов {len(data)} (за {since}" + (", только с ошибками)" if errors else ")"))
    for t in data:
        proc = {k: v["serviceName"] for k, v in t["processes"].items()}
        spans = t["spans"]
        dur = max(s["startTime"] + s["duration"] for s in spans) - min(s["startTime"] for s in spans)
        problems = span_problems(t)
        print(f"  {t['traceID']}  {len(spans):3} спанов  {dur / 1000:7.0f} мс  "
              f"сервисов {len({proc[s['processID']] for s in spans})}"
              + (f"  ⚠ {len(problems)} проблем" if problems else ""))
    q = urllib.parse.urlencode({"service": name, "lookback": since, "limit": limit,
                                **({"tags": '{"error":"true"}'} if errors else {})})
    print(f"\nСМОТРЕТЬ В JAEGER:\n  {BASE}/search?{q}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Разбор трейсов Jaeger из командной строки")
    ap.add_argument("--trace", help="traceId (32 hex) — разобрать один трейс")
    ap.add_argument("--stand", help="стенд — подставляется в JAEGER_SERVICE_TEMPLATE")
    ap.add_argument("--service", help="имя сервиса, напр. order-service")
    ap.add_argument("--operation", help="фильтр по операции, напр. POST")
    ap.add_argument("--errors", action="store_true", help="только трейсы с error=true")
    ap.add_argument("--since", default="1h", help="окно поиска: 15m, 1h, 24h (по умолчанию 1h)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--services", action="store_true", help="список сервисов, шлющих трейсы")
    a = ap.parse_args()

    if not BASE:
        print("JAEGER_URL не задан. Пример: export JAEGER_URL=https://jaeger.example.com")
        return 2
    if a.trace:
        return show_trace(a.trace)
    if a.services:
        names = get("services").get("data", []) or []
        pref = stand_prefix(a.stand)
        if pref:
            names = [n for n in names if n.startswith(pref)]
        print(f"сервисов: {len(names)}")
        for n in sorted(names)[:40]:
            print("  ", n)
        return 0
    return find(a.stand, a.service, a.operation, a.errors, a.since, a.limit)


if __name__ == "__main__":
    sys.exit(main())
