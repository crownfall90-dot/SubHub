# Примеры задач для агента

Конкретные сценарии: куда идти, что читать, как проверить.

## GUI: новая кнопка в разделе YouTube

1. Прочитать [gui-debug.md](gui-debug.md) — дизайн-система
2. Найти `_build_youtube` или аналог в `app.py`
3. Скопировать паттерн соседнего `CTkButton`
4. Если кнопка запускает automation → `_start_run()` или существующий handler
5. Проверка: `app.bat --console`

## Flipkart: сломался селектор OTP

1. [flipkart-playwright.md](flipkart-playwright.md)
2. Открыть `config.yaml` → `selectors.otp_field`, `otp_submit_button`
3. При необходимости — Playwright inspector / скриншот `debug/`
4. Минимальный diff в config, не в menu.py
5. Проверка: `python scripts/run_to_payment.py`

## Flipkart: Access Denied после обновления

1. [troubleshooting.md](troubleshooting.md)
2. `vpn_extension/` на месте? `git pull` для dev
3. GUI → VPN → Проверить
4. Код: VPN lifecycle в `menu.py` — только на время сценария
5. Проверка: run_to_payment с VPN

## GGSELL: заказ не появляется в GUI

1. [ggsell.md](ggsell.md)
2. `secrets.yaml` → ggsel credentials
3. `data/app_settings.json` — background mode
4. `ggsell/monitor.py` — polling, `gui_notify_queue`
5. `automation.log` — ошибки API
6. Проверка: перезапуск SubHub, новый тестовый заказ

## GGSELL: новый шаблон сообщения

1. `data/ggsel_templates.json` — формат существующих
2. UI редактирования в `app.py` (поиск ggsel_templates)
3. Использование в `ggsell/bot_ggsell.py` или monitor
4. Не коммить data/ — только код UI/логики

## Grizzly: кнопка отмены в GUI

1. [grizzly-telegram.md](grizzly-telegram.md)
2. `app.py` → `_update_grizzly_cancel_btn`, `_set_grizzly_cancel_status`
3. Статус из `grizzly.py` — без Playwright там
4. Синхронизация visible/hidden с active_total
5. Проверка: app.bat --console, сценарий с номером

## Telegram: новая команда бота

1. `bot.py` — найти соседние command handlers
2. Данные через `_m("function_name")` — **не** `import menu`
3. Статус `_tg_status` при ошибках конфига
4. Проверка: перезапуск, /команда в Telegram

## OTA: добавлен новый Python-модуль

1. [ota.md](ota.md)
2. Добавить путь в `_UPDATE_FILES` в `menu.py`
3. Если нужна зависимость → `requirements.txt` (уже в списке)
4. Push master
5. Проверка: HTTP path на машине без git

## Config: новый таймаут в grizzlysms

1. [config.md](config.md)
2. Добавить в `config.yaml.example` с default
3. Читать в `grizzly_sms.py` / `grizzly.py` / `menu.py` — как соседние ключи
4. `_migrate_config` подхватит при старте
5. Не хардкодить магическое число

## DeepSeek: пополнение падает на Pay

1. [deepseek.md](deepseek.md)
2. `deepseek.py` — locators формы, `DEBUG_DIR` скриншоты
3. Карта в `cards.json`
4. Профиль в `chrome_profiles_deepseek/`
5. Проверка: GUI → DeepSeek или прямой вызов модуля

## Code review перед merge

1. [code-review.md](code-review.md) — чеклист
2. `git diff master...HEAD`
3. Red flags: grizzly+menu, secrets в diff, нет _UPDATE_FILES
4. Feedback в формате 🔴🟡🟢

## Commit по просьбе пользователя

1. [commits.md](commits.md)
2. `git status`, `git diff`, `git log -10`
3. Исключить secrets/data из staging
4. Один логический commit
5. Commit только по явной просьбе

## PR по просьбе пользователя

1. [pull-requests.md](pull-requests.md)
2. Test plan под область изменений
3. `gh pr create`
4. Вернуть URL

## Помощь коллеге с установкой

1. [first-run.md](first-run.md)
2. Проверить Python, secrets, VPN, smoke_test
3. Не коммить их локальные config/secrets
