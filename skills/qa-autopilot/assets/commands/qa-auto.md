---
description: Автономный QA-конвейер (скилл qa-autopilot) — `PROJ-123 --stand test-3 [--assume-deployed]` | `PROJ-123 --resume` | `PROJ-123 --approve`
---
Активируй скилл `qa-autopilot` (через Skill tool) и работай как его оркестратор строго по фазам: PREFLIGHT → CONTEXT → PLAN → PLAN_REVIEW → ENV → EXECUTE → EVIDENCE_REVIEW → CLEANUP → PACKAGE → HUMAN_GATE; после `--approve` — PUBLISH → INGEST → DONE.

Живых действий на стенде сам не выполняй — только через роли-субагенты (qa-analyst, qa-plan-reviewer, qa-env-engineer, qa-executor, qa-evidence-reviewer, qa-reporter). Состояние веди только через `scripts/autopilot.py`. Наружу ничего не публикуй до `APPROVAL.md`.

Если в аргументах нет ключа задачи (вида `PROJ-123`) или нет `--stand <имя>` (и это не `--resume` / `--approve`) — остановись и спроси: автопилот не выбирает стенд сам.

Аргументы: $ARGUMENTS
