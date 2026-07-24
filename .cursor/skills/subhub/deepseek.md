# DeepSeek Platform

## Модули

| Файл | Роль |
|------|------|
| `subhub/deepseek.py` | Playwright — пополнение platform.deepseek.com |
| `subhub/ggsell/deepseek_orders.py` | Заказы GGSELL для DeepSeek |
| `chrome_profiles_deepseek/` | Профили по email (runtime) |

**Важно:** `deepseek.py` **самостоятельный** — не импортирует `menu.py`.

## Флоу пополнения

```
1. Логин email+пароль
2. /usage — баланс до оплаты
3. /top_up — USD, Visa/Mastercard
4. Stripe Payment Element → Pay; 3DS вручную
5. declined → следующая карта (`data/card_order.json`)
6. Успех = баланс на /usage вырос
```

Карты — через консоль / `data/cards.json` (не в git).

## Чеклист

```
- [ ] Нет import menu в deepseek.py
- [ ] Профили только в chrome_profiles_deepseek/
- [ ] debug/deepseek/ не коммитить
```
