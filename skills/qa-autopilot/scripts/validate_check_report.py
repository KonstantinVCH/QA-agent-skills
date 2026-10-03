#!/usr/bin/env python3
"""validate_check_report.py — SubagentStop-хук автопилота qa-autopilot: не выпускать qa-executor без карточки.

Из stdin берёт agent_type и agent_transcript_path, чтобы дописать в progress.md строку о возврате
субагента: роль, сколько минут работал, сколько вызвал инструментов и каких, сколько токенов, чем
ответил. Источник истины по фазе и диспатчу остаётся state.json.
Если активного прогона нет или текущий диспатч не qa-executor — exit 0.
Для каждой TC из state.current_dispatch.tcs требует checks/TC-N.md с обязательными
разделами и статусом. PASS/FAIL требуют traceId — либо строки «traceId: нет. <почему трейса
не существует>» для проверок без вызова сервиса (SELECT, конфигурация, management API).
В фазе CLEANUP требует CLEANUP.md.
Отказ = exit 2 + перечень недостающего в stderr (модель дописывает и пробует снова).
Лимит отказов на один диспатч — 3, дальше пропускаем (оркестратор увидит в леджере).
"""
import datetime
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ACTIVE_PTR = Path.home() / ".claude" / "qa-autopilot" / "active.json"
REQUIRED = ["## Исходное состояние", "## Запрос", "## Проверка результата", "## Трейс", "## Откат", "## Сравнение с ожидаемым"]
STATUSES = {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE", "NEEDS_HUMAN"}
BLOCK_CAP = 3


def read_event():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def agent_metrics(ev):
    """Вызовы инструментов и токены из личного transcript субагента.

    SubagentStop отдаёт `agent_transcript_path` — отдельный .jsonl на каждого субагента
    (…/<сессия>/subagents/agent-<id>.jsonl). Без него счётчики просто не печатаются.

    Один ответ модели лежит в журнале несколькими строками (text, tool_use, продолжение),
    и `usage` одного и того же запроса повторяется в каждой: input и cache совпадают,
    output растёт по мере генерации. Поэтому расход считается по уникальному `message.id`,
    последним значением на id. Наивная сумма по строкам завышала результат вдвое
    (замер на 25 транскриптах: 13.9M против 6.9M). Вызовы инструментов, наоборот,
    в строках не дублируются — они считаются как есть.
    """
    tools, per_message = Counter(), {}
    p = ev.get("agent_transcript_path")
    if not p:
        return tools, 0
    try:
        raw = Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return tools, 0
    for n, line in enumerate(raw.splitlines()):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        msg = o.get("message") or {}
        for part in (msg.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "tool_use":
                tools[part.get("name") or "?"] += 1
        u = msg.get("usage") or {}
        if u:
            per_message[msg.get("id") or f"строка-{n}"] = sum(
                int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens"))
    return tools, sum(per_message.values())


def bump_usage(run_dir, role, tokens, transcript):
    """Прибавить расход роли к state.usage и вернуть новое значение.

    Файл перечитывается прямо перед записью и пишется через временный файл с os.replace:
    состояние в памяти хука снято в начале его работы, а за это время оркестратор или хук
    второй вернувшейся роли успевают записать своё. Складывать поверх устаревшего снимка —
    терять и чужие токены, и чужие поля state.json.

    Считается **разница** по транскрипту, а не вся его сумма. Отказ хука (exit 2) роль не
    завершает: она дописывает карточку и сдаётся снова, а транскрипт к этому моменту содержит
    и всю прошлую работу. Без учёта уже посчитанного каждая пересдача удваивала бы расход
    прогона и накручивала счётчик возвратов.
    """
    sp = Path(run_dir) / "state.json"
    try:
        cur = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    u = cur.setdefault("usage", {"tokens": 0, "subagents": 0, "by_role": {}})
    key = str(transcript or f"{role}-без-транскрипта-{u.get('subagents', 0)}")
    counted = u.setdefault("by_transcript", {})
    delta = max(0, tokens - int(counted.get(key) or 0))
    first_return = key not in counted
    counted[key] = max(int(counted.get(key) or 0), tokens)
    u["tokens"] = int(u.get("tokens") or 0) + delta
    if first_return:
        u["subagents"] = int(u.get("subagents") or 0) + 1
    u.setdefault("by_role", {})[role] = int((u.get("by_role") or {}).get(role) or 0) + delta
    tmp = sp.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, sp)
    except OSError:
        pass
    return u


def log_return(run_dir, st, ev):
    """Строка в progress.md на возврате субагента: закрывает молчание между диспатчем и следующей фазой."""
    cd = st.get("current_dispatch") or {}
    role = ev.get("agent_type") or cd.get("role") or "субагент"
    parts = []
    started = cd.get("at")
    if started:
        try:
            mins = (datetime.datetime.now() - datetime.datetime.fromisoformat(started)).total_seconds() / 60
            if 0 <= mins < 60 * 24:
                parts.append(f"{mins:.0f} мин")
        except ValueError:
            pass
    tools, tokens = agent_metrics(ev)
    st["usage"] = bump_usage(run_dir, role, tokens, ev.get("agent_transcript_path"))
    if tools:
        top = ", ".join(f"{n} {c}" for n, c in tools.most_common(3))
        parts.append(f"инструментов {sum(tools.values())} ({top})")
    if tokens:
        parts.append(f"токенов {tokens / 1000:.1f}k")
    tail = (ev.get("last_assistant_message") or "").strip().replace("\n", " ")
    if tail:
        parts.append(f"«{tail[:90]}»")
    try:
        with open(Path(run_dir) / "progress.md", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M}] SUBAGENT {role} вернулся"
                    + (": " + " · ".join(parts) if parts else "") + "\n")
    except OSError:
        pass


