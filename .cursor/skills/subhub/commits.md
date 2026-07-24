# Git — commit messages для SubHub

Используй когда пользователь просит commit, сообщение к коммиту или перед `git commit`.

## Формат

Команда пишет **на английском** (как в истории `master`) или **на русском** — следуй последним коммитам в ветке. По умолчанию для этого репо:

```
<краткий заголовок в повелительном наклонении, ≤72 символа>

[опционально: 1–2 предложения «зачем», не перечисление файлов]
```

### Стиль заголовка (из истории репо)

| Тип | Примеры |
|-----|---------|
| Fix | `Fix stuck VPN sidebar status during extension bootstrap.` |
| Add | `Add three-option close dialog: quit, tray, or cancel.` |
| GUI | `GUI: VPN/Grizzly статусы, фоновый режим и скрытие лишних кнопок` |
| Feature area | `VeepN/Flipkart: стабильный VPN, India-сервер и сценарий до оплаты` |
| SubHub prefix | `SubHub: живой статус запуска, плавный UI и понятная панель GGSELL` |

**Правила:**
- Повелительное наклонение: Fix / Add / Hide / Sync — не «Fixed» / «Adding»
- Заголовок — **почему/что для пользователя**, не список файлов
- Одна логическая задача на коммит
- Точка в конце заголовка — опционально (в репо встречается и так, и так)

## Чего не коммитить

```
config.yaml
secrets.yaml
cards.json
data/
chrome_profiles*/
debug/
*.log
cookies_backup/
._update_sha
```

Если пользователь просит закоммитить секреты — **предупреди** и не добавляй без явного подтверждения.

## Workflow агента

1. Параллельно: `git status`, `git diff`, `git log -10 --oneline`
2. Проверь diff на секреты и runtime
3. Один commit = одна цель
4. Commit только **по явной просьбе** пользователя
5. Не push без просьбы; не `--no-verify`; не amend unless rules allow

## Примеры

### Bugfix GUI

```
Fix Grizzly cancel button visibility when no active numbers.

Hide the control on idle state so sidebar matches backend.
```

### Flipkart / VPN

```
VeepN/Flipkart: resilient navigation after VPN connect.

Retry _force_navigate_flipkart when Access Denied clears post-VPN.
```

### GGSELL

```
Sync GGSell monitor with background mode setting.

Pause polling when user disables background work in app settings.
```

### Мелкий fix

```
Destroy Grizzly cancel button when no active numbers.
```

### Русский заголовок (допустимо в репо)

```
Профили: интерактивное меню, статусы и список на весь экран
```

## Scope по областям (для заголовка)

| Область | Ключевые слова в заголовке |
|---------|---------------------------|
| GUI | `GUI:`, `UI:`, `SubHub:` |
| Flipkart/VPN | `VeepN/Flipkart:`, `Flipkart`, `VPN` |
| Grizzly | `Grizzly`, `SMS` |
| GGSELL | `GGSell`, `GGSELL` |
| Telegram | `Telegram`, `bot` |
| Infra | `OTA`, `menu.bat`, `smoke_test` |

## PR

Полный workflow: [pull-requests.md](pull-requests.md)

Краткий шаблон body:

```markdown
## Summary
- ...

## Test plan
- [ ] menu.bat — окно и трей
- [ ] python scripts/smoke_test.py
- [ ] [специфично для изменения]
```
