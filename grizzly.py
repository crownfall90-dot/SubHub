"""
grizzly.py — GrizzlySMS background tasks integration.
Extracted from menu.py.

IMPORTANT: This module runs background tasks on a daemon thread.
It must NOT import menu.py (causes import-lock deadlock) and must NOT
use Playwright objects (not thread-safe across event loops → hangs).
Browser cleanup is done via OS process kill + shutil.rmtree.
"""

import asyncio
import json
import shutil
import sys
import threading
import time
from pathlib import Path

import yaml

try:
    from grizzly_sms import GrizzlySMSClient, GrizzlySMSError
except ImportError:
    GrizzlySMSClient = None
    GrizzlySMSError = Exception

# ── ANSI цвета (локальные копии с префиксом _ чтобы не конфликтовали) ────────
_R   = "\033[91m"
_G   = "\033[92m"
_Y   = "\033[93m"
_C   = "\033[96m"
_DIM = "\033[2m"
_BLD = "\033[1m"
_RST = "\033[0m"

_HERE = Path(__file__).parent

# ── Временные (transient) сообщения в консоли — стираются через N секунд ─────
_TPRINT_LOCK  = threading.Lock()
_TPRINT_LINES = 0
_TPRINT_TIMER: "threading.Timer | None" = None

def _transient_print(text: str, delay: float = 5.0) -> None:
    """Печатает 1–2-строчное сообщение и стирает его через delay секунд (ANSI)."""
    global _TPRINT_LINES, _TPRINT_TIMER
    with _TPRINT_LOCK:
        if _TPRINT_TIMER is not None:
            _TPRINT_TIMER.cancel()
        n = text.count('\n') + 1
        _TPRINT_LINES += n
        print(text, flush=True)
        total = _TPRINT_LINES

        def _erase():
            global _TPRINT_LINES
            with _TPRINT_LOCK:
                if _TPRINT_LINES <= 0:
                    return
                sys.stdout.write(f'\033[{_TPRINT_LINES}A\033[J')
                sys.stdout.flush()
                _TPRINT_LINES = 0

        _TPRINT_TIMER = threading.Timer(delay, _erase)
        _TPRINT_TIMER.daemon = True
        _TPRINT_TIMER.start()

# ── Статистика запуска ────────────────────────────────────────────────────────
_STATS: dict = {
    "numbers_bought":    0,
    "numbers_cancelled": 0,
    "numbers_bad_action": 0,
    "balance_start":     None,
    "balance_end":       None,
    "profiles_saved":    0,
}

def reset_run_stats() -> None:
    _STATS["numbers_bought"]    = 0
    _STATS["numbers_cancelled"] = 0
    _STATS["numbers_bad_action"] = 0
    _STATS["balance_start"]     = None
    _STATS["balance_end"]       = None
    _STATS["profiles_saved"]    = 0

def get_run_stats() -> dict:
    return dict(_STATS)

# ── Standalone config reading (без import menu) ─────────────────────────────