def main():
    ev = read_event()
    # Только настоящий SubagentStop. На пилоте исполнитель запустил хук руками «проверить карточки»:
    # скрипт дописал в progress.md 14 ложных строк и накрутил current_dispatch.blocks до 7.
    if ev.get("hook_event_name") != "SubagentStop":
        print("validate_check_report.py — хук SubagentStop, вручную не запускается: карточку проверит "
              "сам хук при возврате роли; обязательные разделы — REQUIRED в этом файле", file=sys.stderr)
        sys.exit(0)
    if not ACTIVE_PTR.exists():
        sys.exit(0)
    try:
        run_dir = Path(json.loads(ACTIVE_PTR.read_text(encoding="utf-8"))["run_dir"])
        sp = run_dir / "state.json"
        st = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        sys.exit(0)
    log_return(run_dir, st, ev)
    cd = st.get("current_dispatch") or {}
    if cd.get("role") != "qa-executor":
        sys.exit(0)
    problems = []
    if st.get("phase") == "CLEANUP":
        c = run_dir / "CLEANUP.md"
        if not c.exists() or "|" not in c.read_text(encoding="utf-8"):
            problems.append("CLEANUP.md отсутствует или без таблицы фикстур (Фикстура | Исходное | Откат | SELECT чистоты | Результат)")
    for tc in cd.get("tcs", []):
        p = run_dir / "checks" / f"{tc}.md"
        if not p.exists():
            problems.append(f"{tc}: нет файла checks/{tc}.md")
            continue
        txt = p.read_text(encoding="utf-8")
        for h in REQUIRED:
            if h not in txt:
                problems.append(f"{tc}: нет раздела «{h}»")
        m = re.search(r"^status:\s*(\S+)", txt, re.M)
        status = m.group(1).upper() if m else None
        if status not in STATUSES:
            problems.append(f"{tc}: нет строки `status:` с одним из {sorted(STATUSES)}")
        if status in ("PASS", "FAIL"):
            t = re.search(r"traceId:\s*([0-9a-fA-F]{8,})", txt)
            # Проверка может не создавать спанов вовсе: чистый SELECT по БД, чтение конфигурации,
            # GET в RabbitMQ management. Требовать traceId там — загонять верный результат
            # в INCONCLUSIVE. Послабление платное: исполнитель обязан назвать причину строкой
            # `traceId: нет. <почему трейса не существует>` — молчаливого пропуска нет.
            no_trace = re.search(r"traceId:\s*нет[.,:\s—-]+(\S.{15,})", txt)
            if not t and not no_trace:
                problems.append(f"{tc}: статус {status} без traceId. Либо полный traceId, "
                                f"либо строка «traceId: нет. <почему трейса не существует>» — "
                                f"причина обязательна и не короче 15 символов")
            if not re.search(r"результат:\s*\S", txt):
                problems.append(f"{tc}: нет строк «результат:» с фактическими данными SELECT")
        if re.search(r"\.\.\.\s*$", txt, re.M) and "curl" in txt:
            problems.append(f"{tc}: запрос сокращён «...» — нужен полностью")
    if not problems:
        sys.exit(0)
    cd["blocks"] = int(cd.get("blocks", 0)) + 1
    st["current_dispatch"] = cd
    try:
        # то же правило, что в bump_usage: пишем поверх свежего файла, а не своего снимка
        fresh = json.loads(sp.read_text(encoding="utf-8"))
        fresh["current_dispatch"] = cd
        tmp = sp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, sp)
        with open(run_dir / "progress.md", "a", encoding="utf-8") as f:
            f.write(f"HOOK validate_check_report: {len(problems)} проблем (блок #{cd['blocks']})\n")
    except Exception:
        pass
    if cd["blocks"] > BLOCK_CAP:
        print(f"[validate_check_report] лимит {BLOCK_CAP} отказов исчерпан, пропускаю; проблемы: {problems}", file=sys.stderr)
        sys.exit(0)
    print("[qa-autopilot] Карточка проверки неполная, дописать перед завершением:\n- " + "\n- ".join(problems)
          + "\nШаблон: references/state-schema.md скилла qa-autopilot (раздел checks/TC-N.md).",
          file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
