---
name: subhub
description: Навигирует по SubHub — Flipkart/YouTube Premium, GGSELL, Telegram, GrizzlySMS/PVAPins, DeepSeek, Playwright, OTA, config/secrets. Применяется при правках subhub/menu.py/bot.py/main.py/grizzly.py/ggsell/, отладке VPN/OTP, code review, commit/PR, онбординге, menu.bat, config.yaml, data/.
---

# SubHub — навык проекта

## Контекст

**SubHub** — консольная автоматизация для команды: YouTube Premium через Flipkart, маркетплейс GGSELL, Telegram-бот, DeepSeek. Стек: Python 3.10+, Playwright, httpx, loguru, PyYAML.

Репозиторий: [SubHub](https://github.com/crownfall90-dot/SubHub)

## Маршрутизация задач

| Ключевые слова | Модуль | Workflow |
|----------------|--------|----------|
| Flipkart, OTP, селектор, профиль Chrome, VPN, покупка | `subhub/menu.py` / `main.py` | [flipkart-playwright.md](flipkart-playwright.md) |
| заказ GGSELL, шаблон, webhook, monitor | `subhub/ggsell/` | [ggsell.md](ggsell.md) |
| Telegram, уведомление, бот-команда | `subhub/bot.py` | [grizzly-telegram.md](grizzly-telegram.md) |
| виртуальный номер, Grizzly, PVAPins, SMS | `grizzly.py` / `*_sms.py` / `sms_failover.py` | [grizzly-telegram.md](grizzly-telegram.md) |
| DeepSeek, пополнение API | `subhub/deepseek.py` | [deepseek.md](deepseek.md) |
| config, secrets, селектор, таймаут | `config.yaml` / `secrets.yaml` | [config.md](config.md) |
| OTA, обновление, _UPDATE_FILES | `subhub/menu.py` | [ota.md](ota.md) |
| запуск, перезапуск, exit 42, smoke | `menu.bat` / `scripts/` | [troubleshooting.md](troubleshooting.md) |
| не работает, ошибка, Access Denied | — | [troubleshooting.md](troubleshooting.md) |
| установка, первый запуск | — | [first-run.md](first-run.md) |
| code review / commit / PR | — | [code-review.md](code-review.md), [commits.md](commits.md), [pull-requests.md](pull-requests.md) |

**Неочевидные случаи:**
- Уведомление о заказе → `ggsell/monitor.py` (`emit_ggs_notify`) → `bot.py`
- Playwright flow входа → `main.py` (`LoginAutomation`); массовые сценарии → `menu.py`
- Секреты → `secrets.yaml`
- Новый deployable файл → `_UPDATE_FILES` в `menu.py`

## Архитектура

| Файл / папка | Роль |
|--------------|------|
| `menu.bat` / `python -m subhub` | Точка входа (консоль) |
| `subhub/menu.py` | Ядро — Flipkart, профили, VPN, покупки, OTA |
| `subhub/main.py` | Playwright login |
| `subhub/bot.py` | Telegram-бот; lazy `_m()` → menu |
| `subhub/grizzly.py` | GrizzlySMS фон (**без** menu import) |
| `subhub/*_sms.py`, `sms_failover.py` | SMS-провайдеры |
| `subhub/deepseek.py` | DeepSeek (**без** menu import) |
| `subhub/ggsell/` | GGSELL: client, monitor, bot_ggsell, deepseek_orders |
| `scripts/` | smoke / утилиты |
| `data/` | Runtime (не в git) |
| `chrome_profiles*/` | Профили Chrome (не в git) |

### Критичные ограничения

1. **`grizzly.py` не импортирует `menu.py`** — deadlock.
2. **`grizzly.py` не использует Playwright**.
3. **`bot.py` не импортирует `menu` на уровне модуля** — только `_m(name)`.
4. **`deepseek.py` не импортирует `menu.py`**.
5. **Минимальный diff** — `menu.py` огромный; только затронутая логика.

## Запуск

| Команда | Назначение |
|---------|------------|
| `menu.bat` | Консоль; exit **42** → restart |
| `python -m subhub` | То же |
| `python scripts/test_console_smoke.py` | Smoke |

`pip install -r requirements.txt` + `python -m playwright install chromium`

## Конфигурация

| Файл | В git | Шаблон |
|------|-------|--------|
| `config.yaml` | Нет | `config.yaml.example` |
| `secrets.yaml` | Нет | `secrets.yaml.example` |
| `cards.json` / `data/cards.json` | Нет | консоль → Карты |

## Чего избегать

- Рефакторинг всего `menu.py` без запроса
- `import menu` в `grizzly.py` / `deepseek.py`
- Коммит `data/`, профилей, логов, `debug/`, `secrets.yaml`
- Возвращать удалённый CustomTkinter GUI (`app.py`)
