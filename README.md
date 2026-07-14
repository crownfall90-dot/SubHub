# SubHub

**SubHub** — десктопное приложение для команды: автоматизация YouTube Premium (Flipkart), маркетплейс GGSELL и Telegram-бот в одном окне.

> Репозиторий: [github.com/crownfall90-dot/SubHub](https://github.com/crownfall90-dot/SubHub)

---

## Возможности

| Модуль | Описание |
|--------|----------|
| **YouTube Premium** | Вход, покупка подписки через Flipkart, профили Chrome, VPN, карты |
| **GGSELL** | Мониторинг заказов, доставка ссылок, баланс |
| **Telegram-бот** | Управление профилями, заказами, обновлениями |
| **Фоновый режим** | Работа в трее, автозапуск Windows |

---

## Быстрый старт (для коллег)

### Установка через Setup.exe (рекомендуется)

1. Скачать [`setup.exe`](https://github.com/crownfall90-dot/SubHub/raw/master/setup.exe) из корня репозитория  
   (или взять из [Releases](https://github.com/crownfall90-dot/SubHub/releases))
2. Запустить установщик — появятся ярлыки, `SubHub.exe` и деинсталлятор
3. Скопировать `config.yaml.example` → `config.yaml`, `secrets.yaml.example` → `secrets.yaml` и заполнить ключи

### 1. Требования (если ставите из исходников)

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
| Банковская карта | через приложение → **Карты** | Оплата на Flipkart |

### 4. Запуск

Двойной клик по **`app.bat`** — откроется **SubHub** (без консоли).

Альтернативы:

| Файл | Назначение |
|------|------------|
| `app.bat` | Главное GUI-приложение SubHub |
| `menu.bat` | Консольное меню (отладка) |
| `create_shortcut.bat` | Ярлык **SubHub** на рабочем столе с иконкой |

При первом запуске автоматически установятся зависимости (`pip`) и Chromium (`playwright`).

### 5. VPN для Flipkart

Папка **`vpn_extension/`** должна быть в корне проекта (уже в репозитории).  
Без VPN Flipkart возвращает *Access Denied* — расширение ставится в профили автоматически.

---

## Структура проекта

```
SubHub/
├── app.py              # GUI (главное приложение)
├── app.bat             # Запуск без консоли
├── app_launch.vbs      # Скрытый старт pythonw
├── menu.py             # Ядро автоматизации
├── bot.py              # Telegram-бот
├── main.py             # Полный цикл входа
├── assets/
│   └── app.ico         # Иконка SubHub (окно, трей, ярлык)
├── vpn_extension/      # VPN-расширение для Flipkart
├── ggsell/             # Модуль маркетплейса
├── data/               # Runtime (создаётся автоматически, не в git)
├── config.yaml         # Настройки (не в git)
└── secrets.yaml        # API-ключи (не в git)
```

---

## Обновления

### Через приложение (рекомендуется)

1. SubHub → **Настройки** → блок **Обновления**
2. Нажать **Скачать и перезапустить**  
   Или дождаться уведомления в Telegram-боте → **Обновить сейчас**

Приложение скачивает актуальные файлы с ветки `master` на GitHub.

### Через Git (для разработчиков)

```bash
git pull origin master
pip install -r requirements.txt
python -m playwright install chromium
```

Затем перезапустить `app.bat`.

### Что обновляется автоматически

`app.py`, `menu.py`, `bot.py`, `main.py`, батники, `ggsell/*`, `assets/app.ico`, `requirements.txt` и др. — полный список в `menu.py` → `_UPDATE_FILES`.

> **Важно:** папка `vpn_extension/` обновляется только через `git pull` (слишком много файлов для OTA).

---

## Работа в фоне

- Иконка **SubHub** в системном трее — сразу при запуске
- Закрытие крестиком → сворачивание в трей (бот продолжает работать)
- **Настройки** → автозапуск Windows, старт свёрнутым

---

## Частые вопросы

**Не открывается Flipkart / таймаут**  
→ Проверьте VPN: **YouTube → VPN → Проверить**. Должен быть установлен Google Chrome.

**Нет иконки в трее**  
→ `pip install pystray Pillow`

**Старый ярлык на рабочем столе**  
→ Запустите `create_shortcut.bat` или **Настройки → Ярлык на рабочем столе**

**Конфликт двух копий бота**  
→ Закройте все экземпляры SubHub в трее, оставьте один

---

## Безопасность

Не коммитьте в git:

- `config.yaml`, `secrets.yaml`, `cards.json`
- `chrome_profiles*/`, `data/`, `cookies_backup/`

Эти пути уже в `.gitignore`.

---

## Лицензия и поддержка

Внутренний инструмент команды. Вопросы и баги — в Telegram-чат команды или через issue в репозитории.
