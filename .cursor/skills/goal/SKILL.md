---
name: goal
description: Автономный цикл /goal — правка, проверка, ревью, лимит 15 итераций. Use when user says /goal or asks to iterate until done.
---

# /goal — автономная работа до результата

## Когда использовать

- Пользователь: `/goal <цель> | <как понять что готово>`
- «Крути сам», «сделай и проверь», «повторяй пока не заработает» (с лимитом!)

## Обязательные правила

Читай и соблюдай **всегда**:

- `.cursor/rules/agent-verify-and-limits.mdc`
- `.cursor/rules/goal-autonomous-loop.mdc`

## Быстрый чеклист SubHub

```bash
python -m py_compile <changed.py>
python scripts/smoke_test.py
```

## Финальные ревью (обязательно)

1. `bugbot` subagent — `Diff: uncommitted changes`
2. `security-review` subagent — `Diff: uncommitted changes`
3. Сверка с планом: таблица пункт → статус

## Лимит

**15 итераций** → стоп + отчёт «где застрял». Не превышать без явного разрешения пользователя.
