# DeepSeek Platform

## Модули

| Файл | Роль |
|------|------|
| `deepseek.py` | Playwright-автоматизация пополнения platform.deepseek.com |
| `ggsell/deepseek_orders.py` | Заказы GGSELL для DeepSeek |
| `app.py` | GUI-раздел `deepseek` в sidebar |
| `chrome_profiles_deepseek/` | Профили по email (runtime, .gitignore) |

**Важно:** `deepseek.py` **самостоятельный** — не импортирует `menu.py` (как `grizzly.py`).

## Флоу пополнения (`deepseek.py`)

```
1. Логин email+пароль (профиль на аккаунт)
2. /usage — «Topped-up balance» до оплаты
3. /top_up — USD, пресет или Custom, Visa/Mastercard
4. Оплата через **Stripe Payment Element** (iframe) → Pay; 3DS — ждём вручную в браузере
5. При `declined` — следующая карта по `data/card_order.json` (`retry_cards=True`)
6. Успех = баланс на /usage вырос на сумму
```

Константы:
- `PRESET_AMOUNTS = (2, 5, 10, 20, 50, 100, 500)`
- `LOGIN_MANUAL_WAIT = 180` — ручной вход при капче
- `PROFILES_DIR = chrome_profiles_deepseek/`
- `DEBUG_DIR = debug/deepseek/`

## GUI (`app.py`)

- Сервис `deepseek` в sidebar (рядом с `youtube`, `kling`)
- `_build_deepseek()`, `_refresh_deepseek()`
- Запуск: `import deepseek as ds` — лениво при действии пользователя
- `kling` — заглушка `_build_coming_soon`

## Карты

Карты для оплаты — `cards.json` (через GUI → Карты), не в git.

## Отладка

- Скриншоты: `debug/deepseek/`
- При ошибке браузер может оставаться открытым `KEEP_OPEN_ON_FAIL` сек
- Логи через callback `_safe_log(cb)` — ошибки колбэка не роняют оплату

## GGSELL-интеграция

Заказы DeepSeek на маркетплейсе → `ggsell/deepseek_orders.py`.  
Связь с `deepseek.py` — через вызовы модуля, не через menu.

## OTA

`deepseek.py` в `_UPDATE_FILES`.  
`ggsell/deepseek_orders.py` тоже в списке.

## Чеклист при правках

```
- [ ] Не добавлен import menu в deepseek.py
- [ ] Профили в chrome_profiles_deepseek/, не в flipkart profiles
- [ ] Async Playwright в том же event loop что и существующий код
- [ ] DEBUG_DIR скриншоты не коммитятся
```

## Типичные задачи

| Задача | Файл |
|--------|------|
| Новый пресет суммы | `deepseek.py` → `PRESET_AMOUNTS` |
| Селектор формы оплаты | `deepseek.py` — рядом с существующими locators |
| Кнопка в GUI | `app.py` → `_build_deepseek` |
| Автовыдача заказа GGSell | `ggsell/deepseek_orders.py` |
