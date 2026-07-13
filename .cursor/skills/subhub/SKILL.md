---
name: subhub
description: Навигирует по SubHub — Flipkart/YouTube Premium, GGSELL, Telegram, GrizzlySMS, DeepSeek, Playwright, CustomTkinter, OTA, config/secrets. Применяется при правках app.py/menu.py/bot.py/grizzly.py/ggsell/deepseek.py, отладке VPN/OTP, code review, commit/PR, онбординге коллег, app.bat, config.yaml, data/ и любых задачах в flipkart-automation.
---

# SubHub — навык проекта

## Контекст

**SubHub** — Windows-десктоп для команды: YouTube Premium через Flipkart, маркетплейс GGSELL, Telegram-бот, DeepSeek. Стек: Python 3.10+, Playwright, CustomTkinter, httpx, loguru, PyYAML.

Репозиторий: [flipkart-automation](https://github.com/crownfall90-dot/flipkart-automation)

## Маршрутизация задач

| Ключевые слова | Модуль | Workflow |
|----------------|--------|----------|
| кнопка, экран, трей, sidebar, тема | `app.py` | [gui-debug.md](gui-debug.md) |
| Flipkart, OTP, селектор, профиль Chrome, VPN, покупка | `menu.py` / `main.py` | [flipkart-playwright.md](flipkart-playwright.md) |
| заказ GGSELL, шаблон, webhook, monitor | `ggsell/` | [ggsell.md](ggsell.md) |
| Telegram, уведомление, бот-команда | `bot.py` | [grizzly-telegram.md](grizzly-telegram.md) |
| виртуальный номер, Grizzly, refund, SMS API | `grizzly.py` / `grizzly_sms.py` | [grizzly-telegram.md](grizzly-telegram.md) |
| DeepSeek, пополнение API, kling | `deepseek.py` | [deepseek.md](deepseek.md) |
| config, secrets, селектор, таймаут, migrate | `config.yaml` / `secrets.yaml` | [config.md](config.md) |
| OTA, обновление, _UPDATE_FILES, git pull | `menu.py` | [ota.md](ota.md) |
| запуск, перезапуск, exit 42, smoke | батники / `scripts/` | [gui-debug.md](gui-debug.md) |
| не работает, ошибка, Access Denied, зависает | — | [troubleshooting.md](troubleshooting.md) |
| установка, первый запуск, онбординг | — | [first-run.md](first-run.md) |
| code review, PR, проверка diff | любой | [code-review.md](code-review.md) |
| commit, git commit, сообщение коммита | — | [commits.md](commits.md) |
| pull request, gh pr | — | [pull-requests.md](pull-requests.md) |
| пример, как сделать, типичная задача | — | [examples.md](examples.md) |

**Неочевидные случаи:**
- Логика покупки в GUI → сначала `menu.py`, потом привязка в `app.py`
- Уведомление о заказе → `ggsell/monitor.py` (`emit_ggs_notify`) → `bot.py` / `app.py`
- Playwright flow входа → `main.py` (`LoginAutomation`); массовые сценарии → `menu.py`
- Секреты → `secrets.yaml`; `_init_secrets` подставляет в `config.yaml` при старте
- Новый deployable файл → `_UPDATE_FILES` в `menu.py`

## Архитектура

| Файл / папка | Роль |
|--------------|------|
| `app.py` | GUI (CustomTkinter), трей, OTA-обновления, точка входа |
| `menu.py` | **Ядро** — Flipkart, профили, VPN, покупки, OTA, config migrate |
| `main.py` | Playwright login (`LoginAutomation`, `BrowserProfileManager`) |
| `bot.py` | Telegram-бот; lazy `_m()` → menu |
| `grizzly.py` | GrizzlySMS фон (**без** menu import, **без** Playwright) |
| `grizzly_sms.py` | HTTP-клиент GrizzlySMS |
| `deepseek.py` | DeepSeek Platform (**без** menu import) |
| `ggsell/` | GGSELL: client, monitor, bot_ggsell, gui_orders |
| `proxy.py`, `deepseek.py` | Вспомогательные интеграции |
| `scripts/` | `smoke_test.py`, `run_to_payment.py` |
| `data/` | Runtime (не в git) |
| `vpn_extension/` | VPN; **только git pull**, не HTTP OTA |
| `chrome_profiles*/` | Профили Chrome (не в git) |

### Критичные ограничения

1. **`grizzly.py` не импортирует `menu.py`** — deadlock.
2. **`grizzly.py` не использует Playwright** — kill chrome + `shutil.rmtree`.
3. **`bot.py` не импортирует `menu` на уровне модуля** — только `_m(name)`.
4. **`deepseek.py` не импортирует `menu.py`** — самостоятельный модуль.
5. **Минимальный diff** — `menu.py` / `app.py` огромные; только затронутая логика.

## Workflow агента

```
- [ ] Модуль определён (таблица маршрутизации)
- [ ] Workflow-файл прочитан
- [ ] Окружающий код прочитан
- [ ] Секреты/runtime не в diff
- [ ] _UPDATE_FILES если новый deployable файл
- [ ] Проверка запуском
```

### После изменений

| Тип | Проверка |
|-----|----------|
| GUI | `app.bat --console` |
| Flipkart | `python menu.py` / `scripts/smoke_test.py` |
| grizzly/bot | перезапуск SubHub, `automation.log` |
| OTA/батники | exit **42** → автоперезапуск |

## Запуск

| Команда | Назначение |
|---------|------------|
| `app.bat` | GUI без консоли |
| `app.bat --console` | GUI + консоль; exit **42** → restart |
| `menu.bat` | Консольное меню |
| `python scripts/smoke_test.py` | Smoke-тест |
| `create_shortcut.bat` | Ярлык на рабочем столе |

`pip install -r requirements.txt` + `python -m playwright install chromium`

## Конфигурация

| Файл | В git | Шаблон |
|------|-------|--------|
| `config.yaml` | Нет | `config.yaml.example` |
| `secrets.yaml` | Нет | `secrets.yaml.example` |
| `cards.json` | Нет | GUI → Карты |

Подробно: [config.md](config.md). Flipkart без VPN → Access Denied.

## Стиль кода

- Python 3.10+, `pathlib.Path`, `os.chdir(_HERE)` в app.py
- Логи: loguru (Playwright); `automation.log` через `_TeeWriter`
- GUI: константы `BG_*`, `ACCENT`, `FONT_*` — не плодить цвета
- Windows: батники, `pythonw`, VBS; `PYTHONUTF8=1`, `chcp 65001`

## Чего избегать

- Рефакторинг всего `menu.py` / `app.py` без запроса
- `import menu` в `grizzly.py` / `deepseek.py`
- Playwright в daemon-потоках grizzly
- Коммит `data/`, профилей, логов, `debug/`
- Markdown в корне репо без запроса
- Commit/push **без явной просьбы** пользователя

## Полный индекс workflow

| Тема | Файл |
|------|------|
| Flipkart, Playwright, VPN, профили | [flipkart-playwright.md](flipkart-playwright.md) |
| GGSELL | [ggsell.md](ggsell.md) |
| GrizzlySMS, Telegram | [grizzly-telegram.md](grizzly-telegram.md) |
| DeepSeek | [deepseek.md](deepseek.md) |
| config.yaml, secrets.yaml | [config.md](config.md) |
| OTA, _UPDATE_FILES | [ota.md](ota.md) |
| GUI, запуск, exit 42 | [gui-debug.md](gui-debug.md) |
| Диагностика ошибок | [troubleshooting.md](troubleshooting.md) |
| Первый запуск коллеги | [first-run.md](first-run.md) |
| Примеры задач агента | [examples.md](examples.md) |
| Code review | [code-review.md](code-review.md) |
| Commit messages | [commits.md](commits.md) |
| Pull requests | [pull-requests.md](pull-requests.md) |
| Модули, data/, справочник | [reference.md](reference.md) |
