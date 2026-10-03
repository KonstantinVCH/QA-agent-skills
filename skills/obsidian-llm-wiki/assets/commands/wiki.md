---
description: База знаний Obsidian (Karpathy LLM Wiki) — wiki-first перед задачей
---
Активируй скилл `obsidian-llm-wiki` и работай по протоколу Karpathy LLM Wiki:
1. Preflight: проверь доступ к vault (MCP, Local REST API или файлы). Ничего не отвечает — СТОП, сообщи пользователю.
2. Wiki-first: прочитай `wiki/overview.md`, затем поиск по конкретным терминам (рус + англ + аббревиатура).
3. При необходимости — ingest с cascade (source → entity → concept → cross-links → log.md → index.md).

Запрос: $ARGUMENTS
