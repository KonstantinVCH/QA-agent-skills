#!/usr/bin/env python3
"""post_json.py - отправить JSON на свой эндпоинт, играя роль внешней системы.

Нужен там, где мока быть не может: интеграция входящая (партнёр зовёт нас, а не мы
его), и вместо заглушки надо самому послать тот запрос, который прислал бы партнёр.
Пример - платёжный провайдер: браузер уходит на страницу оплаты, провайдер постит
результат оплаты нам (callback / webhook / postback).

    python post_json.py http://payment-service.example.test/openapi/paymentCallback body.json
    python post_json.py <url> body.json --set orderId=<uuid> --set payer.last_name=ИВАНОВ
    python post_json.py <url> body.json -X PUT -H "X-Api-Key: abc" --dry-run
    python post_json.py <url> --get                      # просто прочитать

Почему не curl: тело таких запросов почти всегда содержит русские ФИО и адреса, а
`curl -d` из Git Bash/консоли Windows отправляет их как U+FFFD - данные молча
сохраняются битыми. Здесь JSON уходит с ensure_ascii=True, то есть по проводу идёт чистый ASCII.
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TIMEOUT = 60


def set_path(obj, dotted, value, as_json=False):
    """--set payer.document.series="45 24" -> вложенная правка тела.

    Значение кладётся СТРОКОЙ. Автоугадывание типа здесь опасно: в спеках почти все
    поля строковые, а `--set ...number=112454` превращался бы в JSON-число - менялся
    бы тип поля, а у значений с ведущими нулями (номера документов, индексы) ещё и терялись нули. Нужен другой
    тип - `--set-json путь=значение` (например `--set-json gender=1`).
    """
    keys = dotted.split(".")
    cur = obj
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    if as_json:
        try:
            cur[keys[-1]] = json.loads(value)
        except json.JSONDecodeError:
            sys.exit(f"--set-json {dotted}: значение '{value}' не JSON. "
                     f"Строку писать в кавычках, число как 1, null как null. "
                     f"Для обычной строки есть --set.")
    else:
        cur[keys[-1]] = value


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("body", nargs="?", help="файл с JSON-телом (не нужен для --get)")
    p.add_argument("-X", "--method", help="метод, по умолчанию POST (или GET при --get)")
    p.add_argument("--get", action="store_true", help="только прочитать, тело не отправлять")
    p.add_argument("-H", "--header", action="append", default=[], help='"Имя: значение", можно несколько')
    p.add_argument("--basic", help="user:password для Basic Auth (спеки часто требуют)")
    p.add_argument("--set", action="append", default=[], metavar="путь=значение",
                   help="правка тела перед отправкой (строкой), путь через точку")
    p.add_argument("--set-json", action="append", default=[], metavar="путь=JSON",
                   dest="set_json", help="то же, но значение разбирается как JSON (число, bool, null)")
    p.add_argument("--dry-run", action="store_true", help="показать, что уйдёт, и не отправлять")
    a = p.parse_args()

    method = a.method or ("GET" if a.get else "POST")
    data = None
    if not a.get:
        if not a.body:
            sys.exit("Нужен файл с телом. Тело метода берётся из примера в спецификации "
                     "(пример входящего сообщения) - не выдумывать.")
        try:
            body = json.load(open(a.body, encoding="utf-8"))
        except FileNotFoundError:
            sys.exit(f"Файл не найден: {a.body}")
        except json.JSONDecodeError as e:
            sys.exit(f"{a.body} - не JSON ({e})")
        for pair, as_json in [(x, False) for x in a.set] + [(x, True) for x in a.set_json]:
            if "=" not in pair:
                sys.exit(f"--set ждёт вид путь=значение, получено: {pair}")
            k, v = pair.split("=", 1)
            set_path(body, k, v, as_json)
        # ключевая строка: ASCII по проводу, кириллица не бьётся
        data = json.dumps(body, ensure_ascii=True).encode("ascii")

    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for h in a.header:
        if ":" not in h:
            sys.exit(f'-H ждёт вид "Имя: значение", получено: {h}')
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()
    if a.basic:
        headers["Authorization"] = "Basic " + base64.b64encode(a.basic.encode()).decode()

    print(f"{method} {a.url}")
    for k, v in headers.items():
        print(f"  {k}: {'***' if k.lower() == 'authorization' else v}")
    if data is not None:
        shown = json.dumps(json.loads(data), ensure_ascii=False, indent=1)
        print("  тело:", shown if len(shown) < 1500 else shown[:1500] + " …")
    if a.dry_run:
        print("-- dry-run, запрос не отправлен")
        return

    req = urllib.request.Request(a.url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            code, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        sys.exit(f"Не дозвонился: {e}. Проверить, развёрнут ли сервис на этом стенде.")

    print(f"-- {code}")
    try:
        print(json.dumps(json.loads(raw), ensure_ascii=False, indent=1)[:2000])
    except Exception:
        print(raw[:2000] or "(пустое тело)")
    # 2xx у части входящих методов не значит успех: бывают спеки, где прямо написано,
    # что метод возвращает 200 вне зависимости от результата сохранения
    if 200 <= code < 300:
        print("-- код успешный, но проверять надо результат: прочитать сущность обратно "
              "(GET-метод сервиса) или посмотреть БД. 200 сам по себе ничего не доказывает.")
    sys.exit(0 if 200 <= code < 300 else 2)


if __name__ == "__main__":
    main()
