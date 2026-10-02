#!/usr/bin/env python3
"""Lint базы знаний Obsidian при прямом доступе к файлам vault. Только отчёт: файлы не правит.

Проверки:
  broken_links       [[wikilink]], который не ведёт ни в один файл vault
  missing_sources    ссылка из frontmatter sources:, которая не ведёт ни в один файл
  empty_sources      sources: [] или нет поля sources у страниц type entity/concept/lesson/source
  orphans            страницы без входящих ссылок
  only_index_or_log  входящие ссылки есть только из index.md / log.md
  not_in_index       страницы wiki/entities и wiki/concepts, которых нет в wiki/index.md
  duplicate_names    страницы одного type с именами, совпадающими после нормализации
                     (order-service / OrderService); lesson и entity одной задачи — не дубль
  alias_collisions   один и тот же alias у разных страниц

Ссылки внутри `инлайн-кода` и блоков ``` не считаются: там примеры синтаксиса.

По умолчанию отчёт строится только по страницам графа — с полем type во frontmatter
(плюс wiki/index.md, wiki/log.md, wiki/overview.md). Рабочие заметки и черновики без type
в отчёт не попадают, но ссылки из них на страницы графа учитываются. --all-pages — по всем .md.

Пример:
  python wiki_lint.py --vault ~/notes --out lint.json
  python wiki_lint.py --vault ~/notes --scope wiki/entities --exclude drafts --exclude PROJ-123
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

try:
    import yaml  # необязательно: без PyYAML работает упрощённый разбор
except ImportError:
    yaml = None

DEFAULT_EXCLUDE = {'.obsidian', '.git', '.trash', 'node_modules'}
SPECIAL = {'wiki/index.md', 'wiki/log.md', 'wiki/overview.md'}
TYPED = {'entity', 'concept', 'lesson', 'source'}

R_FENCE = re.compile(r'^(```|~~~).*?^\1', re.S | re.M)
R_INLINE = re.compile(r'`[^`\n]*`')
R_LINK = re.compile(r'!?\[\[([^\]\|#\n]*)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]')
R_FM = re.compile(r'\A---\s*\n(.*?)\n---\s*(\n|\Z)', re.S)


def norm_name(s):
    return re.sub(r'[\s_\-.]+', '', s.lower())


def read(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def frontmatter(text):
    m = R_FM.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end():]
    if yaml:
        try:
            data = yaml.safe_load(raw) or {}
            return (data if isinstance(data, dict) else {}), body
        except yaml.YAMLError:
            return {'__invalid__': True}, body
    data, key = {}, None
    for line in raw.splitlines():
        km = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            if val.startswith('[') and val.endswith(']'):
                inner = val[1:-1].strip()
                data[key] = [x.strip().strip('"\'') for x in inner.split(',')] if inner else []
            elif val:
                data[key] = val.strip('"\'')
            else:
                data[key] = []
        elif key and re.match(r'^\s*-\s+', line) and isinstance(data.get(key), list):
            data[key].append(re.sub(r'^\s*-\s+', '', line).strip().strip('"\''))
    return data, body


def links_in(text):
    text = R_FENCE.sub('', text)
    text = R_INLINE.sub('', text)
    return [t.strip() for t in R_LINK.findall(text) if t.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--vault', default=os.environ.get('OBSIDIAN_VAULT'), help='корень vault (или OBSIDIAN_VAULT)')
    ap.add_argument('--scope', help='отчитываться только по страницам внутри этой папки (ссылки ищутся по всему vault)')
    ap.add_argument('--exclude', action='append', default=[], help='папка верхнего уровня, которую пропустить (можно несколько)')
    ap.add_argument('--all-pages', action='store_true', help='отчитываться по всем .md, а не только по страницам с type')
    ap.add_argument('--out', help='записать JSON в файл')
    a = ap.parse_args()
    if not a.vault or not os.path.isdir(os.path.expanduser(a.vault)):
        sys.exit('Укажите --vault или переменную OBSIDIAN_VAULT (путь к существующей папке).')
    root = os.path.abspath(os.path.expanduser(a.vault))
    exclude = DEFAULT_EXCLUDE | set(a.exclude)

    all_files, md = [], []
    for dp, dns, fns in os.walk(root):
        rel_dir = os.path.relpath(dp, root).replace('\\', '/')
        top = rel_dir.split('/')[0]
        if top in exclude or any(part in DEFAULT_EXCLUDE for part in rel_dir.split('/')):
            dns[:] = []
            continue
        for fn in fns:
            rel = (fn if rel_dir == '.' else f"{rel_dir}/{fn}")
            all_files.append(rel)
            if fn.lower().endswith('.md'):
                md.append(rel)

    by_path = {f.lower(): f for f in all_files}
    by_base = defaultdict(list)
    for f in all_files:
        base = f.rsplit('/', 1)[-1]
        by_base[base.lower()].append(f)
        if base.lower().endswith('.md'):
            by_base[base[:-3].lower()].append(f)

    def resolve(target):
        t = target.strip().replace('\\', '/').lstrip('/')
        cands = [t, t + '.md']
        for c in cands:
            if c.lower() in by_path:
                return by_path[c.lower()]
        base = t.rsplit('/', 1)[-1].lower()
        hits = by_base.get(base) or by_base.get(base + '.md')
        if hits:
            if '/' in t:
                tail = [h for h in hits if h.lower().endswith(t.lower()) or h.lower().endswith(t.lower() + '.md')]
                return tail[0] if tail else None
            return hits[0]
        return None

    in_dir = (lambda p: p.startswith(a.scope.strip('/') + '/')) if a.scope else (lambda p: True)
    graph = set()
    in_scope = lambda p: in_dir(p) and (a.all_pages or p in graph or p in SPECIAL)
    incoming = defaultdict(set)
    rep = defaultdict(list)
    aliases = defaultdict(set)

    parsed = {}
    for page in md:
        fm, body = frontmatter(read(os.path.join(root, page)))
        parsed[page] = (fm, body)
        if str(fm.get('type', '')).strip():
            graph.add(page)

    for page in md:
        fm, body = parsed[page]
        if fm.get('__invalid__') and in_scope(page):
            rep['invalid_frontmatter'].append(page)
        for t in links_in(body):
            dst = resolve(t)
            if dst:
                incoming[dst].add(page)
            elif in_scope(page):
                rep['broken_links'].append({"page": page, "link": t})
        src = fm.get('sources')
        src_list = src if isinstance(src, list) else ([src] if src else [])
        for s in src_list:
            for t in links_in(str(s)) or []:
                dst = resolve(t)
                if dst:
                    incoming[dst].add(page)
                elif in_scope(page):
                    rep['missing_sources'].append({"page": page, "source": t})
        ptype = str(fm.get('type', '')).strip()
        if in_scope(page) and ptype in TYPED and not src_list:
            rep['empty_sources'].append(page)
        al = fm.get('aliases') if in_scope(page) else None
        for x in (al if isinstance(al, list) else ([al] if al else [])):
            aliases[str(x).strip().lower()].add(page)

    index_links = set()
    if 'wiki/index.md' in md:
        for t in links_in(read(os.path.join(root, 'wiki/index.md'))):
            d = resolve(t)
            if d:
                index_links.add(d)

    for page in md:
        if not in_scope(page) or page in SPECIAL:
            continue
        src = incoming.get(page, set()) - {page}
        if not src:
            rep['orphans'].append(page)
        elif src <= {'wiki/index.md', 'wiki/log.md'}:
            rep['only_index_or_log'].append(page)
        if 'wiki/index.md' in md and page.startswith(('wiki/entities/', 'wiki/concepts/')) and page not in index_links:
            rep['not_in_index'].append(page)

    groups = defaultdict(list)
    for page in md:
        if not in_scope(page):
            continue
        ptype = str(parsed[page][0].get('type', '')).strip()
        groups[(ptype, norm_name(page.rsplit('/', 1)[-1][:-3]))].append(page)
    rep['duplicate_names'] = [v for v in groups.values() if len([p for p in v if in_scope(p)]) > 1]
    rep['alias_collisions'] = [{"alias": k, "pages": sorted(v)} for k, v in aliases.items()
                               if len(v) > 1 and any(in_scope(p) for p in v)]

    summary = {k: len(v) for k, v in rep.items()}
    result = {"vault": root, "pages": len(md), "graphPages": len(graph), "scope": a.scope, "summary": summary, "issues": rep}
    out = json.dumps(result, ensure_ascii=False, indent=1)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(out)
        print(json.dumps({"pages": len(md), "graphPages": len(graph), "summary": summary, "out": a.out}, ensure_ascii=False))
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        print(out)


if __name__ == '__main__':
    main()
