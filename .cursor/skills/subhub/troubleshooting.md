# Troubleshooting — SubHub

Быстрая диагностика по симптомам. Подробные workflow — в тематических файлах.

## Flipkart / VPN

| Симптом | Вероятная причина | Действие |
|---------|-------------------|----------|
| Access Denied | VPN выкл / не India | GUI → VPN → Проверить; `vpn_extension/` |
| OTP timeout | Grizzly баланс / tiers | `config.yaml` grizzlysms; логи automation.log |
| Селектор не найден | Flipkart сменил UI | `config.yaml` → selectors |
| Профиль «битый» | Неверная папка профиля | active/done/used/backup — [flipkart-playwright.md](flipkart-playwright.md) |
| Зависание навигации | VPN bootstrap | Недавние fix в app.py VPN status |

**Логи:** `automation.log`, `debug/`

**Команды:**
```powershell
python menu.py
python scripts/run_to_payment.py
```

## GrizzlySMS

| Симптом | Причина | Действие |
|---------|---------|----------|
| Number unavailable | Нет слотов +91 | price_tiers, max_price |
| Insufficient balance | Мало на балансе Grizzly | Пополнить API |
| Зависание фона | Playwright в grizzly.py | **Запрещено** — kill chrome + rmtree |
| Import deadlock | grizzly import menu | Убрать import |

**Файлы:** `grizzly.py`, `grizzly_sms.py`, `secrets.yaml`

## Telegram / bot

| Симптом | Причина | Действие |
|---------|---------|----------|
| Бот не отвечает | Неверный token / 401 | secrets.yaml → telegram.token |
| Два бота | Два SubHub | Один экземпляр в трее |
| Нет уведомлений GGSELL | Очередь / webhook | [ggsell.md](ggsell.md) |
| OTP через TG не приходит | mode не telegram | config otp.mode |

## GGSELL

| Симптом | Причина | Действие |
|---------|---------|----------|
| «Не настроен» | secrets ggsel пуст | api_key, seller_id |
| Заказы не в GUI | monitor не запущен | background mode в app_settings |
| Webhook 403 | webhook_secret | secrets.yaml ggsel.webhook_secret |

## GUI / запуск

| Симптом | Причина | Действие |
|---------|---------|----------|
| Нет окна | pythonw тихо упал | app.bat --console |
| GUI freeze | Блокировка UI thread | subprocess в _start_run |
| Нет трея | pystray | pip install pystray Pillow |
| Кракозябры | UTF-8 | app.bat chcp/PYTHONUTF8 |
| Старая версия после update | Не перезапуск | exit 42 — [gui-debug.md](gui-debug.md) |

## OTA / git

| Симптом | Причина | Действие |
|---------|---------|----------|
| Файл не обновился | Не в _UPDATE_FILES | [ota.md](ota.md) |
| git merge fail | Локальные правки | stash / resolve |
| 401 GitHub | Нет github.token | secrets.yaml |

## Конфиг

| Симптом | Причина | Действие |
|---------|---------|----------|
| Ключи слетели | Пересоздан config | _init_secrets восстановит из secrets |
| Новый параметр missing | Старый config | _migrate_config при старте |
| YOUR_* в логах | Не заполнен secrets | [first-run.md](first-run.md) |

## Диагностика агентом — порядок

1. Определи модуль ([SKILL.md](SKILL.md) маршрутизация)
2. Прочитай хвост `automation.log`
3. Проверь `data/heartbeat_*.json` — процесс жив?
4. `python scripts/smoke_test.py`
5. `app.bat --console` для GUI-проблем
6. Проверь secrets/config не с плейсхолдерами

## Файлы логов и runtime

| Файл | Что смотреть |
|------|--------------|
| `automation.log` | Flipkart, subprocess, menu |
| `data/runtime_state.json` | Состояние сессии |
| `data/heartbeat_app.json` | GUI alive |
| `data/app_settings.json` | background mode, UI prefs |
| `debug/*.png` | Скриншоты ошибок (локально) |

Не коммить логи и data в git.
