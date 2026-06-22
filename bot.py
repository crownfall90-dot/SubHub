"""
bot.py — Telegram bot background thread.
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
_notified_update_hashes: set = set()


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
        import yaml as _y
    except ImportError:
        _tg_status = "error:pip install pyyaml"
        return
    try:
        token = (_m("_read_secrets")().get("telegram") or {}).get("token", "").strip()
    except Exception:
        token = ""
    if not token:
        # fallback: config.yaml (для совместимости при первом запуске до _init_secrets)
        try:
            cfg_path = _HERE / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _fh:
                    _cfg_raw = _y.safe_load(_fh)
                token = ((_cfg_raw or {}).get("telegram") or {}).get("token", "").strip()
        except Exception:
            pass
    if not token:
        return
    try:
        import httpx as _hx
    except ImportError:
        _tg_status = "error:pip install httpx"
        return

    # Проверяем наличие GGSell-ключей
    try:
        _gs = (_m("_read_secrets")().get("ggsel") or {})
        if _gs.get("api_key", "").strip() and str(_gs.get("seller_id") or "").strip():
            _ggsel_status = "ok"
    except Exception:
        pass

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
        _ggsel_cli      = [None]  # GGSell client (ленивая инициализация)
        _ggsel_orders: dict = {}  # {invoice_id: item из notify_queue}
        _ggsel_confirm: dict = {} # {invoice_id: link} — ждёт подтверждения от пользователя
        _ggsel_done: dict        = {} # {invoice_id: datetime_str} — выполнено (ссылка отправлена)
        _ggsel_done_loaded       = [False]
        _ggsel_reply_mode: dict  = {} # {cid: invoice_id} — ждём текст ответа от пользователя
        _pool_pick_pending: dict = {} # {cid: link}      — ссылка из пула ждёт выбора покупателя

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
            noaddr = []
            hasaddr = []
            active = []
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
                    is_active = (st in ("activated", "explore_now", "activate_now")) or bool(vt)
                    if is_active:
                        active.append((ph, p, m))
                    elif st == "email_completed":
                        hasaddr.append((ph, p, m))
                    else:
                        noaddr.append((ph, p, m))
            noaddr.sort(key=lambda x: x[2].get("login_ts") or 0, reverse=True)
            hasaddr.sort(key=lambda x: x[2].get("login_ts") or 0, reverse=True)
            active.sort(key=lambda x: x[2].get("login_ts") or 0, reverse=True)
            return noaddr, hasaddr, active

        def _profiles_text():
            noaddr, hasaddr, active = _get_profile_categories()
            _, archiv = _cnt_profiles()
            return (
                "📁 *Профили*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Неактивные (без адреса): *{len(noaddr)}*\n"
                f"Неактивные (с адресом): *{len(hasaddr)}*\n"
                f"Активные: *{len(active)}*\n"
                f"В архиве: *{archiv}*\n\n"
                "Выберите действие:"
            )

        def _profiles_kb():
            noaddr, hasaddr, active = _get_profile_categories()
            _, archiv = _cnt_profiles()
            return {"inline_keyboard": [
                [{"text": f"❌ Без адреса ({len(noaddr)})", "callback_data": "profiles:list:noaddr"},
                 {"text": f"📍 С адресом ({len(hasaddr)})", "callback_data": "profiles:list:hasaddr"}],
                [{"text": f"🌟 Активные ({len(active)})", "callback_data": "profiles:list:active"},
                 {"text": f"📦 Архив ({archiv})", "callback_data": "profiles:list:archive"}],
                [{"text": "✅ Проверить всех неактивных", "callback_data": "profiles:checkall"}],
                [{"text": "📍 Адреса (без адреса)", "callback_data": "profiles:addrall"}],
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
                noaddr, hasaddr, active = _get_profile_categories()
                if list_type == "noaddr":
                    title = f"❌ *Неактивные профили (без адреса)* ({len(noaddr)} шт.)"
                    pairs = noaddr
                elif list_type == "hasaddr":
                    title = f"📍 *Неактивные профили (с адресом)* ({len(hasaddr)} шт.)"
                    pairs = hasaddr
                elif list_type == "active":
                    title = f"🌟 *Активные профили* ({len(active)} шт.)"
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
                    icon = "🌟" if (st in ("activated", "explore_now", "activate_now") or vt) else ("📍" if st == "email_completed" else "❌")
                    line = f"{icon} `{ph}`"
                    if vt:
                        line += f"  до {vt}"
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

                noaddr, hasaddr, active = _get_profile_categories()
                if list_type == "noaddr":
                    pairs = noaddr
                elif list_type == "hasaddr":
                    pairs = hasaddr
                elif list_type == "active":
                    pairs = active
                else:
                    pairs = []

                rows = []
                for ph, p, m in pairs[:20]:
                    vt = m.get("black_valid_till") or ""
                    st = m.get("status") or ""
                    is_iss = bool(m.get("issued_ts"))
                    has_login = bool(m.get("login_ts"))
                    if list_type in ("noaddr", "hasaddr"):
                        # Показываем только профили с успешным входом (номер + OTP + вход)
                        if not has_login:
                            continue
                        icon = "🔵" if is_iss else "🟢"
                    else:
                        # active: старая логика
                        icon = "🔵" if is_iss else ("🌟" if (st in ("activated", "explore_now", "activate_now") or vt) else "🟢")
                    label = f"{icon} {ph}"
                    if is_iss and vt:
                        label += f" · до {vt}"
                    elif vt:
                        label += f" · {vt}"
                    rows.append([{"text": label, "callback_data": f"profile:menu:{ph}:{list_type}"}])
                rows.append([{"text": "◀️ Назад", "callback_data": "go:profiles"}])
            except Exception:
                rows = [[{"text": "◀️ Назад", "callback_data": "go:profiles"}]]
            return {"inline_keyboard": rows}

        def _profile_menu_kb(phone, list_type="noaddr", rec_key=""):
            if list_type == "archive":
                return {"inline_keyboard": [
                    [{"text": f"📱 {phone}", "callback_data": "noop"}],
                    [{"text": "📞 Показать номер", "callback_data": f"profile:shownum:{phone}"}],
                    [{"text": "🍪 Экспорт куки JSON", "callback_data": f"profile:cookies_archived:{phone}:{rec_key}"}],
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
            is_active = (st in ("activated", "explore_now", "activate_now")) or bool(vt)
            is_issued = bool(m.get("issued_ts"))

            if is_active:
                _issued_btn = (
                    {"text": "🟢 Выдан", "callback_data": "noop"}
                    if is_issued else
                    {"text": "🔵 Поставить статус выдан", "callback_data": f"profile:set_issued:{phone}"}
                )
                has_link = bool(m.get("black_activation_link") or m.get("black_short_link"))
                rows = [
                    [{"text": f"📱 {phone}", "callback_data": "noop"}],
                    [{"text": "✅ Проверить активацию Black", "callback_data": f"profile:activate:{phone}"}],
                    [_issued_btn],
                ]
                if has_link and not is_issued:
                    rows.append([{"text": "📦 В пул ссылок", "callback_data": f"profile:topool:{phone}"}])
                rows += [
                    [{"text": "📦 Перенести в архив", "callback_data": f"profile:archive_one:{phone}"}],
                    [{"text": "🍪 Экспорт куки JSON", "callback_data": f"profile:cookies:{phone}"}],
                    [{"text": "◀️ Назад", "callback_data": "profiles:list:active"}],
                ]
                return {"inline_keyboard": rows}
            else:
                back_callback = f"profiles:list:{list_type}" if list_type in ("noaddr", "hasaddr") else "profiles:list:noaddr"
                return {"inline_keyboard": [
                    [{"text": f"📱 {phone}", "callback_data": "noop"}],
                    [{"text": "🥈 Купить 3 мес · ₹399", "callback_data": f"profile:buy:3:{phone}"},
                     {"text": "🥇 12 мес · ₹1499", "callback_data": f"profile:buy:12:{phone}"}],
                    [{"text": "📍 Заполнить адрес доставки", "callback_data": f"profile:address:{phone}"}],
                    [{"text": "✅ Проверить активацию Black", "callback_data": f"profile:activate:{phone}"}],
                    [{"text": "🟢 Перенести в актив", "callback_data": f"profile:set_active:{phone}"}],
                    [{"text": "🍪 Экспорт куки JSON", "callback_data": f"profile:cookies:{phone}"}],
                    [{"text": "◀️ Назад", "callback_data": back_callback}],
                ]}

        def _archive_text():
            if not USED_PROFILES_DIR or not USED_PROFILES_DIR.exists():
                return "📦 *Архив*\n\n_Архив пуст_"
            records = sorted(USED_PROFILES_DIR.glob("record_*.json"), reverse=True)
            if not records:
                return "📦 *Архив*\n\n_Архив пуст_"
            lines = [f"📦 *Архив* ({len(records)} шт.)", "━━━━━━━━━━━━━━━━━━━━━━", ""]
            for rec in records[:20]:
                try:
                    d  = json.loads(rec.read_text(encoding="utf-8"))
                    ph = d.get("username") or rec.stem.replace("record_", "")
                    vt = d.get("black_valid_till") or ""
                    ts = d.get("archived_str") or d.get("login_str") or ""
                    if not ts and d.get("login_ts"):
                        import datetime as _dt
                        try:
                            ts = _dt.datetime.fromtimestamp(float(d["login_ts"])).strftime("%d.%m.%Y")
                        except Exception:
                            ts = ""
                    suffix = (f"  ·  до {vt}" if vt else (f"  ·  {ts}" if ts else ""))
                    lines.append(f"{'🌟' if vt else '✅'} `{ph}`" + suffix)
                except Exception:
                    lines.append(f"  • {rec.name}")
            if len(records) > 20:
                lines.append(f"\n_...и ещё {len(records) - 20}_")
            return "\n".join(lines)

        def _archive_kb():
            try:
                if not USED_PROFILES_DIR or not USED_PROFILES_DIR.exists():
                    return {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:profiles"}]]}
                records = sorted(USED_PROFILES_DIR.glob("record_*.json"), reverse=True)
                rows = []
                for rec in records[:20]:
                    rec_key = rec.stem.replace("record_", "")
                    try:
                        d  = json.loads(rec.read_text(encoding="utf-8"))
                        ph = d.get("username") or rec.stem.replace("record_", "")
                        vt = d.get("black_valid_till") or ""
                        icon = "🌟" if vt else "✅"
                        label = f"{icon} {ph}"
                        if vt:
                            label += f" · {vt}"
                        rows.append([{"text": label, "callback_data": f"profile:menu:{ph}:archive:{rec_key}"}])
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
                [{"text": "💳 Карты",       "callback_data": "show:cards"},
                 {"text": "🌐 Прокси",      "callback_data": "show:proxy"}],
                [{"text": "📋 Логи",        "callback_data": "show:logs"},
                 {"text": "📊 Статистика",  "callback_data": "show:stats"}],
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

        # ── GGSell ────────────────────────────────────────────────────────────

        def _get_ggsel_client():
            if _ggsel_cli[0] is not None:
                return _ggsel_cli[0]
            try:
                from ggsell.client import GGSellClient
                sec = _m("_read_secrets")().get("ggsel") or {}
                key = sec.get("api_key", "").strip()
                sid = int(sec.get("seller_id") or 0)
                if key and sid:
                    _ggsel_cli[0] = GGSellClient(api_key=key, seller_id=sid)
            except Exception:
                pass
            return _ggsel_cli[0]

        def _ggsel_read_pool() -> list:
            try:
                f = Path(__file__).parent / "data" / "ggsel_links.json"
                return json.loads(f.read_text(encoding="utf-8")).get("links", [])
            except Exception:
                return []

        def _ggsel_remove_link(link: str) -> None:
            try:
                f = Path(__file__).parent / "data" / "ggsel_links.json"
                raw = json.loads(f.read_text(encoding="utf-8"))
                raw["links"] = [l for l in raw.get("links", []) if l != link]
                f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        def _ggsel_get_done() -> dict:
            """Словарь {invoice_id: datetime_str} выполненных заказов (с персистентной загрузкой)."""
            if not _ggsel_done_loaded[0]:
                try:
                    f = Path(__file__).parent / "data" / "ggsel_done.json"
                    loaded = json.loads(f.read_text(encoding="utf-8")).get("done", {})
                    _ggsel_done.update({int(k): v for k, v in loaded.items()})
                except Exception:
                    pass
                _ggsel_done_loaded[0] = True
            return _ggsel_done

        def _ggsel_mark_done(invoice_id: int) -> None:
            """Пометить заказ как выполненный и сохранить на диск."""
            from datetime import datetime
            dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            _ggsel_get_done()[invoice_id] = dt_str
            try:
                f = Path(__file__).parent / "data" / "ggsel_done.json"
                try:
                    raw = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    raw = {"done": {}}
                raw.setdefault("done", {})[str(invoice_id)] = dt_str
                f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        def _ggsel_order_status(invoice_id: int) -> str:
            done = _ggsel_get_done()
            if invoice_id in done:
                return f"✅ Выполнено · {done[invoice_id]}"
            if invoice_id in _ggsel_confirm:
                return "⏳ Ждёт подтверждения"
            return "🆕 Новый"

        def _ggsel_pool_text() -> str:
            links = _ggsel_read_pool()
            if not links:
                return "📦 *Пул ссылок GGSell*\n\n_Пул пуст_"
            lines = [f"📦 *Пул ссылок GGSell* ({len(links)} шт.)", "━━━━━━━━━━━━━━━━━━━━━━", ""]
            for lnk in links[:10]:
                short = lnk[:60] + "…" if len(lnk) > 60 else lnk
                lines.append(f"▸ `{short}`")
            if len(links) > 10:
                lines.append(f"\n_...и ещё {len(links) - 10}_")
            return "\n".join(lines)

        def _ggsel_parse_order(order: dict) -> dict:
            """Извлечь поля заказа с множеством fallback-имён."""
            product  = order.get("product") or {}
            name     = (product.get("name") or product.get("product_name")
                        or order.get("product_name") or order.get("name") or "YouTube Premium")
            buyer    = order.get("buyer") or order.get("buyer_info") or {}
            email    = (buyer.get("email") or order.get("email")
                        or order.get("buyer_email") or "")
            sum_buy  = (order.get("sum") or order.get("amount") or order.get("price")
                        or order.get("price_rub") or product.get("price_rub") or "")
            sum_sell = (order.get("sum_seller") or order.get("seller_sum")
                        or order.get("profit") or order.get("payout") or "")
            status   = order.get("status") or order.get("state") or ""
            date     = str(order.get("date") or "").replace("T", " ")[:16]
            # Обрезаем длинное название — оставляем первые два сегмента через |
            name_short = name
            parts = [p.strip() for p in str(name).split("|")]
            if len(parts) >= 2:
                name_short = f"{parts[0]} | {parts[1]}"
            if len(name_short) > 60:
                name_short = name_short[:57] + "…"
            return {
                "name": str(name),
                "name_short": name_short,
                "email": str(email),
                "sum_buy": sum_buy,
                "sum_sell": sum_sell,
                "status": str(status),
                "date": date,
            }

        def _ggsel_order_text(invoice_id: int) -> str:
            item        = _ggsel_orders.get(invoice_id, {})
            order       = item.get("order", {})
            p           = _ggsel_parse_order(order)
            email       = item.get("buyer_email") or p["email"] or "?"
            confirm_lnk = _ggsel_confirm.get(invoice_id)
            lines = [
                f"📋 *Заказ GGSell #{invoice_id}*",
                "━━━━━━━━━━━━━━━━━━━━━━", "",
                f"📦 {p['name']}",
            ]
            if email and email != "?":
                lines.append(f"👤 `{email}`")
            if p["sum_buy"]:
                buy_line = f"💰 Сумма: *{p['sum_buy']}₽*"
                if p["sum_sell"]:
                    buy_line += f"  ·  💼 Выплата: *{p['sum_sell']}₽*"
                lines.append(buy_line)
            if p["status"]:
                lines.append(f"📍 Статус: `{p['status']}`")
            if p["date"]:
                lines.append(f"🕒 {p['date']}")
            lines += ["", f"📊 {_ggsel_order_status(invoice_id)}"]
            if confirm_lnk:
                lines += ["", f"🔗 *Ссылка готова:*\n`{confirm_lnk}`"]
            return "\n".join(lines)

        def _ggsel_order_kb(invoice_id: int) -> dict:
            if invoice_id in _ggsel_get_done():
                return {"inline_keyboard": [
                    [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
                ]}
            confirm_lnk = _ggsel_confirm.get(invoice_id)
            if confirm_lnk:
                return {"inline_keyboard": [
                    [{"text": "📤 Отправить покупателю",
                      "callback_data": f"ggsell:send:{invoice_id}"},
                     {"text": "❌ Не отправлять",
                      "callback_data": f"ggsell:nosend:{invoice_id}"}],
                    [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
                ]}
            return {"inline_keyboard": [
                [{"text": "▶️ Выполнить заказ",
                  "callback_data": f"ggsell:run:{invoice_id}"}],
                [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
            ]}

        # ── GGSell: вспомогательная функция баланса ──────────────────────────
        async def _ggsel_fetch_balance(cli):
            """Возвращает (bal_s, lock_s, plus_s, payment_date_s)."""
            bal_s = lock_s = plus_s = payment_date_s = ""
            try:
                bi = await cli.get_balance_info()
                bal_s  = f"${bi['free']:.2f}"
                lock_s = f"${bi['lock']:.2f}" if bi["lock"] else ""
                plus_s = f"${bi['plus']:.2f}" if bi["plus"] else ""
            except Exception as exc:
                bal_s = f"❌ {exc}"
            try:
                sched = await cli.get_payment_schedule()
                if isinstance(sched, dict) and sched:
                    c = sched.get("content") or sched
                    items = c if isinstance(c, list) else (
                        c.get("items") or c.get("data") or c.get("transactions") or []
                    )
                    if isinstance(items, list) and items:
                        f0 = items[0]
                        amt = (f0.get("amount") or f0.get("sum") or f0.get("total") or "")
                        dt  = (f0.get("date") or f0.get("payment_date") or f0.get("release_date") or "")
                        if amt and not plus_s:
                            try:
                                plus_s = f"${float(amt):.2f}"
                            except Exception:
                                plus_s = str(amt)
                        if dt:
                            payment_date_s = str(dt)[:16].replace("T", " ")
                    elif isinstance(c, dict):
                        amt = c.get("pending") or c.get("pending_amount") or ""
                        dt  = c.get("next_payment") or c.get("next_payment_date") or ""
                        if amt and not plus_s:
                            try:
                                plus_s = f"${float(amt):.2f}"
                            except Exception:
                                plus_s = str(amt)
                        if dt:
                            payment_date_s = str(dt)[:16].replace("T", " ")
            except Exception:
                pass
            return bal_s, lock_s, plus_s, payment_date_s

        # ── GGSell: главная панель ────────────────────────────────────────────
        async def _bg_ggsel_info(cid, mid):
            cli = _get_ggsel_client()
            if cli is None:
                await _edit(cid, mid,
                    "💰 *GGSell*\n\n❌ _Не настроен. Заполните_ `ggsel` _в_ `secrets.yaml`_._",
                    {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:other"}]]})
                return

            pool = len(_ggsel_read_pool())
            try:
                f = Path(__file__).parent / "data" / "ggsel_orders.json"
                processed_cnt = len(json.loads(f.read_text(encoding="utf-8")).get("processed", []))
            except Exception:
                processed_cnt = 0

            bal_s, lock_s, plus_s, payment_date_s = await _ggsel_fetch_balance(cli)

            # Быстрая статистика
            total_sales = total_revenue = ""
            try:
                stat = await cli.get_stats()
                if isinstance(stat, dict):
                    c = stat.get("content") or stat
                    total_sales   = c.get("total_sales") or c.get("cnt_sales") or c.get("cnt") or ""
                    total_revenue = c.get("total_revenue") or c.get("revenue") or c.get("sum") or ""
            except Exception:
                pass

            pending_cnt = len(_ggsel_confirm)

            lines = ["💰 *GGSell — Панель продавца*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
            lines.append(f"💵 Баланс: *{bal_s}*" + (f"  ·  🔒 {lock_s}" if lock_s else ""))
            if plus_s:
                dp = f" (поступит {payment_date_s})" if payment_date_s else ""
                lines.append(f"⏳ К поступлению: *{plus_s}*{dp}")
            lines.append("")
            if total_sales:
                lines.append(f"🛒 Продаж: *{total_sales}*" + (f"  ·  💰 *${float(total_revenue):.2f}*" if total_revenue else ""))
            lines.append(f"📦 Ссылок в пуле: *{pool}*  ·  ✅ Обработано: *{processed_cnt}*")
            if pending_cnt:
                lines.append(f"⏳ Ждут подтверждения: *{pending_cnt}*")

            kb_rows = [
                [{"text": "📋 Заказы",      "callback_data": "ggsell:orders"},
                 {"text": "💬 Чаты",        "callback_data": "ggsell:chats"}],
                [{"text": "📦 Пул ссылок",  "callback_data": "ggsell:pool"},
                 {"text": "⚙️ Настройки",   "callback_data": "ggsell:settings"}],
                [{"text": "🔄 Обновить",    "callback_data": "ggsell:refresh"},
                 {"text": "◀️ Назад",       "callback_data": "go:other"}],
            ]
            await _edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

        # ── GGSell: страница заказов ──────────────────────────────────────────
        async def _bg_ggsel_orders_page(cid, mid):
            cli = _get_ggsel_client()
            if cli is None:
                await _edit(cid, mid, "❌ GGSell не настроен.",
                    {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
                return

            try:
                orders = await cli.get_last_orders()
                yt_orders = [o for o in orders
                             if int((o.get("product") or {}).get("id") or 0) == 102276416]
            except Exception:
                yt_orders = []

            done = _ggsel_get_done()
            lines = ["📋 *GGSell — Заказы*", "━━━━━━━━━━━━━━━━━━━━━━", ""]

            action_rows = []  # кнопки для заказов, требующих действия

            if yt_orders:
                for o in yt_orders[:10]:
                    inv   = o.get("invoice_id") or o.get("id") or "?"
                    inv_i = int(inv) if str(inv).isdigit() else 0
                    p     = _ggsel_parse_order(o)

                    if inv_i in done:
                        tag = "✅"
                    elif inv_i in _ggsel_confirm:
                        tag = "⏳"
                    else:
                        tag = "🆕"

                    sum_s = f" · {p['sum_buy']}₽" if p["sum_buy"] else ""
                    sell_s = f" → {p['sum_sell']}₽" if p["sum_sell"] else ""
                    lines.append(
                        f"*{tag} #{inv}* · {p['date']}\n"
                        f"    {p['name_short']}{sum_s}{sell_s}"
                    )
                    if p["email"]:
                        lines.append(f"    👤 {p['email']}")

                    # Кнопка только для не завершённых
                    if inv_i not in done:
                        if inv_i in _ggsel_confirm:
                            action_rows.append([{"text": f"⏳ #{inv} — отправить ссылку",
                                                  "callback_data": f"ggsell:order:{inv_i}"}])
                        else:
                            action_rows.append([{"text": f"▶️ #{inv} — выполнить",
                                                  "callback_data": f"ggsell:order:{inv_i}"}])
            else:
                lines.append("_Нет последних заказов YouTube Premium_")

            kb_rows = action_rows[:5] + [
                [{"text": "🔄 Обновить",  "callback_data": "ggsell:orders"},
                 {"text": "◀️ Назад",     "callback_data": "go:ggsell"}],
            ]
            await _edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

        # ── GGSell: страница чатов ────────────────────────────────────────────
        async def _bg_ggsel_chats_page(cid, mid):
            cli = _get_ggsel_client()
            if cli is None:
                await _edit(cid, mid, "❌ GGSell не настроен.",
                    {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
                return

            try:
                chats = await cli.get_chats()
            except Exception as exc:
                await _edit(cid, mid, f"❌ Ошибка загрузки чатов: {exc}",
                    {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
                return

            lines = ["💬 *GGSell — Чаты с покупателями*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
            chat_rows = []
            if chats:
                for ch in chats[:15]:
                    inv_id  = ch.get("id_i") or ch.get("invoice_id") or ch.get("id") or "?"
                    email   = ch.get("email") or ch.get("buyer_email") or "?"
                    cnt_new = int(ch.get("cnt_new") or 0)
                    new_tag = f" · 🔴 {cnt_new} новых" if cnt_new else ""
                    email_s = str(email)[:30]
                    lines.append(f"▸ `#{inv_id}` {email_s}{new_tag}")
                    btn_label = f"{'🔴 ' + str(cnt_new) + ' · ' if cnt_new else ''}#{inv_id} {email_s[:20]}"
                    chat_rows.append([{"text": btn_label,
                                       "callback_data": f"ggsell:order:{inv_id}"}])
            else:
                lines.append("_Нет активных чатов_")

            kb_rows = chat_rows[:8] + [
                [{"text": "🔄 Обновить", "callback_data": "ggsell:chats"},
                 {"text": "◀️ Назад",    "callback_data": "go:ggsell"}],
            ]
            await _edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

        # ── GGSell: страница настроек (синхронная) ────────────────────────────
        def _ggsel_settings_page(cid, mid_unused):
            ord_on = _get(cid, "ggsel_notify_orders")
            msg_on = _get(cid, "ggsel_notify_messages")
            lines = [
                "⚙️ *GGSell — Настройки*",
                "━━━━━━━━━━━━━━━━━━━━━━", "",
                "*Уведомления:*",
                f"  {'🔔' if ord_on else '🔕'} Заказы: {'включены' if ord_on else 'выключены'}",
                f"  {'🔔' if msg_on else '🔕'} Сообщения: {'включены' if msg_on else 'выключены'}",
            ]
            kb = {"inline_keyboard": [
                [{"text": ("🔔 Заказы: Вкл"      if ord_on else "🔕 Заказы: Выкл"),
                  "callback_data": "ggsell:toggle:orders"},
                 {"text": ("🔔 Сообщения: Вкл"   if msg_on else "🔕 Сообщения: Выкл"),
                  "callback_data": "ggsell:toggle:messages"}],
                [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
            ]}
            return "\n".join(lines), kb

        async def _bg_ggsel_pool_pick(cid, mid, link: str) -> None:
            """Показать список заказов для выбора получателя ссылки из пула."""
            cli = _get_ggsel_client()
            try:
                orders = await cli.get_last_orders() if cli else []
                yt_orders = [o for o in orders
                             if int((o.get("product") or {}).get("id") or 0) == 102276416]
            except Exception:
                yt_orders = []

            done = _ggsel_get_done()
            link_preview = link[:60] + "…" if len(link) > 60 else link
            lines = [
                "📦 *Отправить ссылку из пула*",
                "━━━━━━━━━━━━━━━━━━━━━━", "",
                f"🔗 `{link_preview}`", "",
                "Выберите покупателя:",
            ]
            order_rows = []
            for o in yt_orders[:8]:
                inv_i = int(o.get("invoice_id") or o.get("id") or 0)
                if inv_i in done:
                    continue
                p = _ggsel_parse_order(o)
                label = f"#{inv_i}"
                if p["email"]:
                    label += f" · {p['email'][:28]}"
                order_rows.append([{"text": label,
                                     "callback_data": f"ggsell:pool_order:{inv_i}"}])
            if not order_rows:
                lines.append("\n_Нет незавершённых заказов для отправки_")
            kb_rows = order_rows[:7] + [
                [{"text": "❌ Отмена", "callback_data": "ggsell:pool"}],
            ]
            await _edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

        async def _bg_ggsel_pool_send(cid, mid, invoice_id: int, link: str) -> None:
            """Отправить ссылку из пула покупателю и снять её из пула."""
            cli = _get_ggsel_client()
            if not cli:
                await _edit(cid, mid, "❌ GGSell не настроен.",
                    {"inline_keyboard": [[{"text": "◀️ GGSell", "callback_data": "go:ggsell"}]]})
                return
            from ggsell.monitor import MSG_TEMPLATE
            ok = await cli.send_message(invoice_id, MSG_TEMPLATE.format(link=link))
            _ggsel_remove_link(link)
            if ok:
                _ggsel_mark_done(invoice_id)
                await _edit(cid, mid,
                    f"✅ *Ссылка отправлена покупателю!*\n\n"
                    f"Заказ: `#{invoice_id}`\n🔗 `{link}`",
                    {"inline_keyboard": [
                        [{"text": "📦 Пул ссылок", "callback_data": "ggsell:pool"},
                         {"text": "◀️ GGSell",     "callback_data": "go:ggsell"}],
                    ]})
            else:
                await _edit(cid, mid,
                    f"❌ Не удалось отправить ссылку заказу `#{invoice_id}`.\n\nСсылка возвращена в пул.",
                    {"inline_keyboard": [[{"text": "📦 Пул", "callback_data": "ggsell:pool"}]]})
                # Возвращаем ссылку в пул
                from ggsell.monitor import add_link_to_pool
                add_link_to_pool(link)

        async def _ggsel_notify_order(item: dict) -> None:
            """Отправить уведомление о новом заказе всем подписчикам."""
            invoice_id  = item.get("invoice_id")
            order       = item.get("order", {})
            _ggsel_orders[invoice_id] = item

            p = _ggsel_parse_order(order)
            email = item.get("buyer_email") or p["email"]

            lines = [f"💸 *Новый заказ* `#{invoice_id}`", "━━━━━━━━━━━━━━━━━━━━━━", ""]
            lines.append(f"📦 {p['name']}")
            if email:
                lines.append(f"👤 `{email}`")
            if p["sum_buy"]:
                lines.append(f"💰 Сумма покупки: *{p['sum_buy']}₽*")
            if p["sum_sell"]:
                lines.append(f"💼 Твоя выплата: *{p['sum_sell']}₽*")
            if p["status"]:
                lines.append(f"📍 Статус: `{p['status']}`")
            if p["date"]:
                lines.append(f"🕒 {p['date']}")
            lines += ["", "_Нажмите «Выполнить» чтобы запустить автоматизацию._"]
            text = "\n".join(lines)
            kb = {"inline_keyboard": [
                [{"text": f"📋 Детали #{invoice_id}",
                  "callback_data": f"ggsell:order:{invoice_id}"}],
                [{"text": "▶️ Выполнить заказ",
                  "callback_data": f"ggsell:run:{invoice_id}"}],
            ]}
            for _cid in list(subs):
                if not _get(_cid, "ggsel_notify_orders"):
                    continue
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": _cid, "text": text,
                                            "parse_mode": "Markdown", "reply_markup": kb})
                except Exception:
                    pass

        async def _bg_ggsel_run(cid, mid, invoice_id: int) -> None:
            """Запустить автоматизацию для заказа и дождаться ссылки."""
            item        = _ggsel_orders.get(invoice_id, {})
            buyer_email = item.get("buyer_email") or "?"

            # Пробуем получить email покупателя если ещё не знаем
            if buyer_email == "?":
                try:
                    cli = _get_ggsel_client()
                    if cli:
                        fetched = await cli.get_buyer_email(invoice_id)
                        if fetched:
                            buyer_email = fetched
                            _ggsel_orders.setdefault(invoice_id, {})["buyer_email"] = fetched
                except Exception:
                    pass

            await _edit(cid, mid,
                f"⏳ *Выполняю заказ* `#{invoice_id}`\n\n"
                f"📧 Покупатель: `{buyer_email}`\n\n"
                "_Запускаю автоматизацию — создаю профиль..._\n"
                "_Это займёт несколько минут._",
                {"inline_keyboard": []})

            # Снимок пула ДО запуска
            before_links = set(_ggsel_read_pool())

            args = [
                sys.executable,
                str(Path(__file__).parent / "main.py"),
                "--tg-login", "--accounts", "1",
            ]
            try:
                import os
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                loop = asyncio.get_running_loop()
                proc = await loop.run_in_executor(
                    None, lambda: subprocess.Popen(args, creationflags=creationflags))
                await loop.run_in_executor(None, proc.wait)
                code = proc.returncode
            except Exception as exc:
                await _send(cid, f"❌ Ошибка запуска автоматизации (заказ `#{invoice_id}`): {exc}")
                return

            if code != 0:
                await _send(cid,
                    f"⚠️ Автоматизация завершилась с кодом {code} (заказ `#{invoice_id}`).\n"
                    "Проверьте /logs")
                return

            # Ищем новые ссылки в пуле
            after_links = _ggsel_read_pool()
            new_links   = [l for l in after_links if l not in before_links]

            if new_links:
                link = new_links[0]
                _ggsel_remove_link(link)
                _ggsel_confirm[invoice_id] = link
                await _send(cid,
                    f"✅ *Ссылка для заказа* `#{invoice_id}` *готова!*\n\n"
                    f"🔗 `{link}`\n\n"
                    f"📧 Покупатель: `{buyer_email}`\n\n"
                    "Отправить ссылку покупателю в чат GGSell?",
                    reply_markup={"inline_keyboard": [
                        [{"text": "📤 Отправить покупателю",
                          "callback_data": f"ggsell:send:{invoice_id}"}],
                        [{"text": "📦 В пул ссылок",
                          "callback_data": f"ggsell:topool:{invoice_id}"},
                         {"text": "❌ Не отправлять",
                          "callback_data": f"ggsell:nosend:{invoice_id}"}],
                    ]})
            else:
                await _send(cid,
                    f"⚠️ Заказ `#{invoice_id}`: автоматизация завершена, но новая ссылка не найдена.\n\n"
                    "_Добавьте ссылку вручную или повторите запуск._")

        async def _ggsel_notify_message(item: dict) -> None:
            """Уведомить подписчиков о новом сообщении от покупателя."""
            invoice_id  = item.get("invoice_id")
            msg         = item.get("message", {})
            chat        = item.get("chat", {})
            email       = chat.get("email") or "?"
            msg_text    = (msg.get("text") or msg.get("message") or msg.get("body") or "…")
            if len(msg_text) > 300:
                msg_text = msg_text[:300] + "…"

            raw_date = (msg.get("date") or msg.get("created_at") or msg.get("timestamp")
                        or msg.get("date_add") or "")
            msg_time = str(raw_date)[:16].replace("T", " ") if raw_date else ""

            text = (
                f"💬 *Новое сообщение · заказ* `#{invoice_id}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📧 {email}" + (f" · 📅 {msg_time}" if msg_time else "") + "\n\n"
                f"{msg_text}"
            )
            kb = {"inline_keyboard": [
                [{"text": "💬 Ответить",
                  "callback_data": f"ggsell:reply:{invoice_id}"},
                 {"text": f"📋 Заказ #{invoice_id}",
                  "callback_data": f"ggsell:order:{invoice_id}"}],
            ]}
            for _cid in list(subs):
                if not _get(_cid, "ggsel_notify_messages"):
                    continue
                try:
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": _cid, "text": text,
                                            "parse_mode": "Markdown", "reply_markup": kb})
                except Exception:
                    pass

        async def _bg_ggsel_reply(cid, invoice_id: int, text: str) -> None:
            """Отправить ответ продавца в чат GGSell."""
            cli = _get_ggsel_client()
            if not cli:
                await _send(cid, "❌ GGSell клиент не настроен.")
                return
            try:
                ok = await cli.send_message(invoice_id, text)
            except Exception as exc:
                await _send(cid, f"❌ Ошибка отправки (заказ `#{invoice_id}`): {exc}")
                return
            if ok:
                await _send(cid,
                    f"✅ Сообщение отправлено покупателю!\n\n"
                    f"Заказ: `#{invoice_id}`\n"
                    f"_{text}_")
            else:
                await _send(cid, f"⚠️ Не удалось отправить сообщение (заказ `#{invoice_id}`).")

        async def _bg_ggsel_send(cid, invoice_id: int) -> None:
            """Отправить ссылку покупателю через GGSell API."""
            link = _ggsel_confirm.pop(invoice_id, None)
            if not link:
                await _send(cid, f"❌ Ссылка для заказа `#{invoice_id}` не найдена.")
                return
            cli = _get_ggsel_client()
            if not cli:
                await _send(cid, "❌ GGSell клиент не настроен.")
                return
            item        = _ggsel_orders.get(invoice_id, {})
            buyer_email = item.get("buyer_email") or "?"
            try:
                from ggsell.monitor import MSG_TEMPLATE
                ok = await cli.send_message(invoice_id, MSG_TEMPLATE.format(link=link))
            except Exception as exc:
                await _send(cid, f"❌ Ошибка отправки в GGSell (заказ `#{invoice_id}`): {exc}")
                return
            if ok:
                _ggsel_mark_done(invoice_id)
                await _send(cid,
                    f"✅ *Ссылка отправлена покупателю!*\n\n"
                    f"Заказ: `#{invoice_id}` · ✅ Выполнено\n"
                    f"📧 {buyer_email}\n"
                    f"🔗 `{link}`")
            else:
                await _send(cid,
                    f"⚠️ Не удалось отправить сообщение в GGSell (заказ `#{invoice_id}`).")

        # ── Карты ─────────────────────────────────────────────────────────────
        def _cards_text():
            try:
                if not CARDS_FILE.exists():
                    return "💳 *Карты*\n\n_Файл cards.json не найден_"
                cards = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
                if not cards:
                    return "💳 *Карты*\n\n_Список пуст_"
                lines = ["💳 *Платёжные карты*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
                for i, c in enumerate(cards, 1):
                    num  = str(c.get("number", "")).replace(" ", "").replace("-", "")
                    mask = f"**** {num[-4:]}" if len(num) >= 4 else "????"
                    exp  = c.get("expiry") or c.get("exp") or "—"
                    name = c.get("name", "")
                    lines.append(f"*{i}.* `{mask}`  {exp}  _{name}_")
                return "\n".join(lines)
            except Exception as e:
                return f"💳 *Карты*\n\n❌ Ошибка: {e}"

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
                f"▸ Готово: *{avail}*  ·  В архиве: *{archiv}*",
            ]
            if bal is not None:
                lines += ["", f"💰 *Баланс GrizzlySMS: `${bal:.4f}`*"]
            return "\n".join(lines)

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
                                    [{"text": "📦 В пул ссылок",
                                      "callback_data": f"profile:topool:{phone}"}],
                                ]})
                else:
                    msgs = {
                        "activated":    f"✨ <b>{phone}</b> — АКТИВИРОВАН\nДо: {vt}",
                        "explore_now":  f"✅ <b>{phone}</b> — Explore Now",
                        "not_logged_in":f"🔒 <b>{phone}</b> — не авторизован",
                    }
                    await _send(cid, msgs.get(st,
                        f"❓ <b>{phone}</b> — {st}" + (f"\n{err_safe}" if err_safe else "")), parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка проверки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)

        async def _bg_address(cid, phone):
            _bg_ops[phone] = "running"
            await _send(cid, f"⏳ Заполняю адрес для <code>{phone}</code>...", parse_mode="HTML")
            try:
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
                else:
                    await _send(cid, f"⚠️ <b>{phone}</b>: {msg2_safe}", parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка адреса <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)

        async def _bg_cookies(cid, phone):
            _bg_ops[phone] = "running"
            await _send(cid, f"⏳ Экспортирую куки <code>{phone}</code>...", parse_mode="HTML")
            try:
                pp = _find_profile(phone)
                if not pp:
                    await _send(cid, f"❌ Профиль <code>{phone}</code> не найден", parse_mode="HTML")
                    return

                def _export():
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
                MAX_CHUNK = 3800
                json_chunks = [safe_json[i:i+MAX_CHUNK] for i in range(0, len(safe_json), MAX_CHUNK)]

                import io
                # 1. Отправка файла
                try:
                    await client.post(f"{api}/sendDocument",
                        data={"chat_id": str(cid), "caption": caption, "parse_mode": "HTML"},
                        files={"document": (fname, io.BytesIO(cookies_json.encode("utf-8")), "application/json")})
                except Exception as fe:
                    await _send(cid, f"❌ Ошибка отправки файла кук: {fe}")

                # 2. Отправка JSON кук текстом
                for i, chunk in enumerate(json_chunks):
                    header = f"Куки {phone} ({len(cookies_out)} шт.)"
                    if len(json_chunks) > 1:
                        header += f" (часть {i+1}/{len(json_chunks)})"
                    msg = f"{header}\n<pre><code class=\"language-json\">{chunk}</code></pre>"
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": msg, "parse_mode": "HTML"})
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
                MAX_CHUNK = 3800
                json_chunks = [safe_json[i:i+MAX_CHUNK] for i in range(0, len(safe_json), MAX_CHUNK)]

                import io
                # 1. Отправка файла
                try:
                    await client.post(f"{api}/sendDocument",
                        data={"chat_id": str(cid), "caption": caption, "parse_mode": "HTML"},
                        files={"document": (fname, io.BytesIO(cookies_json.encode("utf-8")), "application/json")})
                except Exception as fe:
                    await _send(cid, f"❌ Ошибка отправки файла кук: {fe}")

                # 2. Отправка JSON кук текстом
                for i, chunk in enumerate(json_chunks):
                    header = f"Куки {phone} ({len(cookies_out)} шт.)"
                    if len(json_chunks) > 1:
                        header += f" (часть {i+1}/{len(json_chunks)})"
                    msg = f"{header}\n<pre><code class=\"language-json\">{chunk}</code></pre>"
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": msg, "parse_mode": "HTML"})
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка куки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")

        async def _bg_buy(cid, phone, months):
            _bg_ops[phone] = "running"
            tariff = "₹1,499 · 12 мес." if months == 12 else "₹399 · 3 мес."
            await _send(cid, f"⏳ <b>Покупка Black Membership</b>\n\n<code>{phone}</code>\n💳 {tariff}", parse_mode="HTML")
            try:
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
                raw  = await loop.run_in_executor(None, lambda: asyncio.run(
                    _m("_do_buy_membership")(pp, months, cards[0])))
                ok, msg_r = _unpack(raw)

                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                msg_r_safe = escape_html(msg_r)

                if ok:
                    await _send(cid, f"✅ <b>{phone}</b> — куплено\n<i>{msg_r_safe}</i>", parse_mode="HTML")
                else:
                    await _send(cid, f"⚠️ <b>{phone}</b> — не куплено\n<i>{msg_r_safe or 'неизвестно'}</i>", parse_mode="HTML")
            except Exception as e:
                def escape_html(t: str) -> str:
                    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                await _send(cid, f"❌ Ошибка покупки <code>{phone}</code>: {escape_html(str(e))}", parse_mode="HTML")
            finally:
                _bg_ops.pop(phone, None)

        async def _bg_check_all(cid):
            """Проверяет активацию всех профилей."""
            profiles = [p for p in
                        (DONE_PROFILES_DIR.glob("profile_*") if DONE_PROFILES_DIR.exists() else [])
                        if p.is_dir()]
            if not profiles:
                await _send(cid, "📁 _Готовых профилей нет_")
                return
            await _send(cid, f"⏳ *Проверяю {len(profiles)} профилей...*\n_Это займёт время._")
            activated = activate_now = explore = not_logged = access_denied = errors = 0
            error_details = []
            for pp in profiles:
                phone = pp.name.replace("profile_", "")
                try:
                    loop   = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda pp=pp: asyncio.run(
                        _m("_check_black_store_activation")(pp, username=phone, headless=True)))
                    st  = result.get("status", "?") if isinstance(result, dict) else "?"
                    err = result.get("error") if isinstance(result, dict) else None
                    if st == "activated":       activated     += 1
                    elif st == "activate_now":  activate_now  += 1
                    elif st == "explore_now":   explore       += 1
                    elif st == "not_logged_in": not_logged    += 1
                    elif st == "access_denied": access_denied += 1
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
            if access_denied:
                lines.append(f"🌐 Нет индийского IP: *{access_denied}* _(включите VPN или прокси)_")
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
            await _send(cid, f"⏳ *Заполняю адрес для {len(need)} профилей...*")
            ok_cnt = fail_cnt = 0
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
            await _send(cid,
                f"📍 *Адреса заполнены*\n\n"
                f"✅ Успешно: *{ok_cnt}*\n"
                f"❌ Ошибки: *{fail_cnt}*")

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

            # Инициализация: не уведомляем о существующих обновлениях после рестарта
            try:
                _init = await asyncio.get_event_loop().run_in_executor(None, _fetch)
                _notified_update_hashes.update({c.split()[0] for c in _init if c})
                _update_available = bool(_init)
                _update_commits   = _init
                _update_checked   = True
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
                        nc   = [c for c in fetched if c.split()[0] in new_h]
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
                _pm_slink   = _pm.get("black_short_link") or ""
                _info = f"📱 <code>+91 {phone}</code>"
                if _pm_login:
                    _info += f"\n📆 Создан:  <code>{_pm_login}</code>"
                if _pm_issued:
                    _info += f"\n📋 Выдан:   <code>{_pm_issued}</code>"
                if _pm_vt:
                    _info += f"\n⏳ До:       <b>{_pm_vt}</b>"
                if _pm_slink:
                    _info += f"\n🔗 <a href=\"{_pm_slink}\">{_pm_slink}</a>"
                txt = (_info + "\n\n" +
                       ("⏳ <i>Операция выполняется...</i>" if busy else "Выберите действие:"))
                await _edit(cid, mid, txt, _profile_menu_kb(phone, list_type, rec_file), parse_mode="HTML")
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
                await _edit(cid, mid, _cards_text(),
                            {"inline_keyboard": [[{"text": "◀️ Назад",
                                                    "callback_data": "go:other"}]]})
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

            if data == "run:status":
                if _running():
                    lbl = _mode_label(_mode[0])
                    st  = "⏸ Пауза" if _paused[0] else "🟢 Работает"
                    txt = f"{st} · {lbl} · PID {_proc[0].pid}"
                else:
                    txt = "🔴 Нет активного процесса"
                await _ack(qid, txt, alert=True)
                return

            # ── GGSell ────────────────────────────────────────────────────────
            if data in ("go:ggsell", "ggsell:refresh"):
                await _ack(qid)
                await _edit(cid, mid, "⏳ *GGSell* — загружаю данные...",
                            {"inline_keyboard": [[{"text": "◀️ Назад",
                                                    "callback_data": "go:other"}]]})
                asyncio.create_task(_bg_ggsel_info(cid, mid))
                return

            if data == "ggsell:orders":
                await _ack(qid)
                await _edit(cid, mid, "⏳ *GGSell* — загружаю заказы...",
                            {"inline_keyboard": [[{"text": "◀️ Назад",
                                                    "callback_data": "go:ggsell"}]]})
                asyncio.create_task(_bg_ggsel_orders_page(cid, mid))
                return

            if data == "ggsell:chats":
                await _ack(qid)
                await _edit(cid, mid, "⏳ *GGSell* — загружаю чаты...",
                            {"inline_keyboard": [[{"text": "◀️ Назад",
                                                    "callback_data": "go:ggsell"}]]})
                asyncio.create_task(_bg_ggsel_chats_page(cid, mid))
                return

            if data == "ggsell:settings":
                await _ack(qid)
                txt, kb = _ggsel_settings_page(cid, mid)
                await _edit(cid, mid, txt, kb)
                return

            if data == "ggsell:pool":
                await _ack(qid)
                links = _ggsel_read_pool()
                link_btns = []
                for idx, lnk in enumerate(links[:10]):
                    preview = lnk[8:48] + "…" if len(lnk) > 48 else lnk  # убираем https://
                    link_btns.append([{"text": f"📤 {preview}",
                                       "callback_data": f"ggsell:pool_pick:{idx}"}])
                kb_rows = link_btns + [
                    [{"text": "🔄 Обновить", "callback_data": "ggsell:pool"},
                     {"text": "◀️ Назад",    "callback_data": "go:ggsell"}],
                ]
                await _edit(cid, mid, _ggsel_pool_text(), {"inline_keyboard": kb_rows})
                return

            if data.startswith("ggsell:order:"):
                invoice_id = int(data.split(":")[2])
                await _ack(qid)
                await _edit(cid, mid,
                            _ggsel_order_text(invoice_id),
                            _ggsel_order_kb(invoice_id))
                return

            if data.startswith("ggsell:run:"):
                invoice_id = int(data.split(":")[2])
                await _ack(qid, "⏳ Запускаю автоматизацию...")
                asyncio.create_task(_bg_ggsel_run(cid, mid, invoice_id))
                return

            if data.startswith("ggsell:send:"):
                invoice_id = int(data.split(":")[2])
                await _ack(qid, "⏳ Отправляю...")
                asyncio.create_task(_bg_ggsel_send(cid, invoice_id))
                return

            if data.startswith("ggsell:reply:"):
                invoice_id = int(data.split(":")[2])
                _ggsel_reply_mode[cid] = invoice_id
                await _ack(qid)
                await _send(cid,
                    f"💬 *Ответ на заказ* `#{invoice_id}`\n\n"
                    "Напишите сообщение — оно будет отправлено покупателю в чат GGSell:",
                    reply_markup={"inline_keyboard": [
                        [{"text": "❌ Отмена",
                          "callback_data": f"ggsell:reply_cancel:{invoice_id}"}],
                    ]})
                return

            if data.startswith("ggsell:reply_cancel:"):
                invoice_id = int(data.split(":")[2])
                _ggsel_reply_mode.pop(cid, None)
                await _ack(qid, "❌ Отменено")
                await _edit(cid, mid,
                    f"❌ Ответ на заказ `#{invoice_id}` отменён.",
                    {"inline_keyboard": [[{"text": "◀️ GGSell",
                                           "callback_data": "go:ggsell"}]]})
                return

            if data.startswith("ggsell:nosend:"):
                invoice_id = int(data.split(":")[2])
                _ggsel_confirm.pop(invoice_id, None)
                await _ack(qid, "❌ Отправка отменена")
                await _edit(cid, mid,
                            f"❌ Ссылка для заказа `#{invoice_id}` *не отправлена* покупателю.",
                            {"inline_keyboard": [
                                [{"text": "◀️ GGSell", "callback_data": "go:ggsell"}],
                            ]})
                return

            if data.startswith("ggsell:toggle:"):
                kind = data.split(":")[2]  # "orders" or "messages"
                if kind in ("orders", "messages"):
                    cfg_key = f"ggsel_notify_{kind}"
                    new_val = not _get(cid, cfg_key)
                    _set(cid, cfg_key, new_val)
                    label = "🔔 Включено" if new_val else "🔕 Выключено"
                    await _ack(qid, label)
                    txt, kb = _ggsel_settings_page(cid, mid)
                    await _edit(cid, mid, txt, kb)
                else:
                    await _ack(qid)
                return

            if data.startswith("ggsell:topool:"):
                # Переместить ссылку из очереди подтверждения в пул
                invoice_id = int(data.split(":")[2])
                link = _ggsel_confirm.pop(invoice_id, None)
                if not link:
                    await _ack(qid, "❌ Ссылка не найдена", alert=True)
                    return
                from ggsell.monitor import add_link_to_pool
                add_link_to_pool(link)
                await _ack(qid, "📦 Добавлено в пул!")
                await _edit(cid, mid,
                    f"📦 Ссылка для заказа `#{invoice_id}` добавлена в пул.\n\n🔗 `{link}`",
                    {"inline_keyboard": [
                        [{"text": "📦 Открыть пул", "callback_data": "ggsell:pool"},
                         {"text": "◀️ GGSell",      "callback_data": "go:ggsell"}],
                    ]})
                return

            if data.startswith("ggsell:pool_pick:"):
                idx = int(data.split(":")[2])
                links = _ggsel_read_pool()
                if idx >= len(links):
                    await _ack(qid, "❌ Пул изменился, обновите список", alert=True)
                    return
                link = links[idx]
                _pool_pick_pending[cid] = link
                await _ack(qid)
                await _edit(cid, mid, "⏳ Загружаю заказы...",
                            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "ggsell:pool"}]]})
                asyncio.create_task(_bg_ggsel_pool_pick(cid, mid, link))
                return

            if data.startswith("ggsell:pool_order:"):
                invoice_id = int(data.split(":")[2])
                link = _pool_pick_pending.pop(cid, None)
                if not link:
                    await _ack(qid, "❌ Сессия истекла, выберите ссылку снова", alert=True)
                    return
                await _ack(qid, "⏳ Отправляю...")
                asyncio.create_task(_bg_ggsel_pool_send(cid, mid, invoice_id, link))
                return

            if data.startswith("profile:topool:"):
                phone = data.split(":", 2)[2]
                pp = _find_profile(phone)
                if not pp:
                    await _ack(qid, "❌ Профиль не найден", alert=True)
                    return
                try:
                    m = _m("_read_profile_meta")(pp)
                    link = m.get("black_activation_link") or m.get("black_short_link") or ""
                except Exception:
                    link = ""
                if not link:
                    await _ack(qid, "⚠️ Ссылка не найдена в профиле", alert=True)
                    return
                from ggsell.monitor import add_link_to_pool
                add_link_to_pool(link)
                await _ack(qid, f"📦 Ссылка добавлена в пул!")
                return

            # Неизвестная команда
            await _ack(qid)

        # ══════════════════════════════════════════════════════════════════════
        # Обработчик входящих сообщений
        # ══════════════════════════════════════════════════════════════════════

        async def _handle_msg(client, msg):
            cid  = int(msg["chat"]["id"])
            text = (msg.get("text") or "").strip()

            # Режим ответа в GGSell чат — перехватываем ЛЮБОЕ сообщение
            if cid in _ggsel_reply_mode and text and not text.startswith("/"):
                invoice_id = _ggsel_reply_mode.pop(cid)
                asyncio.create_task(_bg_ggsel_reply(cid, invoice_id, text))
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
                    await client.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": _cards_text(),
                                            "parse_mode": "Markdown",
                                            "reply_markup": {"inline_keyboard": [[
                                                {"text": "◀️ Меню", "callback_data": "go:main"},
                                            ]]}})
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

        _timeout_obj = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=_timeout_obj, trust_env=False) as client:

            asyncio.ensure_future(_bg_update_loop())

            # После рестарта от обновления: убираем "Перезапускаю..."
            try:
                rf = Path(__file__).parent / "._restart_msg.json"
                if rf.exists():
                    rm = json.loads(rf.read_text(encoding="utf-8"))
                    rf.unlink()
                    done = rm.get("text", "").replace(
                        "\n\n⚡ _Перезапускаю..._", "\n\n✅ _Перезапущен_")
                    await client.post(f"{api}/editMessageText",
                                      json={"chat_id": rm["chat_id"],
                                            "message_id": rm["msg_id"],
                                            "text": done,
                                            "parse_mode": "Markdown"})
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
                    _first   = False
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

                    # Drain GGSell notify queue → уведомляем о новых заказах
                    try:
                        from ggsell.monitor import notify_queue as _gs_q
                        while True:
                            try:
                                _gs_item = _gs_q.get_nowait()
                                if _gs_item.get("type") == "new_order":
                                    asyncio.create_task(_ggsel_notify_order(_gs_item))
                                elif _gs_item.get("type") == "new_message":
                                    asyncio.create_task(_ggsel_notify_message(_gs_item))
                            except Exception:
                                break
                    except Exception:
                        pass

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
        finally:
            _loop.close()
    else:
        asyncio.run(_poll())
