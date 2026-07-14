# GUI, запуск и отладка

## app.py — структура

| Компонент | Назначение |
|-----------|------------|
| `SubHubApp(ctk.CTk)` | Главное окно, sidebar, сервисы |
| `LogSink` | Очередь логов для виджета в GUI |
| `_start_run()` | Запуск subprocess automation (как smoke_test) |
| `main()` | Bootstrap, single-instance, трей (`pystray`) |

Сервисы в sidebar: `youtube`, `ggsell`, `deepseek`, `kling`.

## Дизайн-система

Константы в начале `app.py` — **переиспользуй**, не добавляй новые цвета:

```
BG_MAIN (#161C28), BG_SIDEBAR (#131824), BG_CARD (#1E2634), BG_CARD_HOVER, BG_SURFACE, BG_NAV_ACTIVE
ACCENT (#7C74FF мягкий violet), ACCENT_HOVER (#948DFF), ACCENT_CYAN (#38BDF8), ACCENT_SOFT (#272B4D)
SUCCESS (#2EB88A), WARNING (#E8A33D), ERROR (#E86A6A) — смягчены, не «неон»
TEXT_DIM (#B4BDCB), TEXT_MUTED (#8F99AB) — контраст ≥4.5:1 на карточках
FONT_UI, FONT_TITLE (24), FONT_SECTION, FONT_BODY, FONT_CAPTION, FONT_SMALL (11)
BORDER_SUBTLE (#313C4F), RADIUS_CARD (14), BTN_H (34), _PAD_PAGE (20)
```

Скролл: страницы — `AutoHideScrollFrame` (скроллбар прячется, если контент
помещается); колесо обрабатывает `_on_global_wheel` — крутит ТОЛЬКО tk.Text и
канвасы с меткой `_scroll_owner`. Не скролль обычные CTk-виджеты: это canvas'ы,
их отрисовка «уезжает». Главная — статичный CTkFrame с `_static = True`
(без скролла и анимаций входа).

Тонкие полоски-индикаторы (3px) — только `corner_radius=0`, иначе CTk рисует
«пиксельные» зазубрины. GUI-логи идут в `automation.log` через
`_append_log_file` в `_tick_logs`; экран «Логи» читает из файла — не добавляй
прямую запись в виджеты, будут дубли.

Главная — Pinterest-доска: `_pin_card` (кликабельная карточка целиком,
`_make_card_clickable`), `_pin_stat` (мини-метрики 2×2). Новая карточка на
главной → `_pin_card` в одну из трёх колонок `_build_home`.

Шрифты — только через `_ui_font()` / `_display_font()` (кэшируются, не мутировать).
Анимации — `_animate_color` (ease-out) и `_animate_page_enter` (слайд + каскад карточек);
новые цветовые переходы делай через них, без своих `after`-циклов.

Новый экран → копируй паттерн соседнего раздела (card + sidebar nav).

## Запуск

### Цепочка без консоли

```
app.bat → app_launch.vbs → pythonw app.py
         (fallback: python app.py)
```

`app_launch.vbs` — скрытый запуск, `CurrentDirectory` = папка скрипта.

### С консолью и автоперезапуском

```
app.bat --console
  → python app.py
  → exit 42 → ping 2s → restart
```

Код **42** — сигнал перезапуска после OTA или «перезапустить приложение»:

```python
os._exit(42)  # или sys.exit(42)
```

Тот же паттерн в `menu.bat`.

### Кодировка Windows

`app.bat` задаёт:
```
chcp 65001
PYTHONIOENCODING=utf-8
PYTHONUTF8=1
```

Не убирай — иначе сломается кириллица в логах и GUI.

## Отладка

| Сценарий | Команда |
|----------|---------|
| GUI с логами в консоли | `app.bat --console` |
| Только automation | `python menu.py` |
| Smoke (импорты, log stream, heartbeat) | `python scripts/smoke_test.py` |
| Зависшие pythonw | При выходе SubHub убивает все `app.py` из этой папки; при старте — старые экземпляры |
| До оплаты Flipkart | `python scripts/run_to_payment.py` |

### automation.log

`menu.py` → `_TeeWriter` дублирует stdout в `automation.log`. `smoke_test.py` проверяет, что subprocess-строки попадают в лог.

### Heartbeat

- `data/heartbeat_app.json` — GUI жив
- `data/heartbeat_console.json` — консольный прогон

### Single instance

`app.py` `main()` — не запускай два GUI одновременно (конфликт Telegram-бота и трея).

## OTA из GUI

Делегируется функциям из `menu.py`. Список файлов — `_UPDATE_FILES`. После обновления — перезапуск (exit 42).

Новый файл в проект → **добавь в `_UPDATE_FILES`**, иначе OTA его не подтянет.

## Типичные задачи GUI

| Задача | Паттерн |
|--------|---------|
| Новая кнопка | Найти соседний `CTkButton` в том же разделе |
| Новый раздел sidebar | Скопировать nav item + `_show_*` метод |
| Показ лога | `LogSink` + существующий textbox |
| Запуск прогона | `_start_run()` — subprocess, не блокировать UI thread |
| Иконка трея | `assets/app.ico`, `pystray` + Pillow |
| Ярлык на рабочий стол | `create_shortcut.bat` |

## Частые проблемы

| Симптом | Действие |
|---------|----------|
| Нет окна, нет ошибки | Запустить `app.bat --console` |
| Нет иконки трея | `pip install pystray Pillow` |
| GUI завис при прогоне | Проверить, что тяжёлая работа в subprocess/thread |
| После OTA старая версия | Перезапуск; проверить `_UPDATE_FILES` |
| pythonw не найден | VBS fallback на `python` — ок, но будет видно в диспетчере |

## Перед commit (GUI/infra)

- [ ] `app.bat --console` — окно, трей, закрытие → трей
- [ ] `python scripts/smoke_test.py`
- [ ] Новые deployable файлы → `_UPDATE_FILES`
- [ ] См. также [commits.md](commits.md), [code-review.md](code-review.md)
