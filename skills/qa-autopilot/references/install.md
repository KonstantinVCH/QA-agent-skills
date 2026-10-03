# Установка: скилл, субагенты, хуки, команда

Автопилот — это не один файл: оркестратор (этот скилл), шесть субагентов и хуки Claude Code.
Без субагентов оркестратору некого диспатчить; без хуков нет детерминированных запретов.

## 1. Скилл

Положи каталог `qa-autopilot` в `~/.claude/skills/` (для всех проектов) или в
`.claude/skills/` проекта. Пути ниже — для пользовательской установки; для проектной замени
`~/.claude` на `.claude` в корне проекта.

## 2. Субагенты → `.claude/agents/`

Файлы ролей лежат в `assets/agents/`. Claude Code ищет субагентов в `~/.claude/agents/`
(пользовательские) и `.claude/agents/` (проектные).

```bash
mkdir -p ~/.claude/agents
cp ~/.claude/skills/qa-autopilot/assets/agents/qa-*.md ~/.claude/agents/
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\agents" | Out-Null
Copy-Item "$HOME\.claude\skills\qa-autopilot\assets\agents\qa-*.md" "$HOME\.claude\agents\"
```

После копирования открой каждый файл и подгони frontmatter:

- `tools:` — в файлах перечислены только встроенные инструменты. Допиши MCP-инструменты своей
  установки (полные имена вида `mcp__<server>__<tool>`), которые роли нужны: чтение задачи,
  страницы вики, трейсы, read-only SQL. Без них роль ходит в REST через `curl`.
  У `qa-plan-reviewer` и `qa-evidence-reviewer` нет `Write` и `Edit` **намеренно**: ревьюер не
  должен иметь возможности «поправить» то, что проверяет. Файл отчёта они пишут через Bash heredoc.
  У `qa-reporter` нет инструментов публикации — тоже намеренно.
- `skills:` — убери скиллы, которых у тебя нет. Роль справится по описанию в своём файле.
- `model:` — `qa-executor` на `sonnet` (механическая работа), остальные `inherit`.

Проверь: `/agents` в Claude Code показывает шесть ролей `qa-*`.

## 3. Хуки → `~/.claude/settings.json`

Открой `assets/hooks.autopilot.json` и **слей** содержимое ключа `hooks` с разделом `hooks` в
`~/.claude/settings.json` (не затирай уже существующие хуки). Хуки:

| Событие | Скрипт | Что делает |
|---|---|---|
| `PreToolUse` (MCP, Bash, PowerShell) | `guard_external_writes.py` | запрещает публикации до одобрения и hard-stops |
| `SubagentStop` (`qa-.*`) | `validate_check_report.py` | не выпускает исполнителя без полной карточки; считает токены каждой роли |
| `Stop` | `autopilot.py check-stop` | не даёт оркестратору закончить в нетерминальной фазе |
| `SessionStart`, `SubagentStart` (`qa-.*`) | `autopilot.py bootstrap` | напоминает об активном прогоне и hard-stops |

Все скрипты инертны, пока нет активного прогона (`~/.claude/qa-autopilot/active.json`): в обычной
работе хуки ничего не делают и не мешают.

На Windows команды хуков выполняются в Git Bash: `python` должен быть в `PATH` (или замени на
`py`). Скилл в другом каталоге — поправь путь в командах.

## 4. Slash-команда (необязательно)

```bash
mkdir -p ~/.claude/commands
cp ~/.claude/skills/qa-autopilot/assets/commands/qa-auto.md ~/.claude/commands/
```

Даёт `/qa-auto PROJ-123 --stand test-3 [--assume-deployed]`, `--resume`, `--approve`.

## 5. Переменные окружения

Минимум для первого прогона:

```bash
export QA_AUTOPILOT_STANDS="test-1,test-2,test-3"
export JIRA_URL="https://jira.example.com"
export JIRA_TOKEN="<токен из профиля Jira>"
export JAEGER_URL="http://jaeger.example.com"
```

Полный список и смысл каждой — `project-config.md`. Задать их для Claude Code можно в блоке
`env` файла `~/.claude/settings.json` — но токены лучше держать в профиле shell или менеджере
секретов, а не в файле настроек.

## 6. Проверка

```bash
python ~/.claude/skills/qa-autopilot/scripts/smoke_test.py
```

Ожидаемо: «все проверки прошли». Затем первый пилот — на задаче с заранее известным результатом,
с `--assume-deployed`. Убедись, что `qa-analyst` назвал каталог прогона без подсказки в промпте:
значит, хук `SubagentStart` работает.
