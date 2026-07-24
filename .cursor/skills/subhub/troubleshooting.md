# Troubleshooting — SubHub

## Flipkart / VPN

| Симптом | Вероятная причина | Действие |
|---------|-------------------|----------|
| Access Denied | VPN выкл / не India | Консоль → VPN; проверить расширение |
| OTP timeout | Баланс / tiers SMS | secrets + config sms/grizzlysms/pvapins |
| Селектор не найден | Flipkart сменил UI | `config.yaml` → selectors |
| Профиль «битый» | Неверная папка | chrome_profiles* — [flipkart-playwright.md](flipkart-playwright.md) |

**Логи:** `data/automation.log`, `debug/`

```powershell
python -m subhub
python scripts/run_to_payment.py
```

## SMS (Grizzly / PVAPins)

| Симптом | Причина | Действие |
|---------|---------|----------|
| Number unavailable | Нет слотов +91 | tiers / другой провайдер **[М]** |
| Insufficient balance | $0 на API | Пополнить |
| Import deadlock | grizzly import menu | Убрать import |

## Telegram / bot

| Симптом | Причина | Действие |
|---------|---------|----------|
| Бот не отвечает | token / 401 | secrets.yaml |
| Два бота | Две консоли | Один `menu.bat` |
| Нет уведомлений GGSELL | очередь / webhook | [ggsell.md](ggsell.md) |

## GGSELL

| Симптом | Причина | Действие |
|---------|---------|----------|
| «Не настроен» | secrets ggsel пуст | api_key, seller_id |
| Webhook 403 | webhook_secret | secrets.yaml |

## Запуск / OTA

| Симптом | Причина | Действие |
|---------|---------|----------|
| Кракозябры | UTF-8 | `menu.bat` уже ставит UTF-8 |
| Старая версия после update | Не перезапуск | exit 42 |
| Файл не обновился OTA | Не в `_UPDATE_FILES` | [ota.md](ota.md) |

## Диагностика

1. Модуль по [SKILL.md](SKILL.md)
2. Хвост `data/automation.log`
3. `data/heartbeat_console.json` / `runtime_state.json`
4. `python scripts/test_console_smoke.py`
5. secrets/config без `YOUR_*`
