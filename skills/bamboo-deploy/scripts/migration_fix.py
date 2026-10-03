#!/usr/bin/env python3
"""Починка блокеров, найденных migration_precheck.py, чтобы миграция прошла.

Работает только с FK-блокерами: у них есть два безопасных решения —
завести родителя-заглушку (данные сохраняются) или удалить строку-сироту
(если это мусор прогона). Дубли по UNIQUE и NULL под NOT NULL не чинит:
там выбор «какую из двух строк оставить» — бизнес-решение, автоматике не место.

Без --apply ничего не делает: печатает план и SQL, чтобы человек увидел,
что именно предлагается. С --apply — одна транзакция, бэкап перед изменением
и обязательный контрольный чек: если нарушители остались, делает ROLLBACK,
потому что применённый наполовину фикс хуже, чем не начатый.

Примеры:
    # посмотреть план
    python migration_fix.py --plan plan.json --stand 1
    # активные/реальные строки сохранить заглушкой, мусор прогонов удалить
    python migration_fix.py --plan plan.json --stand 1 --stub-parents --delete-test-data --apply
    # заглушку скопировать со стенда, где миграция уже прошла (так её уже заводили)
    python migration_fix.py --plan plan.json --stand 1 --stub-parents --reference-stand 3 --apply
    # checksum mismatch: снять запись версии из истории Flyway
    python migration_fix.py --stand 1 --schema catalog --checksum-version 130 --apply
    # «already exists»: пометить версию применённой, скопировав запись с эталонного стенда
    python migration_fix.py --stand 1 --schema catalog --mark-applied 49 --reference-stand 5 --apply

Подключение к БД — как у migration_precheck.py: --dsn, либо DB_DSN_TEMPLATE /
DB_DSN_STAND_<N> с плейсхолдерами {stand} и {password}, пароль из DB_PASSWORD.
"""
import argparse, datetime, json, os, sys

STUB_TEXT = os.environ.get("STUB_TEXT", "QA stub: FK fix (осиротевшие строки)")

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


def dsn_for(stand, dsn):
    """DSN: явный --dsn, иначе DB_DSN_STAND_<N>, иначе DB_DSN_TEMPLATE; пароль из DB_PASSWORD."""
    if dsn:
        return dsn
    tpl = os.environ.get(f"DB_DSN_STAND_{stand}") or os.environ.get("DB_DSN_TEMPLATE")
    if not tpl:
        print("задай DB_DSN_TEMPLATE (или DB_DSN_STAND_<N>) либо передай --dsn")
        raise SystemExit(2)
    return tpl.format(stand=stand, password=os.environ.get("DB_PASSWORD", ""))


def columns_meta(cur, table):
    schema, name = table.split(".", 1)
    cur.execute("""select column_name, data_type, is_nullable, column_default
                   from information_schema.columns
                   where table_schema=%s and table_name=%s order by ordinal_position""", (schema, name))
    return cur.fetchall()


def missing_parent_keys(cur, con, ignore_ids=()):
    """Значения дочерней колонки, для которых нет родителя (то, что валит FK).

    ignore_ids — строки, которые мы собираемся удалить: их ключи заглушками
    закрывать не нужно. Считаем в SQL, а не по показанным precheck'ом строкам,
    иначе при большом числе нарушителей часть ключей потеряется и FK не встанет.
    """
    t, col = con["table"], con["cols"][0]
    parent, pcol = con["parent"], con["parent_cols"][0]
    cur.execute("""select data_type from information_schema.columns
                   where table_schema=%s and table_name=%s and column_name=%s""",
                (*t.split(".", 1), col))
    child_type = (cur.fetchone() or [None])[0]
    cur.execute("""select data_type from information_schema.columns
                   where table_schema=%s and table_name=%s and column_name=%s""",
                (*parent.split(".", 1), pcol))
    parent_type = (cur.fetchone() or [None])[0]
    cast = "::text" if child_type != parent_type else ""
    skip = ""
    params = ()
    if ignore_ids and "id" in {c[0] for c in columns_meta(cur, t)}:
        skip = " and id <> all(%s)"
        params = (list(ignore_ids),)
    cur.execute(f"""select distinct {col} from {t}
                    where {col} is not null{skip} and not exists
                      (select 1 from {parent} p where p.{pcol}{cast} = {t}.{col}{cast})""", params)
    return [r[0] for r in cur.fetchall()]


