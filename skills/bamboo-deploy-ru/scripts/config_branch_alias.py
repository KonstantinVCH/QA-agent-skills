#!/usr/bin/env python3
"""Задеплоить конкретную ветку репозитория конфигурации, даже если её имя не подходит под автоподбор.

Схема, под которую написан скрипт: деплой-скрипт в Bamboo берёт имя релиза (например
PROJ-123-5), ищет в репозитории конфигурации (Ansible/Helm values/kustomize) ветку с номером
задачи (PROJ-123, feature-PROJ-123, feature/PROJ-123) и деплоит с неё; не нашёл — берёт
основную ветку. Ветки вида TMP-TEST-PROJ-123, PROJ-123-FOR-TEST или произвольные имена такой
автоподбор не находит — деплой молча уезжает на основную ветку.

Это не повод отвечать «не могу»: ветка-алиас решает вопрос за один запрос. Алиас — второе имя
для того же коммита, он ничего не копирует и не меняет содержимое.

    python config_branch_alias.py --scan-stand 5                               # кто какую ветку тянет
    python config_branch_alias.py --from PROJ-123-FOR-TESTING --as PROJ-123
    python config_branch_alias.py --from PROJ-123-FOR-TESTING --as PROJ-123 --check-env 123456
    python config_branch_alias.py --from PROJ-123-FOR-TESTING --as PROJ-123 --apply

Если имя-алиас уже занято другой веткой (частый случай: PROJ-123-FOR-TESTING выросла из
PROJ-123, и ту тянут другие сервисы), скрипт не перезаписывает чужое, а берёт свободную
метку {TASK_KEY_PREFIX}-990NN: деплой ищет ветку по имени релиза, поэтому метка работает так же.

Переменные окружения:
    BITBUCKET_URL      базовый URL Bitbucket Server / Data Center, напр. https://git.example.com
    BITBUCKET_TOKEN    HTTP access token с правом записи в репозиторий конфигурации
                       (если не задан — логин берётся из git credential helper)
    CONFIG_REPO        репозиторий конфигурации в виде PROJECT/repo, напр. DEVOPS/deploy-config
    BAMBOO_URL         базовый URL Bamboo (для --scan-stand и --check-env)
    BAMBOO_TOKEN       personal access token Bamboo
    TASK_KEY_PREFIX    ключ проекта задач для меток, по умолчанию PROJ
    DEPLOY_ENV_TEMPLATE  имя окружения Bamboo для стенда, по умолчанию "Testing-{stand}"
    CONFIG_BRANCH_VAR  имя переменной окружения деплоя, жёстко задающей ветку (для сообщений)
    BRANCH_PINNED_RE / BRANCH_AUTO_RE / BRANCH_FINAL_RE
                       регулярки строк лога деплоя о выборе ветки (см. references/config-branch.md)
"""
import argparse, base64, json, os, re, subprocess, sys, urllib.error, urllib.parse, urllib.request

BITBUCKET = os.environ.get("BITBUCKET_URL", "").rstrip("/")
CONFIG_REPO = os.environ.get("CONFIG_REPO", "")
BAMBOO = os.environ.get("BAMBOO_URL", "").rstrip("/") + "/rest/api/latest"
PREFIX = os.environ.get("TASK_KEY_PREFIX", "PROJ")
ENV_TEMPLATE = os.environ.get("DEPLOY_ENV_TEMPLATE", "Testing-{stand}")
BRANCH_VAR = os.environ.get("CONFIG_BRANCH_VAR", "config_branch")
PINNED_RE = re.compile(os.environ.get("BRANCH_PINNED_RE", r"задана вручную|pinned to"), re.I)
AUTO_RE = re.compile(os.environ.get("BRANCH_AUTO_RE", r"найдена ветка|found config branch"), re.I)
FINAL_RE = re.compile(os.environ.get("BRANCH_FINAL_RE", r"выполняется из ветки|deploying from config branch"), re.I)
NL = chr(10)
LABELS = range(99001, 99100)          # свободные номера-метки на случай занятого имени
_AUTH = None

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def repo_url():
    if not (BITBUCKET and "/" in CONFIG_REPO):
        print("задай BITBUCKET_URL и CONFIG_REPO=PROJECT/repo (см. references/project-config.md)")
        raise SystemExit(2)
    proj, repo = CONFIG_REPO.split("/", 1)
    return f"{BITBUCKET}/rest/api/1.0/projects/{proj}/repos/{repo}"


