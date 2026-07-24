---
name: magic-mcp
description: >-
  21st.dev Magic MCP — генерация React/UI-компонентов через /ui в Cursor.
  Применяется при веб-UI, React, Tailwind, лендингах, dashboard в браузере,
  «сделай как v0», компоненты из 21st.dev. SubHub GUI (console) — только
  как референс; код пиши в subhub/menu.py вручную. Требует API key и MCP в .cursor/mcp.json.
---

# Magic MCP (21st.dev)

Upstream: [21st-dev/magic-mcp](https://github.com/21st-dev/magic-mcp) → `vendor/magic-mcp/`.

## Предварительные условия

1. Node.js 18+ (LTS)
2. API key: [21st.dev/magic](https://21st.dev/magic) → Magic Console
3. MCP включён в `.cursor/mcp.json` (см. `.cursor/mcp.json.example`)

Установка одной командой:

```powershell
npx @21st-dev/cli@latest install cursor --api-key <KEY>
```

Или скрипт проекта: `.\scripts\setup_magic_mcp.ps1`

## Когда использовать

| Задача | Инструмент |
|--------|------------|
| Палитра, UX, design system | **UI/UX Pro Max** (`search.py --design-system`) |
| Hero / motion prompt | **MotionSites** (`vendor/motionsites_prompts/`) |
| Готовый React/Tailwind компонент | **Magic MCP** (`/ui ...`) |

**SubHub `subhub/menu.py` (console)** — Magic не пишет в Tkinter напрямую. Бери идеи/структуру из сгенерированного React, переноси токены и layout в CTk.

## Workflow в чате

1. Убедись, что MCP `@21st-dev/magic` подключён (Customize → MCP).
2. Запрос вида: `/ui create a modern settings panel with dark theme`
3. Magic создаёт компонент в проекте (React + TS).
4. Для лендинга: сначала Pro Max + MotionSites prompt, потом `/ui` с контекстом.

## Ручная конфигурация MCP

Скопируй блок из `.cursor/mcp.json.example` в `.cursor/mcp.json` и подставь ключ:

```json
"@21st-dev/magic": {
  "command": "npx",
  "args": ["-y", "@21st-dev/magic@latest"],
  "env": {
    "API_KEY": "<YOUR_21ST_MAGIC_API_KEY>"
  }
}
```

`.cursor/mcp.json` в `.gitignore` — ключи не коммитить.

## Обновление

```powershell
git submodule update --init --remote vendor/magic-mcp
```

или `.\scripts\update_vendor_refs.ps1`

## Ограничения

- Beta; лимиты генераций на плане 21st.dev.
- Только файлы, связанные с сгенерированными компонентами.
- Не вызывать Magic для правок `menu.py`, `grizzly.py`, Playwright-логики.
