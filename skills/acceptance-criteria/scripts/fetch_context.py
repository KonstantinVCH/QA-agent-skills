#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_context.py — собрать контекст задачи Jira для составления Acceptance Criteria.

Одной командой достаёт то, что иначе собирается четырьмя-пятью curl-ами вручную:
  - описание, комментарии, поле «Как проверить» (если задан JIRA_HOWTO_FIELD), метки, тип задачи;
  - страницы Confluence, на которые ссылаются ОПИСАНИЕ И КОММЕНТАРИИ (pageId=, /pages/ID,
    /display/SPACE/Title);
  - git diff всех PR задачи (Jira dev-status -> Bitbucket Server), с фильтром шума и лимитами.

Почему скриптом: сбор — детерминированная рутина (разбор ссылок, усечение диффа). Скрипт делает
её одинаково и быстро; внимание нужно на СОСТАВЛЕНИЕ критериев, а не на REST.

Использование:
  python3 fetch_context.py PROJ-123            # человекочитаемый контекст в stdout
  python3 fetch_context.py PROJ-123 --json     # JSON для программной обработки
  python3 fetch_context.py PROJ-123 --no-diff  # без git diff (быстрее)

Переменные окружения (секреты НЕ хардкодить):
  JIRA_URL           (обязателен)  — например https://jira.example.com
  JIRA_TOKEN         (обязателен)  — Personal Access Token Jira Server/Data Center
  CONFLUENCE_URL     (опционален)  — например https://wiki.example.com
  CONFLUENCE_TOKEN   (опционален)  — PAT Confluence; без него страницы не грузятся
  BITBUCKET_URL      (опционален)  — например https://git.example.com
  BITBUCKET_TOKEN    (опционален)  — HTTP access token Bitbucket Server; без него diff не грузится
  JIRA_HOWTO_FIELD   (опционален)  — id кастомного поля «Как проверить», например customfield_10006

