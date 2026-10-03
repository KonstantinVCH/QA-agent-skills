# Доступ к vault: MCP, Local REST API, файлы

## Содержание

1. Obsidian MCP (`mcp-obsidian`)
2. Local REST API напрямую (curl)
3. Прямой доступ к файлам
4. База в git-репозитории, Obsidian не установлен
5. Известные ловушки

---

## 1. Obsidian MCP (`mcp-obsidian`)

Цепочка: Obsidian (открыт) → плагин **Local REST API** (слушает `127.0.0.1:27124`) →
MCP-сервер `mcp-obsidian` → инструменты `obsidian_*` у агента.

| Компонент | Зачем | Признак работы |
|---|---|---|
| Obsidian, открыт с нужным vault | хост vault | окно запущено |
| Плагин Local REST API | HTTP-доступ к vault | `curl -k https://127.0.0.1:27124/` отвечает `"status": "OK"` |
| MCP-сервер `mcp-obsidian` | инструменты `obsidian_*` | `obsidian_list_files_in_vault` отвечает |
| Smart Connections / Copilot (опц.) | семантический поиск по embeddings | — |

Пример подключения в Claude Code (`.mcp.json` проекта или пользовательский конфиг):

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "uvx",
      "args": ["--with", "mcp<2.0.0", "mcp-obsidian"],
      "env": {
        "OBSIDIAN_API_KEY": "${OBSIDIAN_API_KEY}",
        "OBSIDIAN_HOST": "127.0.0.1",
        "OBSIDIAN_PORT": "27124"
      }
    }
  }
}
```

Ключ API — в настройках плагина Local REST API. Храните его в переменной окружения
`OBSIDIAN_API_KEY`, не в файле конфигурации под git.

Инструменты:

| Инструмент | Для чего |
|---|---|
| `obsidian_list_files_in_vault` | preflight, корень vault |
| `obsidian_list_files_in_dir` | содержимое папки |
| `obsidian_get_file_contents` | прочитать страницу |
| `obsidian_batch_get_file_contents` | прочитать несколько страниц разом |
| `obsidian_simple_search` | полнотекстовый поиск; задавайте узкий запрос и малый `context_length` |
| `obsidian_complex_search` | поиск JsonLogic (по тегам, путям, frontmatter) |
| `obsidian_append_content` | дописать в конец файла (создаёт файл, если его нет) |
| `obsidian_patch_content` | вставка относительно заголовка, блока или поля frontmatter |
| `obsidian_put_content` | создать или полностью перезаписать файл |
| `obsidian_delete_file` | удалить — только по явной просьбе пользователя |

## 2. Local REST API напрямую

Если MCP не подключён, а плагин работает, — тот же доступ через `curl`. По умолчанию
HTTPS на порту 27124 с самоподписанным сертификатом (`-k`); HTTP на 27123 включается
в настройках плагина. Полная документация API — по ссылке из настроек плагина
(проект `coddingtonbear/obsidian-local-rest-api`).

```bash
H="Authorization: Bearer $OBSIDIAN_API_KEY"
B="https://127.0.0.1:27124"

