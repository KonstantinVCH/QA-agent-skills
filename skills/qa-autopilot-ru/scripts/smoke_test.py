# -*- coding: utf-8 -*-
"""Дымовой тест автопилота: состояние, учёт токенов, лимит прогона, гарды, сторож, Stop-хук.

Запуск: `python scripts/smoke_test.py` из каталога скилла. Только stdlib, сеть не нужна.
Прогон идёт во временном каталоге с подменённым домашним каталогом, поэтому боевой указатель
`~/.claude/qa-autopilot/active.json` не трогается. Переменные окружения настройки
(QA_AUTOPILOT_*) тест задаёт сам, свои не подмешивает.

Каждая проверка закрывает дефект, который уже случался или был найден на ревью:
  1. стенд вне списка и запрещённое окружение не заводят прогон;
  2. `--budget-mtok` доходит до диспатча, а не лежит в state.json мёртвым флагом;
  3. `grant --scope budget-mtok` поднимает лимит, и работа продолжается;
  4. хук складывает токены поверх свежего файла, а не поверх своего снимка;
  5. один ответ модели считается один раз, даже когда лежит в журнале тремя строками;
  6. пересдача роли после отказа хука не удваивает расход прогона;
  7. `grant --scope test-branch` разрешает ветку задачи в инфраструктурном репозитории — и только это;
  8. деплой в CLEANUP — только после разрешения человека; запрещённое окружение — никогда;
  9. сторож видит молчащую роль, Stop-хук держит свою сессию и отпускает чужую.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
AP = SCRIPTS / "autopilot.py"
VC = SCRIPTS / "validate_check_report.py"
GUARD = SCRIPTS / "guard_external_writes.py"
failures = []

BB = "https://bitbucket.example.com/rest/api/latest/projects/SHOP/repos"
BAMBOO = "https://bamboo.example.com/rest/api/latest/queue/deployment"


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  ПАДЕНИЕ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def run(env, args, stdin=None):
    r = subprocess.run([sys.executable, *args], input=stdin, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def state_of(run_dir):
    return json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8"))


def put_state(run_dir, **fields):
    st = state_of(run_dir)
    st.update(fields)
    Path(run_dir, "state.json").write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def transcript(path, messages):
    """Журнал субагента. messages — список (id, [имена инструментов], токены)."""
    lines = []
    for mid, tools, tokens in messages:
        lines.append(json.dumps({"message": {
            "id": mid,
            "content": [{"type": "tool_use", "name": t} for t in tools],
            "usage": {"input_tokens": tokens, "output_tokens": 0}}}, ensure_ascii=False))
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def stop_event(tr, role):
    return json.dumps({"hook_event_name": "SubagentStop", "agent_type": role,
                       "agent_transcript_path": tr, "session_id": "smoke",
                       "last_assistant_message": f"{role} отработал"}, ensure_ascii=False)


def guard(env, cmd, tool="Bash"):
    """Гард как хук PreToolUse: 0 — пропустил, 2 — запретил."""
    inp = {"command": cmd} if tool in ("Bash", "PowerShell") else cmd
    ev = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp}, ensure_ascii=False)
    rc, _, _ = run(env, [str(GUARD)], stdin=ev)
    return rc


def make_env(home):
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("QA_AUTOPILOT_")}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                "USERPROFILE": str(home), "HOME": str(home), "CLAUDE_CODE_SESSION_ID": "smoke",
                "QA_AUTOPILOT_STANDS": "test-1,test-2,test-3",
                "QA_AUTOPILOT_BRANCH_REPOS": "infra-config"})
    return env


def test_init_rules(env, tmp):
    rc, _, err = run(env, [str(AP), "init", "--task", "PROJ-1", "--stand", "test-9", "--root", str(tmp / "x")])
    check("стенд вне QA_AUTOPILOT_STANDS не заводит прогон", rc == 1 and "не входит" in err, err[:160])
    rc, _, err = run({**env, "QA_AUTOPILOT_STANDS": "test-1,production"},
                     [str(AP), "init", "--task", "PROJ-1", "--stand", "production", "--root", str(tmp / "x")])
    check("запрещённое окружение не заводит прогон даже из списка", rc == 1 and "запрещённое" in err, err[:160])
    rc, _, err = run({**env, "QA_AUTOPILOT_STANDS": ""},
                     [str(AP), "init", "--task", "PROJ-1", "--stand", "test-1", "--root", str(tmp / "x")])
    check("без QA_AUTOPILOT_STANDS автопилот не стартует", rc == 1 and "QA_AUTOPILOT_STANDS" in err, err[:160])


def test_budget(env, tmp, run_dir):
    put_state(run_dir, current_dispatch={"role": "qa-analyst", "tcs": [], "at": state_of(run_dir)["started"], "blocks": 0})

    tr1 = transcript(tmp / "agent-1.jsonl", [("msg_a", ["Bash"], 60000)])
    rc, _, err = run(env, [str(VC)], stdin=stop_event(tr1, "qa-analyst"))
    check("хук пропускает роль, которая не qa-executor", rc == 0, err)
    check("токены роли попали в state.usage", state_of(run_dir).get("usage", {}).get("tokens") == 60000,
          str(state_of(run_dir).get("usage")))

    tr2 = transcript(tmp / "agent-2.jsonl", [("msg_b", ["Read"], 55000)])
    run(env, [str(VC)], stdin=stop_event(tr2, "qa-plan-reviewer"))
    u = state_of(run_dir).get("usage", {})
    check("второй возврат прибавляется, а не затирает", u.get("tokens") == 115000 and u.get("subagents") == 2, str(u))
    check("расход разложен по ролям", u.get("by_role") == {"qa-analyst": 60000, "qa-plan-reviewer": 55000}, str(u))

    # один ответ модели лежит в журнале тремя строками с общим message.id и тем же usage
    tr3 = transcript(tmp / "agent-3.jsonl",
                     [("msg_c", ["Bash"], 40000), ("msg_c", ["Grep"], 40000), ("msg_c", [], 40000)])
    run(env, [str(VC)], stdin=stop_event(tr3, "qa-executor-2"))
    u = state_of(run_dir).get("usage", {})
    check("повторы message.id считаются один раз", u.get("tokens") == 155000, f"ожидалось 155000, в файле {u.get('tokens')}")

    # пересдача после отказа хука: транскрипт дописан, но прошлое уже посчитано
    transcript(tr3, [("msg_c", ["Bash"], 40000), ("msg_c", ["Grep"], 40000), ("msg_d", ["Bash"], 5000)])
    run(env, [str(VC)], stdin=stop_event(tr3, "qa-executor-2"))
    u = state_of(run_dir).get("usage", {})
    check("пересдача роли добавляет только разницу", u.get("tokens") == 160000, f"в файле {u.get('tokens')}")
    check("пересдача не накручивает счётчик возвратов", u.get("subagents") == 3, str(u.get("subagents")))

    rc, out, _ = run(env, [str(AP), "status", "--run-dir", run_dir])
    check("status печатает расход с лимитом", "расход: токенов 160.0k из 0.1M" in out, out[-200:])
    check("денег в строке нет, пока не задана ставка", "$" not in out, out[-200:])
    _, out2, _ = run({**env, "QA_AUTOPILOT_USD_PER_MTOK": "10"}, [str(AP), "status", "--run-dir", run_dir])
    check("со своей ставкой появляется оценка в валюте", "~ $1.6" in out2, out2[-200:])

    # роль здесь любая, кроме qa-executor: его отдельно не пускает проверка фазы, и до бюджета не дойдёт
    rc, _, err = run(env, [str(AP), "dispatch", "--role", "qa-analyst", "--run-dir", run_dir])
    check("диспатч останавливается на исчерпанном лимите", rc == 1 and "лимит токенов" in err, err[:160])

    rc, _, err = run(env, [str(AP), "grant", "--scope", "budget-mtok", "--value", "8",
                           "--quote", "тест поднимает лимит", "--run-dir", run_dir])
    check("grant поднимает лимит", rc == 0 and state_of(run_dir)["flags"]["budget_mtok"] == 8.0, err)
    rc, _, err = run(env, [str(AP), "dispatch", "--role", "qa-analyst", "--run-dir", run_dir])
    check("после поднятия лимита диспатч проходит", rc == 0, err[:160])
    rc, out, err = run(env, [str(AP), "timeline", "--run-dir", run_dir])
    check("timeline показывает расход", rc == 0 and "расход: токенов" in out, err[:160])


def test_guard(env, run_dir):
    put_state(run_dir, phase="ENV", grants=[{"scope": "test-branch", "value": "PROJ-9999", "quote": "заводи ветку"}])
    cases_env = [
        ("ветка задачи в разрешённом репозитории создаётся", 0,
         f'curl -X POST {BB}/infra-config/branches -d \'{{"name":"PROJ-9999","startPoint":"refs/heads/master"}}\''),
        ("ветка с суффиксом тоже", 0, f'curl -X POST {BB}/infra-config/branches -d \'{{"name":"PROJ-9999-mock"}}\''),
        ("ветка в репозитории сервиса запрещена", 2,
         f'curl -X POST {BB}/order-service/branches -d \'{{"name":"PROJ-9999"}}\''),
        ("удаление ветки запрещено", 2, f'curl -X DELETE {BB}/infra-config/branches -d \'{{"name":"PROJ-9999"}}\''),
        ("чужая ветка с номером в теле запрещена", 2,
         f'curl -X POST {BB}/infra-config/branches -d \'{{"name":"master","message":"PROJ-9999"}}\''),
        ("PR через /rest/api/latest запрещён", 2,
         f'curl -X POST {BB}/infra-config/pull-requests -d \'{{"title":"PROJ-9999"}}\''),
        ("коммит файла в разрешённую ветку проходит", 0,
         f'curl -X PUT {BB}/infra-config/browse/roles/vars.yml -F content=@v.yml -F branch=PROJ-9999 -F message=fix'),
        ("коммит файла в master запрещён", 2,
         f'curl -X PUT {BB}/infra-config/browse/roles/vars.yml -F content=@v.yml -F branch=master -F message=fix'),
        ("деплой в ENV на разрешённый стенд проходит", 0,
         f'curl -X POST {BAMBOO} -d "environmentId=42&test-3"'),
        ("деплой без имени стенда запрещён", 2, f'curl -X POST {BAMBOO} -d "versionId=7"'),
        ("деплой рядом с production запрещён", 2,
         f'curl -X POST {BAMBOO} -d "environmentId=42&test-3&production"'),
        ("git push запрещён", 2, "git push origin PROJ-9999"),
        ("чтение истории git merge-base не режется", 0, "git merge-base origin/master HEAD"),
        ("сброс общего WireMock запрещён", 2, "curl -X POST https://wiremock.example.com:8080/__admin/reset"),
        ("UPDATE без WHERE через psql запрещён", 2, 'psql "$DSN" -c "UPDATE orders SET status=1;"'),
        ("комментарий в Jira до APPROVAL запрещён", 2,
         'curl -X POST https://jira.example.com/rest/api/2/issue/PROJ-9999/comment -d \'{"body":"x"}\''),
        ("GET в Jira разрешён", 0, "curl -s https://jira.example.com/rest/api/2/issue/PROJ-9999"),
    ]
    for name, expected, cmd in cases_env:
        rc = guard(env, cmd)
        check(name, rc == expected, f"гард вернул {rc}, ожидалось {expected}")

    rc = guard(env, {"issue_key": "PROJ-9999", "comment": "x"}, tool="mcp__atlassian__jira_add_comment")
    check("MCP-комментарий в Jira до APPROVAL запрещён", rc == 2, f"гард вернул {rc}")
    rc = guard(env, {"sql": "select 1"}, tool="mcp__postgres-test__query")
    check("SELECT через MCP БД разрешён", rc == 0, f"гард вернул {rc}")
    rc = guard(env, {"sql": "DELETE FROM orders;"}, tool="mcp__postgres-test__query")
    check("DELETE без WHERE через MCP БД запрещён", rc == 2, f"гард вернул {rc}")

    put_state(run_dir, phase="CLEANUP")
    deploy = f'curl -X POST {BAMBOO} -d "environmentId=42&test-3&order-service"'
    rc = guard(env, deploy)
    check("откат релиза в CLEANUP без разрешения запрещён", rc == 2, f"гард вернул {rc}")
    put_state(run_dir, grants=[{"scope": "cleanup-deploy", "value": "order-service", "quote": "катите обратно"}])
    rc = guard(env, deploy)
    check("откат релиза в CLEANUP после разрешения проходит", rc == 0, f"гард вернул {rc}")


def test_watchdog_and_stop(env, run_dir):
    put_state(run_dir, phase="EXECUTE",
              current_dispatch={"role": "qa-executor", "tcs": ["TC-1"], "at": state_of(run_dir)["started"], "blocks": 0})
    rc, out, _ = run(env, [str(AP), "stall", "--minutes", "999", "--run-dir", run_dir])
    check("сторож молчит, пока роль укладывается в порог", rc == 0 and out.startswith("ok"), out[:120])
    rc, out, _ = run(env, [str(AP), "stall", "--minutes", "0", "--run-dir", run_dir])
    check("сторож видит молчащую роль (exit 4)", rc == 4 and "STALL" in out, f"rc={rc} {out[:120]}")
    check("сторож отделяет отказ классификатора от ошибки", "повторять нельзя" in out, out[-200:])

    stop_in = json.dumps({"hook_event_name": "Stop", "session_id": "smoke"})
    rc, _, err = run(env, [str(AP), "check-stop"], stdin=stop_in)
    check("Stop-хук держит свою сессию в нетерминальной фазе", rc == 2 and "прогон не завершён" in err, err[:120])
    rc, _, err = run(env, [str(AP), "check-stop"], stdin=json.dumps({"hook_event_name": "Stop", "session_id": "чужая"}))
    check("Stop-хук отпускает чужую сессию", rc == 0, err[:120])


def main():
    tmp = Path(tempfile.mkdtemp(prefix="autopilot-smoke-"))
    try:
        env = make_env(tmp / "home")
        print("Запуск прогона:")
        test_init_rules(env, tmp)
        rc, run_dir, err = run(env, [str(AP), "init", "--task", "PROJ-9999", "--stand", "test-3",
                                     "--root", str(tmp / "runs"), "--budget-mtok", "0.1"])
        check("init создаёт прогон", rc == 0 and Path(run_dir, "state.json").exists(), err)
        if rc != 0:
            print("дальше без прогона проверять нечего")
            sys.exit(1)
        print("Бюджет и учёт токенов:")
        test_budget(env, tmp, run_dir)
        print("Гарды:")
        test_guard(env, run_dir)
        print("Сторож и Stop-хук:")
        test_watchdog_and_stop(env, run_dir)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\nПРОВАЛЕНО {len(failures)}: " + ", ".join(failures))
        sys.exit(1)
    print("\nвсе проверки прошли")


if __name__ == "__main__":
    main()