Авторизация — заголовок «Authorization: Bearer <token>» (Jira/Confluence/Bitbucket Server и
Data Center). Jira Cloud этим скриптом не поддерживается.
"""
import argparse, json, os, re, sys, urllib.request, urllib.parse, urllib.error

MAX_CONF_PAGES = 5
MAX_CONF_TOTAL = 14000
MAX_PRS = 6
MAX_DIFF_TOTAL = 15000
MAX_DIFF_PER_FILE = 3500

SKIP_FILE = [re.compile(p, re.I) for p in [
    r"(^|/)src/test/", r"(^|/)tests?/", r"Test\w*\.java$", r"(^|/)pom\.xml$",
    r"\.(lock|md|pptx|png|jpe?g|svg|gif)$", r"(^|/)(target|build|node_modules|generated)/"]]


def _env_url(name):
    v = (os.environ.get(name) or "").strip().rstrip("/")
    return v or None


def _get(url, token, is_json=True, timeout=30):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read().decode("utf-8", "replace")
    return json.loads(data) if is_json else data


def _strip_html(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def fetch(issue_key, want_diff=True):
    jira_url = _env_url("JIRA_URL")
    jira = os.environ.get("JIRA_TOKEN")
    if not jira_url or not jira:
        sys.exit("ОШИБКА: задай JIRA_URL и JIRA_TOKEN (export JIRA_URL=https://jira.example.com; "
                 "export JIRA_TOKEN=<PAT из профиля Jira>)")
    wiki_url = _env_url("CONFLUENCE_URL")
    conf = os.environ.get("CONFLUENCE_TOKEN")
    git_url = _env_url("BITBUCKET_URL")
    bb = os.environ.get("BITBUCKET_TOKEN")
    howto_field = (os.environ.get("JIRA_HOWTO_FIELD") or "").strip()

    fields = "summary,description,comment,labels,issuetype" + (f",{howto_field}" if howto_field else "")
    issue = _get(f"{jira_url}/rest/api/2/issue/{issue_key}?fields={fields}", jira)
    f = issue["fields"]
    description = f.get("description") or ""
    how = str(f.get(howto_field) or "") if howto_field else ""
    comments = "\n".join(
        f"[{(c.get('author') or {}).get('displayName', '?')}]: {str(c.get('body', ''))[:800]}"
        for c in ((f.get("comment") or {}).get("comments") or []))
    src = description + "\n" + comments

    # --- Confluence: ссылки из описания и комментариев ---
    conf_pages, conf_links, warn = [], [], []
    if conf and wiki_url:
        host = re.escape(urllib.parse.urlparse(wiki_url).netloc)
        page_ids = []
        for m in re.finditer(host + r"/(?:.*?pageId=(\d+)|.*?/(?:pages|content)/(\d+))", src, re.I):
            pid = m.group(1) or m.group(2)
            if pid and pid not in page_ids:
                page_ids.append(pid)
        for m in re.finditer(host + r"/display/([^/\s?#\"'|\]]+)/([^\s?#\"'|\]]+)", src, re.I):
            space = urllib.parse.unquote(m.group(1))
            title = urllib.parse.unquote(m.group(2).replace("+", " "))
            try:
                sr = _get(f"{wiki_url}/rest/api/content?spaceKey={urllib.parse.quote(space)}"
                          f"&title={urllib.parse.quote(title)}&limit=1", conf, timeout=15)
                pid = ((sr.get("results") or [{}])[0]).get("id")
                if pid and pid not in page_ids:
                    page_ids.append(pid)
            except Exception:
                pass
        total = 0
        for pid in page_ids[:MAX_CONF_PAGES]:
            if total >= MAX_CONF_TOTAL:
                break
            try:
                pg = _get(f"{wiki_url}/rest/api/content/{pid}?expand=body.storage", conf)
                text = _strip_html(((pg.get("body") or {}).get("storage") or {}).get("value", ""))[:5000]
                url = f"{wiki_url}/pages/viewpage.action?pageId={pid}"
                conf_links.append({"title": pg.get("title", pid), "url": url})
                conf_pages.append(f'### Confluence "{pg.get("title", pid)}" ({url})\n{text}')
                total += len(text)
            except Exception as e:
                conf_pages.append(f"### Confluence {pid}: не загружена ({e})")
    else:
        warn.append("CONFLUENCE_URL/CONFLUENCE_TOKEN не заданы — страницы документации пропущены")

    # --- git diff PR задачи ---
    prs, pr_links, diffs = [], [], []
    if want_diff and bb and git_url:
        try:
            detail = _get(f"{jira_url}/rest/dev-status/1.0/issue/detail?issueId={issue['id']}"
                          "&applicationType=stash&dataType=pullrequest", jira)
            pr_list = []
            for det in detail.get("detail", []):
                for pr in det.get("pullRequests", []):
                    if str(pr.get("status", "")).upper() not in ("MERGED", "OPEN"):
                        continue
                    mm = re.search(r"/projects/([^/]+)/repos/([^/]+)/pull-requests/(\d+)", pr.get("url", ""))
                    if mm:
                        pr_list.append((mm.group(1), mm.group(2), mm.group(3), str(pr.get("name", "")), pr["status"]))
            total = 0
            for proj, repo, pid, name, state in pr_list[:MAX_PRS]:
                prs.append(f"{repo}#{pid} [{state}] {name}")
                pr_links.append({"title": f"{repo}#{pid}: {name}",
                                 "url": f"{git_url}/projects/{proj}/repos/{repo}/pull-requests/{pid}"})
                if total >= MAX_DIFF_TOTAL:
                    continue
                try:
                    raw = _get(f"{git_url}/rest/api/1.0/projects/{proj}/repos/{repo}/pull-requests/{pid}.diff"
                               "?contextLines=2", bb, is_json=False)
                    for block in re.split(r"^diff --git ", raw, flags=re.M):
                        if not block.strip():
                            continue
                        pm = re.search(r"dst://(\S+)", block)
                        path = pm.group(1) if pm else "(unknown)"
                        if any(s.search(path) for s in SKIP_FILE) or total >= MAX_DIFF_TOTAL:
                            continue
                        chunk = ("diff --git " + block)[:MAX_DIFF_PER_FILE]
                        diffs.append(f"### {repo}: {path}\n{chunk}")
                        total += len(chunk)
                except Exception:
                    pass
        except Exception as e:
            warn.append(f"dev-status/diff недоступен: {e}")
    elif want_diff:
        warn.append("BITBUCKET_URL/BITBUCKET_TOKEN не заданы — git diff пропущен")

    return {
        "issueKey": issue_key, "issueId": issue["id"], "summary": f.get("summary", ""),
        "issueUrl": f"{jira_url}/browse/{issue_key}", "issueType": (f.get("issuetype") or {}).get("name", ""),
        "labels": f.get("labels", []), "description": description, "howToCheck": how, "comments": comments,
        "confluence": conf_pages, "confluenceLinks": conf_links,
        "prs": prs, "prLinks": pr_links, "diff": diffs, "warnings": warn,
    }


def render(ctx):
    L = []
    L.append(f"# {ctx['issueKey']} — {ctx['summary']}")
    L.append(f"Тип: {ctx['issueType']} | Метки: {', '.join(ctx['labels']) or '—'} | {ctx['issueUrl']}")
    if ctx["warnings"]:
        L.append("\n[!] " + "; ".join(ctx["warnings"]))
    L.append("\n## Описание\n" + (ctx["description"] or "(нет)"))
    if ctx["howToCheck"]:
        L.append("\n## Как проверить (кастомное поле)\n" + ctx["howToCheck"])
    if ctx["comments"]:
        L.append("\n## Комментарии\n" + ctx["comments"])
    if ctx["confluence"]:
        L.append("\n## Документация Confluence\n" + "\n\n".join(ctx["confluence"]))
    if ctx["confluenceLinks"] or ctx["prLinks"]:
        L.append("\n## Ссылки для блока «Новое» (использовать ТОЛЬКО эти URL)")
        for l in ctx["confluenceLinks"]:
            L.append(f"- Confluence: {l['title']} -> {l['url']}")
        for l in ctx["prLinks"]:
            L.append(f"- PR: {l['title']} -> {l['url']}")
    if ctx["prs"]:
        L.append("\n## PR задачи\n" + "; ".join(ctx["prs"]))
    if ctx["diff"]:
        L.append("\n## Git diff (что реально меняется)\n" + "\n".join(ctx["diff"]))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Собрать контекст задачи Jira для AC")
    ap.add_argument("issue", help="ключ задачи, например PROJ-123")
    ap.add_argument("--json", action="store_true", help="вывести JSON вместо текста")
    ap.add_argument("--no-diff", action="store_true", help="не тянуть git diff")
    a = ap.parse_args()
    try:
        ctx = fetch(a.issue.strip(), want_diff=not a.no_diff)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
    out = json.dumps(ctx, ensure_ascii=False, indent=2) if a.json else render(ctx)
    sys.stdout.buffer.write((out + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
