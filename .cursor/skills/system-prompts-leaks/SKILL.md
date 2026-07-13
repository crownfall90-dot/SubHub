---
name: system-prompts-leaks
description: >-
  Локальный справочник system prompt'ов (vendor/system_prompts_leaks). ОБЯЗАТЕЛЬНО
  применяй при любом вопросе про Cursor, Claude Code, Codex, ChatGPT, Gemini,
  Copilot, Grok, system prompt, инструкции агента, skills/rules/hooks, сравнение
  поведения ИИ-агентов — сначала Read из vendor/, потом ответ. Также при настройке
  .cursor/skills и .cursor/rules в этом репо.
---

# System Prompts Leaks

Правило `.cursor/rules/system-prompts-leaks.mdc` (`alwaysApply: true`) дублирует триггеры — соблюдай оба.

## Путь

`vendor/system_prompts_leaks/` — git submodule.

## Workflow

1. По продукту из вопроса выбери файл (таблица в rule или README).
2. `Read` файл — цитируй по факту, помечай если снимок мог устареть.
3. Submodule не редактировать.

## Обновление

`scripts/update_system_prompts_leaks.ps1` или `git submodule update --remote vendor/system_prompts_leaks`
