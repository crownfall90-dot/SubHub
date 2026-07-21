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

# ── Throttled-логирование ошибок монитора ───────────────────────────────────
# Монитор крутится каждые 5 сек — без троттла одна и та же ошибка засыпала бы
# консоль. Печатаем не чаще раза в _ERR_LOG_INTERVAL сек на каждый ключ, с
# подсчётом подавленных повторов. Раньше эти ошибки глотались (except: pass),
# из-за чего сбои оплаты/возврата/OTP были невидимы для диагностики.
_ERR_LOG_AT: dict[str, float] = {}
_ERR_LOG_COUNT: dict[str, int] = {}
_ERR_LOG_INTERVAL = 60.0

def _log_err(key: str, msg: str) -> None:
    """Печатает ошибку монитора не чаще раза в минуту на ключ (с числом повторов)."""
    now = time.monotonic()
    last = _ERR_LOG_AT.get(key, 0.0)
    _ERR_LOG_COUNT[key] = _ERR_LOG_COUNT.get(key, 0) + 1
    if now - last < _ERR_LOG_INTERVAL:
        return
    cnt = _ERR_LOG_COUNT[key]
    _ERR_LOG_AT[key] = now
    _ERR_LOG_COUNT[key] = 0
    _rep = f" {_DIM}(×{cnt} за минуту){_RST}" if cnt > 1 else ""
    print(f"  {_Y}[Фон] ⚠ {msg}{_RST}{_rep}", flush=True)

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


def _read_gsms_float(key: str, default: float) -> float:
    try:
        with open(_HERE / "config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return float((cfg.get("grizzlysms") or {}).get(key, default))
    except Exception:
        return default


def _number_lifetime_seconds() -> float:
    return _read_gsms_float("number_lifetime_seconds", 180.0)


def _no_phase1_cancel_seconds() -> float:
    """Таймаут отмены, если номер куплен, но не введён на Flipkart и OTP нет."""
    lifetime = _number_lifetime_seconds()
    return min(_read_gsms_float("no_phase1_cancel_seconds", 90.0), lifetime)


def _grizzly_min_cancel_age() -> float:
    """Минимальный возраст номера перед отменой в GrizzlySMS (по умолчанию 1:30)."""
    return _read_gsms_float("grizzly_min_cancel_seconds", 90.0)


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
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                           capture_output=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed = -1
        except Exception:
            pass
    return killed


def kill_all_bot_chrome() -> int:
    """Убивает ВСЕ Chrome-процессы запущенные этим ботом (chrome_profiles*)."""
    import subprocess
    markers = ["chrome_profiles", "chrome_profiles_done",
               "chrome_profiles_backup", "chrome_profiles_used",
               "_vpn_ping_profile"]
    killed = 0
    try:
        import psutil
        # Сначала берём только chrome-процессы (без cmdline — быстро)
        chrome_pids = [
            p.pid for p in psutil.process_iter(["pid", "name"])
            if "chrome" in (p.info.get("name") or "").lower()
        ]
        # Затем проверяем cmdline только у chrome (не у всех процессов)
        for pid in chrome_pids:
            try:
                proc = psutil.Process(pid)
                cmdline = " ".join(proc.cmdline())
                if any(m in cmdline for m in markers):
                    proc.kill()
                    killed += 1
            except Exception:
                pass
    except ImportError:
        try:
            conditions = " -or ".join(
                f"$_.CommandLine -like '*{m}*'" for m in markers
            )
            ps_cmd = (
                f"Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" | "
                f"Where-Object {{{conditions}}} | "
                f"ForEach-Object {{Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}}"
            )
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
        "phase1_ok": False,
        "otp_received": False,
        "cancel_attempts": 0,
        "next_attempt_at": rented_at + _grizzly_min_cancel_age(),
        "cancelling": False,
    }
    _STATS["numbers_bought"] += 1
    _start_monitor_if_needed()

def mark_phase1_ok(activation_id) -> None:
    """Номер введён на Flipkart (phase1), ждём OTP."""
    aid = str(activation_id)
    if aid in _RENTALS:
        _RENTALS[aid]["phase1_ok"] = True

