# Контракты автопилота: state.json и шаблоны артефактов

Содержание: state.json · 00_INDEX.md · progress.md · CONTEXT.md · PLAN.md · checks/TC-N.md ·
VERIFICATION.md · CLEANUP.md · HUMAN_GATE.md · APPROVAL.md.

Пример предметной области в шаблонах — вымышленный интернет-магазин (`order-service`,
`payment-service`, `notification-service`).

## state.json

Руками не правится: только через `scripts/autopilot.py`.

```json
{
  "task": "PROJ-123",
  "stand": "test-3",
  "run_dir": "<QA_AUTOPILOT_ROOT>/PROJ-123/autopilot/run-20260914-1830",
  "run_tag": "autopilot-PROJ-123-20260914-1830",
  "phase": "EXECUTE",
  "status": "running",
  "started": "2026-09-14T18:30:00",
  "updated": "2026-09-14T19:02:11",
  "flags": { "assume_deployed": true, "testcases": true, "budget_mtok": 4 },
  "rounds": { "PLAN_REVIEW": 1, "EVIDENCE_REVIEW": 0, "ENV": 0, "CLEANUP": 0 },
  "stop_blocks": 0,
  "current_dispatch": { "role": "qa-executor", "tcs": ["TC-3"], "at": "2026-09-14T19:01:40" },
  "usage": { "tokens": 512300, "subagents": 7, "by_role": { "qa-executor": 301200, "qa-analyst": 211100 },
             "by_transcript": { "…/subagents/agent-a1b2.jsonl": 301200 } },
  "grants": [ { "scope": "test-branch", "value": "PROJ-123", "quote": "<слова человека>", "at": "…" } ],
  "next_action": "EXECUTE TC-3: диспатч qa-executor",
  "rulings": [ { "what": "…", "why": "…", "cost": "…", "at": "…" } ],
  "blocked_code": null,
  "blocked_reason": null
}
```

`usage` пишет SubagentStop-хук на возврате каждой роли: всего токенов, сколько раз роли
возвращались, разбивка по ролям. Хук перечитывает файл прямо перед записью, поэтому два
одновременных возврата не затирают счётчик друг друга. Считается по уникальному `message.id`:
один ответ модели лежит в журнале несколькими строками с одинаковым `usage`, и сумма по строкам
завышала расход вдвое. `by_transcript` хранит, сколько уже учтено по журналу каждой роли, и
прибавляется только разница — иначе пересдача после отказа хука (exit 2) считала бы всю прошлую
работу роли заново.

`flags.budget_mtok` — лимит прогона в миллионах токенов: `dispatch` отказывается отправлять
следующую роль, когда расход дошёл до лимита (`0` — без лимита). Денег в состоянии нет: у ролей
разные модели и тарифы, средняя ставка врала бы. Нужна оценка в валюте — переменная окружения
`QA_AUTOPILOT_USD_PER_MTOK`.

`grants` — разрешения человека на одно действие из hard-stops (`autopilot.py grant`), со словами
человека дословно:

- `test-branch` разрешает создать ветку с номером задачи в имени в репозитории из
  `QA_AUTOPILOT_BRANCH_REPOS` и писать файлы в неё;
- `budget-mtok` поднимает лимит токенов;
- `cleanup-deploy` разрешает один откат релиза в фазе `CLEANUP`, и значение (сервис или
  окружение) обязано встречаться в самой команде деплоя.

Гард `guard_external_writes.py` читает этот список и по нему решает, пропустить ли запрос.

`blocked_code` — один из STOP-кодов (`autopilot.py block --code …`): `preflight_failed`,
`no_stand`, `plan_rejected_3x`, `deploy_mismatch`, `deploy_failed_3x`, `migration_blocker`,
`env_unavailable`, `dirty_fixture`, `evidence_stalled`, `cleanup_failed`, `hard_stop`,
`needs_human`, `budget_exceeded`, `other`. `blocked_reason` — текст для человека.