def stub_row_sql(cur, con, key, reference_cur):
    """INSERT родителя-заглушки: сначала пробуем скопировать строку с эталонного стенда."""
    parent, pcol = con["parent"], con["parent_cols"][0]
    meta = columns_meta(cur, parent)
    have = {c[0] for c in meta}

    if reference_cur is not None:
        reference_cur.execute(f"select row_to_json(t) from (select * from {parent} where {pcol} = %s) t", (key,))
        r = reference_cur.fetchone()
        if r:
            src = {k: v for k, v in r[0].items() if k in have and k != "id" and v is not None}
            cols = list(src)
            return (f"insert into {parent} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
                    [src[c] for c in cols], "скопирована с эталонного стенда")

    values = {pcol: key}
    for name, dtype, nullable, default in meta:
        if name == pcol or name == "id" or default is not None or nullable == "YES":
            continue
        if "char" in dtype or "text" in dtype:
            values[name] = STUB_TEXT
        elif "bool" in dtype:
            values[name] = False
        elif "int" in dtype or "numeric" in dtype or "double" in dtype:
            values[name] = 0
        elif "timestamp" in dtype or "date" in dtype:
            values[name] = datetime.datetime.now()
    for nice, val in (("name", STUB_TEXT), ("short_name", "QA stub"),
                      ("active", False), ("is_active", False),
                      ("created_by", os.environ.get("USER") or os.environ.get("USERNAME", "qa")), ("create_date", datetime.datetime.now())):
        if nice in have and nice not in values:
            values[nice] = val
    cols = list(values)
    return (f"insert into {parent} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
            [values[c] for c in cols], "собрана по NOT NULL-колонкам таблицы")


