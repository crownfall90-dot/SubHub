---
name: motionsites
description: >-
  Локальная библиотека MotionSites (vendor/motionsites_prompts). ОБЯЗАТЕЛЬНО при
  задачах про красивый UI, лендинг, hero, анимации, Framer Motion, scroll-эффекты,
  glassmorphism, 3D-секции, редизайн SubHub/app.py, Canvas, веб-дизайн. Сначала
  Read prompt из vendor/, адаптируй под стек проекта (CustomTkinter — упрощённо;
  веб/React — как в промпте).
---

# MotionSites — дизайн и анимации

Правило `.cursor/rules/motionsites-prompts.mdc` (`alwaysApply: true`) — дублирует триггеры.

## Где лежит

```
vendor/motionsites_prompts/
  catalog.json          # каталог slug / title / category / tier
  *.md                  # готовые промпты (текст с motionsites.ai)
```

## Workflow

1. По задаче выбери промпт: `Read vendor/motionsites_prompts/catalog.json` или `INDEX.md`.
2. `Read vendor/motionsites_prompts/<slug>.md` — секция **Prompt**.
3. Адаптируй под контекст:
   - **SubHub GUI** (`app.py`, CustomTkinter) — возьми палитру, иерархию, motion-идеи; не тащи React один в один.
   - **Веб / Canvas / лендинг** — React + Tailwind + Framer Motion как в промпте.
4. Если файла нет — `python scripts/fetch_motionsites_prompts.py --slug <slug>` или импорт вручную.

## Пополнение библиотеки

| Команда | Когда |
|---------|--------|
| `--free` | все free-tier из catalog.json |
| `--slug X` | один промпт |
| `--visible --wait-login 120` | premium после входа на motionsites.ai |
| `scripts/import_motionsites_prompt.ps1` | вставили текст из буфера вручную |

## Стек по умолчанию в промптах MotionSites

React, Vite, Tailwind CSS, Framer Motion — для веба. Для десктопа SubHub — переноси визуальную логику, не копируй JSX слепо.
