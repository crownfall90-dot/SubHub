# OTA — обновления SubHub

## Два пути обновления

| Условие | Метод | Функция |
|---------|-------|---------|
| Есть `.git/` + git в PATH | `git fetch` + `git merge --ff-only` | `_do_git_update()` |
| ZIP без git / git недоступен | HTTP raw GitHub | `_http_do_update()` |

Обе ветки вызываются из `_do_git_update()` в `menu.py`. GUI делегирует в `menu.py`.

## Проверка обновлений

- `_http_check_updates()` — GitHub API commits vs local SHA
- Local SHA: `.git/refs/heads/master` → `packed-refs` → `._update_sha`
- Фон: `_check_updates_bg()`, `_update_notify_loop()`
- Telegram: `_notify_tg_update(commits)`

## Список OTA-файлов — `_UPDATE_FILES`

**Любой новый deployable файл → добавь в список**, иначе коллеги на ZIP-установке не получат его.

Текущий список (`menu.py` ~532):

```
README.md
menu.py, bot.py, main.py, app.py
menu.bat, app.bat, app_launch.vbs, create_shortcut.bat, create_shortcut.vbs
grizzly_sms.py, proxy.py, grizzly.py, deepseek.py, requirements.txt, .gitignore
config.yaml.example, secrets.yaml.example, secrets1.yaml.example
assets/app.ico, assets/subhub_icon.png
ggsell/__init__.py, ggsell/bot_ggsell.py, ggsell/client.py, ggsell/gui_orders.py
ggsell/monitor.py, ggsell/deepseek_orders.py
```

### НЕ в OTA (обновляются отдельно)

| Путь | Как обновить |
|------|--------------|
| `vpn_extension/` | `git pull` (в репо, но не HTTP OTA) |
| `config.yaml`, `secrets.yaml` | Локально пользователя |
| `data/`, профили Chrome | Runtime |
| `.cursor/` | Только для разработчиков с git |

## HTTP-скачивание

```
https://raw.githubusercontent.com/{owner}/{repo}/master/{fname}
```

- Owner/repo: `.git/config` или fallback `crownfall90-dot/flipkart-automation`
- Token: `secrets.yaml` → `github.token` (для приватного репо / rate limit)
- `.bat` файлы: нормализация **CRLF** при сохранении

После успешного HTTP update → обновляется `._update_sha`.

## После обновления

1. `_init_secrets()` — восстановление ключей
2. `_migrate_config()` — новые ключи из example
3. **Перезапуск** — exit code **42**

```python
_exit_code[0] = 42
sys.exit(42)  # menu.bat / app.bat --console → restart loop
```

GUI: `_needs_restart_for_update()` — кнопка перезапуска если файлы обновлены но процесс старый.

## Чеклист разработчика (OTA)

```
- [ ] Изменённый файл в _UPDATE_FILES (если должен доставляться коллегам)
- [ ] Новая зависимость → requirements.txt (уже в списке)
- [ ] Новый config-ключ → config.yaml.example + _migrate_config
- [ ] .bat с LF-only не сломает Windows (HTTP path нормализует CRLF)
- [ ] После merge в master — коллеги видят коммиты в «Проверить обновления»
- [ ] Критичное изменение → пользователь должен перезапустить (42)
```

## GUI: инструменты обновления

`app.py`:
- `_tool_check_updates()` / `_tool_check_updates_now()`
- Badge уведомлений: `_refresh_update_badge()`
- Список коммитов: `_update_commits_text()`

## Типичные проблемы

| Симптом | Причина | Fix |
|---------|---------|-----|
| «Уже последняя версия» после push | Local SHA совпал | Нормально; или `._update_sha` устарел |
| Файл не обновился у коллеги | Не в `_UPDATE_FILES` | Добавить в список, push master |
| git merge failed | Локальные правки | `git stash` / resolve; HTTP path не поможет при конфликте |
| 401 GitHub API | Нет `github.token` | secrets.yaml |
| Старая версия после OTA | Не перезапустили | exit 42 или вручную закрыть SubHub |

## Связь с git workflow

Разработчик с git: `git pull` эквивалентен OTA для tracked файлов + получает `vpn_extension/`, `.cursor/`.

Коллега с ZIP: только `_UPDATE_FILES` через HTTP.