def _read_secrets_standalone() -> dict:
    """Читает secrets.yaml напрямую, без import menu."""
    try:
        sp = _HERE / "secrets.yaml"
        if sp.exists():
            with open(sp, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def _get_telegram_token_standalone() -> str:
    """Получает токен TG-бота исключительно из secrets.yaml."""
    try:
        tok = (_read_secrets_standalone().get("telegram") or {}).get("token", "").strip()
        return tok
    except Exception:
        return ""


def _get_tg_subscribers_standalone() -> list:
    """Возвращает список chat_id подписчиков с включённым buy_number."""
    try:
        sp = _HERE / "data" / "tg_subscribers.json"
        if sp.exists():
            data = json.loads(sp.read_text(encoding="utf-8"))
            ss = data.get("settings", {})
            return [int(c) for c in data.get("chats", [])
                    if ss.get(str(c), {}).get("buy_number", True)]
    except Exception:
        pass
    return []


def _get_all_tg_chat_ids() -> list:
    """Все chat_id без фильтра buy_number — для системных уведомлений."""
    try:
        sp = _HERE / "data" / "tg_subscribers.json"
        if sp.exists():
            data = json.loads(sp.read_text(encoding="utf-8"))
            return [int(c) for c in data.get("chats", [])]
    except Exception:
        pass
    return []


def _get_grizzly_api_key() -> str:
    """Получает API-ключ GrizzlySMS из secrets.yaml."""
    return _read_secrets_standalone().get("grizzlysms", {}).get("api_key", "").strip()


# ── Persistent background asyncio loop (daemon thread) ────────────────────────
# Позволяет фоновым задачам (_bg_cancel_loop, _bg_login_with_otp) продолжать
# работу между вызовами asyncio.run() — т.е. пока открыта консоль.

_BG_LOOP = None


def _get_bg_loop():
    """Возвращает persistent event loop в daemon thread (создаёт при первом вызове)."""
    global _BG_LOOP
    if _BG_LOOP is not None and not _BG_LOOP.is_closed():
        return _BG_LOOP
    loop = asyncio.new_event_loop()
    _BG_LOOP = loop
    threading.Thread(
        target=loop.run_forever,
        daemon=True,
        name="bg-cancel-daemon",
    ).start()
    return loop


# ── OS-level browser cleanup (thread-safe) ───────────────────────────────────

def _kill_chrome_for_profile_standalone(profile_path) -> int:
    """Завершает Chrome-процессы для указанного профиля (thread-safe, синхронный)."""
    import subprocess, os
    path_str = str(profile_path).replace("/", "\\")
    folder_name = os.path.basename(path_str)
    killed = 0
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "chrome" not in (proc.info.get("name") or "").lower():
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if folder_name in cmdline or path_str in cmdline:
                    proc.kill()
                    killed += 1
            except Exception:
                pass
    except ImportError:
        try:
            ps_cmd = (
                f"Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" | "
                f"Where-Object {{$_.CommandLine -like '*{folder_name}*'}} | "
                f"ForEach-Object {{Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}}"
            )
            subprocess.run(["powershell", "-Command", ps_cmd],
                           capture_output=True, timeout=8)
            killed = -1
        except Exception:
            pass
    return killed


# ── Реестр активных номеров для отслеживания и отмены ────────────────────────
_RENTALS = {}
_MONITOR_TASK = None
_BG_FUTURES = []
# IDs успешно завершённых активаций — монитор не должен их повторно регистрировать
_COMPLETED_IDS: set = set()

def register_rental(activation_id, phone_10, rented_at, profile_path=None, login_url=None, months=3, intercept_mode=False, **_ignored):
    """Регистрирует арендованный номер для отслеживания и отмены.

    НЕ принимает pw/ctx/page — Playwright объекты нельзя трогать из другого потока.
    """
    aid = str(activation_id)
    _RENTALS[aid] = {
        "phone_10": phone_10,
        "rented_at": rented_at,
        "status": "active",
        "profile_path": profile_path,
        "login_url": login_url or "https://www.flipkart.com/account/login?ret=/",
        "months": months,
        "intercept_mode": intercept_mode,
        "cancel_attempts": 0,
        "next_attempt_at": rented_at + 150.0,
        "cancelling": False,
    }
    _STATS["numbers_bought"] += 1
    _start_monitor_if_needed()

def update_rental_browser(activation_id, profile_path=None, **_ignored):
    """Обновляет путь профиля для зарегистрированной аренды."""
    aid = str(activation_id)
    if aid in _RENTALS and profile_path is not None:
        _RENTALS[aid]["profile_path"] = profile_path

def mark_completed(activation_id):
    """Помечает номер как успешно завершённый (вход выполнен)."""
    aid = str(activation_id)
    _COMPLETED_IDS.add(aid)
    _RENTALS.pop(aid, None)

def mark_failed(activation_id):
    """Помечает номер как нерабочий. Запускает фоновую очистку."""
    aid = str(activation_id)
    if aid in _RENTALS:
        r = _RENTALS[aid]
        r["status"] = "failed"
        r["next_attempt_at"] = r["rented_at"] + 150.0
        # Очистка профиля (kill chrome + rmtree) — синхронная, thread-safe
        _cleanup_profile(r)


def _cleanup_profile(r):
    """Убивает Chrome-процесс профиля и удаляет папку (thread-safe)."""
    pp = r.get("profile_path")
    if pp:
        try:
            _kill_chrome_for_profile_standalone(pp)
        except Exception:
            pass
        try:
            shutil.rmtree(pp, ignore_errors=True)
            print(f"  {_DIM}[BG] Временный профиль +91 {r['phone_10']} удалён.{_RST}")
        except Exception:
            pass
        r["profile_path"] = None


def _start_monitor_if_needed():
    global _MONITOR_TASK
    loop = _get_bg_loop()
    if _MONITOR_TASK is None or _MONITOR_TASK.done():
        _MONITOR_TASK = asyncio.run_coroutine_threadsafe(_rental_monitor_loop(), loop)

def _monitor_watchdog():
    """Поток-сторож: перезапускает монитор если он упал, раз в 15 сек."""
    import time as _time
    while True:
        _time.sleep(15)
        try:
            _start_monitor_if_needed()
        except Exception:
            pass

def start_global_monitor():
    """Запускает фоновый монитор при старте консоли — сканирует GrizzlySMS API
    на наличие активных номеров даже если _RENTALS пуст."""
    _start_monitor_if_needed()
    import threading
    _wd = threading.Thread(target=_monitor_watchdog, daemon=True, name="grizzly-watchdog")
    _wd.start()

async def _rental_monitor_loop():
    _otp_last_check: dict[str, float] = {}
    _api_scan_at: float = 0.0
    while True:
        try:
            now = time.monotonic()
            api_key = _get_grizzly_api_key()

            # 0. Каждые 10 сек сканируем GrizzlySMS API на активные номера,
            #    которых нет в _RENTALS — подхватываем «чужие» и забытые.
            if api_key and now - _api_scan_at >= 10.0:
                _api_scan_at = now
                try:
                    _scan_client = GrizzlySMSClient(api_key, http_timeout=10)
                    _active_list = await _scan_client.get_active_activations()
                    await _scan_client.close()
                    for _item in (_active_list or []):
                        _aid = str(_item.get("activationId") or _item.get("id") or "")
                        _ph_raw = str(_item.get("phoneNumber") or _item.get("phone") or "")
                        if not _aid or _aid in _RENTALS or _aid in _COMPLETED_IDS:
                            continue
                        _ph10 = _ph_raw[-10:] if len(_ph_raw) >= 10 else _ph_raw
                        print(f"\n  {_Y}[Фон] Обнаружен активный номер +91 {_ph10} (id={_aid}) в GrizzlySMS{_RST}")
                        _RENTALS[_aid] = {
                            "phone_10":      _ph10,
                            "rented_at":     time.monotonic(),  # даём 150 сек; если OTP нет — отменяем
                            "status":        "active",
                            "profile_path":  None,
                            "login_url":     "https://www.flipkart.com/account/login?ret=/",
                            "months":        3,
                            "intercept_mode": False,
                            "cancel_attempts": 0,
                            "next_attempt_at": 0.0,
                            "cancelling":    False,
                            "external":      True,
                        }
                except Exception:
                    pass

            # 1. Проверяем активные номера на OTP каждые 10 сек
            if api_key:
                for aid, r in list(_RENTALS.items()):
                    if r["status"] == "active" and not r.get("intercept_mode") and not r.get("external"):
                        last_check = _otp_last_check.get(aid, 0.0)
                        if now - last_check >= 10.0:
                            _otp_last_check[aid] = now
                            try:
                                client = GrizzlySMSClient(api_key, http_timeout=10)
                                st = await client.get_status(aid)
                                await client.close()
                                if st.get("type") == "OK" and st.get("code"):
                                    otp = st["code"]
                                    if aid not in _RENTALS:
                                        continue
                                    print(f"\n  {_G}📲 OTP для +91 {r['phone_10']}: {otp} — вход в фоне...{_RST}")
                                    login_url = ""
                                    months = 3
                                    try:
                                        with open(_HERE / "config.yaml", encoding="utf-8") as fh:
                                            cfg = yaml.safe_load(fh)
                                        login_url = cfg.get("site", {}).get("url", "https://www.flipkart.com/account/login?ret=/")
                                    except Exception:
                                        pass
                                    _submit_bg_login(api_key, aid, otp, login_url, months, phone_10=r["phone_10"])
                                    r["status"] = "completed"
                                    _RENTALS.pop(aid, None)
                                    _otp_last_check.pop(aid, None)
                            except Exception:
                                pass

            # 2. Переводим активные номера без OTP в failed после 150 сек
            for aid, r in list(_RENTALS.items()):
                if r["status"] == "active":
                    if now - r["rented_at"] >= 150.0:
                        r["status"] = "failed"
                        r["next_attempt_at"] = r["rented_at"] + 150.0
                        _cleanup_profile(r)

            # 3. Проверяем неудачные номера, готовые к отмене
            for aid, r in list(_RENTALS.items()):
                if r["status"] == "failed":
                    if now >= r["next_attempt_at"] and not r.get("cancelling"):
                        asyncio.create_task(_cancel_rental_task(aid))
        except Exception:
            pass
        try:
            await asyncio.sleep(5)
        except BaseException:
            pass  # CancelledError и пр. — не останавливаем цикл

async def _cancel_rental_task(aid):
    r = _RENTALS.get(aid)
    if not r or r.get("cancelling"):
        return
    r["cancelling"] = True
    try:
        api_key = _get_grizzly_api_key()
        if not api_key:
            print(f"  {_R}[BG] Нет API-ключа GrizzlySMS для отмены{_RST}")
            _RENTALS.pop(aid, None)
            return
        client = GrizzlySMSClient(api_key, http_timeout=15)
        
        # Проверяем, вдруг OTP пришел в последний момент перед отменой
        try:
            st = await client.get_status(aid)
            if st["type"] == "OK" and st.get("code"):
                otp = st["code"]
                # Если основной поток уже обработал этот номер (mark_completed удалил из _RENTALS) —
                # не запускаем фоновый вход повторно
                if aid not in _RENTALS:
                    await client.close()
                    return
                # В intercept-режиме OTP передаётся в TG основным потоком —
                # монитор не должен запускать фоновый вход, только завершить аренду
                if r.get("intercept_mode") or r.get("external"):
                    # intercept: OTP уже передан основным потоком в TG
                    # external: номер подхвачен сканером — его уже обработал
                    # _background_login_monitor в main.py; повторный вход не нужен
                    _reason = "перехват" if r.get("intercept_mode") else "внешний, уже обработан"
                    print(f"\n  {_G}✓ OTP для +91 {r['phone_10']} ({_reason}) — завершаю аренду{_RST}")
                    try:
                        await client.complete(aid)
                    except Exception:
                        pass
                    mark_completed(aid)  # добавляет в _COMPLETED_IDS, не даёт сканеру переподхватить
                    await client.close()
                    return
                print(f"\n  {_G}✓ OTP для +91 {r['phone_10']} пришёл в последний момент: {otp}. Вход...{_RST}")
                login_url = ""
                months = 3
                try:
                    with open(_HERE / "config.yaml", encoding="utf-8") as fh:
                        cfg = yaml.safe_load(fh)
                    login_url = cfg.get("site", {}).get("url", "https://www.flipkart.com/account/login?ret=/")
                except Exception:
                    pass
                _submit_bg_login(api_key, aid, otp, login_url, months, phone_10=r["phone_10"])
                r["status"] = "completed"
                _RENTALS.pop(aid, None)
                await client.close()
                return
        except Exception:
            pass
            
        try:
            await client.cancel(aid)
            _transient_print(f"  {_G}[Фон] ✅ +91 {r['phone_10']} отменён{_RST}")
            r["status"] = "cancelled"
            _STATS["numbers_cancelled"] += 1
            _RENTALS.pop(aid, None)
        except Exception as ce:
            if "BAD_ACTION" in str(ce):
                _transient_print(f"  {_Y}[Фон] ⚠ +91 {r['phone_10']} — уже не существует{_RST}")
                _STATS["numbers_bad_action"] += 1
                _RENTALS.pop(aid, None)
            else:
                r["cancel_attempts"] += 1
                now = time.monotonic()
                if r["cancel_attempts"] == 1:
                    r["next_attempt_at"] = r["rented_at"] + 150.0 + 10.0
                else:
                    r["next_attempt_at"] = now + 10.0
                _transient_print(f"  {_Y}[Фон] ↺ +91 {r['phone_10']} — повтор через 10 сек{_RST}")
        
        await client.close()
    finally:
        if aid in _RENTALS:
            _RENTALS[aid]["cancelling"] = False

async def _tg_cancel_notify(ph: str, reason: str = "") -> None:
    """Отправляет TG-уведомление об отмене номера (standalone, без import menu)."""
    try:
        import httpx as _hx_c
        _tok = _get_telegram_token_standalone()
        if not _tok:
            return
        _nc = _get_tg_subscribers_standalone()
        if not _nc:
            return
        try:
            api_key = _get_grizzly_api_key()
            client = GrizzlySMSClient(api_key, http_timeout=15)
            _bal = await client.get_balance()
            await client.close()
            _bal_line = f"\n💰 Баланс GrizzlySMS: `${_bal:.4f}`"
        except Exception:
            _bal_line = ""
        _msg = f"❌ *Номер отменён*\n\n`{ph}`"
        if reason:
            _msg += f"\n_{reason}_"
        _msg += _bal_line
        async with _hx_c.AsyncClient(timeout=8, trust_env=False) as _hcn:
            for _c in _nc:
                try:
                    await _hcn.post(
                        f"https://api.telegram.org/bot{_tok}/sendMessage",
                        json={"chat_id": _c, "text": _msg, "parse_mode": "Markdown"})
                except Exception:
                    pass
    except Exception:
        pass


async def _tg_login_fail_notify(phone_10: str, otp_code: str, error_msg: str) -> None:
    """Шлёт TG-уведомление об ошибке фонового входа всем подписчикам."""
    try:
        import httpx as _hx_c
        _tok = _get_telegram_token_standalone()
        if not _tok:
            return
        _nc = _get_all_tg_chat_ids()
        if not _nc:
            return
        _msg = (
            f"⚠️ *Ошибка фонового входа*\n\n"
            f"📞 Номер: `{phone_10}`\n"
            f"🔑 OTP: `{otp_code}`\n"
            f"📝 Статус: _{error_msg}_"
        )
        async with _hx_c.AsyncClient(timeout=8, trust_env=False) as _hcn:
            for _c in _nc:
                try:
                    await _hcn.post(
                        f"https://api.telegram.org/bot{_tok}/sendMessage",
                        json={"chat_id": _c, "text": _msg, "parse_mode": "Markdown"})
                except Exception:
                    pass
    except Exception:
        pass


async def _tg_login_ok_notify(phone_10: str) -> None:
    """Шлёт TG-уведомление об успешном фоновом входе."""
    try:
        import httpx as _hx_ok
        _tok = _get_telegram_token_standalone()
        if not _tok:
            return
        _nc = _get_tg_subscribers_standalone()
        if not _nc:
            return
        _msg = f"✅ *Вход выполнен*\n\n`{phone_10}`\n_Профиль готов_"
        async with _hx_ok.AsyncClient(timeout=8, trust_env=False) as _hok:
            for _c in _nc:
                try:
                    await _hok.post(
                        f"https://api.telegram.org/bot{_tok}/sendMessage",
                        json={"chat_id": _c, "text": _msg, "parse_mode": "Markdown"})
                except Exception:
                    pass
    except Exception:
        pass


async def _send_cookies_to_tg_standalone(ctx2, phone_10: str, otp_code: str = "") -> None:
    """Отправляет куки из фонового контекста в Telegram (файл и текст)."""
    try:
        import json as _jo, io, httpx as _hx
        _tok = _get_telegram_token_standalone()
        _nc = _get_tg_subscribers_standalone()
        if not _tok or not _nc:
            return

        raw = await ctx2.cookies()
        if not raw:
            return

        ss_map = {"Lax": "lax", "Strict": "strict", "None": "no_restriction", "": "no_restriction"}
        allowed_names = {"T", "ULSN", "at", "rt", "vd", "ud", "S", "SN"}
        all_fk = [c for c in raw if "flipkart.com" in (c.get("domain") or "").lower() and c.get("name") in allowed_names]
        if not all_fk:
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

        cookies_json = _jo.dumps(cookies_out, ensure_ascii=False, indent=2)
        cookies_json_compact = _jo.dumps(cookies_out, ensure_ascii=False, separators=(",", ":"))

        # Локальный бэкап куков на диск
        try:
            _bk_dir = Path("cookies_backup")
            _bk_dir.mkdir(exist_ok=True)
            _bk_name = f"cookies_{phone_10}.json"
            (_bk_dir / _bk_name).write_text(cookies_json, encoding="utf-8")
        except Exception:
            pass

        otp_line = f"\n🔑 OTP: <code>{otp_code}</code>" if otp_code else ""
        label_phone = phone_10
        phone_code = f"<code>{label_phone}</code>"
        caption = f"🍪 Файл кук <code>{label_phone}</code> (фон){otp_line} ({len(cookies_out)} шт.)"
        fname = f"cookies_{phone_10}.json"

        def escape_html(t: str) -> str:
            return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        safe_json = escape_html(cookies_json_compact)
        otp_prefix = f"🔑 OTP: <code>{otp_code}</code>\n" if otp_code else ""
        header_base = f"<b>Куки {label_phone} (фон) ({len(cookies_out)} шт.):</b>"
        _TAGS = len(f"{otp_prefix}{header_base}\n<pre><code class=\"language-json\"></code></pre>")
        _TG_MAX = 4096 - _TAGS - 10
        text_msg = None
        if len(safe_json) <= _TG_MAX:
            text_msg = f"{otp_prefix}{header_base}\n<pre><code class=\"language-json\">{safe_json}</code></pre>"

        async with _hx.AsyncClient(timeout=15, trust_env=False) as _client:
            for _chat in _nc:
                try:
                    # 1. Отправка файла
                    await _client.post(
                        f"https://api.telegram.org/bot{_tok}/sendDocument",
                        data={"chat_id": str(_chat), "caption": caption, "parse_mode": "HTML"},
                        files={"document": (fname, io.BytesIO(cookies_json.encode("utf-8")), "application/json")}
                    )

                    # 2. Текст — только если влезает в одно сообщение
                    if text_msg:
                        await _client.post(
                            f"https://api.telegram.org/bot{_tok}/sendMessage",
                            json={"chat_id": _chat, "text": text_msg, "parse_mode": "HTML"}
                        )
                except Exception:
                    pass
    except Exception:
        pass


def _submit_bg_cancel(activation_ids: list, api_key: str,
                       login_url: str = "", months: int = 3) -> None:
    """Для обратной совместимости: регистрирует и отменяет номера."""
    for aid in activation_ids:
        aid_s = str(aid)
        if aid_s not in _RENTALS:
            register_rental(aid_s, "unknown", time.monotonic())
        mark_failed(aid_s)


def _submit_bg_login(api_key: str, activation_id: str, otp_code: str,
                     login_url: str, months: int, phone_10: str = "") -> None:
    """Fire-and-forget: фоновый вход на _BG_LOOP (переживает asyncio.run() exit)."""
    fut = asyncio.run_coroutine_threadsafe(
        _bg_login_with_otp(api_key, activation_id, otp_code, login_url, months, phone_10),
        _get_bg_loop(),
    )
    _BG_FUTURES.append(fut)


def cleanup_all_rentals_on_exit():
    """Быстрая очистка готовых номеров при выходе из скрипта.
    Номера у которых ещё не прошло 2 мин — остаются в фоновом мониторе (_BG_LOOP),
    который продолжает работать пока открыта консоль."""
    import concurrent.futures

    # Ждём завершения всех фоновых входов перед отчисткой номеров
    active_futures = [f for f in _BG_FUTURES if not f.done()]
    if active_futures:
        print(f"\n{_Y}{_BLD}  [Выход] Ожидание завершения фонового входа ({len(active_futures)} шт.)...{_RST}")
        try:
            concurrent.futures.wait(active_futures, timeout=75)
        except KeyboardInterrupt:
            print(f"  {_Y}[Выход] Прервано — пропускаю ожидание фонового входа{_RST}")

    active_ids = [aid for aid, r in _RENTALS.items() if r["status"] in ("active", "failed")]
    if not active_ids:
        return

    # Убиваем Chrome-процессы для всех профилей
    for aid in list(_RENTALS.keys()):
        r = _RENTALS.get(aid)
        if r:
            _cleanup_profile(r)

    # Переводим active → failed
    now = time.monotonic()
    for aid in active_ids:
        r = _RENTALS.get(aid)
        if r and r["status"] == "active":
            r["status"] = "failed"

    # Разделяем: готовые к отмене прямо сейчас, и те что ещё нельзя (< 2 мин)
    ready_ids   = [aid for aid in active_ids
                   if aid in _RENTALS and now >= _RENTALS[aid].get("next_attempt_at", 0)]
    pending_ids = [aid for aid in active_ids
                   if aid in _RENTALS and now < _RENTALS[aid].get("next_attempt_at", 0)]

    if pending_ids:
        phones = ", ".join(f"+91 {_RENTALS[aid]['phone_10']}" for aid in pending_ids if aid in _RENTALS)
        print(f"\n  {_Y}[Фон] {len(pending_ids)} номер(ов) < 2 мин — отмена в фоне: {phones}{_RST}")
        _start_monitor_if_needed()

    if not ready_ids:
        return

    print(f"\n{_Y}{_BLD}  [Выход] Завершение работы. Очищаю оставшиеся номера ({len(ready_ids)} шт.)...{_RST}")

    loop = asyncio.new_event_loop()

    async def _async_cleanup():
        api_key = _get_grizzly_api_key()
        if not api_key:
            print(f"  {_R}[Выход] Нет API-ключа для отмены номеров{_RST}")
            return
        client = GrizzlySMSClient(api_key, http_timeout=15)
        try:
            for aid in list(ready_ids):
                r = _RENTALS.get(aid)
                if not r:
                    continue
                age = time.monotonic() - r["rented_at"]
                print(f"  [Выход] Попытка отмены +91 {r['phone_10']} (id={aid}), прошло {int(age)} сек...")
                try:
                    await client.cancel(aid)
                    print(f"  {_G}[Выход✓] Номер +91 {r['phone_10']} успешно отменён.{_RST}")
                    r["status"] = "cancelled"
                    _STATS["numbers_cancelled"] += 1
                    await _tg_cancel_notify(r["phone_10"], "Отменён при выходе из скрипта")
                    _RENTALS.pop(aid, None)
                except Exception as e:
                    # Проверяем, не пришёл ли код в последний момент
                    try:
                        _st = await client.get_status(aid)
                        if _st.get("type") == "OK" and _st.get("code"):
                            print(f"  {_G}[Выход✓] Код {_st['code']} обнаружен — вход в фоне{_RST}")
                            fut = asyncio.run_coroutine_threadsafe(
                                _bg_login_with_otp(api_key, aid, _st["code"],
                                                   r.get("login_url", ""), r.get("months", 3), r["phone_10"]),
                                _get_bg_loop(),
                            )
                            _BG_FUTURES.append(fut)
                            r["status"] = "completed"
                            _RENTALS.pop(aid, None)
                            continue
                    except Exception:
                        pass
                    err_str = str(e)
                    if "BAD_ACTION" in err_str:
                        print(f"  {_Y}[Выход] +91 {r['phone_10']}: BAD_ACTION — удаляю.{_RST}")
                        _STATS["numbers_bad_action"] += 1
                        _RENTALS.pop(aid, None)
                    else:
                        # EARLY_CANCEL_DENIED или сеть — оставляем фоновому монитору
                        r["next_attempt_at"] = time.monotonic() + 10
                        print(f"  {_Y}[Фон⏳] +91 {r['phone_10']} — повтор в фоне через 10 сек ({e}){_RST}")
        finally:
            await client.close()

    try:
        loop.run_until_complete(_async_cleanup())
        # Ждём завершения новых фоновых входов, запущенных во время очистки
        active_futures = [f for f in _BG_FUTURES if not f.done()]
        if active_futures:
            print(f"\n{_Y}{_BLD}  [Выход] Ожидание фоновых входов ({len(active_futures)} шт.)...{_RST}")
            concurrent.futures.wait(active_futures, timeout=75)
    except KeyboardInterrupt:
        print(f"\n{_R}  [Выход] Очистка прервана.{_RST}")
    finally:
        loop.close()

    # Запускаем монитор для номеров что остались (EARLY_CANCEL_DENIED, pending и т.д.)
    if any(r["status"] in ("active", "failed") for r in _RENTALS.values()):
        _start_monitor_if_needed()


async def _bg_login_with_otp(api_key: str, activation_id: str, otp_code: str,
                              login_url: str, months: int, phone_10: str = "") -> None:
    """
    Фоновый вход в Flipkart с OTP «проигравшего» номера.
    Создаёт отдельный браузер (headless), выполняет вход, сохраняет профиль.
    Использует динамический import menu только для функций Playwright (безопасно,
    т.к. к моменту вызова menu уже полностью импортирован).
    """
    import importlib
    _menu = importlib.import_module("menu")

    from playwright.async_api import async_playwright
    from grizzly_sms import GrizzlySMSClient
    import random as _rbg

    DONE_PROFILES_DIR        = _menu.DONE_PROFILES_DIR
    _pre_inject_chrome_prefs = _menu._pre_inject_chrome_prefs
    _browser_launch_kw       = _menu._browser_launch_kw
    _flipkart_phase1         = _menu._flipkart_phase1
    _OTP_SEL                 = _menu._OTP_SEL

    client = GrizzlySMSClient(api_key, http_timeout=15)
    _bg_del_profile = False
    ctx2 = None
    profile_path = None
    pw = await async_playwright().start()
    try:
        try:
            # Если phone_10 не передан — ищем через активные активации
            if not phone_10:
                try:
                    acts = await client.get_active_activations()
                    for a in acts:
                        aid_s = str(a.get("activationId") or a.get("id") or "")
                        if aid_s == str(activation_id):
                            raw_ph = str(a.get("phoneNumber") or a.get("phone") or "")
                            raw_ph = raw_ph.lstrip("+")
                            if raw_ph.startswith("91") and len(raw_ph) > 10:
                                raw_ph = raw_ph[2:]
                            phone_10 = raw_ph[-10:]
                            break
                except Exception:
                    pass

            if not phone_10:
                print(f"  [BG] Не удалось определить номер для id={activation_id}")
                return

            profile_path = DONE_PROFILES_DIR / f"profile_{phone_10}"
            if profile_path.exists():
                print(f"  [BG] Профиль +91 {phone_10} уже существует, пропускаю")
                try:
                    await client.complete(activation_id)
                except Exception:
                    pass
                mark_completed(activation_id)
                try:
                    await pw.stop()
                except Exception:
                    pass
                return
            # Также проверяем профили с другим форматом имени (напр. profile_0004_919850389594)
            try:
                phone_variants = (phone_10, "91" + phone_10)
                if any(
                    any(v in p.name for v in phone_variants)
                    for p in DONE_PROFILES_DIR.iterdir() if p.is_dir()
                ):
                    print(f"  [BG] Профиль +91 {phone_10} уже существует (другое имя), пропускаю")
                    try:
                        await client.complete(activation_id)
                    except Exception:
                        pass
                    mark_completed(activation_id)
                    try:
                        await pw.stop()
                    except Exception:
                        pass
                    return
            except Exception:
                pass

            profile_path.mkdir(parents=True, exist_ok=True)
            _pre_inject_chrome_prefs(profile_path)

            ctx2 = await pw.chromium.launch_persistent_context(
                str(profile_path.resolve()),
                **_browser_launch_kw(headless=True, phone=phone_10)
            )
            page2 = ctx2.pages[0] if ctx2.pages else await ctx2.new_page()
            r2 = await _flipkart_phase1(page2, login_url, phone_10)
            if r2 != "ok":
                print(f"  [BG] Фаза1 не прошла: {r2}")
                await _tg_login_fail_notify(phone_10, otp_code, f"Фаза 1 не прошла (ввод номера): {r2}")
                _bg_del_profile = True
                return

            # Вводим OTP (посимвольно — триггерит React onChange)
            # GrizzlySMS не отдаёт новый код после статуса OK, поэтому используем
            # исходный OTP — он ещё действителен сразу после Phase 1.
            print(f"  [BG] +91 {phone_10}: OTP {otp_code} — ввожу")
            otp_el = page2.locator(_OTP_SEL).first
            _auto_submitted = False
            try:
                await otp_el.wait_for(state="visible", timeout=15_000)
                _bb_e = await otp_el.bounding_box()
                if _bb_e:
                    await page2.mouse.click(_bb_e["x"] + _bb_e["width"] / 2,
                                            _bb_e["y"] + _bb_e["height"] / 2)
                else:
                    await otp_el.click()
                await page2.wait_for_timeout(150)
                await page2.keyboard.press("Control+a")
                await page2.keyboard.press("Delete")
                for ch in otp_code:
                    await page2.keyboard.type(ch)
                    await asyncio.sleep(_rbg.uniform(0.05, 0.10))
                    try:
                        if "login" not in page2.url.lower():
                            _auto_submitted = True
                            break
                    except Exception:
                        _auto_submitted = True
                        break
                await page2.wait_for_timeout(400)
            except Exception as e:
                print(f"  [BG] Ошибка ввода OTP: {e}. Fallback keyboard...")
                try:
                    await page2.keyboard.type(otp_code, delay=80)
                except Exception:
                    pass

            # Цикл верификации OTP (до 120 секунд)
            try:
                login_success = _auto_submitted and "login" not in page2.url.lower()
            except Exception:
                login_success = _auto_submitted  # соединение потеряно, доверяем _auto_submitted
            deadline = time.time() + 30.0
            _btn_clicked = False

            try:
                await page2.wait_for_timeout(300)
            except Exception: pass

            while time.time() < deadline:
                # Проверяем редирект в начале каждой итерации
                try:
                    _cur_url = page2.url.lower()
                except Exception:
                    if _auto_submitted:
                        login_success = True
                    break
                if "login" not in _cur_url:
                    login_success = True
                    break

                # Если кнопку ещё не нажали — проверяем и переводим OTP
                if not _btn_clicked:
                    try:
                        otp_val = await page2.eval_on_selector(_OTP_SEL, "el => el.value")
                        if not otp_val:
                            print(f"  [BG] Поле OTP пустое, ввожу заново для +91 {phone_10}...")
                            otp_el = page2.locator(_OTP_SEL).first
                            await otp_el.click()
                            await page2.keyboard.type(otp_code, delay=80)
                            await page2.wait_for_timeout(200)
                    except Exception:
                        pass

                # Нажимаем кнопку VERIFY (trusted click через координаты)
                if not _btn_clicked:
                    _verify_sels = [
                        "button:has-text('VERIFY')", "button:has-text('Verify')",
                        "button:has-text('LOGIN')",  "button:has-text('Login')",
                        "button:has-text('CONTINUE')", "button:has-text('Continue')",
                        "button:has-text('Signup')",   "button:has-text('SIGNUP')",
                    ]
                    _clicked = False
                    for _sel in _verify_sels:
                        try:
                            _btn = page2.locator(_sel).first
                            if await _btn.is_visible():
                                _bb = await _btn.bounding_box()
                                if _bb:
                                    await page2.mouse.click(
                                        _bb["x"] + _bb["width"] / 2,
                                        _bb["y"] + _bb["height"] / 2,
                                    )
                                else:
                                    await _btn.click()
                                _clicked = True
                                _btn_clicked = True
                                break
                        except Exception:
                            pass
                    if not _clicked:
                        # Fallback: Enter на OTP-поле
                        try:
                            _otp_loc = page2.locator(_OTP_SEL).first
                            if await _otp_loc.count() > 0:
                                await _otp_loc.press("Enter")
                            else:
                                await page2.keyboard.press("Enter")
                        except Exception:
                            try:
                                await page2.keyboard.press("Enter")
                            except Exception:
                                pass

                await page2.wait_for_timeout(1000)

            if login_success:
                try:
                    (profile_path / ".profile_meta.json").write_text(
                        json.dumps({
                            "username": phone_10,
                            "login_ts": time.time(),
                            "otp_code": otp_code,
                            "source": "bg_loser",
                        }, ensure_ascii=False), encoding="utf-8"
                    )
                    _STATS["profiles_saved"] += 1
                except Exception:
                    pass
                try:
                    await client.complete(activation_id)
                except Exception:
                    pass
                print(f"  [BG✓] Профиль +91 {phone_10} сохранён (фоновый вход)")
                try:
                    await _tg_login_ok_notify(phone_10)
                except Exception:
                    pass
                # TG: отправка кук
                try:
                    await _send_cookies_to_tg_standalone(ctx2, phone_10, otp_code)
                except Exception as _bgcke:
                    print(f"  [BG] Ошибка отправки кук в TG: {_bgcke}")
            else:
                print(f"  [BG] Фоновый вход +91 {phone_10} не прошёл в течение 30 секунд")
                await _tg_login_fail_notify(phone_10, otp_code, "Таймаут входа (30 секунд истекло, сайт не перенаправил)")
                _bg_del_profile = True
        except BaseException as e:
            _err_str = str(e).lower()
            _conn_closed = any(k in _err_str for k in ("connection", "closed", "driver", "disconnected", "target closed"))
            if _conn_closed and _auto_submitted:
                print(f"  [BG] Соединение с браузером потеряно для +91 {phone_10}, но страница уже перешла с логина — профиль сохраняем")
                try:
                    _meta_path = profile_path / ".profile_meta.json"
                    if profile_path and not _meta_path.exists():
                        _meta_path.write_text(
                            json.dumps({"username": phone_10, "login_ts": time.time(),
                                        "otp_code": otp_code, "source": "bg_loser"},
                                       ensure_ascii=False), encoding="utf-8"
                        )
                        _STATS["profiles_saved"] += 1
                except Exception:
                    pass
                try:
                    await client.complete(activation_id)
                except Exception:
                    pass
                try:
                    await _tg_login_ok_notify(phone_10)
                except Exception:
                    pass
                _bg_del_profile = False
            else:
                if not isinstance(e, Exception):
                    print(f"  [BG] Прервано ({type(e).__name__}) для +91 {phone_10} — профиль удаляется")
                else:
                    print(f"  [BG] Ошибка при фоновом входе +91 {phone_10}: {e}")
                try:
                    await _tg_login_fail_notify(phone_10, otp_code, f"{type(e).__name__}: {e}")
                except Exception:
                    pass
                _bg_del_profile = True
        finally:
            if _bg_del_profile:
                try:
                    if ctx2:
                        await ctx2.close()
                    await pw.stop()
                except Exception:
                    pass
            if _bg_del_profile and profile_path and profile_path.exists():
                try:
                    shutil.rmtree(profile_path, ignore_errors=True)
                    print(f"  [BG] Профиль +91 {phone_10} удалён (неуспешный вход)")
                except Exception:
                    pass
    finally:
        await client.close()
