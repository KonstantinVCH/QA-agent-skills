#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_ac.py — опубликовать согласованные Acceptance Criteria в задачу Jira.

Почему скриптом: некоторые MCP-обёртки Jira при обновлении описания портят wiki markup
(1. -> #, {*} -> {_}, теряются параметры картинок). Прямой REST сохраняет разметку как есть.
Скрипт одинаково правильно пишет и в описание (с заменой существующего блока AC, без дублей),
и комментарием.

Использование:
  python3 publish_ac.py PROJ-123 --file ac.txt              # в ОПИСАНИЕ (заменить/добавить блок)
  python3 publish_ac.py PROJ-123 --comment --file ac.txt    # КОММЕНТАРИЕМ
  cat ac.txt | python3 publish_ac.py PROJ-123               # AC из stdin
  python3 publish_ac.py PROJ-123 --file ac.txt --dry-run    # показать, но не отправлять

Вход (файл или stdin) — готовая таблица AC в Jira wiki markup, начиная с заголовка (h2./h3.).
Скрипт НЕ придумывает критерии — их составляет агент по SKILL.md.

ВАЖНО: публикация меняет общую задачу. Скрипт печатает превью и по умолчанию требует
подтверждения (--yes, чтобы пропустить). Без явного «да» человека не публиковать.

Переменные окружения:
  JIRA_URL    — например https://jira.example.com
  JIRA_TOKEN  — Personal Access Token Jira Server/Data Center (Bearer)
  AC_HEADERS  — (опционально) свои заголовки блока AC через «;», если в команде принят другой
"""
import argparse, json, os, sys, urllib.request, urllib.error

DEFAULT_HEADERS = ("h3. Acceptance Criteria", "h2. Acceptance Criteria", "h2. ✅ Критерии приёмки",
                   "h2. Критерии приёмки", "h3. Критерии приёмки")


def _req(base, method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": f"Bearer {token}"}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read().decode("utf-8", "replace")
        return r.status, (json.loads(t) if t.strip()[:1] in "{[" else t)


def main():
    ap = argparse.ArgumentParser(description="Опубликовать AC в задачу Jira")
    ap.add_argument("issue", help="ключ задачи, например PROJ-123")
    ap.add_argument("--file", help="файл с AC (иначе читается stdin)")
    ap.add_argument("--comment", action="store_true", help="постить комментарием, а не в описание")
    ap.add_argument("--dry-run", action="store_true", help="показать и выйти, ничего не отправляя")
    ap.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    a = ap.parse_args()

    base = (os.environ.get("JIRA_URL") or "").strip().rstrip("/")
    token = os.environ.get("JIRA_TOKEN")
    if not base or not token:
        sys.exit("ОШИБКА: задай JIRA_URL и JIRA_TOKEN (PAT из профиля Jira)")

    extra = [h.strip() for h in (os.environ.get("AC_HEADERS") or "").split(";") if h.strip()]
    headers = tuple(extra) + DEFAULT_HEADERS

    ac = open(a.file, encoding="utf-8").read().strip() if a.file else sys.stdin.read().strip()
    if len(ac) < 20:
        sys.exit("ОШИБКА: текст AC пуст или слишком короткий")

    key = a.issue.strip()

    if a.comment:
        target = "КОММЕНТАРИЙ"
        payload = (f"/rest/api/2/issue/{key}/comment", {"body": ac})
    else:
        # получить текущее описание, заменить существующий блок AC или добавить в конец
        _, cur = _req(base, "GET", f"/rest/api/2/issue/{key}?fields=description", token)
        current = (cur["fields"].get("description") or "") if isinstance(cur, dict) else ""
        cut = None
        for hdr in headers:
            i = current.find("\n" + hdr)
            if i == -1:
                i = current.find(hdr)
                i = i if i == 0 else -1
            if i != -1:
                cut = i if cut is None else min(cut, i)
        base_desc = current[:cut].rstrip() if cut is not None else current.rstrip()
        new_desc = (base_desc + "\n\n----\n" + ac) if base_desc else ac
        target = "ОПИСАНИЕ (замена блока)" if cut is not None else "ОПИСАНИЕ (добавление)"
        payload = (f"/rest/api/2/issue/{key}", {"fields": {"description": new_desc}})

    print(f"Задача: {key} | Куда: {target}", file=sys.stderr)
    print("--- превью AC ---", file=sys.stderr)
    print("\n".join(ac.splitlines()[:12]) + ("\n..." if ac.count("\n") > 12 else ""), file=sys.stderr)

    if a.dry_run:
        print("[dry-run] не отправлено", file=sys.stderr)
        return
    if not a.yes:
        try:
            if input("Публиковать? [y/N] ").strip().lower() not in ("y", "yes", "д", "да"):
                print("Отменено.", file=sys.stderr)
                return
        except EOFError:
            sys.exit("Нет подтверждения (stdin недоступен). Запусти с --yes, если человек согласился.")

    method = "POST" if a.comment else "PUT"
    try:
        st, _ = _req(base, method, payload[0], token, payload[1])
        print(f"OK {st} — опубликовано в {key}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")


if __name__ == "__main__":
    main()
