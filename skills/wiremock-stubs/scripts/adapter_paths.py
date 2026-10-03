#!/usr/bin/env python3
"""adapter_paths.py - собрать перечень исходящих эндпоинтов сервиса-адаптера (Java/Kotlin, Spring).

Отвечает на вопрос «что вообще надо застабить». Читает репозиторий сервиса и
печатает строки `METHOD /path`, готовые для `wm.py cover`.

Метод важен так же, как путь: стаб на POST при вызове GET отдаёт
404 Request was not matched, и это выглядит как недоступность партнёра. В конфиге
методов нет, поэтому они достаются из кода - `.post()` стоит на 1-3 строки выше
`.path(clientProperties.getPath().getCreatePayment())`.

    python adapter_paths.py ~/src/payment-gateway-adapter          # склонированный репо
    python adapter_paths.py payment-gateway-adapter.zip            # архив ветки
    python adapter_paths.py payment-gateway-adapter --pat <PAT>    # скачает архив из Bitbucket Server сам
    python adapter_paths.py <источник> --client payment-gateway -o paths.txt

Поддерживаются две распространённые схемы объявления путей:
  A. в application.yml блоком `<префикс>.clients.<имя>.path.<ключ>: /путь`
     (Spring @ConfigurationProperties; имя блока меняется через --clients-key);
  B. литералом в коде: .path("/v1/partner/order/add").
Скрипт разбирает обе и говорит, какая сработала. Другая схема - перечень собирать
вручную (grep по коду HTTP-клиента) и подавать в `wm.py cover` в том же формате.

Переменные окружения (для скачивания по slug):
    BITBUCKET_URL       адрес Bitbucket Server/Data Center, напр. https://git.example.com
    BITBUCKET_TOKEN     PAT с правом Read (можно вместо --pat)
    BITBUCKET_PROJECTS  ключи проектов через запятую, где искать репозиторий
    ADAPTER_INTERNAL_CLIENTS  имена внутренних клиентов через запятую - их не мокать
"""

import argparse
import io
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BITBUCKET = (os.environ.get("BITBUCKET_URL") or "").rstrip("/")
PROJECTS = tuple(p.strip() for p in (os.environ.get("BITBUCKET_PROJECTS") or "").split(",") if p.strip())

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Внутренние клиенты платформы: под мок-режим попадают, но мокать их не надо -
# заглушки нужны для ВНЕШНИХ партнёров. Перечень - свой у каждого проекта.
INTERNAL = {c.strip() for c in (os.environ.get("ADAPTER_INTERNAL_CLIENTS") or "").split(",") if c.strip()}
METHODS = ("post", "put", "patch", "delete", "get")


# --------------------------------------------------- откуда читаем репозиторий
# Репозиторий адаптера часто не склонирован локально - на этом проваливается
# сверка покрытия. Поэтому источников три, интерфейс один.


class Dir:
    """Склонированный репозиторий."""

    def __init__(self, path):
        self.root = Path(path)
        self.name = self.root.name

    def read(self, rel):
        f = self.root / rel
        return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else None

    def code_files(self):
        for pat in ("*.java", "*.kt"):
            for f in (self.root / "src" / "main").rglob(pat):
                yield str(f), f.read_text(encoding="utf-8", errors="replace")


class Zip:
    """Архив ветки (Bitbucket -> Download, GitHub -> Download ZIP). Распаковывать не нужно."""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path)
        self.name = Path(path).stem if isinstance(path, (str, Path)) else "archive"
        names = self.zf.namelist()
        # Bitbucket кладёт файлы в корень, GitHub-подобные - в единственную папку
        tops = {n.split("/")[0] for n in names if "/" in n}
        self.prefix = f"{tops.pop()}/" if len(tops) == 1 and not any(
            n.startswith("src/") for n in names) else ""

    def read(self, rel):
        try:
            return self.zf.read(self.prefix + rel).decode("utf-8", "replace")
        except KeyError:
            return None

    def code_files(self):
        for n in self.zf.namelist():
            if n.startswith(self.prefix + "src/main/") and n.endswith((".java", ".kt")):
                yield n, self.zf.read(n).decode("utf-8", "replace")


def fetch_archive(slug, pat, branch="master"):
    """Скачать архив ветки из Bitbucket Server. Нужен PAT: анонимно API обычно отдаёт 401."""
    if not BITBUCKET or not PROJECTS:
        sys.exit("Для скачивания по slug задать BITBUCKET_URL и BITBUCKET_PROJECTS "
                 "(см. references/project-config.md), либо передать каталог или .zip.")
    for proj in PROJECTS:
        url = (f"{BITBUCKET}/rest/api/latest/projects/{proj}/repos/{slug}"
               f"/archive?at=refs/heads/{branch}&format=zip")
        r = urllib.request.Request(url, headers={"Authorization": f"Bearer {pat}"})
        try:
            with urllib.request.urlopen(r, timeout=180) as resp:
                return io.BytesIO(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"Bitbucket {e.code}: PAT не подошёл (нужны права Read на {proj}/{slug})")
            continue
        except OSError as e:
            sys.exit(f"Bitbucket недоступен: {e}")
    sys.exit(f"Репозиторий '{slug}' не найден в проектах {', '.join(PROJECTS)}. "
             f"Проверить slug или указать ветку через --branch.")


