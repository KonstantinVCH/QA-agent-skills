"""
Jaeger MCP-сервер: инструменты для поиска и разбора распределённых трейсов Jaeger.

Работает с любым MCP-клиентом по stdio (Claude Code, Claude Desktop, OpenCode и др.).
Нужны Python >= 3.10 и пакеты `mcp`, `httpx`:  pip install mcp httpx

Переменные окружения:
    JAEGER_URL    базовый URL Jaeger, напр. https://jaeger.example.com (обязательно)
    JAEGER_TOKEN  Bearer-токен, если Jaeger закрыт авторизацией (необязательно)
"""
import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jaeger")

JAEGER_BASE = os.environ.get("JAEGER_URL", "").rstrip("/") + "/api"
HEADERS = ({"Authorization": "Bearer " + os.environ["JAEGER_TOKEN"]}
           if os.environ.get("JAEGER_TOKEN") else {})


def client(timeout):
    """HTTP-клиент с авторизацией, если она задана."""
    if JAEGER_BASE == "/api":
        raise RuntimeError("JAEGER_URL не задан в окружении MCP-сервера")
    return httpx.AsyncClient(timeout=timeout, headers=HEADERS)


@mcp.tool()
async def jaeger_services() -> dict:
    """Получить список всех сервисов из Jaeger."""
    async with client(30) as http:
        r = await http.get(f"{JAEGER_BASE}/services")
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def jaeger_operations(service: str) -> dict:
    """
    Получить список операций для сервиса.
    
    Args:
        service: имя сервиса из jaeger_services()
    """
    async with client(30) as http:
        r = await http.get(f"{JAEGER_BASE}/services/{service}/operations")
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def jaeger_find_traces(
    service: str,
    operation: str = "",
    lookback: str = "1h",
    limit: int = 20,
    min_duration: str = "",
    max_duration: str = "",
    tags: str = "",
) -> dict:
    """
    Найти трейсы по фильтрам.

    Args:
        service: имя сервиса (обязательно)
        operation: имя операции (опционально)
        lookback: период поиска: 1h, 2h, 6h, 12h, 24h, 2d, 7d
        limit: количество трейсов (макс 100)
        min_duration: минимальная длительность, например "100ms", "1s"
        max_duration: максимальная длительность, например "5s"
        tags: JSON-строка с тегами, например {"http.status_code":"500"} или {"error":"true"}
    """
    params = {"service": service, "limit": limit, "lookback": lookback}
    if operation:
        params["operation"] = operation
    if min_duration:
        params["minDuration"] = min_duration
    if max_duration:
        params["maxDuration"] = max_duration
    if tags:
        params["tags"] = tags

    async with client(60) as http:
        r = await http.get(f"{JAEGER_BASE}/traces", params=params)
        r.raise_for_status()
        data = r.json()

    # Вернуть краткое резюме по каждому трейсу
    traces = data.get("data", [])
    summary = []
    for trace in traces:
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        services = set()
        has_error = False
        max_duration_us = 0

        for span in spans:
            proc = processes.get(span.get("processID", ""), {})
            services.add(proc.get("serviceName", "unknown"))
            dur = span.get("duration", 0)
            if dur > max_duration_us:
                max_duration_us = dur
            tags_map = {t["key"]: t["value"] for t in span.get("tags", [])}
            if tags_map.get("error") is True or str(tags_map.get("error", "")).lower() == "true":
                has_error = True

        root = spans[0] if spans else {}
        summary.append({
            "traceID": trace.get("traceID"),
            "rootOperation": root.get("operationName", ""),
            "spanCount": len(spans),
            "services": sorted(list(services)),
            "durationMs": round(max_duration_us / 1000, 1),
            "hasError": has_error,
            "startTime": root.get("startTime", 0),
        })

    return {"total": len(summary), "traces": summary}


