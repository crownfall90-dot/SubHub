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
| Диагностика | [troubleshooting.md](troubleshooting.md) |
| Онбординг | [first-run.md](first-run.md) |
| Примеры задач | [examples.md](examples.md) |
| Code review / commits / PR | [code-review.md](code-review.md), [commits.md](commits.md), [pull-requests.md](pull-requests.md) |

## Python-модули (`subhub/`)

### menu.py

- Консольное меню + **вся бизнес-логика** Flipkart
- Директории профилей: `chrome_profiles*`
- `_TeeWriter` → `data/automation.log`
- `_UPDATE_FILES` — OTA с GitHub `master`
- Точка входа: `python -m subhub` → `menu`

### main.py

- `ConfigManager`, `BrowserProfileManager`, `LoginAutomation` (Playwright)
- Config/secrets/log — из корня репо (`paths.ROOT`)

### bot.py

- Фоновый Telegram-бот; lazy `_m(name)` → `menu`
- Webhook GGSELL (aiohttp) при `ggsel.webhook_port` > 0

### grizzly.py / *_sms.py / sms_failover.py

- Фон GrizzlySMS / PVAPins; **запрет** import `menu`, Playwright

### ggsell/

| Файл | Назначение |
|------|------------|
| `client.py` | REST API |
| `monitor.py` | опрос заказов, `notify_queue` → TG |
| `bot_ggsell.py` | обработка в Telegram |
| `deepseek_orders.py` | DeepSeek-заказы |

### deepseek.py

- Пополнение platform.deepseek.com; профили `chrome_profiles_deepseek/`

## Запуск

```
menu.bat              → python -m subhub
python -m subhub      → консоль
```

## data/ (runtime, .gitignore)

| Файл | Назначение |
|------|------------|
| `runtime_state.json` | PID автоматизации, restart |
| `heartbeat_console.json` | Heartbeat консоли |
| `ggsel_*.json` | Заказы / шаблоны GGSELL |
| `tg_subscribers.json` | Подписчики Telegram |
| `automation.log` | Единый журнал |

## Зависимости

`httpx`, `aiohttp`, `loguru`, `playwright`, `pyyaml`, `openpyxl`, `Pillow`

## Код выхода 42

Перезапуск `menu.bat` после OTA / «перезапустить консоль».

## Безопасность

Не коммить: `config.yaml`, `secrets.yaml`, `cards.json`, `data/`, `chrome_profiles*/`, `debug/`, `*.log`