def open_source(arg, pat, branch):
    p = Path(arg).expanduser()
    if p.is_dir():
        return Dir(p)
    if p.is_file() and p.suffix.lower() == ".zip":
        return Zip(p)
    if pat:
        z = Zip(fetch_archive(arg, pat, branch))
        z.name = arg
        return z
    sys.exit(f"'{arg}' - не каталог и не .zip. Если репозиторий не склонирован: "
             f"скачать архив ветки (Download) и передать .zip, "
             f"либо добавить --pat <Bitbucket PAT> (или env BITBUCKET_TOKEN) и скачаю сам.")


# ----------------------------------------------------------- конфиг (схема A)

def _indent(line):
    return len(line) - len(line.lstrip(" "))


def parse_clients(yml_text, clients_key="clients"):
    """<префикс>.<clients_key>.<имя> -> {url, paths:{ключ:путь}, timeouts, retry}.

    Ищется блок `<clients_key>:` с отступом 2 (под любым ключом верхнего уровня).
    Ручной разбор по отступам вместо PyYAML: у скрипта не должно быть зависимостей,
    а нужен один вложенный блок, не весь документ.
    """
    lines = yml_text.splitlines()
    head = re.compile(r"^  %s:\s*$" % re.escape(clients_key))
    start = next((i for i, l in enumerate(lines) if head.match(l)), None)
    if start is None:
        return {}
    out, cur = {}, None
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ind = _indent(line)
        if ind < 4:                                   # вышли из clients:
            break
        m = re.match(r"^ {4}([\w-]+):\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = {"url": None, "paths": {}, "timeouts": {}, "retry": {}, "_in": None}
            continue
        if cur is None:
            continue
        c = out[cur]
        m = re.match(r"^ {6}(url|token):\s*(\S+)", line)
        if m:
            c["_in"] = None
            if m.group(1) == "url":
                c["url"] = m.group(2).strip("'\"")
            continue
        m = re.match(r"^ {6}(path|timeouts|retry):\s*(.*)$", line)
        if m:
            key, inline = m.group(1), m.group(2).strip()
            c["_in"] = key
            if inline.startswith("{"):                # timeouts: { response: 70s }
                for k, v in re.findall(r"([\w-]+):\s*([^,}]+)", inline):
                    c["timeouts" if key == "timeouts" else key][k] = v.strip()
                c["_in"] = None
            continue
        if ind == 6:                                  # любой другой ключ клиента
            c["_in"] = None
            continue
        m = re.match(r"^ {8}([\w-]+):\s*(\S+)", line)
        if m and c["_in"]:
            target = "paths" if c["_in"] == "path" else c["_in"]
            c[target][m.group(1)] = m.group(2).strip("'\"")
    for c in out.values():
        c.pop("_in", None)
    return out


# ------------------------------------------------------------- код (методы + B)

def _camel_to_kebab(name):
    """getCreatePayment -> create-payment"""
    name = re.sub(r"^get", "", name)
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def scan_code(source, window=4):
    """Пары (метод, ключ-конфига) и (метод, литеральный-путь) из java/kotlin.

    Ищем ближайший вызов метода ВЫШЕ строки с .path(...) в пределах window строк:
    в WebClient-цепочке между ними стоит только .uri(uriBuilder -> ...
    """
    by_key, literals = {}, {}
    for _name, text in source.code_files():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            # .path(clientProperties.getPath().getCreatePayment())
            # геттеров в строке несколько; нужен последний и не сам аксессор getPath()
            after = line.partition(".path(")[2]
            getters = [g for g in re.findall(r"get(\w+)\(\)", after)
                       if g not in ("Path", "Paths")]
            if getters:
                meth = _method_above(lines, i, window)
                if meth:
                    by_key.setdefault(_camel_to_kebab("get" + getters[-1]), set()).add(meth)
            for m in re.finditer(r'\.path\(\s*"(/[^"]+)"', line):
                meth = _method_above(lines, i, window)
                literals.setdefault(m.group(1), set()).add(meth or "ANY")
    return by_key, literals


def _method_above(lines, idx, window):
    for j in range(idx, max(-1, idx - window) - 1, -1):
        m = re.search(r"\.(%s)\(\)" % "|".join(METHODS), lines[j])
        if m:
            return m.group(1).upper()
    return None


# ------------------------------------------------------------------------ вывод

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("repo", help="каталог склонированного репо, .zip архива ветки, или slug "
                                "репозитория (тогда нужен --pat / BITBUCKET_TOKEN)")
    p.add_argument("--pat", default=os.environ.get("BITBUCKET_TOKEN"),
                   help="Bitbucket PAT: скачать архив ветки по slug (или env BITBUCKET_TOKEN)")
    p.add_argument("--branch", default="master", help="ветка для скачивания (по умолчанию master)")
    p.add_argument("--config", default="src/main/resources/application.yml",
                   help="путь к конфигу внутри репо (по умолчанию src/main/resources/application.yml)")
    p.add_argument("--clients-key", default="clients",
                   help="имя блока клиентов на втором уровне конфига (по умолчанию clients)")
    p.add_argument("--client", help="только этот клиент из блока клиентов (по умолчанию все внешние)")
    p.add_argument("--internal", help="внутренние клиенты через запятую, их не мокать "
                                      "(дополняет env ADAPTER_INTERNAL_CLIENTS)")
    p.add_argument("--all-clients", action="store_true",
                   help="не отфильтровывать внутренние клиенты платформы")
    p.add_argument("-o", "--out", help="писать в файл (utf-8) вместо stdout")
    a = p.parse_args()

    internal = set(INTERNAL)
    if a.internal:
        internal |= {c.strip() for c in a.internal.split(",") if c.strip()}

    source = open_source(a.repo, a.pat, a.branch)
    yml = source.read(a.config)
    clients = parse_clients(yml, a.clients_key) if yml else {}
    by_key, literals = scan_code(source)

    rows, notes = [], []
    ext = {n: c for n, c in clients.items()
           if c["paths"] and (a.all_clients or n not in internal)}
    if a.client:
        ext = {n: c for n, c in ext.items() if n == a.client}
        if not ext:
            sys.exit(f"Клиента '{a.client}' с блоком path: нет. Есть: "
                     + (", ".join(sorted(clients)) or "(ничего)"))

    if ext:
        notes.append(f"схема A: пути из <префикс>.{a.clients_key}.*.path")
        for name, c in sorted(ext.items()):
            notes.append(f"клиент {name}: url={c['url']}"
                         + (f", timeouts={c['timeouts']}" if c["timeouts"] else "")
                         + (f", retry={c['retry']}" if c["retry"] else ""))
            for key, path in sorted(c["paths"].items()):
                meths = sorted(by_key.get(key, [])) or ["ANY"]
                for meth in meths:
                    rows.append((meth, path, f"{name}.{key}"))
    if literals and not ext:
        notes.append(f"схема B: литеральные пути из кода (блока {a.clients_key}.*.path нет)")
        for path, meths in sorted(literals.items()):
            for meth in sorted(meths):
                rows.append((meth, path, "код"))
    elif literals and ext:
        extra = {p_: m for p_, m in literals.items()
                 if not any(p_ == r[1] for r in rows)}
        if extra:
            notes.append(f"плюс {len(extra)} литеральных путей из кода (нет в конфиге)")
            for path, meths in sorted(extra.items()):
                for meth in sorted(meths):
                    rows.append((meth, path, "код"))

    if not rows:
        sys.exit(f"Пути не найдены. Проверить вручную: {a.config} -> блок {a.clients_key}, "
                 "и grep по .path( в src/main. Возможно, схема объявления путей другая.")

    # Один путь может прийти из двух ключей конфига. Если хоть один ключ дал
    # конкретный метод, строка с ANY - шум: она бы ещё и подняла предупреждение про ANY.
    known = {path for meth, path, _ in rows if meth != "ANY"}
    seen, uniq = set(), []
    # why входит в ключ сортировки: иначе при двух ключах конфига на один путь
    # комментарий выбирался случайно и вывод плавал между запусками, засоряя дифф
    for meth, path, why in sorted(set(rows), key=lambda r: (r[1], r[0], r[2])):
        if meth == "ANY" and path in known:
            continue
        if (meth, path) not in seen:
            seen.add((meth, path))
            uniq.append((meth, path, why))

    body = [f"# {source.name}"] + [f"# {n}" for n in notes]
    if any(m == "ANY" for m, _, _ in uniq):
        body.append("# ANY = метод не удалось связать с путём, проверить в коде вручную")
    body += [f"{m} {p}".ljust(48) + f"# {w}" for m, p, w in uniq]
    text = "\n".join(body) + "\n"

    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"{len(uniq)} эндпоинтов -> {a.out}")
        print("\n".join(f"  {m} {p}" for m, p, _ in uniq))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
