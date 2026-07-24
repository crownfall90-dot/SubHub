# GGSELL

## Модули (`subhub/ggsell/`)

| Файл | Роль |
|------|------|
| `client.py` | REST API (`GGSellClient`) |
| `monitor.py` | `GGSellMonitor` — опрос заказов |
| `bot_ggsell.py` | Telegram + webhook |
| `deepseek_orders.py` | DeepSeek-специфика |

## Очередь уведомлений

```python
# monitor.py
notify_queue       # → bot.py (Telegram)
emit_ggs_notify()  # кладёт в notify_queue
```

Новое событие → `emit_ggs_notify`, не пиши в очередь напрямую из случайных мест.

## Конфигурация

- Ключи → `secrets.yaml` (ggsel)
- Шаблоны → `data/ggsel_templates.json`
- Webhook: `ggsel.webhook_port` > 0 → aiohttp в `bot.py`; fail-closed без `sha256`

## Типичные задачи

| Задача | Куда |
|--------|------|
| Новый статус / автоответ | `monitor.py` |
| Telegram-команда | `bot_ggsell.py` |
| Шаблон | `data/ggsel_templates.json` + консоль |
| Webhook | `bot_ggsell.py` |

## OTA

Файлы в `_UPDATE_FILES` под путями `subhub/ggsell/...`.
