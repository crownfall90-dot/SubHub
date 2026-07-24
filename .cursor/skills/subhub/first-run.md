# Первый запуск и онбординг

## Требования

- Windows 10/11
- Python 3.10+ в PATH
- Google Chrome

## Установка

```powershell
git clone https://github.com/crownfall90-dot/SubHub.git
cd SubHub
git submodule update --init --recursive
pip install -r requirements.txt
python -m playwright install chromium
```

ZIP: GitHub → Download ZIP → те же `pip` / `playwright`.

## Конфигурация

```powershell
copy config.yaml.example config.yaml
copy secrets.yaml.example secrets.yaml
```

| Ключ | Файл | Обязательно |
|------|------|-------------|
| GrizzlySMS / PVAPins api_key | secrets.yaml | Для авто-OTP |
| Telegram token | secrets.yaml | Для бота |
| GGSELL | secrets.yaml | Опционально |

## Первый запуск

```
menu.bat
```

или `python -m subhub`

## VPN

Нужен для Flipkart (иначе Access Denied). В консоли: проверка VPN / статус.

## Карты

Консоль → раздел карт → `data/cards.json` (не в git).

## Проверка

```powershell
python scripts/test_console_smoke.py
menu.bat
```

## Чеклист

```
- [ ] Python 3.10+ в PATH
- [ ] config.yaml и secrets.yaml из example
- [ ] secrets заполнены
- [ ] pip + playwright chromium
- [ ] smoke проходит
- [ ] menu.bat открывает консоль
```

## Частые ошибки

| Проблема | Решение |
|----------|---------|
| Python не найден | Add to PATH |
| Нет secrets.yaml | copy example |
| Access Denied | VPN |
| Пустой бот | token в secrets.yaml |
