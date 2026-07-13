# SubHub — справочник

## Индекс workflow

| Тема | Файл |
|------|------|
| Flipkart, Playwright, профили | [flipkart-playwright.md](flipkart-playwright.md) |
| GGSELL | [ggsell.md](ggsell.md) |
| GrizzlySMS, Telegram | [grizzly-telegram.md](grizzly-telegram.md) |
| DeepSeek | [deepseek.md](deepseek.md) |
| config.yaml, secrets.yaml | [config.md](config.md) |
| OTA, _UPDATE_FILES | [ota.md](ota.md) |
| GUI, запуск, отладка | [gui-debug.md](gui-debug.md) |
| Диагностика | [troubleshooting.md](troubleshooting.md) |
| Онбординг | [first-run.md](first-run.md) |
| Примеры задач | [examples.md](examples.md) |
| Code review | [code-review.md](code-review.md) |
| Commits | [commits.md](commits.md) |
| Pull requests | [pull-requests.md](pull-requests.md) |

## Python-модули

### app.py (~6100 строк)

- Класс `SubHubApp(ctk.CTk)` — главное окно, sidebar, сервисы (`youtube`, `ggsell`, `deepseek`, `kling`)
- `LogSink` — очередь логов для GUI
- `main()` — bootstrap, single-instance, трей (`pystray`)
- Импортирует `grizzly` лениво для SMS/GGSELL-фона
- OTA-обновления делегирует функциям из `menu.py`
- Перезапуск: `os._exit(42)`

### menu.py (~14600 строк)

- Консольное меню + **вся бизнес-логика** Flipkart
- Директории профилей: `chrome_profiles`, `chrome_profiles_done`, `chrome_profiles_used`, `chrome_profiles_backup`
- `_TeeWriter` → `automation.log`
- ANSI-цвета для консоли (Windows VT)
- `_UPDATE_FILES` — список файлов для OTA с GitHub `master`
- Точка входа: `if __name__ == "__main__"` — интерактивное меню

### main.py (~3000 строк)

- `ConfigManager` — чтение `config.yaml`
- `BrowserProfileManager` — изолированные профили Chrome
- `HumanBehavior` — задержки, имитация человека
- `LoginAutomation` — Playwright async flow
- `TelegramBotManager` — подписчики в `data/tg_subscribers.json`, stats в `data/tg_stats.json`
- `ResultTracker` — результаты прогонов

### bot.py (~4500 строк)

- Фоновый Telegram-бот, статусы `_tg_status`, `_ggsel_status`
- Webhook GGSELL (aiohttp) при `ggsel.webhook_port` > 0
- Ленивый `_m(name)` → атрибуты `menu`
- Обновления: `_update_available`, уведомления в Telegram

### grizzly.py (~1430 строк)

- Daemon-thread фоновые задачи GrizzlySMS
- `_transient_print` — временные сообщения в консоли
- `_log_err` — throttled ошибки (раз в 60 сек на ключ)
- **Запрет**: import `menu`, Playwright API
- Очистка: kill chrome + `shutil.rmtree` профиля

### grizzly_sms.py

- `GrizzlySMSClient` — buy number, poll OTP, cancel/refund
- Исключения: `GrizzlySMSError`, `NumberUnavailableError`, `InsufficientBalanceError`

### ggsell/

| Файл | Назначение |
|------|------------|
| `client.py` | REST API GGSell (`GGSellClient`) |
| `monitor.py` | `GGSellMonitor` — опрос заказов, очереди `notify_queue`, `gui_notify_queue` |
| `bot_ggsell.py` | Обработка заказов в Telegram-контексте |
| `gui_orders.py` | UI заказов (вызывается из app) |
| `deepseek_orders.py` | DeepSeek-специфика заказов |

### deepseek.py

- Самостоятельный модуль (не import menu) — пополнение platform.deepseek.com
- Профили: `chrome_profiles_deepseek/`
- GUI: `app.py` → sidebar `deepseek`
- Подробно: [deepseek.md](deepseek.md)

## Батники

```
app.bat          → app_launch.vbs (pythonw) или --console loop
app_launch.vbs   → скрытый pythonw app.py, fallback python
menu.bat         → python menu.py
create_shortcut.bat → ярлык с assets/app.ico
```

## data/ (runtime, .gitignore)

Создаётся автоматически. Типичные файлы:

| Файл | Назначение |
|------|------------|
| `app_settings.json` | Настройки GUI (автозапуск, тема и т.д.) |
| `runtime_state.json` | Состояние сессии |
| `heartbeat_app.json` | Heartbeat GUI |
| `heartbeat_console.json` | Heartbeat консоли |
| `ggsel_templates.json` | Шаблоны сообщений продавца |
| `tg_subscribers.json` | Подписчики Telegram |
| `tg_stats.json` | Статистика SMS/логинов |
| `_vpn_ping_profile/` | Профиль для проверки VPN |

## scripts/

- `smoke_test.py` — быстрая проверка импортов и базового flow
- `run_to_payment.py` — прогон до экрана оплаты (отладка)

## Зависимости (requirements.txt)

```
httpx, loguru, playwright, pyyaml, openpyxl
customtkinter, pystray, Pillow
```

Playwright: `python -m playwright install chromium`

## Код выхода 42

Сигнал перезапуска для `app.bat --console` и `menu.bat`:

```bat
if "%EX%"=="42" goto restart
```

Используется после OTA-обновления или «перезапустить консоль».

## Безопасность (.gitignore)

Не коммить: `config.yaml`, `secrets.yaml`, `cards.json`, `data/`, `chrome_profiles*/`, `debug/`, `*.log`, `cookies_backup/`

## Частые проблемы

Полная таблица: [troubleshooting.md](troubleshooting.md)

| Симптом | Действие |
|---------|----------|
| Flipkart Access Denied | VPN: `vpn_extension/`, GUI → VPN |
| Зависание Grizzly | Нет Playwright в `grizzly.py` |
| Два бота | Один экземпляр SubHub |
| Import deadlock | grizzly/deepseek ↔ menu |
| OTA gap | Файл не в `_UPDATE_FILES` |

## Cursor / плагины

Правило `.cursor/rules/subhub-cursor-plugins.mdc`: для этого стека дополнительные MCP не обязательны; предлагать Prisma/Notion/Figma/Datadog только по запросу.

## Связанные workflow-файлы skill

| Файл | Содержимое |
|------|------------|
| [code-review.md](code-review.md) | Чеклист review, red flags, формат feedback |
| [commits.md](commits.md) | Commit messages, примеры, .gitignore для коммитов |
| [flipkart-playwright.md](flipkart-playwright.md) | 6-шаговая отладка Flipkart/VPN |
| [gui-debug.md](gui-debug.md) | app.bat, GUI, OTA |
| [ggsell.md](ggsell.md) | Заказы, webhook, шаблоны |
| [grizzly-telegram.md](grizzly-telegram.md) | SMS API, bot, import graph |
