#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trace_warnings.py — вывести все WARN и ERROR из логов всех спанов трейса Jaeger.

Зачем: формулировка «ошибок нет» в баг-репорте допустима, только если просмотрены логи ВСЕХ
спанов. Скрипт печатает каждый WARN/ERROR с сервисом и логгером, чтобы классифицировать их
одной строкой: относится к дефекту / нормальное поведение / отдельная находка.

Использование:
  python3 trace_warnings.py <traceId>          # трейс берётся из $JAEGER_URL/api/traces/<traceId>
  python3 trace_warnings.py --file trace.json  # уже скачанный ответ API Jaeger

Переменные окружения:
  JAEGER_URL    — например https://jaeger.example.com (без завершающего /)
  JAEGER_TOKEN  — (опционально) токен, если API закрыт авторизацией; уходит как Bearer
"""
import argparse, json, os, sys, urllib.request


def load(trace_id, path):
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    base = (os.environ.get("JAEGER_URL") or "").strip().rstrip("/")
    if not base:
        sys.exit("ОШИБКА: задай JAEGER_URL или передай --file")
    headers = {}
    if os.environ.get("JAEGER_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["JAEGER_TOKEN"]
    req = urllib.request.Request(f"{base}/api/traces/{trace_id}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser(description="WARN/ERROR всех спанов трейса Jaeger")
    ap.add_argument("trace_id", nargs="?", help="traceId")
    ap.add_argument("--file", help="JSON-ответ /api/traces/<id>, сохранённый заранее")
    a = ap.parse_args()
    if not a.trace_id and not a.file:
        ap.error("нужен traceId или --file")

    data = load(a.trace_id, a.file)
    traces = data.get("data") or []
    if not traces:
        sys.exit("Трейс не найден (пустой data)")
    t = traces[0]
    proc = {k: v.get("serviceName", k) for k, v in (t.get("processes") or {}).items()}
    spans = t.get("spans") or []

    found = 0
    for sp in spans:
        tags = {x.get("key"): x.get("value") for x in sp.get("tags", [])}
        span_err = tags.get("error") in (True, "true")
        for lg in sp.get("logs", []):
            f = {x.get("key"): x.get("value") for x in lg.get("fields", [])}
            level = str(f.get("level", "")).upper()
            if level in ("WARN", "WARNING", "ERROR") or "error.object" in f or f.get("event") == "error":
                found += 1
                msg = str(f.get("error.object") or f.get("message") or f.get("event") or "")
                print(f"{proc.get(sp.get('processID'), '?')} | {level or 'ERROR'} | "
                      f"{f.get('logger', '-')} | span {sp.get('spanID')} {sp.get('operationName', '')}")
                print("    " + msg[:300].replace("\n", " "))
        if span_err and not sp.get("logs"):
            found += 1
            print(f"{proc.get(sp.get('processID'), '?')} | ERROR(tag) | span {sp.get('spanID')} "
                  f"{sp.get('operationName', '')}")

    print(f"\nИтого: {len(spans)} спанов, WARN/ERROR записей: {found}", file=sys.stderr)


if __name__ == "__main__":
    main()