def bb_auth():
    """Заголовок авторизации Bitbucket: BITBUCKET_TOKEN, иначе логин из git credential helper.

    Отдельный токен заводить не обязательно: если репозитории уже клонированы по https,
    креды лежат в менеджере учётных данных ОС и git отдаёт их по запросу.
    """
    global _AUTH
    if _AUTH:
        return _AUTH
    tok = os.environ.get("BITBUCKET_TOKEN")
    if tok:
        _AUTH = "Bearer " + tok
        return _AUTH
    host = urllib.parse.urlparse(BITBUCKET).hostname or ""
    p = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True,
                       input="protocol=https" + NL + "host=" + host + NL + NL)
    kv = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
    if kv.get("username") and kv.get("password"):
        _AUTH = "Basic " + base64.b64encode(f"{kv['username']}:{kv['password']}".encode()).decode()
        return _AUTH
    print("нет доступа к Bitbucket: задай BITBUCKET_TOKEN или склонируй репозиторий по https, "
          "чтобы креды попали в менеджер")
    raise SystemExit(1)


def api(path, method="GET", body=None, params=None):
    url = f"{repo_url()}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": bb_auth(), "Accept": "application/json", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=40)), None
    except urllib.error.HTTPError as e:
        try:
            return None, json.load(e)
        except Exception:                                        # noqa: BLE001
            return None, {"errors": [{"message": f"HTTP {e.code}"}]}


def bamboo(path):
    r = urllib.request.Request(f"{BAMBOO}/{path}", headers={
        "Authorization": "Bearer " + os.environ["BAMBOO_TOKEN"], "Accept": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=90))


def branch_mode(log):
    """Разобрать строки, которыми деплой-скрипт сообщает выбор ветки конфигурации.

        <PINNED>  <ветка>   — ветка жёстко задана переменной окружения деплоя
        <AUTO>    <ветка>   — сработал автоподбор по имени релиза
        <FINAL>   <ветка>   — итог, печатается всегда

    Пин и автоподбор взаимоисключающи, итоговая строка есть в обоих случаях. Если из трёх
    в логе только итоговая — автоподбор ветку задачи не нашёл и уехал на то, что в ней.
    Ветка берётся как последнее слово после двоеточия.
    """
    final = None
    for line in log.splitlines():
        if PINNED_RE.search(line):
            return "pinned", line.split(":")[-1].strip()
        if AUTO_RE.search(line):
            return "auto", line.split(":")[-1].strip()
        if FINAL_RE.search(line):
            final = line.split(":")[-1].strip()
    if final:
        return "auto", (None if final in ("master", "main") else final)
    return None, None


def env_log(env_id):
    """Лог последнего деплоя окружения. Строки о ветке идут в первых десятках записей."""
    res = bamboo(f"deploy/environment/{env_id}/results?max-results=1").get("results", [])
    if not res:
        return None, None
    d = bamboo(f"deploy/result/{res[0]['id']}?includeLogs=true&max-results=600")
    return (NL.join(e.get("unstyledLog", "") for e in d["logEntries"]["logEntry"]),
            res[0]["deploymentVersionName"])


def env_branch_mode(env_id):
    """Как окружение выбирает ветку сейчас — по логу последнего деплоя.

    Проверять ДО создания алиаса: если на окружении выставлена переменная-пин, она
    перебивает автоподбор целиком, и алиас ничего не даст. Значения переменных окружения
    через API обычному пользователю часто недоступны (403), поэтому смотрим лог.
    """
    if not os.environ.get("BAMBOO_TOKEN"):
        return None, "BAMBOO_TOKEN не задан — режим окружения не проверить"
    try:
        log, _ = env_log(env_id)
    except Exception as e:                                       # noqa: BLE001
        return None, f"не удалось прочитать лог окружения: {type(e).__name__}"
    if log is None:
        return None, "на окружении ещё не было деплоев — режим неизвестен"
    mode, detail = branch_mode(log)
    if mode:
        return mode, detail
    return None, "в логе последнего деплоя нет строки о выборе ветки"