## 00_INDEX.md

Пишется `autopilot.py init`: порядок фаз и какой файл к какой фазе относится, плюс список
STOP-кодов. Читать первым при `--resume` и при знакомстве с чужим прогоном.

Фазы: `PREFLIGHT, CONTEXT, PLAN, PLAN_REVIEW, ENV, EXECUTE, EVIDENCE_REVIEW, CLEANUP, PACKAGE,
HUMAN_GATE, PUBLISH, INGEST, DONE, BLOCKED`. Терминальные для Stop-хука: `HUMAN_GATE, BLOCKED, DONE`.

`run_tag` — метка прогона: кладётся в `external_id` фикстур, в имена стабов WireMock, в
комментарии SQL. Откат = найти всё с этой меткой.

Указатель на активный прогон: `~/.claude/qa-autopilot/active.json` →
`{"run_dir": "...", "task": "...", "sessions": ["<id сессии-владельца>"]}`. Хуки читают его;
нет файла — хуки ничего не делают.

## progress.md (леджер, только дописывается)

```
# autopilot ledger — task: PROJ-123 — run: run-20260914-1830 — stand: test-3
[18:30] PREFLIGHT ok: tokens 4/4, jaeger ok
[18:31] CONTEXT dispatch qa-analyst
[18:44] SUBAGENT qa-analyst вернулся: 13 мин · инструментов 41 (Bash 22, Read 12, Grep 7) · токенов 211.1k
[18:45] PLAN_REVIEW round 1 qa-plan-reviewer: needs_fixes (3 issues)
…
[19:40] EVIDENCE_REVIEW: passed 8/9, TC-6 INCONCLUSIVE → round 2
```

## CONTEXT.md

```markdown
# CONTEXT — PROJ-123 — стенд test-3

## Зачем доработка (бизнес-смысл)
<что получает пользователь или бизнес, со ссылкой на источник>

## Что делает правка в коде
<было → стало>

## Критерии приёмки
| REQ | Формулировка | Источник (страница спеки / комментарий / файл:строка) | Тип (HP/EC/regress) |

## Карта изменений
| Репо | PR | Ветка | Методы/классы | Проверено diff ветка↔master |

## Миграции БД
| Схема | Версия в ветке | Последняя на стенде | С каким деплоем едет Job | Конфликт |

## Готовность интеграции            ← только если задача трогает партнёра, адаптер, колбэк или мок
| Компонент | Состояние | Чем проверено |
Вывод: … · Пути с ценой: 1) … 2) …

## Референсы живого пути
- <эталонный E2E-кейс продукта: id/ссылка, что он проходит>

## Что прочитано и записано в базу знаний (если она есть)
- …

## ASSUMED (без источника — гипотезы)
- …
```

## PLAN.md — строка матрицы

```markdown
| TC | REQ | Тип | Метод | Предусловия (SELECT + фактический результат) | Запрос (полностью) | Ожидаемо (+источник) | fails_when | Откат | Риск |
```

Требования к строке — в промпте `qa-plan-reviewer` (`assets/agents/qa-plan-reviewer.md`). Пары:
у негативной TC колонка `Тип` = `negative(control: TC-K)`. Проверки с участием человека:
`Тип` = `NEEDS_HUMAN`. После ревью аналитик дописывает раздел «Ответ на ревью, раунд N»:
по каждому замечанию `ADDRESSED` и что изменено.

## checks/TC-N.md — карточка исполнителя

Обязательные заголовки (проверяет `validate_check_report.py`):

````markdown
# TC-N — <название> — REQ-K
status: PASS | FAIL | BLOCKED | INCONCLUSIVE | NEEDS_HUMAN
run_tag: autopilot-…

## Исходное состояние (SELECT до)
```sql
select id, status, total from orders where external_id = 'autopilot-PROJ-123-…';
```
результат: <строки дословно>

