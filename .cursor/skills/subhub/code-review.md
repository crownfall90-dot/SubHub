# Code Review — SubHub

Используй при review PR, diff или когда пользователь просит проверить изменения.

## Чеклист (скопируй и отмечай)

```
Review Progress:
- [ ] Область изменений определена (GUI / Flipkart / GGSELL / Grizzly / bot / infra)
- [ ] Diff минимален — нет «заодно» рефакторинга menu.py/app.py
- [ ] Архитектурные запреты не нарушены
- [ ] Секреты и runtime не в diff
- [ ] Thread/async safety
- [ ] Windows / кодировка / батники
- [ ] OTA (_UPDATE_FILES) если добавлены файлы — см. [ota.md](ota.md)
- [ ] Новые config-ключи в .example — см. [config.md](config.md)
- [ ] Тест-план понятен
```

## 🔴 Критично — блокирует merge

### Архитектура

- [ ] `grizzly.py` **не импортирует** `menu.py`
- [ ] **Нет Playwright** в `grizzly.py` (daemon thread)
- [ ] `bot.py` — **нет** `import menu` на уровне модуля (только `_m()`)
- [ ] Тяжёлая работа в GUI **не блокирует** UI thread (subprocess/thread)

### Безопасность

- [ ] Нет `config.yaml`, `secrets.yaml`, `cards.json` в diff
- [ ] Нет API-ключей, токенов, номеров карт в коде/логах
- [ ] Нет `data/`, `chrome_profiles*/`, `debug/` скриншотов

### Корректность

- [ ] Логика OTP/VPN/оплаты не сломана на happy path
- [ ] Exit code **42** сохранён там, где нужен перезапуск
- [ ] Новые файлы добавлены в `_UPDATE_FILES` (если должны обновляться OTA)

## 🟡 Важно — желательно исправить

### Стиль и scope

- [ ] Изменения локальны — не переименованы сотни строк без причины
- [ ] Новые цвета GUI не дублируют существующие константы `BG_*` / `ACCENT`
- [ ] Комментарии только для неочевидной логики (VPN tiers, OTP timeouts)
- [ ] Имена и паттерны совпадают с окружающим кодом

### Async / threading

- [ ] Playwright вызывается из правильного event loop
- [ ] Shared state между потоками защищён (lock/queue)
- [ ] Daemon threads не держат ресурсы после shutdown

### Конфиг

- [ ] Новые настройки — в `config.yaml.example` / `secrets.yaml.example` с placeholder
- [ ] Defaults не ломают существующие установки без миграции

## 🟢 Nice to have

- [ ] Обновлён README если меняется UX запуска или конфиг
- [ ] `smoke_test.py` покрывает новый CLI-флаг (если добавлен)
- [ ] Ошибки логируются через loguru / `_log_err`, не `except: pass`

## Review по модулям

### app.py

| Проверить | Почему |
|-----------|--------|
| `_start_run()` / subprocess | Логи должны идти в `automation.log` |
| Sidebar / сервисы | Статусы синхронны с `bot.py` / `grizzly` |
| Закрытие окна | Трей vs quit — не убить бота случайно |
| `os._exit(42)` после OTA | Иначе пользователь на старой версии |

### menu.py

| Проверить | Почему |
|-----------|--------|
| VPN lifecycle | Подключение только на время сценария |
| Профили Chrome | Правильная папка: active/done/used/backup |
| Селекторы | Prefer `config.yaml` selectors over hardcode |
| `_UPDATE_FILES` | Новые deployable файлы |

### grizzly.py / bot.py

| Проверить | Почему |
|-----------|--------|
| Import graph | Нет циклов grizzly ↔ menu |
| `_ggsel_status` / `_tg_status` | GUI индикаторы |
| Throttled logging | Не засыпать консоль в цикле 5 сек |

### ggsell/

| Проверить | Почему |
|-----------|--------|
| `emit_ggs_notify()` | Единая точка уведомлений |
| `manual_confirm` | Осознанное изменение автовыдачи |
| Webhook handler | SHA256 secret из secrets |

## Формат feedback

```markdown
## Summary
[1–2 предложения: что делает PR и общая оценка]

## 🔴 Critical
- [файл:строка] описание проблемы → предложение fix

## 🟡 Suggestions
- ...

## 🟢 Nice to have
- ...

## Test plan
- [ ] ...
```

## Быстрые red flags в diff

| Паттерн в diff | Риск |
|----------------|------|
| `import menu` в `grizzly.py` | Deadlock |
| `async_playwright` в `grizzly.py` | Hang |
| `CTkButton` без thread offload | GUI freeze |
| Hardcoded token/key | Leak |
| Удаление `chcp 65001` / UTF-8 env | Кириллица |
| Большой rename в `menu.py` | Merge hell, скрытые баги |
| Файл не в `_UPDATE_FILES` | OTA gap |