def fix_checksum(psycopg2, a):
    """Снять запись версии из flyway_schema_history — лечение checksum mismatch.

    Когда нужно: две ветки заняли один номер версии. На стенде применён вариант из одной,
    а образ мигратора несёт другой — Flyway отказывается стартовать с
    «Migration checksum mismatch for migration version N» и джоба деплоя падает.

    Чего это НЕ делает: объекты, созданные удалённой миграцией (колонки, индексы, данные),
    остаются в схеме, а записи о них в истории больше нет. Поэтому после удаления печатается
    предупреждение: убедиться, что миграция, которая придёт этой версией, идемпотентна
    (`add column if not exists`), иначе она упадёт с «already exists».
    """
    if not a.schema:
        print("нужен --schema (в какой схеме версия)"); return 1
    conn = psycopg2.connect(dsn_for(a.stand, a.dsn)); cur = conn.cursor()
    tbl = f"{a.schema}.flyway_schema_history"
    v3 = (a.checksum_version,) * 3
    cur.execute(f"select version, description, type, script, checksum, installed_on, installed_by, success "
                f"from {tbl} where {version_match()}", v3)
    rows = cur.fetchall()
    if not rows:
        print(f"в {tbl} нет записи версии {a.checksum_version} — чинить нечего "
              f"(значит mismatch в другой схеме или версия уже снята)")
        conn.close(); return 0
    print(f"запись в {tbl}:")
    for r in rows:
        print(f"  version={r[0]} description={r[1]!r} script={r[3]!r}")
        print(f"  checksum={r[4]} installed_on={r[5]} by={r[6]} success={r[7]}")
    print("")
    print(f"план: удалить эту запись → Flyway применит версию {a.checksum_version} из текущего образа заново")
    if not a.apply:
        print("это dry-run. Показать человеку, получить «да», затем повторить с --apply")
        conn.close(); return 0

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bpath = f"backup_flyway_{a.schema}_v{a.checksum_version}_{ts}.json"
    cur.execute(f"select row_to_json(t) from (select * from {tbl} where {version_match()}) t", v3)
    json.dump([r[0] for r in cur.fetchall()], open(bpath, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print(f"бэкап записи: {bpath}")
    try:
        cur.execute(f"delete from {tbl} where {version_match()}", v3)
        deleted = cur.rowcount
        cur.execute(f"select count(*) from {tbl} where {version_match()}", v3)
        if cur.fetchone()[0]:
            conn.rollback(); print("ROLLBACK: запись осталась"); return 1
        conn.commit()
        print(f"✅ удалено записей: {deleted}. Перезапустить деплой сервиса — джоба применит версию заново.")
        print("⚠️ Объекты, созданные удалённой миграцией, остались в схеме, а записи о них теперь нет.")
        print("   Проверить, что приходящая версия идемпотентна (add column if not exists / create ... if not exists),")
        print("   иначе она упадёт с «already exists». И сказать разработчику: номер версии занят в master —")
        print("   правильное решение не здесь, а перенумерация миграции в ветке задачи.")
        return 0
    except Exception as e:
        conn.rollback(); print(f"ROLLBACK: {type(e).__name__}: {e}"); return 1
    finally:
        conn.close()


def mark_applied(psycopg2, a):
    """Пометить версию как применённую, скопировав запись с эталонного стенда.

    Когда нужно: миграция падает с «already exists» (SQL State 42701) — объект в схеме есть,
    а записи в истории нет. Так бывает, если DDL накатили руками, если запись снимали для
    лечения checksum mismatch, или если миграция неидемпотентна (`ADD COLUMN` без
    `IF NOT EXISTS`) и её пытаются применить повторно.

    Почему копируем со стенда, а не пишем свою строку: Flyway при следующем запуске сверит
    checksum записи с файлом в образе. Придуманный checksum превратит одну проблему (already
    exists) в другую (checksum mismatch). На эталонном стенде, где миграция прошла штатно,
    checksum корректный — его и переносим.
    """
    if not a.schema or not a.reference_stand:
        print("нужны --schema и --reference-stand (стенд, где эта версия применена штатно)")
        return 1
    tbl = f"{a.schema}.flyway_schema_history"
    conn = psycopg2.connect(dsn_for(a.stand, a.dsn)); cur = conn.cursor()
    ref = psycopg2.connect(dsn_for(a.reference_stand, None)); rcur = ref.cursor()

    m3 = (a.mark_applied,) * 3
    cur.execute(f"select count(*) from {tbl} where {version_match()}", m3)
    if cur.fetchone()[0]:
        print(f"на стенде {a.stand} запись версии {a.mark_applied} уже есть — помечать нечего")
        return 0
    rcur.execute(f"select row_to_json(t) from (select * from {tbl} where {version_match()}) t", m3)
    row = rcur.fetchone()
    if not row:
        print(f"на эталонном стенде {a.reference_stand} версии {a.mark_applied} тоже нет — "
              f"копировать нечего; значит миграция нигде не проходила штатно")
        return 1
    src = row[0]
    cur.execute(f"select coalesce(max(installed_rank), 0) + 1 from {tbl}")
    rank = cur.fetchone()[0]
    src["installed_rank"] = rank
    cols = [k for k in src if src[k] is not None]
    print(f"скопировать в {tbl} запись версии {a.mark_applied} со стенда {a.reference_stand}:")
    print(f"  description={src.get('description')!r} script={src.get('script')!r}")
    print(f"  checksum={src.get('checksum')} → installed_rank={rank}")
    print("")
    print("ВНИМАНИЕ: это говорит Flyway «версия уже применена», но НЕ проверяет, что схема совпадает.")
    print("   Убедись, что объекты миграции в схеме действительно есть — иначе следующая миграция,")
    print("   рассчитывающая на них, упадёт уже по другой причине.")
    if not a.apply:
        print("")
        print("это dry-run. Показать человеку, получить «да», затем повторить с --apply")
        return 0
    try:
        cur.execute(f"insert into {tbl} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",
                    [src[c] for c in cols])
        cur.execute(f"select count(*) from {tbl} where {version_match()}", m3)
        if not cur.fetchone()[0]:
            conn.rollback(); print("ROLLBACK: запись не появилась"); return 1
        conn.commit()
        print(f"✅ версия {a.mark_applied} помечена применённой — перезапустить деплой сервиса")
        return 0
    except Exception as e:
        conn.rollback(); print(f"ROLLBACK: {type(e).__name__}: {e}"); return 1
    finally:
        conn.close(); ref.close()


def version_match(col="version"):
    """Условие сравнения версии: в истории лежит '049', а человек передаёт '49'.

    Сравниваем и как строку, и численно — иначе скрипт отвечает «версии нет»
    на существующую запись и вводит в заблуждение (поймано самопроверкой).
    """
    return (f"({col} = %s or ({col} ~ '^[0-9]+$' and %s ~ '^[0-9]+$' "
            f"and {col}::bigint = %s::bigint))")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--stand"); ap.add_argument("--dsn")
    ap.add_argument("--reference-stand", help="стенд, где миграция уже прошла — оттуда копируем заглушку")
    ap.add_argument("--stub-parents", action="store_true", help="заводить родителей-заглушки")
    ap.add_argument("--delete-test-data", action="store_true",
                    help="строки с признаками тест-мусора удалять вместо заглушки")
    ap.add_argument("--delete-ids", help="явный список id строк на удаление, через запятую")
    ap.add_argument("--checksum-version", help="починить checksum mismatch: снять запись этой версии "
                                               "из {schema}.flyway_schema_history (нужен --schema)")
    ap.add_argument("--schema", help="схема для --checksum-version/--mark-applied, напр. catalog")
    ap.add_argument("--mark-applied", help="пометить версию как уже применённую: скопировать запись "
                                           "истории со стенда из --reference-stand (нужен --schema)")
    ap.add_argument("--apply", action="store_true", help="без него — только показать план")
    a = ap.parse_args()

    import psycopg2
    if a.checksum_version:
        return fix_checksum(psycopg2, a)
    if a.mark_applied:
        return mark_applied(psycopg2, a)
    if not os.path.exists(a.plan):
        print(f"нет файла плана {a.plan} — сначала запусти migration_precheck.py "
              f"(он пишет plan.json только когда нашёл блокеры; если блокеров нет, чинить нечего)")
        return 1
    plan = json.load(open(a.plan, encoding="utf-8"))
    fks = [b for b in plan["blockers"] if b["constraint"]["kind"] == "fk"]
    other = [b for b in plan["blockers"] if b["constraint"]["kind"] != "fk"]
    for b in other:
        c = b["constraint"]
        print(f"⚠️ {b['migration']}: {c['kind']} {c['name']} на {c['table']} — руками, "
              f"выбор какую строку оставить автоматике не отдаём")
    if not fks:
        print("FK-блокеров нет — чинить нечем"); return 0

    conn = psycopg2.connect(dsn_for(a.stand, a.dsn)); cur = conn.cursor()
    ref_cur = None
    if a.reference_stand:
        ref_cur = psycopg2.connect(dsn_for(a.reference_stand, None)).cursor()

    explicit_ids = {int(x) for x in a.delete_ids.split(",")} if a.delete_ids else set()
    actions = []            # (описание, sql, params)
    backup = []

    for b in fks:
        con = b["constraint"]
        t = con["table"]
        # строки-нарушители с их признаками — из плана precheck
        rows_by_id = {}
        for h in b["rows"]:
            d = dict(zip(h["fields"], h["values"]))
            if "id" in d:
                rows_by_id[int(d["id"])] = (d, h["test_markers"])

        del_ids = {i for i, (d, marks) in rows_by_id.items()
                   if i in explicit_ids or (a.delete_test_data and marks)}
        for i in sorted(del_ids):
            d, marks = rows_by_id[i]
            cur.execute(f"select row_to_json(t) from (select * from {t} where id = %s) t", (i,))
            r = cur.fetchone()
            if r:
                backup.append({"table": t, "row": r[0]})
            actions.append((f"удалить {t} id={i} ({', '.join(marks) or 'по явному списку'})",
                            f"delete from {t} where id = %s", (i,)))

        if a.stub_parents:
            # ключи, которые останутся нарушителями ПОСЛЕ удалений — только их закрываем заглушкой
            for key in missing_parent_keys(cur, con, ignore_ids=del_ids):
                sql, params, how = stub_row_sql(cur, con, key, ref_cur)
                actions.append((f"завести {con['parent']}.{con['parent_cols'][0]}={key} — заглушка ({how})",
                                sql, params))

    if not actions:
        print("нечего делать: не указан ни --stub-parents, ни --delete-test-data/--delete-ids")
        return 1

    print(f"\nплан починки ({len(actions)} шагов):")
    for desc, sql, _ in actions:
        print(f"  • {desc}\n      {sql}")
    if not a.apply:
        print("\nэто dry-run. Показать человеку, получить «да», затем повторить с --apply")
        return 0

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bpath = f"backup_{plan['schema']}_{ts}.json"
    if backup:
        json.dump(backup, open(bpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print(f"\nбэкап удаляемых строк: {bpath}")

    try:
        for desc, sql, params in actions:
            cur.execute(sql, params)
            print(f"  ok: {desc}")
        # контроль тем же условием, что у precheck: не осталось ли нарушителей
        left = []
        for b in fks:
            con = b["constraint"]
            rest = missing_parent_keys(cur, con)
            if rest:
                left.append(f"{con['name']} на {con['table']} (ключи: {rest[:5]}…)"
                            if len(rest) > 5 else f"{con['name']} на {con['table']} (ключи: {rest})")
        if left:
            conn.rollback()
            print("ROLLBACK: нарушители остались → " + ", ".join(left))
            return 1
        conn.commit()
        print("\n✅ COMMIT: блокеры сняты, можно катить (релиз пересобирать не нужно)")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ROLLBACK: {type(e).__name__}: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
