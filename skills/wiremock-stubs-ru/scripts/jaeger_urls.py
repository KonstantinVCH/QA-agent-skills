#!/usr/bin/env python3
"""jaeger_urls.py - куда сервис реально ходит, по трейсам Jaeger.

Отвечает на единственный вопрос, который нельзя решить чтением конфигов: применился
мок-адрес или сервис уехал на реальный контур партнёра. Ни файлы конфигурации по
отдельности, ни «деплой SUCCESS» этого не говорят, а actuator у сервисов часто закрыт.

    python jaeger_urls.py payment-gateway-adapter 3
    python jaeger_urls.py payment-gateway-adapter 3 --lookback 2d --limit 200
    python jaeger_urls.py payment-gateway-adapter --trace <traceId>

Печатает исходящие вызовы (client-спаны) с частотой и помечает, мок это или нет.

Переменные окружения:
    JAEGER_URL               адрес Jaeger Query, напр. http://jaeger.example.com
    JAEGER_SERVICE_TEMPLATE  как сервис называется в Jaeger; плейсхолдеры {service} и {stand}.
                             По умолчанию '{service}'. Пример для стендов: 'test-{stand}.{service}'
    MOCK_HOST_MARKER         подстрока хоста, по которой вызов считается ушедшим в мок
                             (по умолчанию хост из WIREMOCK_URL, иначе 'wiremock')

Почему не MCP-инструменты Jaeger: `operations` для HTTP-клиента обычно отдаёт только
имена спанов (`GET`, `consumer`, `producer`) - путей там нет, нужен разбор `http.url`
из тел трейсов. Для чтения ОДНОГО трейса MCP удобнее, здесь - нет.
"""

import argparse
import collections
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

JAEGER = (os.environ.get("JAEGER_URL") or "").rstrip("/")
TEMPLATE = os.environ.get("JAEGER_SERVICE_TEMPLATE") or "{service}"
_wm_host = urllib.parse.urlsplit(os.environ.get("WIREMOCK_URL") or "").hostname
MOCK_MARKER = (os.environ.get("MOCK_HOST_MARKER") or _wm_host or "wiremock").lower()
TIMEOUT = 60


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        sys.exit(f"{url} -> {e.code}: {e.read()[:200]}")
    except OSError as e:
        sys.exit(f"Jaeger недоступен: {e}")


def full_name(name, stand):
    if "{stand}" in TEMPLATE and not stand:
        sys.exit(f"JAEGER_SERVICE_TEMPLATE='{TEMPLATE}' требует номер стенда вторым аргументом")
    return TEMPLATE.format(service=name, stand=stand or "")


def resolve_service(name, stand):
    """Полное имя сервиса в Jaeger по шаблону JAEGER_SERVICE_TEMPLATE."""
    want = full_name(name, stand)
    services = fetch(f"{JAEGER}/api/services").get("data") or []
    if want in services:
        return want
    near = [s for s in services if name.lower() in s.lower()]
    if near:
        sys.exit(f"Трейсов от '{want}' нет. Похожие: "
                 + ", ".join(sorted(near))
                 + ". Стенд/имя не подменяю: вывод про другой стенд"
                   " ответил бы не на тот вопрос. Проверить JAEGER_SERVICE_TEMPLATE.")
    sys.exit(f"'{want}' не найден и похожих нет. Сервис ни разу не слал трейсы "
             f"с этого стенда - сделать один вызов и повторить.")


def _is_internal(host):
    """Свои сервисы в кластере: 'order-service', '...svc.cluster.local', короткие имена без точки."""
    h = host.split(":")[0]
    return h.endswith("-service") or ".svc" in h or "." not in h


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("service", help="имя сервиса, напр. payment-gateway-adapter")
    p.add_argument("stand", nargs="?", help="стенд, если он входит в имя сервиса в Jaeger")
    p.add_argument("--lookback", default="7d", help="период (1h, 24h, 7d), по умолчанию 7d")
    p.add_argument("--limit", type=int, default=100, help="сколько трейсов забрать")
    p.add_argument("--trace", help="разобрать один конкретный traceId")
    a = p.parse_args()

    if not JAEGER:
        sys.exit("Не задан JAEGER_URL (см. references/project-config.md)")

    if a.trace:
        traces = fetch(f"{JAEGER}/api/traces/{a.trace}").get("data") or []
        want_service = full_name(a.service, a.stand) if a.service else None
    else:
        svc = resolve_service(a.service, a.stand)
        want_service = svc
        q = urllib.parse.urlencode({"service": svc, "lookback": a.lookback, "limit": a.limit})
        traces = fetch(f"{JAEGER}/api/traces?{q}").get("data") or []

    calls = collections.Counter()
    skipped = 0
    for t in traces:
        # В трейсе лежат спаны ВСЕХ участников. Без фильтра по processID в вывод
        # попадают исходящие вызовы соседних сервисов, и вердикт про наш сервис врёт.
        procs = {k: (v or {}).get("serviceName") for k, v in (t.get("processes") or {}).items()}
        for sp in t.get("spans", []):
            tags = {x["key"]: x["value"] for x in sp.get("tags", [])}
            url = tags.get("http.url") or tags.get("url.full")
            if tags.get("span.kind") != "client" or not url:
                continue
            owner = procs.get(sp.get("processID"))
            if want_service and owner and owner != want_service:
                skipped += 1
                continue
            u = urllib.parse.urlsplit(str(url))
            meth = tags.get("http.method") or tags.get("http.request.method") or "?"
            calls[(meth, u.netloc, u.path)] += 1

    print(f"# трейсов {len(traces)}, исходящих вызовов самого сервиса {len(calls)}"
          + (f" (отброшено спанов соседних сервисов: {skipped})" if skipped else ""))
    if not calls:
        if skipped:
            # Спаны есть, но все чужие. Сказать «сервис никуда не ходил» тут было бы
            # ложью: скорее не тот стенд или не то имя сервиса.
            print(f"# у самого сервиса client-спанов нет, хотя в трейсах их {skipped} - "
                  f"все принадлежат другим сервисам. Проверить стенд и имя сервиса; "
                  f"при --trace убедиться, что трейс с этого стенда.")
        else:
            print("# client-спанов нет: сервис за этот период никуда не ходил, "
                  "либо трассировка выключена. Увеличить --lookback или сделать вызов.")
        return
    mocked = ext_total = 0
    for (meth, host, path), n in calls.most_common():
        is_mock = MOCK_MARKER in host.lower()
        internal = _is_internal(host) and not is_mock
        if not internal:
            ext_total += n
            mocked += n if is_mock else 0
        mark = "<- МОК" if is_mock else ("" if not internal else "(внутренний)")
        print(f"{n:>5}  {meth:<6} {host}{path}   {mark}")
    # Внутренние вызовы платформы под мок не идут и в знаменателе только шумят:
    # вопрос был про внешнего партнёра.
    if not ext_total:
        print("# внешних вызовов нет вообще - по этим трейсам про мок сказать нечего")
    elif mocked == ext_total:
        print(f"# все {ext_total} внешних вызовов ушли в мок -> переключение работает")
    elif mocked:
        print(f"# в мок ушло {mocked} из {ext_total} внешних -> часть путей мимо мока, "
              f"сверить конфиг и перечень стабов")
    else:
        print(f"# ни один из {ext_total} внешних вызовов не пошёл в мок -> "
              f"сервис НЕ переключён, идёт на реальный контур")


if __name__ == "__main__":
    main()
