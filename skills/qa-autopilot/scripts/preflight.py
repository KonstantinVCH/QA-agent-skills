#!/usr/bin/env python3
"""preflight.py — проверка окружения перед автономным прогоном (скилл qa-autopilot). Только stdlib.

  preflight.py --run-dir <RUN_DIR> [--skip-net]

Проверяет: стенд из QA_AUTOPILOT_STANDS; токены систем и их валидность по HTTP (значения НЕ
печатает); Jaeger; OpenSearch/Kibana; базу знаний Obsidian (если настроена); прямое подключение
к БД стенда; клон API-коллекций; подключены ли хуки автопилота.

Система проверяется, только если задан её URL. Jira обязательна: без трекера задачи нет.
Bamboo нужен только для деплоя: с --assume-deployed его отсутствие — WARN.
Необязательные проверки печатаются как WARN и прогон не блокируют.
Итог — в stdout и в progress.md. exit 0 — можно ехать; exit 1 — BLOCKED с перечнем.

Переменные (подробно — references/project-config.md):
  JIRA_URL + JIRA_TOKEN, CONFLUENCE_URL + CONFLUENCE_TOKEN, BITBUCKET_URL + BITBUCKET_TOKEN,
  BAMBOO_URL + BAMBOO_TOKEN, JAEGER_URL, OPENSEARCH_URL, OBSIDIAN_HOST/OBSIDIAN_PORT,
  QA_DB_DSN_<STAND>, QA_API_COLLECTIONS_REPO, QA_AUTOPILOT_STANDS, QA_AUTOPILOT_INSECURE_TLS.
Токены проверяются заголовком `Authorization: Bearer` (Personal Access Token в Server/Data Center).
Для облачных версий с другой схемой авторизации запускай с --skip-net.
"""
import argparse
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def env_url(name):
    return (os.environ.get(name) or "").rstrip("/")


JIRA = env_url("JIRA_URL")
CONF = env_url("CONFLUENCE_URL")
BB = env_url("BITBUCKET_URL")
BAMBOO = env_url("BAMBOO_URL")
JAEGER = env_url("JAEGER_URL")
OPENSEARCH = env_url("OPENSEARCH_URL")
COLLECTIONS = os.environ.get("QA_API_COLLECTIONS_REPO")

CTX = ssl.create_default_context()
if os.environ.get("QA_AUTOPILOT_INSECURE_TLS"):
    # внутренние системы часто живут на самоподписанных сертификатах; включается явно
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE


def http(url, token=None, timeout=8):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"ERR {type(e).__name__}"


def check_db(stand):
    """(ok, detail) нового подключения к БД стенда. DSN — из QA_DB_DSN_<STAND>."""
    var = "QA_DB_DSN_" + re.sub(r"[^A-Za-z0-9]", "_", str(stand)).upper()
    dsn = os.environ.get(var)
    if not dsn:
        return False, f"нет DSN: задай {var} — без него фикстуры и откаты пойдут только через MCP или psql"
    try:
        import psycopg2  # необязательная зависимость: только для этой проверки
    except ImportError:
        return False, "нет psycopg2 (pip install psycopg2-binary) — проверка подключения пропущена"
    try:
        # DSN идёт целиком, с параметрами: подключение без options — не то подключение,
        # каким потом пишутся фикстуры.
        conn = psycopg2.connect(dsn, connect_timeout=8)
        conn.close()
        return True, "новое подключение открывается"
    except Exception as e:
        first = str(e).strip().splitlines()[0][:90]
        return False, (f"{first} — запись фикстур и откаты недоступны; "
                       "проверь VPN и пул соединений (pgbouncer) до EXECUTE")


