# Flipkart Automation Bot

Автоматизация покупки Flipkart Black Membership с управлением через Telegram.

---

## Установка

**1.** Установить [Python 3.10+](https://www.python.org/downloads/) — при установке отметить **"Add Python to PATH"**

**2.** Скачать репозиторий:
```
git clone https://github.com/crownfall90-dot/flipkart-automation.git
```
Или: **Code → Download ZIP** → распаковать.

**3.** Скопировать шаблоны и заполнить ключи:
```
config.yaml.example  →  config.yaml
secrets.yaml.example →  secrets.yaml
```

**4.** Запустить:
```
menu.bat
```
При первом запуске зависимости и Chromium установятся автоматически.

---

## Необходимые ключи

- **GrizzlySMS** (`config.yaml` → `grizzlysms.api_key`) — виртуальные номера +91
- **Telegram Bot** (`secrets.yaml` → `telegram.token`) — создать через [@BotFather](https://t.me/BotFather)
- **Карта Visa/MC** — вносится через меню программы

Прокси опциональны. Если не нужны — `proxy.enabled: false` в `config.yaml`.

---

## Обновления

Проверяются автоматически. При новой версии придёт уведомление в Telegram — нажмите **«Обновить сейчас»**.
