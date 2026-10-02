# Allure TestOps REST — полные контракты мануальных тест-кейсов

Официальная документация API: https://docs.qameta.io/reference/api/ — справочник по базовым
ручкам. Ниже реальное поведение, проверенное живыми вызовами, включая то, чего в документации нет.
При расхождении верить свежему живому вызову и обновить этот файл.

Обозначения: `{ALLURE_URL}` — адрес инстанса, `{PROJECT_ID}` — id проекта, `{TREE_ID}` — id дерева
тест-кейсов, `{JIRA_INTEGRATION_ID}` — id интеграции с Jira. Значения — в `project-config.md`.

Авторизация: заголовок `Authorization: Api-Token <token>`.

## Содержание

- [Тест-кейс](#тест-кейс)
- [Шаги мануального сценария (дерево)](#шаги-мануального-сценария-дерево)
- [Шаг с ожидаемым результатом](#шаг-с-ожидаемым-результатом)
- [Вложения](#вложения)
- [Общие шаги (shared steps)](#общие-шаги-shared-steps)
- [Дерево тест-кейсов](#дерево-тест-кейсов)
- [Custom fields](#custom-fields)
- [Связи с Jira](#связи-с-jira)
- [Поиск](#поиск)
- [Bearer-обмен](#bearer-обмен)

## Тест-кейс

```
POST /api/testcase
{"projectId":{PROJECT_ID},"name":"...","description":"...","automated":false,"statusId":-1,"workflowId":1}
→ 200 {id,...}     # без statusId/workflowId → 400 "workflow and status are required"
                    # statusId -1 = Draft; workflowId 1 = Manual (проверь id в своём инстансе)
PATCH /api/testcase/{id}   {"precondition":"..."} | {"description":"..."} | {"name":"..."} | {"statusId":N}
GET   /api/testcase/{id}   → карточка (precondition, status, workflow, automated, links)
DELETE /api/testcase/{id}  → мягкое удаление (кейс восстанавливается)
```

Id статусов и workflow берутся из карточки любого существующего кейса (`GET /api/testcase/{id}`,
поля `status` и `workflow`). Статус «на ревью» у каждой команды свой — записать его id в конфиг.

## Шаги мануального сценария (дерево)

⚠️ Хранилища два. `/api/testcase/{id}/scenario` — сценарий АВТОТЕСТА: `POST` вернёт 200, но в UI
мануального кейса шагов не будет («пустые кейсы»). Мануальные шаги — только так:

```
GET  /api/testcase/{id}/step
→ {"root":{"children":[ids]},
   "scenarioSteps":{id:{body,children,attachmentId,sharedStepId,expectedResultId}},
   "attachments":{id:{name,...}}}

POST /api/testcase/step
{"testCaseId":N,"body":"текст"}                → {"createdStepId":X}   # корневой
{"testCaseId":N,"parentId":P,"body":"текст"}   → вложенный
{"testCaseId":N,"parentId":P,"attachmentId":A} → узел-вложение
{"testCaseId":N,"sharedStepId":S}              → вставка общего шага (в конец)

POST /api/testcase/step/{stepId}/move  {"testCaseId":N,"beforeId":X}   # позиционирование
     # {"index":1} НЕ работает: 200, но порядок не меняется. Только beforeId/afterId

PATCH  /api/testcase/step/{stepId} {"body":"..."}   # ⚠️ затирает expectedResultId шага
DELETE /api/testcase/step/{stepId}?testCaseId=N     # ⚠️ удаляет детей И их вложения из кейса
```

Родное поле ОР напрямую не ставится: `PATCH {"expectedResultId":X}` → 400 `step.onlyonedetail`
(у шага одна «деталь»: ОР, или children, или вложение). Старый обходной формат, который встречается
в давних кейсах: дочерний узел `body:"Expected Result"`, внутри него — узлы с текстом результата.
Новые кейсы делать нативным способом, ниже.

## Шаг с ожидаемым результатом

Нативный формат, как при ручном вводе в UI:

```
POST  …/step {"testCaseId":N,"body":"<шаг>"}                                     → createdStepId
PATCH …/step/{id}?withExpectedResult=true {"body":"<тот же текст шага>","expectedResult":""}
                                                                                  → expectedResultId
POST  …/step {"testCaseId":N,"parentId":<expectedResultId>,"body":"<текст ОР>"}  ← текст сюда
```

Для общих шагов — те же три вызова на `/api/sharedstep/step` с ключом `sharedStepId`.

⚠️ **Текст ОР пишется НЕ в сам узел `expectedResultId`.** Узел ОР — контейнер со служебным телом,
текст лежит в его **дочерних** узлах. Если записать текст в сам контейнер (`PATCH …/step/{erId}`),
API отдаёт его в `body`, а UI показывает пустое поле «Опишите ожидаемый результат».

⚠️ **Ответ PATCH может прийти без `expectedResultId`.** Это бывает, когда у шага уже были
дети-вложения: контейнер ОР создаётся, а id в ответе нет. Тогда `POST …/step` с `parentId=None`
положит текст ОР корневым шагом сценария. После конвертации перечитывать дерево
(`GET …/step`) и брать `expectedResultId` оттуда.

Ограничение: конвертировать можно только шаг **без детей и вложений** — иначе 400
`…onlyonedetail`. Вложения к шагу добавляются **после** конвертации обычным
`POST …/step {parentId, attachmentId}` — так у шага одновременно есть и ОР, и файлы.

Вложения ОР лежат внутри контейнера `expectedResultId` и в плоском обходе `root.children`
не видны — обходить надо и детей контейнера.

## Вложения

```
POST  /api/testcase/attachment?testCaseId=N        multipart file  → [{id,name,contentType,...}]
GET   /api/testcase/attachment/{id}/content        → содержимое (НЕ /api/attachment/{id}/content — 404)
PATCH /api/testcase/attachment/{id} {"name":"curl"}
POST  /api/sharedstep/attachment?sharedStepId=S    multipart file
DELETE /api/sharedstep/attachment/{id}             # удалить вложение общего шага
```

⚠️ Не-ASCII в имени multipart-файла превращается в `U+FFFD` → имя латиницей, rename после.

⚠️ `DELETE` шага удаляет и вложения, которые висели на нём и его детях: они исчезают из списка
вложений сущности целиком. При пересборке дерева файлы заливать заново, старые id не годятся.

⚠️ Вложений к предусловию API не поддерживает: файл грузить вложением кейса и ссылаться на него
в тексте предусловия по имени.

**Скриншот UI из живой Playwright-сессии** (куки и состояние сохраняются, отдельный headless
Chrome не нужен):

```js
const buf = await page.screenshot({type: 'png'});
await page.request.post('{ALLURE_URL}/api/sharedstep/attachment?sharedStepId=<S>', {
  headers: {Authorization: 'Api-Token ' + process.env.ALLURE_TOKEN},
  multipart: {file: {name: 'ui_1_payment_screen.png', mimeType: 'image/png', buffer: buf}}});
```

## Общие шаги (shared steps)

```
GET  /api/sharedstep?projectId={PROJECT_ID}&size=20   → {content:[{id,name,stepsCount,testCasesCount}]}
POST /api/sharedstep {"projectId":{PROJECT_ID},"name":"Авторизация тестового покупателя"} → {id}
GET  /api/sharedstep/{id}/step                        → {"root","sharedStepScenarioSteps","sharedStepAttachments"}
POST /api/sharedstep/step {"sharedStepId":S,"body":"..."[,"parentId"][,"attachmentId"]} → {createdStepId}
POST /api/sharedstep/attachment?sharedStepId=S        multipart file
```

Вставка в кейс и позиционирование — раздел «Шаги» (`sharedStepId` + `/move`).

⚠️ `PATCH` тела узла, вставленного как общий шаг, превращает его в обычный текстовый шаг:
общий шаг из кейса пропадает.

## Дерево тест-кейсов

Поиск референса и дублей (Шаг 0 скилла):

```
GET /api/testcasetree/entity?projectId={PROJECT_ID}&treeId={TREE_ID}&search=<base64>&parentNodeId=0
    &page=0&size=100&sort=nodeSortOrder,asc&sort=name,asc&deleted=false
    search = base64( [{"id":"name","value":"<подстрока>","type":"string"}] )
    → {"content":[{"id","name","type":"GROUP"|"LEAF"}]}
    вглубь: parentNodeId=<id группы> + path=<id,id,... через запятую>
GET  /api/testcasetree/countleaves?projectId={PROJECT_ID}&treeId={TREE_ID}&search=<base64>&deleted=false
POST /api/testcasetree/runstats
```

`treeId` — id дерева проекта: открыть дерево в UI и взять из адресной строки или из запросов
страницы в DevTools. Уровни дерева задаются custom fields (типичная схема — Feature → Story → Layer).

⚠️ **`search` фильтрует и ЛИСТЬЯ, а не только группы.** По подстроке в ветке из 10 кейсов видны
только те, чьё ИМЯ её содержит, — остальные существуют, но скрыты. Найдя группу, раскрывать её
ПОВТОРНЫМ запросом с пустым `search`. Готовая обёртка: `allure_tc.py find "<подстрока>"`.

## Custom fields

Дерево TestOps строится из них.

```
GET  /api/testcase/{id}/cfv        → [{customField:{id,name},id,name}]   (/customfield — 404)
POST /api/testcase/{id}/cfv        [{"customField":{"id":<fieldId>},"id":<valueId>}]   # значения ИЗ ЭТАЛОНА
```

Id полей Feature/Story/Layer и id их значений смотреть в ответе `GET …/cfv` соседнего кейса.
Значения копировать с соседа: самодельные кладут кейс в корень дерева. Готовая обёртка:
`allure_tc.py cf <новый> --from-etalon <сосед>`. Инструмент `set_test_case_custom_fields`
MCP-сервера Allure работает корректно (ADD-семантика).

## Связи с Jira

```
GET  /api/testcase/{id}/issue   → привязанные задачи
POST /api/testcase/{id}/issue   [{"name":"PROJ-123","url":"{JIRA_URL}/browse/PROJ-123","integrationId":{JIRA_INTEGRATION_ID}}]
     # семантика REPLACE: тело = новый полный список связей
```

❌ Инструмент `set_test_case_issues` MCP-сервера Allure привязал к кейсу ВЕСЬ справочник задач
проекта (сотни связей разом). Связи ставить только REST-вызовом выше; он же чинит последствия —
POST-replace с одним элементом.

Элементы без `integrationId` молча игнорируются. `integrationId` — в настройках интеграций
TestOps или в `GET /api/testcase/{id}/issue` любого кейса, уже связанного с Jira.

## Поиск

```
GET /api/testcase/__search?projectId={PROJECT_ID}&rql=...
  rql: name ~= "строка"        # оператор ~ (без =) → 400 invalid.aql
       cf["Story"] = "..."     # надёжно для веток дерева
```

Кириллица в `name ~=` работает. RQL ищет только по имени: сценарные кейсы с описательными
названиями по имени метода не находятся — смотреть содержимое ветки дерева целиком.

## Bearer-обмен

Если `Api-Token` где-то не принимается:

```
POST /api/uaa/oauth/token   (x-www-form-urlencoded)  grant_type=apitoken&scope=openid&token=<APITOKEN>
→ access_token (живёт ~16 ч)
```