@mcp.tool()
async def jaeger_get_trace(trace_id: str) -> dict:
    """
    Получить полный trace по ID.

    Args:
        trace_id: ID трейса из jaeger_find_traces()
    """
    async with client(60) as http:
        r = await http.get(f"{JAEGER_BASE}/traces/{trace_id}")
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def jaeger_analyze_trace(trace_id: str) -> dict:
    """
    Анализ трейса: ошибки, медленные спаны, дерево вызовов, корневая причина.

    Args:
        trace_id: ID трейса из jaeger_find_traces()
    """
    async with client(60) as http:
        r = await http.get(f"{JAEGER_BASE}/traces/{trace_id}")
        r.raise_for_status()
        data = r.json()

    traces = data.get("data", [])
    if not traces:
        return {"error": "trace not found"}

    trace = traces[0]
    spans = trace.get("spans", [])
    processes = trace.get("processes", {})

    errors = []
    slow_spans = []
    all_services = set()
    span_map = {}

    for span in spans:
        proc = processes.get(span.get("processID", ""), {})
        service = proc.get("serviceName", "unknown")
        all_services.add(service)
        duration_us = span.get("duration", 0)
        tags_map = {t["key"]: t["value"] for t in span.get("tags", [])}
        logs = span.get("logs", [])

        span_map[span["spanID"]] = {
            "spanID": span["spanID"],
            "operation": span.get("operationName"),
            "service": service,
            "durationMs": round(duration_us / 1000, 1),
            "tags": tags_map,
        }

        if tags_map.get("error") is True or str(tags_map.get("error", "")).lower() == "true":
            error_detail = {
                "spanID": span["spanID"],
                "operation": span.get("operationName"),
                "service": service,
                "durationMs": round(duration_us / 1000, 1),
                "tags": tags_map,
                "logs": logs,
            }
            # Добавить http.url, db.statement если есть
            for key in ["http.url", "http.method", "http.status_code", "db.statement",
                        "peer.service", "rpc.method", "error.object", "message"]:
                if key in tags_map:
                    error_detail[key] = tags_map[key]
            errors.append(error_detail)

        if duration_us > 200_000:  # > 200ms
            slow_spans.append({
                "spanID": span["spanID"],
                "operation": span.get("operationName"),
                "service": service,
                "durationMs": round(duration_us / 1000, 1),
                "tags": {k: v for k, v in tags_map.items()
                         if k in ["http.url", "http.status_code", "db.statement",
                                  "peer.service", "rpc.method"]},
            })

    slow_spans.sort(key=lambda x: -x["durationMs"])

    # Общая длительность трейса
    all_durations = [s.get("duration", 0) for s in spans]
    total_duration_ms = round(max(all_durations) / 1000, 1) if all_durations else 0

    # Найти корневой спан
    root_span = None
    for span in spans:
        if not span.get("references"):
            root_span = span
            break
    if not root_span and spans:
        root_span = spans[0]

    return {
        "traceID": trace.get("traceID"),
        "totalDurationMs": total_duration_ms,
        "spanCount": len(spans),
        "services": sorted(list(all_services)),
        "rootOperation": root_span.get("operationName") if root_span else None,
        "rootService": processes.get(root_span.get("processID", ""), {}).get("serviceName") if root_span else None,
        "errors": errors,
        "slowSpans": slow_spans[:15],
        "summary": (
            f"Трейс содержит {len(spans)} спанов, {len(all_services)} сервисов, "
            f"общее время {total_duration_ms}ms. "
            f"Ошибок: {len(errors)}. "
            f"Медленных спанов (>200ms): {len(slow_spans)}."
        ),
    }


@mcp.tool()
async def jaeger_find_errors(
    service: str,
    lookback: str = "1h",
    limit: int = 20,
) -> dict:
    """
    Найти все трейсы с ошибками для сервиса за период.

    Args:
        service: имя сервиса
        lookback: период: 1h, 2h, 6h, 12h, 24h
        limit: количество трейсов
    """
    params = {
        "service": service,
        "limit": limit,
        "lookback": lookback,
        "tags": '{"error":"true"}',
    }
    async with client(60) as http:
        r = await http.get(f"{JAEGER_BASE}/traces", params=params)
        r.raise_for_status()
        data = r.json()

    traces = data.get("data", [])
    result = []
    for trace in traces:
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        error_spans = []
        for span in spans:
            tags_map = {t["key"]: t["value"] for t in span.get("tags", [])}
            if tags_map.get("error") is True or str(tags_map.get("error", "")).lower() == "true":
                proc = processes.get(span.get("processID", ""), {})
                error_spans.append({
                    "operation": span.get("operationName"),
                    "service": proc.get("serviceName", "unknown"),
                    "durationMs": round(span.get("duration", 0) / 1000, 1),
                    "tags": {k: v for k, v in tags_map.items()
                             if k in ["http.status_code", "http.url", "error.object",
                                      "db.statement", "rpc.method"]},
                })
        if error_spans:
            result.append({
                "traceID": trace.get("traceID"),
                "errorSpans": error_spans,
            })

    return {"total": len(result), "errorTraces": result}


if __name__ == "__main__":
    mcp.run()