def mark_otp_received(activation_id) -> None:
    """OTP получен от GrizzlySMS."""
    aid = str(activation_id)
    if aid in _RENTALS:
        _RENTALS[aid]["otp_received"] = True
        _RENTALS[aid]["phase1_ok"] = True

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
        r["next_attempt_at"] = r["rented_at"] + _grizzly_min_cancel_age()
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
    """Запускает фоновый монитор аренд. Пока номеров нет, цикл простаивает и
    не обращается к API GrizzlySMS — опрос включается только когда автоматизация
    арендует номер (register_rental). Забытые с прошлого запуска номера
    отменяются один раз через startup_cleanup_active_rentals()."""
    _start_monitor_if_needed()
    import threading
    _wd = threading.Thread(target=_monitor_watchdog, daemon=True, name="grizzly-watchdog")
    _wd.start()


async def fetch_active_rentals_status() -> dict:
    """Только чтение: сколько активных номеров и баланс (без отмены)."""
    result: dict = {"total": 0, "balance": None}
    api_key = _get_grizzly_api_key()
    if not api_key:
        result["error"] = "no_api_key"
        return result
    if GrizzlySMSClient is None:
        result["error"] = "no_client"
        return result

    client = GrizzlySMSClient(api_key, http_timeout=10)
    try:
        try:
            active = await client.get_active_activations() or []
            result["total"] = len(active)
        except Exception as exc:
            result["error"] = str(exc)
            return result
        try:
            result["balance"] = await client.get_balance()
        except Exception:
            pass
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return result


def fetch_active_rentals_status_blocking(timeout: float = 12.0) -> dict:
    """Синхронная обёртка для UI."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(fetch_active_rentals_status(), timeout=timeout)
        )
    except asyncio.TimeoutError:
        return {"error": "timeout", "total": 0, "balance": None}
    except Exception as exc:
        return {"error": str(exc), "total": 0, "balance": None}
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def cancel_all_active_rentals(reason: str = "") -> dict:
    """Отменяет все активные номера через GrizzlySMS API."""
    result: dict = {
        "total": 0, "cancelled": 0, "failed": 0, "phones": [], "balance": None,
    }
    api_key = _get_grizzly_api_key()
    if not api_key:
        result["error"] = "no_api_key"
        return result
    if GrizzlySMSClient is None:
        result["error"] = "no_client"
        return result

    client = GrizzlySMSClient(api_key, http_timeout=15)
    try:
        try:
            active = await client.get_active_activations() or []
        except Exception as exc:
            result["error"] = str(exc)
            return result

        result["total"] = len(active)
        if not active:
            return result

        tag = f" ({reason})" if reason else ""
        print(f"\n  {_Y}[Grizzly] Отмена {len(active)} активных номеров{tag}...{_RST}", flush=True)

        notify_tasks = []
        for item in active:
            aid = str(item.get("activationId") or item.get("id") or "")
            ph_raw = str(item.get("phoneNumber") or item.get("phone") or "")
            if not aid:
                continue
            ph10 = ph_raw[-10:] if len(ph_raw) >= 10 else ph_raw
            try:
                await client.cancel(aid)
                result["cancelled"] += 1
                result["phones"].append(ph10)
                _RENTALS.pop(aid, None)
                _COMPLETED_IDS.add(aid)
                print(f"  {_G}  ✓ +91 {ph10} (id={aid}){_RST}", flush=True)
                notify_tasks.append(_tg_cancel_notify(ph10, reason or "Отменён"))
            except Exception as exc:
                err = str(exc)
                result["failed"] += 1
                if "BAD_ACTION" in err:
                    _RENTALS.pop(aid, None)
                    _COMPLETED_IDS.add(aid)
                    print(f"  {_DIM}  · +91 {ph10} — уже не активен{_RST}", flush=True)
                else:
                    print(f"  {_Y}  ✗ +91 {ph10} — {err[:80]}{_RST}", flush=True)

        try:
            result["balance"] = await client.get_balance()
        except Exception:
            pass

        bal = result.get("balance")
        bal_s = f" · 💰 ${bal:.4f}" if bal is not None else ""
        if result["cancelled"]:
            print(
                f"  {_G}[Grizzly] Отменено {result['cancelled']}/{result['total']}{bal_s}{_RST}",
                flush=True,
            )
        elif result["total"]:
            print(
                f"  {_Y}[Grizzly] Не удалось отменить {result['failed']}/{result['total']}"
                f" (возможен лимит 1:30){bal_s}{_RST}",
                flush=True,
            )
        if notify_tasks:
            await asyncio.gather(*notify_tasks, return_exceptions=True)
    finally:
        try:
            await client.close()
        except Exception:
            pass
    return result


def cancel_all_active_rentals_blocking(reason: str = "", timeout: float = 45.0) -> dict:
    """Синхронная отмена всех активных номеров (старт/перезапуск/кнопка в UI)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(cancel_all_active_rentals(reason), timeout=timeout)
        )
    except asyncio.TimeoutError:
        return {"error": "timeout", "total": 0, "cancelled": 0, "failed": 0, "phones": []}
    except Exception as exc:
        return {"error": str(exc), "total": 0, "cancelled": 0, "failed": 0, "phones": []}
    finally:
        try:
            loop.close()
        except Exception:
            pass


