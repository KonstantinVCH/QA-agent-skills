#!/usr/bin/env python3
"""Подключить Jaeger MCP к Claude Code: проверки + правка ~/.claude.json.

Зачем скрипт, а не инструкция: настройка спотыкается об одно и то же — короткое `python`
в команде не резолвится на Windows, конфиг правят не тот (Claude Code читает MCP-серверы
пользовательского уровня из `~/.claude.json`, ключ `mcpServers` верхнего уровня), забывают про
версию Python и про перезапуск сессии. Скрипт подставляет путь текущего интерпретатора
(`sys.executable`), пишет в правильный файл и делает бэкап.

    export JAEGER_URL=https://jaeger.example.com
    python setup_jaeger_mcp.py                # проверки и план, ничего не меняя
    python setup_jaeger_mcp.py --apply        # записать в ~/.claude.json (с бэкапом)
    python setup_jaeger_mcp.py --apply --name jaeger-test   # под другим именем сервера

Ничего не удаляет: существующий блок с тем же именем показывает и, если он отличается,
меняет только его. После применения нужен перезапуск сессии Claude Code.
Для других клиентов (Claude Desktop, OpenCode) блок конфига тот же, файл — свой.
"""
import argparse, datetime, json, os, shutil, sys, urllib.request
from pathlib import Path

JAEGER_URL = os.environ.get("JAEGER_URL", "").rstrip("/")
CONFIG = Path(os.environ.get("CLAUDE_CONFIG", Path.home() / ".claude.json"))
SERVER_FILE = Path(__file__).resolve().parent / "jaeger_mcp.py"

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def check(name, ok, detail):
    print(f"  {'OK  ' if ok else 'FAIL'} {name:34} {detail}")
    return ok


def preflight():
    """Всё, что должно быть верно до правки конфига. Возвращает список проблем."""
    problems = []

    v = sys.version_info
    if not check("Python >= 3.10", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"):
        problems.append("обнови Python: пакету mcp нужен 3.10+")

    for mod, why in (("mcp", "сам MCP-сервер"), ("httpx", "HTTP-клиент внутри сервера")):
        try:
            __import__(mod)
            check(f"модуль {mod}", True, why)
        except ImportError:
            check(f"модуль {mod}", False, "не установлен")
            problems.append(f"установи зависимости: {sys.executable} -m pip install mcp httpx")

    if not check("файл сервера", SERVER_FILE.exists(), str(SERVER_FILE)):
        problems.append(f"нет {SERVER_FILE} — положи jaeger_mcp.py рядом с этим скриптом")

    if not JAEGER_URL:
        check("JAEGER_URL", False, "не задан")
        problems.append("задай JAEGER_URL (см. references/project-config.md) — без него сервер не знает, куда ходить")
    else:
        try:
            with urllib.request.urlopen(f"{JAEGER_URL}/api/services", timeout=15) as r:
                data = json.load(r).get("data", []) or []
            check("Jaeger доступен", True, f"HTTP {r.status}, сервисов {len(data)}")
        except Exception as e:                                     # noqa: BLE001
            check("Jaeger доступен", False, f"{type(e).__name__} — есть ли сетевой доступ/VPN?")
            problems.append("без сетевого доступа сервер поднимется, но данных не отдаст")

    check("конфиг Claude Code", CONFIG.exists(), str(CONFIG) if CONFIG.exists() else "будет создан")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="записать изменения (без него — только план)")
    ap.add_argument("--name", default="jaeger", help="имя MCP-сервера в конфиге")
    a = ap.parse_args()

    print("Проверки:")
    problems = preflight()
    entry = {"type": "stdio", "command": sys.executable, "args": [str(SERVER_FILE)],
             "env": {"JAEGER_URL": JAEGER_URL}}

    cfg = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception as e:                                     # noqa: BLE001
            print(f"\n{CONFIG} не читается как JSON ({type(e).__name__}) — почини файл и повтори")
            return 1
    servers = cfg.get("mcpServers", {})
    current = servers.get(a.name)

    print("\nЧто будет в конфиге:")
    print(f"  файл : {CONFIG}")
    print(f"  ключ : mcpServers.{a.name}")
    print(f"  блок : {json.dumps(entry, ensure_ascii=False)}")
    if current == entry:
        print("\nТакой блок уже есть — менять нечего. Если сервер всё равно не виден, "
              "перезапусти сессию Claude Code.")
        return 0
    if current:
        print("  ⚠ уже есть другой блок с этим именем, он будет заменён:")
        print(f"      {json.dumps(current, ensure_ascii=False)}")

    if problems:
        print("\nСначала устранить:")
        for p in problems:
            print("  •", p)
        if any(("mcp" in p) or ("Python" in p) or ("jaeger_mcp.py" in p) or ("JAEGER_URL" in p)
               for p in problems):
            print("\nБез этого сервер не стартует — конфиг не трогаю.")
            return 1

    if not a.apply:
        print("\nЭто план. Повтори с --apply, чтобы записать.")
        return 0

    if CONFIG.exists():
        backup = CONFIG.with_suffix(f".json.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(CONFIG, backup)
        print(f"\nбэкап конфига: {backup}")
    cfg.setdefault("mcpServers", {})[a.name] = entry
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"записано в {CONFIG}")
    print("\nДальше: перезапусти сессию Claude Code (конфиг читается при старте) и спроси "
          "«какие сервисы есть в Jaeger» — должен вернуться список сервисов.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
