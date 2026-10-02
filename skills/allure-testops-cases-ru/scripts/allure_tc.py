# -*- coding: utf-8 -*-
"""CLI-хелпер для мануальных тест-кейсов Allure TestOps через REST.

Инкапсулирует проверенные контракты и ловушки TestOps (см. SKILL.md и references/api-map.md).
Зависимость одна: requests (pip install requests).

Настройка — переменными окружения (см. references/project-config.md):
  ALLURE_URL                  базовый URL инстанса, напр. https://allure.example.com   (обязательно)
  ALLURE_TOKEN                API-токен: профиль → API tokens                           (обязательно, или --token)
  ALLURE_PROJECT_ID           id проекта в TestOps                                      (обязательно)
  ALLURE_TREE_ID              id дерева тест-кейсов (Feature → Story → Layer); для find, по умолчанию 0
  ALLURE_JIRA_INTEGRATION_ID  id интеграции с Jira в TestOps; для issue
  JIRA_URL                    базовый URL Jira, напр. https://jira.example.com; для issue
  ALLURE_DOC_LINK_MARKER      подстрока ссылки на документацию в описании ТК; для audit
                              (по умолчанию значение CONFLUENCE_URL, иначе "http")
  ALLURE_NAME_STOP_WORDS      стоп-слова в названии через запятую
                              (по умолчанию: позитивный,негативный,проверка)
  ALLURE_NAME_MAX_LEN         порог длины названия для audit (по умолчанию 120)
  ALLURE_STATUS_DRAFT_ID      id статуса Draft (по умолчанию -1)
  ALLURE_WORKFLOW_MANUAL_ID   id workflow Manual (по умолчанию 1)
  CHROME_PATH                 путь к Chrome/Chromium для screenshot (иначе ищется сам)

Примеры:
  python allure_tc.py find "/orders/{id}/status"                 # ШАГ 0: папка метода + дубли
  python allure_tc.py create --name "/orders/{id}/status - Получение статуса оплаченного заказа" \
                             --description "Тест-кейс позволяет проверить ..."
  python allure_tc.py precondition 1001 --text "1. Создан заказ ..."
  python allure_tc.py add-step --tc 1001 --body "Отправить запрос из вложения" \
                               --er "Ответ 200, status=PAID" --attach curl_order_status.txt
  python allure_tc.py shared-create --name "Авторизация тестового покупателя"
  python allure_tc.py insert-shared 1001 --shared 52 [--before 64017]
  python allure_tc.py cf 1001 --from-etalon 990                  # скопировать Feature/Story/Layer
  python allure_tc.py issue 1001 --key PROJ-123                  # REPLACE всех связей одной
  python allure_tc.py audit 1001                                 # проверка по конвенции
  python allure_tc.py show 1001                                  # дерево шагов человекочитаемо
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _env_int(name, default=None):
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    try:
        return int(v)
    except ValueError:
        sys.exit(f"{name}={v!r} — ожидается целое число")


BASE = (os.environ.get("ALLURE_URL") or "").rstrip("/")
PROJECT_ID = _env_int("ALLURE_PROJECT_ID")
TREE_ID = _env_int("ALLURE_TREE_ID", 0)
JIRA_INTEGRATION_ID = _env_int("ALLURE_JIRA_INTEGRATION_ID")
JIRA_URL = (os.environ.get("JIRA_URL") or "").rstrip("/")
STATUS_DRAFT_ID = _env_int("ALLURE_STATUS_DRAFT_ID", -1)
WORKFLOW_MANUAL_ID = _env_int("ALLURE_WORKFLOW_MANUAL_ID", 1)
NAME_MAX_LEN = _env_int("ALLURE_NAME_MAX_LEN", 120)
DOC_LINK_MARKER = (os.environ.get("ALLURE_DOC_LINK_MARKER")
                   or os.environ.get("CONFLUENCE_URL") or "http").rstrip("/")
STOP_WORDS = tuple(w.strip().lower() for w in (
    os.environ.get("ALLURE_NAME_STOP_WORDS") or "позитивный,негативный,проверка").split(",") if w.strip())


def need(value, name, why):
    if value in (None, ""):
        sys.exit(f"Не задана переменная {name} — нужна для {why}. См. references/project-config.md")
    return value


def hdr(token, ct_json=True):
    h = {"Authorization": f"Api-Token {token}"}
    if ct_json:
        h["Content-Type"] = "application/json"
    return h


def out(resp):
    try:
        print(json.dumps(resp.json(), ensure_ascii=False, indent=1)[:2000])
    except Exception:
        print(resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json() if resp.content else None


def cmd_create(a):
    # workflowId (Manual) и statusId (Draft) обязательны — иначе 400 "workflow and status are required"
    payload = {
        "projectId": need(PROJECT_ID, "ALLURE_PROJECT_ID", "создания ТК"),
        "name": a.name,
        "description": a.description or "",
        "automated": False,
        "statusId": STATUS_DRAFT_ID,
        "workflowId": WORKFLOW_MANUAL_ID,
    }
    out(requests.post(f"{BASE}/api/testcase", headers=hdr(a.token), json=payload, timeout=30))


def cmd_precondition(a):
    out(requests.patch(f"{BASE}/api/testcase/{a.tc}", headers=hdr(a.token),
                       json={"precondition": a.text}, timeout=30))


def cmd_description(a):
    out(requests.patch(f"{BASE}/api/testcase/{a.tc}", headers=hdr(a.token),
                       json={"description": a.text}, timeout=30))


def cmd_step(a):
    payload = {"testCaseId": a.tc, "body": a.body}
    if a.parent:
        payload["parentId"] = a.parent
    r = requests.post(f"{BASE}/api/testcase/step", headers=hdr(a.token), json=payload, timeout=30)
    r.raise_for_status()
    print(json.dumps({"createdStepId": r.json().get("createdStepId")}))


def _entity(a):
    """(базовый url шагов, ключ сущности, имя поля со словарём шагов, url дерева)."""
    if getattr(a, "shared", None):
        return (f"{BASE}/api/sharedstep/step", {"sharedStepId": a.shared},
                "sharedStepScenarioSteps", f"{BASE}/api/sharedstep/{a.shared}/step")
    if not getattr(a, "tc", None):
        sys.exit("Нужен --tc <id тест-кейса> или --shared <id общего шага>")
    return (f"{BASE}/api/testcase/step", {"testCaseId": a.tc},
            "scenarioSteps", f"{BASE}/api/testcase/{a.tc}/step")


def _tree(token, tree_url):
    d = requests.get(tree_url, headers=hdr(token), timeout=30).json()
    return d.get("sharedStepScenarioSteps") or d.get("scenarioSteps") or {}


def _write_er(a, base, key, skey, tree_url, step_id, step_body, er_text):
    """Перевести шаг в формат «с ожидаемым результатом» и записать текст ОР.

    Порядок и место записи важны:
    - PATCH ?withExpectedResult=true создаёт контейнер ОР (expectedResultId);
    - ответ PATCH иногда приходит БЕЗ expectedResultId, хотя контейнер создан, —
      достоверный источник id только дерево шагов;
    - текст ОР пишется ДОЧЕРНИМ узлом контейнера: текст в самом контейнере API отдаёт,
      а UI показывает пустое поле «Ожидаемый результат».
    """
    rr = requests.patch(f"{base}/{step_id}", headers=hdr(a.token), params={"withExpectedResult": "true"},
                        json={"body": step_body, "expectedResult": ""}, timeout=30)
    rr.raise_for_status()
    er_id = (rr.json().get(skey, {}).get(str(step_id)) or {}).get("expectedResultId")
    if not er_id:
        er_id = (_tree(a.token, tree_url).get(str(step_id)) or {}).get("expectedResultId")
    if not er_id:
        sys.exit("шаг не удалось перевести в формат с ОР — у него уже есть дети/вложение "
                 "(onlyonedetail): конвертировать можно только шаг без детей")
    p2 = dict(key)
    p2.update({"parentId": er_id, "body": er_text})
    requests.post(base, headers=hdr(a.token), json=p2, timeout=30).raise_for_status()
    nodes = _tree(a.token, tree_url)
    er_kids = nodes.get(str(er_id), {}).get("children") or []
    if not any((nodes.get(str(c), {}).get("body") or "").strip() for c in er_kids):
        sys.exit(f"ОР не записался (контейнер {er_id} пуст) — проверь шаг {step_id} вручную")
    return er_id


def cmd_er(a):
    """Сделать существующий шаг «шагом с ожидаемым результатом» и записать текст ОР."""
    base, key, skey, tree_url = _entity(a)
    er_id = _write_er(a, base, key, skey, tree_url, a.step, a.step_body, a.text)
    print(json.dumps({"stepId": a.step, "expectedResultId": er_id}, ensure_ascii=False))


def _find_chrome():
    cands = [os.environ.get("CHROME_PATH")]
    cands += [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium",
                                        "chromium-browser", "chrome")]
    cands += [r"C:/Program Files/Google/Chrome/Application/chrome.exe",
              r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def cmd_screenshot(a):
    """Снимок страницы headless-Chrome (или готовый PNG) → вложение → (опционально) привязка к шагу."""
    png = a.file
    if not png:
        if not a.url:
            sys.exit("Нужен --url страницы или --file с готовым PNG")
        chrome = _find_chrome()
        if not chrome:
            sys.exit("Chrome/Chromium не найден — задай CHROME_PATH или передай готовый файл через --file")
        png = os.path.join(tempfile.gettempdir(), f"{a.name}.png")
        subprocess.run([chrome, "--headless", "--disable-gpu", f"--window-size={a.size}",
                        f"--screenshot={png}", a.url, "--virtual-time-budget=9000"],
                       capture_output=True, timeout=120)
        if not os.path.exists(png):
            sys.exit("скриншот не создан — проверь URL/доступность страницы")
    if a.shared:
        url, params = f"{BASE}/api/sharedstep/attachment", {"sharedStepId": a.shared}
    else:
        if not a.tc:
            sys.exit("Нужен --tc или --shared")
        url, params = f"{BASE}/api/testcase/attachment", {"testCaseId": a.tc}
    with open(png, "rb") as f:
        r = requests.post(url, params=params, headers=hdr(a.token, ct_json=False),
                          files={"file": (a.name + ".png", f, "image/png")}, timeout=60)
    r.raise_for_status()
    d = r.json()
    att = (d[0] if isinstance(d, list) else d)["id"]
    result = {"attachmentId": att, "file": png}
    if a.parent:
        base, key, _, _ = _entity(a)
        payload = dict(key)
        payload.update({"parentId": a.parent, "attachmentId": att})
        requests.post(base, headers=hdr(a.token), json=payload, timeout=30).raise_for_status()
        result["attachedToStep"] = a.parent
    print(json.dumps(result, ensure_ascii=False))


def _upload(url_path, params, a):
    # имя файла — латиницей: кириллица в имени multipart-файла превращается в мусор
    name = a.name or os.path.basename(a.file)
    if any(ord(c) > 127 for c in name):
        print(f"WARN: не-ASCII в имени '{name}' побьётся — переименуй латиницей", file=sys.stderr)
    with open(a.file, "rb") as f:
        r = requests.post(f"{BASE}{url_path}", params=params, headers=hdr(a.token, ct_json=False),
                          files={"file": (name, f, "text/plain")}, timeout=60)
    r.raise_for_status()
    d = r.json()
    att = d[0] if isinstance(d, list) else d
    print(json.dumps({"attachmentId": att.get("id"), "name": att.get("name")}, ensure_ascii=False))


def cmd_attach(a):
    _upload("/api/testcase/attachment", {"testCaseId": a.tc}, a)


def cmd_attach_step(a):
    out(requests.post(f"{BASE}/api/testcase/step", headers=hdr(a.token),
                      json={"testCaseId": a.tc, "parentId": a.parent, "attachmentId": a.attachment}, timeout=30))


def cmd_shared_create(a):
    out(requests.post(f"{BASE}/api/sharedstep", headers=hdr(a.token),
                      json={"projectId": need(PROJECT_ID, "ALLURE_PROJECT_ID", "создания общего шага"),
                            "name": a.name}, timeout=30))


def cmd_shared_step(a):
    payload = {"sharedStepId": a.shared}
    if a.body:
        payload["body"] = a.body
    if a.parent:
        payload["parentId"] = a.parent
    if a.attachment:
        payload["attachmentId"] = a.attachment
    out(requests.post(f"{BASE}/api/sharedstep/step", headers=hdr(a.token), json=payload, timeout=30))


def cmd_shared_attach(a):
    _upload("/api/sharedstep/attachment", {"sharedStepId": a.shared}, a)


def cmd_insert_shared(a):
    r = requests.post(f"{BASE}/api/testcase/step", headers=hdr(a.token),
                      json={"testCaseId": a.tc, "sharedStepId": a.shared}, timeout=30)
    r.raise_for_status()
    created = r.json().get("createdStepId")
    if a.before:
        # {"index": N} отвечает 200, но порядок не меняет — работает только beforeId/afterId
        requests.post(f"{BASE}/api/testcase/step/{created}/move", headers=hdr(a.token),
                      json={"testCaseId": a.tc, "beforeId": a.before}, timeout=30).raise_for_status()
    print(json.dumps({"createdStepId": created, "movedBefore": a.before}))


def cmd_issue(a):
    # POST = REPLACE всех связей одним списком. Элементы без integrationId молча игнорируются.
    integ = need(JIRA_INTEGRATION_ID, "ALLURE_JIRA_INTEGRATION_ID", "связи с Jira")
    jira = need(JIRA_URL, "JIRA_URL", "ссылки на задачу")
    url = f"{jira}/browse/{a.key}"
    r = requests.post(f"{BASE}/api/testcase/{a.tc}/issue", headers=hdr(a.token),
                      json=[{"name": a.key, "url": url, "integrationId": integ}], timeout=30)
    r.raise_for_status()
    print(json.dumps({"linked": [i["name"] for i in r.json()]}, ensure_ascii=False))


def cmd_cf(a):
    src = requests.get(f"{BASE}/api/testcase/{a.from_etalon}/cfv", headers=hdr(a.token), timeout=30)
    src.raise_for_status()
    values = src.json()
    payload = [{"customField": {"id": v["customField"]["id"]}, "id": v["id"]} for v in values]
    r = requests.post(f"{BASE}/api/testcase/{a.tc}/cfv", headers=hdr(a.token), json=payload, timeout=30)
    r.raise_for_status()
    print(json.dumps({"copied": [(v["customField"]["name"], v["name"]) for v in values]}, ensure_ascii=False))


def cmd_show(a):
    tc = requests.get(f"{BASE}/api/testcase/{a.tc}", headers=hdr(a.token), timeout=30).json()
    print(f"[{a.tc}] {tc.get('name')}")
    print("PRECONDITION:", (tc.get("precondition") or "—")[:400])
    d = requests.get(f"{BASE}/api/testcase/{a.tc}/step", headers=hdr(a.token), timeout=30).json()
    steps, att = d.get("scenarioSteps", {}), d.get("attachments", {})

    def walk(ids, depth=0):
        for sid in ids:
            s = steps.get(str(sid), {})
            aid = s.get("attachmentId")
            label = s.get("body") or (
                f"[вложение: {att.get(str(aid), {}).get('name')}]" if aid
                else f"[shared: {s.get('sharedStepId')}]" if s.get("sharedStepId") else "?")
            print("  " * depth + f"- ({sid}) " + label.replace("\n", " ")[:110])
            walk(s.get("children", []), depth + 1)
            # вложения и текст ОР лежат внутри контейнера expectedResultId — без его обхода не видны
            er = s.get("expectedResultId")
            if er:
                print("  " * (depth + 1) + f"= ОР ({er})")
                walk(steps.get(str(er), {}).get("children", []), depth + 2)

    walk(d.get("root", {}).get("children", []))
    issues = requests.get(f"{BASE}/api/testcase/{a.tc}/issue", headers=hdr(a.token), timeout=30).json()
    print("ISSUES:", [i["name"] for i in issues][:5], f"(всего {len(issues)})")


def _tree_nodes(token, query, parent=0, path=None, tree=0):
    """Узлы дерева TestOps по подстроке имени. search — base64 от массива фильтров."""
    s = base64.b64encode(json.dumps(
        [{"id": "name", "value": query, "type": "string"}]).encode()).decode()
    p = {"projectId": need(PROJECT_ID, "ALLURE_PROJECT_ID", "поиска по дереву"), "search": s, "treeId": tree,
         "sort": ["nodeSortOrder,asc", "name,asc"], "deleted": "false",
         "parentNodeId": parent, "page": 0, "size": 100}
    if path:
        p["path"] = path
    r = requests.get(f"{BASE}/api/testcasetree/entity", headers=hdr(token), params=p, timeout=30)
    r.raise_for_status()
    return r.json().get("content", [])


def cmd_find(a):
    """ШАГ 0: где в дереве живёт метод и какие ТК на него уже есть.

    ⚠️ Параметр search фильтрует И ЛИСТЬЯ: внутри найденной группы видны только ТК,
    чьё имя содержит подстроку. Поэтому найденные группы раскрываются ПОВТОРНЫМ
    запросом с ПУСТЫМ search — иначе увидишь 1 кейс из 10 и заведёшь дубль."""
    if not a.tree:
        print("WARN: ALLURE_TREE_ID не задан (0) — укажи id дерева проекта (--tree или env)", file=sys.stderr)
    found_leaf = []
    groups = []

    def walk(parent, path, depth=0):
        for c in _tree_nodes(a.token, a.query, parent, path, a.tree):
            kind, nid, nm = c.get("type"), c.get("id"), c.get("name")
            print("  " * depth + f"[{kind}] {nid} {nm}")
            if kind == "GROUP" and depth < 4:
                sub = f"{path},{nid}" if path else str(nid)
                groups.append((nid, sub))
                walk(nid, sub, depth + 1)
            elif kind == "LEAF":
                found_leaf.append((nid, nm, path))

    walk(0, None)
    # Полное содержимое конечных групп — без фильтра по имени
    deepest = [g for g in groups if not any(p2 != g[1] and p2.startswith(g[1]) for _, p2 in groups)]
    for gid, gpath in deepest:
        full = _tree_nodes(a.token, "", gid, gpath, a.tree)
        seen = {i for i, _, _ in found_leaf}
        extra = [c for c in full if c.get("type") == "LEAF" and c["id"] not in seen]
        if extra:
            print(f"\n  ВСЕ ТК группы {gid} (search скрывал {len(extra)} шт.):")
            for c in full:
                if c.get("type") == "LEAF":
                    mark = "" if c["id"] in seen else "  ← был скрыт фильтром"
                    print(f"    {c['id']}: {c['name'][:80]}{mark}")
                    if c["id"] not in seen:
                        found_leaf.append((c["id"], c["name"], gpath))
    if not found_leaf:
        print("\nДУБЛЕЙ НЕТ — метод в дереве не найден, ТК будет первым. "
              "Родительскую папку и CF взять у соседнего метода того же сервиса.")
        return
    print(f"\nНАЙДЕНО ТК: {len(found_leaf)}. Прежде чем создавать новый — проверь, "
          f"не покрыт ли сценарий одним из них (обновить ОР дешевле, чем плодить кейс).")
    ref = found_leaf[0][0]
    cf = requests.get(f"{BASE}/api/testcase/{ref}/cfv", headers=hdr(a.token), timeout=30).json()
    print(f"CF референса {ref}: " + ", ".join(
        f"{c['customField']['name']}={c['name']!r}" for c in cf))
    print(f"Скопировать в новый ТК: allure_tc.py cf <новый> --from-etalon {ref}")


def cmd_delete_step(a):
    r = requests.delete(f"{BASE}/api/testcase/step/{a.step}", headers=hdr(a.token),
                        params={"testCaseId": a.tc}, timeout=30)
    r.raise_for_status()
    print(json.dumps({"deleted": a.step, "warn": "вложения шага и его детей удалены вместе с ним"},
                     ensure_ascii=False))


# ---------------------------------------------------------------- составные команды

# места действия: если в одном шаге упомянуты два разных — шаг составной
PLACES = {
    "БД": ("бд ", "базу", "базе", "select", "таблиц", "схем"),
    "трейсы": ("jaeger", "трейс"),
    "UI": ("экран", "браузер", "нажать", "страниц"),
    "API": ("curl", "запрос из вложения", "post ", "get ", "put "),
    "очередь": ("очеред", "rabbit", "kafka", "inject", "сообщение в"),
    "логи": ("opensearch", "kibana", "логи "),
}

# Обороты, которые НЕ означают отдельного места действия: указание, чем проверяют
# (SQL/curl во вложении). Без этого детектор давал ложные срабатывания:
# «Проверить в БД статус заказа — SQL во вложении» читался как «БД + API».
NOT_A_PLACE = (
    "запрос из вложения", "запросы из вложения", "sql из вложения", "sql во вложении",
    "curl из вложения", "во вложении", "из вложения",
)


def _split_hint(body):
    """Список мест действия, упомянутых в шаге.

    Учитывается только первое предложение: пояснения после точки описывают контекст,
    а не второе действие."""
    low = body.lower().split(". ")[0]
    for phrase in NOT_A_PLACE:
        low = low.replace(phrase, " ")
    return [name for name, markers in PLACES.items() if any(m in low for m in markers)]


def cmd_add_step(a):
    """Шаг + ожидаемый результат + вложения одним вызовом, в безопасном порядке.

    Порядок важен: тело → конвертация в шаг с ОР → текст ОР дочерним узлом → вложения.
    Обратный порядок затирает ОР, а текст в самом контейнере ОР не виден в UI.
    """
    base, key, skey, tree_url = _entity(a)
    payload = dict(key)
    payload["body"] = a.body
    if a.parent:
        payload["parentId"] = a.parent
    # TestOps не поддерживает блоки кода в теле шага (PATCH с codeBlock → 400, текст схлопывается
    # в один абзац). Длинные скрипты — вложением text/plain.
    if len(a.body) > 400 or any(m in a.body.upper() for m in ("SELECT ", "CURL ", "INSERT ", "UPDATE ")):
        print("WARN: в теле шага похоже на скрипт/SQL — оформи вложением (--attach), "
              "в теле оставь короткую инструкцию", file=sys.stderr)
    places = _split_hint(a.body)
    if len(places) > 1 or " и открыть " in a.body.lower() or " и проверить трейс" in a.body.lower():
        print(f"WARN: шаг составной — упомянуты разные места действия ({', '.join(places)}). "
              "Разбей: один шаг = одно действие в одном месте", file=sys.stderr)
    r = requests.post(base, headers=hdr(a.token), json=payload, timeout=30)
    r.raise_for_status()
    sid = r.json()["createdStepId"]
    out_data = {"stepId": sid}
    if a.er:
        out_data["expectedResultId"] = _write_er(a, base, key, skey, tree_url, sid, a.body, a.er)
    for path in (a.attach or []):
        name = os.path.splitext(os.path.basename(path))[0]
        if any(ord(c) > 127 for c in name):
            print(f"WARN: не-ASCII в имени '{name}' побьётся — переименуй файл латиницей", file=sys.stderr)
        mime = "image/png" if path.lower().endswith(".png") else "text/plain"
        url = (f"{BASE}/api/sharedstep/attachment" if getattr(a, "shared", None)
               else f"{BASE}/api/testcase/attachment")
        with open(path, "rb") as f:
            ra = requests.post(url, params=key, headers=hdr(a.token, ct_json=False),
                               files={"file": (name, f, mime)}, timeout=60)
        ra.raise_for_status()
        att = ra.json()
        att = att[0] if isinstance(att, list) else att
        p3 = dict(key)
        p3.update({"parentId": sid, "attachmentId": att["id"]})
        requests.post(base, headers=hdr(a.token), json=p3, timeout=30).raise_for_status()
        out_data.setdefault("attachments", []).append({"id": att["id"], "name": att.get("name")})
    print(json.dumps(out_data, ensure_ascii=False))


def cmd_audit(a):
    """Проверка кейса по конвенции (references/project-config.md). Печатает список замечаний."""
    tc = requests.get(f"{BASE}/api/testcase/{a.tc}", headers=hdr(a.token), timeout=30).json()
    steps = requests.get(f"{BASE}/api/testcase/{a.tc}/step", headers=hdr(a.token), timeout=30).json()
    issues = requests.get(f"{BASE}/api/testcase/{a.tc}/issue", headers=hdr(a.token), timeout=30).json()
    cfv = requests.get(f"{BASE}/api/testcase/{a.tc}/cfv", headers=hdr(a.token), timeout=30).json()
    st = steps.get("scenarioSteps", {})
    problems = []

    name = tc.get("name") or ""
    for w in STOP_WORDS:
        if w in name.lower():
            problems.append(f"название содержит стоп-слово «{w}»")
    # Порог по умолчанию 120: имя метода в начале названия само съедает до ~50 символов,
    # а порог 90 давал ложные замечания кейсам, оформленным ровно как соседние.
    if len(name) > NAME_MAX_LEN:
        problems.append(f"название длинное ({len(name)} симв.) — конвенция требует коротко и лаконично")
    if not name.strip():
        problems.append("нет названия")

    desc = tc.get("description") or ""
    if DOC_LINK_MARKER not in desc:
        problems.append(f"в описании нет ссылки на документацию (ищу «{DOC_LINK_MARKER}»)")
    if len(desc) < 40:
        problems.append("описание слишком короткое: нужна логика проверяемого метода")

    pre = tc.get("precondition") or ""
    if not pre.strip():
        problems.append("не заполнено предусловие")
    elif not any(line.strip()[:2].rstrip(".").isdigit() for line in pre.splitlines() if line.strip()):
        problems.append("шаги предусловия не пронумерованы")

    roots = steps.get("root", {}).get("children", [])
    if not roots:
        problems.append("в сценарии нет шагов")
    for i in roots:
        s = st.get(str(i), {})
        if s.get("sharedStepId"):
            continue
        body = (s.get("body") or "").strip()
        if not body:
            problems.append(f"шаг {i}: пустое тело")
            continue
        # ОР бывает в двух форматах: нативный (expectedResultId) и legacy — дочерний узел
        # с телом "Expected Result", внутри которого лежит текст. Оба считаем валидными.
        er = s.get("expectedResultId")
        texts = [st.get(str(c), {}).get("body") for c in st.get(str(er), {}).get("children", [])] if er else []
        if not any(texts):
            for c in s.get("children", []):
                cs = st.get(str(c), {})
                if (cs.get("body") or "").strip().lower() == "expected result":
                    texts = [st.get(str(g), {}).get("body") for g in cs.get("children", [])]
                    break
        # ОР может быть показан вложением (скриншот) — допустимо, но текст всё равно нужен
        er_children = st.get(str(er), {}).get("children", []) if er else []
        has_attach = any(st.get(str(c), {}).get("attachmentId") for c in er_children)
        joined = " ".join(t for t in texts if t)
        places = _split_hint(body)
        if len(places) > 1:
            problems.append(f"шаг «{body[:45]}…»: составной — действия в разных местах "
                            f"({', '.join(places)}); разбить на отдельные шаги")
        if len(body) > 400 or any(m in body.upper() for m in ("SELECT ", "CURL ", "INSERT ")):
            problems.append(f"шаг «{body[:45]}…»: скрипт/SQL в теле шага — вынести во вложение "
                            "(TestOps не умеет блоки кода, текст схлопывается в абзац)")
        if er and not er_children:
            problems.append(f"шаг «{body[:45]}…»: контейнер ОР пуст — текст ОР не записан, в UI поле пустое")
        if not joined and not has_attach:
            problems.append(f"шаг «{body[:45]}…»: нет ожидаемого результата")
        elif not joined and has_attach:
            problems.append(f"шаг «{body[:45]}…»: ОР только скриншотом — добавить текстом, что проверяется")
        elif len(joined) < 25:
            problems.append(f"шаг «{body[:45]}…»: ОР слишком общий — перечислить, что именно проверяется")

    if not issues:
        problems.append("нет связи с задачей Jira")
    if (tc.get("status") or {}).get("name") == "Draft":
        problems.append("статус Draft — после создания перевести в статус ревью (см. project-config.md)")
    if not cfv:
        problems.append("не заполнены custom fields — кейс не попадёт в дерево")

    if problems:
        print(f"Замечания по ТК {a.tc} ({len(problems)}):")
        for x in problems:
            print(" •", x)
    else:
        print(f"ТК {a.tc}: замечаний по конвенции нет")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--token", default=os.environ.get("ALLURE_TOKEN"), help="Api-Token (или env ALLURE_TOKEN)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("create"); s.add_argument("--name", required=True); s.add_argument("--description"); s.set_defaults(f=cmd_create)
    s = sub.add_parser("precondition"); s.add_argument("tc", type=int); s.add_argument("--text", required=True); s.set_defaults(f=cmd_precondition)
    s = sub.add_parser("description"); s.add_argument("tc", type=int); s.add_argument("--text", required=True); s.set_defaults(f=cmd_description)
    s = sub.add_parser("step"); s.add_argument("tc", type=int); s.add_argument("--body", required=True); s.add_argument("--parent", type=int); s.set_defaults(f=cmd_step)
    s = sub.add_parser("er", help="сделать шаг «с ожидаемым результатом» и записать ОР")
    s.add_argument("step", type=int); s.add_argument("--step-body", required=True, dest="step_body")
    s.add_argument("--text", required=True); s.add_argument("--tc", type=int); s.add_argument("--shared", type=int)
    s.set_defaults(f=cmd_er)
    s = sub.add_parser("screenshot", help="скрин страницы → вложение → шаг")
    s.add_argument("--url"); s.add_argument("--file"); s.add_argument("--name", required=True)
    s.add_argument("--tc", type=int); s.add_argument("--shared", type=int); s.add_argument("--parent", type=int)
    s.add_argument("--size", default="1280,900"); s.set_defaults(f=cmd_screenshot)
    s = sub.add_parser("attach"); s.add_argument("tc", type=int); s.add_argument("--file", required=True); s.add_argument("--name"); s.set_defaults(f=cmd_attach)
    s = sub.add_parser("attach-step"); s.add_argument("tc", type=int); s.add_argument("--parent", type=int, required=True); s.add_argument("--attachment", type=int, required=True); s.set_defaults(f=cmd_attach_step)
    s = sub.add_parser("shared-create"); s.add_argument("--name", required=True); s.set_defaults(f=cmd_shared_create)
    s = sub.add_parser("shared-step"); s.add_argument("shared", type=int); s.add_argument("--body"); s.add_argument("--parent", type=int); s.add_argument("--attachment", type=int); s.set_defaults(f=cmd_shared_step)
    s = sub.add_parser("shared-attach"); s.add_argument("shared", type=int); s.add_argument("--file", required=True); s.add_argument("--name"); s.set_defaults(f=cmd_shared_attach)
    s = sub.add_parser("insert-shared"); s.add_argument("tc", type=int); s.add_argument("--shared", type=int, required=True); s.add_argument("--before", type=int); s.set_defaults(f=cmd_insert_shared)
    s = sub.add_parser("issue"); s.add_argument("tc", type=int); s.add_argument("--key", required=True); s.set_defaults(f=cmd_issue)
    s = sub.add_parser("cf"); s.add_argument("tc", type=int); s.add_argument("--from-etalon", type=int, required=True, dest="from_etalon"); s.set_defaults(f=cmd_cf)
    s = sub.add_parser("add-step", help="шаг + ОР + вложения одной командой (безопасный порядок)")
    s.add_argument("--body", required=True); s.add_argument("--er")
    s.add_argument("--attach", action="append", help="путь к файлу; можно повторять")
    s.add_argument("--tc", type=int); s.add_argument("--shared", type=int); s.add_argument("--parent", type=int)
    s.set_defaults(f=cmd_add_step)
    s = sub.add_parser("audit", help="проверка ТК по конвенции команды")
    s.add_argument("tc", type=int); s.set_defaults(f=cmd_audit)
    s = sub.add_parser("find", help="ШАГ 0: найти папку метода и существующие ТК (дубли)")
    s.add_argument("query"); s.add_argument("--tree", type=int, default=TREE_ID)
    s.set_defaults(f=cmd_find)
    s = sub.add_parser("show"); s.add_argument("tc", type=int); s.set_defaults(f=cmd_show)
    s = sub.add_parser("delete-step"); s.add_argument("step", type=int); s.add_argument("--tc", type=int, required=True); s.set_defaults(f=cmd_delete_step)

    a = p.parse_args()
    need(BASE, "ALLURE_URL", "любой команды")
    if not a.token:
        sys.exit("Нет токена: env ALLURE_TOKEN или --token ({ALLURE_URL} → профиль → API tokens)")
    a.f(a)


if __name__ == "__main__":
    main()
