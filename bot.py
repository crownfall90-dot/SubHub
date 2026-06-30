"""
bot.py — Telegram bot background thread.
Integrated Flipkart accessibility checks and update notifications persistence.
"""

import asyncio
import json
import subprocess
import sys
import threading
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="unclosed transport", category=ResourceWarning)

_orig_unraisablehook = sys.unraisablehook
def _quiet_unraisablehook(u):
    if (isinstance(u.exc_value, ValueError)
            and "closed pipe" in str(u.exc_value)
            and (u.object is None or "Transport" in type(u.object).__name__)):
        return
    _orig_unraisablehook(u)
sys.unraisablehook = _quiet_unraisablehook

try:
    import httpx
except ImportError:
    httpx = None

try:
    import yaml
except ImportError:
    yaml = None

try:
    import aiohttp as _aiohttp
    from aiohttp import web as _aio_web
except ImportError:
    _aiohttp = None
    _aio_web = None

from proxy import (
    _read_proxy_cfg, _write_proxy_cfg,
    _p6_cfg, _p6_buy_affordable, _p6_getlist, _p6_balance,
)

# ── ANSI (консоль) ────────────────────────────────────────────────────────────
R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
DIM = "\033[90m"; BLD = "\033[1m"; RST = "\033[0m"

# ── Ленивый доступ к menu.py ──────────────────────────────────────────────────
def _m(name):
    import sys as _s, importlib as _i
    mod = _s.modules.get("menu") or _i.import_module("menu")
    return getattr(mod, name)

