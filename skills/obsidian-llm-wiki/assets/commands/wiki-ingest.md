---
description: Записать находки сессии в базу знаний Obsidian (с cascade)
---
Активируй скилл `obsidian-llm-wiki`, режим **ingest**: запиши находки текущей сессии в vault по порядку — source-файл (`sources/{topic}--summary.md`) → entity → concept → cross-links В ОБЕ СТОРОНЫ → **cascade** (поиском обновить смежные страницы) → запись в `wiki/log.md` → строка в `wiki/index.md`. Перед созданием каждой страницы — поиск дублей. Существующие страницы обновлять, не перезаписывать; страницы с `reviewed: true` только дополнять.

Контекст / тема: $ARGUMENTS