def cancel_all_active_rentals_with_wait(
    reason: str = "выход",
    max_wait: float | None = None,
) -> dict:
    """Отменяет все активные номера, дожидаясь минимального лимита Grizzly при необходимости."""
    import time as _time

    if max_wait is None:
        max_wait = _grizzly_min_cancel_age() + 20.0

    def _count_active() -> int:
        async def _c() -> int:
            api_key = _get_grizzly_api_key()
            if not api_key or GrizzlySMSClient is None:
                return 0
            client = GrizzlySMSClient(api_key, http_timeout=10)
            try:
                acts = await client.get_active_activations()
                return len(acts or [])
            except Exception:
                return -1
            finally:
                try:
                    await client.close()
                except Exception:
                    pass

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_c())
        finally:
            try:
                loop.close()
            except Exception:
                pass

    deadline = _time.monotonic() + max_wait
    last: dict = {"cancelled": 0, "failed": 0, "total": 0, "phones": []}
    while _time.monotonic() < deadline:
        last = cancel_all_active_rentals_blocking(reason, timeout=40.0)
        if last.get("error") == "no_api_key":
            return last
        remaining = _count_active()
        if remaining <= 0:
            return last
        _time.sleep(min(10.0, max(2.0, deadline - _time.monotonic())))
    return last


def startup_cleanup_active_rentals(reason: str = "старт") -> dict:
    """Отмена «хвостов» перед запуском фонового монитора."""
    return cancel_all_active_rentals_blocking(reason)


