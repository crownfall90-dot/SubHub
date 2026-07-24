# Примеры задач для агента

## Flipkart: сломался селектор OTP

1. [flipkart-playwright.md](flipkart-playwright.md)
2. `config.yaml` → `selectors`
3. При необходимости — скриншот `debug/`
4. Минимальный diff
5. Проверка: `python scripts/run_to_payment.py`

## Flipkart: Access Denied

1. [troubleshooting.md](troubleshooting.md)
2. VPN в консоли / расширение
3. Код VPN lifecycle в `subhub/menu.py`

## GGSELL: заказ не доходит в Telegram

1. [ggsell.md](ggsell.md)
2. `secrets.yaml` → ggsel
3. `emit_ggs_notify` / `notify_queue` → `bot.py`
4. `data/automation.log`

## GGSELL: новый шаблон

1. `data/ggsel_templates.json`
2. Использование в `bot_ggsell.py` / monitor
3. Не коммить `data/`

## Telegram: новая команда

1. `subhub/bot.py` — соседние handlers
2. Данные через `_m("...")` — **не** `import menu`
3. Перезапуск консоли, проверка в TG

## OTA: новый Python-модуль

1. Файл в `subhub/`
2. Добавить в `_UPDATE_FILES`
3. [ota.md](ota.md)

## Config: новый ключ

1. `config.yaml.example` + migrate в `menu.py`
2. [config.md](config.md)
