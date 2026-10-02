#!/usr/bin/env python3
"""wm.py - работа с WireMock / WireMock Studio из командной строки.

Только stdlib. Кириллица уходит по проводу как ASCII (\\uXXXX) - иначе Git Bash
и другие консоли с не-UTF-8 кодировкой превращают русские имена стабов в U+FFFD необратимо.

Настройка (переменные окружения):
    WIREMOCK_URL   базовый адрес, напр. http://wiremock.example.com или http://localhost:8080
    WIREMOCK_MODE  studio | plain. Не задан - определяется сам:
                   studio - WireMock Studio с реестром моков GET /api/v1/mock-apis
                            (много mock API на одном хосте, у каждого свой порт);
                   plain  - обычный WireMock: admin-API на <хост>:<порт>/__admin.

Как назвать мок в командах (<мок>):
    studio: имя, id или порт мока                     python wm.py stubs payment-gateway-adapter
    plain:  порт на хосте WIREMOCK_URL или '-'        python wm.py stubs 8080   |   python wm.py stubs -
            ('-' = сам WIREMOCK_URL)

    python wm.py apis                    # только studio: реестр моков
    python wm.py stubs <мок>
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# консоль Git Bash/Windows - cp1251, без этого любой русский вывод падает UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = (os.environ.get("WIREMOCK_URL") or "").rstrip("/")
API = BASE + "/api/v1/mock-apis"
TIMEOUT = 30
_MODE = None


def _req(method, url, body=None):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        # ensure_ascii=True - ключевая строка файла, не убирать
        data = json.dumps(body, ensure_ascii=True).encode("ascii")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, (json.loads(raw) if raw.strip() else None)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, raw
    except OSError as e:
        sys.exit(f"{method} {url}: нет связи ({e}). Проверить WIREMOCK_URL и доступность сервера.")


def mode():
    """studio | plain. Явный WIREMOCK_MODE важнее автоопределения."""
    global _MODE
    if _MODE:
        return _MODE
    if not BASE:
        sys.exit("Не задан WIREMOCK_URL (см. references/project-config.md)")
    m = (os.environ.get("WIREMOCK_MODE") or "").strip().lower()
    if m in ("studio", "plain"):
        _MODE = m
        return m
    st, d = _req("GET", API)
    _MODE = "studio" if st == 200 and isinstance(d, dict) and "mockApis" in d else "plain"
    return _MODE


def studio_only(what):
    if mode() != "studio":
        sys.exit(f"'{what}' есть только в WireMock Studio (реестр mock API). "
                 f"В обычном WireMock один сервер = один мок; новый поднимается отдельным процессом/контейнером.")


def _apis():
    st, d = _req("GET", API)
    if st != 200:
        sys.exit(f"GET {API} -> {st}: {d}")
    out = []
    for m in d["mockApis"]:
        dn = m.get("domainNames") or [{}]
        port = (dn[0].get("domainName") or ":?").split(":")[-1]
        out.append({"id": m["id"], "name": m["name"], "port": port, "state": m["state"]})
    return out


def resolve(token):
    """<мок> -> (admin_base, name, port). Падает, если неоднозначно.

    admin_base - адрес, к которому дописывается '__admin/...'."""
    token = str(token).strip()
    if mode() == "plain":
        u = urllib.parse.urlsplit(BASE)
        if token in ("-", "", "default"):
            return BASE, u.hostname, str(u.port or "")
        if token.isdigit():
            base = urllib.parse.urlunsplit((u.scheme, f"{u.hostname}:{token}", "", "", ""))
            return base, u.hostname, token
        sys.exit(f"plain-режим: мок задаётся портом на хосте {u.hostname} или '-' "
                 f"(сам WIREMOCK_URL), получено '{token}'")
    apis = _apis()
    exact = [a for a in apis if token in (a["id"], a["port"]) or a["name"] == token]
    if len(exact) == 1:
        a = exact[0]
        return f'{API}/{a["id"]}', a["name"], a["port"]
    if len(exact) > 1:
        sys.exit("Неоднозначно: " + ", ".join(f'{a["name"]}({a["port"]})' for a in exact))
    part = [a for a in apis if token.lower() in a["name"].lower()]
    if len(part) == 1:
        a = part[0]
        return f'{API}/{a["id"]}', a["name"], a["port"]
    if not part:
        sys.exit(f"Мок '{token}' не найден. Список: python wm.py apis")
    sys.exit("Неоднозначно: " + ", ".join(f'{a["name"]}({a["port"]})' for a in part))


def admin(base, path):
    return f"{base}/{path.lstrip('/')}"


def mappings(base):
    """Все стабы мока. limit=500 задан в одном месте, чтобы не расходился по командам."""
    st, d = _req("GET", admin(base, "__admin/mappings?limit=500"))
    if st != 200:
        sys.exit(f"Чтение стабов -> {st}: {d}")
    d.setdefault("meta", {"total": len(d.get("mappings", []))})
    return d


def _print_json(obj):
    # stdout переведён в utf-8 выше; в консоли cp1251 кириллица покажется
    # кракозябрами, но при редиректе в файл байты корректные
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------- команды


def cmd_apis(a):
    studio_only("apis")
    rows = _apis()
    if a.grep:
        rows = [r for r in rows if a.grep.lower() in r["name"].lower()]
    rows.sort(key=lambda r: r["port"])
    for r in rows:
        n = "?"
        if a.count:
            st, d = _req("GET", admin(f'{API}/{r["id"]}', "__admin/mappings?limit=1"))
            n = d.get("meta", {}).get("total", "?") if st == 200 and isinstance(d, dict) else "err"
        print(f'{r["port"]}  {r["id"]:<7} {r["state"]:<8} стабов={n:<4} {r["name"]}')
    print(f"-- всего {len(rows)}")


def cmd_stubs(a):
    base, name, port = resolve(a.mock)
    d = mappings(base)
    print(f"# {name}  порт {port}  стабов {d['meta']['total']}")
    for m in d["mappings"]:
        rq = m.get("request", {})
        url = rq.get("url") or rq.get("urlPath") or rq.get("urlPattern") or rq.get("urlPathPattern") or "?"
        sc = m.get("scenarioName")
        flags = []
        if sc:
            flags.append(f"scenario={sc}:{m.get('requiredScenarioState')}->{m.get('newScenarioState')}")
        if m.get("postServeActions"):
            flags.append("webhook")
        if "response-template" in (m.get("response", {}).get("transformers") or []):
            flags.append("tmpl")
        if m.get("response", {}).get("fixedDelayMilliseconds"):
            flags.append(f"delay={m['response']['fixedDelayMilliseconds']}ms")
        print(
            f'{m.get("uuid", m.get("id"))}  p{m.get("priority", "-"):<3} {rq.get("method", "?"):<6} '
            f'{m.get("response", {}).get("status", "?"):<4} {url:<45} '
            f'{m.get("name", "")} {" ".join(flags)}'
        )


def _stub_urls(base):
    d = mappings(base)
    out = []
    for m in d["mappings"]:
        rq = m.get("request", {})
        literal = rq.get("url") or rq.get("urlPath")
        pattern = rq.get("urlPattern") or rq.get("urlPathPattern")
        out.append({
            "method": rq.get("method", "ANY"),
            "literal": (literal or "").split("?")[0],
            "pattern": pattern,
            "name": m.get("name", ""),
        })
    return out


def _path_to_re(p):
    """/orders/{id}/items -> ^/orders/[^/]+/items$"""
    parts = [re.escape(x) for x in re.split(r"\{[^}]*\}", p)]
    return re.compile("^" + "[^/]+".join(parts) + "$")


def cmd_cover(a):
    """Сверить перечень путей (из кода/конфига сервиса) с тем, что реально застаблено."""
    base, name, port = resolve(a.mock)
    stubs = _stub_urls(base)
    # Файл может быть выводом adapter_paths.py: строки вида
    # "POST /payments              # payment-gateway.create-payment".
    # Комментарий - пояснение, откуда путь; для сверки он лишний.
    lines = []
    for raw in open(a.file, encoding="utf-8"):
        body = raw.split("#", 1)[0].strip()
        if body:
            lines.append(body)
    if not lines:
        sys.exit(f"В {a.file} нет ни одного пути - сверять нечего (см. шаг 5 в SKILL.md)")
    pref = (a.prefix or "").rstrip("/")
    print(f"# {name}:{port}  путей на входе {len(lines)}, стабов {len(stubs)}"
          + (f", префикс {pref}" if pref else ""))
    missing = []
    for line in lines:
        bits = line.split(None, 1)
        method, path = (bits[0].upper(), bits[1]) if len(bits) == 2 and bits[0].isalpha() else ("ANY", line)
        rx = _path_to_re(pref + path.split("?")[0])
        hit = [
            s for s in stubs
            if (method in ("ANY", s["method"]) or s["method"] == "ANY")
            and (rx.match(s["literal"])
                 or (s["pattern"] and re.fullmatch(s["pattern"], pref + path.split("?")[0])))
        ]
        if hit:
            print(f'  OK   {method:<5} {path:<45} <- {hit[0]["name"] or hit[0]["literal"]}'
                  + (f" (+{len(hit) - 1})" if len(hit) > 1 else ""))
        else:
            missing.append(f"{method} {path}")
            print(f"  НЕТ  {method:<5} {path}")

    def _touched(st_):
        for l in lines:
            pth = pref + l.split(None, 1)[-1].split("?")[0]
            if st_["literal"] and _path_to_re(pth).match(st_["literal"]):
                return True
            if st_["pattern"] and re.fullmatch(st_["pattern"], pth):
                return True
        return False

    orphan = [s for s in stubs if not _touched(s)]
    if orphan:
        print(f"-- стабов без пути на входе: {len(orphan)}")
        for s in orphan:
            print(f'     {s["method"]:<5} {s["literal"] or s["pattern"]}   {s["name"]}')
    if missing:
        print(f"-- НЕ ЗАСТАБЛЕНО: {len(missing)} -> эти пути получат 404 Request was not matched")
        sys.exit(2)
    if not any(l.split(None, 1)[0].isalpha() and len(l.split(None, 1)) == 2 for l in lines):
        print("-- ВНИМАНИЕ: во входном файле нет методов, совпадение по методу НЕ проверено.")
        print("   Метод берётся из кода клиента (.post/.get), в конфиге путей его обычно нет:")
        print("   стаб POST при вызове GET даст 404, а cover этого не увидит.")
    print("-- все пути покрыты (по путям)")


def cmd_get(a):
    base, _, _ = resolve(a.mock)
    path = f"__admin/mappings/{a.uuid}" if a.uuid else "__admin/mappings?limit=500"
    st, d = _req("GET", admin(base, path))
    if st != 200:
        sys.exit(f"{st}: {d}")
    _print_json(d)


def cmd_backup(a):
    base, name, port = resolve(a.mock)
    d = mappings(base)
    safe = re.sub(r"[^\w.-]+", "_", name)
    out = a.out or f"{safe}-{port}-backup.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"снимок {d['meta']['total']} стабов -> {out}")


def cmd_restore(a):
    """Единственный надёжный способ откатиться: снести всё и налить из снимка.

    POST одного и того же стаба второй раз НЕ перезаписывает его, а создаёт дубль
    С ТЕМ ЖЕ uuid (проверено на WireMock Studio 2.33.1) - после этого update/rm по uuid
    бессмысленны. /__admin/mappings/import там отвечает 200 и не делает ничего.
    """
    base, name, port = resolve(a.mock)
    snap = _load(a.file)
    items = snap["mappings"] if isinstance(snap, dict) and "mappings" in snap else snap
    have = mappings(base)["meta"]["total"]
    if not a.yes:
        sys.exit(f'Снесёт все {have} стабов мока "{name}" ({port}) и нальёт {len(items)} '
                 f"из {a.file}. Если мок общий - предупредить команду. Повторить с --yes.")
    st, d = _req("DELETE", admin(base, "__admin/mappings"))
    if st not in (200, 204):
        sys.exit(f"DELETE всех стабов -> {st}: {d}")
    print(f"снесено {have}")
    for it in items:
        st, d = _req("POST", admin(base, "__admin/mappings"), it)
        if st not in (200, 201):
            sys.exit(f'FAIL {st} на "{it.get("name")}": {d}  <-- мок в ПОЛУПУСТОМ состоянии, добить вручную')
        print(f'  OK {it.get("name", "")}')
    print(f"восстановлено {len(items)}")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"Файл не найден: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"{path} - не JSON ({e}). Тело стаба и снимок это .json; "
                 f"перечень путей для cover это .txt - не перепутать команды.")


def cmd_add(a):
    base, _, _ = resolve(a.mock)
    body = _load(a.file)
    items = body.get("mappings") if isinstance(body, dict) and "mappings" in body else body
    items = items if isinstance(items, list) else [items]
    existing = _stub_urls(base)
    for it in items:
        rq = it.get("request", {})
        path = (rq.get("url") or rq.get("urlPath") or "").split("?")[0]
        twin = [e for e in existing if path and e["literal"] == path
                and e["method"] == rq.get("method", "ANY")]
        if twin:
            print(f'  ! затенит существующий "{twin[0]["name"]}" ({rq.get("method")} {path}) - '
                  f"при равном priority побеждает добавленный последним")
        st, d = _req("POST", admin(base, "__admin/mappings"), it)
        ok = st in (200, 201)
        uuid = (d.get("uuid") or d.get("id")) if ok and isinstance(d, dict) else d
        print(f'{"OK " if ok else "FAIL"} {st} {it.get("name", "")} uuid={uuid}')
        if not ok:
            sys.exit(1)


def cmd_update(a):
    base, _, _ = resolve(a.mock)
    st, d = _req("PUT", admin(base, f"__admin/mappings/{a.uuid}"), _load(a.file))
    print(st, d if st != 200 else "OK")
    sys.exit(0 if st == 200 else 1)


def cmd_rename(a):
    """Самая частая фикстура: включить/выключить негативный стаб переименованием."""
    base, _, _ = resolve(a.mock)
    st, stub = _req("GET", admin(base, f"__admin/mappings/{a.uuid}"))
    if st != 200:
        sys.exit(f"{st}: {stub}")
    was = stub.get("name")
    stub["name"] = a.name
    st, d = _req("PUT", admin(base, f"__admin/mappings/{a.uuid}"), stub)
    print(f'{st} "{was}" -> "{a.name}"' if st == 200 else f"{st}: {d}")
    sys.exit(0 if st == 200 else 1)


def cmd_rm(a):
    base, _, _ = resolve(a.mock)
    st, d = _req("DELETE", admin(base, f"__admin/mappings/{a.uuid}"))
    print(st, d or "удалён")


def cmd_log(a):
    base, name, port = resolve(a.mock)
    path = "__admin/requests/unmatched" if a.unmatched else f"__admin/requests?limit={a.n}"
    st, d = _req("GET", admin(base, path))
    if st != 200:
        sys.exit(f"{st}: {d}")
    reqs = d.get("requests", [])
    print(f'# {name}:{port}  записей {d.get("meta", {}).get("total", len(reqs))}')
    for r in reqs[: a.n]:
        rq = r.get("request", r)
        rs = r.get("response", {})
        matched = r.get("wasMatched")
        stub = (r.get("stubMapping") or {}).get("name", "")
        print(
            f'{rq.get("loggedDateString") or rq.get("loggedDate") or ""} {rq.get("method")} {rq.get("url")} '
            f'-> {rs.get("status", "-")} matched={matched} stub="{stub}"'
        )
    if a.full and reqs:
        _print_json(reqs[0])


def cmd_reset_log(a):
    base, _, _ = resolve(a.mock)
    st, d = _req("DELETE", admin(base, "__admin/requests"))
    print(st, d or "журнал очищен")


def cmd_reset_scenarios(a):
    base, _, _ = resolve(a.mock)
    st, d = _req("POST", admin(base, "__admin/scenarios/reset"), {})
    print(st, d or "сценарии сброшены")


def cmd_scenarios(a):
    base, _, _ = resolve(a.mock)
    st, d = _req("GET", admin(base, "__admin/scenarios"))
    if st != 200:
        sys.exit(f"{st}: {d}")
    _print_json(d)


def cmd_create(a):
    studio_only("create")
    # Studio МОЛЧА создаёт второй мок с тем же именем на другом порту (проверено):
    # после этого непонятно, в какой из них ходит сервис, и resolve() встаёт.
    same = [x for x in _apis() if x["name"] == a.name]
    if same and not a.force:
        sys.exit("Мок с таким именем уже есть: "
                 + ", ".join(f'{x["name"]} порт {x["port"]} id {x["id"]}' for x in same)
                 + ". Использовать его или повторить с --force (появятся два одноимённых).")
    st, d = _req("POST", API, {"mockApi": {"name": a.name}})
    if st not in (200, 201):
        sys.exit(f"{st}: {d}")
    m = d.get("mockApi", d)
    dn = (m.get("domainNames") or [{}])[0].get("domainName", ":?")
    print(f'создан: id={m["id"]} порт={dn.split(":")[-1]} имя="{m["name"]}"')
    print(f'UI: {BASE}/mock-apis/{m["id"]}/stubs')


def cmd_delete_api(a):
    studio_only("delete-api")
    base, name, port = resolve(a.mock)
    if not a.yes:
        sys.exit(f'Удалит мок "{name}" (порт {port}) целиком. Повторить с --yes.')
    st, d = _req("DELETE", base)
    print(st, d or f'мок "{name}" удалён')


# ---------------------------------------------------------------- разбор


def main():
    p = argparse.ArgumentParser(prog="wm.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)

    q = s.add_parser("apis", help="реестр моков Studio: порт, id, имя")
    q.add_argument("--grep", help="фильтр по имени")
    q.add_argument("--count", action="store_true", help="дописать число стабов (медленнее)")
    q.set_defaults(func=cmd_apis)

    q = s.add_parser("stubs", help="список стабов мока")
    q.add_argument("mock")
    q.set_defaults(func=cmd_stubs)

    q = s.add_parser("cover", help="сверить перечень путей с застабленным")
    q.add_argument("mock")
    q.add_argument("file", help="файл: по строке путь, можно 'POST /payments'")
    q.add_argument("--prefix", help="префикс из базового URL партнёра, напр. /gateway/api/v1")
    q.set_defaults(func=cmd_cover)

    q = s.add_parser("get", help="полное тело стаба (или всех)")
    q.add_argument("mock")
    q.add_argument("uuid", nargs="?")
    q.set_defaults(func=cmd_get)

    q = s.add_parser("backup", help="снимок всех стабов в файл")
    q.add_argument("mock")
    q.add_argument("-o", "--out")
    q.set_defaults(func=cmd_backup)

    q = s.add_parser("restore", help="откат из снимка: снести все стабы и налить заново")
    q.add_argument("mock")
    q.add_argument("file")
    q.add_argument("--yes", action="store_true")
    q.set_defaults(func=cmd_restore)

    q = s.add_parser("add", help="создать стаб(ы) из файла")
    q.add_argument("mock")
    q.add_argument("file")
    q.set_defaults(func=cmd_add)

    q = s.add_parser("update", help="заменить стаб целиком (PUT)")
    q.add_argument("mock")
    q.add_argument("uuid")
    q.add_argument("file")
    q.set_defaults(func=cmd_update)

    q = s.add_parser("rename", help="переименовать стаб (вкл/выкл негатива)")
    q.add_argument("mock")
    q.add_argument("uuid")
    q.add_argument("name")
    q.set_defaults(func=cmd_rename)

    q = s.add_parser("rm", help="удалить стаб")
    q.add_argument("mock")
    q.add_argument("uuid")
    q.set_defaults(func=cmd_rm)

    q = s.add_parser("log", help="журнал запросов")
    q.add_argument("mock")
    q.add_argument("-n", type=int, default=20)
    q.add_argument("--unmatched", action="store_true", help="только несовпавшие")
    q.add_argument("--full", action="store_true", help="дописать первую запись целиком")
    q.set_defaults(func=cmd_log)

    q = s.add_parser("reset-log", help="очистить журнал")
    q.add_argument("mock")
    q.set_defaults(func=cmd_reset_log)

    q = s.add_parser("scenarios", help="состояния сценариев")
    q.add_argument("mock")
    q.set_defaults(func=cmd_scenarios)

    q = s.add_parser("reset-scenarios", help="сбросить сценарии в Started")
    q.add_argument("mock")
    q.set_defaults(func=cmd_reset_scenarios)

    q = s.add_parser("create", help="только studio: создать новый mock API (порт выдаётся сам)")
    q.add_argument("name")
    q.add_argument("--force", action="store_true", help="создать, даже если имя занято")
    q.set_defaults(func=cmd_create)

    q = s.add_parser("delete-api", help="только studio: удалить мок целиком")
    q.add_argument("mock")
    q.add_argument("--yes", action="store_true")
    q.set_defaults(func=cmd_delete_api)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