async def _rental_monitor_loop():
    _otp_last_check: dict[str, float] = {}
    _api_scan_at: float = 0.0
    # Один persistent GrizzlySMSClient на весь цикл — переиспользует TCP-соединения
    # вместо создания нового клиента (и пула) на каждый poll. Пересоздаём только
    # при смене API-ключа. Отмена номеров (_cancel_rental_task) держит свой клиент,
    # чтобы не делить соединения с этим циклом.
    _mon_client = None
    _mon_key = None
    while True:
        try:
            now = time.monotonic()
            api_key = _get_grizzly_api_key()

            # Пересоздаём общий клиент при смене ключа / закрываем при его отсутствии
            if api_key and api_key != _mon_key:
                if _mon_client is not None:
                    try:
                        await _mon_client.close()
                    except Exception:
                        pass
                _mon_client = GrizzlySMSClient(api_key, http_timeout=10)
                _mon_key = api_key
            elif not api_key and _mon_client is not None:
                try:
                    await _mon_client.close()
                except Exception:
                    pass
                _mon_client = None
                _mon_key = None

            # 0. Пока в этом процессе есть свои аренды (идёт автоматизация),
            #    каждые 10 сек сканируем GrizzlySMS API на активные номера,
            #    которых нет в _RENTALS — страховка от потерянной регистрации.
            #    Без автоматизации API не опрашиваем: забытые с прошлого запуска
            #    номера убирает разовый startup_cleanup_active_rentals().
            if api_key and _RENTALS and now - _api_scan_at >= 10.0:
                _api_scan_at = now
                try:
                    _active_list = await _mon_client.get_active_activations()
                    for _item in (_active_list or []):
                        _aid = str(_item.get("activationId") or _item.get("id") or "")
                        _ph_raw = str(_item.get("phoneNumber") or _item.get("phone") or "")
                        if not _aid or _aid in _RENTALS or _aid in _COMPLETED_IDS:
                            continue
                        _ph10 = _ph_raw[-10:] if len(_ph_raw) >= 10 else _ph_raw
                        print(
                            f"\n  {_Y}[Фон] Найден незавершённый номер +91 {_ph10} (id={_aid}) — "
                            f"с прошлого запуска, новая покупка не выполнялась{_RST}"
                        )
                        _RENTALS[_aid] = {
                            "phone_10":      _ph10,
                            "rented_at":     time.monotonic(),
                            "status":        "active",
                            "profile_path":  None,
                            "login_url":     "https://www.flipkart.com/account/login?ret=/",
                            "months":        3,
                            "intercept_mode": False,
                            "phase1_ok":     False,
                            "otp_received":  False,
                            "cancel_attempts": 0,
                            "next_attempt_at": time.monotonic() + _grizzly_min_cancel_age(),
                            "cancelling":    False,
                            "external":      True,
                        }
                except Exception as _scan_ex:
                    _log_err("api_scan", f"сканирование активных номеров: {_scan_ex}")

            # 1. Проверяем активные номера на OTP каждые 10 сек
            if api_key:
                for aid, r in list(_RENTALS.items()):
                    if r["status"] != "active":
                        continue
                    last_check = _otp_last_check.get(aid, 0.0)
                    if now - last_check >= 10.0:
                        _otp_last_check[aid] = now
                        try:
                            st = await _mon_client.get_status(aid)
                            if st.get("type") == "OK" and st.get("code"):
                                r["otp_received"] = True
                                if r.get("intercept_mode") or r.get("external"):
                                    continue
                                otp = st["code"]
                                if aid not in _RENTALS:
                                    continue
                                print(f"\n  {_G}📲 OTP для +91 {r['phone_10']} получен — вход в фоне...{_RST}")
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
                        except Exception as _otp_ex:
                            _log_err(f"otp_{aid}",
                                     f"проверка OTP +91 {r.get('phone_10','?')}: {_otp_ex}")

            # 2. Истекают номера без OTP: не введённые на Flipkart — раньше
            lifetime = _number_lifetime_seconds()
            no_phase1_limit = _no_phase1_cancel_seconds()
            min_cancel = _grizzly_min_cancel_age()
            for aid, r in list(_RENTALS.items()):
                if r["status"] != "active" or r.get("otp_received"):
                    continue
                age = now - r["rented_at"]
                limit = lifetime if r.get("phase1_ok") else no_phase1_limit
                if age >= limit:
                    r["status"] = "failed"
                    r["next_attempt_at"] = max(now, r["rented_at"] + min_cancel)
                    if not r.get("phase1_ok"):
                        _cleanup_profile(r)
                        _log_err(
                            f"no_phase1_{aid}",
                            f"+91 {r.get('phone_10','?')}: не введён на Flipkart, OTP нет — отмена",
                        )

            # 3. Проверяем неудачные номера, готовые к отмене
            for aid, r in list(_RENTALS.items()):
                if r["status"] == "failed":
                    if now >= r["next_attempt_at"] and not r.get("cancelling"):
                        asyncio.create_task(_cancel_rental_task(aid))
        except Exception as _loop_ex:
            _log_err("monitor_loop", f"итерация монитора: {_loop_ex}")
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
                print(f"\n  {_G}✓ OTP для +91 {r['phone_10']} пришёл в последний момент. Вход...{_RST}")
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
        except Exception as _last_ex:
            _log_err(f"cancel_otp_{aid}",
                     f"проверка OTP перед отменой +91 {r.get('phone_10','?')}: {_last_ex}")

        try:
            await client.cancel(aid)
            _reason = (
                "Нет OTP (номер был на Flipkart)"
                if r.get("phase1_ok")
                else "Не введён на Flipkart, OTP не пришёл"
            )
            _transient_print(f"  {_G}[Фон] ✅ +91 {r['phone_10']} отменён{_RST}")
            r["status"] = "cancelled"
            _STATS["numbers_cancelled"] += 1
            _RENTALS.pop(aid, None)
            await _tg_cancel_notify(r["phone_10"], _reason)
        except Exception as ce:
            if "BAD_ACTION" in str(ce):
                _transient_print(f"  {_Y}[Фон] ⚠ +91 {r['phone_10']} — уже не существует{_RST}")
                _STATS["numbers_bad_action"] += 1
                _RENTALS.pop(aid, None)
            else:
                r["cancel_attempts"] += 1
                now = time.monotonic()
                if r["cancel_attempts"] == 1:
                    r["next_attempt_at"] = r["rented_at"] + _grizzly_min_cancel_age() + 10.0
                else:
                    r["next_attempt_at"] = now + 10.0
                _transient_print(f"  {_Y}[Фон] ↺ +91 {r['phone_10']} — повтор через 10 сек{_RST}")

        await client.close()
    except Exception as _ct_ex:
        # Возврат денег не прошёл из-за неожиданной ошибки — логируем (раньше
        # терялось как «Task exception was never retrieved»). Номер останется в
        # _RENTALS и будет повторно отменён следующей итерацией монитора.
        _log_err(f"cancel_task_{aid}",
                 f"отмена номера +91 {r.get('phone_10','?')}: {_ct_ex}")
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
            f"🔑 OTP: `***{otp_code[-2:]}`\n"
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
    """Deprecated: cookie export now runs in bg_login.py."""
    return None


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
    if str(activation_id) in _COMPLETED_IDS:
        return
    _BG_FUTURES[:] = [item for item in _BG_FUTURES if not item.done()]
    from bg_login import submit_bg_login
    fut = submit_bg_login(
        api_key, activation_id, otp_code, login_url, months, phone_10,
        _get_bg_loop(),
    )
    if fut is not None:
        _BG_FUTURES.append(fut)


