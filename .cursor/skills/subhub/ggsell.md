# GGSELL

## Модули

| Файл | Роль |
|------|------|
| `ggsell/client.py` | REST API (`GGSellClient`) |
| `ggsell/monitor.py` | `GGSellMonitor` — опрос заказов, очереди уведомлений |
| `ggsell/bot_ggsell.py` | Обработка в Telegram, webhook handler |
| `ggsell/gui_orders.py` | UI заказов в `app.py` |
| `ggsell/deepseek_orders.py` | DeepSeek-специфика |

## Очереди уведомлений

```python
# ggsell/monitor.py
notify_queue       # → bot.py (Telegram)
gui_notify_queue   # → app.py (GUI toast/список)
emit_ggs_notify()  # кладёт в обе очереди
```

Новое уведомление о заказе → вызывай `emit_ggs_notify`, не пиши в очереди напрямую из случайных мест.

## Конфигурация

- API-ключи и seller ID → `secrets.yaml` (секция ggsel)
- Шаблоны сообщений продавца → `data/ggsel_templates.json` (runtime, не в git)
- Webhook: `ggsel.webhook_port` > 0 в config → aiohttp в `bot.py`
- Webhook URL в боте: `{webhook_url}/ggsel/notify` (`bot_ggsell.py`)
- Webhook: **fail-closed** — без `sha256` или при ошибке верификации → `403` (`_verify_ggsell_webhook`)

## Типичные задачи

| Задача | Куда |
|--------|------|
| Новый статус заказа / автоответ | `ggsell/monitor.py` |
| Telegram-команда для заказов | `ggsell/bot_ggsell.py` |
| Кнопка / таблица заказов в GUI | `ggsell/gui_orders.py` + привязка в `app.py` |
| Шаблон сообщения | `data/ggsel_templates.json` + UI в app |
| Webhook payload | `bot_ggsell.py` → `make_webhook_handler` |

## manual_confirm

В `monitor.py`: `manual_confirm=True` (по умолчанию) — только эмит в `notify_queue`, без автовыдачи. Меняй осознанно: влияет на автоматизацию выдачи.

## Статусы в bot.py

```python
_ggsel_status  # "" | "ok" | "error:..."
```

GUI читает статус для индикатора сервиса. При ошибках API — обновляй `_ggsel_status`, не только логируй.

## OTA

Файлы GGSELL в `_UPDATE_FILES`:
`ggsell/__init__.py`, `bot_ggsell.py`, `client.py`, `gui_orders.py`, `monitor.py`, `deepseek_orders.py`

После OTA с изменением GGSELL — перезапуск SubHub (exit 42 или вручную).

## Отладка

1. Проверить `secrets.yaml` → ggsel credentials
2. Логи в `automation.log` — фильтр по `ggsel` / `GGSell`
3. GUI → раздел GGSELL → список заказов
4. Telegram: статус `_ggsel_status` в `bot.py`

## Перед commit (GGSELL)

- [ ] `secrets.yaml.example` если новые ключи webhook
- [ ] `emit_ggs_notify` для новых событий
- [ ] Файлы ggsell/* в `_UPDATE_FILES`
- [ ] См. [commits.md](commits.md), [code-review.md](code-review.md)