def scan_stand(stand, only=None):
    """Какую ветку конфигурации тянет каждый сервис стенда.

    Переменная-пин живёт на ОКРУЖЕНИИ сервиса, а не на стенде целиком: на одном стенде часть
    сервисов бывает запинена на ветку задачи, часть — на основную ветку (и тогда конфиг задачи
    до них не доезжает), часть работает автоподбором.
    """
    if not os.environ.get("BAMBOO_TOKEN") or BAMBOO == "/rest/api/latest":
        print("нужны BAMBOO_URL и BAMBOO_TOKEN"); return 1
    want = ENV_TEMPLATE.format(stand=stand).strip().lower()
    names = [s.strip().lower() for s in only.split(",")] if only else None
    rows, skipped = [], 0
    for proj in bamboo("deploy/project/all"):
        svc = proj["name"].split(" - ")[-1].strip()
        if names and not any(n in svc.lower() for n in names):
            continue
        for e in proj.get("environments", []):
            if not e["name"].strip().lower().startswith(want):
                continue
            try:
                log, release = env_log(e["id"])
            except Exception:                                    # noqa: BLE001
                skipped += 1
                continue
            if log is None:
                continue
            mode, detail = branch_mode(log)
            rows.append((svc, e["id"], release, mode, detail))
    if not rows:
        print(f"окружений «{want}…» не нашлось" + (f" по фильтру --only {only}" if only else ""))
        return 1

    print(f"стенд {stand}: окружений {len(rows)}" + (f", пропущено {skipped}" if skipped else ""))
    print(f"  {'сервис':26} {'env':10} {'релиз':24} откуда ветка")
    main_names = ("master", "main")
    for svc, eid, release, mode, detail in sorted(rows):
        if mode == "pinned":
            src = f"ПИН {detail}" + ("   <- пин на основную ветку: конфиг задачи не доедет"
                                     if detail in main_names else "")
        elif mode == "auto":
            src = f"автоподбор -> {detail}" if detail else "автоподбор -> ветки задачи нет, основная"
        else:
            src = "по логу не понял"
        print(f"  {svc[:26]:26} {str(eid):10} {str(release)[:24]:24} {src}")
    pinned_main = [r[0] for r in rows if r[3] == "pinned" and r[4] in main_names]
    if pinned_main:
        print("")
        print("Пин на основную ветку — это не «ветки нет», а выключенный автоподбор. Сервисы: "
              + ", ".join(pinned_main))
        print(f"  Алиас им не поможет: нужен DevOps — очистить {BRANCH_VAR} "
              "или выставить её в нужную ветку.")
    return 0


def find_branch(name):
    d, err = api("branches", params={"filterText": name, "limit": 25})
    if err:
        return None
    for b in (d or {}).get("values", []):
        if b["displayId"] == name:
            return b
    return None


def free_label():
    """Свободный номер-метка, если каноничное имя занято чужой веткой."""
    for n in LABELS:
        name = f"{PREFIX}-{n}"
        if not find_branch(name):
            return name
    return None