def hooks_connected():
    """Хуки ищутся в пользовательских и проектных settings."""
    candidates = [Path.home() / ".claude" / "settings.json",
                  Path.cwd() / ".claude" / "settings.json",
                  Path.cwd() / ".claude" / "settings.local.json"]
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        candidates += [Path(proj) / ".claude" / "settings.json", Path(proj) / ".claude" / "settings.local.json"]
    for p in candidates:
        try:
            if p.exists() and "guard_external_writes.py" in p.read_text(encoding="utf-8", errors="ignore"):
                return True, str(p)
        except OSError:
            continue
    return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--skip-net", action="store_true", help="только наличие переменных, без HTTP")
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    state = run_dir / "state.json"
    if not state.exists():
        sys.exit(f"state.json не найден в {run_dir}. Сначала создай прогон: "
                 f"autopilot.py init --task <KEY> --stand <STAND>, и передай напечатанный им каталог.")
    st = json.loads(state.read_text(encoding="utf-8"))

    rows, fails = [], []

    def add(name, ok, detail, blocking=True):
        """blocking=False — проверка печатается как WARN и прогон не останавливает."""
        rows.append((name, ok, detail, blocking))
        if not ok and blocking:
            fails.append(name)

    stand = st.get("stand")
    stands = [s.strip() for s in (os.environ.get("QA_AUTOPILOT_STANDS") or "").split(",") if s.strip()]
    add("стенд", bool(stand) and stand in stands,
        f"{stand} ∈ QA_AUTOPILOT_STANDS" if stand in stands
        else f"{stand} не входит в QA_AUTOPILOT_STANDS ({', '.join(stands) or 'пусто'})")

    assume_deployed = bool(st.get("flags", {}).get("assume_deployed"))
    # (имя, URL, путь проверки, токен, блокирующая ли)
    systems = [
        ("Jira", JIRA, "/rest/api/2/myself", os.environ.get("JIRA_TOKEN"), True),
        ("Confluence", CONF, "/rest/api/user/current", os.environ.get("CONFLUENCE_TOKEN"), True),
        ("Bitbucket", BB, "/rest/api/1.0/users?limit=1", os.environ.get("BITBUCKET_TOKEN"), True),
        # Bamboo нужен только для деплоя. С --assume-deployed деплоя не будет.
        ("Bamboo", BAMBOO, "/rest/api/latest/info", os.environ.get("BAMBOO_TOKEN"), not assume_deployed),
    ]
    for name, base, path, tok, blocking in systems:
        if not base:
            if name == "Jira":
                add(name, False, "JIRA_URL не задан — без трекера задачи нет")
            else:
                add(name, False, f"{name.upper()}_URL не задан — шаги с {name} роли пропустят или сделают вручную",
                    blocking=False)
            continue
        if not tok:
            add(name, False, f"{name.upper()}_TOKEN не задан"
                + ("" if blocking else " — не помеха: с --assume-deployed деплоя не будет"), blocking=blocking)
            continue
        if a.skip_net:
            add(name, True, "токен задан (сеть не проверялась)", blocking=blocking)
            continue
        code = http(base + path, tok)
        add(name, code == 200, f"HTTP {code} ← {base}{path}", blocking=blocking)

    if not a.skip_net:
        if JAEGER:
            code = http(f"{JAEGER}/api/services")
            add("Jaeger", code == 200, f"HTTP {code} ← {JAEGER}/api/services")
        else:
            add("Jaeger", False, "JAEGER_URL не задан — трейсов нет, доказательства слабее", blocking=False)

        # Логи стендов: именно ими разбирается упавший деплой — единственный шаг,
        # способный заблокировать прогон.
        if OPENSEARCH:
            code = http(f"{OPENSEARCH}/api/status")
            add("OpenSearch/Kibana (логи)", code == 200,
                f"HTTP {code} ← {OPENSEARCH}/api/status" if code == 200 else
                f"HTTP {code} — разбор падения деплоя придётся вести другим каналом", blocking=False)
        else:
            add("OpenSearch/Kibana (логи)", False, "OPENSEARCH_URL не задан", blocking=False)

    # База знаний (Obsidian Local REST API) — необязательна: нет её — роли работают без неё.
    host = os.environ.get("OBSIDIAN_HOST")
    if host:
        port = int(os.environ.get("OBSIDIAN_PORT") or 27124)
        try:
            with socket.create_connection((host, port), timeout=3):
                add("база знаний (Obsidian REST)", True, f"{host}:{port} открыт", blocking=False)
        except Exception as e:
            add("база знаний (Obsidian REST)", False, f"{host}:{port} закрыт ({type(e).__name__})", blocking=False)

    # Прямое подключение к БД стенда: через него идут запись фикстур и откаты. MCP-сервер БД
    # обычно только читает и держит соединение, открытое при старте сессии, поэтому его ответ
    # ничего не говорит о новом подключении: на пилоте MCP отвечал, а пул соединений рвал каждое
    # новое подключение сразу после аутентификации — это вскрылось только на ENV, через три часа.
    if not a.skip_net and stand:
        add("БД стенда (psycopg2)", *check_db(stand), blocking=False)

    # Коллекции — лишь подсказка по структуре вызова. В существующем клоне может быть своя ветка
    # и несохранённые правки: только fast-forward, иначе — WARN.
    if COLLECTIONS:
        repo = Path(os.path.expanduser(COLLECTIONS))
        if repo.exists():
            try:
                r = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"],
                                   capture_output=True, text=True, timeout=60)
                out = (r.stdout or r.stderr).strip().splitlines()
                tail = out[-1][:80] if out else "ok"
                add("API-коллекции", r.returncode == 0,
                    tail if r.returncode == 0 else f"{tail} — обнови вручную перед EXECUTE", blocking=False)
            except Exception as e:
                add("API-коллекции", False, f"git pull не удался: {type(e).__name__}", blocking=False)
        else:
            add("API-коллекции", False, f"нет клона {repo} — не помеха: тела берутся из трейсов и шагов ТК",
                blocking=False)

    hooked, where = hooks_connected()
    # Без хуков нет детерминированных гардов, поэтому деплой запрещён. С --assume-deployed деплоя
    # и не будет — тогда это WARN, а не стоп.
    add("хуки автопилота", hooked,
        f"подключены ({where})" if hooked else
        ("НЕ подключены — гардов нет; деплой в автопилоте запрещён, нужен --assume-deployed"
         if not assume_deployed else
         "НЕ подключены — гардов нет; допустимо только потому, что задан --assume-deployed"),
        blocking=not assume_deployed)

    width = max(len(r[0]) for r in rows)
    lines = [f"{'OK  ' if ok else ('FAIL' if blocking else 'WARN')} {name.ljust(width)}  {detail}"
             for name, ok, detail, blocking in rows]
    print("\n".join(lines))
    verdict = "PREFLIGHT ok" if not fails else "PREFLIGHT FAIL: " + ", ".join(dict.fromkeys(fails))
    print(verdict)
    try:
        with open(run_dir / "progress.md", "a", encoding="utf-8") as f:
            f.write(f"PREFLIGHT: {verdict}\n")
    except Exception:
        pass
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