curl -sk "$B/"                                         # статус, без авторизации
curl -sk -H "$H" "$B/vault/"                           # корень vault
curl -sk -H "$H" "$B/vault/wiki/entities/"             # содержимое папки
curl -sk -H "$H" "$B/vault/wiki/overview.md"           # прочитать файл
curl -sk -H "$H" -X POST "$B/search/simple/?query=order-service&contextLength=80"
curl -sk -H "$H" -X PUT  -H "Content-Type: text/markdown" --data-binary @page.md "$B/vault/wiki/concepts/x.md"   # создать/перезаписать
curl -sk -H "$H" -X POST -H "Content-Type: text/markdown" --data-binary @tail.md "$B/vault/wiki/log.md"         # дописать в конец
```

Пробелы и кириллицу в пути и в `query` кодируйте как в обычном URL (`%20`, percent-encoding UTF-8).

## 3. Прямой доступ к файлам

Vault — обычная папка с markdown. Если ни MCP, ни плагина нет, работайте с файлами
средствами агента (чтение, поиск, правка). Obsidian подхватывает изменения сам.

```bash
VAULT="${OBSIDIAN_VAULT:-$HOME/notes}"
grep -ril --include="*.md" "order-service" "$VAULT/wiki" "$VAULT/sources" "$VAULT/lessons"
grep -rn  --include="*.md" -i "idempotency" "$VAULT/wiki/concepts" | head -40
```

Правила для прямой записи:

- Frontmatter — валидный YAML между строками `---`. Битый frontmatter Obsidian
  показывает как текст, а поиск по `aliases` перестаёт находить страницу.
- Wikilink `[[entities/order-service|Order Service]]` — путь от корня vault без `.md`.
  Короткая форма `[[order-service]]` работает, только пока имя файла уникально в vault.
- Большие файлы (`wiki/index.md`) не перезаписывать целиком: добавлять строку точечной
  правкой в нужный раздел.
- Команда договорилась писать только через Obsidian (например, индекс ведёт плагин) —
  прямую запись не использовать, только чтение.

## 4. База в git-репозитории, Obsidian не установлен

Многие команды публикуют vault в git. Тогда **прочитать** знания можно всегда:
клонировать репозиторий или скачать архив ветки и искать локально.

```bash
git clone --depth 1 {WIKI_REPO_URL} wiki-kb && grep -ril "<термин>" wiki-kb/wiki wiki-kb/sources wiki-kb/lessons
```

Code search на self-hosted Bitbucket/GitLab часто не индексирован, поэтому веб-поиск по
репозиторию бесполезен: надёжнее архив и локальный `grep`.

В репозитории лежит версия на момент последнего мержа в основную ветку. Свежие находки
могут висеть в открытых PR: если знание ожидалось, но его нет, проверьте их.

Запись в такую базу — по правилам команды: ветка, коммит, PR. Рабочие папки сессий
(`{topic}/`, черновики) обычно закрыты `.gitignore` — не добавляйте их в коммит.

## 5. Известные ловушки

Проверено на `mcp-obsidian` 0.2.2 и плагине Local REST API 4.x (2026).

| Симптом | Причина | Что делать |
|---|---|---|
| MCP показывает `Failed to connect`, а `curl -k https://127.0.0.1:27124/` отвечает OK | `uvx` подтянул несовместимый `mcp` 2.x: `AttributeError: 'Server' object has no attribute 'list_tools'` | запуск `uvx --with "mcp<2.0.0" mcp-obsidian`; диагностику вести реальным вызовом инструмента, а не по сводке статуса |
| `WinError 10061` / `Connection refused` | Obsidian закрыт или плагин выключен | открыть Obsidian, включить плагин |
| `obsidian_get_recent_changes` → ошибка 40012 (invalid Content-Type) | несовместимость клиента и плагина | не использовать; свежие изменения смотреть по `log.md` или `git log` |
| `obsidian_patch_content` → ошибка 40084 «Markdown-Patch-Version header required» | новые версии плагина требуют заголовок, который клиент MCP не шлёт | обход: `obsidian_append_content` в конец файла с пометкой «К сортировке»; либо прямой `PATCH` через REST с этим заголовком по документации своей версии плагина; поле `updated` во frontmatter через MCP тогда не обновить — правьте файл напрямую |
| `obsidian_simple_search` вернул сотни тысяч символов, контекст переполнен | слишком широкий запрос (`api`, `сервис`) | искать по конкретным терминам, ставить малый `context_length` |

Инструмент патчинга не сработал на шаге, где он нужен (например, строка в `index.md`), —
зафиксируйте сбой как находку сессии в `wiki/log.md`, а не пропускайте шаг молча.
