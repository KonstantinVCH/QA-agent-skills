#!/usr/bin/env python3
"""guard_external_writes.py — PreToolUse-хук автопилота qa-autopilot-ru: детерминированный hard-stop.

Читает JSON от Claude Code из stdin (tool_name, tool_input). Если активного прогона нет
(~/.claude/qa-autopilot/active.json) — молча разрешает (exit 0): вне автопилота хук инертен.
Иначе:
  * write-инструменты наружу (трекер, вики, тест-кейсы, PR/push, мессенджеры) — запрет,
    пока фаза != PUBLISH и действие не перечислено в APPROVAL.md;
  * деплой вне разрешённых стендов, запрещённые окружения и prod-хосты, правка данных стенда
    скриптами в боевом режиме, сброс общего мока, опасный SQL, git push — запрет всегда;
  * прочее — разрешено.
Блокировка = exit 2 + причина в stderr (её видит модель). Каждый отказ пишется в progress.md.
Это защита от ошибки, не от злого умысла: третий слой — классификатор auto mode Claude Code.

Настройка (подробно — references/project-config.md):
  QA_AUTOPILOT_STANDS           разрешённые стенды; имя стенда обязано стоять в команде деплоя
  QA_AUTOPILOT_FORBIDDEN_ENVS   слова запрещённых окружений (по умолчанию prod,production,stable)
  QA_AUTOPILOT_PROD_HOSTS       подстроки prod-хостов через запятую (logs.prod.example.com,…)
  QA_AUTOPILOT_BRANCH_REPOS     репозитории, где человек может разрешить тестовую ветку (grant test-branch)
  QA_AUTOPILOT_EXTRA_DENY       доп. regex запрещённых команд Bash (например, переключение общего ингресса)
  QA_AUTOPILOT_EXTRA_MCP_DENY   доп. regex запрещённых MCP-инструментов
  QA_AUTOPILOT_TASK_RX          формат ключа задачи (по умолчанию [A-Z][A-Z0-9]+-\\d+)
  QA_AUTOPILOT_DEPLOY_RX        regex запроса на деплой (по умолчанию — очередь деплоя Bamboo)
  JIRA_URL, CONFLUENCE_URL, BITBUCKET_URL, ALLURE_URL — хосты систем для распознавания записи
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ACTIVE_PTR = Path.home() / ".claude" / "qa-autopilot" / "active.json"
TASK_RX = os.environ.get("QA_AUTOPILOT_TASK_RX") or r"[A-Z][A-Z0-9]+-\d+"


def _list(name, default=""):
    return [s.strip() for s in (os.environ.get(name) or default).split(",") if s.strip()]


STANDS = _list("QA_AUTOPILOT_STANDS")
FORBIDDEN_ENVS = _list("QA_AUTOPILOT_FORBIDDEN_ENVS", "prod,production,stable")
PROD_HOSTS = _list("QA_AUTOPILOT_PROD_HOSTS")
# Ветка задачи заводится ради подмены конфигурации в деплое (например, образа мигратора); в
# репозитории сервиса она запустила бы сборку и CI. Список расширяется переменной окружения,
# а не разрешением человека в чате.
BRANCH_REPOS = tuple(_list("QA_AUTOPILOT_BRANCH_REPOS"))


def _host_rx(*names):
    hosts = [urlparse(os.environ[n]).netloc for n in names if os.environ.get(n)]
    hosts = [h for h in hosts if h]
    return re.compile("|".join(re.escape(h) for h in hosts), re.I) if hosts else None


# --- MCP-инструменты, публикующие наружу (regex по tool_name; префикс сервера — любой) ---
MCP_PUBLISH = {
    "jira_comment": re.compile(r"^mcp__.+__jira_(add_comment|edit_comment)$"),
    "jira_issue": re.compile(r"^mcp__.+__jira_(create_issue|batch_create_issues|update_issue|transition_issue|"
                             r"delete_issue|create_issue_link|link_to_epic|add_worklog|add_watcher|remove_watcher|"
                             r"create_remote_issue_link|update_proforma_form_answers|add_issues_to_sprint|"
                             r"create_sprint|update_sprint|create_version|batch_create_versions|update_version)$"),
    "confluence": re.compile(r"^mcp__.+__confluence_(create_page|update_page|update_page_section|delete_page|"
                             r"add_comment|add_inline_comment|reply_to_comment|add_label|move_page|"
                             r"upload_attachment|upload_attachments|delete_attachment)$"),
    "allure": re.compile(r"^mcp__.*allure.*__(create_|update_|delete_|add_|set_|bulk_|assign_|resolve_|run_|"
                         r"close_|reopen_|merge_|rename_|remove_|restore_)", re.I),
    "messaging": re.compile(r"^mcp__.*(telegram|slack|discord|mattermost|teams|mail|gmail).*__"
                            r"(send|reply|post|edit|react|create_draft)", re.I),
    "git_remote": re.compile(r"^mcp__.*(gitlab|github|bitbucket).*__(create_|push_|fork_|merge_|update_pull)", re.I),
}
MCP_ALWAYS_DENY = re.compile(r"^mcp__.*__(obsidian_)?delete_file$")
EXTRA_MCP_DENY = re.compile(os.environ["QA_AUTOPILOT_EXTRA_MCP_DENY"]) if os.environ.get("QA_AUTOPILOT_EXTRA_MCP_DENY") else None
MCP_SQL = re.compile(r"^mcp__.*(postgres|pg|sql|database|db).*__(query|execute|execute_sql|run_query)$", re.I)


def _forbidden_env_rules():
    """Запрещённое окружение рядом с командой деплоя, БД или логов — в любом порядке."""
    tools = r"(psql|bamboo|deploy|kibana|opensearch|helm|kubectl)"
    rules = []
    for w in FORBIDDEN_ENVS:
        word = rf"(?<!\w){re.escape(w)}(?!\w)"
        rules.append((re.compile(rf"{word}.*\b{tools}\b|\b{tools}\b.*{word}", re.I),
                      f"окружение «{w}» рядом с деплоем/БД/логами — hard-stop #1/#7"))
    for h in PROD_HOSTS:
        rules.append((re.compile(re.escape(h), re.I), f"prod-хост {h} — hard-stop #7"))
    return rules


# --- Bash: всегда запрещено ---
BASH_ALWAYS = [
    (re.compile(r"\bgit\s+push\b"), "git push запрещён автопилоту (PR/merge/push — только человек)"),
    # (?![-\w]) — чтобы не резать чтение истории: `git merge-base origin/master …`
    (re.compile(r"\bgit\s+(merge|rebase)(?![-\w]).*\b(master|main)\b"), "merge/rebase в master запрещён"),
    (re.compile(r"migration_fix\.py\b.*--apply"), "скрипт правки данных стенда в боевом режиме — hard-stop #2"),
    (re.compile(r"\bwm\.py\s+(restore|delete-api)\b"), "массовое восстановление/удаление стабов общего мока — hard-stop #4"),
    (re.compile(r"__admin/(mappings/)?reset\b|-X\s*DELETE\b[^|;&]*__admin/mappings/?(\s|[\"']|$)", re.I),
     "сброс или удаление всех стабов общего WireMock — hard-stop #4"),
    (re.compile(r"\bDROP\s+(TABLE|SCHEMA|DATABASE|INDEX|VIEW)\b", re.I), "DROP — hard-stop #2"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE — hard-stop #2"),
    (re.compile(r"\bALTER\s+TABLE\b", re.I), "ALTER TABLE — hard-stop #2"),
    (re.compile(r"telegram-agent\s+(send|reply|edit)\b"), "отправка сообщений — только после APPROVAL"),
] + _forbidden_env_rules()
if os.environ.get("QA_AUTOPILOT_EXTRA_DENY"):
    BASH_ALWAYS.append((re.compile(os.environ["QA_AUTOPILOT_EXTRA_DENY"]),
                        "команда из QA_AUTOPILOT_EXTRA_DENY (проектный hard-stop, например смена ингресса)"))

# --- Bash: публикация наружу через REST (разрешается только в PUBLISH при APPROVAL) ---
# Запись узнаётся не только по флагам curl: на пилоте деплой ушёл из python
# (urllib, method='POST') и прошёл мимо сторожа — упал лишь на ответе самого сервера деплоя.
WRITE_METHOD = re.compile(
    r"(-X\s*(POST|PUT|PATCH|DELETE)\b|--request\s+(POST|PUT|PATCH|DELETE)\b|\s-d\s|--data(-raw|-binary)?\b|--json\b"
    r"|Invoke-RestMethod.*-Method\s+(Post|Put|Patch|Delete)"
    r"|method\s*=\s*['\"](POST|PUT|PATCH|DELETE)['\"]"
    r"|\b(requests|httpx|session)\.(post|put|patch|delete)\s*\()", re.I)
# Скрипт из файла (`python deploy.py`) сторож видит только по имени — читаем сам файл.
SCRIPT_ARG = re.compile(r"\b(?:python3?|py|node|bash|sh)\s+[\"']?([^\s\"'|;&<>]+\.(?:py|mjs|js|sh))", re.I)
BASH_PUBLISH = [
    ("jira", re.compile(r"/rest/api/(2|latest)/(issue|comment|issueLink|worklog|version)\b", re.I), _host_rx("JIRA_URL")),
    ("confluence", re.compile(r"/rest/api/content\b", re.I), _host_rx("CONFLUENCE_URL")),
    ("allure", re.compile(r"/api/(testcase|launch|testresult|testplan|rs/)", re.I), _host_rx("ALLURE_URL")),
    ("bitbucket_pr", re.compile(r"/rest/(api|branch-utils)/(1\.0|latest)/projects/.*/(pull-requests|branches|browse)\b",
                                re.I), None),
]
BAMBOO_DEPLOY = re.compile(os.environ.get("QA_AUTOPILOT_DEPLOY_RX") or r"/rest/api/latest/queue/deployment|/deploy/",
                           re.I)


def scripts_text(cmd, cwd):
    """Текст локальных скриптов, запускаемых командой: проверки деплоя и публикации применяются и к ним."""
    out = []
    bases = [cwd] + [d.strip() for d in re.findall(r"\bcd\s+\"?([^\"&;|]+?)\"?\s*(?:&&|;)", cmd)]
    for name in SCRIPT_ARG.findall(cmd):
        p = Path(os.path.expanduser(name))
        for cand in ([p] if p.is_absolute() else [Path(os.path.expanduser(b)) / p for b in bases]):
            try:
                # собственные скрипты автопилота не сканируются: в их тексте есть URL и слова запретов
                if cand.resolve().parent == Path(__file__).resolve().parent:
                    break
                if cand.is_file() and cand.stat().st_size <= 512_000:
                    out.append("\n" + cand.read_text(encoding="utf-8", errors="replace"))
                    break
            except OSError:
                continue
    return "".join(out)


SQL_NO_WHERE = [
    (re.compile(r"\bDELETE\s+FROM\s+[\w.]+\s*(;|$|\)|'|\"|}|\\\")", re.I | re.M), "DELETE без WHERE"),
    (re.compile(r"\bUPDATE\s+[\w.\"]+\s+SET\b(?![^;]*\bWHERE\b)", re.I | re.S), "UPDATE без WHERE"),
]

# DDL-правила (DROP/TRUNCATE/ALTER) переиспользуются для SQL, пришедшего через MCP-сервер БД
SQL_DDL = [r for r in BASH_ALWAYS if r[1].startswith(("DROP", "TRUNCATE", "ALTER"))]


def load_active():
    if not ACTIVE_PTR.exists():
        return None, None
    try:
        ptr = json.loads(ACTIVE_PTR.read_text(encoding="utf-8"))
        run_dir = Path(ptr["run_dir"])
        st = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        return run_dir, st
    except Exception:
        return None, None


def approval(run_dir):
    p = run_dir / "APPROVAL.md"
    if not p.exists():
        return None
    ap = {"jira_comment": False, "bugs": [], "testcases": []}
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(\w+)\s*:\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if k == "jira_comment":
            ap[k] = v.lower() in ("yes", "да", "true")
        elif k in ("bugs", "testcases"):
            ap[k] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
    return ap


def deny(run_dir, tool, reason):
    try:
        import datetime as dt
        with open(run_dir / "progress.md", "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%H:%M}] HOOK deny {tool}: {reason}\n")
    except Exception:
        pass
    print(f"[qa-autopilot-ru guard] ЗАПРЕЩЕНО: {reason}. Инструмент: {tool}. "
          f"Если действие нужно — это точка для человека: autopilot.py block --reason ... "
          f"или дождаться APPROVAL.md (фаза PUBLISH).", file=sys.stderr)
    sys.exit(2)


def granted_branch(cmd, st):
    """Тестовая ветка, которую человек разрешил явно: autopilot.py grant --scope test-branch --value PROJ-123.
    Разрешается ровно одно: создание ветки, в имени которой стоит этот номер, в репозитории из
    QA_AUTOPILOT_BRANCH_REPOS. Не PR, не merge, не удаление и не переписывание чужой ветки.
    Урок пилота: подмена образа мигратора требует ветку инфраструктурного репозитория,
    а гард резал её безусловно.

    Коммит файла через `/browse` разрешается в ту же ветку: параметр `branch` в команде
    должен совпадать с разрешённой. Без параметра запись идёт в ветку по умолчанию — запрет."""
    if re.search(r"pull-requests|/merge", cmd, re.I):
        return False
    if not any(f"/repos/{r}/" in cmd for r in BRANCH_REPOS):
        return False
    if "/browse" in cmd:
        # Запись файла в ветку — PUT, удаление файла — DELETE: удалять человек не разрешал.
        if re.search(r"(-X\s*|--request\s+)DELETE\b|-Method\s+Delete\b", cmd, re.I):
            return False
        # ветка приходит параметром формы (-F branch=…) или полем JSON
        m = re.search(r'branch["\'=\s:]+([\w./-]+)', cmd)
    else:
        # Создание ветки — POST (в curl подразумевается при -d). Явные DELETE, PUT и PATCH
        # человек, разрешая «завести тестовую ветку», не разрешал.
        if re.search(r"(-X\s*|--request\s+)(DELETE|PUT|PATCH)\b|-Method\s+(Delete|Put|Patch)\b", cmd, re.I):
            return False
        m = re.search(r'"name"\s*:\s*"([^"]+)"', cmd)
    branch = (m.group(1) if m else "").replace("refs/heads/", "").strip()
    if not branch:
        return False
    for g in st.get("grants", []):
        if g.get("scope") != "test-branch" or not re.fullmatch(TASK_RX, g.get("value", "")):
            continue
        # Номер задачи должен стоять в имени создаваемой ветки, а не где угодно в команде:
        # иначе номер в теле коммита открывал запись в любую ветку, включая master.
        v = g["value"]
        if branch == v or re.fullmatch(re.escape(v) + r"[-_][\w.-]+", branch):
            return True
    return False


def publish_allowed(kind, st, ap):
    if st.get("phase") != "PUBLISH" or not ap:
        return False
    if kind == "jira_comment":
        return ap["jira_comment"] or bool(ap["bugs"])
    if kind == "jira_issue":
        return bool(ap["bugs"])
    if kind == "allure":
        return bool(ap["testcases"])
    if kind == "confluence":
        return False  # вики автопилот не правит никогда
    return False


def stand_in_deploy(cmd):
    """Команда деплоя обязана назвать разрешённый стенд (или передавать окружение переменной)."""
    for s in STANDS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])", cmd, re.I):
            return True
    return bool(re.search(r"TARGET_ENV|\benv(ironment)?[_-]?id\b", cmd, re.I))


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.exit(0)
    run_dir, st = load_active()
    if run_dir is None:
        sys.exit(0)  # вне автопилота хук инертен
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}
    ap = approval(run_dir)

    # --- MCP ---
    if MCP_ALWAYS_DENY.search(tool) or (EXTRA_MCP_DENY and EXTRA_MCP_DENY.search(tool)):
        deny(run_dir, tool, "удаление/публикация через MCP запрещены автопилоту")
    for kind, rx in MCP_PUBLISH.items():
        if rx.search(tool):
            if kind in ("messaging", "git_remote"):
                deny(run_dir, tool, f"{kind}: публикация наружу запрещена автопилоту (hard-stop #5/#6)")
            if not publish_allowed(kind, st, ap):
                deny(run_dir, tool, f"{kind}: публикация наружу до APPROVAL.md запрещена (фаза {st.get('phase')})")
            return
    if MCP_SQL.search(tool):
        sql = json.dumps(inp, ensure_ascii=False)
        for rx, why in SQL_NO_WHERE:
            if rx.search(sql):
                deny(run_dir, tool, f"{why} — hard-stop #2")
        for rx, why in SQL_DDL:
            if rx.search(sql):
                deny(run_dir, tool, why)
        return

    # --- Bash / PowerShell ---
    if tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        # Запреты «git push», SQL и т.п. — только по тексту самой команды: в чужом скрипте эти слова
        # встречаются в строках и комментариях (сам autopilot.py пишет «git push запрещён»),
        # и сторож блокировал собственный оркестратор.
        for rx, why in BASH_ALWAYS:
            if rx.search(cmd):
                deny(run_dir, tool, why)
        for rx, why in SQL_NO_WHERE:
            if rx.search(cmd) and re.search(r"\b(psql|docker\s+exec|query)\b", cmd, re.I):
                deny(run_dir, tool, f"{why} — hard-stop #2")
        # Деплой и публикация наружу — и по тексту запускаемых локальных скриптов.
        cmd += scripts_text(cmd, data.get("cwd") or os.getcwd())
        # Только запись: GET результатов и логов деплоя — чтение (гард резал разбор
        # чужого деплоя из-за одного пути в URL)
        if BAMBOO_DEPLOY.search(cmd) and WRITE_METHOD.search(cmd):
            # Откат релиза на CLEANUP — не автоматическое действие: стенд общий, и «релиз до»
            # нужен не всегда (SKILL.md, раздел CLEANUP). Решение человека записывается grant-ом,
            # значение (сервис или окружение) обязано встречаться в самой команде.
            cleanup_ok = st.get("phase") == "CLEANUP" and any(
                g.get("scope") == "cleanup-deploy" and g.get("value") and g["value"] in cmd
                for g in st.get("grants", []))
            if (st.get("phase") != "ENV" and not cleanup_ok) or st.get("flags", {}).get("assume_deployed"):
                deny(run_dir, tool, "деплой разрешён в фазе ENV; откат релиза в CLEANUP — "
                                    "только после autopilot.py grant --scope cleanup-deploy --value <сервис> "
                                    "--quote \"<слова человека>\" (при --assume-deployed деплой запрещён совсем)")
            if not stand_in_deploy(cmd):
                deny(run_dir, tool, "деплой без явного разрешённого стенда (QA_AUTOPILOT_STANDS) в команде — hard-stop #1")
        if WRITE_METHOD.search(cmd):
            for kind, path_rx, host_rx in BASH_PUBLISH:
                if path_rx.search(cmd) or (host_rx and host_rx.search(cmd)):
                    if kind == "bitbucket_pr":
                        if granted_branch(cmd, st):
                            continue
                        deny(run_dir, tool, "PR, ветки и запись файлов в Bitbucket через REST — hard-stop #5. "
                             "Тестовая ветка — только в репозитории из QA_AUTOPILOT_BRANCH_REPOS после "
                             "autopilot.py grant --scope test-branch --value <ключ задачи>, и только создание "
                             "ветки и коммит в неё")
                    k = "jira_comment" if kind == "jira" and "comment" in cmd else ("jira_issue" if kind == "jira" else kind)
                    if not publish_allowed(k, st, ap):
                        deny(run_dir, tool, f"{kind}: write-запрос наружу до APPROVAL.md запрещён (фаза {st.get('phase')})")
    sys.exit(0)


if __name__ == "__main__":
    main()
