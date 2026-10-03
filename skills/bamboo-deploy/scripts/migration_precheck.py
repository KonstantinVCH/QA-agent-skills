#!/usr/bin/env python3
"""Пре-чек стенда перед деплоем сервиса с миграциями БД.

Отвечает на два вопроса, на которые деплой отвечает только через 11 минут FAILED:
  1) какие миграции применятся на этом стенде (стенд может отставать от master
     на несколько версий — поедут и чужие накопленные миграции);
  2) упадёт ли какая-нибудь из них на ДАННЫХ этого стенда (DDL, добавляющий
     FK / UNIQUE / NOT NULL, встречает мусор, оставленный прогонами).

Ничего не меняет. Находки пишет в plan.json — его понимает migration_fix.py.

Рассчитан на Flyway + PostgreSQL, миграции лежат в репозитории Bitbucket Server
(файлы V###__описание.sql в каталоге схемы).

Пример:
    python migration_precheck.py --stand 1 --schema catalog --task-branch PROJ-123

Переменные окружения:
    BITBUCKET_URL        базовый URL Bitbucket Server / Data Center
    BITBUCKET_TOKEN      HTTP access token (чтение репозитория миграций)
    MIGRATIONS_REPO      репозиторий миграций в виде PROJECT/repo
    MIGRATIONS_PATH      путь к каталогу схемы, по умолчанию
                         src/main/resources/db/migration/{schema}
    MIGRATIONS_MAIN_BRANCH  основная ветка, из которой собран образ мигратора (по умолчанию master)
    DB_PASSWORD          пароль БД стендов
    DB_DSN_TEMPLATE      DSN с плейсхолдерами {stand} и {password}, напр.
                         postgresql://app_{stand}:{password}@db.example.com:5432/app_{stand}
    DB_DSN_STAND_<N>     точный DSN для стенда N, если он не укладывается в шаблон
                         (тоже может содержать {password})
Пароли в аргументах не передавать: они попадают в историю и в лог сессии.
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request

# консоль Windows по умолчанию cp1251 и падает на «→»/«❌» посреди отчёта
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

MAIN_BRANCH = os.environ.get("MIGRATIONS_MAIN_BRANCH", "master")
MIGRATIONS_PATH = os.environ.get("MIGRATIONS_PATH", "src/main/resources/db/migration/{schema}")
TEST_MARKERS = ("тест", "test", "stub", "qa", "проверка", "dummy")


# ─────────────────────────── источники данных ───────────────────────────

def bb_base():
    url, repo = os.environ.get("BITBUCKET_URL", "").rstrip("/"), os.environ.get("MIGRATIONS_REPO", "")
    if not (url and "/" in repo):
        print("задай BITBUCKET_URL и MIGRATIONS_REPO=PROJECT/repo (см. references/project-config.md)")
        raise SystemExit(2)
    proj, name = repo.split("/", 1)
    return f"{url}/rest/api/1.0/projects/{proj}/repos/{name}"


def bb_headers():
    return {"Authorization": "Bearer " + os.environ["BITBUCKET_TOKEN"], "Accept": "application/json"}


def stand_dsn(stand):
    """DSN стенда: точный DSN_STAND_<N>, иначе шаблон. Пароль подставляется из DB_PASSWORD."""
    tpl = os.environ.get(f"DB_DSN_STAND_{stand}") or os.environ.get("DB_DSN_TEMPLATE")
    if not tpl:
        print("задай DB_DSN_TEMPLATE (или DB_DSN_STAND_<N>) либо передай --dsn")
        raise SystemExit(2)
    return tpl.format(stand=stand, password=os.environ.get("DB_PASSWORD", ""))


def schema_dir(schema):
    return MIGRATIONS_PATH.format(schema=schema)


def bb_get(path, params=None):
    url = f"{bb_base()}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=bb_headers())
    return json.load(urllib.request.urlopen(req, timeout=30))


def bb_raw(path, at):
    url = f"{bb_base()}/raw/{path}?at=" + urllib.parse.quote(f"refs/heads/{at}", safe="")
    req = urllib.request.Request(url, headers=bb_headers())
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def migration_files(schema, branch):
    """Имена файлов миграций схемы в ветке. None — ветки нет (смёржена/удалена/опечатка)."""
    try:
        files = bb_get(f"files/{schema_dir(schema)}",
                       {"at": f"refs/heads/{branch}", "limit": 1000}).get("values", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    out = []
    for f in files:
        m = re.match(r"V(\d+)__(.+)\.sql$", f)
        if m:
            out.append((int(m.group(1)), f))
    return sorted(out)


def connect(dsn):
    import psycopg2
    return psycopg2.connect(dsn)


# ─────────────────────────── разбор миграций ───────────────────────────

def parse_constraints(sql, default_schema):
    """Вытащить из SQL миграции DDL, который может не сойтись с данными.

    Разбираем построчно с запоминанием текущей таблицы: миграции пишут
    `alter table shop.foo add constraint … foreign key (…) references …`,
    иногда разнося это на несколько строк.
    """
    sql = re.sub(r"--[^\n]*", " ", sql)          # комментарии мешают регэкспам
    flat = re.sub(r"\s+", " ", sql)
    found, table = [], None
    for stmt in flat.split(";"):
        m = re.search(r"alter table (?:if exists )?([\w.]+)", stmt, re.I)
        if m:
            table = m.group(1)
        t = table if table and "." in (table or "") else f"{default_schema}.{table}" if table else None

        for m in re.finditer(
                r"add constraint (\w+) foreign key \(([^)]+)\) references ([\w.]+) ?\(([^)]+)\)", stmt, re.I):
            parent = m.group(3) if "." in m.group(3) else f"{default_schema}.{m.group(3)}"
            found.append({"kind": "fk", "name": m.group(1), "table": t,
                          "cols": [c.strip() for c in m.group(2).split(",")],
                          "parent": parent, "parent_cols": [c.strip() for c in m.group(4).split(",")]})
        for m in re.finditer(r"add constraint (\w+) unique \(([^)]+)\)", stmt, re.I):
            found.append({"kind": "unique", "name": m.group(1), "table": t,
                          "cols": [c.strip() for c in m.group(2).split(",")]})
        for m in re.finditer(r"create unique index (?:if not exists )?(\w+) on ([\w.]+) ?\(([^)]+)\)", stmt, re.I):
            tbl = m.group(2) if "." in m.group(2) else f"{default_schema}.{m.group(2)}"
            found.append({"kind": "unique", "name": m.group(1), "table": tbl,
                          "cols": [c.strip() for c in m.group(3).split(",")]})
        for m in re.finditer(r"alter column (\w+) set not null", stmt, re.I):
            found.append({"kind": "notnull", "name": f"{t}.{m.group(1)}", "table": t, "cols": [m.group(1)]})
    return found


# ─────────────────────────── проверка данных ───────────────────────────

def table_columns(cur, table):
    schema, name = table.split(".", 1)
    cur.execute("""select column_name from information_schema.columns
                   where table_schema=%s and table_name=%s""", (schema, name))
    return [r[0] for r in cur.fetchall()]


def describe_rows(cur, table, where, params, cols):
    """Показательные поля нарушителей: id + ключи + признаки жизни строки."""
    have = table_columns(cur, table)
    if not have:
        return None, []                                  # таблицы ещё нет — миграция её и создаст
    interesting = [c for c in ("id", *cols, "is_active", "active", "create_date", "update_date")
                   if c in have]
    seen, picked = set(), []
    for c in interesting:
        if c not in seen:
            seen.add(c); picked.append(c)
    cur.execute(f"select {', '.join(picked)} from {table} where {where} limit 20", params)
    return picked, cur.fetchall()


def looks_like_test_data(fields, row):
    """Эвристика, не приговор: помогает человеку выбрать удаление или заглушку."""
    d = dict(zip(fields, row))
    reasons = []
    for flag in ("is_active", "active"):
        if flag in d and d[flag] is False:
            reasons.append(f"{flag}=false")
    for k, v in d.items():
        if isinstance(v, str) and any(mk in v.lower() for mk in TEST_MARKERS):
            reasons.append(f"{k}={v!r}")
    return reasons


def column_type(cur, table, col):
    schema, name = table.split(".", 1)
    cur.execute("""select data_type from information_schema.columns
                   where table_schema=%s and table_name=%s and column_name=%s""", (schema, name, col))
    r = cur.fetchone()
    return r[0] if r else None


def check_constraint(cur, con):
    """Вернуть список нарушителей для одного констрейнта (пусто = миграция пройдёт)."""
    t = con["table"]
    if con["kind"] == "fk":
        col, pcol = con["cols"][0], con["parent_cols"][0]
        if not table_columns(cur, con["parent"]):
            return "parent-missing", None, []
        # в схемах встречаются пары varchar↔bigint: без каста Postgres откажется сравнивать
        cast = "::text" if column_type(cur, t, col) != column_type(cur, con["parent"], pcol) else ""
        where = (f"{col} is not null and not exists "
                 f"(select 1 from {con['parent']} p where p.{pcol}{cast} = {t}.{col}{cast})")
        fields, rows = describe_rows(cur, t, where, (), con["cols"])
        return "fk", fields, rows
    if con["kind"] == "unique":
        cols = ", ".join(con["cols"])
        if not table_columns(cur, t):
            return "table-missing", None, []
        cur.execute(f"select {cols}, count(*) from {t} group by {cols} having count(*) > 1 limit 20")
        return "unique", con["cols"] + ["count"], cur.fetchall()
    if con["kind"] == "notnull":
        col = con["cols"][0]
        if col not in table_columns(cur, t):
            return "column-missing", None, []
        fields, rows = describe_rows(cur, t, f"{col} is null", (), con["cols"])
        return "notnull", fields, rows
    return con["kind"], None, []


# ─────────────────────────────── main ───────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stand", help="номер тестового стенда (DSN из DB_DSN_TEMPLATE / DB_DSN_STAND_<N>)")
    ap.add_argument("--dsn", help="полный DSN вместо --stand")
    ap.add_argument("--schema", required=True, help="схема миграций, напр. catalog")
    ap.add_argument("--task-branch", help="ветка задачи в репозитории миграций (напр. PROJ-123)")
    ap.add_argument("--from-version", type=int,
                    help="считать, что стенд на этой версии (перепроверка уже применённой миграции)")
    ap.add_argument("--branch-migrations", action="store_true",
                    help="проверить и миграции ветки задачи, которых нет в master — узнать заранее, "
                         "сойдутся ли они с данными стенда (после мёржа или подмены образа)")
    ap.add_argument("--sql-file", action="append", default=[],
                    help="проверить конкретный файл миграции с диска вместо репозитория "
                         "(можно повторять; удобно для черновика миграции и для проверки самого скрипта)")
    ap.add_argument("--plan", default="plan.json", help="куда записать находки")
    a = ap.parse_args()

    if a.dsn:
        dsn = a.dsn
    elif a.stand:
        dsn = stand_dsn(a.stand)
    else:
        ap.error("нужен --stand или --dsn")

    conn = connect(dsn); cur = conn.cursor()
    cur.execute(f"""select version, success, installed_on from {a.schema}.flyway_schema_history
                    order by installed_rank desc limit 1""")
    row = cur.fetchone()
    stand_ver = a.from_version if a.from_version is not None else (int(row[0]) if row else 0)
    print(f"стенд: схема {a.schema} на версии {stand_ver}" + (f" (применена {row[2]})" if row else ""))

    master = migration_files(a.schema, MAIN_BRANCH) or []
    pending = [(v, f) for v, f in master if v > stand_ver]
    print(f"{MAIN_BRANCH}: {len(master)} миграций, последняя V{master[-1][0] if master else 0}")
    print(f"поедут при деплое: {len(pending)} → " + (", ".join(f"V{v}" for v, _ in pending) or "нет"))

    task_only = []
    if a.task_branch:
        branch = migration_files(a.schema, a.task_branch)
        if branch is None:
            # частый и нормальный случай: ветку смёржили и удалили — всё уже в master
            print(f"ветка {a.task_branch} в репозитории миграций не найдена "
                  f"(смёржена/удалена?) → сверяю только {MAIN_BRANCH}")
            branch = []
        master_names = {f for _, f in master}
        task_only = [(v, f) for v, f in branch if f not in master_names]
        if task_only:
            print(f"⚠️ миграции задачи НЕ в {MAIN_BRANCH}: " + ", ".join(f"V{v}" for v, _ in task_only))
            print(f"   если образ мигратора собран из {MAIN_BRANCH} (latest) → на стенд они не поедут")
        else:
            print(f"миграции задачи: в {MAIN_BRANCH} уже есть (или их нет в ветке) → поедут")

    # что именно проверяем: (метка, имя файла, функция-загрузчик SQL)
    to_check = [(f"V{v}", f, lambda f=f: bb_raw(f"{schema_dir(a.schema)}/{f}", MAIN_BRANCH))
                for v, f in pending]
    if a.branch_migrations and task_only:
        to_check += [(f"V{v} (ветка {a.task_branch})", f,
                      lambda f=f: bb_raw(f"{schema_dir(a.schema)}/{f}", a.task_branch))
                     for v, f in task_only]
    for path in a.sql_file:
        to_check.append((f"файл {os.path.basename(path)}", os.path.basename(path),
                         lambda p=path: open(p, encoding="utf-8").read()))

    blockers = []
    for label, fname, load in to_check:
        sql = load()
        for con in parse_constraints(sql, a.schema):
            try:
                kind, fields, rows = check_constraint(cur, con)
            except Exception as e:
                # проверить не удалось — это не «всё хорошо»: сказать прямо и идти дальше,
                # чтобы одна экзотическая миграция не прятала остальные находки
                conn.rollback()
                print(f"⚠️ {label}: не смог проверить {con['kind']} {con['name']} "
                      f"на {con['table']}: {type(e).__name__}: {str(e).splitlines()[0]}")
                continue
            if kind in ("parent-missing", "table-missing", "column-missing"):
                continue                                  # объект создаётся этой же пачкой миграций
            if not rows:
                continue
            hits = []
            for r in rows:
                hits.append({"fields": fields, "values": [str(x) for x in r],
                             "test_markers": looks_like_test_data(fields, r)})
            blockers.append({"migration": label, "file": fname, "constraint": con,
                             "rows": hits, "rows_shown": len(hits), "rows_limit": 20})
            print(f"\n❌ {label} упадёт: {con['kind']} {con['name']} на {con['table']}"
                  + (" — показаны первые 20 строк" if len(hits) == 20 else ""))
            print(f"   {' | '.join(fields)}")
            for h in hits:
                mark = "  ← похоже на тест-данные: " + ", ".join(h["test_markers"]) if h["test_markers"] else ""
                print("   " + " | ".join(h["values"]) + mark)

    plan = {"dsn_stand": a.stand, "schema": a.schema, "stand_version": stand_ver,
            "pending": [f"V{v}" for v, _ in pending],
            "task_migrations_not_in_master": [f"V{v}" for v, _ in task_only],
            "blockers": blockers}
    with open(a.plan, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    conn.close()

    if blockers:
        print(f"\nнайдено блокеров: {len(blockers)} → план в {a.plan}; чинить — migration_fix.py")
        sys.exit(1)
    print("\n✅ данные стенда миграции пропустят")
    sys.exit(0)


if __name__ == "__main__":
    main()