# ── Глобальные ────────────────────────────────────────────────────────────────
_tg_status: str  = "not_configured"
_ggsel_status: str = ""   # "" — не настроен / "ok" — активен / "error:..." — ошибка
_update_available: bool = False
_update_commits: list   = []
_update_checked: bool   = False
def _load_notified_updates() -> set:
    p = Path(__file__).parent / "data" / "notified_updates.json"
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_notified_updates(hashes: set) -> None:
    p = Path(__file__).parent / "data" / "notified_updates.json"
    try:
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(list(hashes), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_notified_update_hashes: set = _load_notified_updates()


def _tg_status_line() -> str:
    if _tg_status == "not_configured":
        tg_part = f"{DIM}○ Telegram: не настроен{RST}"
    elif _tg_status == "starting":
        tg_part = f"{Y}◎ Telegram: подключение...{RST}"
    elif _tg_status.startswith("error:"):
        tg_part = f"{R}✗ Telegram: {_tg_status[6:]}{RST}"
    else:
        n = _tg_status.split(":", 1)[1] if ":" in _tg_status else "?"
        tg_part = f"{G}● Telegram бот активен  {DIM}({n} подписчик(ов)){RST}"

    if _ggsel_status == "ok":
        ggsel_part = f"   {G}💰 GGSell активен{RST}"
    elif _ggsel_status.startswith("error:"):
        ggsel_part = f"   {R}💰 GGSell: {_ggsel_status[6:]}{RST}"
    else:
        ggsel_part = ""

    return tg_part + ggsel_part


def _menu_tg_bot_thread() -> None:
    global _tg_status, _ggsel_status
    _HERE = Path(__file__).parent

    # Токен только из secrets.yaml
    try:
        token = (_m("_read_secrets")().get("telegram") or {}).get("token", "").strip()
    except Exception:
        token = ""
    if not token:
        return
    try:
        import httpx as _hx
    except ImportError:
        _tg_status = "error:pip install httpx"
        return

    # Проверяем наличие GGSell-ключей и режим сервера
    _server_mode   = False
    _webhook_url   = ""
    try:
        _gs = (_m("_read_secrets")().get("ggsel") or {})
        if _gs.get("api_key", "").strip() and str(_gs.get("seller_id") or "").strip():
            _ggsel_status = "ok"
        _webhook_url = str(_gs.get("webhook_url") or "").rstrip("/")
    except Exception:
        pass
    try:
        _tg_cfg = (_m("_read_secrets")().get("telegram") or {})
        _server_mode = bool(_tg_cfg.get("server_mode", False))
    except Exception:
        pass

    _HEARTBEAT_FILE = Path(__file__).resolve().parent / "data" / "console_heartbeat.json"

    def _is_console_running() -> bool:
        """True если консоль (menu.py/main.py) сейчас запущена."""
        if _server_mode:
            try:
                import time as _t
                raw = json.loads(_HEARTBEAT_FILE.read_text(encoding="utf-8"))
                return _t.time() - float(raw.get("ts", 0)) < 120
            except Exception:
                return False
        return True  # не server_mode → консоль локально = всегда доступна

    TG_SUBSCRIBERS_FILE = _m("TG_SUBSCRIBERS_FILE")
    TG_STATS_FILE       = _m("TG_STATS_FILE")
    DONE_PROFILES_DIR   = _m("DONE_PROFILES_DIR")
    USED_PROFILES_DIR   = _m("USED_PROFILES_DIR")
    MSK                 = _m("MSK")
    CARDS_FILE          = _m("CARDS_FILE")
    _GIT                = _m("_GIT")

    # ── Подписчики ────────────────────────────────────────────────────────────
    def _load_subs():
        try:
            if TG_SUBSCRIBERS_FILE.exists():
                d = json.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
                return (set(int(c) for c in d.get("chats", [])),
                        {int(k): v for k, v in d.get("settings", {}).items()})
        except Exception:
            pass
        return set(), {}

    def _save_subs(chats, cfg_s):
        try:
            TG_SUBSCRIBERS_FILE.write_text(
                json.dumps({"chats": list(chats),
                            "settings": {str(k): v for k, v in cfg_s.items()}},
                           ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════════
    async def _poll():
        global _tg_status, _update_available, _update_commits, \
               _update_checked, _notified_update_hashes
        api    = f"https://api.telegram.org/bot{token}"
        subs, cfg = _load_subs()
        offset    = 0
        cons_err  = 0
        _first    = True

        # ── Состояние процесса ────────────────────────────────────────────────
        _proc    = [None]   # subprocess.Popen | None
        _mode    = [""]     # режим запуска
        _paused  = [False]
        _notify  = [set()]  # chat_ids для уведомления о завершении
        _ctrl    = [{}]     # {"chat_id": int, "msg_id": int} — живое сообщение
        _bg_ops: dict = {}  # phone → "running"
        # Сериализует операции покупки/оплаты: общее состояние (_purchase_cancel,
        # _switch_card_choice, _orders_confirm_choice, _3ds_card_options) в menu.py —
        # глобальные синглтоны, поэтому одновременно может идти только одна такая
        # операция. Параллельные запросы становятся в очередь, а не сталкиваются.
        _op_lock = asyncio.Lock()
        _pending_issued_archive: dict = {}  # cid → [phone, ...] — ожидают архивации после проверки
        _ggsel_cli      = [None]  # GGSell client (ленивая инициализация)
        _ggsel_orders: dict = {}  # {invoice_id: item из notify_queue}
        _ggsel_confirm: dict = {} # {invoice_id: link} — ждёт подтверждения от пользователя
        _ggsel_done: dict        = {} # {invoice_id: datetime_str} — выполнено (ссылка отправлена)
        _ggsel_done_loaded       = [False]
        _ggsel_reply_mode: dict  = {} # {cid: invoice_id} — ждём текст ответа от пользователя
        _pool_pick_pending: dict = {} # {cid: link}      — ссылка из пула ждёт выбора покупателя
        _ggsel_done_links: dict  = {} # {invoice_id: link} — какая ссылка была выдана
        _card_order_waiting: dict = {} # {cid: True} — ждём ввода порядка карт для основного бота
        _sale_input_waiting: dict = {}  # {cid: {"phone": str, "plan": str}} — ждём сумму продажи
        _sales_cost_waiting: dict = {}  # {cid: "3m"|"12m"} — ждём ввод себестоимости
        _note_waiting: dict = {}        # {cid: phone} — ждём текст примечания к профилю

        # ── Вспомогательные ──────────────────────────────────────────────────
        def _get(cid, key):    return cfg.get(cid, {}).get(key, True)
        def _set(cid, key, v):
            cfg.setdefault(cid, {})[key] = v
            _save_subs(subs, cfg)

        def _running():
            p = _proc[0]
            return p is not None and p.returncode is None

        def _mode_label(m):
            if m.startswith("wz:"):
                parts = m.split(":")
                br = "фоновый" if parts[1] == "headless" else "обычный"
                mode_map = {
                    "purchase": "Запуск | Полный цикл",
                    "login": "Запуск | Вход на ПК",
                    "address": "Запуск | Вход с данными",
                    "intercept": "Запуск | Подбор аккаунта TG"
                }
                mode_lbl = mode_map.get(parts[2], parts[2])
                tariff_lbl = f" · {parts[3]} мес." if parts[3] != "none" else ""
                return f"🚀 {mode_lbl}{tariff_lbl} ({br})"
            if m.startswith("full:"):
                parts = (m.split(":") + ["", ""])[:3]
                ms  = f"{parts[1]} мес." if parts[1] else "?"
                md  = "фоновый" if parts[2] == "headless" else "обычный"
                return f"⚡ Полный цикл {ms} · {md}"
            return {"tg": "🔔 Запуск | Подбор аккаунта TG",
                    "normal": "🖥 Запуск | Вход на ПК (обычный)",
                    "headless": "🌑 Запуск | Вход на ПК (фоновый)"}.get(m, m)

        def _disp_phone(ph: str) -> str:
            u = str(ph).strip()
            if len(u) == 12 and u.startswith("91") and u.isdigit():
                return f"+91 {u[2:]}"
            return f"+91 {u}"

        def _cnt_profiles():
            avail  = sum(1 for p in DONE_PROFILES_DIR.glob("profile_*")
                         if p.is_dir()) if DONE_PROFILES_DIR.exists() else 0
            archiv = sum(1 for _ in USED_PROFILES_DIR.glob("record_*.json")) \
                     if USED_PROFILES_DIR.exists() else 0
            return avail, archiv

        # ══════════════════════════════════════════════════════════════════════
        # UI — тексты
        # ══════════════════════════════════════════════════════════════════════

        def _main_text():
            avail, archiv = _cnt_profiles()
            pcfg  = _read_proxy_cfg()
            px_on = pcfg.get("enabled", False)
            px_n  = len(pcfg.get("list") or [])
            px_s  = (f"✅ вкл · {px_n} шт." if px_on and px_n
                     else "✅ вкл" if px_on else "❌ выкл")
            upd_s = (f"⬆️ {len(_update_commits)} новых коммита"
                     if _update_available else "✅ Версия актуальна")
            proc_line = ""
            if _running():
                lbl = _mode_label(_mode[0])
                st  = "⏸ Пауза" if _paused[0] else "🟢"
                proc_line = f"\n\n{st} _{lbl}_"
            return (
                "🤖 *Flipkart Automation*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📁 Профилей: *{avail}* готово · *{archiv}* в архиве\n"
                f"🌐 Прокси: {px_s}\n"
                f"🔄 {upd_s}"
                + proc_line
            )

        # Главная клавиатура — 3 кнопки + процесс если запущен
        def _main_kb(cid):
            rows = []
            if _running():
                lbl = _mode_label(_mode[0])
                st  = "⏸" if _paused[0] else "🟢"
                rows.append([{"text": f"{st} {lbl}",
                               "callback_data": "go:run_ctrl"}])
            rows += [
                [{"text": "🚀 Запуск",    "callback_data": "go:launch"},
                 {"text": "📁 Профили",   "callback_data": "go:profiles"},
                 {"text": "⚙️ Другое",    "callback_data": "go:other"}],
                [{"text": "💰 GGSell",    "callback_data": "go:ggsell"}],
                [{"text": "🔄 Перезапустить консоль", "callback_data": "action:restart"}],
            ]
            return {"inline_keyboard": rows}

        # ── Запуск ────────────────────────────────────────────────────────────
        def _launch_text():
            if _running():
                lbl = _mode_label(_mode[0])
                st  = "⏸ Пауза" if _paused[0] else "🟢 Работает"
                return (
                    "🚀 *Автоматизация*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{st}  ·  _{lbl}_\n"
                    f"`PID {_proc[0].pid}`\n\n"
                    "_Уведомлю когда завершится._"
                )
            return (
                "🚀 *Запуск автоматизации*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Выберите режим:"
            )

        def _launch_kb():
            if _running():
                paused    = _paused[0]
                pause_btn = "▶️ Продолжить" if paused else "⏸ Пауза"
                return {"inline_keyboard": [
                    [{"text": _mode_label(_mode[0]), "callback_data": "noop"}],
                    [{"text": pause_btn,             "callback_data": "run:pause"},
                     {"text": "🔄 Изменить кол-во",  "callback_data": "run:change"}],
                    [{"text": "🛑 Остановить",        "callback_data": "run:stop"}],
                    [{"text": "◀️ Назад",             "callback_data": "go:main"}],
                ]}
            # Шаг 1: Выбор режима браузера
            return {"inline_keyboard": [
                [{"text": "🖥 Обычный (с окном)", "callback_data": "wz:br:normal"}],
                [{"text": "🌑 Фоновый (без окна)", "callback_data": "wz:br:headless"}],
                [{"text": "◀️ Назад", "callback_data": "go:main"}],
            ]}

        def _buy_stop_kb():
            return {"inline_keyboard": [[{"text": "🛑 Остановить", "callback_data": "buy:stop"}]]}

        def _wz_mode_kb(br):
            return {"inline_keyboard": [
                [{"text": "Запуск | Полный цикл", "callback_data": f"wz:md:{br}:purchase"}],
                [{"text": "Запуск | Вход на ПК", "callback_data": f"wz:md:{br}:login"}],
                [{"text": "Запуск | Вход с данными", "callback_data": f"wz:md:{br}:address"}],
                [{"text": "Запуск | Подбор аккаунта TG", "callback_data": f"wz:md:{br}:intercept"}],
                [{"text": "◀️ Назад", "callback_data": "go:launch"}],
            ]}

        def _wz_tariff_kb(br, mode):
            return {"inline_keyboard": [
                [{"text": "🥈 3 мес · ₹399", "callback_data": f"wz:tf:{br}:{mode}:3"},
                 {"text": "🥇 12 мес · ₹1,499", "callback_data": f"wz:tf:{br}:{mode}:12"}],
                [{"text": "◀️ Назад", "callback_data": f"wz:br:{br}"}],
            ]}

        def _wz_count_kb(br, mode, tariff):
            row1 = [{"text": str(i), "callback_data": f"wz:run:{br}:{mode}:{tariff}:{i}"} for i in range(1, 6)]
            row2 = [{"text": str(i), "callback_data": f"wz:run:{br}:{mode}:{tariff}:{i}"} for i in (10, 15, 20)]
            if mode in ("login", "intercept"):
                back_cb = f"wz:br:{br}"
            else:
                back_cb = f"wz:md:{br}:{mode}"
            return {"inline_keyboard": [
                row1,
                row2,
                [{"text": "◀️ Назад", "callback_data": back_cb}],
            ]}

        # ── Профили ───────────────────────────────────────────────────────────
        def _get_profile_categories():
            noaddr  = []  # Доступные
            hasaddr = []  # С данными
            paid    = []  # Оплаченные (есть ссылка, не выдан)
            active  = []  # Выданные
            if DONE_PROFILES_DIR.exists():
                for p in DONE_PROFILES_DIR.glob("profile_*"):
                    if not p.is_dir():
                        continue
                    try:
                        m = _m("_read_profile_meta")(p)
                    except Exception:
                        m = {}
                    ph = _ph(m, p)
                    vt = m.get("black_valid_till") or ""
                    st = m.get("status") or ""
                    is_issued  = bool(m.get("issued_ts"))
                    has_link   = bool(m.get("black_activation_link") or m.get("activation_url"))
                    is_subact  = (st in ("activated", "explore_now", "activate_now")) or bool(vt)
                    is_paid    = (has_link or is_subact) and not is_issued
                    is_ready   = bool(
                        m.get("prepared_ts") or m.get("buyer_email") or st == "email_completed"
                    ) and not is_issued and not is_paid
                    if is_issued:
                        active.append((ph, p, m))
                    elif is_paid:
                        paid.append((ph, p, m))
                    elif is_ready:
                        hasaddr.append((ph, p, m))
                    else:
                        noaddr.append((ph, p, m))
            noaddr.sort(key=lambda x: x[2].get("login_ts") or 0, reverse=True)
            hasaddr.sort(key=lambda x: x[2].get("prepared_ts") or x[2].get("login_ts") or 0, reverse=True)
            paid.sort(key=lambda x: x[2].get("login_ts") or 0, reverse=True)
            active.sort(key=lambda x: x[2].get("issued_ts") or x[2].get("login_ts") or 0, reverse=True)
            return noaddr, hasaddr, paid, active

        def _profiles_text():
            noaddr, hasaddr, paid, active = _get_profile_categories()
            _, archiv = _cnt_profiles()
            return (
                "📁 *Профили*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🟢 Доступные: *{len(noaddr)}*\n"
                f"🟠 С данными: *{len(hasaddr)}*\n"
                f"🟣 Оплаченные: *{len(paid)}*\n"
                f"🔵 Выданные: *{len(active)}*\n"
                f"🟡 Архив: *{archiv}*\n\n"
                "Выберите действие:"
            )

        def _profiles_kb():
            noaddr, hasaddr, paid, active = _get_profile_categories()
            _, archiv = _cnt_profiles()
            return {"inline_keyboard": [
                [{"text": f"🟢 Доступные ({len(noaddr)})", "callback_data": "profiles:list:noaddr"},
                 {"text": f"🟠 С данными ({len(hasaddr)})", "callback_data": "profiles:list:hasaddr"}],
                [{"text": f"🟣 Оплаченные ({len(paid)})", "callback_data": "profiles:list:paid"},
                 {"text": f"🔵 Выданные ({len(active)})", "callback_data": "profiles:list:active"}],
                [{"text": f"🟡 Архив ({archiv})", "callback_data": "profiles:list:archive"},
                 {"text": "Проверить 🟢", "callback_data": "profiles:checkall"}],
                [{"text": "🍪 Восстановить из куков", "callback_data": "profiles:cookies_info"}],
                [{"text": "◀️ Назад", "callback_data": "go:main"}],
            ]}

        def _ph(meta, p):
            """Возвращает чистый 10-значный номер из meta или имени папки."""
            raw = meta.get("username") or ""
            raw = raw.replace("profile_", "").strip()
            if not raw:
                raw = p.name.replace("profile_", "").strip()
            return raw

        def _profile_list_text(list_type="noaddr"):
            try:
                noaddr, hasaddr, paid, active = _get_profile_categories()
                if list_type == "noaddr":
                    title = f"🟢 *Доступные профили* ({len(noaddr)} шт.)"
                    pairs = noaddr
                elif list_type == "hasaddr":
                    title = f"🟠 *С данными* ({len(hasaddr)} шт.)"
                    pairs = hasaddr
                elif list_type == "paid":
                    title = f"🟣 *Оплаченные профили* ({len(paid)} шт.)"
                    pairs = paid
                elif list_type == "active":
                    title = f"🔵 *Выданные профили* ({len(active)} шт.)"
                    pairs = active
                elif list_type == "archive":
                    return _archive_text()
                else:
                    return "📁 *Профили*\n\n_Неизвестный тип списка_"

                if not pairs:
                    return f"{title}\n\n_Профилей в этой категории нет_"

                lines = [title, "─────────────────────", ""]
                for ph, p, m in pairs[:20]:
                    vt = m.get("black_valid_till") or ""
                    st = m.get("status") or ""
                    is_iss  = bool(m.get("issued_ts"))
                    has_lnk = bool(m.get("black_activation_link") or m.get("activation_url"))
                    is_rdy  = bool(m.get("prepared_ts") or m.get("buyer_email") or st == "email_completed")
                    if is_iss:
                        icon = "🔵"
                    elif has_lnk or (st in ("activated", "explore_now", "activate_now") or vt):
                        icon = "🟣"
                    elif is_rdy:
                        icon = "🟠"
                    else:
                        icon = "🟢"
                    line = f"{icon} `{_disp_phone(ph)}`"
                    if vt:
                        line += f"  до {vt}"
                    elif has_lnk and list_type == "paid":
                        line += "  🔗"
                    lines.append(line)
                if len(pairs) > 20:
                    lines.append(f"\n...и ещё {len(pairs) - 20} профилей")
                return "\n".join(lines)
            except Exception as e:
                return f"📁 *Профили*\n\n❌ Ошибка: {e}"

        def _profile_list_kb(list_type="noaddr"):
            try:
                if list_type == "archive":
                    return _archive_kb()

                noaddr, hasaddr, paid, active = _get_profile_categories()
                if list_type == "noaddr":
                    pairs = noaddr
                elif list_type == "hasaddr":
                    pairs = hasaddr
                elif list_type == "paid":
                    pairs = paid
                elif list_type == "active":
                    pairs = active
                else:
                    pairs = []

                rows = []
                for ph, p, m in pairs[:20]:
                    vt = m.get("black_valid_till") or ""
                    st = m.get("status") or ""
                    is_iss  = bool(m.get("issued_ts"))
                    has_lnk = bool(m.get("black_activation_link") or m.get("activation_url"))
                    has_login = bool(m.get("login_ts"))
                    if list_type in ("noaddr", "hasaddr"):
                        if not has_login:
                            continue
                        icon = "🟢"
                    elif list_type == "paid":
                        icon = "🟣" if has_lnk else "🌟"
                    else:
                        icon = "🔵" if is_iss else ("🟣" if (has_lnk or st in ("activated", "explore_now", "activate_now") or vt) else "🟢")
                    label = f"{icon} {_disp_phone(ph)}"
                    if vt:
                        label += f" · до {vt}"
                    rows.append([{"text": label, "callback_data": f"profile:menu:{ph}:{list_type}"}])
                if list_type == "noaddr" and pairs:
                    rows.append([{"text": "⚡ Заполнить все", "callback_data": "profiles:fill_all"}])
                if list_type == "active" and pairs:
                    rows.append([{"text": "🔍 Проверить все", "callback_data": "profiles:check_issued_all"}])
                rows.append([{"text": "◀️ Назад", "callback_data": "go:profiles"}])
            except Exception:
                rows = [[{"text": "◀️ Назад", "callback_data": "go:profiles"}]]
            return {"inline_keyboard": rows}

        def _profile_menu_kb(phone, list_type="noaddr", rec_key=""):
            if list_type == "archive":
                return {"inline_keyboard": [
                    [{"text": "📞 Показать номер", "callback_data": f"profile:shownum:{phone}"}],
                    [{"text": "🍪 Экспорт куки JSON", "callback_data": f"profile:cookies_archived:{phone}:{rec_key}"}],
                    [{"text": "🔓 Восстановить профиль", "callback_data": f"profile:unarchive:{rec_key}"}],
                    [{"text": "🗑 Удалить навсегда", "callback_data": f"profile:arcdel_confirm:{rec_key}"}],
                    [{"text": "◀️ Назад", "callback_data": "profiles:list:archive"}],
                ]}

            pp = _find_profile(phone)
            m = {}
            if pp:
                try:
                    m = _m("_read_profile_meta")(pp)
                except Exception:
                    pass

            vt = m.get("black_valid_till") or ""
            st = m.get("status") or ""
            is_issued = bool(m.get("issued_ts"))
            has_link  = bool(m.get("black_activation_link") or m.get("black_short_link"))
            is_subact = (st in ("activated", "explore_now", "activate_now")) or bool(vt)
            is_paid   = (has_link or is_subact) and not is_issued

            # «Назад» — всегда на вкладку, из которой открыли профиль
            _known_lt = ("noaddr", "hasaddr", "paid", "active")
            back_callback = f"profiles:list:{list_type if list_type in _known_lt else 'noaddr'}"

            _bound_inv = m.get("issued_invoice_id") or ""

            rows = []
            if is_issued:
                # Выданные
                rows.append([{"text": "✅ Проверить активацию Black", "callback_data": f"profile:activate:{phone}"}])
                rows.append([{"text": "🔵 Выдан", "callback_data": "noop"}])
                if _bound_inv:
                    rows.append([{"text": f"📋 Перейти к заказу #{_bound_inv}",
                                  "callback_data": f"ggsell:order:{_bound_inv}"}])
                if has_link:
                    rows.append([{"text": "🔄 Заменить ссылку", "callback_data": f"profile:refresh_link:{phone}"}])
                rows.append([{"text": "💰 Записать продажу", "callback_data": f"profile:record_sale:{phone}"}])
                rows.append([{"text": "📦 Перенести в архив", "callback_data": f"profile:archive_one:{phone}"}])
            elif is_paid:
                # Оплаченные — покупка не нужна, есть ссылка/активация
                rows.append([{"text": "✅ Проверить активацию Black", "callback_data": f"profile:activate:{phone}"}])
                rows.append([{"text": "🔵 Поставить статус выдан", "callback_data": f"profile:set_issued:{phone}"}])
                if has_link:
                    rows.append([{"text": "🔄 Заменить ссылку", "callback_data": f"profile:refresh_link:{phone}"}])
                    rows.append([{"text": "📤 Выдать получателю", "callback_data": f"profile:send_to_buyer:{phone}:0"}])
                rows.append([{"text": "💰 Записать продажу", "callback_data": f"profile:record_sale:{phone}"}])
            else:
                # Доступные / С данными — ссылки ещё нет, нужно купить
                rows.append([{"text": "🥈 Купить 3 мес · ₹399", "callback_data": f"profile:buy:3:{phone}"},
                             {"text": "🥇 12 мес · ₹1499", "callback_data": f"profile:buy:12:{phone}"}])
                rows.append([{"text": "📍 Заполнить данные", "callback_data": f"profile:fill_data:{phone}"}])
                rows.append([{"text": "✅ Проверить активацию Black", "callback_data": f"profile:activate:{phone}"}])
                rows.append([{"text": "🗑 Удалить профиль", "callback_data": f"profile:del_confirm:{phone}"}])

            rows.append([{"text": "🍪 Экспорт куки JSON", "callback_data": f"profile:cookies:{phone}"}])
            _note_lbl = ("📝 Примечание: " + (m.get("note") or "")[:20]) if m.get("note") else "📝 Добавить примечание"
            rows.append([{"text": _note_lbl, "callback_data": f"profile:note:{phone}"}])
            rows.append([{"text": "◀️ Назад", "callback_data": back_callback}])
            return {"inline_keyboard": rows}

        def _archive_text():
            if not USED_PROFILES_DIR or not USED_PROFILES_DIR.exists():
                return "🟡 *Архив*\n\n_Архив пуст_"
            records = sorted(USED_PROFILES_DIR.glob("record_*.json"), reverse=True)
            if not records:
                return "🟡 *Архив*\n\n_Архив пуст_"
            lines = [f"🟡 *Архив* ({len(records)} шт.)", "━━━━━━━━━━━━━━━━━━━━━━", ""]
            for rec in records[:15]:
                try:
                    d  = json.loads(rec.read_text(encoding="utf-8"))
                    ph = d.get("username") or rec.stem.replace("record_", "")
                    vt = d.get("black_valid_till") or ""
                    inv = d.get("issued_invoice_id") or ""
                    email = d.get("buyer_email") or ""
                    link = d.get("issued_link") or d.get("black_short_link") or d.get("black_activation_link") or ""
                    lines.append(f"🟡 *{_disp_phone(ph)}*" + (f"  ·  до {vt}" if vt else ""))
                    if inv:
                        _o = f"   📦 Заказ #{inv}"
                        if email:
                            _o += f"  ·  `{email}`"
                        lines.append(_o)
                    if link:
                        _sl = link[8:46] + "…" if len(link[8:]) > 38 else link[8:]
                        lines.append(f"   🔗 `{_sl}`")
                    lines.append("")
                except Exception:
                    lines.append(f"🟡 {rec.name}")
            if len(records) > 15:
                lines.append(f"_...и ещё {len(records) - 15}_")
            return "\n".join(lines)

        def _archive_kb():
            try:
                if not USED_PROFILES_DIR or not USED_PROFILES_DIR.exists():
                    return {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:profiles"}]]}
                records = sorted(USED_PROFILES_DIR.glob("record_*.json"), reverse=True)
                rows = []
                for rec in records[:15]:
                    rec_key = rec.stem.replace("record_", "")
                    try:
                        d  = json.loads(rec.read_text(encoding="utf-8"))
                        ph = d.get("username") or rec.stem.replace("record_", "")
                        vt = d.get("black_valid_till") or ""
                        email = d.get("buyer_email") or ""
                        icon = "🌟" if vt else "✅"
                        label = f"{icon} {_disp_phone(ph)}"
                        if email:
                            label += f" · {email[:24]}"
                        elif vt:
                            label += f" · {vt}"
                        rows.append([{"text": label, "callback_data": f"profile:menu:{ph}:archive:{rec_key}"}])
                        rows.append([{"text": "🔓 Восстановить профиль", "callback_data": f"profile:unarchive:{rec_key}"}])
                    except Exception:
                        rows.append([{"text": rec.name, "callback_data": f"profile:menu:{rec_key}:archive:{rec_key}"}])
                rows.append([{"text": "◀️ Назад", "callback_data": "go:profiles"}])
            except Exception:
                rows = [[{"text": "◀️ Назад", "callback_data": "go:profiles"}]]
            return {"inline_keyboard": rows}

        # ── Другое ────────────────────────────────────────────────────────────
        def _other_text(cid):
            buy_on = _get(cid, "buy_number")
            otp_on = _get(cid, "otp_code")
            pcfg   = _read_proxy_cfg()
            px_on  = pcfg.get("enabled", False)
            px_n   = len(pcfg.get("list") or [])
            px_s   = f"✅ {px_n} шт." if px_on and px_n else ("✅" if px_on else "❌")
            upd_s  = (f"⬆️ {len(_update_commits)} новых"
                      if _update_available else "✅ актуальна")
            return (
                "⚙️ *Настройки и утилиты*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🌐 Прокси: {px_s}\n"
                f"📣 Покупка: {'✅ вкл' if buy_on else '❌ выкл'}\n"
                f"🔑 OTP: {'✅ вкл' if otp_on else '❌ выкл'}\n"
                f"🔄 Версия: {upd_s}"
            )

        def _other_kb(cid):
            buy_on = _get(cid, "buy_number")
            otp_on = _get(cid, "otp_code")
            buy_b  = "📣 Покупка ✅" if buy_on else "🔇 Покупка ❌"
            otp_b  = "🔑 OTP ✅"     if otp_on else "🔑 OTP ❌"
            upd_b  = (f"⬆️ Обновить ({len(_update_commits)})"
                      if _update_available else "✅ Обновление")
            return {"inline_keyboard": [
                [{"text": "💳 Порядок карт", "callback_data": "show:cards"},
                 {"text": "🌐 Прокси",      "callback_data": "show:proxy"}],
                [{"text": "📋 Логи",        "callback_data": "show:logs"},
                 {"text": "📊 Статистика",  "callback_data": "show:stats"}],
                [{"text": "💰 Продажи",    "callback_data": "go:sales"}],
                [{"text": "📦 Зависимости", "callback_data": "deps:install"},
                 {"text": upd_b,            "callback_data": "update:check"}],
                [{"text": buy_b,            "callback_data": "t:buy_number"},
                 {"text": otp_b,            "callback_data": "t:otp_code"}],
                [{"text": "◀️ Назад",       "callback_data": "go:main"}],
            ]}

        # ── Прокси ────────────────────────────────────────────────────────────
        def _proxy_text():
            pcfg    = _read_proxy_cfg()
            enabled = pcfg.get("enabled", False)
            proxies = pcfg.get("list") or []
            single  = pcfg.get("server", "")
            st = "✅ включён" if enabled else "❌ выключен"
            lines = ["🌐 *Прокси*", "━━━━━━━━━━━━━━━━━━━━━━", "", f"Статус: {st}"]
            if proxies:
                lines.append(f"Серверов: *{len(proxies)} шт.*")
                lines.append("")
                for p in proxies[:3]:
                    lines.append(f"`{p.get('server','').replace('http://','')}`")
                if len(proxies) > 3:
                    lines.append(f"_...ещё {len(proxies)-3}_")
            elif single:
                lines.append(f"`{single.replace('http://','')}`")
            else:
                lines.append("_Прокси не настроены_")
            return "\n".join(lines)

        def _proxy_kb():
            pcfg  = _read_proxy_cfg()
            p6cfg = _p6_cfg()
            tog   = "🔴 Выключить" if pcfg.get("enabled") else "🟢 Включить"
            rows  = [[{"text": tog, "callback_data": "proxy:toggle"}]]
            if p6cfg.get("api_key", "").strip():
                cnt = p6cfg.get("default_count", 10)
                rows += [
                    [{"text": "💰 Баланс Proxy6",  "callback_data": "proxy6:balance"},
                     {"text": f"🛒 Купить {cnt}",  "callback_data": "proxy6:buy"}],
                    [{"text": "🔄 Синхронизировать", "callback_data": "proxy6:sync"}],
                ]
            rows.append([{"text": "◀️ Назад", "callback_data": "go:other"}])
            return {"inline_keyboard": rows}

        # ── GGSell — handler инициализируется ниже, после _edit/_send/_ack ──────
        _ggsel_handler = [None]  # [GGSellBotHandler]


        # ── Порядок карт ──────────────────────────────────────────────────────
        _CARD_ORDER_FILE = Path(__file__).parent / "data" / "card_order.json"

        def _load_card_order():
            try:
                if _CARD_ORDER_FILE.exists():
                    v = json.loads(_CARD_ORDER_FILE.read_text(encoding="utf-8"))
                    if isinstance(v, list):
                        return v
            except Exception:
                pass
            return []

        def _save_card_order(order):
            try:
                _CARD_ORDER_FILE.parent.mkdir(parents=True, exist_ok=True)
                _CARD_ORDER_FILE.write_text(
                    json.dumps(order, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        def _card_label(c: dict, n: int) -> str:
            name = (c.get("nickname") or c.get("name") or "Карта")[:20]
            num  = str(c.get("number", "")).replace(" ", "").replace("-", "")
            mask = f"*{num[-4:]}" if len(num) >= 4 else "****"
            exp  = c.get("expiry") or c.get("exp") or ""
            exp_s = f" {exp}" if exp else ""
            return f"[{n}] {name}  {mask}{exp_s}"

        def _cards_order_page(cid):
            try:
                if not CARDS_FILE.exists():
                    return "💳 *Карты*\n\n_Файл cards.json не найден_", {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:other"}]]}
                cards = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
                if not cards:
                    return "💳 *Карты*\n\n_Список пуст_", {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:other"}]]}

                order = _load_card_order()
                num_icons = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
                medal_icons = ["🥇", "🥈", "🥉"] + ["▫️"] * 10

                lines = ["💳 *Карты для авто-оплаты*", "━━━━━━━━━━━━━━━━━━━━━━", ""]

                lines.append("📋 *Все карты:*")
                for i, c in enumerate(cards):
                    num = str(c.get("number", "")).replace(" ", "").replace("-", "")
                    mask = f"****{num[-4:]}" if len(num) >= 4 else "****"
                    name = c.get("nickname") or c.get("name") or ""
                    exp = c.get("expiry") or c.get("exp") or ""
                    icon = num_icons[i] if i < len(num_icons) else f"{i+1}."
                    parts = [icon, f"`{mask}`"]
                    if name:
                        parts.insert(1, f"*{name}*")
                    if exp:
                        parts.append(f"_{exp}_")
                    lines.append("  " + "  ·  ".join(p for p in parts if p))
                lines.append("")

                if order:
                    lines.append("🔄 *Порядок при авто-оплате:*")
                    for pos, idx in enumerate(order):
                        if 0 <= idx < len(cards):
                            c = cards[idx]
                            num = str(c.get("number", "")).replace(" ", "")[-4:]
                            name = c.get("nickname") or c.get("name") or ""
                            medal = medal_icons[pos] if pos < len(medal_icons) else "▫️"
                            card_str = f"*{name}*  `****{num}`" if name else f"`****{num}`"
                            lines.append(f"  {medal}  {card_str}  _{f'(карта {idx+1})'}_")
                    lines.append("")
                else:
                    lines.append("_Порядок не задан — карты берутся по умолчанию_")
                    lines.append("")

                lines.append("_Отправь номера карт через пробел для задания порядка._")
                lines.append(f"_Пример:_ `2 1 3`")

                kb_rows = []
                if order:
                    kb_rows.append([{"text": "🔄 Сбросить порядок", "callback_data": "cards:order_reset"}])
                kb_rows.append([{"text": "✏️ Задать порядок", "callback_data": "cards:order_edit"}])
                kb_rows.append([{"text": "◀️ Назад", "callback_data": "go:other"}])
                return "\n".join(lines), {"inline_keyboard": kb_rows}
            except Exception as e:
                return f"💳 *Карты*\n\n❌ Ошибка: {e}", {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:other"}]]}

        # ── Логи ──────────────────────────────────────────────────────────────
        def _logs_text(n=40):
            log = Path(__file__).parent / "automation.log"
            if not log.exists():
                return "📋 *Логи*\n\n_automation.log не найден_"
            try:
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
                tail  = lines[-n:]
                text  = "\n".join(tail)
                if len(text) > 3800:
                    text = "…\n" + text[-3700:]
                return f"📋 *Логи* (последние {len(tail)} строк)\n\n```\n{text}\n```"
            except Exception as e:
                return f"📋 *Логи*\n\n❌ {e}"

        # ── Статистика ────────────────────────────────────────────────────────
        def _stats_text():
            try:
                s = (json.loads(TG_STATS_FILE.read_text(encoding="utf-8"))
                     if TG_STATS_FILE.exists() else {})
            except Exception:
                s = {}
            from datetime import datetime
            today  = datetime.now(tz=MSK)
            td, tt = s.get("today", {}), s.get("total", {})
            bal    = s.get("last_balance")
            avail, archiv = _cnt_profiles()
            lines = [
                "📊 *Статистика*", "━━━━━━━━━━━━━━━━━━━━━━", "",
                f"📅 *Сегодня ({today.strftime('%d %b')}):*",
                f"▸ Куплено номеров: *{td.get('numbers_bought', 0)}*",
                f"▸ Получено OTP: *{td.get('otp_received', 0)}*",
                f"▸ Успешных входов: *{td.get('logins', 0)}*",
                f"▸ Возвратов: *{td.get('refunds', 0)}*",
                f"▸ Потрачено: `${td.get('spent', 0.0):.4f}`",
                "",
                "📈 *За всё время:*",
                f"▸ Куплено номеров: *{tt.get('numbers_bought', 0)}*",
                f"▸ Получено OTP: *{tt.get('otp_received', 0)}*",
                f"▸ Успешных входов: *{tt.get('logins', 0)}*",
                f"▸ Возвратов: *{tt.get('refunds', 0)}*",
                f"▸ Потрачено: `${tt.get('spent', 0.0):.4f}`",
                "",
                "💼 *Профили:*",
                f"▸ Готово: *{avail}*  ·  Архив: *{archiv}*",
            ]
            if bal is not None:
                lines += ["", f"💰 *Баланс GrizzlySMS: `${bal:.4f}`*"]
            return "\n".join(lines)

        # ── Продажи ───────────────────────────────────────────────────────────
        _SALES_FILE  = TG_STATS_FILE.parent / "sales_stats.json"
        _SCFG_FILE   = TG_STATS_FILE.parent / "sales_config.json"
        _usd_cache         = [0.0, 0.0]  # [rate_rub_per_usd, timestamp]
        _funpay_rate_cache = [0.0, 0.0]  # [rate_rub_per_usd, timestamp]

        def _get_funpay_rate() -> float:
            """Курс USDT/RUB с Funpay (кеш 30 мин). Требует funpay_golden_key в конфиге."""
            import time as _t, re as _re, urllib.request as _ur
            if _funpay_rate_cache[0] > 0 and _t.time() - _funpay_rate_cache[1] < 1800:
                return _funpay_rate_cache[0]
            try:
                gk = _load_scfg().get("funpay_golden_key", "")
                if not gk:
                    return 0.0
                req = _ur.Request(
                    "https://funpay.com/account/balance",
                    headers={
                        "Cookie": f"golden_key={gk}",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ru-RU,ru;q=0.9",
                    }
                )
                with _ur.urlopen(req, timeout=10) as _resp:
                    _html = _resp.read().decode("utf-8", errors="replace")
                for _pat in [
                    r'data-rate=["\']?([\d]+[.,][\d]+)',
                    r'"rate"\s*:\s*([\d]+[.,][\d]+)',
                    r'[кК]урс\s+([\d]+[.,][\d]+)',
                    r'usdt[_\-]trc[^>]*data-[^>]*>([\d]+[.,][\d]+)',
                ]:
                    _m = _re.search(_pat, _html, _re.IGNORECASE)
                    if _m:
                        _r = float(_m.group(1).replace(",", "."))
                        if _r > 10:
                            _funpay_rate_cache[0] = _r
                            _funpay_rate_cache[1] = _t.time()
                            return _r
            except Exception:
                pass
            return 0.0

        def _get_usd_rate() -> float:
            """Курс USD→RUB. Приоритет: ручной → Funpay USDT → ЦБ РФ (кеш 1 час)."""
            # Ручной курс из конфига
            try:
                manual = _load_scfg().get("usd_rate", 0)
                if manual and float(manual) > 0:
                    return float(manual)
            except Exception:
                pass
            # Funpay USDT-курс
            _fp = _get_funpay_rate()
            if _fp > 0:
                return _fp
            # ЦБ РФ с кешем
            import time as _t
            if _usd_cache[0] > 0 and _t.time() - _usd_cache[1] < 3600:
                return _usd_cache[0]
            try:
                import urllib.request as _ur, xml.etree.ElementTree as _ET
                with _ur.urlopen("https://www.cbr.ru/scripts/XML_daily.asp", timeout=6) as _resp:
                    _tree = _ET.fromstring(_resp.read())
                for _v in _tree.findall("Valute"):
                    if (_v.find("CharCode") is not None
                            and _v.find("CharCode").text == "USD"):
                        _val = float(_v.find("Value").text.replace(",", "."))
                        _nom = int(_v.find("Nominal").text)
                        rate = _val / _nom
                        _usd_cache[0] = rate
                        _usd_cache[1] = _t.time()
                        return rate
            except Exception:
                pass
            return _usd_cache[0] if _usd_cache[0] > 0 else 0.0

        def _rub_fmt(amount: float) -> str:
            """₽1 234  (≈ $15.50)"""
            s = f"₽{amount:,.0f}"
            rate = _get_usd_rate()
            if rate > 0:
                s += f"  _(≈ ${amount / rate:,.2f})_"
            return s

        def _rub_plain(amount: float) -> str:
            """₽1 234 (≈ $15.50) без Markdown-курсива."""
            s = f"₽{amount:,.0f}"
            rate = _get_usd_rate()
            if rate > 0:
                s += f" (≈ ${amount / rate:,.2f})"
            return s

        def _usd_disp(amount_usd: float) -> str:
            """$4.50 (≈ ₽360) — для себестоимости в долларах."""
            s = f"${amount_usd:,.2f}"
            rate = _get_usd_rate()
            if rate > 0:
                s += f" (≈ ₽{amount_usd * rate:,.0f})"
            return s

        def _load_sales() -> list:
            try:
                return json.loads(_SALES_FILE.read_text(encoding="utf-8")) if _SALES_FILE.exists() else []
            except Exception:
                return []

        def _save_sales(records: list) -> None:
            _SALES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

        def _load_scfg() -> dict:
            try:
                return json.loads(_SCFG_FILE.read_text(encoding="utf-8")) if _SCFG_FILE.exists() else {}
            except Exception:
                return {}

        def _save_scfg(cfg_s: dict) -> None:
            _SCFG_FILE.write_text(json.dumps(cfg_s, ensure_ascii=False, indent=2), encoding="utf-8")

        def _record_sale(phone: str, plan: str, sell: float) -> None:
            import time as _t
            scfg = _load_scfg()
            cost_usd = float(scfg.get(f"cost_{plan}", 0))
            rate = _get_usd_rate()
            cost = cost_usd * rate if (cost_usd > 0 and rate > 0) else 0.0
            records = _load_sales()
            records.append({"ts": _t.time(), "phone": phone, "plan": plan,
                            "sell": sell, "cost": cost})
            _save_sales(records)

        def _sales_text(period: str = "all") -> str:
            from datetime import datetime, timedelta
            records = _load_sales()
            scfg    = _load_scfg()
            now     = datetime.now(tz=MSK)
            if period == "today":
                cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                label  = f"Сегодня ({now.strftime('%d %b')})"
            elif period == "week":
                cutoff = (now - timedelta(days=7)).timestamp()
                label  = "За 7 дней"
            elif period == "month":
                cutoff = (now - timedelta(days=30)).timestamp()
                label  = "За 30 дней"
            else:
                cutoff = 0
                label  = "Всё время"

            rows = [r for r in records if r.get("ts", 0) >= cutoff]
            cnt      = len(rows)
            revenue  = sum(r.get("sell", 0) for r in rows)
            costs    = sum(r.get("cost", 0) for r in rows)
            profit   = revenue - costs
            cnt_3m   = sum(1 for r in rows if r.get("plan") == "3m")
            cnt_12m  = sum(1 for r in rows if r.get("plan") == "12m")

            c3  = scfg.get("cost_3m",  "не задана")
            c12 = scfg.get("cost_12m", "не задана")
            c3s  = _usd_disp(c3)  if isinstance(c3,  (int, float)) else str(c3)
            c12s = _usd_disp(c12) if isinstance(c12, (int, float)) else str(c12)

            _rate     = _get_usd_rate()
            _fp_rate  = _funpay_rate_cache[0]
            _man_rate = float(scfg.get("usd_rate", 0) or 0)
            if _man_rate > 0:
                _rate_src = f"₽{_man_rate:,.2f} (ручной)"
            elif _fp_rate > 0:
                _rate_src = f"₽{_fp_rate:,.3f} (Funpay)"
            elif _rate > 0:
                _rate_src = f"₽{_rate:,.2f} (ЦБ РФ)"
            else:
                _rate_src = "не получен"

            lines = [
                f"📊 *Продажи — {label}*",
                "━━━━━━━━━━━━━━━━━━━━━━", "",
                f"🛍 Продаж: *{cnt}*" + (f"  _(3м: {cnt_3m}, 12м: {cnt_12m})_" if cnt else ""),
                f"💵 Выручка: *{_rub_plain(revenue)}*",
                f"💸 Себестоимость: *{_rub_plain(costs)}*",
                f"📈 Прибыль: *{_rub_plain(profit)}*",
                "",
                "⚙️ *Себестоимость (настройки):*",
                f"▸ 3 месяца: *{c3s}*",
                f"▸ 12 месяцев: *{c12s}*",
                f"▸ Курс $1 = *{_rate_src}*",
            ]
            return "\n".join(lines)

        def _sales_kb(period: str = "all") -> dict:
            def _btn(label, p):
                return {"text": f"● {label}" if p == period else label,
                        "callback_data": f"sales:period:{p}"}
            return {"inline_keyboard": [
                [_btn("Всё время", "all"),   _btn("Сегодня", "today")],
                [_btn("7 дней",   "week"),   _btn("30 дней", "month")],
                [{"text": "⚙️ Себестоимость", "callback_data": "sales:config"}],
                [{"text": "◀️ Назад",          "callback_data": "go:other"}],
            ]}

        # ── Клавиатуры для выбора количества и режима ─────────────────────────
        def _count_kb(mode, back="go:launch"):
            return {"inline_keyboard": [
                [{"text": "1",  "callback_data": f"runcnt:{mode}:1"},
                 {"text": "3",  "callback_data": f"runcnt:{mode}:3"},
                 {"text": "5",  "callback_data": f"runcnt:{mode}:5"}],
                [{"text": "10", "callback_data": f"runcnt:{mode}:10"},
                 {"text": "20", "callback_data": f"runcnt:{mode}:20"},
                 {"text": "50", "callback_data": f"runcnt:{mode}:50"}],
                [{"text": "📋 Из конфига", "callback_data": f"runcnt:{mode}:0"}],
                [{"text": "◀️ Назад",      "callback_data": back}],
            ]}

        def _full_count_kb(m="3"):
            return {"inline_keyboard": [
                [{"text": "1",  "callback_data": f"fullm:1:{m}"},
                 {"text": "3",  "callback_data": f"fullm:3:{m}"},
                 {"text": "5",  "callback_data": f"fullm:5:{m}"}],
                [{"text": "10", "callback_data": f"fullm:10:{m}"},
                 {"text": "20", "callback_data": f"fullm:20:{m}"},
                 {"text": "50", "callback_data": f"fullm:50:{m}"}],
                [{"text": "📋 Из конфига", "callback_data": f"fullm:0:{m}"}],
                [{"text": "◀️ Назад",      "callback_data": "go:launch"}],
            ]}

        def _full_mode_kb(cnt, m="3"):
            s = str(cnt)
            return {"inline_keyboard": [
                [{"text": "🌑 Фоновый (без окна) — рекомендуется",
                  "callback_data": f"fullmode:{s}:headless:{m}"}],
                [{"text": "🖥 Обычный (с окном)",
                  "callback_data": f"fullmode:{s}:normal:{m}"}],
                [{"text": "◀️ Назад", "callback_data": f"full:{m}"}],
            ]}

        def _tariff_kb(state, mode):
            rows = []
            for i, c in enumerate(state):
                t   = 3 if c == "3" else 12
                s3  = state[:i] + "3" + state[i+1:]
                s12 = state[:i] + "1" + state[i+1:]
                rows.append([
                    {"text": f"#{i+1}  {'✅' if t==3 else '·'} 3 мес",
                     "callback_data": f"settar:{s3}:{mode}"},
                    {"text": f"{'✅' if t==12 else '·'} 12 мес",
                     "callback_data": f"settar:{s12}:{mode}"},
                ])
            n = len(state)
            rows += [
                [{"text": "Все → 3 мес",  "callback_data": f"settar:{'3'*n}:{mode}"},
                 {"text": "Все → 12 мес", "callback_data": f"settar:{'1'*n}:{mode}"}],
                [{"text": "▶️ Запустить", "callback_data": f"fullrun:{state}:{mode}"}],
                [{"text": "◀️ Назад",     "callback_data": "go:launch"}],
            ]
            return {"inline_keyboard": rows}

        def _single_tariff_kb(cnt, mode, m="3"):
            s = str(cnt)
            return {"inline_keyboard": [
                [{"text": "🥈 3 мес · ₹399",    "callback_data": f"fullrunall:{s}:3:{mode}"}],
                [{"text": "🥇 12 мес · ₹1,499", "callback_data": f"fullrunall:{s}:12:{mode}"}],
                [{"text": "◀️ Назад", "callback_data": f"fullmode:{s}:{mode}:{m}"}],
            ]}

        # ══════════════════════════════════════════════════════════════════════
        # API helpers
        # ══════════════════════════════════════════════════════════════════════

        async def _send(cid, txt, **kw):
            try:
                await client.post(f"{api}/sendMessage",
                                  json={"chat_id": cid, "text": txt,
                                        "parse_mode": "Markdown", **kw})
            except Exception:
                pass

        async def _edit(cid, mid, txt, kb=None, **kw):
            payload = {"chat_id": cid, "message_id": mid,
                       "text": txt, "parse_mode": "Markdown", **kw}
            if kb is not None:
                payload["reply_markup"] = kb
            try:
                r = await client.post(f"{api}/editMessageText", json=payload)
                if r.status_code == 400:
                    body = r.text[:300]
                    # «message is not modified» — not an error
                    if "message is not modified" in body:
                        return
                    print(f"[TG _edit] 400 Markdown error, retrying plain: {body}", flush=True)
                    # Markdown parse error — retry as plain text
                    payload2 = {**payload}
                    del payload2["parse_mode"]
                    r2 = await client.post(f"{api}/editMessageText", json=payload2)
                    if r2.status_code not in (200,):
                        print(f"[TG _edit] retry also failed: {r2.status_code} {r2.text[:300]}", flush=True)
                elif r.status_code != 200:
                    print(f"[TG _edit] failed {r.status_code}: {r.text[:300]}", flush=True)
            except Exception as _e:
                print(f"[TG _edit] exception: {_e}", flush=True)

        async def _ack(qid, txt="", alert=False):
            try:
                await client.post(f"{api}/answerCallbackQuery",
                                  json={"callback_query_id": qid,
                                        "text": txt, "show_alert": alert})
            except Exception:
                pass

        async def _goto_main(cid, mid):
            await _edit(cid, mid, _main_text(), _main_kb(cid))

        # ══════════════════════════════════════════════════════════════════════
        # Фоновые операции
        # ══════════════════════════════════════════════════════════════════════

        def _find_profile(phone):
            if not DONE_PROFILES_DIR or not DONE_PROFILES_DIR.exists():
                return None
            for p in DONE_PROFILES_DIR.glob(f"profile_*{phone}*"):
                if p.is_dir():
                    return p
            return None

        def _unpack(r):
            return (r[0], str(r[1])) if isinstance(r, tuple) and len(r) > 1 \
                   else (bool(r), "")

        def _save_activation_result(pp, result):
            if not isinstance(result, dict):
                return
            meta_updates = {}
            if result.get("status"):
                meta_updates["status"] = result["status"]
            if result.get("valid_till"):
                meta_updates["black_valid_till"] = result["valid_till"]
            if result.get("activation_url"):
                meta_updates["black_activation_link"] = result["activation_url"]
            if result.get("short_link"):
                meta_updates["black_short_link"] = result["short_link"]
            if meta_updates:
                try:
                    _m("_save_meta_field")(pp, **meta_updates)
                except Exception:
                    pass

        async def _bg_activate(cid, phone):
            _bg_ops[phone] = "running"
            await _send(cid, f"⏳ Проверяю активацию <code>{phone}</code>...", parse_mode="HTML")
            try:
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return
                loop   = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, lambda: asyncio.run(
                    _m("_check_black_store_activation")(pp, username=phone, headless=True)))
                _save_activation_result(pp, result)
                st  = result.get("status", "?") if isinstance(result, dict) else "?"
                vt  = (result.get("valid_till") or "") if isinstance(result, dict) else ""
                err = (result.get("error") or "")     if isinstance(result, dict) else str(result)

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                err_safe = escape_html(err)

                if st == "activate_now":
                    act_url = (result.get("activation_url") or "") if isinstance(result, dict) else ""
                    short   = (result.get("short_link")    or "") if isinstance(result, dict) else ""
                    msg = f"⭐ <b>{phone}</b> — Activate Now (готов к выдаче)"
                    if act_url:
                        msg += f"\n\n🔗 <a href=\"{act_url}\">{act_url}</a>"
                    if short and short != act_url:
                        msg += f"\n🔗 {short}"
                    await _send(cid, msg, parse_mode="HTML", disable_web_page_preview=True,
                                reply_markup={"inline_keyboard": [
                                    [{"text": "📤 Отправить покупателю",
                                      "callback_data": f"profile:send_to_buyer:{phone}:0"}],
                                    [{"text": "◀️ В главное меню",
                                      "callback_data": "go:main"}],
                                ]})
                elif st == "activated":
                    # Ссылка активирована покупателем — предлагаем перенести в архив (с подтверждением)
                    await _send(cid,
                        f"✨ <b>{phone}</b> — АКТИВИРОВАН\nДо: {vt}\n\n"
                        f"<i>Ссылка активирована — можно перенести профиль в архив.</i>",
                        parse_mode="HTML",
                        reply_markup={"inline_keyboard": [
                            [{"text": "📦 Перенести в архив",
                              "callback_data": f"profile:archive_one:{phone}"}],
                            [{"text": "👤 Перейти в профиль",
                              "callback_data": f"profile:menu:{phone}:active"}],
                            [{"text": "◀️ В главное меню",
                              "callback_data": "go:main"}],
                        ]})
                else:
                    msgs = {
                        "explore_now":   f"✅ <b>{phone}</b> — Explore Now",
                        "not_logged_in": f"🔒 <b>{phone}</b> — не авторизован",
                        "access_denied": f"🌐 <b>{phone}</b> — нет доступа\n<i>Проверьте подключение к интернету / VPN</i>",
                        "unknown":       f"❓ <b>{phone}</b> — нет ответа от Flipkart\n<i>Проверьте подключение к интернету / VPN</i>",
                    }
                    await _send(cid, msgs.get(st,
                        f"❓ <b>{phone}</b> — {st}" + (f"\n{err_safe}" if err_safe else "")),
                        parse_mode="HTML",
                        reply_markup={"inline_keyboard": [
                            [{"text": "◀️ В главное меню",
                              "callback_data": "go:main"}],
                        ]})
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка проверки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)

        async def _bg_check_issued_all(cid):
            """Проверяет активацию Black по всем Выданным профилям.
            Активированные — предлагает перенести в архив.
            Неактивированные — обновляет короткую ссылку в профиле."""
            _, _, _, active = _get_profile_categories()
            if not active:
                await _send(cid, "🔵 _Выданных профилей нет_")
                return
            await _send(cid, f"🔍 *Проверяю {len(active)} выданных профилей...*\n_Это займёт время._")

            activated_phones = []
            link_updated = 0
            no_link = 0
            no_access = 0
            errors = 0

            for ph, pp, _ in active:
                try:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda pp=pp, ph=ph: asyncio.run(
                        _m("_check_black_store_activation")(pp, username=ph, headless=True)))
                    _save_activation_result(pp, result)
                    st = result.get("status", "?") if isinstance(result, dict) else "?"

                    if st == "activated":
                        activated_phones.append(ph)
                    elif st in ("unknown", "access_denied"):
                        no_access += 1
                    else:
                        # Не активирован — проверяем, обновилась ли ссылка
                        new_link = (result.get("short_link") or result.get("activation_url") or "") \
                                   if isinstance(result, dict) else ""
                        if new_link:
                            link_updated += 1
                        else:
                            no_link += 1
                except Exception:
                    errors += 1

            # Итог
            lines = [
                f"✅ *Проверка выданных завершена* ({len(active)} шт.)",
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"✨ Активированы покупателем: *{len(activated_phones)}*",
                f"🔗 Ссылка обновлена: *{link_updated}*",
                f"⏳ Ещё не активированы: *{no_link}*",
            ]
            if no_access:
                lines.append(f"🌐 Нет доступа к Flipkart: *{no_access}* _(проверьте интернет / VPN)_")
            if errors:
                lines.append(f"❓ Ошибки: *{errors}*")

            kb_rows = []
            if activated_phones:
                _pending_issued_archive[cid] = activated_phones
                kb_rows.append([{"text": f"📦 Перенести {len(activated_phones)} активированных в архив",
                                  "callback_data": "profiles:archive_issued_all"}])
            kb_rows.append([{"text": "◀️ К выданным", "callback_data": "profiles:list:active"}])
            await _send(cid, "\n".join(lines), reply_markup={"inline_keyboard": kb_rows})

        async def _bg_refresh_link(cid, phone):
            """Проверяет активацию Black и обновляет короткую ссылку в профиле."""
            import time as _time_rl
            _bg_ops[phone] = "running"
            await _send(cid, f"🔄 Обновляю ссылку для <code>{phone}</code>...", parse_mode="HTML")
            try:
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return
                loop   = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, lambda: asyncio.run(
                    _m("_check_black_store_activation")(pp, username=phone, headless=True)))
                st    = result.get("status", "?")       if isinstance(result, dict) else "?"
                short = (result.get("short_link") or "") if isinstance(result, dict) else ""
                aurl  = (result.get("activation_url") or "") if isinstance(result, dict) else ""
                link  = short or aurl

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                if link:
                    _m("_save_meta_field")(pp, link_received_ts=_time_rl.time())
                    msg = (f"🔄 <b>{phone}</b> — ссылка обновлена\n\n"
                           f"🔗 {escape_html(link)}")
                    await _send(cid, msg, parse_mode="HTML", disable_web_page_preview=True,
                                reply_markup={"inline_keyboard": [
                                    [{"text": "👤 Перейти в профиль",
                                      "callback_data": f"profile:menu:{phone}:active"}],
                                    [{"text": "📤 Отправить покупателю",
                                      "callback_data": f"profile:send_to_buyer:{phone}:0"}],
                                ]})
                else:
                    await _send(cid,
                        f"⚠️ <b>{phone}</b>: свежая ссылка недоступна (статус: {escape_html(str(st))})",
                        parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка обновления <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)

        async def _send_oos_confirm(cid, phone, retry_note=""):
            """OOS — профиль НЕ удалён. Спрашиваем подтверждение на удаление (Да/Нет)."""
            await _send(cid,
                f"🚫 <b>{phone}</b> — Currently out of stock{retry_note}\n"
                f"<i>Товар недоступен для адреса этого профиля.</i>\n\n"
                f"Удалить профиль?",
                parse_mode="HTML",
                reply_markup={"inline_keyboard": [[
                    {"text": "🗑 Да, удалить", "callback_data": f"profile:oosdel:{phone}"},
                    {"text": "✖️ Нет, оставить", "callback_data": f"profile:ooskeep:{phone}"},
                ]]})

        async def _bg_address(cid, phone):
            _bg_ops[phone] = "running"
            _have_lock = False
            if _op_lock.locked():
                try:
                    await _send(cid, f"⏳ <code>{phone}</code> в очереди — ждёт завершения текущей операции…",
                                parse_mode="HTML")
                except Exception:
                    pass
            await _send(cid, f"⏳ Заполняю адрес для <code>{phone}</code>...",
                        parse_mode="HTML", reply_markup=_buy_stop_kb())
            try:
                await _op_lock.acquire()
                _have_lock = True
                try:
                    _m("_purchase_cancel").clear()
                except Exception:
                    pass
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return
                addr = _m("_gen_indian_address")()
                loop = asyncio.get_running_loop()
                raw  = await loop.run_in_executor(None, lambda: asyncio.run(
                    _m("_do_fill_address")(pp, addr)))
                ok, msg2 = _unpack(raw)

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                msg2_safe = escape_html(msg2)
                addr_str = escape_html(f"{addr.get('pincode','')} {addr.get('city','')}")

                if ok:
                    await _send(cid, f"✅ <b>{phone}</b> — адрес заполнен\n"
                                     f"<code>{addr_str}</code>", parse_mode="HTML")
                elif msg2 == "CANCELLED":
                    await _send(cid, f"🛑 <b>{phone}</b> — остановлено", parse_mode="HTML")
                elif msg2 in ("OUT_OF_STOCK", "OUT_OF_STOCK_2"):
                    _retry_note = " (адрес введён 2 раза)" if msg2 == "OUT_OF_STOCK_2" else ""
                    await _send_oos_confirm(cid, phone, _retry_note)
                else:
                    await _send(cid, f"⚠️ <b>{phone}</b>: {msg2_safe}", parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка адреса <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)
                if _have_lock:
                    try:
                        _op_lock.release()
                    except Exception:
                        pass

        async def _bg_fill_data(cid, phone):
            """Заполняет все данные до оплаты и переносит профиль в «С данными»."""
            _bg_ops[phone] = "running"
            _have_lock = False
            if _op_lock.locked():
                try:
                    await _send(cid, f"⏳ <code>{phone}</code> в очереди — ждёт завершения текущей операции…",
                                parse_mode="HTML")
                except Exception:
                    pass
            await _send(cid, f"⚡ Заполняю данные для <code>{phone}</code>...\n"
                             f"<i>Адрес → чекаут → страница оплаты → закрыть</i>",
                        parse_mode="HTML", reply_markup=_buy_stop_kb())
            try:
                await _op_lock.acquire()
                _have_lock = True
                try:
                    _m("_purchase_cancel").clear()
                except Exception:
                    pass
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return
                addr = _m("_gen_indian_address")()
                loop = asyncio.get_running_loop()
                raw  = await loop.run_in_executor(None, lambda: asyncio.run(
                    _m("_do_fill_address")(pp, addr, stop_at_payment=True)))
                ok, msg2 = _unpack(raw)

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                if ok:
                    addr_str = escape_html(f"{addr.get('pincode','')} {addr.get('city','')}")
                    await _send(cid, f"✅ <b>{phone}</b> — данные заполнены\n"
                                     f"<code>{addr_str}</code>\n"
                                     f"<i>Профиль перенесён в «С данными»</i>", parse_mode="HTML")
                elif msg2 == "CANCELLED":
                    await _send(cid, f"🛑 <b>{phone}</b> — остановлено", parse_mode="HTML")
                elif msg2 in ("OUT_OF_STOCK", "OUT_OF_STOCK_2"):
                    _retry_note = " (адрес введён 2 раза)" if msg2 == "OUT_OF_STOCK_2" else ""
                    await _send_oos_confirm(cid, phone, _retry_note)
                else:
                    await _send(cid, f"⚠️ <b>{phone}</b>: {escape_html(msg2)}", parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)
                if _have_lock:
                    try:
                        _op_lock.release()
                    except Exception:
                        pass

        async def _bg_cookies(cid, phone):
            _bg_ops[phone] = "running"
            await _send(cid, f"⏳ Экспортирую куки <code>{phone}</code>...", parse_mode="HTML")
            try:
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return

                def _export():
                    import grizzly as _gz
                    _gz._kill_chrome_for_profile_standalone(pp)
                    import time as _t; _t.sleep(1)
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as pw:
                        chrome = _m("_find_chrome")()
                        kw = {"headless": True, "args": ["--no-sandbox"]}
                        if chrome:
                            kw["executable_path"] = chrome
                        ctx = pw.chromium.launch_persistent_context(
                            str(pp.resolve()), **kw)
                        raw = ctx.cookies()
                        ctx.close()
                        return raw

                loop    = asyncio.get_running_loop()
                raw_cookies = await loop.run_in_executor(None, _export)
                if not raw_cookies:
                    await _send(cid, f"⚠️ <code>{phone}</code>: куки пусты (не залогинен?)", parse_mode="HTML")
                    return

                ss_map = {"Lax": "lax", "Strict": "strict", "None": "no_restriction", "": "no_restriction"}
                allowed_names = {"T", "ULSN", "at", "rt", "vd", "ud", "S", "SN"}
                all_fk = [c for c in raw_cookies if "flipkart.com" in (c.get("domain") or "").lower() and c.get("name") in allowed_names]
                if not all_fk:
                    await _send(cid, f"⚠️ <code>{phone}</code>: куки flipkart.com не найдены", parse_mode="HTML")
                    return

                cookies_out = [
                    {
                        "name":           c["name"],
                        "value":          c["value"],
                        "domain":         c.get("domain", ".flipkart.com"),
                        "path":           c.get("path", "/"),
                        "secure":         bool(c.get("secure", True)),
                        "httpOnly":       bool(c.get("httpOnly", False)),
                        "expirationDate": c.get("expires", -1),
                        "sameSite":       ss_map.get(c.get("sameSite") or "", "no_restriction"),
                    }
                    for c in all_fk
                ]

                cookies_json = json.dumps(cookies_out, ensure_ascii=False, indent=2)
                cookies_json_compact = json.dumps(cookies_out, ensure_ascii=False, separators=(",", ":"))

                # Локальный бэкап куков на диск
                try:
                    _bk_dir = Path("cookies_backup")
                    _bk_dir.mkdir(exist_ok=True)
                    _bk_name = f"cookies_{phone}.json"
                    (_bk_dir / _bk_name).write_text(cookies_json, encoding="utf-8")
                except Exception:
                    pass

                phone_code = f"<code>{phone}</code>"
                caption = f"🍪 Файл кук {phone_code} ({len(cookies_out)} шт.)"
                fname = f"cookies_{phone}.json"

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                safe_json = escape_html(cookies_json_compact)
                _hdr = f"Куки {phone} ({len(cookies_out)} шт.)"
                _tags_len = len(f"{_hdr}\n<pre><code class=\"language-json\"></code></pre>")
                _tg_max = 4096 - _tags_len - 10
                _body = safe_json if len(safe_json) <= _tg_max else safe_json[:_tg_max]
                text_msg = f"{_hdr}\n<pre><code class=\"language-json\">{_body}</code></pre>"

                import io
                # 1. Отправка файла
                try:
                    await client.post(f"{api}/sendDocument",
                        data={"chat_id": str(cid), "caption": caption, "parse_mode": "HTML"},
                        files={"document": (fname, io.BytesIO(cookies_json.encode("utf-8")), "application/json")})
                except Exception as fe:
                    await _send(cid, f"❌ Ошибка отправки файла кук: {fe}")

                # 2. Текст одним сообщением (обрезается если длиннее лимита TG)
                await client.post(f"{api}/sendMessage",
                                  json={"chat_id": cid, "text": text_msg, "parse_mode": "HTML"})
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка куки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)

        async def _bg_cookies_archived(cid, phone):
            _bk_dir = Path("cookies_backup")
            _bk_name = f"cookies_{phone}.json"
            _bk_path = _bk_dir / _bk_name
            if not _bk_path.exists():
                await _send(cid, f"❌ Бэкап куков для <code>{phone}</code> не найден", parse_mode="HTML")
                return

            try:
                cookies_json_raw = _bk_path.read_text(encoding="utf-8")
                cookies_out_all = json.loads(cookies_json_raw)
                allowed_names = {"T", "ULSN", "at", "rt", "vd", "ud", "S", "SN"}
                cookies_out = [c for c in cookies_out_all if c.get("name") in allowed_names]
                if not cookies_out:
                    await _send(cid, f"⚠️ <code>{phone}</code>: в бэкапе нет нужных сессионных кук", parse_mode="HTML")
                    return
                cookies_json = json.dumps(cookies_out, ensure_ascii=False, indent=2)
                cookies_json_compact = json.dumps(cookies_out, separators=(",", ":"))

                phone_code = f"<code>{phone}</code>"
                caption = f"🍪 Файл кук {phone_code} ({len(cookies_out)} шт.)"
                fname = f"cookies_{phone}.json"

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                safe_json = escape_html(cookies_json_compact)
                _hdr = f"Куки {phone} ({len(cookies_out)} шт.)"
                _tags_len = len(f"{_hdr}\n<pre><code class=\"language-json\"></code></pre>")
                _tg_max = 4096 - _tags_len - 10
                _body = safe_json if len(safe_json) <= _tg_max else safe_json[:_tg_max]
                text_msg = f"{_hdr}\n<pre><code class=\"language-json\">{_body}</code></pre>"

                import io
                # 1. Отправка файла
                try:
                    await client.post(f"{api}/sendDocument",
                        data={"chat_id": str(cid), "caption": caption, "parse_mode": "HTML"},
                        files={"document": (fname, io.BytesIO(cookies_json.encode("utf-8")), "application/json")})
                except Exception as fe:
                    await _send(cid, f"❌ Ошибка отправки файла кук: {fe}")

                # 2. Текст одним сообщением (обрезается если длиннее лимита TG)
                await client.post(f"{api}/sendMessage",
                                  json={"chat_id": cid, "text": text_msg, "parse_mode": "HTML"})
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка куки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")

        async def _bg_buy(cid, phone, months):
            _bg_ops[phone] = "running"
            _have_lock = False
            if _op_lock.locked():
                try:
                    await _send(cid, f"⏳ <code>{phone}</code> в очереди — ждёт завершения текущей операции…",
                                parse_mode="HTML")
                except Exception:
                    pass
            tariff = "₹1,499 · 12 мес." if months == 12 else "₹399 · 3 мес."
            await _send(cid, f"⏳ <b>Покупка Black Membership</b>\n\n<code>{phone}</code>\n💳 {tariff}",
                        parse_mode="HTML", reply_markup=_buy_stop_kb())
            try:
                await _op_lock.acquire()
                _have_lock = True
                try:
                    _m("_purchase_cancel").clear()
                except Exception:
                    pass
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return
                cards = []
                try:
                    if CARDS_FILE.exists():
                        cards = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass
                if not cards:
                    await _send(cid, "❌ Карты не настроены")
                    return
                loop = asyncio.get_running_loop()
                # card=None — применяется единый порядок карт (data/card_order.json)
                raw  = await loop.run_in_executor(None, lambda: asyncio.run(
                    _m("_do_buy_membership")(pp, months, None)))
                ok, msg_r = _unpack(raw)

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                msg_r_safe = escape_html(msg_r)

                if ok:
                    await _send(cid, f"✅ <b>{phone}</b> — куплено\n<i>{msg_r_safe}</i>", parse_mode="HTML")
                elif msg_r == "CANCELLED":
                    await _send(cid, f"🛑 <b>{phone}</b> — остановлено", parse_mode="HTML")
                elif msg_r.startswith("OUT_OF_STOCK"):
                    _rn = " (адрес введён 2 раза)" if "OUT_OF_STOCK_2" in msg_r else ""
                    await _send_oos_confirm(cid, phone, _rn)
                else:
                    await _send(cid, f"⚠️ <b>{phone}</b> — не куплено\n<i>{msg_r_safe or 'неизвестно'}</i>", parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка покупки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)
                if _have_lock:
                    try:
                        _op_lock.release()
                    except Exception:
                        pass

        async def _bg_check_all(cid):
            """Проверяет активацию всех профилей."""
            profiles = [p for p in
                        (DONE_PROFILES_DIR.glob("profile_*") if DONE_PROFILES_DIR.exists() else [])
                        if p.is_dir()]
            if not profiles:
                await _send(cid, "📁 _Готовых профилей нет_")
                return
            await _send(cid, f"⏳ *Проверяю {len(profiles)} профилей...*\n_Это займёт время._")
            activated = activate_now = explore = not_logged = no_access = errors = 0
            error_details = []
            for pp in profiles:
                phone = pp.name.replace("profile_", "")
                try:
                    loop   = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda pp=pp: asyncio.run(
                        _m("_check_black_store_activation")(pp, username=phone, headless=True)))
                    _save_activation_result(pp, result)
                    st  = result.get("status", "?") if isinstance(result, dict) else "?"
                    err = result.get("error") if isinstance(result, dict) else None
                    if st == "activated":                  activated    += 1
                    elif st == "activate_now":             activate_now += 1
                    elif st == "explore_now":              explore      += 1
                    elif st == "not_logged_in":            not_logged   += 1
                    elif st in ("access_denied", "unknown"): no_access  += 1
                    else:
                        errors += 1
                        if err:
                            error_details.append(f"{pp.name}: {err}")
                except Exception as _e:
                    errors += 1
                    error_details.append(f"{pp.name}: {_e}")
            lines = [
                f"✅ *Проверка завершена* ({len(profiles)} профилей)",
                "━━━━━━━━━━━━━━━━━━━━━━\n",
                f"✨ Активированы: *{activated}*",
                f"⭐ Activate Now: *{activate_now}*",
                f"🔵 Explore Now: *{explore}*",
                f"🔒 Не авторизованы: *{not_logged}*",
            ]
            if no_access:
                lines.append(f"🌐 Нет доступа к Flipkart: *{no_access}* _(проверьте подключение к интернету / VPN)_")
            if errors:
                lines.append(f"❓ Ошибки: *{errors}*")
                for d in error_details[:3]:
                    lines.append(f"  • `{d}`")
            await _send(cid, "\n".join(lines))

        async def _bg_address_all(cid):
            """Заполняет адрес для профилей без адреса."""
            profiles = [p for p in
                        (DONE_PROFILES_DIR.glob("profile_*") if DONE_PROFILES_DIR.exists() else [])
                        if p.is_dir()]
            if not profiles:
                await _send(cid, "📁 _Готовых профилей нет_")
                return
            need = [pp for pp in profiles
                    if not _m("_read_profile_meta")(pp).get("address_pincode")]
            if not need:
                await _send(cid, "✅ _У всех профилей уже есть адрес_")
                return
            if _op_lock.locked():
                await _send(cid, "⏳ _В очереди — ждёт завершения текущей операции…_")
            await _send(cid, f"⏳ *Заполняю адрес для {len(need)} профилей...*")
            ok_cnt = fail_cnt = 0
            await _op_lock.acquire()
            try:
                for pp in need:
                    try:
                        addr = _m("_gen_indian_address")()
                        loop = asyncio.get_running_loop()
                        raw  = await loop.run_in_executor(None, lambda pp=pp, a=addr: asyncio.run(
                            _m("_do_fill_address")(pp, a)))
                        ok, _ = _unpack(raw)
                        if ok: ok_cnt   += 1
                        else:  fail_cnt += 1
                    except Exception:
                        fail_cnt += 1
            finally:
                try:
                    _op_lock.release()
                except Exception:
                    pass
            await _send(cid,
                f"📍 *Адреса заполнены*\n\n"
                f"✅ Успешно: *{ok_cnt}*\n"
                f"❌ Ошибки: *{fail_cnt}*")

        async def _bg_fill_all(cid):
            """Заполняет адрес + доходит до страницы оплаты для всех Доступных профилей."""
            noaddr, _, _, _ = _get_profile_categories()
            need = [(ph, pp) for ph, pp, m in noaddr if m.get("login_ts")]
            if not need:
                await _send(cid, "✅ _Нет профилей в категории «Доступные»_")
                return
            if _op_lock.locked():
                await _send(cid, "⏳ _В очереди — ждёт завершения текущей операции…_")
            await _send(cid, f"⚡ *Заполняю все доступные профили* ({len(need)} шт.)\n_Адрес → чекаут → страница оплаты → закрыть_",
                        reply_markup=_buy_stop_kb())
            ok_cnt = fail_cnt = oos_cnt = oos2_cnt = 0
            fail_phones = []
            oos_phones = []
            oos2_phones = []
            await _op_lock.acquire()
            try:
                _m("_purchase_cancel").clear()
            except Exception:
                pass
            for ph, pp in need:
                if _m("_purchase_cancel").is_set():
                    break
                try:
                    addr = _m("_gen_indian_address")()
                    loop = asyncio.get_running_loop()
                    raw  = await loop.run_in_executor(None, lambda pp=pp, a=addr: asyncio.run(
                        _m("_do_fill_address")(pp, a, stop_at_payment=True)))
                    ok, msg_r = _unpack(raw)
                    if ok:
                        ok_cnt += 1
                    elif msg_r == "OUT_OF_STOCK_2":
                        oos2_cnt += 1
                        oos2_phones.append(ph)
                    elif msg_r == "OUT_OF_STOCK":
                        oos_cnt += 1
                        oos_phones.append(ph)
                    else:
                        fail_cnt += 1
                        fail_phones.append(ph)
                except Exception as _fe:
                    fail_cnt += 1
                    fail_phones.append(ph)
            try:
                _op_lock.release()
            except Exception:
                pass
            _oos_total = oos_cnt + oos2_cnt
            lines = [
                f"⚡ *Готово* ({len(need)} профилей)",
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"✅ Успешно: *{ok_cnt}*",
            ]
            if _oos_total:
                lines.append(f"🚫 Out of stock: *{_oos_total}* _(профили НЕ удалены — подтвердите ниже)_")
            if fail_cnt:
                lines.append(f"❌ Ошибки: *{fail_cnt}*")
                for fp in fail_phones[:5]:
                    lines.append(f"  • `{fp}`")
            await _send(cid, "\n".join(lines))
            # По каждому OOS-профилю — отдельное подтверждение удаления (Да/Нет)
            for fp in oos2_phones:
                await _send_oos_confirm(cid, fp, " (адрес введён 2 раза)")
            for fp in oos_phones:
                await _send_oos_confirm(cid, fp, "")

        async def _bg_install(cid):
            """Устанавливает зависимости."""
            await _send(cid, "⏳ *Устанавливаю зависимости...*\n`pip install -r requirements.txt`")
            try:
                loop = asyncio.get_running_loop()
                def _pip():
                    req = Path(__file__).parent / "requirements.txt"
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-r", str(req), "--upgrade"],
                        capture_output=True, text=True, timeout=120,
                        encoding="utf-8", errors="replace")
                    return r.returncode, ((r.stdout or "") + (r.stderr or ""))
                code, out = await loop.run_in_executor(None, _pip)
                out = out[-1500:] if len(out) > 1500 else out
                icon = "✅" if code == 0 else f"⚠️ (код {code})"
                await _send(cid, f"{icon} *Зависимости*\n\n```\n{out}\n```")
            except Exception as e:
                await _send(cid, f"❌ Ошибка: {e}")

        # ══════════════════════════════════════════════════════════════════════
        # Управление процессом
        # ══════════════════════════════════════════════════════════════════════

        async def _watch_proc(notify):
            p = _proc[0]
            if not p:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, p.wait)
            code = p.returncode
            _paused[0] = False
            text = ("✅ Автоматизация завершена успешно" if code == 0
                    else "🛑 Автоматизация остановлена" if code in (-1, None, -15)
                    else "🌐 Flipkart недоступен — повторите позже" if code == 2
                    else f"⚠️ Завершена с кодом {code}")
            for c in list(notify):
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": c, "text": text})
                except Exception:
                    pass
            ct = _ctrl[0]
            if ct.get("chat_id") and ct.get("msg_id"):
                try:
                    await _edit(ct["chat_id"], ct["msg_id"],
                                f"🚀 *Запуск автоматизации*\n\n{text}\n\nВыберите режим:",
                                _launch_kb())
                except Exception:
                    pass
                _ctrl[0] = {}

        async def _do_run(cid, mode, count=None, mid=0):
            args = [sys.executable, str(Path(__file__).parent / "main.py")]
            if mode == "headless" or mode == "tg":
                args.append("--headless")
            if mode == "tg":
                args.append("--tg-intercept")
            else:
                args.append("--tg-login")
            if count:
                args += ["--accounts", str(count)]
            try:
                import os
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                loop = asyncio.get_running_loop()
                proc = await loop.run_in_executor(None, lambda: subprocess.Popen(args, creationflags=creationflags))
                _proc[0]   = proc
                _mode[0]   = mode
                _paused[0] = False
                _notify[0] = {cid}
                if mid:
                    _ctrl[0] = {"chat_id": cid, "msg_id": mid}
                    await _edit(cid, mid, _launch_text(), _launch_kb())
                asyncio.create_task(_watch_proc(_notify[0]))
            except Exception as exc:
                txt = f"❌ Ошибка запуска: {exc}"
                if mid:
                    await _edit(cid, mid, txt,
                                {"inline_keyboard": [[{"text": "◀️ Назад",
                                                       "callback_data": "go:launch"}]]})
                else:
                    await _send(cid, txt)

        async def _do_run_full(cid, tariffs, mode, mid=0, from_cfg=False):
            args = [sys.executable, str(Path(__file__).parent / "menu.py"), "--full-cycle",
                    "--tariffs", ",".join(str(m) for m in tariffs)]
            if from_cfg:
                args += ["--accounts", "0"]
            if mode == "headless":
                args.append("--headless")
            try:
                import os
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                loop = asyncio.get_running_loop()
                proc = await loop.run_in_executor(None, lambda: subprocess.Popen(args, creationflags=creationflags))
                _proc[0]   = proc
                _mode[0]   = f"full:{','.join(set(str(m) for m in tariffs))}:{mode}"
                _paused[0] = False
                _notify[0] = {cid}
                if mid:
                    _ctrl[0] = {"chat_id": cid, "msg_id": mid}
                    await _edit(cid, mid, _launch_text(), _launch_kb())
                asyncio.create_task(_watch_proc(_notify[0]))
            except Exception as exc:
                txt = f"❌ Ошибка запуска: {exc}"
                if mid:
                    await _edit(cid, mid, txt,
                                {"inline_keyboard": [[{"text": "◀️ Назад",
                                                       "callback_data": "go:launch"}]]})
                else:
                    await _send(cid, txt)

        async def _wz_execute(cid, br, mode, tariff, count, mid=0):
            if mode in ("login", "address", "intercept"):
                args = [sys.executable, str(Path(__file__).parent / "main.py")]
                if mode == "intercept":
                    args.append("--tg-intercept")
                else:
                    args.append("--tg-login")
            else:
                args = [sys.executable, str(Path(__file__).parent / "menu.py")]
                args.append("--full-cycle")
                t_val = tariff if tariff in ("3", "12") else "3"
                args += ["--tariffs", t_val]
            if br == "headless":
                args.append("--headless")
            if count:
                args += ["--accounts", str(count)]
            try:
                import os
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                loop = asyncio.get_running_loop()
                proc = await loop.run_in_executor(None, lambda: subprocess.Popen(args, creationflags=creationflags))
                _proc[0]   = proc
                _mode[0]   = f"wz:{br}:{mode}:{tariff}"
                _paused[0] = False
                _notify[0] = {cid}
                if mid:
                    _ctrl[0] = {"chat_id": cid, "msg_id": mid}
                    await _edit(cid, mid, _launch_text(), _launch_kb())
                asyncio.create_task(_watch_proc(_notify[0]))
            except Exception as exc:
                txt = f"❌ Ошибка запуска: {exc}"
                if mid:
                    await _edit(cid, mid, txt,
                                {"inline_keyboard": [[{"text": "◀️ Назад",
                                                       "callback_data": "go:launch"}]]})
                else:
                    await _send(cid, txt)

        async def _do_stop(cid, mid=0):
            if not _running():
                if mid:
                    await _edit(cid, mid,
                                "🚀 *Запуск автоматизации*\n\nℹ️ Нет активного процесса.",
                                _launch_kb())
                return
            try:
                import os, signal
                if os.name == "nt":
                    _proc[0].send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    _proc[0].terminate()
                if mid:
                    await _edit(cid, mid,
                                "🚀 *Запуск автоматизации*\n\n🛑 Сигнал остановки отправлен...",
                                _launch_kb())
            except Exception as exc:
                if mid:
                    await _edit(cid, mid, f"❌ Ошибка остановки: {exc}", _launch_kb())

        # ══════════════════════════════════════════════════════════════════════
        # Фоновая проверка обновлений
        # ══════════════════════════════════════════════════════════════════════

        async def _bg_update_loop():
            global _update_available, _update_commits, _update_checked, \
                   _notified_update_hashes
            _cwd = Path(__file__).parent

            def _fetch():
                if not (_cwd / ".git").exists():
                    return _m("_http_check_updates")()
                _git_ok = False
                try:
                    _fr = subprocess.run([_GIT, "fetch", "--quiet", "origin"],
                                         capture_output=True, timeout=20, cwd=_cwd)
                    if _fr.returncode == 0:
                        r2 = subprocess.run(
                            [_GIT, "log", "HEAD..FETCH_HEAD", "--oneline", "--no-color"],
                            capture_output=True, text=True, timeout=10, cwd=_cwd,
                            encoding="utf-8", errors="replace")
                        _git_ok = True
                        return [l.strip() for l in r2.stdout.strip().splitlines() if l.strip()]
                except Exception:
                    pass
                if not _git_ok:
                    return _m("_http_check_updates")()

            async def _send_update_notification(nc):
                if not nc:
                    return
                def _esc(t):
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                body = "\n".join(f"<blockquote>{_esc(c)}</blockquote>" for c in nc[:10])
                if len(nc) > 10:
                    body += f"\n<i>...и ещё {len(nc)-10}</i>"
                msg = (
                    "⬆️ <b>Новое обновление!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Новых коммитов: <b>{len(nc)}</b>\n\n"
                    f"{body}\n\n"
                    "<i>Нажмите кнопку для обновления:</i>"
                )
                kb = {"inline_keyboard": [[
                    {"text": "⬆️ Обновить сейчас", "callback_data": "update:pull"},
                    {"text": "⏭ Позже",            "callback_data": "go:main"},
                ]]}
                for _cid in list(subs):
                    try:
                        async with httpx.AsyncClient(
                                timeout=httpx.Timeout(8.0), trust_env=False) as c2:
                            await c2.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": _cid, "text": msg,
                                      "parse_mode": "HTML",
                                      "reply_markup": kb})
                    except Exception:
                        pass

            # Инициализация: проверяем и уведомляем о новых коммитах при старте
            try:
                _init = await asyncio.get_event_loop().run_in_executor(None, _fetch)
                _update_available = bool(_init)
                _update_commits   = _init
                _update_checked   = True
                if _init:
                    fhash = {c.split()[0] for c in _init if c}
                    new_h = fhash - _notified_update_hashes
                    if new_h:
                        _notified_update_hashes.update(fhash)
                        _save_notified_updates(_notified_update_hashes)
                        nc = [c for c in _init if c.split()[0] in new_h]
                        await _send_update_notification(nc)
            except Exception:
                pass

            await asyncio.sleep(300)
            while True:
                try:
                    fetched = await asyncio.get_event_loop().run_in_executor(None, _fetch)
                    fhash   = {c.split()[0] for c in fetched if c}
                    new_h   = fhash - _notified_update_hashes
                    _update_available = bool(fetched)
                    _update_commits   = fetched
                    _update_checked   = True
                    if new_h:
                        _notified_update_hashes.update(fhash)
                        _save_notified_updates(_notified_update_hashes)
                        nc   = [c for c in fetched if c.split()[0] in new_h]
                        await _send_update_notification(nc)
                except Exception:
                    pass
                await asyncio.sleep(300)

        # ══════════════════════════════════════════════════════════════════════
        # Обработчик callback query
        # ══════════════════════════════════════════════════════════════════════

        async def _handle_cbq(client, cbq):
            global _update_available, _update_commits, _update_checked
            qid  = cbq["id"]
            cid  = int(cbq["message"]["chat"]["id"])
            mid  = cbq["message"]["message_id"]
            data = cbq.get("data", "")

            # noop ─────────────────────────────────────────────────────────────
            if data == "noop":
                await _ack(qid)
                return

            # Навигация: главное меню ──────────────────────────────────────────
            if data in ("go:main", "show:main"):
                await _ack(qid)
                await _goto_main(cid, mid)
                return

            # Навигация: запуск ────────────────────────────────────────────────
            if data in ("go:launch", "show:actions", "go:run_ctrl"):
                await _ack(qid)
                if _server_mode and not _is_console_running():
                    await _edit(cid, mid,
                        "❌ *Консоль не запущена*\n\n"
                        "_Бот работает в серверном режиме._\n"
                        "_Для запуска автоматизации запустите_ `menu.py` _или_ `main.py` _локально._",
                        {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:main"}]]})
                    return
                await _edit(cid, mid, _launch_text(), _launch_kb())
                if _running():
                    _ctrl[0] = {"chat_id": cid, "msg_id": mid}
                return

            # Навигация: профили ───────────────────────────────────────────────
            if data == "go:profiles":
                await _ack(qid)
                await _edit(cid, mid, _profiles_text(), _profiles_kb())
                return

            # Навигация: другое ────────────────────────────────────────────────
            if data == "go:other":
                await _ack(qid)
                await _edit(cid, mid, _other_text(cid), _other_kb(cid))
                return

            # Переключение уведомлений ─────────────────────────────────────────
            if data in ("t:buy_number", "t:otp_code"):
                key = "buy_number" if data == "t:buy_number" else "otp_code"
                cur = _get(cid, key)
                _set(cid, key, not cur)
                lbl = "Покупка" if key == "buy_number" else "OTP"
                await _ack(qid, f"{'✅' if not cur else '❌'} {lbl} "
                                 f"{'включена' if not cur else 'выключена'}")
                await _edit(cid, mid, _other_text(cid), _other_kb(cid))
                return

            # Список профилей ──────────────────────────────────────────────────
            if data in ("show:profileslist", "profiles:list") or data.startswith("profiles:list:"):
                list_type = "noaddr"
                if data.startswith("profiles:list:"):
                    list_type = data.split(":", 2)[2]
                await _ack(qid)
                try:
                    txt_pl = _profile_list_text(list_type)
                    kb_pl  = _profile_list_kb(list_type)
                except Exception as _ple:
                    txt_pl = f"📁 *Профили*\n\n❌ Ошибка: `{_ple}`"
                    kb_pl  = {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": "go:profiles"}]]}
                await _edit(cid, mid, txt_pl, kb_pl)
                return

            if data.startswith("profile:menu:"):
                parts = data.split(":")
                phone = parts[2]
                list_type = parts[3] if len(parts) > 3 else "noaddr"
                rec_file = parts[4] if len(parts) > 4 else ""

                await _ack(qid)
                busy = _bg_ops.get(phone) == "running"
                # Загружаем мета для отображения дат и ссылки
                _pm = {}
                if list_type == "archive" and rec_file:
                    # Архивный профиль: читаем запись из chrome_profiles_used
                    try:
                        _rec = USED_PROFILES_DIR / f"record_{rec_file}.json"
                        if _rec.exists():
                            _pm = json.loads(_rec.read_text(encoding="utf-8"))
                            for _k_ts, _k_str in (("login_ts", "login_str"),
                                                  ("issued_ts", "issued_str")):
                                if _pm.get(_k_ts) and not _pm.get(_k_str):
                                    try:
                                        _pm[_k_str] = _m("_fmt_msk")(float(_pm[_k_ts]))
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                else:
                    try:
                        _pp = _find_profile(phone)
                        if _pp:
                            _pm = _m("_read_profile_meta")(_pp)
                    except Exception:
                        pass
                _pm_login   = _pm.get("login_str") or ""
                _pm_issued  = _pm.get("issued_str") or ""
                _pm_vt      = (_pm.get("black_valid_till")
                               or _pm.get("subscription_expires_str") or "")
                _pm_slink   = _pm.get("black_short_link") or _pm.get("issued_link") or ""
                _pm_inv     = _pm.get("issued_invoice_id") or ""
                _pm_email   = _pm.get("buyer_email") or ""
                _info = f"📱 <code>{_disp_phone(phone)}</code>"
                if _pm_login:
                    _info += f"\n📆 Создан:  <code>{_pm_login}</code>"
                if _pm_issued:
                    _info += f"\n✅ Выдан:   <code>{_pm_issued}</code>"
                if _pm_inv:
                    _ord_line = f"\n📦 Заказ:   <code>#{_pm_inv}</code>"
                    if _pm_email:
                        _ord_line += f"  ·  <code>{_pm_email}</code>"
                    _info += _ord_line
                if _pm_vt:
                    _info += f"\n⏳ До:       <b>{_pm_vt}</b>"
                if _pm_slink:
                    _info += f"\n🔗 <a href=\"{_pm_slink}\">{_pm_slink}</a>"
                _pm_note = _pm.get("note") or ""
                if _pm_note:
                    _info += f"\n📝 <i>{_pm_note}</i>"
                txt = (_info + "\n\n" +
                       ("⏳ <i>Операция выполняется...</i>" if busy else "Выберите действие:"))
                await _edit(cid, mid, txt, _profile_menu_kb(phone, list_type, rec_file), parse_mode="HTML")
                return

            if data.startswith("profile:note:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                cur_note = ""
                if pp:
                    try:
                        cur_note = _m("_read_profile_meta")(pp).get("note") or ""
                    except Exception:
                        pass
                _note_waiting[cid] = phone
                await _ack(qid, "")
                _note_hint = f"Текущее: «{cur_note}»\n\n" if cur_note else ""
                await _send(cid,
                    f"📝 <b>Примечание к профилю</b> <code>{_disp_phone(phone)}</code>\n\n"
                    f"{_note_hint}"
                    f"Отправьте текст примечания или <code>-</code> для удаления.",
                    parse_mode="HTML")
                return

            if data.startswith("profile:shownum:"):
                phone = data.split(":", 2)[2]
                await _ack(qid)
                await _send(cid, f"<code>{phone}</code>", parse_mode="HTML")
                return

            if data.startswith("profile:cookies_archived:"):
                parts = data.split(":")
                phone = parts[2]
                await _ack(qid, "⏳ Читаю архивные куки...")
                asyncio.create_task(_bg_cookies_archived(cid, phone))
                return

            if data.startswith("profile:shortlink:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                try:
                    m = _m("_read_profile_meta")(pp)
                except Exception:
                    m = {}
                
                link = m.get("black_activation_link") or ""
                await _ack(qid)
                if link:
                    await _send(cid, f"🔗 Короткая ссылка для <code>{phone}</code>:\n<code>{link}</code>", parse_mode="HTML")
                else:
                    await _send(cid, f"⚠️ Короткая ссылка для <code>{phone}</code> не найдена в метаданных", parse_mode="HTML")
                return

            # ── Продажи ───────────────────────────────────────────────────────
            if data == "go:sales":
                await _ack(qid)
                await _edit(cid, mid, _sales_text("all"), _sales_kb("all"))
                return

            if data.startswith("sales:period:"):
                period = data.split(":", 2)[2]
                await _ack(qid)
                await _edit(cid, mid, _sales_text(period), _sales_kb(period))
                return

            if data == "sales:config":
                await _ack(qid)
                scfg = _load_scfg()
                c3  = scfg.get("cost_3m",  "не задана")
                c12 = scfg.get("cost_12m", "не задана")
                c3s  = _usd_disp(c3)  if isinstance(c3,  (int, float)) else str(c3)
                c12s = _usd_disp(c12) if isinstance(c12, (int, float)) else str(c12)
                _manual_rate = scfg.get("usd_rate", 0)
                _fp_rate     = _funpay_rate_cache[0]
                _cbr_rate    = _usd_cache[0]
                _gk          = scfg.get("funpay_golden_key", "")
                if _manual_rate and float(_manual_rate) > 0:
                    _rate_str = f"₽{float(_manual_rate):,.2f} (ручной)"
                elif _fp_rate > 0:
                    _rate_str = f"₽{_fp_rate:,.2f} (Funpay)"
                elif _cbr_rate > 0:
                    _rate_str = f"₽{_cbr_rate:,.2f} (ЦБ РФ)"
                else:
                    _rate_str = "не получен"
                _gk_status = f"***{_gk[-4:]}" if _gk else "не задан"
                txt = (
                    "⚙️ *Себестоимость*\n\n"
                    f"▸ 3 месяца: *{c3s}*\n"
                    f"▸ 12 месяцев: *{c12s}*\n"
                    f"▸ Курс $1 = *{_rate_str}*\n"
                    f"▸ Funpay key: `{_gk_status}`\n\n"
                    "_Нажмите кнопку для изменения:_"
                )
                kb = {"inline_keyboard": [
                    [{"text": f"✏️ 3 мес ({c3s})",  "callback_data": "sales:set_cost:3m"}],
                    [{"text": f"✏️ 12 мес ({c12s})", "callback_data": "sales:set_cost:12m"}],
                    [{"text": f"💱 Курс USD ({_rate_str})", "callback_data": "sales:set_usd_rate"}],
                    [{"text": f"🔑 Funpay golden_key", "callback_data": "sales:set_funpay_key"}],
                    [{"text": "◀️ Назад", "callback_data": "go:sales"}],
                ]}
                await _edit(cid, mid, txt, kb)
                return

            if data.startswith("sales:set_cost:"):
                plan = data.split(":")[-1]  # "3m" или "12m"
                await _ack(qid)
                _sales_cost_waiting[cid] = plan
                label = "3 месяца" if plan == "3m" else "12 месяцев"
                await _send(cid, f"💬 Введите себестоимость для *{label}* (в долларах, $):",
                            parse_mode="Markdown",
                            reply_markup={"inline_keyboard": [[{"text": "❌ Отмена",
                                                                "callback_data": "sales:config"}]]})
                return

            if data == "sales:set_usd_rate":
                await _ack(qid)
                _sales_cost_waiting[cid] = "usd_rate"
                cur = _get_usd_rate()
                cur_s = f"сейчас: ₽{cur:,.2f}" if cur > 0 else "не задан"
                await _send(cid,
                            f"💱 Введите курс $1 в рублях ({cur_s}):\n\n"
                            "_Оставьте `0` чтобы использовать курс Funpay/ЦБ РФ._",
                            parse_mode="Markdown",
                            reply_markup={"inline_keyboard": [[{"text": "❌ Отмена",
                                                                "callback_data": "sales:config"}]]})
                return

            if data == "sales:set_funpay_key":
                await _ack(qid)
                _sales_cost_waiting[cid] = "funpay_key"
                scfg = _load_scfg()
                _gk_cur = scfg.get("funpay_golden_key", "")
                _gk_s = f"задан (***{_gk_cur[-4:]})" if _gk_cur else "не задан"
                await _send(cid,
                            f"🔑 *Funpay golden_key* ({_gk_s})\n\n"
                            "Откройте браузер, войдите на funpay.com, зайдите в DevTools → "
                            "Application → Cookies → funpay.com и скопируйте значение "
                            "cookie `golden_key`.\n\n"
                            "Введите значение сюда. Пришлите `0` чтобы удалить.",
                            parse_mode="Markdown",
                            reply_markup={"inline_keyboard": [[{"text": "❌ Отмена",
                                                                "callback_data": "sales:config"}]]})
                return

            if data.startswith("profile:record_sale:"):
                phone = data.split(":", 2)[2]
                await _ack(qid)
                scfg = _load_scfg()
                c3   = scfg.get("cost_3m",  0)
                c12  = scfg.get("cost_12m", 0)
                c3s  = _usd_disp(c3)  if c3  else "$?"
                c12s = _usd_disp(c12) if c12 else "$?"
                _p3      = int(scfg.get("preset_sell_3m", 700) or 700)
                _p3_usd  = float(scfg.get("preset_sell_3m_usd", 8.5) or 8.5)
                _cur_r   = _get_usd_rate()
                _p3_from_usd = round(_p3_usd * _cur_r, 2) if _cur_r > 0 else 0
                _usd_btn_lbl = (f"🥈 3м · ${_p3_usd} = {int(_p3_from_usd)}₽"
                                if _p3_from_usd > 0 else f"🥈 3м · ${_p3_usd}")
                txt = (
                    f"💰 *Записать продажу*\n\n"
                    f"Профиль: `{_disp_phone(phone)}`\n\n"
                    "Выберите тариф:"
                )
                kb = {"inline_keyboard": [
                    [{"text": f"🥈 3м · {_p3}₽",
                      "callback_data": f"profile:sale_fast:3m:{phone}:{_p3}"},
                     {"text": _usd_btn_lbl,
                      "callback_data": f"profile:sale_fast:3m:{phone}:{_p3_from_usd}"}],
                    [{"text": "✏️ 3м · своя сумма",
                      "callback_data": f"profile:sale:3m:{phone}"}],
                    [{"text": f"🥇 12 месяцев (себ. {c12s})",
                      "callback_data": f"profile:sale:12m:{phone}"}],
                    [{"text": "❌ Отмена", "callback_data": f"profile:menu:{phone}:active"}],
                ]}
                await _send(cid, txt, parse_mode="Markdown", reply_markup=kb)
                return

            if data.startswith("profile:sale_fast:"):
                # profile:sale_fast:3m:{phone}:{amount_rub}
                _sf = data.split(":", 4)
                _sf_plan, _sf_phone, _sf_amt = _sf[2], _sf[3], _sf[4]
                await _ack(qid)
                try:
                    sell = float(_sf_amt)
                    _record_sale(_sf_phone, _sf_plan, sell)
                    scfg2 = _load_scfg()
                    cost_usd = float(scfg2.get(f"cost_{_sf_plan}", 0))
                    _r2 = _get_usd_rate()
                    cost = cost_usd * _r2 if (cost_usd > 0 and _r2 > 0) else 0.0
                    profit = sell - cost
                    label = "3 мес" if _sf_plan == "3m" else "12 мес"
                    await _send(cid,
                        f"✅ *Продажа записана*\n\n"
                        f"📱 `{_disp_phone(_sf_phone)}`  📦 *{label}*\n"
                        f"💵 Выручка: *{_rub_plain(sell)}*\n"
                        f"💸 Себестоимость: *{_rub_plain(cost)}*\n"
                        f"📈 Прибыль: *{_rub_plain(profit)}*",
                        parse_mode="Markdown",
                        reply_markup={"inline_keyboard": [
                            [{"text": "📊 Продажи", "callback_data": "go:sales"}],
                        ]})
                except Exception:
                    await _ack(qid, "❌ Ошибка записи", alert=True)
                return

            if data.startswith("profile:sale:"):
                parts = data.split(":", 3)  # profile sale 3m/12m phone
                plan  = parts[2]
                phone = parts[3]
                await _ack(qid)
                _sale_input_waiting[cid] = {"phone": phone, "plan": plan}
                label = "3 месяца" if plan == "3m" else "12 месяцев"
                await _send(cid,
                            f"💬 *{label}* — введите сумму продажи:\n"
                            f"• в рублях: `700`\n"
                            f"• в долларах: `$8.5`",
                            parse_mode="Markdown",
                            reply_markup={"inline_keyboard": [[{"text": "❌ Отмена",
                                                                "callback_data": f"profile:menu:{phone}:active"}]]})
                return

            if data.startswith("profile:archive_one:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                await _ack(qid, "⏳ Архивирую профиль...")
                
                loop = asyncio.get_running_loop()
                def _do_arch():
                    archive_fn = _m("_archive_profile")
                    return archive_fn(pp)
                
                ok = await loop.run_in_executor(None, _do_arch)
                if ok:
                    await _send(cid, f"✅ Профиль <code>{phone}</code> успешно заархивирован!", parse_mode="HTML")
                    try:
                        txt_pl = _profile_list_text("active")
                        kb_pl  = _profile_list_kb("active")
                        await _edit(cid, mid, txt_pl, kb_pl)
                    except Exception:
                        await _goto_main(cid, mid)
                else:
                    await _send(cid, f"❌ Не удалось заархивировать профиль <code>{phone}</code>", parse_mode="HTML")
                return

            if data.startswith("profile:unarchive:"):
                rec_key = data.split(":", 2)[2]
                rec_path = USED_PROFILES_DIR / f"record_{rec_key}.json"
                if not rec_path.exists():
                    await _ack(qid, "❌ Запись архива не найдена", alert=True)
                    return
                try:
                    import time as _time2, json as _json2
                    rec_data = _json2.loads(rec_path.read_text(encoding="utf-8"))
                    phone_ua = rec_data.get("username") or rec_key.rsplit("_", 1)[0]
                    profile_dir = DONE_PROFILES_DIR / f"profile_{phone_ua}"
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    rec_data.setdefault("issued_ts", _time2.time())
                    meta_file = profile_dir / ".profile_meta.json"
                    meta_file.write_text(_json2.dumps(rec_data, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
                    rec_path.unlink()
                    await _ack(qid, "🔓 Профиль возвращён в статус «Выдан»")
                    await _edit(cid, mid, _archive_text(), _archive_kb())
                except Exception as exc:
                    await _ack(qid, f"❌ Ошибка: {exc}", alert=True)
                return

            if data.startswith("profile:set_issued:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                import time as _time
                ok = _m("_save_meta_field")(pp, issued_ts=_time.time())
                if ok:
                    await _ack(qid, "🔵 Статус «Выдан» установлен")
                    txt = f"📱 <code>{phone}</code>\n\nВыберите действие:"
                    await _edit(cid, mid, txt, _profile_menu_kb(phone, "active"), parse_mode="HTML")
                else:
                    await _ack(qid, "❌ Не удалось сохранить статус", alert=True)
                return

            if data.startswith("profile:set_active:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                
                ok = _m("_save_meta_field")(pp, status="activated")
                if ok:
                    await _ack(qid, "🟢 Статус изменён на Активный")
                    busy = _bg_ops.get(phone) == "running"
                    txt = (f"📱 <code>{phone}</code>\n\n"
                           f"{'⏳ <i>Операция выполняется...</i>' if busy else 'Выберите действие:'}")
                    await _edit(cid, mid, txt, _profile_menu_kb(phone, "active"), parse_mode="HTML")
                else:
                    await _ack(qid, "❌ Не удалось обновить статус", alert=True)
                return

            if data.startswith("profile:activate:"):
                phone = data.split(":", 2)[2]
                if _bg_ops.get(phone) == "running":
                    await _ack(qid, "⚠️ Уже выполняется", alert=True)
                    return
                await _ack(qid, "⏳ Проверяю...")
                asyncio.create_task(_bg_activate(cid, phone))
                return

            if data.startswith("profile:address:"):
                phone = data.split(":", 2)[2]
                if _bg_ops.get(phone) == "running":
                    await _ack(qid, "⚠️ Уже выполняется", alert=True)
                    return
                await _ack(qid, "⏳ Заполняю адрес...")
                asyncio.create_task(_bg_address(cid, phone))
                return

            if data.startswith("profile:fill_data:"):
                phone = data.split(":", 2)[2]
                if _bg_ops.get(phone) == "running":
                    await _ack(qid, "⚠️ Уже выполняется", alert=True)
                    return
                await _ack(qid, "⚡ Заполняю данные...")
                asyncio.create_task(_bg_fill_data(cid, phone))
                return

            # Подтверждение удаления профиля при OOS (две кнопки Да/Нет)
            if data.startswith("profile:oosdel:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    await _edit(cid, mid, f"❌ Профиль <code>{phone}</code> не найден (уже удалён?)",
                                {"inline_keyboard": []}, parse_mode="HTML")
                    return
                import shutil as _sh_oos
                try:
                    _sh_oos.rmtree(str(pp), ignore_errors=True)
                except Exception:
                    pass
                await _ack(qid, "🗑 Профиль удалён")
                await _edit(cid, mid, f"🗑 Профиль <code>{phone}</code> удалён (Out of stock).",
                            {"inline_keyboard": []}, parse_mode="HTML")
                return

            if data.startswith("profile:ooskeep:"):
                phone = data.split(":", 2)[2]
                await _ack(qid, "Профиль оставлен")
                await _edit(cid, mid, f"✖️ Профиль <code>{phone}</code> оставлен (Out of stock).",
                            {"inline_keyboard": []}, parse_mode="HTML")
                return

            # Удаление активного профиля (не выданного) — Да/Нет
            if data.startswith("profile:del_confirm:"):
                phone = data.split(":", 2)[2]
                await _ack(qid)
                await _edit(cid, mid,
                    f"🗑 Удалить профиль <code>{phone}</code>?\n\n"
                    f"<i>Папка профиля будет удалена безвозвратно.</i>",
                    {"inline_keyboard": [
                        [{"text": "🗑 Да, удалить", "callback_data": f"profile:del_do:{phone}"},
                         {"text": "✖️ Отмена",      "callback_data": f"profile:menu:{phone}:noaddr"}],
                    ]}, parse_mode="HTML")
                return

            if data.startswith("profile:del_do:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    await _edit(cid, mid, f"❌ Профиль <code>{phone}</code> не найден (уже удалён?).",
                                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:profiles"}]]},
                                parse_mode="HTML")
                    return
                import shutil as _sh_del
                try:
                    _sh_del.rmtree(str(pp), ignore_errors=True)
                except Exception:
                    pass
                await _ack(qid, "🗑 Профиль удалён")
                await _edit(cid, mid, f"🗑 Профиль <code>{phone}</code> удалён.",
                            {"inline_keyboard": [[{"text": "◀️ К профилям", "callback_data": "go:profiles"}]]},
                            parse_mode="HTML")
                return

            # Удаление записи из архива — Да/Нет
            if data.startswith("profile:arcdel_confirm:"):
                rec_key = data.split(":", 2)[2]
                await _ack(qid)
                await _edit(cid, mid,
                    f"🗑 Удалить запись архива <code>{rec_key}</code>?\n\n"
                    f"<i>Запись будет удалена безвозвратно (профиль был выдан ранее).</i>",
                    {"inline_keyboard": [
                        [{"text": "🗑 Да, удалить", "callback_data": f"profile:arcdel_do:{rec_key}"},
                         {"text": "✖️ Отмена",      "callback_data": "profiles:list:archive"}],
                    ]}, parse_mode="HTML")
                return

            if data.startswith("profile:arcdel_do:"):
                rec_key = data.split(":", 2)[2]
                rec_path = USED_PROFILES_DIR / f"record_{rec_key}.json" if USED_PROFILES_DIR else None
                if not rec_path or not rec_path.exists():
                    await _ack(qid, "❌ Запись не найдена", alert=True)
                    return
                try:
                    rec_path.unlink()
                except Exception as _e:
                    await _ack(qid, f"❌ Ошибка удаления: {_e}", alert=True)
                    return
                await _ack(qid, "🗑 Запись удалена")
                await _edit(cid, mid, f"🗑 Запись архива <code>{rec_key}</code> удалена.",
                            {"inline_keyboard": [[{"text": "◀️ К архиву", "callback_data": "profiles:archive"}]]},
                            parse_mode="HTML")
                return

            if data.startswith("profile:buy:"):
                parts  = data.split(":")
                months = int(parts[2])
                phone  = parts[3]
                if _bg_ops.get(phone) == "running":
                    await _ack(qid, "⚠️ Уже выполняется", alert=True)
                    return
                await _ack(qid, f"⏳ Покупаю {months} мес...")
                asyncio.create_task(_bg_buy(cid, phone, months))
                return

            if data.startswith("profile:cookies:"):
                phone = data.split(":", 2)[2]
                if _bg_ops.get(phone) == "running":
                    await _ack(qid, "⚠️ Уже выполняется", alert=True)
                    return
                await _ack(qid, "⏳ Экспортирую...")
                asyncio.create_task(_bg_cookies(cid, phone))
                return

            # Пакетные операции профилей ───────────────────────────────────────
            if data == "profiles:checkall":
                await _ack(qid, "⏳ Запускаю проверку всех...")
                asyncio.create_task(_bg_check_all(cid))
                return

            if data == "profiles:addrall":
                await _ack(qid, "⏳ Запускаю заполнение адресов...")
                asyncio.create_task(_bg_address_all(cid))
                return

            if data == "profiles:fill_all":
                await _ack(qid, "⏳ Запускаю заполнение всех доступных...")
                asyncio.create_task(_bg_fill_all(cid))
                return

            if data == "profiles:check_issued_all":
                await _ack(qid, "🔍 Проверяю все выданные...")
                asyncio.create_task(_bg_check_issued_all(cid))
                return

            if data == "profiles:archive_issued_all":
                phones = _pending_issued_archive.pop(cid, [])
                if not phones:
                    await _ack(qid, "⚠️ Нет профилей для архивации", alert=True)
                    return
                await _ack(qid, f"⏳ Архивирую {len(phones)} профилей...")
                loop = asyncio.get_running_loop()
                ok_cnt = fail_cnt = 0
                for _ph in phones:
                    _pp = _find_profile(_ph)
                    if not _pp:
                        fail_cnt += 1
                        continue
                    try:
                        _arch_ok = await loop.run_in_executor(None, lambda pp=_pp: _m("_archive_profile")(pp))
                        if _arch_ok:
                            ok_cnt += 1
                        else:
                            fail_cnt += 1
                    except Exception:
                        fail_cnt += 1
                lines = [f"📦 *Архивация завершена*", f"✅ Перенесено: *{ok_cnt}*"]
                if fail_cnt:
                    lines.append(f"❌ Ошибки: *{fail_cnt}*")
                await _send(cid, "\n".join(lines),
                            reply_markup={"inline_keyboard": [
                                [{"text": "◀️ К выданным", "callback_data": "profiles:list:active"}]]})
                return

            if data == "profiles:archive":
                await _ack(qid)
                await _edit(cid, mid, _archive_text(), _archive_kb())
                return

            if data == "profiles:cookies_info":
                await _ack(qid)
                await _edit(cid, mid,
                    "🍪 *Восстановление из куков*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Запускается из консоли: `[К]`\n\n"
                    "_Положите файл в_ `cookies_backup/` _и нажмите_ `К` _в консоли._",
                    {"inline_keyboard": [[{"text": "◀️ Назад",
                                           "callback_data": "go:profiles"}]]})
                return

            # Прокси ───────────────────────────────────────────────────────────
            if data == "show:proxy":
                await _ack(qid)
                await _edit(cid, mid, _proxy_text(), _proxy_kb())
                return

            if data == "proxy:toggle":
                pcfg = _read_proxy_cfg()
                pcfg["enabled"] = not pcfg.get("enabled", False)
                _write_proxy_cfg(pcfg)
                st = "включён ✅" if pcfg["enabled"] else "выключен ❌"
                await _ack(qid, f"Прокси {st}")
                await _edit(cid, mid, _proxy_text(), _proxy_kb())
                return

            if data == "proxy6:balance":
                await _ack(qid)
                key = _p6_cfg().get("api_key", "").strip()
                if not key:
                    await _ack(qid, "API ключ Proxy6 не настроен", alert=True)
                else:
                    try:
                        bal, cur = _p6_balance(key)
                        await _ack(qid, f"💰 Баланс: {bal} {cur}", alert=True)
                    except Exception as exc:
                        await _ack(qid, f"❌ {exc}", alert=True)
                await _edit(cid, mid, _proxy_text(), _proxy_kb())
                return

            if data == "proxy6:buy":
                await _ack(qid)
                p6  = _p6_cfg()
                key = p6.get("api_key", "").strip()
                if not key:
                    await _ack(qid, "API ключ не настроен", alert=True)
                    await _edit(cid, mid, _proxy_text(), _proxy_kb())
                    return
                try:
                    cnt = int(p6.get("default_count",  10))
                    per = int(p6.get("default_period",  7))
                    new_p, buy_msg = _p6_buy_affordable(
                        key, cnt, per,
                        country=p6.get("country", "in"),
                        proxy_type=p6.get("type", "http"))
                    if not new_p:
                        await _ack(qid, "Proxy6 вернул пустой список", alert=True)
                    else:
                        try:   active = _p6_getlist(key, state="active")
                        except Exception: active = new_p
                        pcfg = _read_proxy_cfg()
                        pcfg.update({"list": active, "enabled": True})
                        for k in ("server", "username", "password"):
                            pcfg.pop(k, None)
                        _write_proxy_cfg(pcfg)
                        await _ack(qid, f"✅ {buy_msg} · {len(active)} активных", alert=True)
                except Exception as exc:
                    await _ack(qid, f"❌ {exc}", alert=True)
                await _edit(cid, mid, _proxy_text(), _proxy_kb())
                return

            if data == "proxy6:sync":
                await _ack(qid)
                key = _p6_cfg().get("api_key", "").strip()
                if not key:
                    await _ack(qid, "API ключ не настроен", alert=True)
                else:
                    try:
                        active = _p6_getlist(key, state="active")
                        pcfg   = _read_proxy_cfg()
                        pcfg.update({"list": active, "enabled": bool(active)})
                        for k in ("server", "username", "password"):
                            pcfg.pop(k, None)
                        _write_proxy_cfg(pcfg)
                        await _ack(qid, f"🔄 Синхронизировано: {len(active)}", alert=True)
                    except Exception as exc:
                        await _ack(qid, f"❌ {exc}", alert=True)
                await _edit(cid, mid, _proxy_text(), _proxy_kb())
                return

            # Пошаговый мастер запуска (Wizard) ───────────────────────────────
            if data.startswith("wz:br:"):
                br = data.split(":")[2]
                await _ack(qid)
                br_lbl = "Обычный" if br == "normal" else "Фоновый"
                await _edit(cid, mid,
                    f"🚀 *Запуск автоматизации*\nРежим браузера: `{br_lbl}`\n\nВыберите режим работы:",
                    _wz_mode_kb(br))
                return

            if data.startswith("wz:md:"):
                _, _, br, mode = data.split(":")
                await _ack(qid)
                br_lbl = "Обычный" if br == "normal" else "Фоновый"
                mode_lbl = {
                    "purchase": "Запуск | Полный цикл",
                    "login": "Запуск | Вход на ПК",
                    "address": "Запуск | Вход с данными",
                    "intercept": "Запуск | Подбор аккаунта TG"
                }.get(mode, mode)
                
                if mode in ("purchase", "address"):
                    await _edit(cid, mid,
                        f"🚀 *Запуск автоматизации*\nРежим браузера: `{br_lbl}`\nРежим работы: `{mode_lbl}`\n\nВыберите тариф:",
                        _wz_tariff_kb(br, mode))
                else:
                    await _edit(cid, mid,
                        f"🚀 *Запуск автоматизации*\nРежим браузера: `{br_lbl}`\nРежим работы: `{mode_lbl}`\n\nВыберите количество:",
                        _wz_count_kb(br, mode, "none"))
                return

            if data.startswith("wz:tf:"):
                _, _, br, mode, tariff = data.split(":")
                await _ack(qid)
                br_lbl = "Обычный" if br == "normal" else "Фоновый"
                mode_lbl = {
                    "purchase": "Запуск | Полный цикл",
                    "login": "Запуск | Вход на ПК",
                    "address": "Запуск | Вход с данными",
                    "intercept": "Запуск | Подбор аккаунта TG"
                }.get(mode, mode)
                tariff_lbl = f"{tariff} мес."
                await _edit(cid, mid,
                    f"🚀 *Запуск автоматизации*\nРежим браузера: `{br_lbl}`\nРежим работы: `{mode_lbl}`\nТариф: `{tariff_lbl}`\n\nВыберите количество:",
                    _wz_count_kb(br, mode, tariff))
                return

            if data.startswith("wz:run:"):
                _, _, br, mode, tariff, count_s = data.split(":")
                count = int(count_s)
                await _ack(qid, "⏳ Запускаю...")
                if _running():
                    try: _proc[0].terminate()
                    except Exception: pass
                    await asyncio.sleep(1.5)
                await _wz_execute(cid, br, mode, tariff, count, mid)
                return

            if data == "run:change":
                m = _mode[0] or "wz:headless:login:none"
                await _ack(qid)
                if m.startswith("wz:"):
                    parts = m.split(":")
                    br, mode, tariff = parts[1], parts[2], parts[3]
                    br_lbl = "Обычный" if br == "normal" else "Фоновый"
                    mode_map = {
                        "purchase": "Запуск | Полный цикл",
                        "login": "Запуск | Вход на ПК",
                        "address": "Запуск | Вход с данными",
                        "intercept": "Запуск | Подбор аккаунта TG"
                    }
                    mode_lbl = mode_map.get(mode, mode)
                    tariff_lbl = f"\nТариф: `{tariff} мес.`" if tariff != "none" else ""
                    await _edit(cid, mid,
                        f"🔄 *Изменить кол-во*\n\nРежим браузера: `{br_lbl}`\nРежим работы: `{mode_lbl}`{tariff_lbl}\n"
                        "_Текущий процесс будет перезапущен._",
                        _wz_count_kb(br, mode, tariff))
                else:
                    await _edit(cid, mid,
                        f"🔄 *Изменить кол-во*\n\nРежим: `{_mode_label(m)}`\n"
                        "_Текущий процесс будет перезапущен._",
                        _wz_count_kb("headless", "login", "none"))
                return

            # Логи ─────────────────────────────────────────────────────────────
            if data == "show:logs":
                await _ack(qid)
                await _edit(cid, mid, _logs_text(), {"inline_keyboard": [[
                    {"text": "🔄 Обновить", "callback_data": "show:logs"},
                    {"text": "◀️ Назад",   "callback_data": "go:other"},
                ]]})
                return

            # Карты ────────────────────────────────────────────────────────────
            if data == "show:cards":
                await _ack(qid)
                _card_order_waiting.pop(cid, None)
                txt, kb = _cards_order_page(cid)
                await _edit(cid, mid, txt, kb)
                return

            if data == "cards:order_reset":
                await _ack(qid, "🔄 Сброшено к умолчанию")
                _save_card_order([])
                txt, kb = _cards_order_page(cid)
                await _edit(cid, mid, txt, kb)
                return

            if data == "cards:order_edit":
                await _ack(qid)
                _card_order_waiting[cid] = True
                await _edit(cid, mid,
                            "✏️ *Введи новый порядок карт*\n\n"
                            "Отправь числа через пробел (например: `1 3 2`).\n"
                            "Это определит, в какой последовательности бот будет пробовать карты при оплате.\n\n"
                            "Чтобы отменить, нажми кнопку ниже.",
                            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "show:cards"}]]})
                return

            # Статистика ───────────────────────────────────────────────────────
            if data == "show:stats":
                await _ack(qid)
                await _edit(cid, mid, _stats_text(),
                            {"inline_keyboard": [[{"text": "◀️ Назад",
                                                    "callback_data": "go:other"}]]})
                return

            # Зависимости ──────────────────────────────────────────────────────
            if data == "deps:install":
                await _ack(qid, "⏳ Устанавливаю...")
                asyncio.create_task(_bg_install(cid))
                return

            # Обновление ───────────────────────────────────────────────────────
            if data == "update:check":
                await _ack(qid, "🔄 Проверяю...")
                _cwd_u = Path(__file__).parent
                try:
                    def _check():
                        if not (_cwd_u / ".git").exists():
                            return _m("_http_check_updates")()
                        try:
                            subprocess.run([_GIT, "fetch", "--quiet", "origin"],
                                           capture_output=True, timeout=20, cwd=_cwd_u)
                            r2 = subprocess.run(
                                [_GIT, "log", "HEAD..FETCH_HEAD", "--oneline", "--no-color"],
                                capture_output=True, text=True, timeout=10, cwd=_cwd_u,
                                encoding="utf-8", errors="replace")
                            return [l.strip() for l in r2.stdout.strip().splitlines() if l.strip()]
                        except Exception:
                            return _m("_http_check_updates")()
                    lines = await asyncio.get_event_loop().run_in_executor(None, _check)
                    _update_available = bool(lines)
                    _update_commits   = lines
                    _update_checked   = True
                except Exception as ue:
                    await _edit(cid, mid, f"❌ Ошибка проверки: `{ue}`",
                                {"inline_keyboard": [[{"text": "◀️ Назад",
                                                       "callback_data": "go:other"}]]})
                    return
                if not _update_available:
                    await _edit(cid, mid,
                        "✅ *Версия актуальна*\n\n_Новых коммитов нет._",
                        {"inline_keyboard": [
                            [{"text": "🔄 Перепроверить", "callback_data": "update:check"}],
                            [{"text": "◀️ Назад",          "callback_data": "go:other"}],
                        ]})
                else:
                    body = "\n".join(f"▸ `{c}`" for c in _update_commits[:10])
                    ext  = (f"\n_...и ещё {len(_update_commits)-10}_"
                            if len(_update_commits) > 10 else "")
                    await _edit(cid, mid,
                        f"⬆️ *Доступно обновлений: {len(_update_commits)}*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{body}{ext}\n\nПрименить обновление?",
                        {"inline_keyboard": [
                            [{"text": "⬆️ Обновить сейчас", "callback_data": "update:pull"},
                             {"text": "◀️ Назад",            "callback_data": "go:other"}],
                        ]})
                return

            if data == "update:pull":
                if _first:
                    await _ack(qid)
                    return
                await _ack(qid, "Применяю обновление...")
                await _edit(cid, mid, "⏳ *Загружаю обновление...*", {"inline_keyboard": []})
                try:
                    ok_upd, msg_upd = await asyncio.get_event_loop().run_in_executor(
                        None, _m("_do_git_update"))
                    if ok_upd:
                        already = ("уже актуальна" in msg_upd or
                                   "already up to date" in msg_upd.lower() or
                                   not msg_upd.strip())
                        if already:
                            await _edit(cid, mid,
                                "✅ *Версия уже актуальна*\n_Новых файлов нет._",
                                {"inline_keyboard": [[{"text": "◀️ Назад",
                                                       "callback_data": "go:other"}]]})
                            return
                        if msg_upd.startswith("Обновлены: "):
                            files  = msg_upd[len("Обновлены: "):].split(", ")
                            flist  = "\n".join(f"  ▸ `{f}`" for f in files)
                            result = (f"✅ *Обновление применено!*\n\n"
                                      f"Обновлены файлы:\n{flist}\n\n"
                                      f"⚡ _Перезапускаю..._")
                        else:
                            result = (f"✅ *Обновление применено!*\n\n"
                                      f"_{msg_upd[:300]}_\n\n"
                                      f"⚡ _Перезапускаю..._")
                        await _edit(cid, mid, result, {"inline_keyboard": []})
                        try:
                            rf = Path(__file__).parent / "._restart_msg.json"
                            rf.write_text(json.dumps({"chat_id": cid, "msg_id": mid,
                                                      "text": result}),
                                          encoding="utf-8")
                        except Exception:
                            pass
                        # Останавливаем запущенный процесс перед рестартом
                        if _running():
                            try:
                                _proc[0].terminate()
                                await asyncio.sleep(2)
                                if _proc[0].returncode is None:
                                    _proc[0].kill()
                            except Exception:
                                pass
                        try:
                            await client.get(f"{api}/getUpdates",
                                             params={"offset": offset, "timeout": 0})
                        except Exception:
                            pass
                        await asyncio.sleep(1)
                        import os as _os; _os._exit(42)
                    else:
                        await _edit(cid, mid,
                            f"❌ *Ошибка обновления*\n\n`{msg_upd[:600]}`",
                            {"inline_keyboard": [[{"text": "◀️ Назад",
                                                   "callback_data": "go:other"}]]})
                except Exception as upe:
                    await _edit(cid, mid, f"❌ Неожиданная ошибка: {upe}",
                                {"inline_keyboard": [[{"text": "◀️ Назад",
                                                       "callback_data": "go:other"}]]})
                return

            if data == "action:restart":
                if _first:
                    await _ack(qid)
                    return
                await _ack(qid, "Перезапускаю консоль...")
                result = "⏳ *Перезапуск консоли...*\n\n⚡ _Перезапускаю..._"
                await _edit(cid, mid, result, {"inline_keyboard": []})
                try:
                    rf = Path(__file__).parent / "._restart_msg.json"
                    rf.write_text(json.dumps({"chat_id": cid, "msg_id": mid,
                                              "text": result}),
                                  encoding="utf-8")
                except Exception:
                    pass
                # Принудительно останавливаем запущенный процесс автоматизации
                if _running():
                    try:
                        _proc[0].terminate()
                        await asyncio.sleep(1)
                        if _proc[0].returncode is None:
                            _proc[0].kill()
                    except Exception:
                        pass
                # Убиваем Chrome-процессы бота в фоне (не блокируем перезапуск)
                try:
                    import threading as _thr_rs
                    import grizzly as _gz_rs
                    _thr_rs.Thread(target=_gz_rs.kill_all_bot_chrome,
                                   daemon=True, name="chrome-kill-restart").start()
                except Exception:
                    pass
                try:
                    await client.get(f"{api}/getUpdates",
                                     params={"offset": offset, "timeout": 0})
                except Exception:
                    pass
                await asyncio.sleep(1)
                try:
                    import menu as _menu_mod
                    _menu_mod._shutting_down = True
                except Exception:
                    pass
                import os as _os
                _os._exit(42)

            # Управление процессом ─────────────────────────────────────────────
            if data in ("run:normal", "run:headless", "run:tg"):
                mode = data.split(":")[1]
                if _running():
                    await _ack(qid, f"⚠️ Уже запущено (PID {_proc[0].pid})", alert=True)
                    return
                await _ack(qid)
                labels = {"normal": "🖥 Запуск | Вход на ПК (обычный)", "headless": "🌑 Запуск | Вход на ПК (фоновый)",
                          "tg": "🔔 Запуск | Подбор аккаунта TG"}
                await _edit(cid, mid,
                    f"▶️ *{labels.get(mode, mode)}*\n\nСколько успешных входов?",
                    _count_kb(mode))
                return

            if data.startswith("runcnt:"):
                _, mode, cnt_s = data.split(":", 2)
                count = int(cnt_s) if cnt_s.isdigit() and int(cnt_s) > 0 else None
                await _ack(qid, "⏳ Запускаю...")
                if _running():
                    try: _proc[0].terminate()
                    except Exception: pass
                    await asyncio.sleep(1.5)
                await _do_run(cid, mode, count, mid)
                return

            if data in ("full:3", "full:12"):
                m = data.split(":")[1]
                if _running():
                    await _ack(qid, f"⚠️ Уже запущено (PID {_proc[0].pid})", alert=True)
                    return
                await _ack(qid)
                lbl = "3 мес · ₹399" if m == "3" else "12 мес · ₹1,499"
                await _edit(cid, mid,
                    f"⚡ *Полный цикл · {lbl}*\n"
                    "_вход + адрес + Buy Now_\n\nСколько аккаунтов?",
                    _full_count_kb(m))
                return

            if data.startswith("fullm:"):
                parts  = data.split(":")
                count  = int(parts[1])
                m      = parts[2] if len(parts) > 2 else "3"
                await _ack(qid)
                lbl = "3 мес · ₹399" if m == "3" else "12 мес · ₹1,499"
                cnt_t = str(count) if count else "из конфига"
                await _edit(cid, mid,
                    f"⚡ *Полный цикл · {lbl} · {cnt_t} акк.*\n\nРежим запуска:",
                    _full_mode_kb(count, m))
                return

            if data.startswith("fullmode:"):
                parts = data.split(":")
                cnt_s, mode, m = parts[1], parts[2], (parts[3] if len(parts) > 3 else "3")
                count = int(cnt_s)
                await _ack(qid)
                mode_lbl = "фоновый" if mode == "headless" else "с окном"
                cnt_t    = str(count) if count else "из конфига"
                def_c    = "1" if m == "12" else "3"
                if 0 < count <= 10:
                    await _edit(cid, mid,
                        f"⚡ *Полный цикл · {cnt_t} акк · {mode_lbl}*\n\nТариф для каждого:",
                        _tariff_kb(def_c * count, mode))
                else:
                    await _edit(cid, mid,
                        f"⚡ *Полный цикл · {cnt_t} акк · {mode_lbl}*\n\nТариф для всех:",
                        _single_tariff_kb(count, mode, m))
                return

            if data.startswith("settar:"):
                _, state, mode = data.split(":")
                await _ack(qid)
                mode_lbl = "фоновый" if mode == "headless" else "с окном"
                await _edit(cid, mid,
                    f"⚡ *Полный цикл · {len(state)} акк · {mode_lbl}*\n\nТариф для каждого:",
                    _tariff_kb(state, mode))
                return

            if data.startswith("fullrun:"):
                _, state, mode = data.split(":")
                tariffs = [3 if c == "3" else 12 for c in state]
                await _ack(qid, "⏳ Запускаю...")
                if _running():
                    try: _proc[0].terminate()
                    except Exception: pass
                    await asyncio.sleep(1.5)
                await _do_run_full(cid, tariffs, mode, mid)
                return

            if data.startswith("fullrunall:"):
                parts  = data.split(":")
                count  = int(parts[1])
                months = int(parts[2])
                mode   = parts[3]
                tariffs = [months] * (count if count > 0 else 1)
                await _ack(qid, "⏳ Запускаю...")
                if _running():
                    try: _proc[0].terminate()
                    except Exception: pass
                    await asyncio.sleep(1.5)
                await _do_run_full(cid, tariffs, mode, mid, from_cfg=(count == 0))
                return

            if data == "run:pause":
                if not _running():
                    await _ack(qid, "ℹ️ Нет активного процесса", alert=True)
                    return
                pid    = _proc[0].pid
                paused = _paused[0]
                try:
                    import ctypes
                    h = ctypes.windll.kernel32.OpenProcess(0x1F0FFF, False, pid)
                    if not paused:
                        ctypes.windll.ntdll.NtSuspendProcess(h)
                        _paused[0] = True
                        alert = "⏸ Приостановлен"
                    else:
                        ctypes.windll.ntdll.NtResumeProcess(h)
                        _paused[0] = False
                        alert = "▶️ Возобновлён"
                    ctypes.windll.kernel32.CloseHandle(h)
                except Exception as exc:
                    alert = f"❌ {exc}"
                await _ack(qid, alert, alert=True)
                try:
                    await client.post(f"{api}/editMessageReplyMarkup",
                                      json={"chat_id": cid, "message_id": mid,
                                            "reply_markup": _launch_kb()})
                except Exception:
                    pass
                return

            if data == "run:change":
                mode = _mode[0] or "normal"
                await _ack(qid)
                labels = {"normal": "🖥 Запуск | Вход на ПК (обычный)", "headless": "🌑 Запуск | Вход на ПК (фоновый)",
                          "tg": "🔔 Запуск | Подбор аккаунта TG"}
                await _edit(cid, mid,
                    f"🔄 *Изменить кол-во*\n\nРежим: *{labels.get(mode, mode)}*\n"
                    "_Текущий процесс будет перезапущен._",
                    _count_kb(mode))
                return

            if data == "run:stop":
                await _ack(qid, "🛑 Останавливаю...")
                await _do_stop(cid, mid)
                return

            if data == "buy:stop":
                try:
                    _m("_purchase_cancel").set()
                except Exception:
                    pass
                await _ack(qid, "🛑 Останавливаю...")
                return

            if data.startswith("pay:switch:") and not data.startswith("pay:switch_confirm:"):
                try:
                    _pos = int(data.split(":")[-1])
                    # Ищем имя карты в _3ds_card_options
                    _card_name = ""
                    try:
                        for _opt in _m("_3ds_card_options"):
                            if _opt.get("pos") == _pos:
                                _c = _opt.get("card", {})
                                _card_name = (_c.get("nickname") or _c.get("name")
                                              or _m("_mask_card")(_c.get("number", "")))
                                break
                    except Exception:
                        pass
                    _card_display = f"*{_card_name}*" if _card_name else f"карту №{_pos + 1}"
                    _kb_confirm = {"inline_keyboard": [
                        [{"text": "✅ Да, сменить карту",
                          "callback_data": f"pay:switch_confirm:{_pos}"}],
                        [{"text": "❌ Нет, продолжить ожидание OTP",
                          "callback_data": "pay:switch_cancel"}],
                    ]}
                    await _edit(cid, mid,
                        f"❓ *Сменить карту?*\n\n"
                        f"Продолжить оплату с карты {_card_display}?\n\n"
                        f"_Текущее ожидание OTP будет прервано._",
                        kb=_kb_confirm)
                except Exception as _sw_err:
                    await _ack(qid, f"Ошибка: {_sw_err}", alert=True)
                    return
                await _ack(qid, "")
                return

            if data.startswith("pay:switch_confirm:"):
                _card_name_sw = ""
                try:
                    _pos = int(data.split(":")[-1])
                    _m("_switch_card_choice")[0] = _pos
                    _m("_switch_card_ev").set()
                    for _opt in _m("_3ds_card_options"):
                        if _opt.get("pos") == _pos:
                            _c = _opt.get("card", {})
                            _card_name_sw = (_c.get("nickname") or _c.get("name")
                                             or _m("_mask_card")(_c.get("number", "")))
                            break
                except Exception:
                    pass
                _sw_lbl = f"*{_card_name_sw}*" if _card_name_sw else f"карту №{_pos + 1}"
                await _ack(qid, "🔄 Переключаю карту...")
                await _edit(cid, mid,
                    f"🔄 *Смена карты...*\n\n"
                    f"Переключаюсь на {_sw_lbl}.\n"
                    f"_Ожидайте, бот продолжает оплату._",
                    {"inline_keyboard": []})
                return

            if data == "pay:switch_cancel":
                await _ack(qid, "✅ Продолжаю ожидание OTP", alert=True)
                return

            if data.startswith("fill:orders_ok:"):
                try:
                    _m("_orders_confirm_choice")[0] = True
                    _m("_orders_confirm_ev").set()
                except Exception:
                    pass
                await _ack(qid, "✅ Продолжаю заполнение...")
                return

            if data.startswith("fill:orders_del:"):
                try:
                    _m("_orders_confirm_choice")[0] = False
                    _m("_orders_confirm_ev").set()
                except Exception:
                    pass
                await _ack(qid, "🗑 Удаляю профиль...")
                return

            if data == "run:status":
                if _running():
                    lbl = _mode_label(_mode[0])
                    st  = "⏸ Пауза" if _paused[0] else "🟢 Работает"
                    txt = f"{st} · {lbl} · PID {_proc[0].pid}"
                else:
                    txt = "🔴 Нет активного процесса"
                await _ack(qid, txt, alert=True)
                return

            # ── GGSell ────────────────────────────────────────────
            if data.startswith("ggsell:") or data == "go:ggsell":
                await _ggsel_handler[0].handle_callback(cid, mid, qid, data)
                return

            if data.startswith("profile:refresh_link:"):
                phone = data.split(":", 2)[2]
                if _bg_ops.get(phone) == "running":
                    await _ack(qid, "⚠️ Уже выполняется", alert=True)
                    return
                await _ack(qid, "🔄 Обновляю ссылку...")
                asyncio.create_task(_bg_refresh_link(cid, phone))
                return

            if data.startswith("profile:send_to_buyer:"):
                parts = data.split(":")
                phone = parts[2]
                offset = int(parts[3]) if len(parts) > 3 else 0
                if not _ggsel_handler[0]:
                    await _ack(qid, "❌ GGSell не настроен", alert=True)
                    return
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                try:
                    m = _m("_read_profile_meta")(pp)
                    # Покупателю — короткую ссылку (clck.ru), длинная только запасной
                    link = m.get("black_short_link") or m.get("black_activation_link") or ""
                except Exception:
                    link = ""
                if not link:
                    await _ack(qid, "⚠️ Ссылка не найдена в профиле", alert=True)
                    return
                await _ack(qid)
                await _edit(cid, mid, "⏳ Загружаю заказы...", {"inline_keyboard": []})
                asyncio.create_task(_ggsel_handler[0].bg_link_to_buyer_page(cid, mid, phone, link, offset))
                return

            if data.startswith("profile:send_to_order:"):
                parts = data.split(":")
                phone = parts[2]
                invoice_id = int(parts[3]) if len(parts) > 3 else 0
                if not _ggsel_handler[0]:
                    await _ack(qid, "❌ GGSell не настроен", alert=True)
                    return
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                try:
                    m = _m("_read_profile_meta")(pp)
                    # Покупателю — короткую ссылку (clck.ru), длинная только запасной
                    link = m.get("black_short_link") or m.get("black_activation_link") or ""
                except Exception:
                    link = ""
                if not link:
                    await _ack(qid, "⚠️ Ссылка не найдена в профиле", alert=True)
                    return
                await _ack(qid, "⏳ Отправляю...")
                asyncio.create_task(_ggsel_handler[0].bg_link_to_order(cid, mid, phone, link, invoice_id))
                return

            # Неизвестная команда
            await _ack(qid)

        # ══════════════════════════════════════════════════════════════════════
        # Обработчик входящих сообщений
        # ══════════════════════════════════════════════════════════════════════

        _OTP_3DS_FILE = Path(__file__).parent / "data" / "tg_otp_3ds.json"

        def _push_otp_3ds(text: str) -> None:
            """Сохранить потенциальный 3DS OTP в файл для menu.py."""
            import re as _re_otp
            m = _re_otp.search(r"\b(\d{4,8})\b", text)
            if not m:
                return
            code = m.group(1)
            try:
                _OTP_3DS_FILE.parent.mkdir(parents=True, exist_ok=True)
                try:
                    existing = json.loads(_OTP_3DS_FILE.read_text(encoding="utf-8"))
                except Exception:
                    existing = []
                existing.append(code)
                _OTP_3DS_FILE.write_text(
                    json.dumps(existing, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

        async def _handle_msg(client, msg):
            cid  = int(msg["chat"]["id"])
            text = (msg.get("text") or "").strip()

            # 3DS OTP: ищем 4-8 цифр в любом тексте (fullmatch не ловил forwarded-сообщения)
            import re as _re_otp
            if text and _re_otp.search(r"\b\d{4,8}\b", text):
                _push_otp_3ds(text)

            # Режим ответа в GGSell чат — перехватываем ЛЮБОЕ сообщение
            _ggsel_inv = _ggsel_handler[0].check_reply_mode(cid, text) if _ggsel_handler[0] else None
            if _ggsel_inv is not None:
                asyncio.create_task(_ggsel_handler[0].bg_reply(cid, _ggsel_inv, text))
                return

            # Режим редактирования шаблона GGSell
            _tpl_name = _ggsel_handler[0].check_template_edit_mode(cid, text) if _ggsel_handler[0] else None
            if _tpl_name is not None:
                asyncio.create_task(_ggsel_handler[0].bg_template_save(cid, _tpl_name, text))
                return

            # Режим ввода порядка карт GGSell
            if _ggsel_handler[0] and _ggsel_handler[0].check_card_order_mode(cid, text):
                asyncio.create_task(_ggsel_handler[0].bg_card_order_save(cid, text))
                return

            # Режим ввода примечания к профилю
            if _note_waiting.get(cid):
                _note_phone = _note_waiting.pop(cid)
                _new_note = "" if text == "-" else text
                _saved = False
                try:
                    pp = _find_profile(_note_phone)
                    if pp:
                        _m("_save_meta_field")(pp, note=_new_note)
                        _saved = True
                except Exception:
                    pass
                if _saved:
                    if _new_note:
                        await _send(cid, f"✅ Примечание сохранено:\n<i>{_new_note}</i>",
                                    parse_mode="HTML")
                    else:
                        await _send(cid, "✅ Примечание удалено")
                else:
                    await _send(cid, "❌ Не удалось сохранить примечание")
                return

            # Режим ввода порядка карт для основного бота
            if _card_order_waiting.get(cid):
                _card_order_waiting.pop(cid, None)
                try:
                    if not CARDS_FILE.exists():
                        await client.post(f"{api}/sendMessage",
                                          json={"chat_id": cid, "text": "❌ Нет карт для настройки порядка.",
                                                "reply_markup": {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "show:cards"}]]}})
                        return
                    cards = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
                    if not cards:
                        await client.post(f"{api}/sendMessage",
                                          json={"chat_id": cid, "text": "❌ Нет карт для настройки порядка.",
                                                "reply_markup": {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "show:cards"}]]}})
                        return

                    # Парсим числа
                    import re as _re
                    tokens = _re.split(r"[\s,;]+", text.strip())
                    order = []
                    errors = []
                    for t in tokens:
                        t = t.strip()
                        if not t:
                            continue
                        try:
                            n = int(t)
                            idx = n - 1
                            if not (0 <= idx < len(cards)):
                                errors.append(f"`{t}` — нет такой карты")
                            elif idx in order:
                                errors.append(f"`{t}` — повторяется")
                            else:
                                order.append(idx)
                        except ValueError:
                            errors.append(f"`{t}` — не число")

                    if errors:
                        err_list = "\n".join(f"  • {e}" for e in errors)
                        _card_order_waiting[cid] = True  # возвращаем в режим ввода
                        await client.post(f"{api}/sendMessage",
                                          json={"chat_id": cid,
                                                "text": f"⚠️ Ошибки в порядке:\n{err_list}\n\n_Попробуй ещё раз: отправь числа через пробел, например_ `1 3 2`",
                                                "parse_mode": "Markdown",
                                                "reply_markup": {"inline_keyboard": [[{"text": "💳 Порядок карт", "callback_data": "show:cards"}]]}})
                        return

                    if not order:
                        _card_order_waiting[cid] = True
                        await client.post(f"{api}/sendMessage",
                                          json={"chat_id": cid, "text": "❌ Не удалось прочитать порядок. Отправь числа через пробел: `1 3 2`",
                                                "reply_markup": {"inline_keyboard": [[{"text": "💳 Порядок карт", "callback_data": "show:cards"}]]}})
                        return

                    _save_card_order(order)
                    order_str = " → ".join(
                        f"*{idx + 1}*  _{cards[idx].get('nickname') or 'Карта'}_"
                        for idx in order if idx < len(cards)
                    )
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid,
                                            "text": f"✅ *Порядок карт сохранён!*\n\n{order_str}",
                                            "parse_mode": "Markdown",
                                            "reply_markup": {"inline_keyboard": [
                                                [{"text": "💳 Порядок карт", "callback_data": "show:cards"}],
                                                [{"text": "◀️ Настройки",   "callback_data": "go:other"}],
                                            ]}})
                except Exception as ex:
                    await client.post(f"{api}/sendMessage", json={"chat_id": cid, "text": f"❌ Ошибка: {ex}"})
                return

            # Ввод суммы продажи
            if _sale_input_waiting.get(cid):
                info = _sale_input_waiting.pop(cid)
                phone = info["phone"]
                plan  = info["plan"]
                try:
                    _raw = text.strip().replace(",", ".")
                    _in_usd = _raw.startswith("$") or _raw.lower().endswith(("usd", "$"))
                    _num = float(_raw.lstrip("$").rstrip("usdUSD ").strip())
                    if _num <= 0:
                        raise ValueError("negative")
                    if _in_usd:
                        _rate = _get_usd_rate()
                        if _rate <= 0:
                            raise ValueError("no_rate")
                        sell = round(_num * _rate, 2)
                        _sell_disp = f"${_num} × {_rate:.2f} = {_rub_plain(sell)}"
                    else:
                        sell = _num
                        _sell_disp = _rub_plain(sell)
                    _record_sale(phone, plan, sell)
                    scfg = _load_scfg()
                    cost_usd = float(scfg.get(f"cost_{plan}", 0))
                    _rate2 = _get_usd_rate()
                    cost = cost_usd * _rate2 if (cost_usd > 0 and _rate2 > 0) else 0.0
                    profit = sell - cost
                    label = "3 мес" if plan == "3m" else "12 мес"
                    await _send(cid,
                        f"✅ *Продажа записана*\n\n"
                        f"📱 Профиль: `{_disp_phone(phone)}`\n"
                        f"📦 Тариф: *{label}*\n"
                        f"💵 Выручка: *{_sell_disp}*\n"
                        f"💸 Себестоимость: *{_rub_plain(cost)}*\n"
                        f"📈 Прибыль: *{_rub_plain(profit)}*",
                        parse_mode="Markdown",
                        reply_markup={"inline_keyboard": [
                            [{"text": "📊 Продажи", "callback_data": "go:sales"}],
                        ]})
                except (ValueError, TypeError) as _se:
                    _hint = ("❌ Не удалось получить курс USD. Введите сумму в рублях."
                             if "no_rate" in str(_se) else
                             "❌ Некорректная сумма. Введите число (например: `800` или `$8.5`)")
                    _sale_input_waiting[cid] = info  # вернуть ожидание
                    await _send(cid, _hint, parse_mode="Markdown",
                                reply_markup={"inline_keyboard": [[{"text": "❌ Отмена",
                                                                    "callback_data": f"profile:menu:{phone}:active"}]]})
                return

            # Ввод себестоимости / курса USD / Funpay key
            if _sales_cost_waiting.get(cid):
                key = _sales_cost_waiting.pop(cid)
                # Funpay golden_key — отдельная ветка (не число)
                if key == "funpay_key":
                    scfg = _load_scfg()
                    if text.strip() == "0":
                        scfg.pop("funpay_golden_key", None)
                        _funpay_rate_cache[0] = 0.0
                        _funpay_rate_cache[1] = 0.0
                        _save_scfg(scfg)
                        await _send(cid, "✅ Funpay golden_key удалён — курс будет браться с ЦБ РФ",
                                    reply_markup={"inline_keyboard": [[{"text": "⚙️ Настройки", "callback_data": "sales:config"}]]})
                    else:
                        scfg["funpay_golden_key"] = text.strip()
                        _funpay_rate_cache[0] = 0.0  # сбросить кеш — обновится при следующем запросе
                        _funpay_rate_cache[1] = 0.0
                        _save_scfg(scfg)
                        # Сразу проверяем, работает ли ключ
                        _test_rate = _get_funpay_rate()
                        if _test_rate > 0:
                            await _send(cid,
                                        f"✅ Funpay golden_key сохранён. Курс: *₽{_test_rate:,.3f}*",
                                        parse_mode="Markdown",
                                        reply_markup={"inline_keyboard": [[{"text": "⚙️ Настройки", "callback_data": "sales:config"}]]})
                        else:
                            await _send(cid,
                                        "⚠️ Ключ сохранён, но курс получить не удалось.\n"
                                        "Проверьте правильность golden_key.",
                                        reply_markup={"inline_keyboard": [[{"text": "⚙️ Настройки", "callback_data": "sales:config"}]]})
                    return
                try:
                    val = float(text.replace(",", ".").replace("₽", "").replace("₹", "").strip())
                    if val < 0:
                        raise ValueError("negative")
                    scfg = _load_scfg()
                    if key == "usd_rate":
                        if val == 0:
                            scfg.pop("usd_rate", None)
                            _usd_cache[0] = 0.0  # сбросить кеш
                            _save_scfg(scfg)
                            await _send(cid, "✅ Курс сброшен — будет использоваться Funpay/ЦБ РФ",
                                        reply_markup={"inline_keyboard": [[{"text": "⚙️ Настройки", "callback_data": "sales:config"}]]})
                        else:
                            scfg["usd_rate"] = val
                            _save_scfg(scfg)
                            await _send(cid, f"✅ Курс сохранён: *$1 = ₽{val:,.2f}*",
                                        parse_mode="Markdown",
                                        reply_markup={"inline_keyboard": [[{"text": "⚙️ Настройки", "callback_data": "sales:config"}]]})
                    else:
                        scfg[f"cost_{key}"] = val
                        _save_scfg(scfg)
                        label = "3 месяца" if key == "3m" else "12 месяцев"
                        await _send(cid,
                            f"✅ Себестоимость *{label}* сохранена: *{_usd_disp(val)}*",
                            parse_mode="Markdown",
                            reply_markup={"inline_keyboard": [
                                [{"text": "⚙️ Себестоимость", "callback_data": "sales:config"}],
                            ]})
                except (ValueError, TypeError):
                    await _send(cid, "❌ Некорректное значение. Введите число.",
                                reply_markup={"inline_keyboard": [[{"text": "❌ Отмена",
                                                                    "callback_data": "sales:config"}]]})
                return

            is_new = cid not in subs
            if is_new:
                subs.add(cid)
                _save_subs(subs, cfg)

            tl = text.lower()
            if tl == "/start" or is_new:
                intro = (
                    "👋 *Добро пожаловать в Flipkart Automation!*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Управляйте автоматизацией через меню:"
                    if is_new else _main_text()
                )
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": intro,
                                            "parse_mode": "Markdown",
                                            "reply_markup": _main_kb(cid)})
                except Exception:
                    pass
                return

            if tl in ("/status", "статус"):
                txt = (_mode_label(_mode[0]) + f" · PID {_proc[0].pid}"
                       if _running() else "🔴 Нет активного процесса")
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": txt})
                except Exception:
                    pass
            elif tl in ("/stop", "стоп"):
                await _do_stop(cid)
            elif tl in ("/proxy", "/прокси"):
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": _proxy_text(),
                                            "parse_mode": "Markdown",
                                            "reply_markup": _proxy_kb()})
                except Exception:
                    pass
            elif tl in ("/logs", "/лог", "/логи"):
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": _logs_text(),
                                            "parse_mode": "Markdown",
                                            "reply_markup": {"inline_keyboard": [[
                                                {"text": "🔄 Обновить", "callback_data": "show:logs"},
                                                {"text": "◀️ Меню",    "callback_data": "go:main"},
                                            ]]}})
                except Exception:
                    pass
            elif tl in ("/cards", "/карты"):
                try:
                    _txt, _kb = _cards_order_page(cid)
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": _txt,
                                            "parse_mode": "Markdown",
                                            "reply_markup": _kb})
                except Exception:
                    pass
            elif tl in ("/profiles", "/профили"):
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": _profiles_text(),
                                            "parse_mode": "Markdown",
                                            "reply_markup": _profiles_kb()})
                except Exception:
                    pass

        # ══════════════════════════════════════════════════════════════════════
        # Основной цикл
        # ══════════════════════════════════════════════════════════════════════

        # ── GGSell webhook-сервер (aiohttp) — запуск после init handler ────────
        _webhook_queue: asyncio.Queue = asyncio.Queue()
        _webhook_runner = [None]

        # ─────────────────────────────────────────────────────────────────────
        _timeout_obj = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=_timeout_obj, trust_env=False) as client:

            # ── Инициализация GGSell handler ──────────────────────────────────
            from ggsell.bot_ggsell import GGSellBotHandler as _GGSellBotHandler
            _ggsel_handler[0] = _GGSellBotHandler(
                orders=_ggsel_orders,
                confirm=_ggsel_confirm,
                done=_ggsel_done,
                done_loaded=_ggsel_done_loaded,
                reply_mode=_ggsel_reply_mode,
                pool_pick_pending=_pool_pick_pending,
                done_links=_ggsel_done_links,
                cli_holder=_ggsel_cli,
                subs=subs,
                edit_fn=_edit,
                send_fn=_send,
                ack_fn=_ack,
                get_fn=_get,
                set_fn=_set,
                m_fn=_m,
                http_client=client,
                tg_api_url=api,
                project_root=Path(__file__).parent,
                webhook_url=_webhook_url,
                record_sale_fn=_record_sale,
            )

            # ── GGSell webhook сервер ─────────────────────────────────────────
            if _aio_web:
                try:
                    _ggs_sec = (_m("_read_secrets")().get("ggsel") or {})
                    _wh_port = int(_ggs_sec.get("webhook_port") or 0)
                    if _wh_port:
                        _wh_handler = _ggsel_handler[0].make_webhook_handler(
                            _webhook_queue, _aio_web)
                        _wh_app = _aio_web.Application()
                        _wh_app.router.add_get("/ggsel/notify",  _wh_handler)
                        _wh_app.router.add_post("/ggsel/notify", _wh_handler)
                        _webhook_runner[0] = _aio_web.AppRunner(_wh_app)
                        await _webhook_runner[0].setup()
                        await _aio_web.TCPSite(_webhook_runner[0], "0.0.0.0", _wh_port).start()
                except Exception:
                    pass

            asyncio.ensure_future(_bg_update_loop())

            # Разовый скан «висящих» заказов (есть сообщение покупателя, профиль
            # не привязан, ссылка не выдана) — предложить начать выполнение.
            try:
                asyncio.ensure_future(_ggsel_handler[0].bg_check_hanging_orders())
            except Exception:
                pass

            # После рестарта: убираем "Перезапускаю..." и сразу открываем главное меню
            try:
                rf = Path(__file__).parent / "._restart_msg.json"
                if rf.exists():
                    rm = json.loads(rf.read_text(encoding="utf-8"))
                    rf.unlink()
                    _restart_cid = rm["chat_id"]
                    done = rm.get("text", "").replace(
                        "\n\n⚡ _Перезапускаю..._", "\n\n✅ _Перезапущен_")
                    await client.post(f"{api}/editMessageText",
                                      json={"chat_id": _restart_cid,
                                            "message_id": rm["msg_id"],
                                            "text": done,
                                            "parse_mode": "Markdown"})
                    # Отправляем главное меню в тот же чат
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": _restart_cid,
                                            "text": _main_text(),
                                            "parse_mode": "Markdown",
                                            "reply_markup": _main_kb(_restart_cid)})
            except Exception:
                pass

            while True:
                try:
                    resp = await client.get(
                        f"{api}/getUpdates",
                        params={"offset": offset,
                                "timeout": 0 if _first else 5,
                                "allowed_updates": ["message", "callback_query"]},
                    )
                    cons_err = 0

                    if resp.status_code == 401:
                        _tg_status = "error:неверный токен (401)"; return
                    if resp.status_code == 409:
                        _tg_status = f"ok:{len(subs)}"
                        await asyncio.sleep(5); continue
                    if resp.status_code != 200:
                        _tg_status = f"error:HTTP {resp.status_code}"
                        await asyncio.sleep(5); continue

                    data = resp.json()
                    if not data.get("ok"):
                        _tg_status = f"error:{data.get('description', '?')}"
                        await asyncio.sleep(5); continue

                    _tg_status = f"ok:{len(subs)}"

                    for upd in data.get("result", []):
                        offset = upd["update_id"] + 1
                        cbq    = upd.get("callback_query")
                        if cbq:
                            await _handle_cbq(client, cbq)
                            continue
                        msg = upd.get("message") or upd.get("edited_message")
                        if msg:
                            await _handle_msg(client, msg)

                    _first = False

                    # Drain GGSell monitor queue → уведомляем о новых заказах/сообщениях
                    try:
                        from ggsell.monitor import notify_queue as _gs_q
                        while True:
                            try:
                                _gs_item = _gs_q.get_nowait()
                                if _gs_item.get("type") == "new_order":
                                    asyncio.create_task(_ggsel_handler[0].notify_order(_gs_item))
                                elif _gs_item.get("type") == "new_message":
                                    asyncio.create_task(_ggsel_handler[0].notify_message(_gs_item))
                                elif _gs_item.get("type") == "new_review":
                                    asyncio.create_task(_ggsel_handler[0].notify_review(_gs_item))
                            except Exception:
                                break
                    except Exception:
                        pass

                    # Drain GGSell webhook queue → уведомляем о покупках через webhook
                    while not _webhook_queue.empty():
                        try:
                            _wh_item = _webhook_queue.get_nowait()
                            if _wh_item.get("type") == "new_order":
                                asyncio.create_task(_ggsel_handler[0].notify_order(_wh_item))
                        except Exception:
                            break

                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    cons_err += 1
                    _tg_status = f"error:{exc}"
                    await asyncio.sleep(min(30, 3 * cons_err))

    # Windows: принудительно ProactorEventLoop для subprocess в executor
    if sys.platform == "win32":
        _loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(_poll())
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            try:
                pending = asyncio.all_tasks(_loop)
                for t in pending:
                    t.cancel()
                if pending:
                    _loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            _loop.close()
    else:
        try:
            asyncio.run(_poll())
        except (KeyboardInterrupt, SystemExit):
            pass
