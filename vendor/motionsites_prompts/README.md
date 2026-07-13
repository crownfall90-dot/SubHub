# MotionSites — локальная библиотека промптов

Промпты с [motionsites.ai](https://motionsites.ai) для генерации красивых лендингов, hero-секций и анимаций в Cursor.

## Быстрый старт

```powershell
# скачать все free-tier (уже в catalog.json)
python scripts/fetch_motionsites_prompts.py --free

# один промпт
python scripts/fetch_motionsites_prompts.py --slug bold-studio

# premium (войти на сайт в открывшемся браузере)
python scripts/fetch_motionsites_prompts.py --slug loader-animation --visible --wait-login 120
```

## Импорт вручную

```powershell
.\scripts\import_motionsites_prompt.ps1 -Slug my-design -Title "My Design"
# вставьте промпт из буфера когда скрипт попросит
```

## Для агента Cursor

Skill: `/motionsites`  
Rule: `motionsites-prompts.mdc` (always on)

## Каталог

См. `catalog.json`. Список скачанных файлов — `INDEX.md` (генерируется скриптом fetch).
