# GrizzlySMS и Telegram

## GrizzlySMS

### Файлы

| Файл | Роль |
|------|------|
| `grizzly_sms.py` | `GrizzlySMSClient` — buy, poll OTP, cancel/refund |
| `grizzly.py` | Фоновый daemon-thread: монитор номеров, оплата, возвраты |
| `config.yaml` | `grizzlysms.*` — api_key, price_tiers, таймауты |

### Исключения (`grizzly_sms.py`)

- `GrizzlySMSError` — общая ошибка API
- `NumberUnavailableError` — номер недоступен
- `InsufficientBalanceError` — нет баланса

### price_tiers

```yaml
price_tiers:
  - max_price: 0.07
    duration: 8    # секунд ждать дешевле
  - max_price: 0.16
    duration: 0    # купить сразу
```

Логика tier'ов — неочевидная бизнес-логика; комментируй при изменении.

### Жёсткие запреты в grizzly.py

1. **Нет `import menu`** — deadlock import lock
2. **Нет Playwright API** — не thread-safe между event loops
3. Очистка браузера: kill chrome process + `shutil.rmtree` профиля

### Логирование в grizzly.py

- `_transient_print` — временные сообщения в консоль
- `_log_err` — throttled ошибки (раз в 60 сек на ключ)

## Telegram-бот

### Файлы

| Файл | Роль |
|------|------|
| `bot.py` | Фоновый бот, webhook GGSELL, lazy `_m()` |
| `main.py` | `TelegramBotManager` — подписчики, stats |
| `secrets.yaml` | `telegram.token` |
| `data/tg_subscribers.json` | Подписчики |
| `data/tg_stats.json` | Статистика SMS/логинов |

### Lazy import menu

```python
def _m(name):
    import sys as _s, importlib as _i
    mod = _s.modules.get("menu") or _i.import_module("menu")
    return getattr(mod, name)
```

**Никогда** не добавляй `import menu` на уровне модуля в `bot.py`.

### Глобальные статусы

```python
_tg_status    # "not_configured" | "ok" | ...
_ggsel_status # "" | "ok" | "error:..."
```

### Webhook GGSELL

При `ggsel.webhook_port` > 0 — aiohttp сервер в `bot.py`, handler из `ggsell/bot_ggsell.py` → `make_webhook_handler`.

### OTP через Telegram

`config.yaml` → `otp.mode: telegram`, `telegram_otp.wait_timeout`.

## Связь grizzly ↔ bot ↔ menu

```
menu.py  ──(subprocess/async Playwright)──► Flipkart
    ▲
    │ _m() lazy
bot.py  ──► notify_queue ◄── ggsell/monitor.py
    │
grizzly.py (отдельно, без menu import)
    └── grizzly_sms.py API
```

При добавлении фичи: определи, в каком потоке она живёт. Playwright — только main/menu; фон SMS — grizzly; уведомления — bot.

## Отладка

| Проблема | Действие |
|----------|----------|
| Бот не отвечает | `secrets.yaml` token; один экземпляр SubHub |
| Два бота конфликтуют | Закрыть дубликат в трее |
| Grizzly зависает | Убедиться, что нет Playwright в `grizzly.py` |
| Import deadlock | Проверить цепочку imports grizzly ↔ menu |
| Нет уведомлений GGSELL | `notify_queue`, webhook port, `_ggsel_status` |

## Перед commit (Grizzly / Telegram)

- [ ] Нет `import menu` в `grizzly.py`
- [ ] Нет Playwright в `grizzly.py`
- [ ] `bot.py` — только `_m()` для menu
- [ ] См. [commits.md](commits.md), [code-review.md](code-review.md)
