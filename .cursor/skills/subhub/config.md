# Конфигурация — config.yaml и secrets.yaml

## Два файла — две роли

| Файл | Содержимое | В git | Шаблон |
|------|------------|-------|--------|
| `config.yaml` | Поведение, таймауты, селекторы, browser | Нет | `config.yaml.example` |
| `secrets.yaml` | API-ключи, токены, GGSELL, GitHub | Нет | `secrets.yaml.example` |
| `cards.json` | Банковские карты | Нет | через GUI → Карты |

**Правило:** секреты живут в `secrets.yaml`. `config.yaml` — настройки и селекторы.

## Как они связаны (`menu.py`)

При каждом запуске:

1. `_init_secrets()` — если нет `config.yaml` → копия из `.example`
2. Ключи из `secrets.yaml` **подставляются** в `config.yaml` (grizzlysms, telegram)
3. `_migrate_config()` — новые ключи из `config.yaml.example` **добавляются** в существующий config без перезаписи пользовательских значений

При добавлении нового параметра в example:
- Обнови `config.yaml.example` (или `secrets.yaml.example`)
- `_migrate_config` подтянет defaults при следующем запуске / OTA

## secrets.yaml — секции

```yaml
grizzlysms:
  api_key: ...           # дублируется в config при старте
telegram:
  token: ...             # бот ([@BotFather](https://t.me/BotFather))
github:
  token: ...             # OTA для ZIP-установок без .git
ggsel:
  api_key: ...
  seller_id: ...
  webhook_port: 8765     # 0 = выключено
  webhook_secret: ""     # SHA256 из GGSell → Уведомления
```

Дополнительные ключи (promo_code и т.д.) — смотри `secrets.yaml.example` и использование в `app.py` / `ggsell/`.

**Читатели secrets:**
- `menu.py` → `_SECRETS` (кэш, единый источник)
- `grizzly.py` → читает напрямую (без import menu)
- `bot.py` → токен только из secrets
- `main.py` → secrets перекрывают плейсхолдеры config

## config.yaml — секции

| Секция | Назначение |
|--------|------------|
| `site` | URL Flipkart login |
| `browser` | headless, profiles_dir, timeout, profile_max_age_days |
| `human_behavior` | Задержки между действиями, typing, между аккаунтами |
| `grizzlysms` | service `xt`, country `22`, price_tiers, таймауты poll/buy |
| `selectors` | Playwright-селекторы (пустые → fallback в коде) |
| `otp` | mode: `api` / `manual` / `telegram`; wait_timeout (мс) |
| `telegram_otp` | wait_timeout (сек) для OTP через бот |
| `captcha` | mode: `manual` |
| `vpn` | provider: `veepn` |
| `auto_accounts` | Число аккаунтов в автопрогоне |
| `max_concurrent_accounts` | Параллельность |

Полный шаблон: `config.yaml.example`.

## Селекторы

```yaml
selectors:
  username_field: input[placeholder*='Mobile']
  submit_button: button:has-text('CONTINUE'), ...
  otp_field: input[type='text']
  otp_submit_button: button:has-text('VERIFY'), ...
```

При смене вёрстки Flipkart — **сначала** правь `config.yaml`, не хардкодь в Python.

Комментарии по селекторам в конце `main.py` (~строка 3018).

## grizzlysms.price_tiers

```yaml
price_tiers:
  - max_price: 0.07
    duration: 8    # сек ждать дешевле
  - max_price: 0.16
    duration: 0    # купить сразу по max_price
```

Бизнес-логика tier'ов — неочевидна; документируй изменения в комментарии.

## Плейсхолдеры (не реальные ключи)

```
YOUR_GRIZZLYSMS_API_KEY
YOUR_TELEGRAM_BOT_TOKEN
YOUR_API_КЛЮЧ
ВАШ_*
```

`_init_secrets` и `_check_setup` считают их пустыми.

## GUI → Настройки

`app.py` открывает файлы:
- `🔑 secrets.yaml` → `_open_secrets`
- `⚙️ config.yaml` → `_open_config`

При первом запуске без `secrets.yaml` — предупреждение в лог GUI.

## Чеклист при добавлении настройки

```
- [ ] Ключ в config.yaml.example или secrets.yaml.example
- [ ] Чтение в коде с fallback/default
- [ ] _migrate_config подхватит новый ключ (если в example)
- [ ] Не захардкожен секрет
- [ ] Документирован в README только если меняется UX онбординга
```

## Чего не делать

- Не коммить `config.yaml`, `secrets.yaml`, `cards.json`
- Не дублировать секреты в коде или логах
- Не класть webhook_secret в config.yaml — только secrets
- Не удалять `_init_secrets` / `_migrate_config` вызовы при старте
