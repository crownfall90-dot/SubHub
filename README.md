# SubHub

**SubHub** — консольный инструмент для команды: автоматизация YouTube Premium (Flipkart), маркетплейс GGSELL и Telegram-бот в одной консоли.

> Репозиторий: [github.com/crownfall90-dot/SubHub](https://github.com/crownfall90-dot/SubHub)

---

## Возможности

| Модуль | Описание |
|--------|----------|
| **YouTube Premium** | Вход, покупка подписки через Flipkart, профили Chrome, VPN, карты |
| **GGSELL** | Мониторинг заказов, доставка ссылок, баланс |
| **Telegram-бот** | Управление профилями, заказами, обновлениями — запускается вместе с консолью |

---

## Быстрый старт

### 1. Требования

- Windows 10/11
- [Python 3.10+](https://www.python.org/downloads/) — при установке отметить **Add Python to PATH**
- [Google Chrome](https://www.google.com/chrome/) (рекомендуется; Playwright Chromium ставится автоматически)

### 2. Клонирование

```bash
git clone https://github.com/crownfall90-dot/SubHub.git
cd SubHub
```

Или **Code → Download ZIP** и распаковать в папку без пробелов в пути (например `C:\SubHub`).

### 3. Конфигурация

Скопировать шаблоны и заполнить ключи:

```
config.yaml.example  →  config.yaml
secrets.yaml.example →  secrets.yaml
```

| Ключ | Где | Зачем |
|------|-----|-------|
| GrizzlySMS | `config.yaml` → `grizzlysms.api_key` | Виртуальные номера +91 |
| Telegram Bot | `secrets.yaml` → `telegram.token` | Бот ([@BotFather](https://t.me/BotFather)) |
| GGSELL | `secrets.yaml` → `ggsel.api_key`, `seller_id` | Маркетплейс (опционально) |
| Банковская карта | в консоли → **Карты** (пункт `0`) | Оплата на Flipkart |

### 4. Запуск

Двойной клик по **`menu.bat`** — откроется консольное меню SubHub. Telegram-бот и
фоновые мониторы (GrizzlySMS, GGSELL) стартуют автоматически вместе с консолью.

При первом запуске автоматически установятся зависимости (`pip`) и Chromium (`playwright`).

### 5. VPN для Flipkart

Папка **`vpn_extension/`** должна быть в корне проекта (уже в репозитории).
Без VPN Flipkart возвращает *Access Denied* — расширение ставится в профили автоматически.

---

## Структура проекта

```
SubHub/
├── menu.bat                 # Запуск консоли
├── README.md
├── requirements.txt
├── VERSION
├── config.yaml.example      # → скопировать в config.yaml
├── secrets.yaml.example     # → скопировать в secrets.yaml
├── subhub/                  # Код приложения
│   ├── menu.py              # Ядро: консольное меню + автоматизация
│   ├── main.py              # Полный цикл входа
│   ├── bot.py               # Telegram-бот
│   ├── grizzly.py / *_sms.py
│   ├── bg_login.py
│   └── ggsell/              # Маркетплейс
├── scripts/                 # Утилиты и smoke-тесты
├── docs/                    # CHANGELOG и доп. документы
├── assets/                  # Картинки для автоматизации
├── data/                    # Runtime (не в git)
├── chrome_profiles*/        # Профили Chrome (не в git)
├── config.yaml              # Настройки (не в git)
└── secrets.yaml             # API-ключи (не в git)
```

Запуск: **`menu.bat`** или `python -m subhub`

---

## Обновления

### Через консоль (рекомендуется)

Главное меню → пункт **`У`** (**Обновить до последней версии**). Консоль скачивает
актуальные файлы с ветки `master` на GitHub и перезапускается.

### Через Git (для разработчиков)

```bash
git pull origin master
pip install -r requirements.txt
python -m playwright install chromium
```

Затем перезапустить `menu.bat`.

### Что обновляется автоматически

`menu.bat`, `subhub/*`, `requirements.txt` и др. —
полный список в `subhub/menu.py` → `_UPDATE_FILES`.

> **Важно:** папка `vpn_extension/` обновляется только через `git pull` (слишком много файлов для OTA).

---

## Частые вопросы

**Не открывается Flipkart / таймаут**
→ Проверьте VPN на ПК. Должен быть установлен Google Chrome.

**Конфликт двух копий бота**
→ Закройте лишние окна консоли, оставьте одно — бота держит один экземпляр.

---

## Безопасность

Не коммитьте в git:

- `config.yaml`, `secrets.yaml`, `cards.json`
- `chrome_profiles*/`, `data/`, `cookies_backup/`

Эти пути уже в `.gitignore`.

---

## Лицензия и поддержка

Внутренний инструмент команды. Вопросы и баги — в Telegram-чат команды или через issue в репозитории.