def cleanup_all_rentals_on_exit():
    """Очистка при выходе/перезапуске: отменяем все номера без OTP (с ожиданием лимита Grizzly)."""
    import concurrent.futures

    try:
        cancel_all_active_rentals_with_wait("выход")
    except Exception:
        pass

    # Убиваем все Chrome-процессы бота
    kill_all_bot_chrome()

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

    # Разделяем: готовые к отмене прямо сейчас, и те что ещё нельзя (< 1:30)
    ready_ids   = [aid for aid in active_ids
                   if aid in _RENTALS and now >= _RENTALS[aid].get("next_attempt_at", 0)]
    pending_ids = [aid for aid in active_ids
                   if aid in _RENTALS and now < _RENTALS[aid].get("next_attempt_at", 0)]

    if pending_ids:
        phones = ", ".join(f"+91 {_RENTALS[aid]['phone_10']}" for aid in pending_ids if aid in _RENTALS)
        print(f"\n  {_Y}[Фон] {len(pending_ids)} номер(ов) < 1:30 — отмена в фоне: {phones}{_RST}")
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
                            print(f"  {_G}[Выход✓] Код обнаружен — вход в фоне{_RST}")
                            _submit_bg_login(
                                api_key, aid, _st["code"], r.get("login_url", ""),
                                r.get("months", 3), r["phone_10"],
                            )
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
    """Deprecated: background login now runs in bg_login.py."""
    return None
