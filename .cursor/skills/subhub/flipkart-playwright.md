# Flipkart / Playwright — отладка и разработка

## Где править

| Задача | Файл |
|--------|------|
| Массовый сценарий, меню, профили, VPN | `menu.py` |
| Одиночный login flow, `HumanBehavior` | `main.py` |
| Селекторы, таймауты, OTP mode | `config.yaml` (из `config.yaml.example`) |
| Проверка до оплаты | `scripts/run_to_payment.py` |

## Workflow отладки (пошагово)

Скопируй чеклист:

```
Flipkart Debug:
- [ ] 1. Окружение (Python, Chrome, Playwright chromium)
- [ ] 2. config.yaml + secrets (grizzlysms, vpn)
- [ ] 3. VPN ping (GUI или smoke)
- [ ] 4. Изолированный прогон (menu / run_to_payment)
- [ ] 5. automation.log + debug/ скриншоты
- [ ] 6. Точечный fix → повтор с шага 4
```

### Шаг 1 — Окружение

```powershell
cd "D:\path\to\flipkart-automation"
python --version          # 3.10+
pip install -r requirements.txt
python -m playwright install chromium
```

Рекомендуется **Google Chrome** (не только Chromium) для расширения VeepN.

### Шаг 2 — Конфиг

| Проверка | Где |
|----------|-----|
| `grizzlysms.api_key` | `config.yaml` или `secrets.yaml` |
| `vpn.provider: veepn` | `config.yaml` |
| `browser.headless: false` | для визуальной отладки |
| `selectors.*` | пустые = fallback в коде |

Папка `vpn_extension/` должна быть в корне (не OTA — только `git pull`).

### Шаг 3 — VPN

**GUI:** YouTube → VPN → Проверить

**Консоль / smoke:**

```powershell
python scripts/smoke_test.py
```

Ищи в `automation.log`:
- `VeepN подключён` / `VeepN уже подключён` — OK
- `VPN не подключился` / `VPN: таймаут` — проблема расширения или India-сервера

Ключевые функции в `menu.py`:
- `_veepn_connect_js()` — JS для popup/service worker
- `_ensure_veepn_connected(context, quick=...)`
- `_navigate_flipkart_resilient()` — retry после VPN

Ping-профиль: `data/_vpn_ping_profile/`

### Шаг 4 — Прогоны

| Уровень | Команда | Когда |
|---------|---------|-------|
| Smoke | `python scripts/smoke_test.py` | После любых правок infra/log/VPN |
| Меню | `python menu.py` | Интерактив, выбор профиля/сценария |
| До оплаты | `python scripts/run_to_payment.py` | Полный путь Buy Now → адрес → checkout |
| С телефоном | `python scripts/run_to_payment.py --phone 91XXXXXXXXXX` | Конкретный профиль |
| Full cycle (boot) | `python menu.py --full-cycle --tariffs 3 --accounts 1 --headless` | Быстрый старт без покупки |

CLI `--fill-to-payment` обрабатывается в `menu.py` (~строка 14326).

### Шаг 5 — Логи и артеfacts

| Источник | Путь |
|----------|------|
| Консоль + файл | `automation.log` (`_TeeWriter` в menu.py) |
| Playwright | loguru в `main.py` |
| Скриншоты | `debug/`, `debug_*.png`, `viewcheckout_debug_*.png` |

При ошибке навигации смотри `_force_navigate_flipkart` (~2872) — возвращает `(ok, err)`.

### Шаг 6 — Типичные fix-петли

```
Access Denied → VPN → _ensure_veepn_connected → retry navigate
OTP timeout   → grizzlysms balance / price_tiers / poll_interval
Selector miss → config.yaml selectors → Flipkart DOM changed
Profile stuck → какая папка (active/done/used) → не копировать вслепую
Hang          → Playwright не в grizzly.py; один event loop
```

## config.yaml — ключевые секции

```yaml
site.url              # Flipkart login URL
browser.*             # headless, profiles_dir, timeout, profile_max_age_days
human_behavior.*      # задержки между действиями и аккаунтами
selectors.*           # login_button, username_field, otp_field, otp_submit_button
otp.mode              # api | manual | telegram
otp.wait_timeout      # мс ожидания OTP в браузере
grizzlysms.*          # API для +91 (service xt, country 22)
vpn.provider          # veepn
```

Селекторы пустые (`''`) → код использует встроенные fallback.

## Жизненный цикл Chrome-профилей

```
chrome_profiles/        → активные
chrome_profiles_done/   → успешно завершённые
chrome_profiles_used/   → использованные (record_<phone>_<ts>.json)
chrome_profiles_backup/ → бэкапы
```

Константы в `menu.py`: `PROFILES_DIR`, `DONE_PROFILES_DIR`, `USED_PROFILES_DIR`, `BACKUP_PROFILES_DIR`.

## OTP

| mode | Поведение |
|------|-----------|
| `api` | GrizzlySMS — номер + poll |
| `manual` | prompt в консоль |
| `telegram` | через бот (`telegram_otp.wait_timeout`) |

Таймауты: `otp.wait_timeout` (мс), `grizzlysms.get_number_timeout` (сек), `grizzlysms.number_lifetime_seconds`.

## Playwright-паттерны

- Async flow в `main.py` → `LoginAutomation`
- `menu.py` — subprocess / asyncio; копируй существующий вызов рядом с правкой
- `HumanBehavior` — задержки из `human_behavior.*`
- VPN подключается на время сценария, отключается после (см. коммиты «VPN только во время активного сценария»)

## smoke_test — что проверяет

1. `imports` — menu, app
2. `subprocess_stream` — логи subprocess в automation.log
3. `vpn_helpers` — `_veepn_connect_js`, India, `_navigate_flipkart_resilient`
4. `fill_to_payment_cli` — `--fill-to-payment`, py_compile menu.py
5. `open_chrome_flipkart` — VPN + Flipkart на done-профиле (skip если нет профилей)
6. `full_cycle_boot` — старт `--full-cycle` 12 сек

## Частые ошибки

| Симптом | Действие |
|---------|----------|
| Access Denied | VPN; `vpn_extension/` актуален; India server |
| OTP timeout | Баланс Grizzly; `price_tiers`; увеличить `wait_timeout` |
| Селектор не найден | `selectors.*` в config; Flipkart сменил вёрстку |
| Профиль «завис» | Проверить папку профиля; record JSON в used |
| Таймаут 150s smoke | Chrome не стартовал или VPN popup заблокирован |
| Checkout не открывается | `run_to_payment.py`; логи `_force_navigate_flipkart` |

## Перед commit (Flipkart-изменения)

- [ ] `python scripts/smoke_test.py` — минимум
- [ ] `run_to_payment.py` — если трогали checkout/VPN/навигацию
- [ ] Селекторы в example yaml если добавлены новые ключи
- [ ] Нет скриншотов/debug в diff
