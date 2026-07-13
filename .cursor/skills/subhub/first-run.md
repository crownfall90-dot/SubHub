# Первый запуск и онбординг

Для коллег и агента, помогающего настроить SubHub с нуля.

## Требования

- Windows 10/11
- Python 3.10+ в PATH
- Google Chrome (Playwright Chromium ставится автоматически)
- Путь без проблем с кириллицей/пробелами желателен (`C:\SubHub`)

## Установка

### Вариант A — git

```powershell
git clone https://github.com/crownfall90-dot/flipkart-automation.git
cd flipkart-automation
git submodule update --init --recursive
pip install -r requirements.txt
python -m playwright install chromium
# UI/UX Pro Max (design system) + MotionSites prompts
npx -y ui-ux-pro-max-cli init --ai cursor
python scripts/fetch_motionsites_prompts.py --free
# Magic MCP (React UI) — нужен API key с https://21st.dev/magic
# .\scripts\setup_magic_mcp.ps1
```

### Вариант B — ZIP

1. GitHub → Code → Download ZIP
2. Распаковать
3. Те же `pip` и `playwright install`

## Конфигурация

```powershell
copy config.yaml.example config.yaml
copy secrets.yaml.example secrets.yaml
```

Заполнить ([config.md](config.md)):

| Ключ | Файл | Обязательно |
|------|------|-------------|
| GrizzlySMS api_key | secrets.yaml | Для авто-OTP (+91) |
| Telegram token | secrets.yaml | Для бота |
| GGSELL api_key, seller_id | secrets.yaml | Опционально |
| github.token | secrets.yaml | Для OTA без git |

При первом запуске `_init_secrets()` создаст `config.yaml` из example если его нет.

## Первый запуск

```
app.bat
```

Или с консолью для отладки:

```
app.bat --console
```

`app.bat` при первом запуске может установить зависимости автоматически.

## VPN (обязательно для Flipkart)

- Папка `vpn_extension/` должна быть в корне (в git)
- GUI → YouTube → VPN → Проверить
- Без VPN → Flipkart **Access Denied**

## Карты

GUI → **Карты** → добавить карту для Flipkart / DeepSeek.  
Хранится в `cards.json` (не в git).

## Проверка что всё работает

```powershell
python scripts/smoke_test.py
app.bat --console
```

В GUI:
1. Статус Telegram (если token задан)
2. VPN → Проверить
3. GGSELL (если настроен)

## Ярлык на рабочий стол

```
create_shortcut.bat
```

## Чеклист агента (помощь коллеге)

```
- [ ] Python 3.10+ в PATH (python --version)
- [ ] config.yaml и secrets.yaml созданы из example
- [ ] secrets.yaml заполнен (не YOUR_* плейсхолдеры)
- [ ] pip install -r requirements.txt
- [ ] playwright install chromium
- [ ] vpn_extension/ на месте
- [ ] app.bat --console открывает окно
- [ ] smoke_test.py проходит
```

## Частые ошибки новичка

| Проблема | Решение |
|----------|---------|
| Python не найден | Переустановить с «Add to PATH» |
| Нет secrets.yaml | copy secrets.yaml.example |
| Access Denied | VPN |
| Пустой бот | token в secrets.yaml |
| Окно не открывается | app.bat --console — смотреть traceback |
| Кириллица в логах | app.bat уже ставит UTF-8 |

## Обновления

- С git: `git pull`
- Без git: GUI → обновления → перезапуск (exit 42)

См. [ota.md](ota.md).