def create_alias(alias, src_name):
    d, err = api("branches", method="POST", body={"name": alias, "startPoint": f"refs/heads/{src_name}"})
    if err:
        msg = "; ".join(e.get("message", "") for e in err.get("errors", []))
        print(f"не удалось создать {alias}: {msg}")
        if "denied" in msg.lower() or "permission" in msg.lower():
            print(f"  прав на запись в {CONFIG_REPO} нет — попроси DevOps создать ветку "
                  f"{alias} от {src_name}, либо выставить {BRANCH_VAR}={src_name} на окружении")
        return 1
    print(f"создана ветка {d['displayId']} -> {d['latestCommit'][:12]}")
    print("")
    print("Дальше:")
    print(f"  1. создать релиз с именем {alias}-N из зелёного билда сервиса")
    print("  2. запустить деплой этого релиза")
    print(f"  3. в логе убедиться, что выбрана ветка {alias}")
    print(f"  4. после прогона алиас удалить — исходная ветка {src_name} не пострадает")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Ветка-алиас в репозитории конфигурации + скан стенда")
    ap.add_argument("--from", dest="src", help="существующая ветка репозитория конфигурации")
    ap.add_argument("--as", dest="alias", help=f"имя-алиас, напр. {PREFIX}-123")
    ap.add_argument("--scan-stand", help="показать, какую ветку тянет каждый сервис стенда")
    ap.add_argument("--only", help="фильтр сервисов для --scan-stand: order-service,payment-service")
    ap.add_argument("--check-env", help="environmentId одного сервиса: проверить, не выключен ли "
                                        "автоподбор пином переменной (нужен BAMBOO_TOKEN)")
    ap.add_argument("--apply", action="store_true", help="без него — только план")
    a = ap.parse_args()

    if a.scan_stand:
        return scan_stand(a.scan_stand, a.only)
    if not (a.src and a.alias):
        ap.error("нужны --from и --as (или --scan-stand)")

    pinned_to = None
    if a.check_env:
        mode, detail = env_branch_mode(a.check_env)
        if mode == "pinned":
            pinned_to = detail
            print(f"окружение {a.check_env}: ПИН — ветка задана вручную: {detail}")
            if detail in (a.alias, a.src):
                print("  переменная уже указывает на нужную ветку — алиас не нужен, просто катите")
                return 0
            print("  автоподбор выключен целиком: даже с правильным именем релиза деплой")
            print(f"  возьмёт ветку из переменной. Алиас не поможет, пока DevOps не очистит")
            print(f"  {BRANCH_VAR} — или сразу выставит её в {a.src}.")
        elif mode == "auto":
            print(f"окружение {a.check_env}: автоподбор работает"
                  + (f" (последний раз взял {detail})" if detail else " (ветки задачи не было -> основная)"))
        else:
            print(f"окружение {a.check_env}: {detail}")
        print("")

    src = find_branch(a.src)
    if not src:
        print(f"ветки {a.src} в {CONFIG_REPO} нет — проверь имя:")
        d, _ = api("branches", params={"filterText": a.src.split("-")[-1], "limit": 10})
        for b in (d or {}).get("values", []):
            print("   похожая:", b["displayId"])
        return 1
    print(f"источник: {a.src} -> коммит {src['latestCommit'][:12]}")

    alias = a.alias
    existing = find_branch(alias)
    if existing:
        if existing["latestCommit"] == src["latestCommit"]:
            print(f"алиас {alias} уже существует и указывает на тот же коммит — создавать нечего")
            print("")
            print(f"Дальше: релиз с именем {alias}-N, деплой, в логе — выбрана ветка {alias}.")
            return 0
        # Частый случай: PROJ-123-FOR-TESTING выросла из PROJ-123, и ту тянут другие
        # сервисы стенда. Перезапись сломала бы им деплой — берём свободную метку.
        print(f"имя {alias} занято другой веткой -> коммит {existing['latestCommit'][:12]}")
        print("  перезаписывать нельзя: её могут тянуть другие сервисы.")
        alias = free_label()
        if not alias:
            print(f"  свободных меток {PREFIX}-{LABELS.start}..{LABELS.stop - 1} не осталось — "
                  "удали старые метки в репозитории конфигурации")
            return 1
        print(f"  беру свободную метку {alias}: деплой ищет ветку по имени релиза, "
              "поэтому метка работает так же")

    print("")
    print(f"план: создать ветку {alias} от {a.src} (алиас на тот же коммит, содержимое не меняется)")
    if alias != a.alias:
        print(f"      имя релиза тогда {alias}-N, а не {a.alias}-N")
    if pinned_to:
        # пин перебивает автоподбор: алиас создастся, но деплой его не увидит — не мусорим ветками
        print("ВНИМАНИЕ: на окружении активен пин — деплой проигнорирует алиас.")
        if a.apply:
            print(f"  --apply не выполняю: сначала снять {BRANCH_VAR} (DevOps), потом алиас.")
            return 1
    if not a.apply:
        print("это dry-run. Показать человеку, получить «да», затем повторить с --apply")
        return 0
    return create_alias(alias, a.src)


if __name__ == "__main__":
    sys.exit(main())
