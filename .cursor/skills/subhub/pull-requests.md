# Pull Requests — SubHub

Используй когда пользователь просит создать PR, подготовить pull request или описание для GitHub.

## Перед созданием PR

Параллельно:

```powershell
git status
git diff
git branch -vv
git log -10 --oneline
git diff master...HEAD
```

Проверь:
- Нет секретов/runtime в diff ([config.md](config.md))
- Новые deployable файлы в `_UPDATE_FILES` ([ota.md](ota.md))
- Архитектурные запреты ([code-review.md](code-review.md))

## Workflow

1. Убедись, что ветка содержит все нужные коммиты
2. Push (только по просьбе пользователя):

```powershell
git push -u origin HEAD
```

3. Создай PR через `gh`:

```powershell
gh pr create --title "Заголовок" --body "$(cat <<'EOF'
## Summary
- ...

## Test plan
- [ ] menu.bat
- [ ] python scripts/smoke_test.py
- [ ] ...

EOF
)"
```

4. Верни URL PR пользователю

## Заголовок PR

Как commit messages ([commits.md](commits.md)):
- Повелительное наклонение / краткое описание пользы
- Scope: `GUI:`, `GGSell`, `VeepN/Flipkart`, `SubHub:`

## Test plan — шаблон по областям

### GUI (`subhub/menu.py`)

- [ ] `menu.bat` — окно, sidebar, трей
- [ ] Закрытие: quit / tray / cancel
- [ ] Статусы VPN / Grizzly / GGSell синхронны

### Flipkart / Playwright

- [ ] `python scripts/smoke_test.py`
- [ ] `python scripts/run_to_payment.py` (если затронут flow)
- [ ] VPN подключён — нет Access Denied

### GGSELL

- [ ] Заказ появляется в GUI
- [ ] Уведомление в Telegram (если бот настроен)
- [ ] Webhook (если менялся handler)

### Grizzly / bot

- [ ] Перезапуск SubHub — бот стартует
- [ ] Нет deadlock при старте (import graph)

### OTA / infra

- [ ] Новые файлы в `_UPDATE_FILES`
- [ ] `config.yaml.example` обновлён
- [ ] `.bat` CRLF ок на Windows

## Summary — примеры

### Bugfix

```markdown
## Summary
- Fix VPN sidebar stuck on "connecting" during extension bootstrap
- Sync Grizzly cancel button visibility with active number count

## Test plan
- [ ] menu.bat → YouTube → VPN → статус OK после bootstrap
- [ ] Grizzly: кнопка отмены скрыта без активных номеров
```

### Feature

```markdown
## Summary
- Add three-option close dialog (quit, tray, cancel)
- Persist choice in data/app_settings.json

## Test plan
- [ ] Закрыть окно → три варианта работают
- [ ] Повторный запуск — выбор запомнен
```

## Чего не делать

- Не push без просьбы
- Не force push на master
- Не включать `data/`, `config.yaml`, `secrets.yaml` в PR
- Не создавать пустой PR без коммитов

## После merge в master

Коллеги на git: `git pull`.  
Коллеги на ZIP: GUI → Проверить обновления → перезапуск (42).

Убедись, что `_UPDATE_FILES` покрывает все изменённые deployable файлы.