## Запрос
```bash
curl … (полностью: метод, URL, все заголовки, тело целиком)
```
или inject в очередь: exchange / routing key / заголовки с типами / payload целиком
ответ: HTTP-код + тело дословно → evidence/TC-N/response.json

## Проверка результата (SELECT после)
```sql
…
```
результат: <строки дословно>

## Трейс
traceId: <полный> · {JAEGER_URL}/trace/<id> · hasError: true|false
ошибки по тексту error.object (каждая: ENV | тест-данные | логика): …
сырой JSON: evidence/TC-N/trace.json
(проверка без вызова сервиса: `traceId: нет. <почему трейса не существует>`)

## Откат
выполнен: да|нет|не требовался · SQL/команда · SELECT чистоты: <результат>

## Сравнение с ожидаемым
ожидалось (источник): … · фактически: … · вывод: …
````

## VERIFICATION.md

```markdown
# VERIFICATION — round R
status: passed | gaps_found | human_needed
живой путь: есть (TC-1: api-gateway → order-service → payment-service → БД) | нет

| TC | Статус исполнителя | Вердикт | Основание (что добыто заново) | Цепочка | Замечание |
| TC-1 | PASS | CONFIRMED | трейс 7f3a… существует, hasError=false, SELECT совпал | живой путь | — |
| TC-4 | FAIL | INCONCLUSIVE | тело запроса не сверено с логом реального вызова | фикстура | нужен контрольный позитив |

## Gaps (для перепрогона)
- TC-4: …

## Что не удалось проверить (Cannot verify)
- …
```

## CLEANUP.md

```markdown
| Фикстура (run_tag) | Исходное значение | Откат выполнен | SELECT чистоты | Результат |
Стабы WireMock с меткой: удалены N · снимок *-BEFORE.json совпал: да|нет

## Что остаётся на стенде осознанно      ← только если так решил человек
- релиз order-service ветки PROJ-123 (решение: не откатывать)
- тестовая ветка PROJ-123 в инфраструктурном репозитории (удалить — человеку)
```

Заголовок со словом «остаются/остаётся» `autopilot.py gate` выносит на первый экран
`HUMAN_GATE.md`: окружение изменено осознанно, а не «грязное».

## HUMAN_GATE.md (собирает `autopilot.py gate`)

```markdown
# HUMAN_GATE — PROJ-123 — стенд test-3 — run …

## Первый экран
Что сломано: <BUG-1 одной строкой | ничего>
Что проверено: CONFIRMED K · REFUTED R · INCONCLUSIVE M · NEEDS_HUMAN L
Стенд после прогона: данные прогона в исходном состоянии (CLEANUP.md) | НЕ ПОДТВЕРЖДЁН — …

## Просим утвердить
- [ ] Комментарий в задачу тестирования — PACKAGE/session-report.md
- [ ] BUG-1 — PACKAGE/bugs/BUG-1.md (второй судья: не опроверг)
- [ ] ТК — PACKAGE/testcases/TC-*.md
- Наблюдения (не публикуются): PACKAGE/observations.md
- Решения агента: PACKAGE/RULINGS.md

## Как одобрить
Скопируй APPROVAL.template.md в APPROVAL.md, отредактируй и запусти `/qa-auto PROJ-123 --approve`.
```

Вопросы аналитику репортер кладёт в `PACKAGE/bugs/QUESTION-N.md`: `gate` выносит их отдельными
пунктами.

## APPROVAL.md (пишет человек)

```yaml
jira_comment: yes | no
bugs: [BUG-1]           # какие баги публиковать как задачи или комментарии
testcases: [TC-3, TC-4] # какие тест-кейсы создать
notes: "…"              # правки, которые внести перед публикацией
```

Хук `guard_external_writes.py` разрешает write-инструменты только при `phase == PUBLISH` и только
для перечисленного здесь.
