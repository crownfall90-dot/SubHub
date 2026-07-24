"""
Interactive console menu for Login Automation
Запуск: python menu.py  (или двойной клик menu.bat)
"""

import asyncio
import contextlib
import os
os.makedirs("debug", exist_ok=True)
import random
import re
import sys
import json
import shutil
import time
import threading
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── ANSI цвета (работают в Windows 10/11 и любом терминале) ──────────────────

if os.name == "nt":
    try:
        import ctypes, ctypes.wintypes
        _k32 = ctypes.windll.kernel32
        _ENABLE_VT = 0x0004
        for _h in (-10, -11):  # stdin, stdout
            _hnd = _k32.GetStdHandle(ctypes.c_ulong(_h))
            _m   = ctypes.wintypes.DWORD()
            if _k32.GetConsoleMode(_hnd, ctypes.byref(_m)):
                _k32.SetConsoleMode(_hnd, _m.value | _ENABLE_VT)
        _k32.SetConsoleTitleW("SubHub")
    except Exception:
        os.system("")   # fallback

R   = "\033[91m"   # красный
G   = "\033[92m"   # зелёный
Y   = "\033[93m"   # жёлтый
C   = "\033[96m"   # голубой
M   = "\033[95m"   # фиолетовый
B   = "\033[94m"   # синий
W   = "\033[97m"   # белый
DIM = "\033[90m"   # серый
BLD = "\033[1m"    # жирный
RST = "\033[0m"    # сброс

try:
    from paths import ROOT as _HERE, PKG as _PKG  # type: ignore
except Exception:
    _PKG = Path(__file__).resolve().parent
    _HERE = _PKG.parent  # repo root (config/data/profiles)

PROFILES_DIR        = _HERE / "chrome_profiles"
DONE_PROFILES_DIR   = _HERE / "chrome_profiles_done"
USED_PROFILES_DIR   = _HERE / "chrome_profiles_used"
BACKUP_PROFILES_DIR = _HERE / "chrome_profiles_backup"
_VPN_PING_PROFILE_DIR = _HERE / "data" / "_vpn_ping_profile"
_OPEN_CHROME_LOCK = threading.Lock()
_OPEN_CHROME_SESSIONS: dict[str, threading.Thread] = {}
_AUTOMATION_LOG     = _HERE / "data" / "automation.log"


class _TeeWriter:
    """Дублирует запись в оригинальный stdout/stderr и в automation.log."""

    _ANSI_RE = None  # компилируется лениво

    def __init__(self, original, log_file_handle):
        self._orig = original
        self._fh = log_file_handle

    # ── Публичные методы io.TextIOBase ────────────────────────────────────────
    def write(self, s):
        if s:
            try:
                self._orig.write(s)
            except Exception:
                pass
            try:
                clean = self._strip_ansi(s)
                if clean:
                    self._fh.write(clean)
                    self._fh.flush()
            except Exception:
                pass
        return len(s) if s else 0

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass
        try:
            self._fh.flush()
        except Exception:
            pass

    # Пробрасываем остальные атрибуты в оригинал
    def __getattr__(self, name):
        return getattr(self._orig, name)

    # ── Утилита ──────────────────────────────────────────────────────────────
    @classmethod
    def _strip_ansi(cls, text: str) -> str:
        if cls._ANSI_RE is None:
            import re
            cls._ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
        return cls._ANSI_RE.sub("", text)


def _start_log_tee():
    """Включает дублирование stdout/stderr → automation.log (append)."""
    if isinstance(sys.stdout, _TeeWriter):
        return
    try:
        fh = open(_AUTOMATION_LOG, "a", encoding="utf-8", errors="replace")
        fh.write(f"\n{'='*60}\n")
        fh.write(f"  Запуск: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"{'='*60}\n\n")
        fh.flush()
        sys.stdout = _TeeWriter(sys.stdout, fh)
        sys.stderr = _TeeWriter(sys.stderr, fh)
    except Exception:
        pass  # если не удалось открыть файл — не ломаем работу

# ── Secrets (единственный источник API-ключей) ────────────────────────────────
_SECRETS: dict = {}


def _read_secrets() -> dict:
    """Читает secrets.yaml и кэширует в _SECRETS. Единственный источник API-ключей."""
    global _SECRETS
    if not _SECRETS:
        try:
            import yaml as _y
            _sp = _HERE / "secrets.yaml"
            if _sp.exists():
                with open(_sp, encoding="utf-8") as _f:
                    _SECRETS = _y.safe_load(_f) or {}
        except Exception:
            pass
    return _SECRETS


def _write_secret(section: str, key: str, value: str) -> None:
    """Сохраняет один ключ в secrets.yaml и сбрасывает кэш _SECRETS."""
    global _SECRETS
    import yaml as _y
    _sp = _HERE / "secrets.yaml"
    try:
        sec = _y.safe_load(_sp.read_text(encoding="utf-8")) or {} if _sp.exists() else {}
        sec.setdefault(section, {})[key] = value
        _tmp = _sp.with_suffix(".yaml.tmp")
        _tmp.write_text(_y.dump(sec, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")
        _tmp.replace(_sp)
        _SECRETS = {}  # сброс кэша, следующий _read_secrets() перечитает файл
    except Exception as _e:
        print(f"  {Y}⚠ Не удалось сохранить в secrets.yaml: {_e}{RST}")


def _get_telegram_token() -> str:
    """Возвращает токен Telegram исключительно из secrets.yaml."""
    try:
        tok = (_read_secrets().get("telegram") or {}).get("token", "").strip()
        return tok
    except Exception:
        return ""


def _tg_notify_enabled() -> bool:
    """Тумблер «Telegram-бот» в настройках SubHub (Уведомления).

    Выключает только рассылку уведомлений подписчикам; сам бот и ответы
    на команды продолжают работать.
    """
    try:
        raw = json.loads((_HERE / "data" / "app_settings.json").read_text(encoding="utf-8"))
        return bool(raw.get("notify_telegram", True))
    except Exception:
        return True


def _send_tg_activation(phone: str, act_url: str, short_url: str = "",
                        valid_till: str = "", login_str: str = "",
                        issued_str: str = "") -> None:
    """Отправляет ссылку активации YouTube Premium в Telegram (синхронно)."""
    if not _tg_notify_enabled():
        return
    import json as _j
    import urllib.request as _ur
    try:
        tg_token = _get_telegram_token()
        if not tg_token:
            return
        subs_path = _HERE / "data" / "tg_subscribers.json"
        chat_ids: list = []
        if subs_path.exists():
            d = _j.loads(subs_path.read_text(encoding="utf-8"))
            chat_ids = [int(c) for c in (d.get("chats", []) if isinstance(d, dict) else d)]
        if not chat_ids:
            return

        _has_short = short_url and short_url != act_url
        _link = short_url or act_url

        # Автоматически сохраняем ссылку и время её получения в профиль
        _recv_str = ""
        try:
            import time as _t_rl
            _pp = None
            for _cand in DONE_PROFILES_DIR.glob(f"profile_*{phone}*"):
                if _cand.is_dir():
                    _pp = _cand
                    break
            if _pp and _link:
                _now_ts = _t_rl.time()
                _save_meta_field(
                    _pp,
                    black_activation_link=act_url or _link,
                    black_short_link=short_url if _has_short else "",
                    link_received_ts=_now_ts,
                )
                _recv_str = _fmt_msk(_now_ts)
        except Exception:
            pass

        _disp = _disp_phone(phone)
        _dates_line = ""
        if login_str:
            _dates_line += f"\n📆 Создан:  <code>{login_str}</code>"
        if issued_str:
            _dates_line += f"\n📋 Выдан:   <code>{issued_str}</code>"
        if _recv_str:
            _dates_line += f"\n🕒 Ссылка получена:  <code>{_recv_str}</code>"
        _till_line = f"\n⏳ Действует до: <b>{valid_till}</b>" if valid_till else ""
        _url_lines = ""
        if act_url:
            _url_lines += f"\n🔗 <a href=\"{act_url}\">{act_url}</a>"
        if _has_short:
            _url_lines += f"\n🔗 {short_url}"
        if not act_url and short_url:
            _url_lines += f"\n🔗 {short_url}"
        if not _link:
            _url_lines = "\n⚠️ Ссылка активации не получена"
        msg = (
            f"🎉 <b>Activate Now — YouTube Premium</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Профиль: <code>{_disp}</code>"
            f"{_dates_line}"
            f"{_till_line}"
            f"{_url_lines}"
        )
        _kb_rows = [
            [{"text": "👤 Перейти в профиль",
              "callback_data": f"profile:menu:{phone}:active"}],
            [{"text": "📤 Отправить получателю",
              "callback_data": f"profile:send_to_buyer:{phone}:0"}],
        ]
        if not _link:
            _kb_rows.insert(0, [{"text": "🔍 Проверить активацию",
                                  "callback_data": f"profile:activate:{phone}"}])
        reply_markup = _j.dumps({"inline_keyboard": _kb_rows})

        for cid in chat_ids:
            try:
                payload = {
                    "chat_id": cid,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                    "reply_markup": reply_markup,
                }
                data = _j.dumps(payload).encode("utf-8")
                req = _ur.Request(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                _ur.urlopen(req, timeout=10)
                print(f"  TG [{cid}]: отправлено (+91 {phone})")
            except Exception as _te:
                print(f"  TG [{cid}]: {_te}")
    except Exception as _e:
        print(f"  TG отправка: {_e}")


def _send_tg_error(phone: str, error_text: str) -> None:
    """Отправляет уведомление об ошибке покупки в Telegram (синхронно)."""
    if not _tg_notify_enabled():
        return
    import json as _j
    import urllib.request as _ur
    try:
        tg_token = _get_telegram_token()
        if not tg_token:
            return
        subs_path = _HERE / "data" / "tg_subscribers.json"
        if not subs_path.exists():
            return
        _sd = _j.loads(subs_path.read_text(encoding="utf-8"))
        chat_ids = [int(c) for c in _sd.get("chats", [])]
        if not chat_ids:
            return
        _phone_line = f"📱 Профиль: <code>+91 {phone}</code>\n\n" if phone else ""
        msg = (
            f"⚠️ <b>Ошибка покупки</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"{_phone_line}"
            f"❌ {error_text}"
        )
        for cid in chat_ids:
            try:
                data = _j.dumps({
                    "chat_id": cid, "text": msg,
                    "parse_mode": "HTML", "disable_web_page_preview": True,
                }).encode("utf-8")
                req = _ur.Request(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    data=data, headers={"Content-Type": "application/json"},
                )
                _ur.urlopen(req, timeout=8)
            except Exception:
                pass
    except Exception:
        pass


_DATA               = _HERE / "data"
_DATA.mkdir(exist_ok=True)
TG_SUBSCRIBERS_FILE = _DATA / "tg_subscribers.json"
TG_STATS_FILE       = _DATA / "tg_stats.json"
CARDS_FILE          = _DATA / "cards.json"
GIFT_CARDS_FILE     = _DATA / "gift_cards.json"
GIFT_USED_FILE      = _DATA / "gift_cards_used.json"  # аудит использованных гифт-карт
# Способ оплаты для покупки: "card" (банковская карта) | "gift" (подарочные карты).
# Ставится из TG до начала покупки, читается платёжным потоком.
_pay_method = ["card"]
GIFT_DENOMS = (50, 100, 200, 250, 500, 1000)

# Код выхода процесса: 42 = menu.bat должен перезапуститься (применено обновление)
_exit_code = [0]
# True когда процесс завершается (os._exit) — pause() должна молчать
_shutting_down = False

MSK = timezone(timedelta(hours=3))

# ── Heartbeat + runtime sync (app / console / Telegram) ─────────────────────
_RUNTIME_STATE_FILE = _DATA / "runtime_state.json"
_runtime_lock = threading.Lock()


def _host_kind() -> str:
    # Десктоп-приложение удалено — единственный хост теперь консоль.
    return "console"


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _process_name_cmdline(pid: int) -> tuple[str, str]:
    """Имя exe + CommandLine процесса (Windows). Пустые строки при ошибке."""
    if not pid or pid <= 0:
        return "", ""
    if os.name != "nt":
        try:
            import pathlib
            name = pathlib.Path(f"/proc/{int(pid)}/exe").resolve().name
            cmd = pathlib.Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            return name, cmd
        except Exception:
            return "", ""
    try:
        # PowerShell быстрее/доступнее чем wmic на Win11
        r = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" "
                f"-ErrorAction SilentlyContinue; if($p){{$p.Name; $p.CommandLine}}",
            ],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return "", ""
        name = lines[0]
        cmd = lines[1] if len(lines) > 1 else ""
        return name, cmd
    except Exception:
        return "", ""


def _is_our_automation_pid(pid: int) -> bool:
    """True только если PID жив и это наш python main.py / menu.py full-cycle.

    Иначе PID reuse (браузер и т.п.) даёт ложное «Автоматизация запущена».
    """
    if not _pid_alive(pid):
        return False
    name, cmd = _process_name_cmdline(pid)
    name_l = (name or "").lower()
    cmd_l = (cmd or "").lower()
    if name_l and name_l not in ("python.exe", "pythonw.exe", "py.exe"):
        return False
    if cmd_l:
        if "main.py" in cmd_l:
            return True
        if "menu.py" in cmd_l and ("--full-cycle" in cmd_l or "--accounts" in cmd_l or "--stop-at-email" in cmd_l):
            return True
        # python жив, но это не наша автоматизация (например другой скрипт)
        return False
    # Нет cmdline — принимаем только свежий python-процесс (< 6 ч)
    if name_l in ("python.exe", "pythonw.exe", "py.exe"):
        started = float(_read_runtime_state().get("automation_started") or 0)
        return bool(started) and (time.time() - started) < 6 * 3600
    return False


def _read_runtime_state() -> dict:
    try:
        if _RUNTIME_STATE_FILE.exists():
            v = json.loads(_RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
            return v if isinstance(v, dict) else {}
    except Exception:
        pass
    return {}


def _write_runtime_state(data: dict) -> None:
    try:
        _atomic_write_text(_RUNTIME_STATE_FILE, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _patch_runtime_state(**kwargs) -> dict:
    with _runtime_lock:
        st = _read_runtime_state()
        st.update(kwargs)
        st["ts"] = time.time()
        _write_runtime_state(st)
        return st


def runtime_touch(event: str = "") -> None:
    """Сигнал для GUI: данные изменились (карты, гифт-карты, оплата и т.д.)."""
    _patch_runtime_state(last_event=event, last_event_ts=time.time())


def _read_host_heartbeat(host: str) -> dict:
    p = _DATA / f"heartbeat_{host}.json"
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def active_host() -> str:
    """Кто сейчас хост. После удаления GUI остаётся только консоль."""
    now = time.time()
    con_hb = _read_host_heartbeat("console")
    if now - float(con_hb.get("ts") or 0) < 90:
        return "console"
    return ""


def is_host_running() -> bool:
    return bool(active_host())


def restart_target_label() -> str:
    return "консоль"


def request_host_restart(source: str = "telegram") -> str:
    """Перезапускает консоль (menu.py)."""
    global _shutting_down
    host = active_host() or "console"
    _patch_runtime_state(restart_requested=True, restart_source=source, restart_host=host)
    _shutting_down = True
    with contextlib.suppress(Exception):
        grizzly_mod = globals().get("_grizzly_module")
        if grizzly_mod is not None:
            grizzly_mod.cleanup_all_rentals_on_exit()
    os._exit(42)
    return "console"  # unreachable


def set_automation_proc(pid: int, mode: str = "", owner: str = "") -> None:
    _patch_runtime_state(
        automation_pid=int(pid or 0),
        automation_mode=mode or "",
        automation_owner=owner or _host_kind(),
        automation_started=time.time() if pid else 0,
    )
    if pid:
        runtime_touch("automation_started")


def clear_automation_proc() -> None:
    _patch_runtime_state(
        automation_pid=0, automation_mode="", automation_owner="", automation_started=0,
    )
    runtime_touch("automation_finished")


def shared_automation_running() -> tuple[bool, dict]:
    st = _read_runtime_state()
    pid = int(st.get("automation_pid") or 0)
    if pid and _is_our_automation_pid(pid):
        return True, st
    if pid:
        clear_automation_proc()
        st = _read_runtime_state()
    return False, st


def _kill_automation_proc() -> bool:
    """Убивает дочерний процесс автоматизации (main.py) вместе с деревом.
    Возвращает True, если процесс был запущен и остановлен."""
    st = _read_runtime_state()
    pid = int(st.get("automation_pid") or 0)
    killed = False
    if pid and _is_our_automation_pid(pid):
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                )
            else:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
            killed = True
        except Exception:
            pass
    if pid:
        clear_automation_proc()
    return killed


def stop_all_automation(reason: str = "стоп") -> dict:
    """Останавливает ВСЁ: процесс автоматизации, Chrome бота и отменяет все
    активные номера GrizzlySMS через API (в т.ч. купленные другим/зависшим процессом)."""
    import grizzly as _gz
    out: dict = {"killed_proc": False, "chrome": 0, "cancel": {}}
    # 1. Процесс автоматизации — первым, чтобы он не покупал новые номера
    out["killed_proc"] = _kill_automation_proc()
    # 2. Chrome-процессы бота
    try:
        out["chrome"] = _gz.kill_all_bot_chrome()
    except Exception:
        pass
    # 3. Отмена всех активных номеров через GrizzlySMS API
    try:
        out["cancel"] = _gz.cancel_all_active_rentals_blocking(reason)
    except Exception as e:
        out["cancel"] = {"error": str(e)}
    return out


def _start_heartbeat():
    import threading as _th
    host = _host_kind()
    _hb_file = _DATA / f"heartbeat_{host}.json"
    _patch_runtime_state(host=host, host_pid=os.getpid())

    def _beat():
        while True:
            try:
                payload = {"ts": time.time(), "pid": os.getpid(), "host": host}
                _hb_file.write_text(json.dumps(payload), encoding="utf-8")
                _patch_runtime_state(host=host, host_pid=os.getpid())
            except Exception:
                pass
            time.sleep(20)

    t = _th.Thread(target=_beat, daemon=True, name=f"heartbeat-{host}")
    t.start()

_start_heartbeat()

# ── Git executable (Windows PATH может не включать git) ───────────────────────
def _find_git() -> str:
    """Всегда git.exe (не git.cmd — иначе вспыхивает cmd.exe)."""
    candidates = [
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        r"D:\Git\bin\git.exe",
        r"D:\Git\cmd\git.exe",
        r"E:\Git\bin\git.exe",
        r"E:\Git\cmd\git.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    found = shutil.which("git")
    if found:
        low = found.lower().replace("/", "\\")
        # which часто даёт …\cmd\git.exe или git.cmd — предпочитаем соседний bin\git.exe
        if low.endswith("\\cmd\\git.exe"):
            alt = found[:-len(r"\cmd\git.exe")] + r"\bin\git.exe"
            if Path(alt).exists():
                return alt
        if low.endswith("git.cmd"):
            alt = str(Path(found).with_name("git.exe"))
            bin_alt = str(Path(found).parent.parent / "bin" / "git.exe")
            if Path(bin_alt).exists():
                return bin_alt
            if Path(alt).exists():
                return alt
        return found
    return "git"

_GIT = _find_git()

# ── GitHub HTTP-обновление (работает без git, только stdlib) ──────────────────
_GH_OWNER = "crownfall90-dot"
_GH_REPO  = "SubHub"  # GitHub-репозиторий (OTA / raw master)

# Файлы, которые скачиваются при «Обновить» в SubHub (коллеги получают то же через git pull)
_UPDATE_FILES = [
    "README.md",
    "menu.bat",
    "requirements.txt",
    ".gitignore",
    "config.yaml.example",
    "secrets.yaml.example",
    "VERSION",
    "subhub/__init__.py",
    "subhub/__main__.py",
    "subhub/paths.py",
    "subhub/menu.py",
    "subhub/bot.py",
    "subhub/main.py",
    "subhub/grizzly_sms.py",
    "subhub/pvapins_sms.py",
    "subhub/sms_failover.py",
    "subhub/proxy.py",
    "subhub/grizzly.py",
    "subhub/bg_login.py",
    "subhub/deepseek.py",
    "subhub/winproc.py",
    "subhub/ggsell/__init__.py",
    "subhub/ggsell/bot_ggsell.py",
    "subhub/ggsell/client.py",
    "subhub/ggsell/monitor.py",
    "subhub/ggsell/deepseek_orders.py",
]

def _parse_git_remote() -> tuple[str, str, str]:
    """Читает .git/config → (owner, repo, token). Fallback: secrets.yaml → константы."""
    try:
        import re
        cfg = (_HERE / ".git" / "config").read_text(encoding="utf-8", errors="replace")
        for line in cfg.splitlines():
            m = re.search(r'url\s*=\s*https://(?:([^@\s]+)@)?github\.com/([^/\s]+)/([^/\s.]+)', line)
            if m:
                return m.group(2), m.group(3).replace(".git", ""), m.group(1) or ""
    except Exception:
        pass
    # Читаем токен из secrets.yaml (github.token) — для ZIP-установок
    try:
        import yaml as _y
        _sec = _y.safe_load(
            (_HERE / "secrets.yaml").read_text(encoding="utf-8")
        ) or {}
        _tok = (_sec.get("github") or {}).get("token", "")
    except Exception:
        _tok = ""
    return _GH_OWNER, _GH_REPO, _tok

def _gh_get(url: str, token: str = "") -> bytes:
    """GET-запрос к GitHub через urllib (без прокси)."""
    import urllib.request
    hdrs = {"User-Agent": "flipkart-updater", "Accept": "application/vnd.github.v3+json"}
    if token:
        hdrs["Authorization"] = f"token {token}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, headers=hdrs), timeout=15) as r:
        return r.read()

def _http_check_updates() -> list[str]:
    """Проверяет новые коммиты через GitHub API — без git."""
    try:
        import json as _j
        owner, repo, token = _parse_git_remote()
        if not owner:
            return []
        _here = _HERE
        _sha_f = _here / "._update_sha"

        # Читаем локальный SHA: .git → ._update_sha
        local_sha = ""
        head_f = _here / ".git" / "refs" / "heads" / "master"
        if head_f.exists():
            local_sha = head_f.read_text().strip()
        else:
            packed = _here / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text().splitlines():
                    if "refs/heads/master" in line and not line.startswith("#"):
                        local_sha = line.split()[0]; break
        if not local_sha and _sha_f.exists():
            local_sha = _sha_f.read_text().strip()

        commits = _j.loads(_gh_get(
            f"https://api.github.com/repos/{owner}/{repo}/commits?sha=master&per_page=20",
            token))

        if not local_sha:
            # Первый запуск — бутстрап: сохраняем текущий HEAD
            if commits:
                _sha_f.write_text(commits[0]["sha"])
            return []

        result = []
        for c in commits:
            sha = c["sha"]
            if sha[:7] == local_sha[:7] or local_sha[:7] == sha[:7]:
                break
            msg = c["commit"]["message"].split("\n")[0][:70]
            result.append(f"{sha[:7]} {msg}")
        return result
    except Exception:
        return []

def _http_do_update() -> tuple[bool, str]:
    """Скачивает файлы с GitHub и заменяет локальные — без git."""
    global _update_available, _update_commits
    try:
        import json as _j
        owner, repo, token = _parse_git_remote()
        if not owner:
            return False, "Не удалось прочитать репозиторий из .git/config"
        _here = _HERE
        _FILES = list(_UPDATE_FILES)
        updated = []
        failed = []
        for fname in _FILES:
            try:
                url  = f"https://raw.githubusercontent.com/{owner}/{repo}/master/{fname}"
                data = _gh_get(url, token)
                tgt  = _here / fname
                tgt.parent.mkdir(parents=True, exist_ok=True)
                # .bat требует CRLF на Windows — нормализуем при сохранении
                save = (data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                        if fname.endswith(".bat") else data)
                if tgt.exists() and tgt.read_bytes() == save:
                    continue
                _atomic_write_bytes(tgt, save)
                updated.append(fname)
            except Exception as exc:
                failed.append(f"{fname}: {exc}")
        if failed:
            return False, "Не удалось обновить файлы:\n" + "\n".join(failed)
        # Обновляем локальный SHA чтобы следующий check показал 0 коммитов
        try:
            ref  = _j.loads(_gh_get(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/master", token))
            sha  = ref["object"]["sha"]
            # Не подменяем refs git вручную: HTTP OTA ведёт собственный SHA.
            _atomic_write_text(_here / "._update_sha", sha)
        except Exception:
            pass
        _update_available = False
        _update_commits   = []
        try:
            _bot_module._update_available = False
            _bot_module._update_commits   = []
        except Exception:
            pass
        _init_secrets()
        _migrate_config()
        return True, ("Обновлены: " + ", ".join(updated)) if updated else "Версия уже актуальна"
    except Exception as e:
        return False, str(e)

# ── Импорты из выделенных модулей ────────────────────────────────────────────
from proxy import _phone_from_path

import grizzly as _grizzly_module
from grizzly import (
    _get_bg_loop, _submit_bg_cancel, _submit_bg_login,
)

import bot as _bot_module
from bot import _tg_status_line, _menu_tg_bot_thread, ensure_tg_bot

# module-level fallback values (перезаписываются _check_updates_bg при проверке)
_update_available: bool = False
_update_commits:   list = []
_update_checked:   bool = False
_update_checked_at: float = 0.0


# ── Утилиты ───────────────────────────────────────────────────────────────────

def cls():
    os.system("cls" if os.name == "nt" else "clear")


def pause(msg: str = "  Нажмите Enter для продолжения..."):
    if _shutting_down:
        return
    try:
        input(f"\n{DIM}{msg}{RST}")
    except (KeyboardInterrupt, EOFError):
        pass


def run(
    cmd: list[str], *, hidden: bool | None = None, timeout: float | None = 300,
) -> int:
    """Запускает команду. Без консольного окна при запуске под pythonw."""
    import winproc
    kw: dict = {}
    use_hidden = winproc.is_gui_host() if hidden is None else hidden
    if use_hidden:
        kw = winproc.hidden_kwargs(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    proc = subprocess.run(cmd, timeout=timeout, **kw)
    return proc.returncode


def _atomic_write_text(path, text: str) -> None:
    """Атомарная запись текста: пишем во временный файл рядом, fsync и заменяем
    целевой через os.replace. Защищает от обрезанного/битого файла, если процесс
    убьют во время записи (например os._exit(42) при перезапуске консоли)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as _f:
        _f.write(text)
        _f.flush()
        os.fsync(_f.fileno())
    os.replace(tmp, path)


def _atomic_write_bytes(path, data: bytes) -> None:
    """Атомарная запись бинарного OTA-файла рядом с целевым."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)


# ── Глобальная сериализация покупки/оплаты ───────────────────────────────────
# Покупка/оплата трогают общие синглтоны (_purchase_cancel, _switch_card_choice,
# _orders_confirm_choice, _3ds_card_options). Реальное выполнение _do_* идёт в
# worker-потоках (asyncio.run в run_in_executor) из РАЗНЫХ источников: ручной
# TG-бот, GGSell-бот, консоль. У каждого свой asyncio.Lock, но они в разных
# event-loop'ах и между собой не координируются. Этот RLock — единая точка
# сериализации на уровне потоков: пока один _do_* идёт, остальные ждут.
# RLock (а не Lock) — чтобы рекурсия (_do_fill_address/_do_buy_membership зовут
# себя при ретраях) и вложенность (_do_all_in_one → _do_buy_membership) в ОДНОМ
# потоке не давали дедлок; другой поток всё равно блокируется до полного выхода.
_PURCHASE_LOCK = threading.RLock()


def _serialize_purchase(_fn):
    """Декоратор: выполняет async-функцию _do_* под _PURCHASE_LOCK и регистрирует
    профиль в реестре активных покупок (для мгновенной остановки кнопкой)."""
    async def _wrap(*a, **kw):
        _PURCHASE_LOCK.acquire()
        _pp = a[0] if a else None
        _register_purchase_profile(_pp)
        try:
            return await _fn(*a, **kw)
        finally:
            _unregister_purchase_profile(_pp)
            _PURCHASE_LOCK.release()
    _wrap.__name__ = getattr(_fn, "__name__", "_wrap")
    _wrap.__doc__ = getattr(_fn, "__doc__", None)
    return _wrap


def header(title: str = "LOGIN AUTOMATION  ──  PROFILE MANAGER", color: str = C):
    print()
    W_ = 54
    line = "═" * W_
    pad = (W_ - len(title)) // 2
    print(f"{color}{BLD}  ╔{line}╗{RST}")
    print(f"{color}{BLD}  ║{' ' * pad}{title}{' ' * (W_ - pad - len(title))}║{RST}")
    print(f"{color}{BLD}  ╚{line}╝{RST}")
    print()


def section(title: str, color: str = DIM):
    print(f"\n{color}  ┌─ {title} {'─' * max(0, 44 - len(title))}┐{RST}")


def opt(key: str, label: str, color: str = W):
    print(f"  {BLD}{Y}[{key}]{RST}  {color}{label}{RST}")


def _fmt_msk(ts: float) -> str:
    """Unix timestamp → строка даты-времени по московскому времени (UTC+3)."""
    return datetime.fromtimestamp(ts, tz=MSK).strftime("%d.%m.%Y  %H:%M  МСК")


def _disp_phone(username: str) -> str:
    """Форматирует номер для вывода: +91 XXXXXXXXXX (без кода страны в самом номере)."""
    u = str(username).strip()
    if len(u) == 12 and u.startswith("91") and u.isdigit():
        return f"+91 {u[2:]}"
    return f"+91 {u}"


def _ask_delete_profile_console(profile_path, username: str, error_text: str) -> bool:
    """Печатает текст ошибки и спрашивает подтверждение на удаление СОХРАНённого
    профиля (Д/Н). Удаляет только при «Д». Возвращает True если удалён."""
    if error_text:
        print(f"\n  {R}✘ {error_text}{RST}")
    try:
        ans = input(f"  {BLD}Удалить профиль {_disp_phone(username)}? [Д/Н]: {RST}").strip().upper()
    except (EOFError, KeyboardInterrupt):
        ans = "Н"
    if ans in ("Д", "ДА", "Y", "YES"):
        import shutil as _shd
        _shd.rmtree(str(profile_path), ignore_errors=True)
        print(f"  {Y}🗑 Профиль {_disp_phone(username)} удалён.{RST}")
        return True
    print(f"  {G}Профиль оставлен.{RST}")
    return False


def _cookies_backup_for_phone(phone: str) -> Path | None:
    """Путь к cookies_backup/cookies_*.json по 10-значному номеру."""
    phone10 = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
    if not phone10:
        return None
    bk_dir = Path("cookies_backup")
    if not bk_dir.is_dir():
        return None
    direct = bk_dir / f"cookies_{phone10}.json"
    if direct.exists():
        return direct
    for p in sorted(bk_dir.glob(f"cookies_*{phone10}.json")):
        if p.is_file():
            return p
    return None


async def _auto_restore_flipkart_session(ctx, page, profile_path: Path) -> bool:
    """Сразу восстановить сессию из cookies_backup в уже открытом Chrome.
    Без вопросов — если бэкап есть, применяем и проверяем вход."""
    import json as _jc

    phone = _phone_from_path(profile_path) or ""
    bk = _cookies_backup_for_phone(phone)
    if not bk:
        print(f"  {Y}🔒 не залогинен — нет cookies_backup для …{phone[-10:] if phone else '?'}{RST}")
        return False
    print(f"  {Y}🔒 не залогинен — сразу восстанавливаю из {bk.name}…{RST}")
    try:
        raw = _jc.loads(bk.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  {R}❌ не прочитал куки: {e}{RST}")
        return False
    if not isinstance(raw, list) or not raw:
        print(f"  {R}❌ JSON куков пустой{RST}")
        return False
    sam_map = {
        "no_restriction": "None", "lax": "Lax", "strict": "Strict",
        "Lax": "Lax", "Strict": "Strict", "None": "None",
    }
    pw_cookies = []
    for c in raw:
        if not c.get("name") or "value" not in c:
            continue
        sam = c.get("sameSite") or c.get("same_site") or "no_restriction"
        exp = c.get("expirationDate") or c.get("expires") or -1
        pw_c = {
            "name": c["name"], "value": c["value"],
            "domain": c.get("domain", ".flipkart.com"),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": sam_map.get(str(sam), "None"),
        }
        if exp and float(exp) > 0:
            pw_c["expires"] = int(float(exp))
        pw_cookies.append(pw_c)
    if not pw_cookies:
        return False
    try:
        with contextlib.suppress(Exception):
            await page.goto(
                "https://www.flipkart.com/",
                wait_until="domcontentloaded", timeout=25_000,
            )
        await ctx.add_cookies(pw_cookies)
        await page.reload(wait_until="domcontentloaded", timeout=25_000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=8_000)
        await page.wait_for_timeout(1500)
    except Exception as e:
        print(f"  {R}❌ restore inject: {e}{RST}")
        return False
    if await _page_logged_out(page):
        print(f"  {R}❌ куки не дали входа (устарели?){RST}")
        return False
    print(f"  {G}✔ сессия восстановлена из куков — продолжаю{RST}")
    return True


def _console_offer_restore(profile_path, username: str) -> bool:
    """Консоль: при not_logged_in сразу восстанавливает из бэкапа куков (без вопроса)."""
    phone = "".join(ch for ch in str(username) if ch.isdigit())[-10:] or str(username)
    bk = _cookies_backup_for_phone(phone)
    print(f"\n  {Y}🔒 Профиль не залогинен — вход слетел.{RST}")
    if not bk:
        print(f"  {DIM}Бэкапа куков нет (cookies_backup/cookies_{phone}.json).{RST}")
        print(f"  {DIM}Нужны свежие куки — пункт «К» в главном меню.{RST}")
        return False
    print(f"  {DIM}Сразу восстанавливаю сессию из {bk.name}…{RST}")
    try:
        ok, msg = asyncio.run(_restore_profile_from_cookies(bk, phone, Path(profile_path)))
    except Exception as e:
        ok, msg = False, str(e)
    if ok:
        print(f"  {G}✅ Сессия восстановлена. Запустите операцию снова.{RST}")
        return True
    print(f"  {R}❌ Куки не дали входа: {msg}. Нужны свежие куки (пункт «К»).{RST}")
    return False


def _profile_login_check_flow(selected: dict, *, quiet_ok: bool = False) -> bool:
    """Проверяет вход профиля на Flipkart. Если сессия слетела — предлагает
    восстановить из куков; если восстановить не удалось — предлагает удалить
    профиль или перенести в архив.

    quiet_ok=True — при активной сессии не делать pause() (для пакетной проверки).
    Возвращает True, если профиль был удалён или перенесён в архив (вызвавшему
    нужно обновить список и выйти из подменю).
    """
    path = Path(selected["path"])
    username = str(selected.get("username", ""))
    print(f"\n  {DIM}Проверяю сессию +91 {_disp_phone(username)} на Flipkart (headless)…{RST}")
    # Профиль может быть открыт в Chrome — тогда папка занята
    _kill_chrome_for_profile(path)
    try:
        logged = asyncio.run(_flipkart_is_logged_in(path))
    except Exception as e:
        print(f"  {R}Ошибка проверки: {e}{RST}")
        pause()
        return False

    if logged:
        print(f"  {G}✅ Залогинен — сессия активна.{RST}")
        if not quiet_ok:
            pause()
        return False

    print(f"  {Y}🔒 НЕ залогинен — вход слетел.{RST}")

    # ── 1. Пытаемся восстановить из бэкапа куков ────────────────────────────
    phone = "".join(ch for ch in username if ch.isdigit())[-10:] or username
    bk = _cookies_backup_for_phone(phone)
    if bk:
        print(f"  {DIM}Пробую восстановить из {bk.name}…{RST}")
        try:
            ok, msg = asyncio.run(_restore_profile_from_cookies(bk, phone, path))
        except Exception as e:
            ok, msg = False, str(e)
        if ok:
            _kill_chrome_for_profile(path)
            try:
                really = asyncio.run(_flipkart_is_logged_in(path))
            except Exception:
                really = True  # при ошибке считаем восстановленным, не удаляем
            if really:
                print(f"  {G}✅ Сессия восстановлена — профиль снова залогинен.{RST}")
                pause()
                return False
            print(f"  {Y}Куки применились, но вход не подтвердился "
                  f"(сессия, вероятно, мертва на сервере).{RST}")
        else:
            print(f"  {R}❌ Восстановить не удалось: {msg}{RST}")
    else:
        print(f"  {DIM}Бэкапа куков нет (cookies_backup/cookies_{phone}.json).{RST}")

    # ── 2. Восстановить не вышло → удалить или в архив ──────────────────────
    print()
    print(f"  {Y}Восстановить вход не удалось. Что сделать с профилем?{RST}")
    opt("А", "Перенести в архив  (сохранить запись, удалить папку)", M)
    opt("У", "Удалить профиль навсегда", R)
    opt("0", "Оставить как есть", DIM)
    ans = input(f"\n  {BLD}Выбор [А/У/0]: {RST}").strip().upper()

    if ans in ("А", "A"):
        ok_arch = _archive_profile(path, used_ts=time.time())
        if ok_arch:
            print(f"\n  {M}✅ Перенесён в архив, папка удалена.{RST}")
        else:
            print(f"\n  {R}Ошибка архивирования.{RST}")
        pause()
        return bool(ok_arch)

    if ans in ("У", "U"):
        confirm = input(f"  {R}Точно удалить безвозвратно? [Д/Н]: {RST}").strip().lower()
        if confirm in ("д", "y"):
            _kill_chrome_for_profile(path)
            try:
                shutil.rmtree(str(path), ignore_errors=True)
            except Exception as e:
                print(f"  {R}Ошибка удаления: {e}{RST}")
                pause()
                return False
            if not path.exists():
                print(f"\n  {M}🗑 Профиль удалён.{RST}")
                pause()
                return True
            print(f"\n  {R}Не удалось удалить (файлы заняты Chrome).{RST}")
        else:
            print(f"\n  {DIM}Удаление отменено.{RST}")
        pause()
        return False

    print(f"\n  {DIM}Оставлено без изменений.{RST}")
    pause()
    return False


def _read_profile_meta(p: Path) -> dict:
    """Читает .profile_meta.json профиля и возвращает обогащённый dict."""
    _raw = p.name[len("profile_"):] if p.name.startswith("profile_") else p.name
    info: dict = {
        "path":        p,
        "username":    _raw,
        "login_ts":    None,
        "login_str":   "нет данных",
        "issued_ts":   None,
        "issued_str":  None,
        "used_ts":     None,
        "used_str":    None,
        "otp_code":    None,
        "site_url":    None,
        "subscription_months":     None,
        "subscription_bought_ts":  None,
        "subscription_bought_str": None,
        "subscription_expires_ts": None,
        "subscription_expires_str": None,
        "black_valid_till":        None,
    }
    meta_file = p / ".profile_meta.json"
    if not meta_file.exists():
        info["login_str"] = "нет мета-файла (профиль не прошёл вход)"
        return info
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        info.update(data)
        info["username"] = data.get("username", _raw)
        info["otp_code"] = data.get("otp_code")
        info["site_url"] = data.get("site_url")
        if "login_ts" in data and data["login_ts"] is not None:
            info["login_ts"]  = float(data["login_ts"])
            info["login_str"] = _fmt_msk(info["login_ts"])
        else:
            info["login_str"] = "нет даты входа (мета есть, login_ts отсутствует)"
        if "issued_ts" in data:
            info["issued_ts"]  = float(data["issued_ts"])
            info["issued_str"] = _fmt_msk(info["issued_ts"])
        if "used_ts" in data:
            info["used_ts"]  = float(data["used_ts"])
            info["used_str"] = _fmt_msk(info["used_ts"])
        if "subscription_months" in data:
            info["subscription_months"] = int(data["subscription_months"])
        if "subscription_bought_ts" in data:
            info["subscription_bought_ts"]  = float(data["subscription_bought_ts"])
            info["subscription_bought_str"] = _fmt_msk(info["subscription_bought_ts"])
        if "subscription_expires_ts" in data:
            info["subscription_expires_ts"]  = float(data["subscription_expires_ts"])
            info["subscription_expires_str"] = _fmt_msk(info["subscription_expires_ts"])
    except Exception as _me:
        info["login_str"] = f"ошибка мета-файла: {_me}"
    return info


def get_profiles() -> list[dict]:
    """Возвращает список профилей с метаданными. done=True — успешный вход."""
    profiles = []

    def _read_dir(directory: Path, done: bool) -> None:
        if not directory.exists():
            return
        for p in sorted(directory.glob("profile_*")):
            meta_file = p / ".profile_meta.json"
            username = p.name
            age_str  = "нет данных"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    username = data.get("username", p.name)
                    age_h = (time.time() - data["login_ts"]) / 3600
                    age_str = f"{age_h:.1f}ч назад"
                except Exception:
                    pass
            profiles.append({"path": p, "username": username, "age": age_str, "done": done})

    _read_dir(DONE_PROFILES_DIR, done=True)   # успешные — первыми
    _read_dir(PROFILES_DIR, done=False)
    return profiles


_DONE_PROFILES_CACHE: tuple[float, list] = (0.0, [])


def _invalidate_done_profiles_cache() -> None:
    global _DONE_PROFILES_CACHE
    _DONE_PROFILES_CACHE = (0.0, [])


def _load_done_profiles(*, force: bool = False) -> list[dict]:
    """Возвращает список профилей из DONE_PROFILES_DIR у которых есть .profile_meta.json."""
    global _DONE_PROFILES_CACHE
    import time as _t
    now = _t.monotonic()
    ts, cached = _DONE_PROFILES_CACHE
    if not force and cached and now - ts < 2.5:
        return cached
    profiles = []
    if not DONE_PROFILES_DIR.exists():
        return profiles
    _now = _t.time()
    for p in sorted(DONE_PROFILES_DIR.glob("profile_*")):
        if not p.is_dir():
            continue
        meta_file = p / ".profile_meta.json"
        if meta_file.exists():
            profiles.append(_read_profile_meta(p))
        else:
            # Удаляем неполные профили (без мета), которые старше 2 часов —
            # активные сессии успеют получить мета за это время
            try:
                age = _now - p.stat().st_mtime
                if age > 7_200:
                    import shutil as _sh
                    _sh.rmtree(p, ignore_errors=True)
            except Exception:
                pass
    profiles.sort(key=lambda x: x["login_ts"] or 0, reverse=True)
    _DONE_PROFILES_CACHE = (now, profiles)
    return profiles


def _profile_url(profile_path: Path) -> str:
    """
    URL для «Открыть Chrome»:
    профиль с сессией → страница Black Membership (3 или 12 мес.);
    без сессии → страница входа из config.yaml.
    """
    meta_file = profile_path / ".profile_meta.json"
    if meta_file.exists():
        months = 3
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            m = int(meta.get("subscription_months") or meta.get("months") or 3)
            months = m if m in _BLACK_URLS else 3
        except Exception:
            pass
        return _BLACK_URLS[months]

    login_url = "https://www.flipkart.com/account/login?ret=/"
    config_path = _HERE / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            login_url = cfg.get("site", {}).get("url", login_url)
        except ImportError:
            # yaml ещё не установлен — ищем URL текстом
            text = config_path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "url:" in line and "flipkart" in line:
                    login_url = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass
    return login_url


def _find_chrome() -> str | None:
    """Возвращает путь к chrome.exe или None если не найден."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(
            os.path.join(local_appdata, r"Google\Chrome\Application\chrome.exe")
        )
    return next((c for c in candidates if Path(c).exists()), None)


def _bundled_chromium_path() -> str | None:
    """Путь к встроенному Chromium Playwright (не Google Chrome 137+)."""
    cached = getattr(_bundled_chromium_path, "_cached", None)
    if cached and Path(cached).exists():
        return cached
    ep: str | None = None
    try:
        base = Path.home() / "AppData" / "Local" / "ms-playwright"
        for pattern in ("chromium-*/chrome-win64/chrome.exe", "chromium-*/chrome-win/chrome.exe"):
            hits = sorted(base.glob(pattern), reverse=True)
            if hits and hits[0].exists():
                ep = str(hits[0])
                break
    except Exception:
        pass
    if not ep:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ep = p.chromium.executable_path
        except Exception:
            ep = None
    if ep and Path(ep).exists():
        _bundled_chromium_path._cached = ep  # type: ignore[attr-defined]
        return ep
    return None


def _chrome_executable_for_profile(
    profile_path: Path | str | None = None, *, force_bundled: bool = False,
) -> str | None:
    """Playwright Chromium + --load-extension для VeepN; системный Chrome — без VPN."""
    if force_bundled or _vpn_extension_dir():
        return _bundled_chromium_path() or _find_chrome()
    chrome = _find_chrome()
    if chrome:
        return chrome
    return _bundled_chromium_path()


def _browser_label(exe: str | None) -> str:
    if not exe:
        return "Chromium"
    low = exe.lower()
    if "ms-playwright" in low or "chromium" in Path(exe).name.lower():
        return "Playwright Chromium"
    if "chrome.exe" in low:
        return "Google Chrome"
    return Path(exe).name


def _connect_vpn_over_cdp(port: int, target_url: str | None = None) -> bool:
    """Подключается к уже запущенному (subprocess) браузеру по CDP, включает VPN,
    затем при необходимости открывает target_url. Браузер остаётся открытым."""
    async def _run() -> bool:
        from playwright.async_api import async_playwright as _ap
        async with _ap() as pw:
            br = None
            for _ in range(30):
                try:
                    br = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            if not br or not br.contexts:
                return False
            ctx = br.contexts[0]
            ok = await _ensure_vpn_connected(ctx)
            if ok and target_url:
                try:
                    await _close_junk_tabs(ctx)
                    page = await _main_work_page(ctx)
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    pass
            # НЕ вызываем br.close() — иначе закроется весь браузер.
            return ok
    try:
        return asyncio.run(_run())
    except Exception:
        return False


def open_chrome(profile_path: Path, url: str | None = None) -> bool:
    """Один видимый Chrome: VPN → Flipkart. Окно остаётся открытым до закрытия пользователем.

    url — необязательный старт (например страница товара 3 мес.); иначе _profile_url.
    """
    profile_path = Path(profile_path)
    key = str(profile_path.resolve())
    with _OPEN_CHROME_LOCK:
        t = _OPEN_CHROME_SESSIONS.get(key)
        if t and t.is_alive():
            print(f"  {Y}Chrome уже открыт/открывается для этого профиля{RST}")
            return True

    if not _chrome_executable_for_profile(profile_path):
        print(f"\n{R}  Chrome не найден. Запустите вручную:{RST}")
        print(f"  chrome.exe --user-data-dir=\"{profile_path.resolve()}\"")
        return False

    def _worker() -> None:
        try:
            asyncio.run(_open_chrome_keep_alive(profile_path, url=url))
        except Exception as exc:
            print(f"  {R}Chrome: {exc}{RST}")
        finally:
            with _OPEN_CHROME_LOCK:
                _OPEN_CHROME_SESSIONS.pop(key, None)

    t = threading.Thread(
        target=_worker, daemon=True,
        name=f"chrome-{_phone_from_path(profile_path) or profile_path.name}",
    )
    with _OPEN_CHROME_LOCK:
        _OPEN_CHROME_SESSIONS[key] = t
    t.start()
    return True


async def _open_chrome_keep_alive(profile_path: Path, url: str | None = None) -> None:
    """Запускает профиль, открывает Flipkart; держит сессию до закрытия окна.

    proxy.enabled → через прокси; выкл → VPN на ПК / напрямую (расширение не включаем).
    """
    profile_path = Path(profile_path)
    tag = _phone_from_path(profile_path) or profile_path.name
    target = (url or "").strip() or _profile_url(profile_path)
    if _is_profile_locked(profile_path):
        _clear_stale_profile_locks(profile_path)

    # Ручной Chrome: прокси если включён; VeepN не поднимаем (только при автоматизации)
    set_profile_op_stage(profile_path, "Открытие Chrome")
    _, proxy, _ = await _resolve_profile_scenario_network(
        profile_path, allow_vpn_extension=False,
    )
    if not proxy and _vpn_extension_dir():
        if not _profile_has_vpn_extension(profile_path):
            if not _install_extension_filesystem(profile_path):
                print(f"  {Y}⚠ VPN-расширение не установлено в профиль{RST}")
        await _vpn_chrome_cooldown(extra=0.5)

    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    ctx = None
    try:
        _pre_inject_chrome_prefs(profile_path)
        launch_kw = _browser_launch_kw(
            phone=tag, profile_path=profile_path,
            use_vpn=False if proxy else None, proxy=proxy,
        )
        exe = launch_kw.get("executable_path")
        print(f"  {DIM}Браузер: {_browser_label(exe)}{RST}")
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            **launch_kw,
        )
        _mark_browser_network(ctx, use_vpn=False, proxy=proxy)
        await asyncio.sleep(2.0)
        await _close_extension_startup_tabs(ctx)
        await _block_vpn_junk_routes(ctx)

        with contextlib.suppress(Exception):
            await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")

        page = await _main_work_page(ctx)
        try:
            await _maximize_window(ctx, page)
        except Exception:
            pass

        if proxy:
            print(f"  {DIM}Открываю Flipkart (+91 {tag}) через прокси → {target[:80]}…{RST}")
        elif _vpn_extension_dir():
            print(f"  {DIM}Flipkart (+91 {tag}) → {target[:80]}…{RST}")
            print(f"  {DIM}VPN не включается — только во время автоматизации{RST}")
            with contextlib.suppress(Exception):
                await _vpn_disconnect(ctx)
        else:
            print(f"  {DIM}Открываю Flipkart (+91 {tag}) → {target[:80]}…{RST}")

        _stealth = _build_stealth_js_m()
        if _stealth:
            await ctx.add_init_script(_stealth)

        ok, page = await _open_flipkart_page(ctx, target, label=tag, work_page=page)
        if not ok:
            with contextlib.suppress(Exception):
                fresh = await ctx.new_page()
                await fresh.bring_to_front()
                ok, page = await _open_flipkart_page(ctx, target, label=tag, work_page=fresh)

        if ok:
            print(f"  {G}✔ Flipkart открыт (+91 {tag}){RST}")
            try:
                page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)
                await _maximize_window(ctx, page)
                await page.bring_to_front()
            except Exception:
                pass
        elif not ok:
            print(f"  {Y}⚠ Откройте Flipkart вручную: {target}{RST}")

        while ctx:
            try:
                if not ctx.pages:
                    break
                alive = False
                for p in ctx.pages:
                    try:
                        _ = p.url
                        alive = True
                        break
                    except Exception:
                        pass
                if not alive:
                    break
            except Exception:
                break
            await asyncio.sleep(0.5)
    finally:
        set_profile_op_stage(profile_path, "")
        if ctx:
            with contextlib.suppress(Exception):
                await _vpn_disconnect(ctx)
            try:
                await ctx.close()
            finally:
                _note_chromium_closed()
        try:
            await pw.stop()
        except Exception:
            pass


async def _maximize_window(ctx, page) -> None:
    """Максимизирует окно браузера через CDP."""
    try:
        cdp = await ctx.new_cdp_session(page)
        winfo = await cdp.send("Browser.getWindowForTarget")
        await cdp.send("Browser.setWindowBounds", {
            "windowId": winfo["windowId"],
            "bounds": {"windowState": "maximized"},
        })
        await cdp.detach()
    except Exception:
        pass


_MENU_VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
]
_MENU_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


def _vpn_provider() -> str:
    """Провайдер VPN из config.yaml: veepn (по умолчанию) или vpnly."""
    try:
        import yaml as _yaml
        cfg_path = _HERE / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as fh:
                cfg = _yaml.safe_load(fh) or {}
            p = (cfg.get("vpn") or {}).get("provider", "veepn")
            return str(p).strip().lower()
    except Exception:
        pass
    return "veepn"


def _vpn_enabled() -> bool:
    """VPN-расширения удалены из проекта — всегда False.

    Сеть: прокси (тумблер в Настройках) или личный VPN на ПК (напрямую).
    """
    return False


def _set_vpn_enabled(enabled: bool) -> bool:
    """Пишет vpn.enabled в config.yaml точечно (сохраняет комментарии вокруг блока)."""
    cfg_path = _HERE / "config.yaml"
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except Exception:
        return False
    m = re.search(r"(?m)^(vpn:\s*\n)(.*?)(?=^[a-zA-Z_][\w]*:|\Z)", text, re.S)
    if not m:
        return False
    head, body = m.group(1), m.group(2)
    new_body, n = re.subn(
        r"(?m)^([ \t]*enabled:\s*)(true|false|True|False)\s*$",
        rf"\g<1>{'true' if enabled else 'false'}",
        body,
        count=1,
    )
    if n == 0:
        new_body = f"  enabled: {'true' if enabled else 'false'}\n" + body
    new_text = text[: m.start()] + head + new_body + text[m.end() :]
    try:
        cfg_path.write_text(new_text, encoding="utf-8")
        return True
    except Exception:
        return False


def _resolve_extension_base(base: Path) -> str | None:
    """manifest.json в base или в единственной подпапке."""
    if not base.exists():
        return None
    if (base / "manifest.json").exists():
        return str(base.resolve())
    for sub in base.iterdir():
        if sub.is_dir() and (sub / "manifest.json").exists():
            return str(sub.resolve())
    return None


def _vpn_extension_dir(*, ignore_toggle: bool = False) -> str | None:
    """VPN-расширения удалены из проекта — всегда None.

    Весь VeepN/VPNLY-код в menu.py остаётся мёртвым (не вызывается):
    сеть — прокси или личный VPN на ПК (напрямую).
    """
    return None


def _vpn_is_veepn() -> bool:
    ext = _vpn_extension_dir()
    if not ext:
        return _vpn_provider() != "vpnly"
    try:
        m = json.loads((Path(ext) / "manifest.json").read_text(encoding="utf-8"))
        blob = f"{m.get('name', '')} {m.get('homepage_url', '')} {ext}".lower()
        return "veepn" in blob
    except Exception:
        return _vpn_provider() != "vpnly"


def _vpn_ext_id_from_path(ext_path: str | None = None) -> str | None:
    """ID unpacked-расширения без manifest.key (VeepN) — хеш пути, как в Chrome."""
    try:
        import hashlib as _hl
        path = str(Path(ext_path or _vpn_extension_dir() or "").resolve())
        if not path or path.endswith("."):
            return None
        h = _hl.sha256(path.encode("utf-16-le")).hexdigest()[:32]
        return "".join(chr(ord("a") + int(c, 16)) for c in h)
    except Exception:
        return None


def _vpn_ext_id_for_install() -> str | None:
    """ID для копирования расширения в профиль."""
    if _vpn_is_veepn():
        return _vpn_ext_id_from_path()
    return _vpn_ext_id_from_key()


def _vpn_ext_id_from_key() -> str | None:
    """Детерминированный ID расширения из поля manifest.key (как считает Chrome:
    sha256 от DER публичного ключа, первые 16 байт → буквы a-p). Работает без
    ожидания service worker (в MV3 он ленивый и может не стартовать сам)."""
    try:
        _ext = _vpn_extension_dir()
        if not _ext:
            return None
        import json as _j, base64 as _b64, hashlib as _hl
        m = _j.loads((Path(_ext) / "manifest.json").read_text(encoding="utf-8"))
        key = m.get("key")
        if not key:
            return None
        der = _b64.b64decode(key)
        h = _hl.sha256(der).hexdigest()[:32]
        return "".join(chr(ord("a") + int(c, 16)) for c in h)
    except Exception:
        return None


def _profile_has_vpn_extension(profile_path: Path | str | None) -> bool:
    """True, если VPN-расширение уже сохранено в профиле Chrome."""
    eid = _vpn_ext_id_for_install()
    if not eid or not profile_path:
        return False
    p = Path(profile_path)
    ext_root = p / "Default" / "Extensions" / eid
    if not ext_root.is_dir():
        return False
    try:
        return any(ext_root.iterdir())
    except Exception:
        return False


def _needs_load_extension(profile_path: Path | str | None) -> bool:
    """Нужно ли передавать --load-extension при запуске браузера."""
    if not _vpn_extension_dir():
        return False
    if profile_path is None:
        return True
    return not _profile_has_vpn_extension(profile_path)


# Бесплатные серверы из vpn_extension/background.js (без de-hub — дубликат DE).
_VPN_FREE_SERVERS: list[dict] = [
    {
        "host": "ge-hub.freeruproxy.ink", "port": 443, "proto": "https",
        "user": "openproxy", "pass": "7a379d234cd89887",
        "uuid": "a96a6e7c-7156-4f27-92f5-a9c4668fea36",
        "city": {"name": "Dusseldorf", "country": {"code": "de", "name": "Germany", "continent": "eu"}},
    },
    {
        "host": "us-hub.freeruproxy.ink", "port": 443, "proto": "https",
        "user": "openproxy", "pass": "685ce62bfdf0d359",
        "uuid": "6bb572d9-3080-49c8-b193-2124973bbf31",
        "city": {"name": "Chicago", "country": {"code": "us", "name": "United States of America", "continent": "us"}},
    },
    {
        "host": "fr-hub.freeruproxy.ink", "port": 443, "proto": "https",
        "user": "openproxy", "pass": "abc21f3a79de33dc",
        "uuid": "b30d6e4e-c9af-4485-b895-e9998718a60a",
        "city": {"name": "Paris", "country": {"code": "fr", "name": "France", "continent": "eu"}},
    },
    {
        "host": "nl-hub.freeruproxy.ink", "port": 443, "proto": "https",
        "user": "openproxy", "pass": "2ad5c3cece9f19f6",
        "uuid": "3644e630-067b-4885-a510-02eeda1d0172",
        "city": {"name": "Amsterdam", "country": {"code": "nl", "name": "Netherlands", "continent": "eu"}},
    },
]

# Порядок стран VPN: USA → остальные бесплатные из списка VeepN.
_VPN_DEFAULT_COUNTRY = "us"
_VPN_FLIPKART_COUNTRY_ORDER = (
    _VPN_DEFAULT_COUNTRY, "ca", "fr", "de", "nl", "gb", "sg", "ru",
)
# обратная совместимость
_VPNLY_FLIPKART_COUNTRY_ORDER = _VPN_FLIPKART_COUNTRY_ORDER
# VPN только для Flipkart / Google (не для фоновых вкладок и простоя)
_VPN_SITE_HOSTS = (
    "flipkart.com",
    "google.com",
    "google.co.",
    "accounts.google",
    "gmail.com",
)


def _url_needs_vpn(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _VPN_SITE_HOSTS)

_VEEPN_CC_PATTERNS: dict[str, tuple[str, str]] = {
    "us": ("US", r"united states|oregon|virginia|chicago|new york|los angeles|miami|dallas|america"),
    "ca": ("CA", r"canada|toronto|montreal|vancouver|ottawa"),
    "fr": ("FR", r"france|paris"),
    "de": ("DE", r"germany|dusseldorf|frankfurt|berlin|munich"),
    "nl": ("NL", r"netherlands|amsterdam"),
    "gb": ("GB", r"united kingdom|\buk\b|london|britain|england"),
    "uk": ("GB", r"united kingdom|\buk\b|london|britain|england"),
    "sg": ("SG", r"singapore"),
    "ru": ("RU", r"russia|saint petersburg|st\.?\s*petersburg|москва|russia"),
}

# UI-имена бесплатных локаций VeepN (скролл по списку «Бесплатные локации»)
_VEEPN_UI_COUNTRY_NAMES: dict[str, list[str]] = {
    "us": ["United States", "USA", "United States of America", "США"],
    "ca": ["Canada", "Канада"],
    "fr": ["France", "Франция"],
    "de": ["Germany", "Deutschland", "Германия"],
    "nl": ["Netherlands", "Нидерланды"],
    "gb": ["United Kingdom", "UK", "Britain", "Великобритания"],
    "uk": ["United Kingdom", "UK", "Britain"],
    "sg": ["Singapore", "Сингапур"],
    "ru": ["Russia", "Russian Federation", "Россия"],
}
_VEEPN_UI_US_STATES = ("Oregon", "Virginia", "New York", "Chicago", "Los Angeles", "Miami", "Dallas")


def _vpnly_country_code(server: dict) -> str:
    return str(
        (server.get("city") or {}).get("country", {}).get("code") or ""
    ).lower()


def _vpn_normalize_cc(cc: str) -> str:
    c = (cc or "").lower().strip()
    if c == "uk":
        return "gb"
    return c


def _vpn_free_country_codes_static() -> list[str]:
    """USA → CA → остальные: сначала фиксированный порядок, потом VPNLY free servers."""
    out: list[str] = []
    for cc in _VPN_FLIPKART_COUNTRY_ORDER:
        n = _vpn_normalize_cc(cc)
        if n and n not in out:
            out.append(n)
    for s in _VPN_FREE_SERVERS:
        n = _vpn_normalize_cc(_vpnly_country_code(s))
        if n and n not in out:
            out.append(n)
    return out


def _vpnly_servers_for_flipkart(*, exclude: frozenset[str] = frozenset()) -> list[dict]:
    order = {cc: i for i, cc in enumerate(_vpn_free_country_codes_static())}
    excl = {_vpn_normalize_cc(c) for c in exclude}
    servers = [
        s for s in _VPN_FREE_SERVERS
        if _vpn_normalize_cc(_vpnly_country_code(s)) not in excl
    ]
    servers.sort(key=lambda s: order.get(_vpn_normalize_cc(_vpnly_country_code(s)), 99))
    return servers


def _iter_profile_dirs() -> list[Path]:
    """Все папки профилей Chrome в проекте."""
    out: list[Path] = []
    for d in (DONE_PROFILES_DIR, PROFILES_DIR):
        if d.exists():
            out.extend(sorted(p for p in d.glob("profile_*") if p.is_dir()))
    _VPN_PING_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if _VPN_PING_PROFILE_DIR not in out:
        out.insert(0, _VPN_PING_PROFILE_DIR)
    return out


async def _close_vpn_extension_tabs(context, eid: str | None = None) -> None:
    """Закрыть вкладки popup/offscreen VPN — не показывать popup вместо Flipkart."""
    if not eid:
        eid = await _vpn_ext_id(context) or _vpn_ext_id_for_install() or _vpn_ext_id_from_key()
    prefix = f"chrome-extension://{eid}" if eid else "chrome-extension://"

    def _is_ext_tab(url: str) -> bool:
        u = (url or "").lower()
        if _is_vpn_junk_url(u):
            return True
        if u.startswith("chrome-extension://"):
            if not eid:
                return True
            return eid in u or u.startswith(prefix)
        return False

    ext_pages = [p for p in context.pages if _is_ext_tab(p.url or "")]
    work_pages = [p for p in context.pages if not _is_ext_tab(p.url or "")]
    if ext_pages and not work_pages:
        with contextlib.suppress(Exception):
            await context.new_page()

    for p in list(context.pages):
        try:
            u = p.url or ""
            if not _is_ext_tab(u):
                continue
            if len(context.pages) > 1:
                await p.close()
            elif _is_vpn_junk_url(u):
                await p.goto("about:blank", wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass


async def _close_extension_startup_tabs(context) -> None:
    """Закрывает мусорные вкладки расширения (vpnlyprotect.ru, ERR_BLOCKED и т.п.)."""
    await _block_vpn_junk_routes(context)
    await asyncio.sleep(0.4)
    await _close_junk_tabs(context)

    def _is_closable(url: str) -> bool:
        u = (url or "").lower()
        if _is_junk_url(u):
            return True
        if u.startswith("chrome-extension://"):
            return True
        if "chrome-error://" in u or "chromewebdata" in u:
            return True
        return False

    for p in list(context.pages):
        try:
            if len(context.pages) <= 1:
                break
            u = p.url or ""
            if not _is_closable(u):
                # ERR_BLOCKED иногда остаётся на chrome-extension URL с ошибкой в body
                with contextlib.suppress(Exception):
                    title = await p.title()
                    if _page_shows_client_block(u, title):
                        if len(context.pages) > 1:
                            await p.close()
                        continue
                continue
            if _is_closable(u):
                await p.close()
        except Exception:
            pass

    # Добить вкладки с текстом «заблокирован» / ERR_BLOCKED_BY_CLIENT
    for p in list(context.pages):
        try:
            if len(context.pages) <= 1:
                break
            body = ""
            with contextlib.suppress(Exception):
                body = await p.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 400)"
                )
            title = ""
            with contextlib.suppress(Exception):
                title = await p.title()
            if _page_shows_client_block(p.url or "", title, body or ""):
                await p.close()
        except Exception:
            pass
    await _close_extra_blank_tabs(context)


async def _bg_install_extensions_on_profiles(browser_only: bool = False) -> int:
    """Установка расширения: только копирование файлов. Браузер — только по кнопке вручную."""
    if not _vpn_extension_dir():
        return 0
    if not browser_only:
        return install_extensions_filesystem_all()

    # Только успешные done-профили — вход/поиск номеров без расширения
    missing = [
        p for p in _iter_profile_dirs()
        if _profile_is_successful_done(p) and not _profile_has_vpn_extension(p)
    ]
    if not missing:
        return 0

    from playwright.async_api import async_playwright
    installed = 0
    with _chrome_window_hider():
        for i, profile_path in enumerate(missing, 1):
            if _install_extension_filesystem(profile_path):
                installed += 1
                continue
            _set_vpn_bg_status("warming", f"Браузер [{i}/{len(missing)}] {profile_path.name}…")
            print(f"  {DIM}[{i}/{len(missing)}] Браузер (headless) → {profile_path.name}{RST}")
            if i > 1:
                await _vpn_chrome_cooldown(extra=2.0)
            pw = await async_playwright().start()
            ctx = None
            try:
                kw = _browser_launch_kw(
                    headless=True, profile_path=profile_path,
                    use_bundled_chromium=True, background_install=True,
                )
                kw["args"] = _hidden_chrome_args(kw.get("args", []))
                ctx = await pw.chromium.launch_persistent_context(
                    str(profile_path.resolve()), **kw)
                await _close_extension_startup_tabs(ctx)
                await asyncio.sleep(3.0)
                await _close_extension_startup_tabs(ctx)
                if _profile_has_vpn_extension(profile_path):
                    installed += 1
                    print(f"  {G}✔ {profile_path.name}{RST}")
            except Exception as exc:
                print(f"  {Y}⚠ {profile_path.name}: {exc}{RST}")
            finally:
                if ctx:
                    try:
                        await ctx.close()
                    finally:
                        _note_chromium_closed()
                try:
                    await pw.stop()
                except Exception:
                    pass
    return installed


async def _bg_vpn_warmup_ping() -> bool:
    """Фон: ping-профиль — расширение + подключение VPN (скрытое окно)."""
    if not _vpn_extension_dir():
        return False
    _VPN_PING_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    profile = _VPN_PING_PROFILE_DIR
    if not _profile_has_vpn_extension(profile):
        await _bg_install_extensions_on_profiles()
    _set_vpn_bg_status("warming", "Проверка VPN (фон)…")
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            kw = _vpn_browser_launch_kw(profile)
            with _chrome_window_hider():
                ctx = await pw.chromium.launch_persistent_context(
                    str(profile.resolve()), **kw)
            try:
                ok = await _vpn_connect_on_use(ctx, profile)
                scan = scan_profiles_extension_status()
                if ok:
                    _set_vpn_bg_status(
                        "ready",
                        f"VPN OK · расширение {scan['with_ext']}/{scan['total']} проф.",
                    )
                else:
                    _set_vpn_bg_status(
                        "error",
                        f"VPN не подключился · расширение {scan['with_ext']}/{scan['total']}",
                    )
                return ok
            finally:
                if ctx:
                    with contextlib.suppress(Exception):
                        await _vpn_disconnect(ctx)
                    try:
                        await ctx.close()
                    finally:
                        _note_chromium_closed()
    except Exception as exc:
        _set_vpn_bg_status("error", f"VPN: {str(exc)[:80]}")
        return False


def _profile_is_successful_done(profile_path: Path | str | None) -> bool:
    """Уже успешный вход: chrome_profiles_done + .profile_meta.json, не tmp/rec."""
    if not profile_path:
        return False
    p = Path(profile_path)
    name = p.name.lower()
    if "_tmp_" in name or "_rec_" in name:
        return False
    try:
        pr = p.resolve()
        done = DONE_PROFILES_DIR.resolve()
        if pr != done and done not in pr.parents and pr.parent != done:
            return False
    except Exception:
        return False
    return (p / ".profile_meta.json").exists()


def _is_temp_profile_dir(profile_path: Path | str | None) -> bool:
    """Временный/незавершённый профиль — можно удалять.

    Не трогаем успешные входы и доступные (есть .profile_meta.json).
    """
    if not profile_path:
        return False
    p = Path(profile_path)
    if not p.is_dir():
        return False
    name = p.name.lower()
    if "_tmp_" in name or "_rec_" in name:
        return True
    if (p / ".profile_meta.json").exists():
        return False  # доступный / успешный вход
    try:
        pr = p.resolve()
        work = PROFILES_DIR.resolve()
        done = DONE_PROFILES_DIR.resolve()
        if pr.parent == work or work in pr.parents:
            return True
        if pr.parent == done or done in pr.parents:
            return True  # done без meta = оборванный вход
    except Exception:
        return False
    return False


def purge_temp_profiles() -> dict:
    """Удаляет временные профили. Успешные/доступные (done+meta) не трогает.

    Сначала rmtree без скана Chrome; если папки заняты — один общий kill и повтор.
    (Раньше kill+sleep на каждый профиль → минуты «зависания».)
    """
    import os as _os
    import shutil as _sh
    import stat as _stat
    import time as _t

    def _rm_err(func, path, exc_info):
        try:
            _os.chmod(path, _stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    removed: list[str] = []
    skipped = 0
    errors: list[str] = []
    to_delete: list[Path] = []
    for root in (PROFILES_DIR, DONE_PROFILES_DIR):
        if not root.exists():
            continue
        for p in root.iterdir():
            if not (p.is_dir() and p.name.startswith("profile_")):
                continue
            if _is_temp_profile_dir(p):
                to_delete.append(p)
            else:
                skipped += 1

    def _attempt(paths: list[Path]) -> list[Path]:
        nonlocal skipped
        failed: list[Path] = []
        for p in paths:
            if not p.exists():
                continue
            # meta мог появиться пока удаляли соседние
            if not _is_temp_profile_dir(p):
                skipped += 1
                continue
            for lock in ("SingletonLock", "lockfile", "SingletonSocket"):
                with contextlib.suppress(PermissionError, OSError):
                    (p / lock).unlink(missing_ok=True)
            try:
                _sh.rmtree(str(p), onerror=_rm_err)
            except Exception as exc:
                errors.append(f"{p.name}: {exc}")
                if p.exists():
                    failed.append(p)
                continue
            if p.exists():
                failed.append(p)
            else:
                removed.append(p.name)
        return failed

    leftover = _attempt(to_delete)
    if leftover:
        with contextlib.suppress(Exception):
            _kill_chrome_for_profiles(leftover)
        _t.sleep(0.4)
        leftover = _attempt(leftover)
        for p in leftover:
            if p.exists():
                errors.append(p.name)

    _invalidate_done_profiles_cache()
    return {
        "removed": len(removed),
        "names": removed,
        "skipped": skipped,
        "errors": errors,
    }


def count_temp_profiles() -> int:
    """Сколько временных профилей сейчас на диске (для кнопки в Настройках)."""
    n = 0
    with contextlib.suppress(Exception):
        for root in (PROFILES_DIR, DONE_PROFILES_DIR):
            if not root.exists():
                continue
            for p in root.iterdir():
                if (p.is_dir() and p.name.startswith("profile_")
                        and _is_temp_profile_dir(p)):
                    n += 1
    return n


def _profile_allows_vpn(
    profile_path: Path | str | None, *, ping_check: bool = False,
) -> bool:
    """VeepN-расширение только для уже успешных done-профилей.

    Поиск номеров / первичный вход — без расширения (прокси или VPN на ПК).
    Ping-профиль — только явная проверка (ping_check=True).
    """
    if not profile_path:
        return False
    pp = Path(profile_path).resolve()
    if pp == _VPN_PING_PROFILE_DIR.resolve():
        return bool(ping_check)
    return _profile_is_successful_done(pp)


def _veepn_reset_session_js() -> str:
    """Сброс таймера VeepN (connectedAt=0) + выкл auto-connect в storage."""
    return """async () => {
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const msg = async (type, data = {}) => {
            try { return await chrome.runtime.sendMessage({ type, data }); }
            catch (e) { return { ok: false, error: String(e) }; }
        };
        try {
            await msg('set-auto-connect-setting', { status: false });
        } catch (e) {}
        // Официальный disconnect сбрасывает connectedAt внутри расширения
        await msg('disconnect', {});
        await sleep(800);
        await msg('disconnect', {});
        await sleep(400);
        // Явный сброс таймера (на случай если disconnect не дошёл)
        try {
            const all = await chrome.storage.local.get(['connection']);
            const conn = (all.connection && typeof all.connection === 'object')
                ? all.connection : {};
            await chrome.storage.local.set({
                connection: {
                    ...conn,
                    connectedAt: 0,
                    useAutoConnect: false,
                },
            });
        } catch (e) {}
        try {
            await chrome.proxy.settings.clear({ scope: 'regular' });
        } catch (e) {}
        await sleep(300);
        try {
            const mode = await chrome.proxy.settings.get({});
            const m = (mode?.value?.mode) || 'direct';
            return !m || m === 'direct' || m === 'system' || m === 'auto_detect';
        } catch (e) { return true; }
    }"""


async def _veepn_eval_js(context, eid: str, js: str):
    """Выполнить JS в service worker VeepN (предпочтительно) или на вкладке расширения."""
    sw = await _wait_vpn_service_worker(context, eid, timeout=10.0)
    if sw:
        with contextlib.suppress(Exception):
            return await sw.evaluate(js)
    for p in list(context.pages):
        u = (p.url or "").lower()
        if u.startswith(f"chrome-extension://{eid.lower()}"):
            with contextlib.suppress(Exception):
                return await p.evaluate(js)
    # Последний шанс: коротко открыть background/offscreen-подобную страницу расширения
    page = None
    try:
        page = await context.new_page()
        await page.goto(
            f"chrome-extension://{eid}/background.html",
            wait_until="domcontentloaded", timeout=8_000,
        )
        return await page.evaluate(js)
    except Exception:
        with contextlib.suppress(Exception):
            if page:
                await page.goto(
                    f"chrome-extension://{eid}/src/popup/popup.html",
                    wait_until="domcontentloaded", timeout=8_000,
                )
                return await page.evaluate(js)
        return None
    finally:
        if page:
            with contextlib.suppress(Exception):
                await page.close()


async def _vpn_disconnect(context) -> bool:
    """Отключает VeepN/VPNLY в расширении (перед закрытием Chrome / сменой страны)."""
    if not _vpn_extension_dir(ignore_toggle=True) or context is None:
        return True
    eid = await _vpn_ext_id(context)
    if not eid:
        return False

    disconnected = False
    if _vpn_is_veepn():
        print(f"  {DIM}VeepN: выключаю перед закрытием…{RST}")
        raw = await _veepn_eval_js(context, eid, _veepn_reset_session_js())
        disconnected = bool(raw)
        with contextlib.suppress(Exception):
            await _veepn_clear_autoconnect(context, eid)
        # UI-питание, если API не снял proxy
        still = False
        with contextlib.suppress(Exception):
            still = await _vpn_is_proxy_active(context, eid)
        if still or not disconnected:
            with contextlib.suppress(Exception):
                pop = await _open_extension_popup_page(
                    context, eid, _veepn_popup_rel_paths(),
                )
                if pop:
                    await pop.bring_to_front()
                    await pop.wait_for_timeout(400)
                    if await _veepn_popup_is_blank(pop):
                        pop = await _veepn_recover_blank_popup(
                            context, eid, blank_page=pop,
                        ) or pop
                    st = await _veepn_connection_label(pop)
                    if st == "on":
                        await _veepn_ui_click_power(pop)
                        await _veepn_wait_until_off(pop, seconds=12.0)
                    with contextlib.suppress(Exception):
                        disconnected = not await _vpn_is_proxy_active(context, eid)
        await _close_vpn_extension_tabs(context, eid)
        if not disconnected:
            with contextlib.suppress(Exception):
                disconnected = not await _vpn_is_proxy_active(context, eid)
        print(f"  {DIM}✔ VeepN выкл{RST}" if disconnected else f"  {DIM}VeepN: выкл (best-effort){RST}")
        return True  # не блокируем закрытие Chrome

    _js = """async () => {
        try {
            await chrome.runtime.sendMessage({type: 'disconnect', data: {}});
            await new Promise(r => setTimeout(r, 900));
            const mode = await chrome.proxy.settings.get({});
            const m = (mode?.value?.mode) || 'direct';
            return !m || m === 'direct' || m === 'system';
        } catch (e) { return false; }
    }"""
    raw = await _veepn_eval_js(context, eid, _js)
    disconnected = bool(raw)
    return disconnected


async def _close_browser_session(
    ctx, pw=None, profile_path: Path | str | None = None, *,
    disconnect_vpn: bool = True,
) -> None:
    """Закрывает браузер; сначала выключает VPN в расширении (сценарий/ошибка/стоп)."""
    if ctx and disconnect_vpn and _vpn_extension_dir(ignore_toggle=True):
        with contextlib.suppress(Exception):
            await _vpn_disconnect(ctx)
    if ctx:
        try:
            await ctx.close()
        except Exception:
            pass
        finally:
            _note_chromium_closed()
    if pw:
        with contextlib.suppress(Exception):
            await pw.stop()
    if profile_path is not None:
        _unregister_purchase_profile(profile_path)


# ── Сериализация экранной активации VeepN ────────────────────────────────────
# VeepN включается физическими кликами мыши (курсор + передний план окна) —
# это глобальные ресурсы. При параллельных окнах клики одного потока сбивают
# другой (иконка пазла не находится, клик уходит не в то окно). Лок пропускает
# к экрану только один поток за раз; поиск номеров, ввод и OTP остаются
# параллельными, т.к. выполняются вне этих функций.
_veepn_screen_locks: "dict" = {}   # loop -> asyncio.Lock
_veepn_screen_owner: "dict" = {}   # loop -> задача-владелец


class _VeepnScreenGuard:
    """Реентрантный per-loop лок экранной активации VeepN.

    В пределах одной задачи вложенный вход — no-op (connect → внутри reconnect
    не блокирует сам себя); между разными задачами — строгая очередь.
    """

    def __init__(self, loop) -> None:
        self._loop = loop
        self._held = False

    async def __aenter__(self):
        lk = _veepn_screen_locks.get(self._loop)
        if lk is None:
            lk = asyncio.Lock()
            _veepn_screen_locks[self._loop] = lk
        cur = asyncio.current_task()
        if _veepn_screen_owner.get(self._loop) is cur:
            return self  # уже держим в этой задаче — реентрантно
        await lk.acquire()
        _veepn_screen_owner[self._loop] = cur
        self._held = True
        return self

    async def __aexit__(self, *exc) -> bool:
        if self._held:
            _veepn_screen_owner.pop(self._loop, None)
            _veepn_screen_locks[self._loop].release()
        return False


def _veepn_screen_guard():
    """Async-контекст: для VeepN — реентрантный per-loop лок экрана, иначе no-op."""
    if not _vpn_is_veepn():
        return contextlib.nullcontext()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return contextlib.nullcontext()
    return _VeepnScreenGuard(loop)


async def _vpn_connect_on_use(
    context, profile_path: Path | str | None = None, *,
    max_attempts: int = 3, quick: bool = False, ping_check: bool = False,
) -> bool:
    """Включает VPN при использовании профиля; экранная активация сериализована."""
    async with _veepn_screen_guard():
        return await _vpn_connect_on_use_impl(
            context, profile_path,
            max_attempts=max_attempts, quick=quick, ping_check=ping_check,
        )


async def _vpn_connect_on_use_impl(
    context, profile_path: Path | str | None = None, *,
    max_attempts: int = 3, quick: bool = False, ping_check: bool = False,
) -> bool:
    """Включает VPN при использовании профиля (расширение уже в профиле или только что загружено)."""
    if getattr(context, "_subhub_via_proxy", False):
        print(f"  {DIM}Браузер на прокси — VeepN/VPNLY не трогаем{RST}")
        return True
    if not _vpn_extension_dir():
        return True
    if profile_path is not None and not _profile_allows_vpn(profile_path, ping_check=ping_check):
        tag = Path(profile_path).name
        print(f"  {DIM}VPN: нет активного сценария для {tag} — подключение пропущено{RST}")
        return False
    await _close_junk_tabs(context)
    await _activate_vpn_extension_via_chrome_page(context)
    wait = 3.0 if quick else (4.0 if profile_path and _profile_has_vpn_extension(profile_path) else 6.0)
    await asyncio.sleep(wait)
    for attempt in range(max_attempts):
        if attempt > 0:
            print(f"  {Y}Повторная попытка подключить VPN ({attempt + 1}/{max_attempts})…{RST}")
            await _vpn_chrome_cooldown(extra=1.0 if quick else 2.0)
        if await _ensure_vpn_connected(context, quick=quick):
            eid = await _vpn_ext_id(context)
            if eid:
                await _close_vpn_extension_tabs(context, eid)
            try:
                wp = await _main_work_page(context)
                await wp.bring_to_front()
            except Exception:
                pass
            return True
    return False


async def _vpn_connect_for_profile(
    context, profile_path: Path | str | None = None, *,
    timeout: float = 120.0, max_attempts: int = 3, quick: bool = False,
    ping_check: bool = False,
) -> bool:
    """VPN для профиля; экранная активация VeepN сериализована между потоками."""
    async with _veepn_screen_guard():
        return await _vpn_connect_for_profile_impl(
            context, profile_path, timeout=timeout,
            max_attempts=max_attempts, quick=quick, ping_check=ping_check,
        )


async def _vpn_connect_for_profile_impl(
    context, profile_path: Path | str | None = None, *,
    timeout: float = 120.0, max_attempts: int = 3, quick: bool = False,
    ping_check: bool = False,
) -> bool:
    """VPN → закрыть popup → рабочая вкладка (единый сценарий для всех потоков).

    Если proxy уже жив — не переподключаем (Flipkart мог открыться; рвать нельзя).
    """
    if getattr(context, "_subhub_via_proxy", False):
        print(f"  {DIM}Браузер на прокси — VeepN/VPNLY не трогаем{RST}")
        return True
    if not _vpn_extension_dir():
        return True
    if profile_path is not None and not _profile_allows_vpn(profile_path, ping_check=ping_check):
        tag = Path(profile_path).name
        print(f"  {DIM}VPN: нет активного сценария для {tag} — подключение пропущено{RST}")
        return False
    await _close_junk_tabs(context)
    eid0 = await _vpn_ext_id(context)
    if eid0 and await _vpn_is_proxy_active(context, eid0):
        print(f"  {G}✔ VPN уже активен — без переподключения{RST}")
        await _close_vpn_extension_tabs(context, eid0)
        with contextlib.suppress(Exception):
            wp = await _main_work_page(context)
            await wp.bring_to_front()
        return True
    # Файлы уже в профиле → chrome://extensions/ выбрать и включить
    await _activate_vpn_extension_via_chrome_page(context)
    wait = 2.5 if quick else (3.5 if profile_path and _profile_has_vpn_extension(profile_path) else 5.0)
    await asyncio.sleep(wait)
    deadline = time.monotonic() + timeout
    connected = False
    for attempt in range(max_attempts):
        if time.monotonic() >= deadline:
            break
        if attempt > 0:
            print(f"  {Y}Повторная попытка подключить VPN ({attempt + 1}/{max_attempts})…{RST}")
            await _vpn_chrome_cooldown(extra=1.0 if quick else 2.0)
        if await _ensure_vpn_connected(context, quick=quick):
            connected = True
            break
    if not connected:
        # Последний шанс: перебор стран Flipkart (USA→FR→DE)
        print(f"  {Y}⚠ VPN: базовое подключение не удалось — перебор стран…{RST}")
        ok_rc, _cc = await _vpn_reconnect_for_flipkart(context, profile_path)
        connected = bool(ok_rc)
    if not connected:
        print(f"  {Y}⚠ VPN: не подключился за {int(timeout)}s{RST}")
        return False
    eid = await _vpn_ext_id(context)
    if eid:
        # Flipkart: зафиксировать USA только если ещё не online; живой proxy не трогаем
        if _vpn_is_veepn() and not await _vpn_is_proxy_active(context, eid):
            with contextlib.suppress(Exception):
                await _veepn_ensure_usa_for_flipkart(context, eid)
        await _close_vpn_extension_tabs(context, eid)
        if not await _wait_vpn_proxy_ready(context, eid, timeout=8.0):
            await _wait_vpn_proxy_ready(context, eid, timeout=15.0)
    with contextlib.suppress(Exception):
        wp = await _main_work_page(context)
        await wp.bring_to_front()
    return True


async def _veepn_switch_country(context, eid: str, country_code: str) -> bool:
    """Полный цикл VeepN: disconnect (+сброс таймера) → страна → connect."""
    cc = country_code.lower()
    iso, _ = _VEEPN_CC_PATTERNS.get(cc, (cc.upper(), cc))
    label = cc.upper()
    print(f"  {DIM}VeepN → {label}…{RST}")
    # Жёсткий disconnect + connectedAt=0, иначе UI показывает старый таймер
    await _veepn_eval_js(context, eid, _veepn_reset_session_js())
    await asyncio.sleep(1.0)
    _js = f"""async () => {{
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const msg = async (type, data = {{}}) => {{
            try {{ return await chrome.runtime.sendMessage({{ type, data }}); }}
            catch (e) {{ return null; }}
        }};
        await msg('update-locations-data', {{}});
        await sleep(800);
        {_veepn_location_pick_js(cc)}
        await sleep(600);
        // Новый connect → setConnectedAt() внутри VeepN → таймер с 00:00:00
        const r = await msg('connect', {{}});
        await sleep(4500);
        const st = await msg('get-connection-state', {{}});
        const mode = await chrome.proxy.settings.get({{}});
        const m = (mode?.value?.mode) || 'direct';
        const proxyOn = m && m !== 'direct' && m !== 'system' && m !== 'auto_detect';
        const status = (st?.data?.status || st?.status || '').toLowerCase();
        return proxyOn || status === 'connected';
    }}"""
    ok = False
    raw = await _veepn_eval_js(context, eid, _js)
    ok = bool(raw)
    if ok:
        await _veepn_finalize_connected(context, eid, via=label)
        return True
    return False


async def _veepn_discover_free_country_codes(context, eid: str) -> list[str]:
    """Коды стран из бесплатных локаций VeepN (если расширение их отдаёт)."""
    js = """async () => {
        const codes = new Set();
        const walk = (node, depth = 0, preferFree = null) => {
            if (!node || depth > 18) return;
            if (Array.isArray(node)) {
                for (const x of node) walk(x, depth + 1, preferFree);
                return;
            }
            if (typeof node !== 'object') return;
            const freeHint = (
                node.free === true || node.isFree === true || node.premium === false
                || node.isPremium === false || node.tier === 'free' || node.type === 'free'
                || node.plan === 'free'
            );
            const paidHint = (
                node.free === false || node.isFree === false || node.premium === true
                || node.isPremium === true || node.tier === 'premium' || node.type === 'premium'
            );
            let nextPrefer = preferFree;
            if (freeHint) nextPrefer = true;
            if (paidHint) nextPrefer = false;
            const code = String(
                node.code || node.countryCode || node.isoCode || node.iso || ''
            ).toLowerCase();
            if (code.length === 2 && /^[a-z]{2}$/.test(code)) {
                // Берём явно free; если флагов нет — тоже пробуем (бесплатный тариф VeepN)
                if (nextPrefer === true || (nextPrefer === null && !paidHint)) {
                    codes.add(code);
                }
            }
            for (const v of Object.values(node)) walk(v, depth + 1, nextPrefer);
        };
        try { walk(await chrome.storage.local.get(null)); } catch (e) {}
        try {
            await chrome.runtime.sendMessage({ type: 'update-locations-data', data: {} });
        } catch (e) {}
        try {
            const r = await chrome.runtime.sendMessage({ type: 'get-locations', data: {} });
            walk(r && (r.data !== undefined ? r.data : r));
        } catch (e) {}
        return Array.from(codes);
    }"""
    out: list[str] = []
    sw = await _wait_vpn_service_worker(context, eid, timeout=8.0)
    raw = None
    if sw:
        with contextlib.suppress(Exception):
            raw = await sw.evaluate(js)
    if not isinstance(raw, list):
        return out
    for item in raw:
        n = _vpn_normalize_cc(str(item or ""))
        if n and n not in out:
            out.append(n)
    return out


async def _flipkart_vpn_country_queue(context=None) -> list[str]:
    """USA → CA → остальные: статический список + free из VeepN/VPNLY."""
    order = _vpn_free_country_codes_static()
    if context is not None and _vpn_is_veepn():
        eid = await _vpn_ext_id(context)
        if eid:
            for cc in await _veepn_discover_free_country_codes(context, eid):
                n = _vpn_normalize_cc(cc)
                if n and n not in order:
                    order.append(n)
    return order


async def _vpn_connect_country(
    context, country_code: str, profile_path=None,
) -> bool:
    """Полный выкл → включить конкретную страну (VeepN или VPNLY free)."""
    cc = _vpn_normalize_cc(country_code)
    if not cc or not _vpn_extension_dir():
        return False
    if profile_path is not None and not _profile_allows_vpn(profile_path):
        return False
    print(f"  {Y}⚠ Flipkart — VPN выкл → {cc.upper()}…{RST}")
    await _vpn_disconnect(context)
    await asyncio.sleep(1.2)
    eid = await _vpn_ext_id(context)
    if not eid:
        return False
    ok = False
    if _vpn_is_veepn():
        ok = await _veepn_switch_country(context, eid, cc)
    else:
        servers = [
            s for s in _vpnly_servers_for_flipkart()
            if _vpn_normalize_cc(_vpnly_country_code(s)) == cc
        ]
        for server in servers or []:
            if await _vpnly_enable_server(context, eid, server):
                ok = True
                break
        if not ok and cc == _VPN_DEFAULT_COUNTRY:
            ok = await _vpn_send_enable_proxy(context, eid)
    if not ok:
        return False
    if not await _wait_vpn_proxy_ready(context, eid, timeout=14.0):
        await _wait_vpn_proxy_ready(context, eid, timeout=10.0)
    await _close_vpn_extension_tabs(context, eid)
    with contextlib.suppress(Exception):
        wp = await _main_work_page(context)
        await wp.bring_to_front()
    return True


async def _veepn_reconnect_for_flipkart(
    context, profile_path=None, *,
    exclude_countries: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """VeepN: перебор бесплатных стран для Flipkart (USA → CA → …)."""
    if not _vpn_is_veepn():
        return False, ""
    eid = await _vpn_ext_id(context)
    if not eid:
        return False, ""
    excl = {_vpn_normalize_cc(c) for c in exclude_countries}
    await _vpn_disconnect(context)
    await asyncio.sleep(1.2)
    for cc in await _flipkart_vpn_country_queue(context):
        if cc in excl:
            continue
        if await _veepn_switch_country(context, eid, cc):
            if await _wait_vpn_proxy_ready(context, eid, timeout=14.0):
                return True, cc
    return False, ""


async def _veepn_reconnect_india(context, profile_path=None) -> bool:
    """Совместимость: переподключение VeepN для Flipkart (USA приоритет)."""
    ok, _ = await _veepn_reconnect_for_flipkart(context, profile_path)
    return ok


def _is_flipkart_nav_error(err: str) -> bool:
    u = (err or "").upper()
    return any(x in u for x in (
        "TIMEOUT", "TIMED_OUT", "CONNECTION", "ERR_", "NET::",
        "ACCESS DENIED", "REFUSED", "PERMISSION TO ACCESS",
    ))


async def _flipkart_page_access_denied(page) -> bool:
    """True, если Flipkart показывает Access Denied (Akamai / гео-блок)."""
    try:
        url = (page.url or "").lower()
        if "errors.edgesuite.net" in url or "accessdenied" in url.replace(" ", ""):
            return True
        body = (await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 1200)"
        )).lower()
        title = (await page.title()).lower()
        return (
            "access denied" in body
            or "access denied" in title
            or "permission to access" in body
            or "don't have permission" in body
            or "you don't have permission" in body
            or "errors.edgesuite.net" in body
        )
    except Exception:
        return False


async def _flipkart_page_blocked(page) -> bool:
    """Access Denied, ERR_CONNECTION_TIMED_OUT и прочие блокировки Chrome."""
    if await _flipkart_page_access_denied(page):
        return True
    try:
        url = (page.url or "").lower()
        if url.startswith("chrome-error://") or "chromewebdata" in url:
            return True
        body = (await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 1400)"
        )).lower()
        title = (await page.title()).lower()
        blocked = (
            "err_connection_timed_out",
            "connection timed out",
            "can't reach this site",
            "this site can't be reached",
            "не удается получить доступ",
            "не удаётся получить доступ",
            "превышено время ожидания",
            "err_connection_reset",
            "err_name_not_resolved",
        )
        return any(m in body or m in title for m in blocked)
    except Exception:
        return False


async def _veepn_connected_country_hint(context, eid: str) -> str:
    """Код страны VeepN ('us', 'nl', …) или '' если неизвестно."""
    for p in list(context.pages):
        u = (p.url or "").lower()
        if not u.startswith(f"chrome-extension://{eid.lower()}"):
            continue
        with contextlib.suppress(Exception):
            blob = (await p.evaluate(
                "() => (document.body?.innerText || '').slice(0, 500)"
            )).lower()
            for cc, (_, pat) in _VEEPN_CC_PATTERNS.items():
                if re.search(pat, blob, re.I):
                    return _vpn_normalize_cc(cc)
    sw = await _wait_vpn_service_worker(context, eid, timeout=5.0)
    if sw:
        with contextlib.suppress(Exception):
            raw = await sw.evaluate("""async () => {
                try {
                    const r = await chrome.runtime.sendMessage({type: 'get-connection-state', data: {}});
                    return JSON.stringify(r?.data || r || {});
                } catch (e) { return ''; }
            }""")
            low = (raw or "").lower()
            for cc, (_, pat) in _VEEPN_CC_PATTERNS.items():
                if re.search(pat, low, re.I):
                    return _vpn_normalize_cc(cc)
    return ""


async def _veepn_connect_country_prefer_api(context, eid: str, country_code: str) -> bool:
    """Сразу UI (карточка → список → штат → питание). API VeepN US почти не закрепляет."""
    cc = _vpn_normalize_cc(country_code) or _VPN_DEFAULT_COUNTRY
    print(f"  {DIM}VeepN: {cc.upper()} через UI (страна → питание)…{RST}")
    ok_ui = await _veepn_ui_reconnect_country(context, cc)
    if ok_ui:
        print(f"  {G}✔ VeepN: {cc.upper()} по UI{RST}")
        return True
    # Last resort — старый API (редко помогает)
    print(f"  {DIM}VeepN: UI не вышел — пробую API…{RST}")
    if await _veepn_switch_country(context, eid, cc):
        for _ in range(16):
            if await _vpn_is_proxy_active(context, eid):
                print(f"  {G}✔ VeepN: {cc.upper()} по API{RST}")
                return True
            await asyncio.sleep(0.35)
    return False


async def _veepn_ensure_usa_for_flipkart(context, eid: str) -> bool:
    """Flipkart: United States по API; UI — только если API не выбрал страну."""
    cc = await _veepn_connected_country_hint(context, eid)
    if await _vpn_is_proxy_active(context, eid):
        # Любая живая free-страна ок для первого захода; USA — после fail в resilient
        if cc == _VPN_DEFAULT_COUNTRY or not cc:
            print(f"  {G}✔ VeepN: VPN жив ({(cc or 'US?').upper()}){RST}")
            return True
        # Уже жив не-US — не рвём до Access Denied / timeout на сайте
        print(f"  {DIM}VeepN: жив {(cc or '?').upper()} — Flipkart без смены страны{RST}")
        return True
    print(f"  {DIM}VeepN: включаю USA (сейчас {(cc or 'выкл').upper()})…{RST}")
    if await _veepn_connect_country_prefer_api(context, eid, _VPN_DEFAULT_COUNTRY):
        return True
    print(f"  {DIM}VeepN: USA не закрепился — другие free-страны…{RST}")
    for want in _vpn_free_country_codes_static():
        if want == _VPN_DEFAULT_COUNTRY:
            continue
        if await _veepn_connect_country_prefer_api(context, eid, want):
            return True
    return await _vpn_is_proxy_active(context, eid)


async def _vpn_fresh_connect_usa(
    context, profile_path=None, *, quick: bool = False,
) -> bool:
    """Перед Flipkart: USA-подключение VeepN; экранная активация сериализована."""
    async with _veepn_screen_guard():
        return await _vpn_fresh_connect_usa_impl(context, profile_path, quick=quick)


async def _vpn_fresh_connect_usa_impl(
    context, profile_path=None, *, quick: bool = False,
) -> bool:
    """Перед Flipkart: если VPN уже жив (US) — не трогать; иначе UI USA.

    Не рвать рабочий VeepN через _vpn_disconnect — это закрывает popup и ломает сценарий.
    """
    if not _vpn_extension_dir():
        return True
    if profile_path is not None and not _profile_allows_vpn(profile_path):
        tag = Path(profile_path).name
        print(f"  {DIM}VPN: нет активного сценария для {tag} — сброс пропущен{RST}")
        return False

    eid = await _vpn_ext_id(context)
    if eid and await _vpn_is_proxy_active(context, eid):
        cc = ""
        with contextlib.suppress(Exception):
            cc = await _veepn_connected_country_hint(context, eid)
        if cc in (_VPN_DEFAULT_COUNTRY, "", "us"):
            print(f"  {G}✔ VPN уже жив ({(cc or 'US').upper()}) — Flipkart без переподключения{RST}")
            with contextlib.suppress(Exception):
                await _veepn_finalize_connected(context, eid, via=(cc or "US").upper())
            return True
        print(f"  {DIM}VPN: жив {(cc or '?').upper()} → UI на USA…{RST}")
    else:
        print(f"  {DIM}VPN: выкл → USA UI (пазл/щит → страна → питание)…{RST}")

    ok = False
    if eid and _vpn_is_veepn():
        with contextlib.suppress(Exception):
            ok = await _veepn_ui_reconnect_country(context, _VPN_DEFAULT_COUNTRY)
    if not ok:
        ok = await _ensure_vpn_connected(context, quick=quick, flipkart=True)
    if not ok:
        print(f"  {Y}⚠ VPN: не удалось включить{RST}")
        return False
    eid = await _vpn_ext_id(context)
    if eid:
        await _close_vpn_extension_tabs(context, eid)
        with contextlib.suppress(Exception):
            await _wait_vpn_proxy_ready(context, eid, timeout=8.0)
    with contextlib.suppress(Exception):
        wp = await _main_work_page(context)
        await wp.bring_to_front()
    print(f"  {G}✔ VPN готов (US) — открываю Flipkart{RST}")
    return True


async def _vpn_toggle_same_country(
    context, profile_path=None, *, quick: bool = True,
) -> bool:
    """Access Denied шаг 2: выкл VPN → вкл снова (та же страна), без смены страны."""
    if not _vpn_extension_dir():
        return False
    if profile_path is not None and not _profile_allows_vpn(profile_path):
        tag = Path(profile_path).name
        print(f"  {DIM}VPN: нет активного сценария для {tag} — toggle пропущен{RST}")
        return False
    eid = await _vpn_ext_id(context)
    print(f"  {Y}⚠ Flipkart Access Denied — VPN выкл → вкл (та же страна)…{RST}")
    await _vpn_disconnect(context)
    await asyncio.sleep(1.5)
    # flipkart=False: иначе ensure сразу утащит на USA и сломает шаг «сначала toggle»
    connected = await _ensure_vpn_connected(context, quick=quick, flipkart=False)
    if not connected:
        return False
    if eid and not await _wait_vpn_proxy_ready(context, eid, timeout=14.0):
        await _wait_vpn_proxy_ready(context, eid, timeout=10.0)
    with contextlib.suppress(Exception):
        wp = await _main_work_page(context)
        await wp.bring_to_front()
    return True


async def _vpn_toggle_reconnect_flipkart(
    context, profile_path=None, *,
    exclude_countries: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """Access Denied: выкл VPN → вкл снова → при неудаче смена страны."""
    if not _vpn_extension_dir():
        return False, ""
    if await _vpn_toggle_same_country(context, profile_path):
        eid = await _vpn_ext_id(context)
        cc = ""
        if eid:
            with contextlib.suppress(Exception):
                cc = await _veepn_connected_country_hint(context, eid) if _vpn_is_veepn() else ""
        return True, cc or ""
    return await _vpn_reconnect_for_flipkart(
        context, profile_path, exclude_countries=exclude_countries,
    )


async def _vpn_reconnect_for_flipkart(
    context, profile_path=None, *,
    exclude_countries: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    """Сменить страну VPN для Flipkart. USA — приоритет (VeepN и VPNLY)."""
    if _vpn_is_veepn():
        return await _veepn_reconnect_for_flipkart(
            context, profile_path, exclude_countries=exclude_countries,
        )
    eid = await _vpn_ext_id(context)
    if not eid:
        return False, ""
    print(f"  {Y}⚠ Flipkart Access Denied — переключаю VPN (USA приоритет)…{RST}")
    await _vpn_disconnect(context)
    await asyncio.sleep(1.2)
    for server in _vpnly_servers_for_flipkart(exclude=exclude_countries):
        cc = _vpnly_country_code(server)
        city = (server.get("city") or {}).get("name") or cc.upper()
        if await _vpnly_enable_server(context, eid, server):
            if await _wait_vpn_proxy_ready(context, eid, timeout=14.0):
                print(f"  {G}✔ VPN: {city} ({cc.upper()}){RST}")
                return True, cc
    return False, ""


async def _flipkart_reload_and_check(page, *, label: str = "") -> tuple[bool, object]:
    """F5 + проверка, что Access Denied / блок снялся."""
    with contextlib.suppress(Exception):
        await page.bring_to_front()
    print(f"  {Y}↻ Flipkart: обновляю страницу (F5){(' — ' + label) if label else ''}…{RST}")
    with contextlib.suppress(Exception):
        await page.reload(wait_until="domcontentloaded", timeout=35_000)
        await page.wait_for_timeout(500)
    if not await _flipkart_page_blocked(page):
        return True, page
    return False, page


async def _flipkart_new_work_tab(context, keep_url: str = "about:blank"):
    """Старая вкладка зависла / chrome-error — открыть новую рабочую."""
    page = await context.new_page()
    with contextlib.suppress(Exception):
        await page.goto(keep_url, wait_until="domcontentloaded", timeout=15_000)
    # Закрыть junk / extension tabs, оставить рабочую
    with contextlib.suppress(Exception):
        await _close_vpn_extension_tabs(context)
        await _close_junk_tabs(context)
    with contextlib.suppress(Exception):
        await page.bring_to_front()
    return page


async def _diagnose_flipkart_state(context, page) -> dict:
    """Самодиагностика: что сломано прямо сейчас (решение по фактам, не по догадкам)."""
    eid = await _vpn_ext_id(context) if _vpn_extension_dir() else None
    proxy = None
    country = ""
    if eid:
        with contextlib.suppress(Exception):
            proxy = await _vpn_is_proxy_active(context, eid)
        if _vpn_is_veepn():
            with contextlib.suppress(Exception):
                country = await _veepn_connected_country_hint(context, eid)
    url = ""
    with contextlib.suppress(Exception):
        url = (page.url or "")[:180]
    title = ""
    body_head = ""
    with contextlib.suppress(Exception):
        title = (await page.title())[:120]
    with contextlib.suppress(Exception):
        body_head = await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 400)"
        )

    kind = "unknown"
    hint = ""
    if not await _flipkart_page_blocked(page):
        # Убедимся что это похоже на Flipkart, а не about:blank
        low_u = (url or "").lower()
        if "flipkart.com" in low_u and "about:blank" not in low_u:
            return {
                "ok": True, "kind": "ok", "proxy": proxy, "country": country,
                "url": url, "hint": "страница открыта",
            }
        if low_u in ("", "about:blank", "chrome://newtab/"):
            kind, hint = "blank", "пустая вкладка"
        else:
            # Возможно ещё грузится
            kind, hint = "unknown", "нет явного блока, но URL странный"

    if await _flipkart_page_access_denied(page):
        kind, hint = "access_denied", "Akamai Access Denied"
    elif url.lower().startswith("chrome-error://") or "chromewebdata" in url.lower():
        kind, hint = "chrome_error", "Chrome error page"
    elif any(x in (body_head or "").lower() for x in (
        "err_connection", "timed out", "can't reach", "не удается получить",
        "не удаётся получить", "превышено время ожидания",
    )):
        # Нет VPN → сначала поднимаем прокси любой free-страной, не F5 в пустоту
        if proxy is False:
            kind, hint = "proxy_dead", "таймаут — VPN выключен"
        else:
            kind, hint = "timeout_page", "таймаут / сайт недоступен"
    elif proxy is False:
        kind, hint = "proxy_dead", "VPN proxy выключен, а сайт не открылся"
    elif proxy is True:
        kind, hint = "proxy_alive_blocked", "VPN есть, но Flipkart всё равно режет"
    else:
        kind, hint = kind or "unknown", hint or "непонятное состояние"

    return {
        "ok": False, "kind": kind, "proxy": proxy, "country": country,
        "url": url, "title": title, "hint": hint,
    }


async def _navigate_flipkart_resilient(
    context, page, url: str, *, label: str = "", profile_path=None,
) -> tuple[bool, object, str]:
    """Автономный заход на Flipkart: сам диагностирует и выбирает следующий шаг.

    Логика (без ожидания подсказок пользователя):
      • VPN мёртв → полный сброс + USA
      • Access Denied / timeout при живом VPN → следующая free-страна
      • chrome-error / blank → новая вкладка + жёсткий goto
      • F5 только один раз на одну и ту же страну
      • крутить очередь стран, пока сайт реально не откроется
    """
    last_err = ""
    exclude: set[str] = set()
    f5_done_for: set[str] = set()
    # Стартуем со статического списка (быстро); VeepN-discover — лениво при recover
    try_order = _vpn_free_country_codes_static()
    discovered = False
    max_rounds = max(10, len(try_order) + 6)
    country_i = 0
    print(f"  {DIM}Flipkart auto-recover · queue: {', '.join(c.upper() for c in try_order)}{RST}")

    async def _enrich_queue() -> None:
        nonlocal try_order, discovered, max_rounds
        if discovered or context is None:
            return
        discovered = True
        extra = await _flipkart_vpn_country_queue(context)
        for cc in extra:
            if cc not in try_order:
                try_order.append(cc)
        max_rounds = max(max_rounds, len(try_order) + 4)

    async def _work_page():
        nonlocal page
        page = await _main_work_page(context)
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        return page

    async def _goto(*, fast: bool = False) -> tuple[bool, str]:
        await _work_page()
        return await _force_navigate_flipkart(page, url, label=label, fast=fast)

    # Старт VPN: живой прокси → сразу Flipkart (не рвём сессию).
    # HTTP-прокси Playwright / direct (skip_vpn) — только goto, VeepN не включаем
    # даже если расширение уже лежит в done-профиле.
    usa_denials = 0
    if _context_skip_vpn(context):
        with contextlib.suppress(Exception):
            if await _vpn_ext_id(context):
                await _vpn_disconnect(context)
        ok_nav, err_nav = await _goto(fast=True)
        diag = await _diagnose_flipkart_state(context, page)
        if diag.get("ok"):
            return True, page, ""
        # ещё 2 попытки без смены страны VPN
        for _ in range(2):
            ok_nav, err_nav = await _goto(fast=False)
            diag = await _diagnose_flipkart_state(context, page)
            if diag.get("ok"):
                return True, page, ""
        return False, page, diag.get("hint") or err_nav or "Flipkart не открылся (прокси/direct)"

    eid0 = await _vpn_ext_id(context) if _vpn_extension_dir() else None
    if not eid0:
        ok_nav, err_nav = await _goto(fast=True)
        diag = await _diagnose_flipkart_state(context, page)
        if diag.get("ok"):
            return True, page, ""
        return False, page, diag.get("hint") or err_nav or "Flipkart не открылся (без VPN)"

    already = bool(await _vpn_is_proxy_active(context, eid0))
    if already:
        cc0 = ""
        if _vpn_is_veepn():
            with contextlib.suppress(Exception):
                cc0 = await _veepn_connected_country_hint(context, eid0)
        print(
            f"  {G}✔ VPN жив ({(cc0 or '?').upper()}) — Flipkart{RST}"
        )
        await _dismiss_all_veepn_welcome(context)
        await _close_vpn_extension_tabs(context, eid0)
        await _work_page()
    else:
        print(f"  {DIM}VPN выкл — включаю…{RST}")
        if not await _ensure_vpn_connected(context, quick=True, flipkart=True):
            print(f"  {DIM}VPN ещё поднимается — авто-перебор на сайте{RST}")
        await _dismiss_all_veepn_welcome(context)
        await _close_vpn_extension_tabs(context, await _vpn_ext_id(context))
        await _work_page()

    for round_n in range(1, max_rounds + 1):
        ok_nav, err_nav = await _goto(fast=(round_n == 1))
        diag = await _diagnose_flipkart_state(context, page)
        print(
            f"  {DIM}🤖 [{round_n}/{max_rounds}] {diag['kind']}"
            f" · proxy={diag['proxy']} · cc={(diag['country'] or '?').upper()}"
            f" · {diag['hint']}{RST}"
        )

        if diag["ok"]:
            print(
                f"  {G}✔ Flipkart OK — VPN не трогаем, "
                f"продолжаем сценарий (приложение / Telegram){RST}"
            )
            return True, page, ""

        if ok_nav is False and err_nav and not _is_flipkart_nav_error(err_nav):
            return False, page, err_nav

        last_err = diag["hint"] or err_nav or diag["kind"]
        if not _vpn_extension_dir():
            return False, page, last_err

        cur_cc = _vpn_normalize_cc(diag.get("country") or "") or (
            try_order[min(country_i, len(try_order) - 1)] if try_order else _VPN_DEFAULT_COUNTRY
        )
        kind = diag["kind"]

        # ── Решение по диагнозу ────────────────────────────────────────────
        if kind in ("blank", "chrome_error"):
            print(f"  {DIM}пустая страница → новая вкладка{RST}")
            page = await _flipkart_new_work_tab(context)
            continue

        if kind == "proxy_dead":
            print(f"  {DIM}VPN нет → включаю…{RST}")
            await _ensure_vpn_connected(context, quick=True, flipkart=True)
            await _dismiss_all_veepn_welcome(context)
            await _close_vpn_extension_tabs(context, await _vpn_ext_id(context))
            continue

        # Таймаут при живом VPN → F5 один раз, потом смена страны
        if kind == "timeout_page" and diag.get("proxy") is not True:
            print(f"  {DIM}таймаут без VPN → включаю…{RST}")
            await _ensure_vpn_connected(context, quick=True, flipkart=True)
            await _dismiss_all_veepn_welcome(context)
            await _close_vpn_extension_tabs(context, await _vpn_ext_id(context))
            continue

        # Access Denied / ERR_TIMED_OUT при живом VPN → UI-смена страны
        # (клик по названию → скролл → US+штат или другая free → питание).
        if kind in ("access_denied", "proxy_alive_blocked", "timeout_page") and diag.get("proxy") is True:
            if cur_cc and cur_cc != _VPN_DEFAULT_COUNTRY:
                exclude.add(cur_cc)
            next_cc = None
            # USA: до 3 попыток (разные штаты), даже если только что был US
            if usa_denials < 3:
                next_cc = _VPN_DEFAULT_COUNTRY
                usa_denials += 1
                print(
                    f"  {Y}🤖 {kind} → UI смена на USA "
                    f"({usa_denials}/3, скролл + штат)…{RST}"
                )
            else:
                if cur_cc == _VPN_DEFAULT_COUNTRY:
                    exclude.add(cur_cc)
                for cc in try_order:
                    if cc not in exclude:
                        next_cc = cc
                        break
                if next_cc is None:
                    await _enrich_queue()
                    for cc in try_order:
                        if cc not in exclude:
                            next_cc = cc
                            break
                if next_cc is None:
                    print(f"  {Y}🤖 {kind} — страны закончились{RST}")
                    break
                print(
                    f"  {Y}🤖 {kind} → UI смена "
                    f"{(cur_cc or '?').upper()} → {next_cc.upper()}{RST}"
                )
            # Сначала API (USA), UI — если API не выбрал; для других стран то же
            ok_sw = False
            if _vpn_is_veepn():
                eid_sw = await _vpn_ext_id(context)
                if eid_sw:
                    ok_sw = await _veepn_connect_country_prefer_api(context, eid_sw, next_cc)
            if not ok_sw:
                ok_sw = await _vpn_connect_country(context, next_cc, profile_path)
            if not ok_sw:
                ok_sw = await _veepn_ui_reconnect_country(context, next_cc)
            if next_cc != _VPN_DEFAULT_COUNTRY:
                exclude.add(next_cc)
            if next_cc in try_order:
                country_i = try_order.index(next_cc) + 1
            await _dismiss_all_veepn_welcome(context)
            await _close_vpn_extension_tabs(context, await _vpn_ext_id(context))
            page = await _flipkart_new_work_tab(context)
            continue

        # Один F5 на текущую страну (только unknown без живого timeout выше)
        if cur_cc not in f5_done_for and kind in ("unknown",):
            f5_done_for.add(cur_cc)
            print(f"  {Y}🤖 Решение: F5 (ещё не пробовали на {cur_cc.upper()}){RST}")
            cleared, page = await _flipkart_reload_and_check(page, label=cur_cc.upper())
            if cleared:
                return True, page, ""

        # После F5 / прочего — сменить страну через UI
        if cur_cc:
            exclude.add(cur_cc)

        next_cc = None
        for cc in try_order:
            if cc not in exclude:
                next_cc = cc
                break
        if next_cc is None:
            await _enrich_queue()
            for cc in try_order:
                if cc not in exclude:
                    next_cc = cc
                    break
        if next_cc is None:
            if round_n < max_rounds // 2:
                print(f"  {Y}🤖 Очередь стран исчерпана — второй круг с USA{RST}")
                exclude.clear()
                f5_done_for.clear()
                next_cc = _VPN_DEFAULT_COUNTRY
            else:
                break

        print(f"  {DIM}смена VPN → {next_cc.upper()}…{RST}")
        ok_sw = False
        if _vpn_is_veepn():
            eid_sw = await _vpn_ext_id(context)
            if eid_sw:
                ok_sw = await _veepn_connect_country_prefer_api(context, eid_sw, next_cc)
        if not ok_sw:
            ok_sw = await _vpn_connect_country(context, next_cc, profile_path)
        if not ok_sw:
            ok_sw = await _veepn_ui_reconnect_country(context, next_cc)
        exclude.add(next_cc)
        if next_cc in try_order:
            country_i = try_order.index(next_cc) + 1 if next_cc in try_order else country_i + 1
        if not ok_sw and diag.get("proxy") is False:
            await _vpn_fresh_connect_usa(context, profile_path, quick=True)
        await _dismiss_all_veepn_welcome(context)
        await _close_vpn_extension_tabs(context, await _vpn_ext_id(context))
        page = await _flipkart_new_work_tab(context)

    diag = await _diagnose_flipkart_state(context, page)
    if diag["ok"]:
        return True, page, ""
    return False, page, last_err or diag.get("hint") or "Flipkart не открылся"


async def _vpn_ext_id(context) -> str | None:
    """ID реально загруженного VPN-расширения в этом браузере (не только из manifest.key)."""

    def _scan() -> str | None:
        try:
            for sw in list(getattr(context, "service_workers", []) or []):
                u = sw.url or ""
                if u.startswith("chrome-extension://"):
                    return u.split("/")[2]
        except Exception:
            pass
        try:
            for bp in getattr(context, "background_pages", []) or []:
                u = bp.url or ""
                if u.startswith("chrome-extension://"):
                    return u.split("/")[2]
        except Exception:
            pass
        try:
            for p in context.pages:
                u = p.url or ""
                if u.startswith("chrome-extension://"):
                    return u.split("/")[2]
        except Exception:
            pass
        return None

    found = _scan()
    if found:
        return found
    try:
        sw = await context.wait_for_event("serviceworker", timeout=8_000)
        return (sw.url or "").split("/")[2]
    except Exception:
        pass
    if _vpn_is_veepn():
        return _vpn_ext_id_from_path()
    return _vpn_ext_id_from_key()


def _veepn_prep_storage_js() -> str:
    """JS: отключить onboarding, включить auto-connect в storage VeepN."""
    return """async () => {
        const now = Date.now();
        const all = await chrome.storage.local.get([
            'app', 'global-state', 'connection', 'intro-offer',
        ]);
        const app = (all.app && typeof all.app === 'object') ? all.app : {};
        const gs = (all['global-state'] && typeof all['global-state'] === 'object')
            ? all['global-state'] : {};
        const conn = (all.connection && typeof all.connection === 'object')
            ? all.connection : {};
        const intro = (all['intro-offer'] && typeof all['intro-offer'] === 'object')
            ? all['intro-offer'] : {};
        await chrome.storage.local.set({
            app: { ...app, showOnboarding: false },
            'global-state': { ...gs, installedAt: gs.installedAt || now },
            connection: { ...conn, useAutoConnect: true },
            'intro-offer': { ...intro, hasVisitedIntroOffer: true },
        });
        const msg = async (type, data = {}) => {
            try { return await chrome.runtime.sendMessage({ type, data }); }
            catch (e) { return null; }
        };
        await msg('close-onboarding', {});
        await msg('set-auto-connect-setting', { status: true });
        return true;
    }"""


def _veepn_clear_autoconnect_js() -> str:
    """JS: отключить auto-connect VeepN (VPN не поднимается сам после disconnect)."""
    return """async () => {
        const all = await chrome.storage.local.get(['connection']);
        const conn = (all.connection && typeof all.connection === 'object')
            ? all.connection : {};
        await chrome.storage.local.set({
            connection: { ...conn, useAutoConnect: false },
        });
        try {
            await chrome.runtime.sendMessage({
                type: 'set-auto-connect-setting',
                data: { status: false },
            });
        } catch (e) {}
        return true;
    }"""


async def _veepn_clear_autoconnect(context, eid: str) -> None:
    _js = _veepn_clear_autoconnect_js()
    sw = await _wait_vpn_service_worker(context, eid, timeout=6.0)
    if sw:
        with contextlib.suppress(Exception):
            await sw.evaluate(_js)
            return
    page = None
    try:
        page = await context.new_page()
        await page.goto(
            f"chrome-extension://{eid}/src/popup/popup.html",
            wait_until="domcontentloaded", timeout=12_000,
        )
        await page.evaluate(_js)
    except Exception:
        pass
    finally:
        if page:
            with contextlib.suppress(Exception):
                await page.close()


async def _veepn_connection_label(page) -> str:
    """'on' | 'off' | 'unknown'. Только явный статус — не рекламное «подключено»."""
    try:
        return str(await page.evaluate("""() => {
            const body = (document.body?.innerText || '').replace(/\\s+/g, ' ');
            const low = body.toLowerCase();
            // ВЫКЛ проверяем раньше — иначе ложный on рвёт открытый UI
            if (/соединение\\s*выкл/i.test(low) || /connection\\s*off/i.test(low)) return 'off';
            // Успех: «Подключено 00:00:57» / «Соединение ВКЛ»
            if (/подключено\\s*\\d{1,2}:\\d{2}/i.test(low) || /connected\\s*\\d{1,2}:\\d{2}/i.test(low)) return 'on';
            if (/соединение\\s*вкл/i.test(low) || /connection\\s*on/i.test(low)) return 'on';
            return 'unknown';
        }"""))
    except Exception:
        return "unknown"


async def _veepn_dismiss_rate_us(page) -> bool:
    """Модалка «Нравится VeePN? Оцените нас.» → крестик справа вверху блока."""
    try:
        clicked = await page.evaluate("""() => {
            const markers = [
                'нравится veepn', 'оцените нас',
                'like veepn', 'rate us', 'rate veepn',
            ];
            const body = (document.body?.innerText || '').toLowerCase();
            if (!markers.some(m => body.includes(m))) return {ok: false};

            const isCloseLooks = (el, t) => {
                const al = ((el.getAttribute('aria-label') || '')
                    + ' ' + (el.getAttribute('title') || '')).toLowerCase();
                if (/close|закрыть|dismiss|скрыть/.test(al)) return true;
                if (/^[×x✕✖✖️]$/i.test(t)) return true;
                if (el.querySelector && el.querySelector('svg') && t.length <= 2) {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.width <= 48 && r.height <= 48;
                }
                return false;
            };

            let card = null;
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
                const raw = (walker.currentNode.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!raw) continue;
                const low = raw.toLowerCase();
                if (!markers.some(m => low.includes(m))) continue;
                let el = walker.currentNode.parentElement;
                for (let i = 0; i < 10 && el; i++) {
                    const r = el.getBoundingClientRect();
                    if (r.width >= 180 && r.height >= 80 && r.width < window.innerWidth * 0.98) {
                        card = el; break;
                    }
                    el = el.parentElement;
                }
                if (card) break;
            }
            if (!card) return {ok: false};

            const cr = card.getBoundingClientRect();
            let best = null, bestScore = -1e9;
            for (const el of card.querySelectorAll(
                'button, a, [role="button"], span, div, i, svg'
            )) {
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '').trim();
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8 || r.width > 56 || r.height > 56) continue;
                const nearRight = r.left >= cr.right - 64;
                const nearTop = r.top <= cr.top + 56;
                if (!nearRight || !nearTop) continue;
                if (!isCloseLooks(el, t) && t.length > 2) continue;
                const score = (cr.right - r.right) * -2 - (r.top - cr.top)
                    + (isCloseLooks(el, t) ? 50 : 0);
                if (score > bestScore) { bestScore = score; best = el; }
            }
            if (best) {
                const clickEl = best.closest('button, a, [role="button"]') || best;
                clickEl.click();
                return {ok: true, via: 'el'};
            }
            // Координаты крестика — правый верх белого блока (Playwright mouse fallback)
            return {
                ok: false,
                x: Math.floor(cr.right - 18),
                y: Math.floor(cr.top + 16),
            };
        }""")
        if isinstance(clicked, dict) and clicked.get("ok"):
            print(f"  {G}✔ VeepN: закрыл «Оцените нас» (крестик){RST}")
            await page.wait_for_timeout(600)
            return True
        if isinstance(clicked, dict) and clicked.get("x") is not None:
            await page.mouse.click(float(clicked["x"]), float(clicked["y"]))
            print(f"  {G}✔ VeepN: закрыл «Оцените нас» (точка крестика){RST}")
            await page.wait_for_timeout(600)
            return True
    except Exception:
        pass
    try:
        rate = page.get_by_text(
            re.compile(r"Оцените\s*нас|Rate\s*us|Нравится\s*VeePN", re.I)
        ).first
        if await rate.count() > 0 and await rate.is_visible():
            # aria/close рядом с модалкой
            for sel in (
                '[aria-label*="close" i]',
                '[aria-label*="закрыть" i]',
                'button:has-text("×")',
                'button:has-text("✕")',
            ):
                with contextlib.suppress(Exception):
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=2000)
                        print(f"  {G}✔ VeepN: закрыл «Оцените нас» (aria){RST}")
                        await page.wait_for_timeout(600)
                        return True
    except Exception:
        pass
    return False


async def _veepn_dismiss_limited_upsell(page) -> bool:
    """После ВКЛ: экран «устройство под угрозой» → «Нет, спасибо, я рискну…»."""
    await _veepn_dismiss_rate_us(page)
    try:
        clicked = await page.evaluate("""() => {
            const want = [
                'нет, спасибо, я рискну с ограниченной защитой',
                'нет, спасибо',
                'no, thanks',
                "i'll risk",
                'risk it with limited',
                'продолжить с ограниченной',
            ];
            const body = (document.body?.innerText || '').toLowerCase();
            const isUpsell = body.includes('под угрозой') || body.includes('at risk')
                || body.includes('защитить устройство') || body.includes('protect your device')
                || body.includes('только свой браузер');
            if (!isUpsell && !want.some(w => body.includes(w))) return false;
            for (const el of document.querySelectorAll('a, button, [role="button"], span, div, p')) {
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                if (!t) continue;
                if (!want.some(w => t.includes(w) || t === w)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 8) continue;
                el.click();
                return true;
            }
            return false;
        }""")
        if clicked:
            print(f"  {G}✔ VeepN: нажал «Нет, спасибо…»{RST}")
            await page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    # Playwright text locator fallback
    for pat in (
        r"Нет,\s*спасибо,\s*я\s*рискну",
        r"Нет,\s*спасибо",
        r"risk.*limited",
    ):
        try:
            loc = page.get_by_text(re.compile(pat, re.I)).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=3000)
                print(f"  {G}✔ VeepN: нажал «Нет, спасибо…» (locator){RST}")
                await page.wait_for_timeout(1200)
                return True
        except Exception:
            pass
    return False


async def _veepn_popup_shows_connected(page) -> bool:
    return (await _veepn_connection_label(page)) == "on"


async def _veepn_ui_click_power(page, context=None) -> dict:
    """Клик по круглой кнопке питания: DOM + mouse; fallback — Win32 по экрану."""
    await _veepn_dismiss_rate_us(page)
    label = await _veepn_connection_label(page)

    # 1) Явный круг над текстом статуса — click() в DOM (надёжнее mouse на popup)
    try:
        info = await page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let statusEl = null;
            while (walker.nextNode()) {
                const t = (walker.currentNode.textContent || '').replace(/\\s+/g, ' ').trim();
                if (/подключено\\s*\\d|соединение\\s*выкл|соединение\\s*вкл|connection\\s*off|connection\\s*on|connected\\s*\\d/i.test(t)) {
                    statusEl = walker.currentNode.parentElement;
                    break;
                }
            }
            const sy = statusEl ? statusEl.getBoundingClientRect().top : window.innerHeight * 0.42;
            const cx = window.innerWidth / 2;
            let bestEl = null, best = null, bestScore = -1e9;
            for (const el of document.querySelectorAll('button, [role="button"], div, span, a')) {
                const r = el.getBoundingClientRect();
                if (r.width < 56 || r.height < 56) continue;
                if (Math.abs(r.width - r.height) > 32) continue;
                if (r.bottom > sy - 2) continue;
                if (r.top < 8) continue;
                const mid = Math.abs((r.left + r.width / 2) - cx);
                const score = r.width * r.height - mid * 30 - Math.abs(sy - r.bottom) * 2;
                if (score > bestScore) {
                    bestScore = score;
                    bestEl = el;
                    best = {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width};
                }
            }
            if (bestEl) {
                try { bestEl.click(); } catch (e) {}
                try {
                    bestEl.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                } catch (e) {}
            }
            return best;
        }""")
        if info and info.get("x"):
            with contextlib.suppress(Exception):
                await page.mouse.click(float(info["x"]), float(info["y"]))
            print(f"  {DIM}VeepN UI: круг питания @ {int(info['x'])},{int(info['y'])}{RST}")
            # Win32 добить — реальный action-popup часто не принимает Playwright mouse
            hwnd = await asyncio.to_thread(_win_chrome_main_hwnd)
            if hwnd:
                await asyncio.sleep(0.25)
                clicked_win = await asyncio.to_thread(_win_click_veepn_power, hwnd)
                if clicked_win:
                    return {
                        "clicked": True, "already": False, "was": label,
                        "via": "circle-dom+win32", "x": int(info["x"]), "y": int(info["y"]),
                    }
            return {
                "clicked": True, "already": False, "was": label,
                "via": "circle-dom+mouse", "x": int(info["x"]), "y": int(info["y"]),
            }
    except Exception as e:
        err = str(e)[:80]
    else:
        err = ""

    # 2) Только Win32
    hwnd = await asyncio.to_thread(_win_chrome_main_hwnd)
    if hwnd and await asyncio.to_thread(_win_click_veepn_power, hwnd):
        return {"clicked": True, "already": False, "was": label, "via": "win32-only"}

    # 3) Fallback: клик выше статуса
    for pat in (
        r"Подключено\s*\d|Соединение\s*ВКЛ|Connected\s*\d",
        r"Соединение\s*ВЫКЛ|Connection\s*OFF",
    ):
        try:
            loc = page.get_by_text(re.compile(pat, re.I)).first
            if await loc.count() > 0:
                box = await loc.bounding_box()
                if box:
                    x = box["x"] + box["width"] / 2
                    y = max(40.0, box["y"] - 90.0)
                    await page.mouse.click(x, y)
                    print(f"  {DIM}VeepN UI: круг над статусом @ {int(x)},{int(y)}{RST}")
                    return {
                        "clicked": True, "already": False, "was": label,
                        "via": "above-status", "x": int(x), "y": int(y),
                    }
        except Exception:
            pass
    return {"clicked": False, "already": False, "was": label, "error": err or "no-circle"}


async def _veepn_wait_until_off(page, *, seconds: float = 25.0) -> bool:
    """После отключения ждём «Соединение ВЫКЛ»."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (await _veepn_connection_label(page)) == "off":
            print(f"  {G}✔ VeepN: Соединение ВЫКЛ{RST}")
            return True
        await page.wait_for_timeout(400)
    st = await _veepn_connection_label(page)
    print(f"  {DIM}VeepN: статус ещё не ВЫКЛ ({st}) — продолжаем{RST}")
    return st == "off"


async def _veepn_wait_until_on(page, *, seconds: float = 45.0) -> bool:
    """После клика: upsell «Нет, спасибо…» → ждём «Подключено» / ВКЛ."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await _veepn_dismiss_limited_upsell(page)
        st = await _veepn_connection_label(page)
        if st == "on":
            print(f"  {G}✔ VeepN: Подключено (ВКЛ){RST}")
            return True
        await page.wait_for_timeout(500)
    st = await _veepn_connection_label(page)
    print(f"  {DIM}VeepN: ещё не Подключено ({st}){RST}")
    return False


async def _veepn_finalize_connected(context, eid: str, *, via: str = "popup") -> None:
    """VPN включён — закрыть popup/расширение и оставить рабочую вкладку для Flipkart."""
    await _veepn_set_autoconnect(context, eid)
    await _dismiss_all_veepn_welcome(context)
    await _close_vpn_extension_tabs(context, eid)
    with contextlib.suppress(Exception):
        wp = await _main_work_page(context)
        await _close_extra_blank_tabs(context, keep=wp)
        await wp.bring_to_front()
    print(f"  {G}✔ VeepN подключён ({via}){RST}")


async def _veepn_dismiss_onboarding(page) -> None:
    """Закрыть welcome/onboarding/pricing VeepN («Продолжить», план подписки)."""
    await _veepn_dismiss_rate_us(page)
    _js = """async () => {
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const msg = async (type, data = {}) => {
            try { return await chrome.runtime.sendMessage({ type, data }); }
            catch (e) { return null; }
        };
        await msg('close-onboarding', {});
        const want = ['продолжить', 'continue', 'continue limited', 'continue without',
                      'продолжить →', 'продолжение ограничено', 'weiter', 'continuer',
                      'start free', 'use free', 'бесплатн'];
        const isWelcome = () => {
            const b = (document.body?.innerText || '').toLowerCase();
            const t = (document.title || '').toLowerCase();
            const u = (location.href || '').toLowerCase();
            return b.includes('thank you for installing') || t.includes('thank you for installing')
                || b.includes('добро пожаловать') || b.includes('welcome to')
                || b.includes('welcome to veepn') || b.includes('что вы получите')
                || b.includes('выберите план') || b.includes('choose a subscription')
                || b.includes('subscription plan') || u.includes('/welcome')
                || u.includes('onboarding');
        };
        for (let round = 0; round < 6; round++) {
            if (!isWelcome()) break;
            for (const el of document.querySelectorAll(
                'button, a, .base-button, [role="button"]')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!want.some(w => t === w || t.includes(w) || t.startsWith(w))) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 30 || el.offsetParent === null) continue;
                el.click();
                await sleep(700);
            }
            await msg('close-onboarding', {});
            await sleep(500);
        }
        return !isWelcome();
    }"""
    try:
        await page.evaluate(_js)
    except Exception:
        pass
    for _label in (
        "Продолжить", "Continue", "Continue limited", "Continue without a plan",
        "Продолжение ограничено", "Get started", "Get Started", "Start", "OK", "Got it",
        "Start Free", "Use Free",
    ):
        try:
            _loc = page.get_by_role("button", name=_label).first
            if await _loc.count() > 0 and await _loc.is_visible():
                await _loc.click(timeout=2_000)
                await page.wait_for_timeout(600)
        except Exception:
            pass


async def _veepn_set_autoconnect(context, eid: str) -> None:
    """Включает Auto-Connect в VeepN (при старте браузера подключится сам)."""
    _js = _veepn_prep_storage_js()
    sw = await _wait_vpn_service_worker(context, eid, timeout=10.0)
    if sw:
        try:
            await sw.evaluate(_js)
            return
        except Exception:
            pass
    page = None
    try:
        page = await context.new_page()
        await page.goto(
            f"chrome-extension://{eid}/src/popup/popup.html",
            wait_until="domcontentloaded", timeout=15_000,
        )
        await page.evaluate(_js)
    except Exception:
        pass
    finally:
        if page:
            with contextlib.suppress(Exception):
                await page.close()


def _veepn_location_pick_js(country_code: str = "us") -> str:
    """JS-фрагмент: выбрать сервер VeepN по коду страны (us/fr/de/nl)."""
    cc = country_code.lower()
    iso, name_pat = _VEEPN_CC_PATTERNS.get(cc, (cc.upper(), cc))
    iso_js = json.dumps(iso)
    return f"""
        const wantIso = {iso_js};
        const nameRe = /{name_pat}/i;
        const pickCountryId = (node, depth = 0) => {{
            if (!node || depth > 14) return null;
            if (typeof node === 'string') {{
                const s = node.toLowerCase();
                if (s === wantIso.toLowerCase() || nameRe.test(s)) return node;
                return null;
            }}
            if (Array.isArray(node)) {{
                for (const x of node) {{
                    const r = pickCountryId(x, depth + 1);
                    if (r) return r;
                }}
                return null;
            }}
            if (typeof node === 'object') {{
                const name = String(
                    node.name || node.city || node.title || node.label || ''
                ).toLowerCase();
                const code = String(
                    node.code || node.countryCode || node.country || ''
                ).toUpperCase();
                if (code === wantIso || nameRe.test(name)) {{
                    return node.id || node.uuid || node.locationId || null;
                }}
                for (const v of Object.values(node)) {{
                    const r = pickCountryId(v, depth + 1);
                    if (r) return r;
                }}
            }}
            return null;
        }};
        let locId = null;
        try {{
            const stored = await chrome.storage.local.get(null);
            locId = pickCountryId(stored);
        }} catch (e) {{}}
        if (!locId) {{
            try {{
                const gl = await msg('get-locations', {{}});
                locId = pickCountryId(gl?.data ?? gl);
            }} catch (e) {{}}
        }}
        await msg('set-active-location', locId || 'optimal');
    """


def _veepn_india_pick_js() -> str:
    """Совместимость: USA вместо India (в VeepN нет IN)."""
    return _veepn_location_pick_js("us")


def _veepn_connect_js(*, loops: int = 14, sleep_ms: int = 2500, force: bool = False) -> str:
    location_pick = _veepn_location_pick_js("us")
    early = "false" if force else "true"
    return f"""async () => {{
        const sleep = ms => new Promise(r => setTimeout(r, ms));
        const msg = async (type, data = {{}}) => {{
            try {{ return await chrome.runtime.sendMessage({{ type, data }}); }}
            catch (e) {{ return {{ success: false, error: String(e) }}; }}
        }};
        const proxyOk = async () => {{
            const mode = await chrome.proxy.settings.get({{}});
            const m = (mode?.value?.mode) || 'direct';
            return m && m !== 'direct' && m !== 'system' && m !== 'auto_detect';
        }};
        await msg('close-onboarding', {{}});
        let st = await msg('get-connection-state', {{}});
        // force=true: не считаем «уже connected» успехом — нужен свежий connect (новый таймер)
        if ({early} && (st?.data?.status === 'connected' || await proxyOk())) return {{ ok: true }};
        if (!{early}) {{
            await msg('disconnect', {{}});
            await sleep(900);
            try {{
                const all = await chrome.storage.local.get(['connection']);
                const conn = (all.connection && typeof all.connection === 'object') ? all.connection : {{}};
                await chrome.storage.local.set({{ connection: {{ ...conn, connectedAt: 0 }} }});
            }} catch (e) {{}}
        }}
        await msg('update-locations-data', {{}});
        await sleep({min(sleep_ms, 2500)});
        {location_pick}
        await sleep(800);
        for (let i = 0; i < {loops}; i++) {{
            st = await msg('get-connection-state', {{}});
            if (st?.data?.status === 'connected' || await proxyOk()) return {{ ok: true }};
            await msg('connect', {{}});
            await sleep({sleep_ms});
            if (await proxyOk()) return {{ ok: true }};
            st = await msg('get-connection-state', {{}});
            if (st?.data?.status === 'connected') return {{ ok: true }};
        }}
        return {{ ok: false }};
    }}"""


def _veepn_popup_rel_paths() -> list[str]:
    """Реальные пути popup VeepN (корневого popup.html НЕТ → ERR_BLOCKED_BY_CLIENT)."""
    return ["src/popup/popup.html"]


def _vpn_popup_rel_paths() -> list[str]:
    """Пути popup для текущего VPN-провайдера."""
    if _vpn_is_veepn():
        return _veepn_popup_rel_paths()
    return ["popup.html", "src/popup/popup.html", "index.html"]


def _page_shows_client_block(url: str = "", title: str = "", body: str = "") -> bool:
    """Chrome interstitial ERR_BLOCKED_BY_CLIENT / «Сайт … заблокирован»."""
    blob = f"{url} {title} {body}".lower()
    return (
        "err_blocked_by_client" in blob
        or "chrome-error://" in blob
        or "chromewebdata" in blob
        or "заблокирован" in blob
        or "blocked by client" in blob
        or "this page has been blocked" in blob
    )


def _is_extension_popup_url(url: str, eid: str) -> bool:
    u = (url or "").lower()
    eid_l = (eid or "").lower()
    if not eid_l or f"chrome-extension://{eid_l}" not in u:
        return False
    if "chromewebdata" in u or "chrome-error" in u:
        return False
    if any(x in u for x in ("/welcome", "welcome/", "onboarding", "/install", "options.html", "background")):
        return False
    # Только реальный UI popup (не несуществующий /popup.html у VeepN)
    if _vpn_is_veepn():
        return "/src/popup/" in u or u.rstrip("/").endswith("/src/popup/popup.html")
    return (
        "popup" in u
        or u.rstrip("/").endswith("/index.html")
        or "/src/popup" in u
    )


async def _veepn_popup_ui_ready(page) -> bool:
    """На экране есть управление VPN («Соединение…» / локация), не пустой синий фон."""
    try:
        return bool(await page.evaluate("""() => {
            const b = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
            if (b.length < 12) return false;
            const low = b.toLowerCase();
            return /соединение|подключено|connection|connected|netherlands|united states|amsterdam|oregon|france|germany|локации|locations|adblock|список обхода|premium/i.test(low);
        }"""))
    except Exception:
        return False


async def _veepn_popup_is_blank(page) -> bool:
    """Пустой синий popup.html — VPN уже ВКЛ; нужен выкл → открыть снова."""
    if not page:
        return True
    with contextlib.suppress(Exception):
        if await _veepn_popup_ui_ready(page):
            return False
    with contextlib.suppress(Exception):
        info = await page.evaluate("""() => {
            const b = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
            const btns = document.querySelectorAll('button, [role="button"]').length;
            return { len: b.length, btns };
        }""")
        if isinstance(info, dict) and int(info.get("len") or 0) < 20 and int(info.get("btns") or 0) < 2:
            return True
    return not await _veepn_popup_ui_ready(page)


_AUTOMATION_CHROME_HWND = 0  # HWND текущего Playwright Chrome (не «любое» окно)


def _win_chrome_main_hwnd() -> int:
    """HWND Chrome: сначала привязанный к автоматизации, иначе самое большое окно."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    global _AUTOMATION_CHROME_HWND
    bound = int(_AUTOMATION_CHROME_HWND or 0)
    if bound and user32.IsWindow(bound) and user32.IsWindowVisible(bound):
        return bound

    best = {"hwnd": 0, "area": 0}
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "chrome_widgetwin_1" not in cls.value.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 400 or h < 300:
            return True
        area = w * h
        if area > best["area"]:
            best["hwnd"] = int(hwnd)
            best["area"] = area
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return int(best["hwnd"] or 0)


def _win_bind_automation_hwnd() -> int:
    """После bring_to_front Playwright — запомнить HWND, чтобы не кликать чужой Chrome."""
    if os.name != "nt":
        return 0
    import ctypes
    global _AUTOMATION_CHROME_HWND
    user32 = ctypes.windll.user32
    fg = int(user32.GetForegroundWindow() or 0)
    if not fg:
        return _win_chrome_main_hwnd()
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(fg, cls, 256)
    if "chrome_widgetwin_1" in cls.value.lower():
        _AUTOMATION_CHROME_HWND = fg
        return fg
    hwnd = _win_chrome_main_hwnd()
    _AUTOMATION_CHROME_HWND = hwnd
    return hwnd


def _win_dpi_aware_once() -> None:
    """Без DPI-aware SetCursorPos и GetWindowRect расходятся на 125%/150% — клики мимо пазла."""
    if os.name != "nt":
        return
    if getattr(_win_dpi_aware_once, "_done", False):
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        with contextlib.suppress(Exception):
            ctypes.windll.user32.SetProcessDPIAware()
    _win_dpi_aware_once._done = True  # type: ignore[attr-defined]


def _win_ensure_chrome_maximized() -> int:
    """Chrome на весь экран (полноширинный maximized). HWND или 0.

    Обязательно перед кликами по пазлу «Расширения»: иначе координаты
    toolbar считаются по узкому окну и промахиваются.
    """
    if os.name != "nt":
        return 0
    import ctypes

    _win_dpi_aware_once()
    hwnd = _win_chrome_main_hwnd()
    if not hwnd:
        return 0
    user32 = ctypes.windll.user32
    # Свернутое → восстановить, затем maximize
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.3)
    if not user32.IsZoomed(hwnd):
        print(f"  {DIM}VeepN: Chrome не на весь экран — разворачиваю…{RST}")
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        time.sleep(0.5)
    else:
        time.sleep(0.15)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.35)
    if not user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, 3)
        time.sleep(0.45)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    return hwnd


def _win_chrome_dpi_scale(hwnd: int) -> float:
    """DPI scale окна (1.0 = 96 DPI)."""
    if not hwnd:
        return 1.0
    import ctypes
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
        if dpi > 0:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


def _chrome_puzzle_icon_path() -> Path:
    return _HERE / "assets" / "chrome_puzzle_icon.png"


def _win_grab_chrome_image(hwnd: int):
    """Скриншот окна Chrome + origin (left, top) в экранных координатах."""
    if os.name != "nt" or not hwnd:
        return None, (0, 0)
    import ctypes
    from ctypes import wintypes
    from PIL import ImageGrab

    _win_dpi_aware_once()
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    bbox = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    try:
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        img = ImageGrab.grab(bbox=bbox)
    return img, (bbox[0], bbox[1])


def _win_find_chrome_puzzle_icon(hwnd: int) -> tuple[int, int] | None:
    """Найти иконку пазла «Расширения» на скрине toolbar (шаблон assets/chrome_puzzle_icon.png).

    Возвращает экранные (x, y) центра иконки или None.
    """
    if os.name != "nt" or not hwnd:
        return None
    from PIL import Image

    icon_path = _chrome_puzzle_icon_path()
    if not icon_path.is_file():
        print(f"  {DIM}VeepN: нет шаблона пазла {icon_path.name}{RST}")
        return None

    hay, (ox, oy) = _win_grab_chrome_image(hwnd)
    if hay is None:
        return None
    w, h = hay.size
    # Ищем только вверху справа — там toolbar
    crop_l = max(0, w - 480)
    crop_t = 0
    crop_b = min(h, 120)
    region = hay.crop((crop_l, crop_t, w, crop_b)).convert("RGB")
    rw, rh = region.size
    tmpl0 = Image.open(icon_path).convert("RGBA")

    best = {"score": 1e18, "x": 0, "y": 0, "tw": 0, "th": 0}
    # step 2 — достаточно для иконки ~20–40px
    for scale in (0.7, 0.85, 1.0, 1.15, 1.3, 1.5):
        tw = max(14, int(tmpl0.width * scale))
        th = max(14, int(tmpl0.height * scale))
        if tw >= rw or th >= rh:
            continue
        tip = tmpl0.resize((tw, th), Image.Resampling.LANCZOS)
        tip_rgb = tip.convert("RGB")
        alpha = tip.split()[-1]
        # Маска: только непрозрачные пиксели пазла
        mask_pts = [
            (i, px)
            for i, px in enumerate(alpha.getdata())
            if px > 40
        ]
        if len(mask_pts) < 30:
            continue
        # Прореживание маски для скорости
        mask_pts = mask_pts[::2]
        tip_data = list(tip_rgb.getdata())
        reg_data = region.load()

        for y in range(0, rh - th, 2):
            for x in range(0, rw - tw, 2):
                sad = 0
                n = 0
                for idx, _a in mask_pts:
                    tx = idx % tw
                    ty = idx // tw
                    pr, pg, pb = tip_data[idx]
                    rr, rg, rb = reg_data[x + tx, y + ty]
                    sad += abs(pr - rr) + abs(pg - rg) + abs(pb - rb)
                    n += 1
                if n <= 0:
                    continue
                score = sad / n
                if score < best["score"]:
                    best["score"] = score
                    best["x"] = x
                    best["y"] = y
                    best["tw"] = tw
                    best["th"] = th

    # Порог: светлая иконка на тёмном toolbar обычно score < ~90
    if best["tw"] <= 0 or best["score"] > 110:
        print(f"  {DIM}VeepN: пазл на экране не найден (best={best['score']:.1f}){RST}")
        return None

    cx = ox + crop_l + best["x"] + best["tw"] // 2
    cy = oy + crop_t + best["y"] + best["th"] // 2
    print(
        f"  {G}✔ VeepN: пазл найден @ {cx},{cy} "
        f"(score={best['score']:.1f}, {best['tw']}x{best['th']}){RST}"
    )
    return int(cx), int(cy)


def _win_find_veepn_toolbar_icon(
    hwnd: int, puzzle_x: int | None = None, puzzle_y: int | None = None,
) -> tuple[int, int] | None:
    """Зелёный щит VeepN, закреплённый в toolbar (слева от пазла) — клик открывает popup."""
    if os.name != "nt" or not hwnd:
        return None
    hay, (ox, oy) = _win_grab_chrome_image(hwnd)
    if hay is None:
        return None
    w, h = hay.size
    if puzzle_x is None or puzzle_y is None:
        found = _win_find_chrome_puzzle_icon(hwnd)
        if found:
            puzzle_x, puzzle_y = found
        else:
            puzzle_x, puzzle_y = ox + w - 80, oy + 50

    # Полоска toolbar: слева от пазла
    ix = max(0, min(w - 1, puzzle_x - ox))
    iy = max(0, min(h - 1, puzzle_y - oy))
    left = max(0, ix - 160)
    right = max(left + 20, ix - 8)
    top = max(0, iy - 28)
    bottom = min(h, iy + 28)
    if right - left < 16 or bottom - top < 16:
        return None
    region = hay.crop((left, top, right, bottom)).convert("RGB")
    rw, rh = region.size
    px = region.load()
    hits: list[tuple[int, int]] = []
    for y in range(0, rh, 1):
        for x in range(0, rw, 1):
            r, g, b = px[x, y]
            # Зелёный щит (активный VPN) или темно-зелёный
            if g < 90:
                continue
            if g <= r + 12:
                continue
            if r > 160 and b > 160:
                continue
            if g > r + 20 and g > b - 10:
                hits.append((x, y))
    if len(hits) < 12:
        return None
    xs = sorted(p[0] for p in hits)
    ys = sorted(p[1] for p in hits)
    sx = xs[len(xs) // 2]
    sy = ys[len(ys) // 2]
    cx = ox + left + sx
    cy = oy + top + sy
    print(f"  {G}✔ VeepN: щит в toolbar @ {cx},{cy}{RST}")
    return int(cx), int(cy)


def _win_find_veepn_extension_row(
    hwnd: int, puzzle_x: int, puzzle_y: int,
) -> tuple[int, int] | None:
    """После открытого меню пазла — найти строку VeePN по зелёному щиту и клик по имени.

    Клик по тексту «Бесплатный VPN…» (справа от щита), не по pin и не по «Управление».
    """
    if os.name != "nt" or not hwnd:
        return None
    hay, (ox, oy) = _win_grab_chrome_image(hwnd)
    if hay is None:
        return None
    w, h = hay.size
    # Меню висит под пазлом, влево от него (скрин)
    ix = max(0, min(w - 1, puzzle_x - ox))
    iy = max(0, min(h - 1, puzzle_y - oy))
    left = max(0, ix - 400)
    top = max(0, iy + 10)
    right = min(w, ix + 40)
    bottom = min(h, iy + 340)
    if right - left < 80 or bottom - top < 60:
        return None
    region = hay.crop((left, top, right, bottom)).convert("RGB")
    rw, rh = region.size
    px = region.load()

    # Зелёный/бирюзовый щит VeePN
    hits: list[tuple[int, int]] = []
    for y in range(0, rh, 2):
        for x in range(0, rw, 2):
            r, g, b = px[x, y]
            if g < 95:
                continue
            if g <= r + 18:
                continue
            if g < b - 25:
                continue
            if r > 170 or b > 200:
                continue
            # Тёмный фон меню не зелёный
            if r + g + b < 120 and g < 110:
                continue
            hits.append((x, y))

    if len(hits) < 5:
        print(f"  {DIM}VeepN: зелёный щит в меню не найден (hits={len(hits)}){RST}")
        return None

    # Медиана кластера = центр щита
    xs = sorted(p[0] for p in hits)
    ys = sorted(p[1] for p in hits)
    sx = xs[len(xs) // 2]
    sy = ys[len(ys) // 2]

    # Имя расширения правее щита; чуть ниже (~0.5 см ≈ 20px @96dpi) — попасть в текст строки
    s = _win_chrome_dpi_scale(hwnd)
    click_x = ox + left + sx + 75
    click_y = oy + top + sy + int(20 * s)
    print(f"  {G}✔ VeepN: строка расширения найдена @ {click_x},{click_y} (щит→имя, +0.5см вниз){RST}")
    return int(click_x), int(click_y)


def _win_chrome_puzzle_menu_coords(hwnd: int) -> dict | None:
    """Координаты: сначала ИЩЕМ пазл на скрине, иначе грубый fallback."""
    if os.name != "nt" or not hwnd:
        return None
    import ctypes
    from ctypes import wintypes

    _win_dpi_aware_once()
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    s = _win_chrome_dpi_scale(hwnd)

    found = _win_find_chrome_puzzle_icon(hwnd)
    # Fallback Y: +20px (~0.5см) ниже прежних точек
    veepn_offsets = [
        (-140, 175), (-150, 190), (-130, 160), (-160, 205),
        (-145, 220), (-155, 145), (-120, 185), (-170, 170),
    ]
    veepn_off = [(int(dx * s), int(dy * s)) for dx, dy in veepn_offsets]

    if found:
        px, py = found
        return {
            "puzzle_x": px,
            "puzzle_y": py,
            "puzzle_xs": [px],
            "puzzle_ys": [py],
            "found": True,
            "veepn_offsets": veepn_off,
            "scale": s,
            "right": int(rect.right),
            "top": int(rect.top),
        }

    print(f"  {DIM}VeepN: пазл не найден шаблоном — грубые координаты{RST}")
    puzzle_xs = [rect.right - int(d * s) for d in (240, 220, 260, 200, 280)]
    puzzle_ys = [rect.top + int(d * s) for d in (48, 40, 56, 32, 64)]
    return {
        "puzzle_x": int(puzzle_xs[0]),
        "puzzle_y": int(puzzle_ys[0]),
        "puzzle_xs": [int(x) for x in puzzle_xs],
        "puzzle_ys": [int(y) for y in puzzle_ys],
        "found": False,
        "veepn_offsets": veepn_off,
        "scale": s,
        "right": int(rect.right),
        "top": int(rect.top),
    }


def _veepn_power_on_icon_path() -> Path:
    return _HERE / "assets" / "veepn_power_on.png"


def _win_find_veepn_power_circle(hwnd: int) -> tuple[int, int] | None:
    """Крупный круг питания VeepN на экране (зелёный ВКЛ / серый ВЫКЛ)."""
    if os.name != "nt" or not hwnd:
        return None
    from PIL import Image

    hay, (ox, oy) = _win_grab_chrome_image(hwnd)
    if hay is None:
        return None
    w, h = hay.size
    # Popup справа под toolbar — ищем только верхнюю правую зону
    left = max(0, w - 560)
    right = min(w, w - 8)
    top = max(0, 55)
    bottom = min(h, 380)
    if right - left < 120 or bottom - top < 120:
        return None
    region = hay.crop((left, top, right, bottom)).convert("RGB")
    rw, rh = region.size

    # 1) Шаблон зелёного круга (со скрина пользователя)
    tmpl_path = _veepn_power_on_icon_path()
    if tmpl_path.is_file():
        tmpl0 = Image.open(tmpl_path).convert("RGB")
        best = {"score": 1e18, "x": 0, "y": 0, "tw": 0, "th": 0}
        for scale in (0.55, 0.7, 0.85, 1.0, 1.15, 1.3):
            tw = max(40, int(tmpl0.width * scale))
            th = max(40, int(tmpl0.height * scale))
            if tw >= rw or th >= rh:
                continue
            tip = tmpl0.resize((tw, th), Image.Resampling.LANCZOS)
            tip_px = tip.load()
            # маска: только «зелёные» пиксели шаблона
            mask = []
            for yy in range(0, th, 2):
                for xx in range(0, tw, 2):
                    r, g, b = tip_px[xx, yy]
                    if r < 90 and g > 160 and (g - r) > 80:
                        mask.append((xx, yy, r, g, b))
            if len(mask) < 30:
                continue
            reg_px = region.load()
            step = max(3, tw // 18)
            for y0 in range(0, rh - th, step):
                for x0 in range(0, rw - tw, step):
                    err = 0
                    n = 0
                    for xx, yy, tr, tg, tb in mask:
                        r, g, b = reg_px[x0 + xx, y0 + yy]
                        err += abs(r - tr) + abs(g - tg) + abs(b - tb)
                        n += 1
                        if err > best["score"] * 1.2 and n > 20:
                            break
                    if n < 20:
                        continue
                    score = err / n
                    if score < best["score"]:
                        best = {"score": score, "x": x0, "y": y0, "tw": tw, "th": th}
        if best["tw"] > 0 and best["score"] < 55:
            cx = ox + left + best["x"] + best["tw"] // 2
            cy = oy + top + best["y"] + best["th"] // 2
            print(
                f"  {G}✔ VeepN: круг на экране @ {cx},{cy} "
                f"(tmpl score={best['score']:.1f}, {best['tw']}x{best['th']}){RST}"
            )
            return int(cx), int(cy)

    # 2) Fallback: залитый бирюзовый диск (ВКЛ) или светло-серый (ВЫКЛ)
    px = region.load()
    best = {"score": 0, "cx": 0, "cy": 0, "kind": ""}
    for cy in range(50, min(rh - 50, 260), 3):
        for cx in range(50, rw - 50, 3):
            green = gray = 0
            samples = 0
            # залитый диск R≈35–55 (не кольцо!)
            for dy in range(-48, 49, 3):
                for dx in range(-48, 49, 3):
                    if dx * dx + dy * dy > 48 * 48:
                        continue
                    x, y = cx + dx, cy + dy
                    if x < 0 or y < 0 or x >= rw or y >= rh:
                        continue
                    r, g, b = px[x, y]
                    samples += 1
                    # бирюзовый ВКЛ: ~#20DE9E
                    if r < 90 and g > 150 and (g - r) > 70:
                        green += 1
                    # серый ВЫКЛ-диск на тёмном фоне
                    elif 150 <= r <= 235 and 150 <= g <= 235 and 150 <= b <= 235:
                        if abs(r - g) < 28 and abs(g - b) < 28:
                            gray += 1
            if samples < 40:
                continue
            if green >= 35:
                score = green
                kind = "on"
            elif gray >= 45 and green < 12:
                # серый круг питания; центр ближе к середине popup
                score = gray // 2
                kind = "off"
            else:
                continue
            # центр popup по X
            score = int(score - abs(cx - rw * 0.52) * 0.08)
            if score > best["score"]:
                best = {"score": score, "cx": cx, "cy": cy, "kind": kind}

    if best["score"] < 28:
        print(f"  {DIM}VeepN: круг на экране не найден{RST}")
        return None
    cx = ox + left + best["cx"]
    cy = oy + top + best["cy"]
    print(
        f"  {G}✔ VeepN: круг на экране @ {cx},{cy} "
        f"({best['kind']}, score={best['score']}){RST}"
    )
    return int(cx), int(cy)


def _win_click_veepn_power(hwnd: int) -> bool:
    """Клик по кругу питания Win32 (когда Playwright mouse не включает VPN)."""
    pt = _win_find_veepn_power_circle(hwnd)
    if not pt:
        print(f"  {DIM}VeepN: круг на экране не найден{RST}")
        return False
    _win_click_screen(pt[0], pt[1])
    return True


def _win_press_escape() -> None:
    if os.name != "nt":
        return
    import ctypes
    user32 = ctypes.windll.user32
    VK_ESCAPE = 0x1B
    user32.keybd_event(VK_ESCAPE, 0, 0, 0)
    user32.keybd_event(VK_ESCAPE, 0, 2, 0)


def _win_find_extensions_flyout(main_hwnd: int, puzzle_x: int, puzzle_y: int):
    """Опционально: отдельное HWND меню (часто нет — меню внутри Chrome)."""
    if os.name != "nt" or not main_hwnd:
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    best = {"hwnd": 0, "area": 0, "rect": None}
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _):
        if int(hwnd) == int(main_hwnd):
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if "chrome" not in cls.value.lower():
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w < 200 or w > 560 or h < 80 or h > 950:
            return True
        if r.top < puzzle_y - 30 or r.top > puzzle_y + 140:
            return True
        if r.left > puzzle_x + 40:
            return True
        area = w * h
        if area > 500_000:
            return True
        if area > best["area"]:
            best["area"] = area
            best["hwnd"] = int(hwnd)
            best["rect"] = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return best["rect"]


def _win_click_screen(x: int, y: int) -> None:
    """Клик ЛКМ по экранным координатам (Windows)."""
    if os.name != "nt":
        return
    import ctypes

    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def _win_click_veepn_row_near_puzzle(
    puzzle_x: int, puzzle_y: int, offsets: list, hwnd: int = 0,
) -> None:
    """Клик по имени «Бесплатный VPN…»: сначала поиск зелёного щита в меню."""
    if hwnd:
        found = _win_find_veepn_extension_row(hwnd, puzzle_x, puzzle_y)
        if found:
            print(f"  {DIM}VeepN: 2) клик «Бесплатный VPN…» @ {found[0]},{found[1]}…{RST}")
            _win_click_screen(found[0], found[1])
            return
    if not offsets:
        offsets = [(-140, 155)]
    dx, dy = offsets[0]
    x = puzzle_x + dx
    y = puzzle_y + dy
    print(f"  {DIM}VeepN: 2) клик «Бесплатный VPN…» (fallback) @ {x},{y}…{RST}")
    _win_click_screen(x, y)


def _win_click_chrome_extensions_then_vpn() -> bool:
    """Синх: найти пазл → клик → найти строку VeePN → клик."""
    hwnd = _win_ensure_chrome_maximized()
    if not hwnd:
        return False
    c = _win_chrome_puzzle_menu_coords(hwnd)
    if not c:
        return False
    px, py = c["puzzle_x"], c["puzzle_y"]
    print(f"  {DIM}VeepN: 1) пазл ({px},{py}) found={c.get('found')}…{RST}")
    _win_click_screen(px, py)
    time.sleep(0.95)
    offs = c.get("veepn_offsets") or [(-140, 155)]
    _win_click_veepn_row_near_puzzle(px, py, offs, hwnd=hwnd)
    time.sleep(0.7)
    return True


async def _veepn_find_ready_popup(context, eid: str, *, before_ids: set | None = None):
    """Живой UI VeepN (кнопка + страна), не синий пустой."""
    for p in list(context.pages):
        with contextlib.suppress(Exception):
            if not _is_extension_popup_url(p.url or "", eid):
                continue
            if await _veepn_popup_ui_ready(p):
                return p
    return None


async def _veepn_find_any_popup(context, eid: str):
    """Любой popup VeepN, в т.ч. синий пустой (VPN уже ВКЛ)."""
    for p in list(context.pages):
        with contextlib.suppress(Exception):
            if _is_extension_popup_url(p.url or "", eid):
                return p
    return None


async def _veepn_wait_popup_after_ext_click(
    context, eid: str, before_ids: set | None = None, *, seconds: float = 7.0,
):
    """После клика по расширению — только ждём UI, без новых кликов мышью."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for p in list(context.pages):
            with contextlib.suppress(Exception):
                if not _is_extension_popup_url(p.url or "", eid):
                    continue
                if await _veepn_popup_ui_ready(p):
                    return p, False
                if await _veepn_popup_is_blank(p):
                    await asyncio.sleep(0.7)
                    if await _veepn_popup_ui_ready(p):
                        return p, False
                    if await _veepn_popup_is_blank(p):
                        return p, True
        await asyncio.sleep(0.2)
    return None, False


async def _veepn_open_popup_via_puzzle_menu(context, eid: str) -> object | None:
    """Открыть UI VeepN: 1) щит в toolbar 2) иначе пазл→строка. Один клик → ждать UI → СТОП."""
    with contextlib.suppress(Exception):
        await _close_vpn_extension_tabs(context, eid)
    with contextlib.suppress(Exception):
        page_m = await _main_work_page(context)
        if page_m:
            await _maximize_window(context, page_m)
            with contextlib.suppress(Exception):
                await page_m.bring_to_front()
            await asyncio.sleep(0.35)
            await asyncio.to_thread(_win_bind_automation_hwnd)

    hwnd = await asyncio.to_thread(_win_ensure_chrome_maximized)
    if not hwnd:
        return None

    before = {id(p) for p in context.pages}

    # 0) Закреплённый зелёный щит слева от пазла (как на скрине)
    shield = await asyncio.to_thread(_win_find_veepn_toolbar_icon, hwnd)
    if shield:
        sx, sy = shield
        print(f"  {DIM}VeepN: 0) клик щит toolbar @ {sx},{sy} — жду UI…{RST}")
        await asyncio.to_thread(_win_click_screen, sx, sy)
        got, is_blank = await _veepn_wait_popup_after_ext_click(
            context, eid, before, seconds=6.0,
        )
        if got and not is_blank:
            print(f"  {G}✔ VeepN: UI открыт со щита — дальше только круг/страна{RST}")
            return got
        if got and is_blank:
            with contextlib.suppress(Exception):
                await _veepn_soft_api_disconnect(context, eid)
            await asyncio.sleep(0.8)
            with contextlib.suppress(Exception):
                if not got.is_closed():
                    await got.close()
            await asyncio.to_thread(_win_click_screen, sx, sy)
            got2, blank2 = await _veepn_wait_popup_after_ext_click(
                context, eid, before, seconds=6.0,
            )
            if got2 and not blank2:
                return got2
        # щит не дал playwright page — всё равно мог открыть visual popup; openPopup
        pop = await _veepn_try_action_open_popup(context, eid)
        if pop and await _veepn_popup_ui_ready(pop):
            return pop

    print(f"  {DIM}VeepN: ищу иконку пазла на экране…{RST}")
    coords = await asyncio.to_thread(_win_chrome_puzzle_menu_coords, hwnd)
    if not coords:
        return await _veepn_try_action_open_popup(context, eid) or (
            await _veepn_find_ready_popup(context, eid)
        )

    before = {id(p) for p in context.pages}
    px, py = coords["puzzle_x"], coords["puzzle_y"]
    offsets = coords.get("veepn_offsets") or [(-140, 175)]

    print(f"  {DIM}VeepN: 1) клик пазл «Расширения» @ {px},{py}…{RST}")
    await asyncio.to_thread(_win_click_screen, px, py)
    await asyncio.sleep(1.0)

    print(f"  {DIM}VeepN: ищу строку «Бесплатный VPN…» в меню…{RST}")
    row = await asyncio.to_thread(_win_find_veepn_extension_row, hwnd, px, py)
    if row:
        vx, vy = row
        label = "щит→имя"
    else:
        vx, vy = px + offsets[0][0], py + offsets[0][1]
        label = "fallback"

    print(
        f"  {DIM}VeepN: 2) клик расширение ({label}) @ {vx},{vy} "
        f"— дальше только жду UI…{RST}"
    )
    await asyncio.to_thread(_win_click_screen, vx, vy)

    got, is_blank = await _veepn_wait_popup_after_ext_click(
        context, eid, before, seconds=7.0,
    )
    if got and not is_blank:
        print(f"  {G}✔ VeepN: UI открыт — дальше круг/страна (без лишних кликов){RST}")
        return got

    if got and is_blank:
        print(
            f"  {Y}VeepN: синий popup (VPN ВКЛ) — soft выкл и "
            f"ОДИН повтор пазл→расширение…{RST}"
        )
        with contextlib.suppress(Exception):
            await _veepn_soft_api_disconnect(context, eid)
        await asyncio.sleep(1.0)
        with contextlib.suppress(Exception):
            if not got.is_closed():
                await got.close()
        coords2 = await asyncio.to_thread(_win_chrome_puzzle_menu_coords, hwnd)
        if coords2:
            px, py = coords2["puzzle_x"], coords2["puzzle_y"]
        await asyncio.to_thread(_win_click_screen, px, py)
        await asyncio.sleep(1.0)
        row2 = await asyncio.to_thread(_win_find_veepn_extension_row, hwnd, px, py)
        if row2:
            vx, vy = row2
        else:
            vx, vy = px + offsets[0][0], py + offsets[0][1]
        print(f"  {DIM}VeepN: 2b) клик расширение @ {vx},{vy} — жду UI…{RST}")
        await asyncio.to_thread(_win_click_screen, vx, vy)
        got2, blank2 = await _veepn_wait_popup_after_ext_click(
            context, eid, before, seconds=7.0,
        )
        if got2 and not blank2:
            print(f"  {G}✔ VeepN: UI открыт — дальше круг/страна{RST}")
            return got2

    print(f"  {DIM}VeepN: page не поймал — openPopup/CDP без лишних кликов мышью…{RST}")
    pop = await _veepn_try_action_open_popup(context, eid)
    if pop and await _veepn_popup_ui_ready(pop):
        return pop
    return await _veepn_find_ready_popup(context, eid)


async def _veepn_soft_api_disconnect(context, eid: str | None = None) -> bool:
    """API-выкл VeepN БЕЗ закрытия popup/вкладок (чтобы не срывать открытый UI)."""
    if context is None:
        return False
    if not eid:
        eid = await _vpn_ext_id(context)
    if not eid:
        return False
    raw = await _veepn_eval_js(context, eid, _veepn_reset_session_js())
    with contextlib.suppress(Exception):
        await _veepn_clear_autoconnect(context, eid)
    await asyncio.sleep(0.6)
    return bool(raw)


async def _veepn_ui_force_disconnect_circle(page, context=None) -> bool:
    """Если «Подключено» — круг выкл в том же UI. Не вызывать _vpn_disconnect (закрывает page)."""
    await _veepn_dismiss_onboarding(page)
    await _veepn_dismiss_rate_us(page)
    st = await _veepn_connection_label(page)
    if st != "on":
        return True
    print(f"  {DIM}VeepN UI: уже Подключено → сразу круг (отключить)…{RST}")
    await _veepn_ui_click_power(page)
    await page.wait_for_timeout(500)
    await _veepn_dismiss_limited_upsell(page)
    if await _veepn_wait_until_off(page, seconds=14.0):
        return True
    print(f"  {DIM}VeepN UI: ещё ВКЛ — повторный круг…{RST}")
    await _veepn_ui_click_power(page)
    await page.wait_for_timeout(500)
    if await _veepn_wait_until_off(page, seconds=10.0):
        return True
    # Soft API — без _close_vpn_extension_tabs
    if context is not None:
        print(f"  {DIM}VeepN: soft API выкл (popup оставляем открытым)…{RST}")
        with contextlib.suppress(Exception):
            await _veepn_soft_api_disconnect(context)
        await asyncio.sleep(0.8)
        with contextlib.suppress(Exception):
            if not page.is_closed():
                await page.reload(wait_until="domcontentloaded", timeout=8000)
                await page.wait_for_timeout(500)
    if page.is_closed():
        return True
    st2 = await _veepn_connection_label(page)
    return st2 != "on"


async def _veepn_ui_select_country_and_connect(
    context, page, eid: str, country_code: str = "us",
) -> bool:
    """Открытый UI VeepN → страна → круг. Без повторных кликов по пазлу/меню."""
    cc = _vpn_normalize_cc(country_code) or "us"
    names = list(_VEEPN_UI_COUNTRY_NAMES.get(cc, [cc.upper()]))
    try:
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        await page.wait_for_timeout(300)
        await _veepn_dismiss_onboarding(page)
        await _veepn_dismiss_rate_us(page)

        st0 = await _veepn_connection_label(page)
        # Уже Подключено на нужной стране (скрин: United States / Oregon) → готово
        if st0 == "on":
            blob = ""
            with contextlib.suppress(Exception):
                blob = str(await page.evaluate(
                    "() => (document.body?.innerText || '').slice(0, 900)"
                )).lower()
            want = [n.lower() for n in names]
            if cc == "us":
                want += ["oregon", "united states", "сша"]
            if any(w in blob for w in want if w):
                proxy_ok = await _vpn_is_proxy_active(context, eid)
                if not proxy_ok:
                    for _ in range(20):
                        await asyncio.sleep(0.4)
                        if await _vpn_is_proxy_active(context, eid):
                            proxy_ok = True
                            break
                if proxy_ok:
                    await _veepn_finalize_connected(context, eid, via=f"UI:{cc.upper()}")
                    print(f"  {G}✔ VeepN: уже Подключено {cc.upper()} (+proxy) — Flipkart{RST}")
                    return True
                print(f"  {DIM}VeepN: UI={cc.upper()} on, proxy нет — круг Win32…{RST}")
                hwnd = await asyncio.to_thread(_win_chrome_main_hwnd)
                if hwnd:
                    await asyncio.to_thread(_win_click_veepn_power, hwnd)
                    await page.wait_for_timeout(800)
                    await _veepn_dismiss_limited_upsell(page)
                    for _ in range(18):
                        await asyncio.sleep(0.4)
                        if await _vpn_is_proxy_active(context, eid):
                            await _veepn_finalize_connected(
                                context, eid, via=f"UI:{cc.upper()}",
                            )
                            print(f"  {G}✔ VeepN: proxy после круга ({cc.upper()}){RST}")
                            return True
                # иначе переподключение через выкл→вкл ниже
            # Другая страна или нет proxy — круг выкл → выбор → вкл
            if not await _veepn_ui_force_disconnect_circle(page, context):
                print(f"  {Y}⚠ VeepN UI: не удалось отключить перед сменой страны{RST}")
                return False
            if page.is_closed():
                page = await _veepn_try_action_open_popup(context, eid) or (
                    await _veepn_find_ready_popup(context, eid)
                )
                if not page:
                    print(f"  {Y}⚠ VeepN UI: popup закрылся после выкл{RST}")
                    return False
            print(f"  {G}✔ VeepN: ВЫКЛ — страна → круг в том же UI{RST}")
            await page.wait_for_timeout(400)

        # Шаг 1–2: страна в списке
        print(f"  {DIM}VeepN UI: 3) клик карточки страны…{RST}")
        if not await _veepn_ui_on_locations_list(page):
            if not await _veepn_ui_click_location_card(page):
                await page.wait_for_timeout(400)
                if not await _veepn_ui_click_location_card(page):
                    print(f"  {Y}⚠ VeepN UI: не открыл список стран{RST}")
                    return False
        await page.wait_for_timeout(450)
        for _ in range(12):
            if await _veepn_ui_on_locations_list(page):
                break
            await page.wait_for_timeout(250)

        print(f"  {DIM}VeepN UI: 4) выбор «{names[0]}» в списке/поиске…{RST}")
        if not await _veepn_ui_scroll_find_and_click(page, names):
            print(f"  {Y}⚠ VeepN UI: «{names[0]}» не найден{RST}")
            return False
        if cc == "us":
            await _veepn_ui_pick_us_state_if_needed(page)

        await _veepn_ui_wait_main_with_country(
            page, names + list(_VEEPN_UI_US_STATES), seconds=10.0,
        )

        # Шаг 3: круг — подключить (повтор если upsell/лагает)
        print(f"  {DIM}VeepN UI: 5) круглая кнопка — подключить…{RST}")
        await _veepn_dismiss_rate_us(page)
        st_now = await _veepn_connection_label(page)
        if st_now == "on":
            print(f"  {DIM}VeepN UI: уже Подключено после выбора страны{RST}")
        else:
            for _pwr in range(3):
                await _veepn_ui_click_power(page, context)
                await page.wait_for_timeout(700)
                await _veepn_dismiss_limited_upsell(page)
                await _veepn_dismiss_rate_us(page)
                if await _vpn_is_proxy_active(context, eid):
                    print(f"  {G}✔ VeepN: proxy после круга{RST}")
                    break
                if await _veepn_wait_until_on(page, seconds=12.0 if _pwr else 16.0):
                    break
            else:
                if (await _veepn_connection_label(page)) != "on" and not (
                    await _vpn_is_proxy_active(context, eid)
                ):
                    print(f"  {Y}⚠ VeepN UI: не дождался «Подключено»{RST}")
                    return False

        # Шаг 4: proxy
        print(f"  {DIM}VeepN UI: 6) проверяю Подключено + proxy…{RST}")
        for _ in range(20):
            proxy_ok = await _vpn_is_proxy_active(context, eid)
            st = await _veepn_connection_label(page)
            if st == "on" and proxy_ok:
                await _veepn_finalize_connected(context, eid, via=f"UI:{cc.upper()}")
                print(f"  {G}✔ VeepN: Подключено и proxy жив ({cc.upper()}){RST}")
                return True
            if st == "on" and not proxy_ok:
                await asyncio.sleep(0.4)
                continue
            await asyncio.sleep(0.35)
        if (await _veepn_connection_label(page)) == "on":
            await _veepn_finalize_connected(context, eid, via=f"UI:{cc.upper()}")
            print(f"  {G}✔ VeepN: Подключено ({cc.upper()}){RST}")
            return True
        print(f"  {Y}⚠ VeepN UI: статус не «Подключено» после сценария{RST}")
        return False
    except Exception as e:
        print(f"  {DIM}VeepN UI connect: {str(e)[:100]}{RST}")
        return False


async def _veepn_try_action_open_popup(context, eid: str) -> object | None:
    """chrome.action.openPopup() из service worker — настоящий popup как по клику."""
    await _wake_vpn_extension(context, eid)
    sw = await _wait_vpn_service_worker(context, eid, timeout=8.0)
    if not sw:
        return None
    with contextlib.suppress(Exception):
        await sw.evaluate("""async () => {
            try {
                if (chrome?.action?.openPopup) {
                    await chrome.action.openPopup();
                    return 'ok';
                }
            } catch (e) { return String(e); }
            return 'no';
        }""")
    await asyncio.sleep(0.8)
    for p in list(context.pages):
        with contextlib.suppress(Exception):
            if _is_extension_popup_url(p.url or "", eid) and await _veepn_popup_ui_ready(p):
                return p
    return None


async def _veepn_reopen_popup_tab(context, eid: str) -> object | None:
    """Закрыть старые popup-вкладки и открыть popup.html заново."""
    await _close_vpn_extension_tabs(context, eid)
    await asyncio.sleep(0.35)
    prefix = f"chrome-extension://{eid}"
    url = f"{prefix}/src/popup/popup.html"
    page = None
    with contextlib.suppress(Exception):
        anchor = context.pages[0] if context.pages else await context.new_page()
        cdp = await context.new_cdp_session(anchor)
        try:
            await cdp.send("Target.createTarget", {"url": url})
        finally:
            with contextlib.suppress(Exception):
                await cdp.detach()
        for _ in range(24):
            await asyncio.sleep(0.2)
            for p in list(context.pages):
                if _is_extension_popup_url(p.url or "", eid):
                    page = p
                    break
            if page:
                break
    if not page:
        with contextlib.suppress(Exception):
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
    if page:
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        for _ in range(10):
            if await _veepn_popup_ui_ready(page):
                return page
            await asyncio.sleep(0.3)
    return page if page and await _veepn_popup_ui_ready(page) else page


async def _veepn_recover_blank_popup(context, eid: str, blank_page=None) -> object | None:
    """Пустой синий popup.html = VPN в этом Chrome уже ВКЛ.

    Правило пользователя: отключить VPN → снова открыть popup → UI появится.
    Если всё ещё пусто — пазл → «Бесплатный VPN…».
    """
    print(
        f"  {Y}VeepN: popup пустой (синий) — VPN уже включён → "
        f"отключаю и открываю снова…{RST}"
    )

    # 1) Снять VPN (API) — иначе SPA на popup.html часто не рисуется
    with contextlib.suppress(Exception):
        await _vpn_disconnect(context)
    await asyncio.sleep(0.8)

    if blank_page is not None:
        with contextlib.suppress(Exception):
            await blank_page.close()

    # 2) Снова открыть popup — после выкл UI должен появиться
    pop = await _veepn_reopen_popup_tab(context, eid)
    if pop and await _veepn_popup_ui_ready(pop):
        print(f"  {G}✔ VeepN: UI появился после отключения + повторного открытия{RST}")
        return pop

    # 3) openPopup из service worker
    pop2 = await _veepn_try_action_open_popup(context, eid)
    if pop2:
        print(f"  {G}✔ VeepN: popup через action.openPopup{RST}")
        return pop2

    # 4) Пазл → VeePN (точные клики) → живой UI
    with contextlib.suppress(Exception):
        page_m = await _main_work_page(context)
        if page_m:
            await _maximize_window(context, page_m)
            await asyncio.sleep(0.35)
    pop4 = await _veepn_open_popup_via_puzzle_menu(context, eid)
    if pop4 and await _veepn_popup_ui_ready(pop4):
        print(f"  {G}✔ VeepN: popup через меню расширений{RST}")
        return pop4

    # 5) Ещё раз: disconnect + reopen (на случай гонки)
    with contextlib.suppress(Exception):
        await _vpn_disconnect(context)
    await asyncio.sleep(0.6)
    pop3 = await _veepn_reopen_popup_tab(context, eid)
    if pop3 and await _veepn_popup_ui_ready(pop3):
        print(f"  {G}✔ VeepN: UI после повторного выкл/открытия{RST}")
        return pop3

    return pop3 if pop3 and await _veepn_popup_ui_ready(pop3) else (
        pop if pop and await _veepn_popup_ui_ready(pop) else None
    )


async def _open_extension_popup_page(context, eid: str, popup_paths: list[str] | None = None):
    """Открывает popup. Для VeepN — только src/popup/popup.html (иначе ERR_BLOCKED)."""
    paths = list(popup_paths or _vpn_popup_rel_paths())
    if _vpn_is_veepn():
        paths = [p for p in paths if "src/popup" in p] or _veepn_popup_rel_paths()

    pop = None
    prefix = f"chrome-extension://{eid}"

    async def _page_blocked(pg) -> bool:
        with contextlib.suppress(Exception):
            title = await pg.title()
            body = ""
            with contextlib.suppress(Exception):
                body = await pg.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 300)"
                )
            if _page_shows_client_block(pg.url or "", title, body or ""):
                return True
        u = (pg.url or "").lower()
        return "chrome-error://" in u or "chromewebdata" in u

    for p in list(context.pages):
        try:
            if await _page_blocked(p):
                if len(context.pages) > 1:
                    with contextlib.suppress(Exception):
                        await p.close()
                continue
            if _is_extension_popup_url(p.url or "", eid):
                pop = p
                break
        except Exception:
            pass

    async def _open_via_cdp(url: str):
        anchor = context.pages[0] if context.pages else await context.new_page()
        cdp = await context.new_cdp_session(anchor)
        try:
            created = await cdp.send("Target.createTarget", {"url": url})
            target_id = created.get("targetId")
            if not target_id:
                return None
            for _ in range(40):
                for pg in list(context.pages):
                    try:
                        if await _page_blocked(pg):
                            if len(context.pages) > 1:
                                with contextlib.suppress(Exception):
                                    await pg.close()
                            continue
                        if _is_extension_popup_url(pg.url or "", eid):
                            return pg
                    except Exception:
                        pass
                await asyncio.sleep(0.25)
        finally:
            with contextlib.suppress(Exception):
                await cdp.detach()
        return None

    if not pop:
        for rel in paths:
            url = f"{prefix}/{rel.lstrip('/')}"
            pop = await _open_via_cdp(url)
            if pop and not await _page_blocked(pop):
                break
            pop = None
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                if await _page_blocked(page):
                    print(f"  {Y}VeepN: вкладка popup заблокирована (ERR_BLOCKED) — закрываю{RST}")
                    with contextlib.suppress(Exception):
                        await page.close()
                    continue
                if _is_extension_popup_url(page.url or "", eid):
                    pop = page
                    break
            except Exception:
                with contextlib.suppress(Exception):
                    await page.close()

    if pop and not _is_extension_popup_url(pop.url or "", eid):
        for rel in paths:
            with contextlib.suppress(Exception):
                await pop.goto(
                    f"{prefix}/{rel.lstrip('/')}",
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
                if await _page_blocked(pop):
                    continue
                if _is_extension_popup_url(pop.url or "", eid):
                    break

    if pop and await _page_blocked(pop):
        with contextlib.suppress(Exception):
            await pop.close()
        pop = None

    if pop:
        with contextlib.suppress(Exception):
            await pop.wait_for_load_state("domcontentloaded", timeout=12_000)
        for _ in range(8):
            if await _veepn_popup_ui_ready(pop):
                return pop
            await asyncio.sleep(0.25)
        if await _veepn_popup_is_blank(pop):
            recovered = await _veepn_recover_blank_popup(context, eid, blank_page=pop)
            if recovered:
                return recovered

    if not pop or await _veepn_popup_is_blank(pop):
        recovered = await _veepn_recover_blank_popup(context, eid, blank_page=pop)
        if recovered:
            return recovered

    return pop


async def _veepn_ui_click_location_card(page) -> bool:
    """На главном экране: клик по строке страны («Netherlands Amsterdam >»). Mouse, не зависающий el.click()."""
    await _veepn_dismiss_rate_us(page)
    with contextlib.suppress(Exception):
        box = await page.evaluate("""() => {
            const re = /netherlands|amsterdam|united states|united kingdom|canada|france|germany|singapore|russia|oregon|virginia|paris|london|optimal location|ip:\\s*\\d/i;
            let best = null, bestScore = -1e9;
            for (const el of document.querySelectorAll('button, [role="button"], div, a')) {
                const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!t || t.length > 100 || !re.test(t)) continue;
                if (/premium|получите|скачать|обход|adblock|попробовать/i.test(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 120 || r.height < 28 || r.height > 140) continue;
                if (r.top < 60 || r.top > window.innerHeight * 0.9) continue;
                const hasChevron = />|›/.test(t) || !!el.querySelector('svg');
                const score = r.width * r.height + (hasChevron ? 8000 : 0)
                    - Math.abs(r.top - window.innerHeight * 0.48) * 2;
                if (score > bestScore) {
                    bestScore = score;
                    best = {x: r.x + r.width / 2, y: r.y + r.height / 2, t: t.slice(0, 40)};
                }
            }
            return best;
        }""")
        if box and box.get("x"):
            print(f"  {DIM}VeepN UI: клик карточки «{box.get('t', '?')}»…{RST}")
            await page.mouse.click(float(box["x"]), float(box["y"]))
            await page.wait_for_timeout(900)
            return True
    for pat in (
        r"Netherlands", r"Amsterdam", r"United States", r"United Kingdom",
        r"Canada", r"France", r"Germany", r"Singapore", r"Russia", r"Oregon",
    ):
        with contextlib.suppress(Exception):
            loc = page.get_by_text(re.compile(pat, re.I)).first
            if await loc.count() > 0 and await loc.is_visible():
                b = await loc.bounding_box()
                if b:
                    await page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                else:
                    await loc.click(timeout=2500, force=True)
                await page.wait_for_timeout(700)
                return True
    return False


async def _veepn_ui_on_locations_list(page) -> bool:
    """Экран «Локации» со списком / поиском."""
    with contextlib.suppress(Exception):
        return bool(await page.evaluate("""() => {
            const b = (document.body?.innerText || '').toLowerCase();
            const u = (location.href || '').toLowerCase();
            return b.includes('бесплатные локации') || b.includes('free locations')
                || b.includes('премиальные локации') || b.includes('premium locations')
                || b.includes('одиночные результаты') || b.includes('single results')
                || (b.includes('локации') && (b.includes('поиск') || b.includes('search')))
                || u.includes('/locations') || u.includes('#/locations');
        }"""))
    return False


async def _veepn_ui_locations_search_box(page):
    """Поле поиска на экране «Локации» (placeholder «Поиск…» или любой text/search input)."""
    candidates = [
        page.get_by_placeholder(re.compile(r"Поиск|Search", re.I)).first,
        page.locator('input[type="search"]').first,
        page.locator('input[type="text"]').first,
        page.locator("input:not([type='hidden']):not([type='checkbox'])").first,
    ]
    for loc in candidates:
        with contextlib.suppress(Exception):
            if await loc.count() > 0 and await loc.is_visible():
                return loc
    return None


async def _veepn_ui_search_and_click(page, names: list[str]) -> bool:
    """Искать страну в поле поиска («United States» → Одиночные результаты) и кликнуть."""
    search = await _veepn_ui_locations_search_box(page)
    if not search:
        return False
    for name in names:
        with contextlib.suppress(Exception):
            await search.click(timeout=2000)
            await search.fill("")
            await search.type(name, delay=35)
            await page.wait_for_timeout(600)
            print(f"  {DIM}VeepN UI: поиск «{name}»…{RST}")
            # Клик по строке результата (не по шеврону штатов отдельно — сама строка)
            box = await page.evaluate(
                """(want) => {
                    const w = (want || '').toLowerCase();
                    if (!w) return null;
                    let best = null, bestScore = -1e9;
                    for (const el of document.querySelectorAll(
                            'button, [role="button"], li, a, div, span')) {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (!t || t.length > 80) continue;
                        const tl = t.toLowerCase();
                        if (!tl.includes(w)) continue;
                        if (/премиальн|premium|получить|скачать/i.test(t)) continue;
                        if (/одиночные|single result|результаты/i.test(t)
                                && tl.split('\\n')[0].indexOf(w) < 0) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 80 || r.height < 22 || r.height > 100) continue;
                        if (r.top < 80 || r.top > window.innerHeight * 0.92) continue;
                        const score = r.width * Math.min(r.height, 56)
                            - Math.abs(r.top - 180) * 3;
                        if (score > bestScore) {
                            bestScore = score;
                            best = {x: r.x + Math.min(r.width, 220) / 2,
                                    y: r.y + r.height / 2, t: t.slice(0, 50)};
                        }
                    }
                    return best;
                }""",
                name,
            )
            if box and box.get("x"):
                await page.mouse.click(float(box["x"]), float(box["y"]))
                print(f"  {DIM}VeepN UI: клик по поиску «{box.get('t', name)}»{RST}")
                await page.wait_for_timeout(700)
                return True
            loc = page.get_by_text(re.compile(rf"^{re.escape(name)}$", re.I)).first
            if await loc.count() == 0:
                loc = page.get_by_text(re.compile(re.escape(name), re.I)).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=2500)
                print(f"  {DIM}VeepN UI: клик «{name}» (поиск){RST}")
                await page.wait_for_timeout(600)
                return True
        with contextlib.suppress(Exception):
            await search.fill("")
            await page.wait_for_timeout(200)
    return False


async def _veepn_ui_scroll_find_and_click(page, names: list[str]) -> bool:
    """Сначала поиск в поле «Поиск…», иначе скролл списка бесплатных локаций."""
    if await _veepn_ui_search_and_click(page, names):
        return True
    for name in names:
        for _scroll in range(14):
            with contextlib.suppress(Exception):
                loc = page.get_by_text(re.compile(rf"^{re.escape(name)}$", re.I)).first
                if await loc.count() == 0:
                    loc = page.get_by_text(re.compile(re.escape(name), re.I)).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.scroll_into_view_if_needed(timeout=2000)
                    await loc.click(timeout=2500)
                    print(f"  {DIM}VeepN UI: клик «{name}» (скролл){RST}")
                    await page.wait_for_timeout(600)
                    return True
            with contextlib.suppress(Exception):
                await page.evaluate("""() => {
                    const nodes = [...document.querySelectorAll('div, section, main, ul')];
                    let box = null, best = 0;
                    for (const el of nodes) {
                        const s = getComputedStyle(el);
                        const ok = /(auto|scroll)/.test(s.overflowY) || el.scrollHeight > el.clientHeight + 40;
                        if (!ok) continue;
                        const r = el.getBoundingClientRect();
                        if (r.height < 120 || r.width < 120) continue;
                        const area = r.width * r.height;
                        if (area > best) { best = area; box = el; }
                    }
                    if (box) box.scrollBy(0, Math.max(160, box.clientHeight * 0.7));
                    else window.scrollBy(0, 220);
                }""")
            await page.wait_for_timeout(280)
        with contextlib.suppress(Exception):
            search = await _veepn_ui_locations_search_box(page)
            if search:
                await search.fill("")
                await page.wait_for_timeout(300)
    return False

async def _veepn_ui_pick_us_state_if_needed(page) -> bool:
    """После клика United States: если развернулись штаты — выбрать любой (Oregon/Virginia…)."""
    await page.wait_for_timeout(400)
    # Уже на главном с Oregon — штат выбран
    with contextlib.suppress(Exception):
        body = (await page.evaluate("() => (document.body?.innerText || '').toLowerCase()")).lower()
        if "соединение" in body or "connection" in body:
            if any(s.lower() in body for s in _VEEPN_UI_US_STATES):
                return True
            if not await _veepn_ui_on_locations_list(page):
                return True  # вернулись на главный без штатов в тексте — ок
    for state in _VEEPN_UI_US_STATES:
        with contextlib.suppress(Exception):
            loc = page.get_by_text(re.compile(rf"^{re.escape(state)}$", re.I)).first
            if await loc.count() == 0:
                loc = page.get_by_text(re.compile(re.escape(state), re.I)).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=2500)
                print(f"  {DIM}VeepN UI: штат {state}{RST}")
                await page.wait_for_timeout(700)
                return True
    # Нет раскрытия штатов — клик по самой строке US мог уже выбрать дефолт
    return True


async def _veepn_ui_wait_main_with_country(page, names: list[str], *, seconds: float = 8.0) -> bool:
    """Ждём главный экран (ВЫКЛ/Подключено) с выбранной страной на карточке."""
    deadline = time.monotonic() + seconds
    name_re = re.compile("|".join(re.escape(n) for n in names), re.I)
    while time.monotonic() < deadline:
        await _veepn_dismiss_rate_us(page)
        st = await _veepn_connection_label(page)
        if st in ("on", "off", "unknown"):
            with contextlib.suppress(Exception):
                blob = await page.evaluate("() => (document.body?.innerText || '').slice(0, 800)")
                if name_re.search(blob or "") and not await _veepn_ui_on_locations_list(page):
                    return True
        # Назад из локаций, если застряли
        if await _veepn_ui_on_locations_list(page):
            with contextlib.suppress(Exception):
                back = page.locator('button, [role="button"], a').filter(
                    has_text=re.compile(r"^<$|^←$|назад|back", re.I)
                ).first
                if await back.count() > 0:
                    await back.click(timeout=1500)
                else:
                    await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
    return False


async def _veepn_ui_reconnect_country(context, country_code: str) -> bool:
    """Смена страны через UI VeepN; экранные клики сериализованы между потоками."""
    async with _veepn_screen_guard():
        return await _veepn_ui_reconnect_country_impl(context, country_code)


async def _veepn_ui_reconnect_country_impl(context, country_code: str) -> bool:
    """Смена страны строго через UI VeepN (как в обучении):

    1) Сначала пазл → VeePN (не открывать синий popup.html через CDP)
    2) Если уже «Подключено» → круг выкл → ждать ВЫКЛ
    3) Карточка страны → список/поиск → United States (+ штат)
    4) Круглая кнопка вкл → проверка «Подключено»
    """
    if not _vpn_is_veepn():
        return False
    eid = await _vpn_ext_id(context)
    if not eid:
        return False
    cc = _vpn_normalize_cc(country_code)
    print(f"  {DIM}VeepN UI: страна → {cc.upper()} (пазл→страна→питание)…{RST}")

    # 1) Пазл первым — CDP popup.html часто синий пустой, и мышь ездит по вкладке
    page = await _veepn_open_popup_via_puzzle_menu(context, eid)
    if not page or not await _veepn_popup_ui_ready(page):
        page = await _open_extension_popup_page(
            context, eid, [
                "src/popup/popup.html",
            ],
        )
        if page and await _veepn_popup_is_blank(page):
            # Синий = VPN вкл → выкл + снова пазл (не кликать по пустой странице)
            recovered = await _veepn_recover_blank_popup(context, eid, blank_page=page)
            page = recovered
            if not page or not await _veepn_popup_ui_ready(page):
                page = await _veepn_open_popup_via_puzzle_menu(context, eid)
    if not page or await _veepn_popup_is_blank(page) or not await _veepn_popup_ui_ready(page):
        return await _veepn_switch_country(context, eid, cc)

    ok = await _veepn_ui_select_country_and_connect(context, page, eid, cc)
    if ok:
        return True
    return await _veepn_switch_country(context, eid, cc)


async def _ensure_veepn_connected(context, *, quick: bool = False, flipkart: bool = True) -> bool:
    """UI-правила VeepN для Flipkart:
    • Proxy уже жив → сразу Flipkart (не рвать)
    • Уже «Подключено» + USA → не трогать
    • ВЫКЛ / другая страна → UI щит/пазл → страна → круг
    """
    eid = await _vpn_ext_id(context)
    if not eid:
        print(f"  {Y}⚠ VeepN: не найден ID расширения{RST}")
        return False

    await _wake_vpn_extension(context, eid)
    await asyncio.sleep(0.5 if quick else 1.0)
    await _dismiss_all_veepn_welcome(context)

    # Самый надёжный короткий путь: proxy уже работает
    if flipkart and await _vpn_is_proxy_active(context, eid):
        cc_now = ""
        with contextlib.suppress(Exception):
            cc_now = await _veepn_connected_country_hint(context, eid)
        print(
            f"  {G}✔ VeepN: proxy уже активен "
            f"({(cc_now or 'US?').upper()}) — Flipkart{RST}"
        )
        with contextlib.suppress(Exception):
            await _veepn_finalize_connected(context, eid, via=(cc_now or "vpn").upper())
        return True

    # Proxy нет — но UI может уже быть Подключено (как на скрине без ожидания proxy)
    # продолжаем через щит/пазл ниже

    # Открываем настоящий UI через щит/пазл (не синий CDP вслепую)
    page = await _veepn_open_popup_via_puzzle_menu(context, eid)
    if not page or not await _veepn_popup_ui_ready(page):
        page = await _open_extension_popup_page(
            context, eid, ["src/popup/popup.html"],
        )
    if not page:
        print(f"  {Y}⚠ VeepN: не открыл popup{RST}")
        return False

    try:
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        await page.wait_for_timeout(400)
        await _veepn_dismiss_onboarding(page)
        await _veepn_dismiss_rate_us(page)

        st = await _veepn_connection_label(page)
        print(f"  {DIM}VeepN статус: {st}{RST}")

        if st == "on" and flipkart:
            cc_now = await _veepn_connected_country_hint(context, eid)
            proxy_ok = await _vpn_is_proxy_active(context, eid)
            if not proxy_ok:
                print(f"  {DIM}VeepN: Подключено — жду proxy…{RST}")
                for _ in range(28):
                    await asyncio.sleep(0.4)
                    if await _vpn_is_proxy_active(context, eid):
                        proxy_ok = True
                        break
            if not proxy_ok:
                # UI сказал on, proxy мёртв — добить круг Win32 и ещё раз ждать
                print(f"  {Y}VeepN: UI on, но proxy нет — клик круга на экране…{RST}")
                hwnd = await asyncio.to_thread(_win_chrome_main_hwnd)
                if hwnd:
                    await asyncio.to_thread(_win_click_veepn_power, hwnd)
                    await _veepn_dismiss_limited_upsell(page)
                    for _ in range(20):
                        await asyncio.sleep(0.4)
                        if await _vpn_is_proxy_active(context, eid):
                            proxy_ok = True
                            break
            if not proxy_ok:
                print(f"  {Y}⚠ VeepN: Подключено в UI, но proxy не поднялся{RST}")
                # не врать Flipkart — идём в сценарий включения
            else:
                print(
                    f"  {G}✔ VeepN: уже Подключено "
                    f"({(cc_now or '?').upper()}) — сразу Flipkart{RST}"
                )
                await _veepn_dismiss_limited_upsell(page)
                await _veepn_finalize_connected(
                    context, eid, via=(cc_now or "vpn").upper(),
                )
                return True
        elif st == "on" and not flipkart:
            print(f"  {DIM}VeepN: отключаю для смены сессии…{RST}")
            click = await _veepn_ui_click_power(page)
            if not click.get("clicked") and not click.get("already"):
                print(f"  {DIM}клик отключения: {click}{RST}")
            await page.wait_for_timeout(800)
            if not await _veepn_wait_until_off(page, seconds=20.0):
                await _veepn_soft_api_disconnect(context, eid)
                await asyncio.sleep(1.0)

        # С ВЫКЛ: карточка страны → USA → питание
        print(f"  {DIM}VeepN: ВЫКЛ → USA (карточка → список → питание)…{RST}")
        usa_ok = False
        if flipkart:
            with contextlib.suppress(Exception):
                usa_ok = await _veepn_ui_select_country_and_connect(
                    context, page, eid, _VPN_DEFAULT_COUNTRY,
                )
            if not usa_ok:
                with contextlib.suppress(Exception):
                    usa_ok = await _veepn_ui_reconnect_country(context, _VPN_DEFAULT_COUNTRY)
        if not usa_ok:
            with contextlib.suppress(Exception):
                usa_ok = await _veepn_connect_country_prefer_api(
                    context, eid, _VPN_DEFAULT_COUNTRY,
                )
        if not usa_ok:
            for attempt in range(1, 3):
                st = await _veepn_connection_label(page)
                if st == "on":
                    break
                if page.is_closed():
                    page = await _veepn_open_popup_via_puzzle_menu(context, eid) or page
                print(f"  {DIM}VeepN: ВЫКЛ → питание ({attempt}/2)…{RST}")
                click = await _veepn_ui_click_power(page)
                if isinstance(click, dict) and not click.get("clicked"):
                    print(f"  {DIM}клик: {click}{RST}")
                await page.wait_for_timeout(800)
                await _veepn_dismiss_limited_upsell(page)
                if await _veepn_wait_until_on(page, seconds=20.0 if quick else 30.0):
                    break
            else:
                if flipkart:
                    with contextlib.suppress(Exception):
                        usa_ok = await _veepn_ensure_usa_for_flipkart(context, eid)
                if not usa_ok and (await _veepn_connection_label(page)) != "on":
                    if not await _vpn_is_proxy_active(context, eid):
                        return False

        for _ in range(12):
            if await _vpn_is_proxy_active(context, eid):
                break
            await asyncio.sleep(0.4)

        if flipkart and not usa_ok:
            await _veepn_ensure_usa_for_flipkart(context, eid)

        cc = await _veepn_connected_country_hint(context, eid) or (
            _VPN_DEFAULT_COUNTRY if flipkart else ""
        )
        await _veepn_finalize_connected(context, eid, via=(cc or "vpn").upper())
        return bool(await _vpn_is_proxy_active(context, eid) or (
            await _veepn_connection_label(page) == "on"
        ))
    except Exception as e:
        print(f"  {DIM}VeepN connect: {str(e)[:120]}{RST}")
        return await _vpn_is_proxy_active(context, eid)


async def _vpn_popup_go_main(pop, eid: str) -> bool:
    """Вернуться на главный экран Connect (не Exceptions/Settings)."""
    main_url = f"chrome-extension://{eid}/src/popup/popup.html"
    for _ in range(8):
        try:
            on_main = await pop.evaluate("""() => !!document.querySelector('.main-connect-button')""")
            if on_main:
                return True
            body = (await pop.evaluate("() => document.body ? document.body.innerText : ''")).lower()
            if (
                "enter website address" in body
                or "add exceptions to the vpn" in body
                or body.strip() == "exceptions"
                or ("exceptions" in body and "add website" in body)
            ):
                clicked = await pop.evaluate("""() => {
                    const bar = document.querySelector('.top-bar');
                    if (bar) { bar.click(); return true; }
                    return false;
                }""")
                if clicked:
                    await pop.wait_for_timeout(900)
                    continue
            try:
                await pop.goto(main_url, wait_until="domcontentloaded", timeout=12_000)
                await pop.wait_for_timeout(1_500)
            except Exception:
                pass
            if await pop.evaluate("() => !!document.querySelector('.main-connect-button')"):
                return True
        except Exception:
            pass
        await pop.wait_for_timeout(500)
    return False


_LAST_CHROMIUM_CLOSED_AT: float = 0.0
_VPN_CHROME_COOLDOWN_SEC = 6.0
_vpn_bg_status: dict = {"state": "idle", "message": ""}
_vpn_bg_lock = threading.Lock()


def get_vpn_bg_status() -> dict:
    with _vpn_bg_lock:
        return dict(_vpn_bg_status)


def _set_vpn_bg_status(state: str, message: str = "") -> None:
    global _vpn_bg_status
    with _vpn_bg_lock:
        _vpn_bg_status = {"state": state, "message": message}


def scan_profiles_extension_status() -> dict:
    """Быстрая проверка профилей на VPN-расширение (без браузера)."""
    profiles = _iter_profile_dirs()
    with_ext = [p for p in profiles if _profile_has_vpn_extension(p)]
    missing = [p for p in profiles if not _profile_has_vpn_extension(p)]
    return {
        "total": len(profiles),
        "with_ext": len(with_ext),
        "missing": len(missing),
        "missing_names": [p.name for p in missing],
    }


def sync_vpn_extension_status() -> dict:
    """Синхронизирует vpn_bg_status с фактическим состоянием расширений."""
    cur = get_vpn_bg_status()
    msg = str(cur.get("message") or "")
    state = cur.get("state", "idle")
    if state == "warming" and any(
        x in msg for x in ("Проверка VPN", "Flipkart", "VPN фон", "VPN OK")
    ):
        return cur
    if state not in ("installing", "warming", "idle"):
        return cur

    if not _vpn_extension_dir():
        _set_vpn_bg_status("disabled", "Сеть: прокси / личный VPN на ПК")
        return get_vpn_bg_status()

    scan = scan_profiles_extension_status()
    total = int(scan.get("total") or 0)
    with_ext = int(scan.get("with_ext") or 0)
    missing = int(scan.get("missing") or 0)

    if total == 0:
        _set_vpn_bg_status("idle", "Профили не найдены")
    elif missing == 0:
        _set_vpn_bg_status(
            "ready",
            f"Расширение {with_ext}/{total} · VPN при сценарии",
        )
    elif with_ext > 0:
        names = ", ".join(scan["missing_names"][:2])
        if missing > 2:
            names += "…"
        _set_vpn_bg_status(
            "ready",
            f"Расширение {with_ext}/{total} · без: {names}",
        )
    else:
        _set_vpn_bg_status(
            "error",
            f"Расширение не установлено ни в один из {total} профилей",
        )
    return get_vpn_bg_status()


def _offscreen_chrome_args(args: list[str]) -> list[str]:
    """Окно Chrome за пределами экрана без headless (popup VPN-расширения)."""
    out = [
        a for a in args
        if a not in ("--start-maximized", "--headless=new", "--headless=old", "--headless")
    ]
    if not any(a.startswith("--window-position") for a in out):
        out.extend([
            "--window-position=-32000,-32000",
            "--start-minimized",
        ])
    return out


def _hidden_chrome_args(args: list[str]) -> list[str]:
    """Headless Chrome — только без popup расширений (установка файлов)."""
    out = _offscreen_chrome_args(args)
    if not any("headless" in a for a in out):
        out.append("--headless=new")
    out.append("--disable-gpu")
    return out


def _vpn_browser_launch_kw(profile_path: Path | str | None = None) -> dict:
    """kwargs для фонового VPN: headed Chrome, окно вне экрана."""
    kw = _browser_launch_kw(
        headless=False, profile_path=profile_path, background_install=True,
    )
    kw["headless"] = False
    kw["args"] = _offscreen_chrome_args(kw.get("args", []))
    chrome = _find_chrome()
    if chrome and profile_path is not None and not _needs_load_extension(profile_path):
        kw["executable_path"] = chrome
    return kw


@contextlib.contextmanager
def _chrome_window_hider():
    """Windows: прячет окна Chrome/Chromium пока идёт фоновая операция."""
    if os.name != "nt":
        yield
        return
    import ctypes
    stop = threading.Event()

    def _loop() -> None:
        user32 = ctypes.windll.user32
        SW_HIDE = 0
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                c = cls.value.lower()
                if "chrome_widgetwin" in c:
                    user32.ShowWindow(hwnd, SW_HIDE)
            return True

        while not stop.is_set():
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
            stop.wait(0.2)

    t = threading.Thread(target=_loop, daemon=True, name="chrome-hider")
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=2.0)


def _register_vpn_extension_prefs(
    profile_path: Path, eid: str, version: str, manifest: dict,
) -> None:
    """Регистрирует расширение в Preferences профиля Chrome."""
    prefs_path = profile_path / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs: dict = {}
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        except Exception:
            prefs = {}
    perms = manifest.get("permissions", [])
    hosts = manifest.get("host_permissions", [])
    perm_block = {
        "api": perms,
        "explicit_host": hosts,
        "manifest_permissions": perms,
    }
    now = str(int(time.time() * 1_000_000))
    prefs.setdefault("extensions", {}).setdefault("settings", {})[eid] = {
        "account_extension_type": 0,
        "active_permissions": perm_block,
        "creation_flags": 38,
        "first_install_time": now,
        "from_webstore": False,
        "granted_permissions": perm_block,
        "last_update_time": now,
        "location": 4,
        "manifest": manifest,
        "path": f"{eid}/{version}",
        "state": 1,
        "was_installed_by_default": False,
        "was_installed_by_oem": False,
    }
    # Не восстанавливать вкладки (vpnlyprotect.ru и т.п.) при следующем запуске Chrome
    prefs.setdefault("session", {})["restore_on_startup"] = 5
    prefs["session"]["startup_urls"] = []
    prefs_path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")


def _install_extension_filesystem(profile_path: Path, *, force: bool = False) -> bool:
    """Копирует VeepN в профиль. Без force — только успешные done-профили."""
    profile_path = Path(profile_path)
    if not force and not _profile_is_successful_done(profile_path):
        return False
    if _profile_has_vpn_extension(profile_path):
        return True
    ext_dir = _vpn_extension_dir()
    eid = _vpn_ext_id_for_install()
    if not ext_dir or not eid:
        return False
    try:
        src = Path(ext_dir)
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        version = str(manifest.get("version", "1.0.0"))
        dest = profile_path / "Default" / "Extensions" / eid / version
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        _register_vpn_extension_prefs(profile_path, eid, version, manifest)
        return _profile_has_vpn_extension(profile_path)
    except Exception as exc:
        print(f"  {Y}⚠ FS {profile_path.name}: {exc}{RST}")
        return False


def install_extensions_filesystem_all() -> int:
    """Устанавливает расширение только в успешные done-профили (без браузера)."""
    if not _vpn_extension_dir():
        return 0
    installed = 0
    for meta in _load_done_profiles(force=True):
        p = Path(meta.get("path") or "")
        if not p.is_dir() or _profile_has_vpn_extension(p):
            continue
        _set_vpn_bg_status("warming", f"Расширение → {p.name} (файлы)…")
        if _install_extension_filesystem(p):
            installed += 1
            print(f"  {G}✔ {p.name} (успешный профиль){RST}")
    return installed


async def _ensure_extension_in_profile(profile_path: Path) -> bool:
    """Ставит VPN-расширение: только успешные done-профили (файлы → headless)."""
    profile_path = Path(profile_path)
    if not _profile_is_successful_done(profile_path):
        return False
    if _profile_has_vpn_extension(profile_path):
        return True
    if not _vpn_extension_dir():
        return False
    if _install_extension_filesystem(profile_path):
        return True
    from playwright.async_api import async_playwright
    pw = None
    ctx = None
    try:
        await _vpn_chrome_cooldown(extra=1.0)
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile_path)
        kw = _browser_launch_kw(
            headless=True, profile_path=profile_path, background_install=True,
        )
        kw["args"] = _hidden_chrome_args(kw.get("args", []))
        with _chrome_window_hider():
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_path.resolve()), **kw)
            await _close_extension_startup_tabs(ctx)
            await asyncio.sleep(2.5)
        return _profile_has_vpn_extension(profile_path)
    except Exception as exc:
        print(f"  {Y}⚠ Расширение {profile_path.name}: {exc}{RST}")
        return False
    finally:
        if ctx:
            try:
                await ctx.close()
            finally:
                _note_chromium_closed()
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


def _vpn_extension_ui_names() -> list[str]:
    """Имена VPN-расширения для поиска на chrome://extensions/."""
    names = [
        "VPNLY", "VeePN", "VeepN", "Бесплатный VPN", "Free VPN",
        "Free VPN & Proxy", "VPN и прокси",
    ]
    ext = _vpn_extension_dir()
    if not ext:
        return names
    for loc in ("ru", "en"):
        msg = Path(ext) / "_locales" / loc / "messages.json"
        if not msg.is_file():
            continue
        with contextlib.suppress(Exception):
            data = json.loads(msg.read_text(encoding="utf-8"))
            n = str((data.get("app_name") or {}).get("message") or "").strip()
            if n and n not in names:
                names.insert(0, n)
    return names


async def _activate_vpn_extension_via_chrome_page(context) -> bool:
    """chrome://extensions/ → найти VPN в списке → включить → клик по карточке.

    Установка файлами уже сделана; здесь только UI выбора/включения.
    """
    if getattr(context, "_subhub_via_proxy", False) or not _vpn_extension_dir():
        return False
    eid = (await _vpn_ext_id(context)) or _vpn_ext_id_for_install() or ""
    names = _vpn_extension_ui_names()
    page = None
    try:
        print(f"  {DIM}chrome://extensions/ → выбираю VPN в списке…{RST}")
        page = await context.new_page()
        await page.goto("chrome://extensions/", wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(700)
        result = await page.evaluate(
            """async ({eid, names}) => {
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              const mgr = document.querySelector('extensions-manager');
              if (!mgr || !mgr.shadowRoot) return {ok: false, err: 'no-manager'};
              const toolbar = mgr.shadowRoot.querySelector('extensions-toolbar');
              const dev = toolbar && toolbar.shadowRoot
                && toolbar.shadowRoot.querySelector('#devMode');
              if (dev && !dev.checked) {
                dev.click();
                await sleep(350);
              }
              const list = mgr.shadowRoot.querySelector('extensions-item-list');
              const root = (list && list.shadowRoot) ? list.shadowRoot : mgr.shadowRoot;
              const items = root.querySelectorAll('extensions-item');
              const lowNames = names.map((n) => String(n).toLowerCase());
              for (const item of items) {
                const id = item.id || item.getAttribute('id') || '';
                const sr = item.shadowRoot;
                if (!sr) continue;
                const nameEl = sr.querySelector('#name, .name, #name-and-version #name');
                const name = ((nameEl && nameEl.textContent) || '').trim();
                const hit = (eid && id === eid) || lowNames.some(
                  (n) => n && name.toLowerCase().includes(n)
                );
                if (!hit) continue;
                const toggle = sr.querySelector(
                  '#enableToggle, cr-toggle#enableToggle, #enable-toggle, cr-toggle'
                );
                let enabled = true;
                if (toggle) {
                  enabled = !!(toggle.checked || toggle.hasAttribute('checked'));
                  if (!enabled) {
                    toggle.click();
                    await sleep(450);
                    enabled = !!(toggle.checked || toggle.hasAttribute('checked'));
                  }
                }
                // Клик по имени / карточке — «зайти» в расширение
                const clickTarget = nameEl || sr.querySelector('#card, .card, a#detailsButton');
                if (clickTarget) {
                  clickTarget.click();
                  await sleep(500);
                }
                return {ok: true, id, name, enabled};
              }
              return {ok: false, err: 'not-found', count: items.length};
            }""",
            {"eid": eid, "names": names},
        )
        if not isinstance(result, dict) or not result.get("ok"):
            err = (result or {}).get("err", "?") if isinstance(result, dict) else "?"
            cnt = (result or {}).get("count", "?") if isinstance(result, dict) else "?"
            print(f"  {Y}⚠ chrome://extensions/: VPN не найден ({err}, items={cnt}){RST}")
            return False
        print(
            f"  {G}✔ chrome://extensions/: «{result.get('name') or result.get('id')}»"
            f" · вкл={result.get('enabled')}{RST}"
        )
        # details URL иногда chrome://extensions/?id=...
        await page.wait_for_timeout(400)
        return True
    except Exception as exc:
        print(f"  {Y}⚠ chrome://extensions/: {exc}{RST}")
        return False
    finally:
        if page is not None:
            with contextlib.suppress(Exception):
                await page.close()


async def _prepare_profile_vpn(profile_path: Path | str, *, label: str = "") -> tuple[bool, str]:
    """Фон: расширение (если нет) + VPN + проверка Flipkart. Браузер закрывается."""
    if not _vpn_extension_dir():
        return True, ""
    profile_path = Path(profile_path)
    tag = label or _phone_from_path(profile_path) or profile_path.name
    if _is_profile_locked(profile_path):
        _clear_stale_profile_locks(profile_path)
    _set_vpn_bg_status("warming", f"VPN фон · {profile_path.name}")
    print(f"  {DIM}[{tag}] VPN в фоне: расширение + подключение…{RST}")
    if not await _ensure_extension_in_profile(profile_path):
        _set_vpn_bg_status("error", f"Нет расширения · {profile_path.name}")
        return False, "VPN-расширение не установлено в профиль"
    await _vpn_chrome_cooldown(extra=1.0)
    pw = None
    ctx = None
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile_path)
        kw = _browser_launch_kw(
            headless=False, profile_path=profile_path, background_install=True,
        )
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()), **kw)
        await _close_extension_startup_tabs(ctx)
        if not await _vpn_connect_on_use(ctx, profile_path):
            _set_vpn_bg_status("error", "VPN не подключился")
            return False, "VPN не подключился (фон)"
        page = await _main_work_page(ctx)
        if not await _verify_flipkart_reachable(page, "https://www.flipkart.com/"):
            _set_vpn_bg_status("error", "Flipkart недоступен после VPN")
            return False, "Flipkart недоступен после VPN"
        print(f"  {G}✔ [{tag}] VPN готов — можно открывать Flipkart{RST}")
        _set_vpn_bg_status("ready", f"VPN OK · {profile_path.name}")
        return True, ""
    except Exception as exc:
        err = str(exc)[:120]
        _set_vpn_bg_status("error", err)
        return False, err
    finally:
        await _close_browser_session(ctx, pw, profile_path, disconnect_vpn=True)
        await _vpn_chrome_cooldown(extra=2.0)


def prepare_profile_vpn_sync(profile_path: Path | str, *, label: str = "") -> tuple[bool, str]:
    try:
        return asyncio.run(_prepare_profile_vpn(profile_path, label=label))
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(_prepare_profile_vpn(profile_path, label=label)),
            ).result()
    except Exception as exc:
        return False, str(exc)[:120]


async def _warmup_vpn_extension() -> bool:
    """Устаревшее имя — только установка расширений в профили без VPN."""
    n = await _bg_install_extensions_on_profiles()
    _set_vpn_bg_status("ready", f"Расширения установлены ({n} проф.)" if n else "Расширения на месте")
    return True


def _note_chromium_closed() -> None:
    """Фиксирует момент закрытия Chromium — VPNLY не успевает в новом окне сразу."""
    global _LAST_CHROMIUM_CLOSED_AT
    _LAST_CHROMIUM_CLOSED_AT = time.monotonic()


def _is_vpn_junk_url(url: str) -> bool:
    u = (url or "").lower()
    if _vpn_is_veepn():
        return "chromewebdata" in u
    return (
        "vpnlyprotect.ru" in u
        or "errors.edgesuite.net" in u
        or "chromewebdata" in u
    )


def _is_junk_url(url: str) -> bool:
    u = (url or "").lower()
    return _is_vpn_junk_url(u) or u in ("about:blank", "chrome://newtab/")


async def _block_vpn_junk_routes(context) -> None:
    """Не открывать vpnlyprotect.ru (403) — только для VPNLY."""
    if _vpn_is_veepn():
        return

    async def _handler(route) -> None:
        try:
            if "vpnlyprotect.ru" in (route.request.url or "").lower():
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    try:
        await context.route("**/*vpnlyprotect.ru/**", _handler)
    except Exception:
        pass


async def _close_junk_tabs(context) -> None:
    """Закрывает vpnlyprotect.ru / 403 — даже если это единственная вкладка."""
    for p in list(context.pages):
        try:
            url = (p.url or "").lower()
            if not _is_vpn_junk_url(url):
                continue
            if len(context.pages) > 1:
                await p.close()
            else:
                await p.goto("about:blank", wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass


def _is_blank_tab_url(url: str) -> bool:
    u = (url or "").lower().strip()
    return u in ("", "about:blank", "chrome://newtab/")


async def _close_extra_blank_tabs(context, keep=None) -> None:
    """Закрывает лишние about:blank / new tab (оставляет одну вкладку keep)."""
    blanks = [p for p in context.pages if _is_blank_tab_url(p.url or "")]
    if len(blanks) <= 1:
        return
    keeper = keep if keep in blanks else blanks[0]
    for p in blanks:
        if p is keeper:
            continue
        if len(context.pages) <= 1:
            break
        with contextlib.suppress(Exception):
            await p.close()


async def _keep_only_flipkart_tabs(context, prefer_page=None):
    """Закрывает about:blank, extension и прочее — только flipkart.com."""
    flipkart_pages = [
        p for p in context.pages if "flipkart.com" in (p.url or "").lower()
    ]
    if not flipkart_pages:
        return prefer_page
    keeper = prefer_page if prefer_page in flipkart_pages else flipkart_pages[0]
    for p in list(context.pages):
        if p is keeper:
            continue
        with contextlib.suppress(Exception):
            await p.close()
    with contextlib.suppress(Exception):
        await keeper.bring_to_front()
    return keeper


def _is_extension_tab_url(url: str) -> bool:
    u = (url or "").lower()
    return u.startswith("chrome-extension://") or _is_vpn_junk_url(u)


async def _dismiss_all_veepn_welcome(context) -> None:
    """Закрывает onboarding VeepN («Thank you for installing» и welcome)."""
    for p in list(context.pages):
        try:
            u = (p.url or "").lower()
            title = ""
            try:
                title = (await p.title() or "").lower()
            except Exception:
                pass
            if (
                u.startswith("chrome-extension://")
                or "veepn" in title
                or "installing" in title
                or "thank you" in title
            ):
                await _veepn_dismiss_onboarding(p)
        except Exception:
            pass


async def _ensure_single_work_page(context):
    """Одна рабочая вкладка без мусора extension/about:blank."""
    keeper = None
    for p in context.pages:
        if "flipkart.com" in (p.url or "").lower():
            keeper = p
            break
    if not keeper:
        for p in context.pages:
            u = p.url or ""
            if not _is_extension_tab_url(u) and not _is_blank_tab_url(u):
                keeper = p
                break
    if not keeper:
        for p in context.pages:
            u = p.url or ""
            if not _is_extension_tab_url(u):
                keeper = p
                break
    if not keeper:
        keeper = await context.new_page()
    for p in list(context.pages):
        if p is keeper:
            continue
        try:
            await p.close()
        except Exception:
            pass
    return keeper


async def _main_work_page(context):
    """Рабочая вкладка: убрать vpnlyprotect, взять нормальную или создать новую."""
    await _close_junk_tabs(context)
    for p in context.pages:
        url = (p.url or "").lower()
        if not _is_vpn_junk_url(url) and not url.startswith("chrome-extension://"):
            if not _is_blank_tab_url(url) or len(context.pages) == 1:
                return p
    return await _ensure_single_work_page(context)


async def _vpn_chrome_cooldown(extra: float = 0.0) -> None:
    """Пауза после закрытия предыдущего Chromium, чтобы VPNLY успел в новом окне."""
    elapsed = time.monotonic() - _LAST_CHROMIUM_CLOSED_AT
    wait = max(0.0, _VPN_CHROME_COOLDOWN_SEC + extra - elapsed)
    if wait > 0:
        print(f"  {DIM}Пауза {wait:.0f}s — ждём готовности VPN в новом браузере...{RST}")
        await asyncio.sleep(wait)


async def _require_vpn_connected(context) -> bool:
    """Подключает VPN; при наличии расширения без VPN — False (2 попытки)."""
    if not _vpn_extension_dir():
        return True
    await _close_junk_tabs(context)
    if await _ensure_vpn_connected(context):
        return True
    print(f"  {Y}Повторная попытка подключить VPN...{RST}")
    await _vpn_chrome_cooldown(extra=3.0)
    return await _ensure_vpn_connected(context)


async def _verify_flipkart_reachable(page, url: str = "https://www.flipkart.com/account/login?ret=/") -> bool:
    """Проверяет, что Flipkart открывается без Access Denied в текущем браузере."""
    ok, _ = await _open_flipkart_page(page.context, url)
    return ok


async def _cdp_navigate(page, url: str, *, timeout: float = 90.0) -> tuple[bool, str]:
    """Навигация через CDP — надёжнее при активном VPN-proxy."""
    try:
        cdp = await page.context.new_cdp_session(page)
    except Exception as exc:
        return False, f"CDP: {exc}"[:120]
    try:
        with contextlib.suppress(Exception):
            await cdp.send("Page.enable")
        result = await cdp.send("Page.navigate", {"url": url})
        err = (result or {}).get("errorText") or ""
        if err:
            return False, err[:120]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cur = (page.url or "").lower()
            if "flipkart.com" in cur:
                return True, ""
            await asyncio.sleep(0.35)
        return False, f"CDP timeout · {(page.url or '')[:60]}"
    except Exception as exc:
        return False, str(exc)[:120]
    finally:
        with contextlib.suppress(Exception):
            await cdp.detach()


async def _force_navigate_flipkart(
    page, url: str, *, label: str = "", fast: bool = False,
) -> tuple[bool, str]:
    """Принудительно открывает Flipkart (CDP + goto + JS fallback).

    fast=True — один быстрый проход по целевому URL (happy path).
    """
    try:
        if page.is_closed():
            return False, "вкладка закрыта"
    except Exception:
        return False, "вкладка недоступна"

    targets: list[str] = []
    for t in (
        url,
        "https://www.flipkart.com/flipkart-black-store",
        "https://www.flipkart.com/",
    ):
        if t and t not in targets:
            targets.append(t)
    if fast:
        targets = targets[:1]

    async def _looks_ok() -> tuple[bool, str]:
        cur = (page.url or "").lower()
        if "flipkart.com" not in cur:
            return False, f"остался на {(page.url or '')[:70]}"
        body = ""
        try:
            body = (await page.evaluate(
                "() => (document.body?.innerText || '').slice(0, 300)"
            )).lower()
        except Exception:
            pass
        if "access denied" in body or "permission to access" in body:
            return False, "Access Denied (VPN / смените страну, USA)"
        with contextlib.suppress(Exception):
            await page.bring_to_front()
        return True, ""

    last_err = ""
    attempts = 1 if fast else 3
    # fast: короткий таймаут — мёртвый HTTP-прокси не должен висеть минутами
    cdp_timeout = 10.0 if fast else 22.0
    goto_timeout = 16_000 if fast else 45_000

    for target in targets:
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(0.8)

            print(f"  {DIM}→ {target[:88]}{'…' if len(target) > 88 else ''}{RST}")

            ok, err = await _cdp_navigate(page, target, timeout=cdp_timeout)
            if ok:
                fine, why = await _looks_ok()
                if fine:
                    return True, ""
                last_err = why or err or last_err
            elif err:
                last_err = err

            for wait_until in (("commit",) if fast else ("domcontentloaded", "commit")):
                try:
                    await page.goto(target, wait_until=wait_until, timeout=goto_timeout)
                    await asyncio.sleep(0.35)
                    fine, why = await _looks_ok()
                    if fine:
                        return True, ""
                    last_err = why
                except Exception as exc:
                    last_err = str(exc)[:150]

            if fast:
                break

            try:
                await page.evaluate("(u) => { window.location.assign(u); }", target)
                await page.wait_for_timeout(2_200)
                fine, why = await _looks_ok()
                if fine:
                    return True, ""
                last_err = why
            except Exception as exc:
                last_err = str(exc)[:150]

            try:
                safe = target.replace("'", "%27")
                await page.set_content(
                    f"<!DOCTYPE html><html><head>"
                    f'<meta http-equiv="refresh" content="0;url={safe}">'
                    f"</head><body><p>Opening Flipkart…</p></body></html>",
                    wait_until="domcontentloaded",
                    timeout=10_000,
                )
                await page.wait_for_timeout(1_800)
                fine, why = await _looks_ok()
                if fine:
                    return True, ""
                last_err = why
            except Exception as exc:
                last_err = str(exc)[:150]

    suffix = f" (+91 {label})" if label else ""
    print(f"  {Y}⚠ Flipkart не открылся{suffix}: {last_err}{RST}")
    return False, last_err


async def _open_flipkart_page(
    ctx, url: str, *, label: str = "", work_page=None,
) -> tuple[bool, object]:
    """Открывает Flipkart на рабочей вкладке.

    VeepN-путь — только если расширение реально загружено в контексте.
    Иначе (прокси Playwright / direct) — сразу goto, без ожидания VPN UI
    (иначе зависание на about:blank).
    """
    page = work_page or await _ensure_single_work_page(ctx)
    with contextlib.suppress(Exception):
        await _maximize_window(ctx, page)
        await page.bring_to_front()
    # HTTP-прокси / direct: не включаем VeepN, даже если он уже в профиле
    if _context_skip_vpn(ctx):
        with contextlib.suppress(Exception):
            if await _vpn_ext_id(ctx):
                await _vpn_disconnect(ctx)
        ok, _ = await _force_navigate_flipkart(
            page, url, label=label, fast=True,
        )
    else:
        eid = await _vpn_ext_id(ctx)
        if eid and _vpn_extension_dir():
            await _dismiss_all_veepn_welcome(ctx)
            await _close_vpn_extension_tabs(ctx, eid)
            ok, page, _ = await _navigate_flipkart_resilient(
                ctx, page, url, label=label,
            )
        else:
            # HTTP-прокси / PC VPN — без VeepN
            ok, _ = await _force_navigate_flipkart(
                page, url, label=label, fast=True,
            )
    if ok:
        page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)
    return ok, page


async def _wait_vpn_service_worker(context, eid: str, timeout: float = 18.0):
    """Ждёт service worker VPN-расширения (MV3 — ленивый старт)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for sw in list(getattr(context, "service_workers", []) or []):
            try:
                if eid in (sw.url or ""):
                    return sw
            except Exception:
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            sw = await asyncio.wait_for(
                context.wait_for_event("serviceworker"),
                timeout=min(2.0, remaining),
            )
            if eid in (sw.url or ""):
                return sw
        except Exception:
            pass
        await asyncio.sleep(0.35)
    return None


async def _wait_vpn_proxy_ready(
    context, eid: str | None = None, *, timeout: float = 30.0,
) -> bool:
    """Ждёт, пока VeepN/VPNLY реально включит proxy (не только UI «connected»)."""
    if not _vpn_extension_dir():
        return True
    if not eid:
        eid = await _vpn_ext_id(context)
    if not eid:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await _vpn_is_proxy_active(context, eid):
            await asyncio.sleep(0.55)  # короткая стабилизация proxy
            return True
        await asyncio.sleep(0.35)
    return False


async def _wake_vpn_extension(context, eid: str) -> None:
    """Будит MV3 service worker расширения (временная вкладка, не рабочая)."""
    page = None
    owned = False
    try:
        for p in context.pages:
            if _is_extension_tab_url(p.url or ""):
                page = p
                break
        if not page:
            page = await context.new_page()
            owned = True
        if not (page.url or "").startswith("http"):
            with contextlib.suppress(Exception):
                await page.goto("about:blank", wait_until="domcontentloaded", timeout=8_000)
        await _wait_vpn_service_worker(context, eid, timeout=6.0)
        await asyncio.sleep(0.8)
    finally:
        if owned and page:
            with contextlib.suppress(Exception):
                await page.close()


async def _vpn_proxy_mode(context, eid: str) -> str | None:
    """Режим chrome.proxy: direct/system = VPN выкл."""
    sw = await _wait_vpn_service_worker(context, eid, timeout=4.0)
    if not sw:
        return None
    try:
        mode = await sw.evaluate("""async () => {
            const s = await chrome.proxy.settings.get({});
            return (s && s.value && s.value.mode) ? s.value.mode : 'direct';
        }""")
        return str(mode or "direct").lower()
    except Exception:
        return None


async def _vpn_is_proxy_active(context, eid: str) -> bool:
    mode = await _vpn_proxy_mode(context, eid)
    return bool(mode and mode not in ("direct", "system", "auto_detect"))


async def _vpnly_enable_server(context, eid: str, server: dict) -> bool:
    """Включает VPNLY на одном сервере через enableProxy API."""
    import json as _json

    await _wake_vpn_extension(context, eid)
    sw = await _wait_vpn_service_worker(context, eid, timeout=12.0)
    pick_json = _json.dumps(server)
    meta_json = _json.dumps([{"uuid": server["uuid"], "city": server["city"]}])
    _js = f"""async () => {{
        const pick = {pick_json};
        const meta = {meta_json};
        await chrome.storage.local.set({{
            configsFree: meta,
            consent: true,
            agreementAccepted: true,
            'proxy-vpn.currentServer': pick,
            currentServer: pick,
        }});
        try {{
            if (chrome.offscreen) {{
                const clients = await self.clients.matchAll();
                const hasOff = clients.some(c => (c.url || '').includes('offscreen.html'));
                if (!hasOff) {{
                    await chrome.offscreen.createDocument({{
                        url: 'offscreen.html',
                        reasons: [chrome.offscreen.Reason.WORKERS],
                        justification: 'VPN authentication',
                    }});
                    await new Promise(r => setTimeout(r, 1200));
                }}
            }}
        }} catch (e) {{}}
        try {{
            await chrome.runtime.sendMessage({{
                type: 'enableProxy',
                payload: pick,
            }});
            for (let i = 0; i < 24; i++) {{
                await new Promise(r => setTimeout(r, 500));
                const mode = await chrome.proxy.settings.get({{}});
                const m = (mode && mode.value && mode.value.mode) || 'direct';
                if (m && m !== 'direct' && m !== 'system' && m !== 'auto_detect') {{
                    return {{ ok: true }};
                }}
            }}
        }} catch (e) {{}}
        return {{ ok: false }};
    }}"""

    if sw:
        try:
            result = await sw.evaluate(_js)
            if result and result.get("ok"):
                return True
        except Exception:
            pass

    page = None
    try:
        page = await context.new_page()
        await page.goto(
            f"chrome-extension://{eid}/offscreen.html",
            wait_until="domcontentloaded", timeout=15_000,
        )
        await page.wait_for_timeout(2_000)
        result = await page.evaluate(_js)
        return bool(result and result.get("ok"))
    except Exception:
        return False
    finally:
        if page:
            with contextlib.suppress(Exception):
                await page.close()


async def _vpn_send_enable_proxy(context, eid: str) -> bool:
    """Включает VPN: бесплатные серверы VPNLY (USA первым для Flipkart)."""
    servers = _vpnly_servers_for_flipkart()
    for pick in servers:
        if await _vpnly_enable_server(context, eid, pick):
            cc = _vpnly_country_code(pick)
            print(f"  {G}✔ VPN: бесплатный сервер ({cc.upper()}){RST}")
            return True
    return False


async def _open_vpn_popup_page(context, eid: str):
    """Открывает popup VPN-расширения на главном экране Connect."""
    popup_url = f"chrome-extension://{eid}/src/popup/popup.html"
    pop = None
    for p in list(context.pages):
        try:
            if (p.url or "").startswith(f"chrome-extension://{eid}"):
                pop = p
                break
        except Exception:
            pass

    async def _open_via_cdp() -> "object | None":
        anchor = context.pages[0] if context.pages else await context.new_page()
        cdp = await context.new_cdp_session(anchor)
        created = await cdp.send("Target.createTarget", {"url": popup_url})
        target_id = created.get("targetId")
        if not target_id:
            return None
        for _ in range(50):
            for p in list(context.pages):
                try:
                    u = p.url or ""
                    if u.startswith(f"chrome-extension://{eid}") and "chromewebdata" not in u:
                        return p
                except Exception:
                    pass
            await asyncio.sleep(0.25)
        return None

    if not pop:
        pop = await _open_via_cdp()
        if not pop:
            pop = await context.new_page()
            try:
                await pop.goto(popup_url, wait_until="load", timeout=20_000)
            except Exception as first_err:
                try:
                    await pop.close()
                except Exception:
                    pass
                pop2 = await _open_via_cdp()
                if pop2:
                    pop = pop2
                else:
                    raise first_err

    try:
        await pop.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        pass
    await _vpn_popup_go_main(pop, eid)
    return pop


async def _ensure_vpn_connected(context, *, quick: bool = False, flipkart: bool = True) -> bool:
    """Подключение VPN: VeepN (по умолчанию) или VPNLY. По умолчанию страна = USA."""
    if not _vpn_extension_dir():
        return False
    if _vpn_is_veepn():
        return await _ensure_veepn_connected(context, quick=quick, flipkart=flipkart)
    return await _ensure_vpnly_connected(context, flipkart=flipkart)


async def _ensure_vpnly_connected(context, *, flipkart: bool = True) -> bool:
    """VPNLY: enableProxy через service worker, затем popup UI как fallback.
    Ждёт активный proxy и/или статус «Защищено». По умолчанию USA."""
    if not _vpn_extension_dir():
        return False
    eid = await _vpn_ext_id(context)
    if not eid:
        print(f"  {Y}⚠ VPN: не удалось определить ID расширения — пропускаю{RST}")
        return False

    async def _connected(pop) -> bool:
        """True, если статус «Защищено»/Protected (исключая «Не защищено»)."""
        try:
            b = (await pop.evaluate("() => document.body ? document.body.innerText : ''")).lower()
        except Exception:
            return False
        if "не защищено" in b or "not protected" in b or "не подключено" in b:
            return False
        return ("защищено" in b or "protected" in b or "отключить" in b
                or "disconnect" in b)

    async def _accept_consent(pop) -> None:
        for _sel in ("button.modal-consent__btn",):
            try:
                _loc = pop.locator(_sel).first
                if await _loc.count() > 0 and await _loc.is_visible():
                    await _loc.click(timeout=2_000)
                    await pop.wait_for_timeout(1_200)
                    return
            except Exception:
                pass
        for _t in ("Согласиться и продолжить", "Accept and continue", "Agree and continue"):
            try:
                _loc = pop.get_by_text(_t, exact=True).first
                if await _loc.count() > 0 and await _loc.is_visible():
                    await _loc.click(timeout=2_000)
                    await pop.wait_for_timeout(1_200)
                    return
            except Exception:
                pass

    async def _click_connect(pop) -> bool:
        try:
            await pop.wait_for_selector(
                ".main-connect-button, button.v-button, .main-action__btn",
                state="visible", timeout=15_000,
            )
        except Exception:
            pass
        for _sel in (".main-connect-button", ".main-action__btn", "button.v-button"):
            try:
                _loc = pop.locator(_sel).first
                if await _loc.count() > 0 and await _loc.is_visible():
                    await _loc.click(timeout=3_000)
                    return True
            except Exception:
                pass
        for _t in ("Подключить", "Connect", "Подключиться", "Turn on"):
            try:
                _loc = pop.get_by_role("button", name=_t, exact=True).first
                if await _loc.count() > 0 and await _loc.is_visible():
                    await _loc.click(timeout=3_000)
                    return True
            except Exception:
                pass
        try:
            clicked = await pop.evaluate(r"""() => {
                const want = ['подключить','connect','подключиться','turn on'];
                const nodes = [
                    ...document.querySelectorAll('.main-connect-button, button'),
                ];
                for (const el of nodes) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (!want.some(w => t === w || t.includes(w))) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 40 || el.offsetParent === null) continue;
                    el.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                return True
        except Exception:
            pass
        return False

    async def _wait_vpn_active(pop=None, *, seconds: int = 30) -> bool:
        for _ in range(seconds):
            if await _vpn_is_proxy_active(context, eid):
                return True
            if pop and await _connected(pop):
                return True
            await asyncio.sleep(1.0)
        return False

    pop = None
    try:
        await _vpn_chrome_cooldown()
        await _close_junk_tabs(context)
        await asyncio.sleep(2.0)

        if await _vpn_is_proxy_active(context, eid) and not flipkart:
            print(f"  {G}✔ VPN уже подключён{RST}")
            return True

        # Flipkart / default: всегда включаем через USA-first список серверов
        if flipkart and await _vpn_is_proxy_active(context, eid):
            print(f"  {Y}⚠ VPNLY: был подключён — переподключаю на USA…{RST}")
            await _vpn_disconnect(context)
            await asyncio.sleep(1.2)

        # 1) Прямое включение через background API (без открытия popup).
        for _api_try in range(3):
            if await _vpn_send_enable_proxy(context, eid):
                if await _wait_vpn_active(seconds=22):
                    await _close_vpn_extension_tabs(context, eid)
                    print(f"  {G}✔ VPN подключён (USA){RST}")
                    return True
            if _api_try < 2:
                await asyncio.sleep(2.0)

        # 2) Fallback: popup UI — только если API не сработал; сразу на главный экран.
        try:
            pop = await _open_vpn_popup_page(context, eid)
        except Exception as _pop_err:
            print(f"  {DIM}VPN popup недоступен ({str(_pop_err)[:60]}) — жду proxy…{RST}")
            if await _wait_vpn_active(seconds=15):
                print(f"  {G}✔ VPN подключён{RST}")
                return True
            pop = None
        if pop:
            await _vpn_popup_go_main(pop, eid)
            await pop.wait_for_timeout(2_000)

            if await _connected(pop) or await _vpn_is_proxy_active(context, eid):
                await _close_vpn_extension_tabs(context, eid)
                print(f"  {G}✔ VPN уже подключён{RST}")
                return True

            for _try in range(5):
                if _try > 0:
                    await _vpn_popup_go_main(pop, eid)
                    try:
                        await pop.reload(wait_until="domcontentloaded", timeout=15_000)
                        await pop.wait_for_timeout(2_000)
                        await _vpn_popup_go_main(pop, eid)
                    except Exception:
                        pass
                await _accept_consent(pop)
                await _vpn_popup_go_main(pop, eid)
                if await _click_connect(pop):
                    print(f"  {DIM}VPN: автоклик Connect…{RST}")
                if await _wait_vpn_active(pop, seconds=30):
                    await _close_vpn_extension_tabs(context, eid)
                    print(f"  {G}✔ VPN подключён{RST}")
                    return True
                if await _vpn_send_enable_proxy(context, eid):
                    if await _wait_vpn_active(pop, seconds=20):
                        await _close_vpn_extension_tabs(context, eid)
                        print(f"  {G}✔ VPN подключён{RST}")
                        return True
                print(f"  {Y}VPN ещё не подключился — повтор ({_try + 1}/5)…{RST}")

        print(f"  {Y}⚠ VPN: не удалось подключиться автоматически{RST}")
        return False
    except Exception as _ve:
        print(f"  {Y}⚠ VPN: ошибка подключения: {_ve}{RST}")
        return False
    finally:
        try:
            if pop:
                await pop.close()
        except Exception:
            pass


def _browser_launch_kw(headless: bool = False, use_bundled_chromium: bool = False,
                       phone: str = "", profile_path: Path | str | None = None,
                       background_install: bool = False,
                       use_vpn: bool | None = None,
                       proxy: dict | None = None, **_) -> dict:
    """Возвращает kwargs для launch_persistent_context.
    --load-extension только если расширения ещё нет в profile_path.
    use_vpn=False — Flipkart доступен напрямую: без расширения и VPN-аргументов.
    proxy — dict Playwright ({'server':..}) для входа через прокси вместо VeePN."""
    vp = random.choice(_MENU_VIEWPORTS)
    ua = random.choice(_MENU_USER_AGENTS)
    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-notifications",
        "--disable-save-password-bubble",
        "--disable-features=TranslateUI,OptimizationHints,MediaRouter,"
        "AutofillCreditCardSave,AutofillSaveCardBubble,"
        "AutofillAddressSavePrompt,AutofillEnableNewSaveCardBubbleUi,"
        "PasswordBubble,SavePasswordBubble",
        "--disable-renderer-backgrounding",
        "--disable-ipc-flooding-protection",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        f"--window-size={vp['width']},{vp['height'] + 74}",
    ]
    kw: dict = {
        "headless": headless,
        "args": args,
        "user_agent": ua,
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
        "extra_http_headers": {"Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8,hi;q=0.7"},
        "ignore_https_errors": True,
    }
    # Прокси задан — используем его вместо VeePN (расширение не грузим).
    if proxy and proxy.get("server"):
        pw_proxy = {k: v for k, v in proxy.items() if not k.startswith("_")}
        kw["proxy"] = pw_proxy
        use_vpn = False
    # Вход/tmp — без расширения; только успешные done-профили
    if use_vpn is not False and profile_path is not None:
        if not _profile_allows_vpn(profile_path):
            use_vpn = False
    elif use_vpn is None:
        use_vpn = False
    # ── VPN: Playwright Chromium + --load-extension (VeepN API не работает в Google Chrome automation).
    use_bundled_chromium = False
    try:
        _ext_dir = None if use_vpn is False else _vpn_extension_dir()
        if _ext_dir:
            kw["ignore_default_args"] = ["--disable-extensions", "--enable-automation"]
            use_bundled_chromium = True
            if background_install or _needs_load_extension(profile_path):
                headless = False
            # Путь с пробелами (…master 3\…) — в одном argv; на Win предпочитаем short path
            ext_arg = str(Path(_ext_dir).resolve())
            if os.name == "nt" and " " in ext_arg:
                with contextlib.suppress(Exception):
                    import ctypes
                    buf = ctypes.create_unicode_buffer(512)
                    if ctypes.windll.kernel32.GetShortPathNameW(ext_arg, buf, 512):
                        short = buf.value
                        if short and " " not in short:
                            ext_arg = short
            args.append("--disable-features=DisableLoadExtensionCommandLineSwitch")
            args.append(f"--disable-extensions-except={ext_arg}")
            args.append(f"--load-extension={ext_arg}")
    except Exception:
        pass

    if not headless:
        args.append("--start-maximized")
        kw["no_viewport"] = True
    else:
        kw["viewport"] = vp
    kw["headless"] = headless
    exe = _chrome_executable_for_profile(
        profile_path, force_bundled=use_bundled_chromium,
    )
    if exe:
        kw["executable_path"] = exe
    return kw


def _build_stealth_js_m() -> str:
    """Заимствует stealth-скрипт из LoginAutomation с рандомными значениями."""
    try:
        from main import LoginAutomation  # type: ignore
        return LoginAutomation._build_stealth_js()
    except Exception:
        return ""


def _pre_inject_chrome_prefs(profile_path: Path) -> None:
    """Записывает настройки в Preferences до запуска браузера.

    - Разрешает геолокацию для flipkart.com (убирает браузерный диалог).
    - Отключает сохранение карт и паролей в профиле.
    """
    import json as _json
    import time as _time

    prefs_file = profile_path / "Default" / "Preferences"
    prefs_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        prefs = _json.loads(prefs_file.read_text(encoding="utf-8")) if prefs_file.exists() else {}
    except Exception:
        prefs = {}

    ts = str(int(_time.time() * 1_000_000))

    # Геолокация: 1 = Allow для flipkart.com
    geo = (prefs
           .setdefault("profile", {})
           .setdefault("content_settings", {})
           .setdefault("exceptions", {})
           .setdefault("geolocation", {}))
    for origin_key in ["https://www.flipkart.com:443,*", "https://www.flipkart.com,*"]:
        geo[origin_key] = {"last_modified": ts, "setting": 1}

    # Отключаем сохранение карт, адресов и паролей
    prefs.setdefault("autofill", {})["credit_card_enabled"] = False
    prefs.setdefault("autofill", {})["profile_enabled"]     = False
    prefs["credentials_enable_service"] = False
    prefs.setdefault("profile", {})["password_manager_enabled"] = False

    # Помечаем прошлый сеанс как нормально завершённый — убирает popup "Восстановить страницы?"
    prefs.setdefault("profile", {})["exit_type"] = "Normal"
    prefs.setdefault("profile", {})["exited_cleanly"] = True

    try:
        prefs_file.write_text(_json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass



def screen_run_auto(tg_mode: str = "none", stop_at_email: bool = False):
    cls()
    use_telegram = (tg_mode in ("login", "intercept"))
    if stop_at_email:
        title = "ЗАПУСК: ВХОД С ДАННЫМИ" + (" + TELEGRAM" if use_telegram else "")
    else:
        title = "ЗАПУСК АВТОМАТИЗАЦИИ ВХОДА" + (" + TELEGRAM" if use_telegram else "")
    header(title, G)

    if not (_HERE / "config.yaml").exists():
        print(f"{R}  [!] Файл config.yaml не найден!{RST}")
        print(f"{DIM}      Создайте config.yaml рядом с menu.bat{RST}")
        pause()
        return

    # ── Запрос количества аккаунтов ──────────────────────────────────────────
    section("Сколько успешных входов нужно?")
    print()
    print(f"  {DIM}Успешный аккаунт = вход по номеру телефона + OTP на любом Chrome-профиле.{RST}")
    print(f"  {DIM}Параллельных Chrome-профилей (до 10) скрипт создаёт сам — это отдельный параметр.{RST}")
    print()
    target_count = None
    while True:
        raw = input(
            f"  {BLD}Сколько успешных входов сделать?{RST} {DIM}(Enter = из config.yaml){RST}: "
        ).strip()
        if raw == "":
            break       # используем значение из конфига
        if raw.isdigit() and int(raw) > 0:
            target_count = int(raw)
            break
        print(f"  {R}  Введите целое число больше 0{RST}")

    if target_count:
        print(f"\n  {G}Цель: {BLD}{target_count}{RST}{G} успешных вход(ов){RST}")
    else:
        print(f"\n  {DIM}Количество возьмём из config.yaml (auto_accounts){RST}")

    months = 3
    if stop_at_email:
        print()
        section("Тариф Black Membership")
        print()
        opt("1", "3 месяца  — ₹343", G)
        opt("2", "12 месяцев — ₹1,499", C)
        print()
        while True:
            tariff = input(f"  {BLD}Тариф [1/2, Enter = 3 мес.]: {RST}").strip()
            if tariff in ("", "1"):
                months = 3
                break
            if tariff == "2":
                months = 12
                break
            print(f"  {R}Введите 1 или 2{RST}")

    # ── Режим браузера ────────────────────────────────────────────────────────
    print()
    section("Режим браузера")
    print()
    opt("1", f"🌑 Фоновый (без окна)  — быстрее, меньше ресурсов", G)
    opt("2", f"🖥  Обычный  (с окном)  — видно процесс в реальном времени", C)
    print()
    while True:
        mode_raw = input(f"  {BLD}Режим [1/2, Enter = фоновый]: {RST}").strip()
        if mode_raw in ("", "1", "2"):
            break
        print(f"  {R}Введите 1 или 2{RST}")
    run_headless = (mode_raw != "2")
    mode_lbl = "фоновый (без окна)" if run_headless else "обычный (с окном)"
    print(f"\n  {DIM}Режим: {mode_lbl}{RST}")
    print()

    print(f"  {DIM}Для остановки нажмите  {RST}{BLD}{Y}Ctrl+C{RST}")
    if use_telegram:
        print(f"  {BLD}{M}[Telegram]{RST} {W}Бот активен! Отправьте ему сообщение для подписки.{RST}")
    print()
    section("Запуск автоматизации входа")
    print()

    import signal, threading as _thr

    # Запоминаем профили ДО запуска чтобы найти новые после
    _profiles_before = (
        set(DONE_PROFILES_DIR.glob("profile_*"))
        if DONE_PROFILES_DIR.exists() else set()
    )

    proc = None
    code = -1
    try:
        if stop_at_email:
            args = [sys.executable, str(Path(__file__)), "--full-cycle", "--stop-at-email", "--tariffs", str(months)]
        else:
            args = [sys.executable, str(_PKG / "main.py")]
            if tg_mode == "login":
                args.append("--tg-login")
            elif tg_mode == "intercept":
                args.append("--tg-intercept")
        if target_count:
            args += ["--accounts", str(target_count)]
        if run_headless:
            args.append("--headless")
        import os, signal
        creationflags = 0
        if os.name == "nt":
            # CREATE_NEW_PROCESS_GROUP — чтобы Ctrl+C консоли не убивал сразу,
            # а мы слали CTRL_BREAK адресно. Без CREATE_NO_WINDOW: процесс наследует
            # консоль и его вывод (покупка номеров, OTP, вход) виден в реальном
            # времени — иначе автоматизация «молчит» и кажется, что не работает.
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(args, creationflags=creationflags, cwd=str(_HERE))
        set_automation_proc(proc.pid, "login", "console")
        proc.wait()
        code = proc.returncode
    except KeyboardInterrupt:
        print(f"\n\n{Y}  [!] Остановка по Ctrl+C...{RST}")
        if proc and proc.poll() is None:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        code = -1
    except Exception as exc:
        print(f"\n{R}  Ошибка запуска: {exc}{RST}")
        code = -1
    finally:
        clear_automation_proc()

    print()
    if code == 0:
        section(f"Завершено успешно")
    elif code == -1:
        section(f"{Y}Остановлено пользователем{RST}")
    else:
        section(f"Завершено с ошибкой (exit code: {code})")

    # ── Проверка активации для новых профилей ────────────────────────────────
    if DONE_PROFILES_DIR.exists():
        _new_profiles = [
            p for p in DONE_PROFILES_DIR.glob("profile_*")
            if p.is_dir() and p not in _profiles_before
            and (p / ".profile_meta.json").exists()
        ]
        if _new_profiles:
            print(f"\n  {G}Найдено {len(_new_profiles)} новых профиля(ей) — запускаю проверку активации...{RST}")

            def _activation_worker(prof_path: Path):
                # Читаем username и даты из метаданных
                _un = prof_path.name
                _login_str_w = ""
                _issued_str_w = ""
                try:
                    import json as _j
                    _meta = _j.loads((prof_path / ".profile_meta.json").read_text(encoding="utf-8"))
                    _un = _meta.get("username", prof_path.name)
                    if _meta.get("login_ts"):
                        _login_str_w = _fmt_msk(float(_meta["login_ts"]))
                    if _meta.get("issued_ts"):
                        _issued_str_w = _fmt_msk(float(_meta["issued_ts"]))
                except Exception:
                    pass

                chk = asyncio.run(_check_black_store_activation(
                    prof_path, username=_un, headless=True))
                st  = chk.get("status", "unknown")
                vt  = chk.get("valid_till")
                err = chk.get("error")

                print(f"\n\n  ╔══ [ПРОВЕРКА] +91 {_un} ══╗")
                if err:
                    print(f"  ║  {R}Ошибка: {err}{RST}")
                    time.sleep(3)
                elif st == "explore_now":
                    print(f"  ║  {C}💳 Explore Now — доступен для оплаты{RST}")
                elif st == "activate_now":
                    _aurl = chk.get("activation_url", "")
                    _slink = chk.get("short_link", "")
                    _vt2 = chk.get("valid_till", "")
                    print(f"  ║  {G}⭐ Activate Now — доступен к выдаче{RST}")
                    if _aurl:
                        print(f"  ║  {G}🔗 Ссылка активации:{RST}")
                        if _slink and _slink != _aurl:
                            print(f"  ║  {G}Короткая: {_slink}{RST}")
                        print(f"  ║  {B}{_aurl}{RST}")
                        _send_tg_activation(_un, _aurl, _slink, _vt2,
                                            login_str=_login_str_w, issued_str=_issued_str_w)
                    else:
                        print(f"  ║  {Y}⏳ Ссылка не получена{RST}")
                elif st == "activated":
                    print(f"  ║  {M}✨ Activated{' до ' + vt if vt else ''} — аккаунт уже активирован{RST}")
                elif st == "not_logged_in":
                    print(f"  ║  {R}Не залогинен{RST}")
                else:
                    print(f"  ║  {Y}Статус неизвестен{RST}")
                print(f"  ╚{'═' * (len(_un) + 16)}╝\n")

            for _prof in _new_profiles:
                t = _thr.Thread(target=_activation_worker, args=(_prof,), daemon=True)
                t.start()
                print(f"  {DIM}▶ Проверка {_prof.name} запущена в фоне{RST}")

    pause()


def _profile_addr_meta(addr: dict | None) -> dict:
    """Поля адреса для .profile_meta.json (GUI показывает под «Создан»)."""
    if not addr:
        return {}
    name = str(addr.get("name") or "").strip()
    pin = str(addr.get("pincode") or "").strip()
    city = str(addr.get("city") or "").strip()
    state = str(addr.get("state") or "").strip()
    house = str(addr.get("house") or "").strip()
    road = str(addr.get("road") or "").strip()
    line = str(addr.get("address_line") or "").strip()
    if not line and (house or road):
        line = ", ".join(x for x in (house, road) if x)
    locality = str(addr.get("locality") or "").strip()
    phone = str(addr.get("phone") or "").strip()
    summary = " | ".join(
        x for x in (name, f"{pin} {city}".strip(), state) if x
    )
    out = {
        "address_name": name,
        "address_pincode": pin,
        "address_city": city,
        "address_state": state,
        "address_house": house,
        "address_road": road,
        "address_line": line,
        "address_locality": locality,
        "address_phone": phone,
        "address_summary": summary,
    }
    return {k: v for k, v in out.items() if v}


def _save_meta_field(profile_path: Path, **fields) -> bool:
    """Записывает/обновляет поля в .profile_meta.json профиля.

    Автоматически ведёт link_history: каждая новая/заменённая ссылка профиля
    (black_short_link / black_activation_link / issued_link) добавляется в
    список {"ts", "link"}. Первая уже существовавшая ссылка заносится задним
    числом при первом изменении."""
    meta_file = profile_path / ".profile_meta.json"
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        try:
            _new_link = ""
            # Приоритет: короткая (то, что реально выдаётся) > полная > выданная
            for _lk in ("black_short_link", "black_activation_link", "issued_link"):
                _v = fields.get(_lk)
                if isinstance(_v, str) and _v.strip():
                    _new_link = _v.strip()
                    break
            if _new_link:
                _hist = data.get("link_history")
                if not isinstance(_hist, list):
                    _hist = []
                if not _hist:
                    # Профиль существовал до появления истории — фиксируем его
                    # текущую ссылку как первую запись (задним числом)
                    _old = (data.get("black_short_link") or data.get("black_activation_link")
                            or data.get("issued_link") or "")
                    if _old and _old != _new_link:
                        _hist.append({"ts": data.get("link_received_ts")
                                            or data.get("issued_ts") or 0,
                                      "link": _old})
                if not _hist or _hist[-1].get("link") != _new_link:
                    import time as _t_lh
                    _hist.append({"ts": _t_lh.time(), "link": _new_link})
                data["link_history"] = _hist
        except Exception:
            pass
        data.update(fields)
        _atomic_write_text(meta_file, json.dumps(data, ensure_ascii=False, indent=2))
        _invalidate_done_profiles_cache()
        return True
    except Exception as exc:
        print(f"\n  {R}Ошибка записи метаданных: {exc}{RST}")
        return False


def _kill_chrome_for_profiles(profile_paths) -> int:
    """Один проход по процессам — убивает Chrome для любого из профилей."""
    import os
    import subprocess
    paths = [Path(p) for p in (profile_paths or []) if p]
    if not paths:
        return 0
    needles: list[str] = []
    for p in paths:
        path_str = str(p).replace("/", "\\")
        needles.append(path_str)
        needles.append(os.path.basename(path_str))
    needles = list(dict.fromkeys(n for n in needles if n))
    killed = 0
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "chrome" not in (proc.info.get("name") or "").lower():
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if any(n in cmdline for n in needles):
                    proc.kill()
                    killed += 1
            except Exception:
                pass
        return killed
    except ImportError:
        # psutil недоступен — PowerShell по имени папки (по одной)
        for p in paths:
            folder_name = os.path.basename(str(p).replace("/", "\\"))
            try:
                ps_cmd = (
                    f"Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" | "
                    f"Where-Object {{$_.CommandLine -like '*{folder_name}*'}} | "
                    f"ForEach-Object {{Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}}"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                    capture_output=True, timeout=8,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass
        return -1


def _kill_chrome_for_profile(profile_path) -> int:
    """Завершает Chrome-процессы, использующие указанную папку профиля."""
    return _kill_chrome_for_profiles([profile_path])


def _find_chrome_pids_for_profile(profile_path: Path) -> list:
    """Возвращает PID-ы Chrome-процессов, запущенных с данным user-data-dir."""
    import subprocess as _sp
    import winproc
    profile_str = str(profile_path.resolve()).lower()
    pids: list = []
    try:
        r = winproc.run(
            ["wmic", "process", "where", "name='chrome.exe'",
             "get", "processid,commandline"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        for line in r.stdout.splitlines():
            if profile_str in line.lower():
                parts = line.strip().rsplit(None, 1)
                if len(parts) == 2 and parts[1].isdigit():
                    pids.append(int(parts[1]))
    except Exception:
        pass
    return pids


def _clear_stale_profile_locks(profile_path: Path) -> None:
    """Убивает зависший Chrome с этим профилем и удаляет стейл-локи."""
    _kill_chrome_for_profile(profile_path)
    import time as _t; _t.sleep(0.3)
    for name in ("SingletonLock", "lockfile", "SingletonSocket"):
        try:
            (profile_path / name).unlink(missing_ok=True)
        except (PermissionError, OSError):
            pass


def _is_profile_locked(profile_path: Path) -> bool:
    """Проверяет, открыт ли профиль в другом Chrome."""
    for name in ("SingletonLock", "lockfile", "SingletonSocket"):
        if (profile_path / name).exists():
            return True
    return bool(_find_chrome_pids_for_profile(profile_path))


def _archive_profile(profile_path: Path, **extra_fields) -> bool:
    """Сохраняет JSON-запись в архив и удаляет папку профиля.

    В chrome_profiles_used создаётся лёгкий файл record_<phone>_<ts>.json
    с полным метаданными (даты создания, выдачи, использования, подписки).
    Папка профиля (Chrome data) удаляется — она больше не нужна.
    """
    USED_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    meta_file = profile_path / ".profile_meta.json"
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    except Exception:
        data = {}
    data.update(extra_fields)
    data.setdefault("profile_name", profile_path.name)

    # Имя файла записи
    phone = data.get("username", profile_path.name)
    ts_int = int(data.get("used_ts") or time.time())
    record_name = f"record_{phone}_{ts_int}.json"
    record_path = USED_PROFILES_DIR / record_name
    try:
        _atomic_write_text(record_path, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"\n  {R}Ошибка записи архивной записи: {exc}{RST}")
        return False
    # Удаляем папку профиля (onerror снимает блокировку Windows на .pma и др.)
    import os as _os2, stat as _stat2
    def _rm_err(func, path, exc_info):
        try:
            _os2.chmod(path, _stat2.S_IWRITE)
            func(path)
        except Exception:
            pass

    shutil.rmtree(str(profile_path), onerror=_rm_err)
    if Path(str(profile_path)).exists():
        # Папка ещё существует — файлы заняты Chrome. Убиваем процесс и повторяем.
        print(f"  {Y}Файлы заняты — завершаю Chrome-процессы профиля...{RST}")
        _kill_chrome_for_profile(profile_path)
        time.sleep(2)
        shutil.rmtree(str(profile_path), onerror=_rm_err)
    if Path(str(profile_path)).exists():
        print(f"\n  {R}Папка не удалена даже после завершения Chrome.{RST}")
        return False
    _invalidate_done_profiles_cache()
    return True


async def _check_black_store_activation(profile_path: Path, username: str = "",
                                        headless: bool = False) -> dict:
    """
    Открывает flipkart-black-store, прокручивает вниз, определяет статус активации.
    Возвращает dict:
      status: "activated" | "activate_now" | "explore_now" | "not_logged_in" | "unknown"
      valid_till: str | None  (например "14 Sep 2026")
      error: str | None
    """
    from playwright.async_api import async_playwright
    result = {"status": "unknown", "valid_till": None, "error": None}
    pw = None
    ctx = None
    try:
        use_vpn, proxy, net_err = await _resolve_profile_scenario_network(profile_path)
        if net_err:
            result["status"] = "vpn_failed"
            result["error"] = net_err
            return result
        set_profile_op_stage(profile_path or username, "Активация · браузер")
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile_path)
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            **_browser_launch_kw(
                headless=headless, phone=username,
                profile_path=profile_path, use_vpn=use_vpn, proxy=proxy,
            ))
        _mark_browser_network(ctx, use_vpn=use_vpn, proxy=proxy)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        if not headless:
            await _maximize_window(ctx, page)
        _register_purchase_profile(profile_path)
        if use_vpn and not await _vpn_connect_on_use(ctx, profile_path):
            result["status"] = "vpn_failed"
            result["error"] = "VPN не подключился"
            return result

        store_url = "https://www.flipkart.com/flipkart-black-store"
        set_profile_op_stage(profile_path or username, "Активация · Black Store")
        if use_vpn or proxy:
            ok_nav, page, nav_err = await _navigate_flipkart_resilient(
                ctx, page, store_url, label=username, profile_path=profile_path,
            )
            if not ok_nav:
                denied = (
                    "access denied" in (nav_err or "").lower()
                    or await _flipkart_page_access_denied(page)
                )
                result["status"] = "access_denied" if denied else "nav_failed"
                result["error"] = (
                    "Access Denied — смените страну VPN (USA) или прокси"
                    if denied else (nav_err or "Не удалось открыть Flipkart")
                )
                return result
        else:
            try:
                await page.goto(store_url, wait_until="domcontentloaded", timeout=12_000)
            except Exception:
                pass
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)  # ждём React-рендеринг

        if "login" in page.url.lower():
            result["status"] = "not_logged_in"
            result["error"] = "Не выполнен вход"
            return result

        # Проверяем залогинен ли.
        # Flipkart при логине показывает "Account" в навбаре (не "Hello").
        # НЕ ищем "login" по всем кнопкам — footer и другие секции могут его содержать.
        try:
            _not_logged = await page.evaluate("""() => {
                const fullText = (document.body?.innerText || '').toLowerCase();
                // Позитивный сигнал залогиненности (достаточно одного)
                if (/\\baccount\\b|hello,|logout|my profile/.test(fullText)) return false;
                // Если страница Black-store загрузилась и есть бенефит — тоже залогинен
                if (document.querySelector('a[href*="black-youtube-premium-benefit"]')) return false;
                // Негативный: "login" в первых 10 строках тела
                const lines = fullText.substring(0, 600).split('\\n')
                    .map(s => s.trim()).filter(Boolean);
                for (const l of lines.slice(0, 10)) {
                    if (l === 'login') return true;
                }
                return false;
            }""")
            if _not_logged:
                result["status"] = "not_logged_in"
                result["error"] = "Сессия истекла — требуется повторный вход"
                return result
        except Exception:
            pass

        # Проверка блокировки по IP (Akamai Access Denied — сменить страну VPN, USA)
        try:
            _page_title = await page.title()
            _page_text  = await page.evaluate("() => document.body?.innerText || ''")
            if "access denied" in _page_text.lower() or "access denied" in _page_title.lower():
                result["status"] = "access_denied"
                result["error"]  = "Access Denied — смените страну VPN (USA)"
                return result
        except Exception:
            pass

        # Кликаем по центру страницы чтобы передать фокус
        try:
            vp = page.viewport_size or {"width": 1280, "height": 720}
            await page.mouse.click(vp["width"] // 2, vp["height"] // 2)
            await page.wait_for_timeout(300)
        except Exception:
            pass

        _VALID_TILL_JS = """() => {
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || el.textContent || '').trim();
                if (!/membership valid till/i.test(t) || t.length > 200) continue;
                let m = t.match(/\\d{1,2}[\\s\\-/]+[A-Za-z]+[\\s\\-/]+\\d{2,4}/);
                if (m) return m[0];
                m = t.match(/[A-Za-z]+[\\s]+\\d{1,2}[,\\s]+\\d{2,4}/);
                if (m) return m[0];
                m = t.match(/\\d{1,2}[\\s\\-/]+\\d{1,2}[\\s\\-/]+\\d{2,4}/);
                if (m) return m[0];
            }
            return null;
        }"""

        # Сначала ищем "Membership valid till" — видна сразу вверху
        for _vt_try in range(3):
            try:
                result["valid_till"] = await page.evaluate(_VALID_TILL_JS)
            except Exception:
                pass
            if result["valid_till"]:
                break
            await page.wait_for_timeout(500)

        _COMPREHENSIVE_JS = """() => {
            const chk = (s) => {
                if (!s) return null;
                const t = (s + '').toLowerCase();
                // Приоритет: сначала явные кнопки (самые конкретные)
                if (t.includes('activate now')) return 'activate_now';
                if (t.includes('explore now'))  return 'explore_now';
                // Только однозначные признаки активации YouTube Premium
                if (t.includes('activated') && !t.includes('not activated')) return 'activated';
                if (t.includes('membership is active'))   return 'activated';
                if (t.includes('subscription is active')) return 'activated';
                // 'membership valid till' = дата Black подписки, НЕ признак активации YT Premium
                return null;
            };

            let combined = '';

            // A) innerText — видимый текст страницы
            combined += ' ' + (document.body.innerText || '');

            // B) Все text-ноды (в т.ч. скрытые через visibility)
            const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            let nd; while (nd = tw.nextNode()) { combined += ' ' + (nd.textContent || ''); }

            // C) CSS pseudo-elements видимых элементов (±300px от viewport)
            const vh = window.innerHeight;
            for (const el of document.querySelectorAll('*')) {
                try {
                    const rc = el.getBoundingClientRect();
                    if (rc.bottom < -300 || rc.top > vh + 300) continue;
                    for (const ps of ['::before','::after']) {
                        const c = window.getComputedStyle(el, ps).content;
                        if (c && c !== 'none') combined += ' ' + c;
                    }
                } catch(e) {}
            }

            // D) data-status / data-state атрибуты
            for (const el of document.querySelectorAll('[data-status],[data-state]')) {
                combined += ' ' + (el.getAttribute('data-status') || '');
                combined += ' ' + (el.getAttribute('data-state')  || '');
            }

            // E) aria-label точное совпадение (добавляем в конец)
            for (const el of document.querySelectorAll('[aria-label]')) {
                const al = (el.getAttribute('aria-label') || '').trim();
                if (['Activated','Activate Now','Explore Now'].includes(al))
                    combined += ' ' + al;
            }

            // F) Promos-картинка 1200x213 = кнопка Activate Now (PNG, текст не в DOM)
            //    Проверяем ДО chk() чтобы перебить ложный "activated" от "membership valid till"
            for (const img of document.querySelectorAll('img[width="1200"]')) {
                if (img.getAttribute('height') === '213' && (img.src||'').includes('/promos/'))
                    return 'activate_now';
            }

            return chk(combined);
        }"""

        status = None

        async def _check_frames(verbose: bool = False):
            """Собирает текст ВСЕХ фреймов, потом расставляет приоритеты.
            activate_now побеждает даже если в другом фрейме есть activated."""
            _texts = []
            _frame_list = [page] + list(page.frames)
            _frame_info = []
            for _fi, _frame in enumerate(_frame_list):
                try:
                    _ft = (await _frame.evaluate(
                        "() => (document.body?.textContent||'').toLowerCase()"))
                    if _ft:
                        _tags = []
                        if "activate now" in _ft:
                            _tags.append("ACTIVATE_NOW")
                        if "explore now" in _ft:
                            _tags.append("EXPLORE_NOW")
                        if "activated" in _ft and "not activated" not in _ft:
                            _tags.append("ACTIVATED")
                        if "membership valid till" in _ft:
                            _tags.append("VALID_TILL")
                        if _tags:
                            _u = (_frame.url or "")[:50]
                            _frame_info.append(f"Frame{_fi}[{_u}]: {','.join(_tags)}")
                        _texts.append(_ft)
                except Exception:
                    pass
            if verbose:
                _info_str = " | ".join(_frame_info) if _frame_info else "нет ключевых слов"
                print(f"  {DIM}Фреймов: {len(_frame_list)} | {_info_str}{RST}")
            # Приоритет: activate_now > explore_now > activated
            if any("activate now" in t for t in _texts):
                return "activate_now"
            if any("explore now" in t for t in _texts):
                return "explore_now"
            if any("activated" in t and "not activated" not in t for t in _texts):
                return "activated"
            return None

        # Сначала проверяем ВСЕ фреймы (activate_now может быть в iframe)
        # потом COMPREHENSIVE_JS как запасной
        try:
            await page.evaluate("() => { if (document.body) window.scrollTo(0, document.body.scrollHeight); }")
            await page.wait_for_timeout(1_000)
            status = await _check_frames(verbose=True)
            if not status:
                status = await page.evaluate(_COMPREHENSIVE_JS)
        except Exception:
            pass

        # Возвращаемся наверх
        try:
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(400)
        except Exception:
            pass

        # Медленный скролл если статус не найден: 20 шагов × 200px × 0.8с = ~16 сек макс
        if not status:
            for _scroll_i in range(20):
                # Фреймы — первый приоритет (activate_now может быть в iframe)
                _fs = await _check_frames()
                if _fs:
                    status = _fs
                    print(f"  Найдено в frame (шаг {_scroll_i}): {_fs}")
                    break

                try:
                    _s = await page.evaluate(_COMPREHENSIVE_JS)
                    if _s:
                        status = _s
                        print(f"  Найдено (шаг {_scroll_i}, ~{_scroll_i * 200}px): {_s}")
                        break
                except Exception:
                    pass

                if not result["valid_till"]:
                    try:
                        result["valid_till"] = await page.evaluate(_VALID_TILL_JS)
                    except Exception:
                        pass

                try:
                    await page.evaluate(f"window.scrollTo(0, {(_scroll_i + 1) * 200})")
                except Exception:
                    pass
                await page.wait_for_timeout(800)

        # Если "Explore Now" — обновляем страницу 30 секунд в ожидании "Activate Now"
        if status == "explore_now":
            print(f"  «Explore Now» — обновляю страницу (до 30 сек)...")
            _loop = asyncio.get_running_loop()
            deadline = _loop.time() + 30
            while _loop.time() < deadline:
                await page.reload(wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                # Скролл и проверка
                new_status = None
                for _ in range(20):
                    try:
                        new_status = await page.evaluate(_COMPREHENSIVE_JS)
                    except Exception:
                        pass
                    if new_status and new_status != "explore_now":
                        break
                    await page.evaluate("window.scrollBy(0, 500)")
                    await page.wait_for_timeout(300)
                if new_status and new_status != "explore_now":
                    status = new_status
                    break
                await asyncio.sleep(5)
            # Если по-прежнему explore_now — значит не оплачен
            if not status or status == "explore_now":
                status = "explore_now"

        # Если статус всё ещё не определён — проверяем наличие benefit-секции в HTML
        # Ссылка /black-youtube-premium-benefit-faq-store = страница загрузилась корректно
        # → без "membership valid till" = explore_now (не оплачен)
        if not status:
            try:
                _html_check = await page.evaluate(
                    "() => document.documentElement.innerHTML")
                if "black-youtube-premium-benefit-faq-store" in _html_check:
                    if result.get("valid_till") or "membership valid till" in _html_check.lower():
                        status = "activated"
                        print(f"  {C}💳 Benefit-секция + Valid Till → activated{RST}")
                    else:
                        status = "explore_now"
                        print(f"  {C}💳 Benefit-секция найдена → explore_now{RST}")
                elif result.get("valid_till") or "membership valid till" in _html_check.lower():
                    # Резервный чек: если есть валидная дата, но benefit-faq-store не найден в html
                    if "explore now" in _html_check.lower():
                        status = "explore_now"
                        print(f"  {C}💳 Резерв: Найдено Valid Till + explore now → explore_now{RST}")
                    elif "activate now" in _html_check.lower():
                        status = "activate_now"
                        print(f"  {C}💳 Резерв: Найдено Valid Till + activate now → activate_now{RST}")
                    else:
                        status = "activated"
                        print(f"  {C}💳 Резерв: Найдено Valid Till → activated{RST}")
            except Exception:
                pass

        result["status"] = status or "unknown"

        # Если Activated — пробуем ещё раз найти valid_till
        if result["status"] == "activated" and not result["valid_till"]:
            try:
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(500)
                result["valid_till"] = await page.evaluate(_VALID_TILL_JS)
            except Exception:
                pass

        # При activate_now — JS-клик по PNG-кнопке (img 1200x213) → ссылка активации
        if result["status"] == "activate_now":
            _CLICK_JS = """() => {
                for (const img of document.querySelectorAll('img[width="1200"]')) {
                    if (img.getAttribute('height') !== '213') continue;
                    if (!(img.src||'').includes('/promos/')) continue;
                    let el = img.parentElement;
                    for (let i = 0; i < 6 && el; i++) {
                        if (el.style && el.style.cursor === 'pointer') {
                            el.scrollIntoView({behavior:'instant', block:'center'});
                            el.click(); return 'img-1200x213';
                        }
                        el = el.parentElement;
                    }
                    img.scrollIntoView({behavior:'instant', block:'center'});
                    img.click(); return 'img-direct';
                }
                return null;
            }"""
            try:
                _new_pg = asyncio.ensure_future(ctx.wait_for_event("page", timeout=10_000))
                _method = await page.evaluate(_CLICK_JS)
                if _method:
                    print(f"  {G}✅ Кнопка «Activate now» нажата{RST}")
                    try:
                        _tab = await _new_pg
                        await _tab.wait_for_load_state("domcontentloaded", timeout=12_000)
                        result["activation_url"] = _tab.url
                    except Exception:
                        _new_pg.cancel()
                        await page.wait_for_timeout(3_000)
                        if "flipkart-black-store" not in page.url:
                            result["activation_url"] = page.url
                    if result.get("activation_url"):
                        print(f"  {G}🔗 Ссылка: {result['activation_url'][:80]}...{RST}")
                    else:
                        print(f"  {Y}⚠ Ссылка не получена после клика{RST}")
                else:
                    _new_pg.cancel()
                    print(f"  {Y}⚠ Кнопка «Activate now» не найдена на странице{RST}")
            except Exception as _je:
                print(f"  {Y}⚠ Ошибка клика: {_je}{RST}")

            # Сокращаем ссылку через clck.ru
            if result.get("activation_url"):
                _short = result["activation_url"]
                _clck_page = await ctx.new_page()
                try:
                    await _clck_page.goto("https://clck.ru/", wait_until="domcontentloaded", timeout=15_000)
                    await _clck_page.wait_for_timeout(2_000)
                    _inp = _clck_page.locator("input[name='url'], input[type='url'], input[type='text']").first
                    if await _inp.count() > 0:
                        await _inp.fill(result["activation_url"])
                        await _clck_page.wait_for_timeout(400)
                        _sbt = await _clck_page.evaluate("""() => {
                            for (const el of document.querySelectorAll('button, input[type="submit"], a')) {
                                const t = (el.innerText || el.value || el.textContent || '').trim();
                                if (/сократ/i.test(t) || /shorten/i.test(t) || /submit/i.test(t.toLowerCase())) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width >= 20 && r.height >= 8)
                                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                                }
                            }
                            return null;
                        }""")
                        if _sbt:
                            await _clck_page.mouse.click(_sbt["x"], _sbt["y"])
                        else:
                            await _inp.press("Enter")
                        await _clck_page.wait_for_timeout(4_000)
                    _SHORT_JS = """() => {
                        for (const el of document.querySelectorAll('input, a, .result, .short-url, [class*="result"], span, div')) {
                            const v = (el.value || el.href || el.innerText || el.textContent || '').trim();
                            const m = v.match(/(?:https?:\\/\\/)?clck\\.ru\\/[A-Za-z0-9_-]+/);
                            if (m && m[0].length > 12) return m[0];
                        }
                        return '';
                    }"""
                    _short_raw = (await _clck_page.evaluate(_SHORT_JS) or "").strip()
                    if _short_raw:
                        _short = _short_raw if _short_raw.startswith("http") else "https://" + _short_raw
                    elif "clck.ru/" in _clck_page.url and _clck_page.url != "https://clck.ru/":
                        _short = _clck_page.url
                    result["short_link"] = _short
                    print(f"  {G}Короткая ссылка: {_short}{RST}")
                except Exception as _ce:
                    print(f"  clck.ru ошибка: {_ce}")
                    result["short_link"] = result["activation_url"]
                finally:
                    try:
                        await _clck_page.close()
                    except Exception:
                        pass

        # При unknown — скриншот + дамп текста для диагностики
        if result["status"] == "unknown":
            try:
                await page.screenshot(path="debug/debug_activation.png", full_page=True)
                print(f"  Скриншот: debug_activation.png")
            except Exception:
                pass
            try:
                _pg_txt = await page.evaluate(
                    "() => (document.body?.innerText || '').slice(0, 800)")
                print(f"  Текст страницы:\n{_pg_txt}")
            except Exception:
                pass

        return result
    except Exception as e:
        result["error"] = str(e)
        return result
    finally:
        set_profile_op_stage(profile_path or username, "")
        await _close_browser_session(ctx, pw, profile_path, disconnect_vpn=True)


def screen_restore_from_cookies():
    """Восстановить профиль Flipkart из JSON-файла куков (из TG или cookies_backup/)."""
    cls()
    header("ВОССТАНОВЛЕНИЕ ПРОФИЛЯ ИЗ КУКОВ", C)

    bk_dir = Path("cookies_backup")
    candidates: list[Path] = []
    if bk_dir.exists():
        candidates = sorted(bk_dir.glob("*.json"))
    # Плюс JSON прямо в корне проекта
    for f in sorted(Path(".").glob("cookies*.json")):
        if f not in candidates:
            candidates.append(f)

    if not candidates:
        print(f"\n  {Y}Нет JSON-файлов куков.{RST}")
        print(f"  {DIM}Скачайте файл из TG-бота и положите в папку cookies_backup/{RST}")
        print(f"  {DIM}Имя файла: cookies_НОМЕР.json или cookies_profile....json{RST}")
        pause()
        return

    print(f"\n  Найдено файлов куков: {W}{BLD}{len(candidates)}{RST}\n")
    for i, f in enumerate(candidates, 1):
        print(f"  [{i}] {f}")

    print()
    raw = input(f"  {BLD}Выберите файл [1-{len(candidates)}]: {RST}").strip()
    try:
        chosen = candidates[int(raw) - 1]
    except Exception:
        print(f"  {R}Неверный выбор.{RST}")
        pause()
        return

    phone = input(f"  {BLD}Номер телефона профиля (без +91, только цифры): {RST}").strip()
    phone = "".join(filter(str.isdigit, phone))
    if not phone:
        print(f"  {R}Номер не введён.{RST}")
        pause()
        return

    print(f"\n  {DIM}Восстанавливаю сессию из {chosen.name}...{RST}\n")
    ok, msg = asyncio.run(_restore_profile_from_cookies(chosen, phone))
    if ok:
        print(f"\n  {G}✅ Профиль восстановлен: {msg}{RST}")
        print(f"  {DIM}Откройте его через пункт 3 → выберите профиль.{RST}")
    else:
        print(f"\n  {R}❌ Не удалось восстановить: {msg}{RST}")
        print(f"  {DIM}Возможно куки устарели. Попробуйте свежие из TG.{RST}")
    pause()


def screen_check_all_activated():
    """Проверить активацию Black для всех профилей со статусом «Выдан»."""
    cls()
    header("ПРОВЕРКА АКТИВАЦИИ — ВСЕ ПРОФИЛИ", G)
    print(f"  {DIM}Проверка доступности Flipkart...{RST}")
    if not _is_flipkart_accessible_sync():
        print(f"\n  {R}⚠ Flipkart недоступен{RST}")
        print(f"  {Y}Повторите попытку позже.{RST}")
        pause()
        return
    print(f"  {G}Flipkart доступен.{RST}")

    all_profiles = _load_done_profiles()
    profiles = [p for p in all_profiles if p.get("issued_ts")]
    if not profiles:
        print(f"\n  {Y}Нет профилей со статусом «Выдан».{RST}")
        if all_profiles:
            print(f"  {DIM}(Всего профилей: {len(all_profiles)}, но ни один не помечен «Выдан»){RST}")
        pause()
        return

    print(f"\n  Выданных профилей: {W}{BLD}{len(profiles)}{RST}\n")

    deleted_list: list[str] = []
    not_activated: list[str] = []
    unknown_list: list[str] = []

    for i, p in enumerate(profiles, 1):
        username = p.get("username", p["path"].name)
        print(f"  [{i}/{len(profiles)}] {_disp_phone(username)}")
        print(f"  {DIM}  Открываю black-store, прокручиваю...{RST}")

        try:
            chk = asyncio.run(
                _check_black_store_activation(p["path"], username=username, headless=True))
        except KeyboardInterrupt:
            print(f"\n  {Y}Прервано пользователем.{RST}")
            break
        except Exception as e:
            print(f"  {R}  Ошибка: {e}{RST}\n")
            unknown_list.append(username)
            continue

        status    = chk.get("status", "unknown")
        valid_till = chk.get("valid_till") or ""
        err       = chk.get("error")

        if err:
            print(f"  {R}  Ошибка: {err}{RST}\n")
            unknown_list.append(username)

        elif status == "activated":
            print(f"  {M}✨ Activated{' до ' + valid_till if valid_till else ''} — аккаунт активирован{RST}")
            try:
                _confirm_arch = input(f"  Перенести в архив? [Д/Н]: ").strip().lower()
            except KeyboardInterrupt:
                _confirm_arch = "н"
            if _confirm_arch == "д":
                _arch_ok = _archive_profile(
                    p["path"],
                    used_ts=time.time(),
                    activation_status="activated",
                    valid_till=valid_till,
                )
                print(f"  {G}✔ Архивирован{RST}\n" if _arch_ok else f"  {Y}⚠ Не удалось архивировать{RST}\n")
            else:
                print(f"  {DIM}Пропущено{RST}\n")
            not_activated.append(username)

        elif status == "activate_now":
            _aurl = chk.get("activation_url", "")
            _slink = chk.get("short_link", "")
            _vt3 = chk.get("valid_till", "")
            print(f"  {G}⭐ Activate Now — доступен к выдаче{RST}")
            if _aurl:
                if _slink and _slink != _aurl:
                    print(f"  {G}   🔗 Короткая: {_slink}{RST}")
                print(f"  {B}   Ссылка: {_aurl}{RST}")
                _send_tg_activation(username, _aurl, _slink, _vt3,
                                    login_str=p.get("login_str", ""),
                                    issued_str=p.get("issued_str", ""))
            else:
                print(f"  {Y}   ⏳ Ссылка не получена{RST}")
            print()
            not_activated.append(username)

        elif status == "explore_now":
            print(f"  {C}💳 Explore Now — доступен для оплаты{RST}\n")
            unknown_list.append(username)

        elif status == "not_logged_in":
            print(f"  {R}❌ Не залогинен в профиле{RST}\n")
            unknown_list.append(username)

        else:
            extra = f" (valid_till={valid_till})" if valid_till else ""
            print(f"  {DIM}  Статус неизвестен{extra}{RST}\n")
            unknown_list.append(username)

    print(f"\n  {'─' * 48}")
    print(f"  ⭐ Activated / Activate Now : {G}{BLD}{len(not_activated)}{RST}"
          + (f"  {DIM}{', '.join(not_activated[:5])}{RST}" if not_activated else ""))
    print(f"  ❓ Ошибки / неизвестно      : {R}{BLD}{len(unknown_list)}{RST}")
    pause()


def screen_profiles():
    """Профили с успешным входом: открыть / пометить «Выдан» / пометить «Использован»."""
    while True:
        cls()
        header("ПРОФИЛИ  —  УПРАВЛЕНИЕ", C)

        profiles = _load_done_profiles()

        if not profiles:
            print(f"  {DIM}Нет сохранённых профилей с успешным входом.{RST}")
            print(f"  {DIM}Запустите автоматизацию (пункт 1).{RST}")
            pause()
            return

        section(f"Профили с успешным входом  [{len(profiles)} шт.]  (новые сначала)")
        print()
        for i, p in enumerate(profiles, 1):
            no_meta = p.get("login_ts") is None
            _vt_disp = p.get("black_valid_till") or p.get("subscription_expires_str") or ""
            _slink_disp = p.get("black_short_link") or ""
            _st = p.get("status") or ""
            if p.get("issued_ts"):
                _ln = (f"{DIM}{p['login_str']}{RST}"
                       f"  {DIM}|{RST}  {B}выдан: {p['issued_str']}{RST}"
                       + (f"  {DIM}|{RST}  {M}до: {_vt_disp}{RST}" if _vt_disp else ""))
                status_pre = f"  {B}🔵{RST}"
                status_lbl = f"{B}Выданные{RST}"
            elif no_meta:
                _ln = f"{R}⚠ Нет данных{RST}"
                status_pre = f"  {R}⚠{RST}"
                status_lbl = f"{R}Нет данных{RST}"
            elif p.get("black_valid_till") or p.get("paid_ready") or _st in ("activated", "explore_now", "activate_now"):
                _ln = (f"{DIM}{p['login_str']}{RST}"
                       + (f"  {DIM}|{RST}  {G}до: {_vt_disp}{RST}" if _vt_disp else ""))
                status_pre = f"  {M}🟣{RST}"
                status_lbl = f"{M}Оплаченные{RST}"
            elif p.get("prepared_ts") or p.get("buyer_email") or _st == "email_completed":
                _ln = f"{DIM}{p['login_str']}{RST}"
                status_pre = f"  \033[38;5;208m🟠{RST}"
                status_lbl = f"\033[38;5;208mС данными{RST}"
            else:
                _ln = f"{DIM}{p['login_str']}{RST}"
                status_pre = f"  {G}🟢{RST}"
                status_lbl = f"{G}Доступные{RST}"
            login_col = R if no_meta else DIM
            print(
                f"  {BLD}{Y}[{i:>2}]{RST}{status_pre}  "
                f"{W}{_disp_phone(p['username']):<14}{RST}  "
                f"{_ln}  {DIM}│{RST}  {status_lbl}"
            )
        # Кнопка удаления профилей без данных (если есть)
        no_data_count = sum(1 for p in profiles if p.get("login_ts") is None)
        noaddr_profiles = [p for p in profiles
                           if not p.get("issued_ts")
                           and not p.get("black_valid_till") and not p.get("paid_ready")
                           and p.get("status") not in ("activated", "explore_now", "activate_now")
                           and not p.get("prepared_ts") and not p.get("buyer_email")
                           and p.get("status") != "email_completed"
                           and p.get("login_ts")]
        if noaddr_profiles:
            opt("А", f"Заполнить все доступные [{len(noaddr_profiles)} шт.]  (адрес → чекаут → до оплаты)", G)
        if no_data_count:
            opt("9", f"Удалить все {no_data_count} профиля без данных (нет мета-файла)", R)
        opt("Л", "Проверить вход у ВСЕХ профилей  (восстановить / удалить / архив)", C)
        print()
        opt("0", "Назад", R)
        print()

        choice = input(f"  {BLD}Выберите профиль [1-{len(profiles)}], А, Л, 9 или 0: {RST}").strip().upper()
        if choice == "0" or choice == "":
            return

        if choice in ("Л", "L"):
            cls()
            header("ПРОВЕРКА ВХОДА — ВСЕ ПРОФИЛИ", C)
            print(f"  {DIM}Проверяю {len(profiles)} профил(ей). Залогиненные пропускаю,{RST}")
            print(f"  {DIM}по слетевшим предложу восстановить / удалить / архив.{RST}")
            for _bi, _bp in enumerate(profiles, 1):
                print(f"\n  {BLD}[{_bi}/{len(profiles)}]{RST} +91 {_disp_phone(_bp['username'])}", end="")
                _profile_login_check_flow(_bp, quiet_ok=True)
            print(f"\n  {G}✅ Проверка всех профилей завершена.{RST}")
            pause()
            continue

        if choice == "А" and noaddr_profiles:
            print(f"\n  {G}⚡ Заполняю все доступные профили ({len(noaddr_profiles)} шт.)...{RST}")
            print(f"  {DIM}Адрес → чекаут → страница оплаты → закрыть{RST}\n")
            _fa_ok = _fa_err = 0
            _fa_oos_list = []  # профили с OOS — спросим удаление в конце
            for _fa_p in noaddr_profiles:
                _fa_addr = _gen_indian_address()
                print(f"  {DIM}▶ {_fa_p['username']} ...{RST}", end="", flush=True)
                try:
                    _fa_ok_r, _fa_msg = asyncio.run(
                        _do_fill_address(_fa_p["path"], _fa_addr, stop_at_payment=True))
                    if _fa_ok_r:
                        _fa_ok += 1
                        print(f"  {G}✔ готов{RST}")
                    elif _fa_msg in ("OUT_OF_STOCK", "OUT_OF_STOCK_2"):
                        _ag = " (2 адреса)" if _fa_msg == "OUT_OF_STOCK_2" else ""
                        _fa_oos_list.append(_fa_p)
                        print(f"  {R}✘ OOS{_ag} — профиль НЕ удалён{RST}")
                    else:
                        _fa_err += 1
                        print(f"  {Y}⚠ ошибка: {_fa_msg[:60]}{RST}")
                except Exception as _fa_exc:
                    _fa_err += 1
                    print(f"  {R}✘ {_fa_exc}{RST}")
            print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━")
            print(f"  {G}✅ Готово: {_fa_ok}{RST}")
            if _fa_err:
                print(f"  {Y}❌ Ошибки: {_fa_err}{RST}")
            # Подтверждение удаления профилей с OOS
            if _fa_oos_list:
                print(f"\n  {R}🚫 Out of stock: {len(_fa_oos_list)} профил(ей){RST}")
                for _op in _fa_oos_list:
                    print(f"     • {_disp_phone(_op['username'])}")
                _ans = input(f"\n  {BLD}Удалить эти {len(_fa_oos_list)} профил(ей) с OOS? [Д/Н]: {RST}").strip().upper()
                if _ans in ("Д", "ДА", "Y", "YES"):
                    import shutil as _shoo
                    for _op in _fa_oos_list:
                        _shoo.rmtree(str(_op["path"]), ignore_errors=True)
                    print(f"  {Y}🗑 Удалено: {len(_fa_oos_list)}{RST}")
                else:
                    print(f"  {G}Профили оставлены.{RST}")
            pause()
            continue

        if choice == "9" and no_data_count:
            import shutil as _sh, os as _os, stat as _stat
            def _rm_err(func, path, exc_info):
                try:
                    _os.chmod(path, _stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            deleted = 0
            locked = 0
            for p in profiles:
                if p.get("login_ts") is None:
                    _sh.rmtree(p["path"], onerror=_rm_err)
                    if p["path"].exists():
                        print(f"  {Y}Файлы заняты — завершаю Chrome для {p['path'].name}...{RST}")
                        _kill_chrome_for_profile(p["path"])
                        time.sleep(2)
                        _sh.rmtree(p["path"], onerror=_rm_err)
                    if not p["path"].exists():
                        print(f"  {G}Удалён: {p['path'].name}{RST}")
                        deleted += 1
                    else:
                        print(f"  {R}Не удалён: {p['path'].name}{RST}")
                        locked += 1
            if locked:
                print(f"\n  {Y}{locked} папок не удалось удалить{RST}")
            print(f"\n  {G}Удалено профилей без данных: {deleted}{RST}")
            time.sleep(2)
            continue

        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(profiles)):
                raise ValueError
        except ValueError:
            print(f"\n  {R}Неверный номер.{RST}")
            time.sleep(3)
            continue

        selected = profiles[idx]

        # ── Подменю действий ────────────────────────────────────────────────────
        while True:
            cls()
            header("ДЕЙСТВИЕ С ПРОФИЛЕМ", C)
            _sel_vt = selected.get("black_valid_till") or selected.get("subscription_expires_str") or ""
            _sel_slink = selected.get("black_short_link") or ""
            print(f"  Профиль  : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
            _info_line = f"{G}{selected['login_str']}{RST}"
            if selected.get("issued_str"):
                _info_line += f"  {DIM}|{RST}  {B}выдан: {selected['issued_str']}{RST}"
            if _sel_vt:
                _info_line += f"  {DIM}|{RST}  {M}до: {_sel_vt}{RST}"
            print(f"  Даты     : {_info_line}")
            if _sel_slink:
                print(f"  Ссылка   : {C}{_sel_slink}{RST}")
            _sel_note = selected.get("note") or ""
            if _sel_note:
                print(f"  Примечание: {Y}{_sel_note}{RST}")
            # Использованные при оплате подарочные карты
            try:
                _gcu = _read_profile_meta(selected["path"]).get("gift_cards_used")
            except Exception:
                _gcu = None
            if isinstance(_gcu, list) and _gcu:
                _sum_g = sum(int(g.get("denom") or 0) for g in _gcu)
                print(f"  Гифт-карты: {C}{len(_gcu)} шт. на ₹{_sum_g}{RST}")
                for g in _gcu:
                    _gw = g.get("used_str") or (_fmt_msk(g["used_ts"]) if g.get("used_ts") else "—")
                    _gnum = g.get("number") or g.get("number_mask", "")
                    _gpin = f" PIN {g.get('pin')}" if g.get("pin") else ""
                    print(f"             {DIM}₹{int(g.get('denom') or 0):<5} "
                          f"серия {_gnum}{_gpin}  ·  {_gw}{RST}")
            print()

            opt("1",     "Открыть в Chrome  →  flipkart-black-store", C)
            opt("П / 7", "Проверить активацию  (black-store → Activated?)", G)
            opt("4",     "Открыть страницу покупки Black Membership", Y)
            opt("5",     "Заполнить рандомный адрес доставки (Индия)", Y)
            opt("6",     "Полный цикл: адрес → email → оплата → ссылка  (авто)", G)
            print(f"  {DIM}           ⚠  Для 4, 5, 6 закройте Chrome с этим профилем заранее{RST}")
            if not selected.get("issued_ts"):
                opt("2 / В", "Пометить «Выдан»        (активен, передан клиенту)", B)
            opt("3 / И", "Пометить «Использован»   (завершён, перенести в архив)", M)
            opt("К",     "Восстановить сессию из JSON куков (cookies_backup/)", C)
            opt("Л",     "Проверить вход  →  восстановить / удалить / архив", C)
            opt("Н",     f"Примечание{'  «' + _sel_note[:30] + '»' if _sel_note else '  (нет)'}", Y)
            opt("9",     "Удалить профиль навсегда", R)
            print()
            opt("0", "Назад к списку", R)
            print()

            action = input(f"  {BLD}Действие: {RST}").strip().upper()

            if action in ("0", ""):
                break

            if action in ("П", "7"):
                _uname  = selected["username"]
                _ppath  = selected["path"]

                def _run_bg_check(_path, _un):
                    import threading as _thr
                    def _worker():
                        chk = asyncio.run(
                            _check_black_store_activation(_path, username=_un, headless=True))
                        st  = chk.get("status", "unknown")
                        vt  = chk.get("valid_till")
                        err = chk.get("error")
                        print(f"\n\n  ╔══ [ФОН] ПРОВЕРКА +91 {_un} ══╗")
                        if err:
                            print(f"  ║  {R}Ошибка: {err}{RST}")
                            time.sleep(3)
                        elif st == "activated":
                            print(f"  ║  {M}✨ Activated{' до ' + vt if vt else ''} — аккаунт активирован{RST}")
                            print(f"  ║  {DIM}Для архива выберите пункт «3 / И» в меню профиля{RST}")
                        elif st == "activate_now":
                            print(f"  ║  {G}⭐ Activate Now — доступен к выдаче{RST}")
                            _act_url = chk.get("activation_url", "")
                            _short_url = chk.get("short_link", "")
                            _vt = chk.get("valid_till", "")
                            if _act_url:
                                print(f"  ║  {G}🔗 Ссылка активации:{RST}")
                                if _short_url and _short_url != _act_url:
                                    print(f"  ║  {G}Короткая: {_short_url}{RST}")
                                print(f"  ║  {B}{_act_url}{RST}")
                                _send_tg_activation(_un, _act_url, _short_url, _vt,
                                                    login_str=selected.get("login_str", ""),
                                                    issued_str=selected.get("issued_str", ""))
                            else:
                                print(f"  ║  {Y}⏳ Ссылка не получена{RST}")
                        elif st == "explore_now":
                            print(f"  ║  {C}💳 Explore Now — доступен для оплаты{RST}")
                        elif st == "not_logged_in":
                            print(f"  ║  {R}Не залогинен{RST}")
                        else:
                            print(f"  ║  {Y}Статус неизвестен{RST}")
                        print(f"  ╚{'═' * 30}╝\n")
                    t = _thr.Thread(target=_worker, daemon=True)
                    t.start()

                _run_bg_check(_ppath, _uname)
                print(f"\n  {G}✅ Проверка запущена в фоне (headless) для +91 {_uname}{RST}")
                print(f"  {DIM}Результат появится в консоли автоматически...{RST}")
                time.sleep(1.5)

            elif action == "5":
                addr = _gen_indian_address()
                print(f"\n  {C}Генерирую адрес для {_disp_phone(selected['username'])}:{RST}")
                print(f"  Имя    : {W}{addr['name']}{RST}")
                print(f"  Индекс : {W}{addr['pincode']}{RST}  {DIM}({addr['city']}, {addr['state']}){RST}")
                print(f"  Адрес1 : {DIM}{addr['house']}{RST}")
                print(f"  Адрес2 : {DIM}{addr['road']}{RST}")
                print(f"\n  {DIM}Открываю браузер...{RST}")
                ok, msg = asyncio.run(_do_fill_address(selected["path"], addr))
                if ok:
                    print(f"\n  {G}✅ Адрес сохранён: {msg}{RST}")
                elif msg in ("OUT_OF_STOCK", "OUT_OF_STOCK_2"):
                    _ag = " (адрес введён 2 раза)" if msg == "OUT_OF_STOCK_2" else ""
                    _ask_delete_profile_console(
                        selected["path"], selected["username"],
                        f"Currently out of stock{_ag} — товар недоступен для этого профиля.")
                else:
                    print(f"\n  {R}❌ Ошибка: {msg}{RST}")
                pause()

            elif action == "6":
                # ── Полный цикл покупки прямо для этого профиля ──────────────
                cls()
                header("ПОЛНЫЙ ЦИКЛ — ПОКУПКА BLACK MEMBERSHIP", G)
                print(f"  Профиль : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
                print(f"  Вход    : {DIM}{selected['login_str']}{RST}")
                print()
                opt("1", "3 месяца  — ₹343  (скидка 20%)", G)
                opt("2", "12 месяцев — ₹1,499", C)
                print()
                opt("0", "Отмена", R)
                print()
                tariff6 = input(f"  {BLD}Тариф [1/2/0]: {RST}").strip()
                if tariff6 == "0" or tariff6 == "":
                    pass
                elif tariff6 in ("1", "2"):
                    months6  = 3  if tariff6 == "1" else 12
                    label6   = "3 месяца / ₹343" if tariff6 == "1" else "12 месяцев / ₹1,499"
                    print(f"\n  {DIM}Запускаю браузер — {label6}...{RST}")
                    print(f"  {DIM}Цепочка: вход → адрес → email → оплата → ссылка{RST}")
                    print(f"  {DIM}Карты берутся по установленному порядку (data/card_order.json){RST}\n")
                    ok6, msg6 = asyncio.run(
                        _do_buy_membership(selected["path"], months6, card=None)
                    )
                    if ok6:
                        print(f"\n  {G}{BLD}✅ {msg6}{RST}")
                        pause()
                    elif msg6.startswith("OUT_OF_STOCK"):
                        addr_info = msg6.split("|", 1)[1] if "|" in msg6 else ""
                        if addr_info:
                            print(f"  {G}✔ {addr_info}{RST}")
                        _ag = " (адрес введён 2 раза)" if "OUT_OF_STOCK_2" in msg6 else ""
                        if _ask_delete_profile_console(
                            selected["path"], selected["username"],
                            f"Currently out of stock{_ag} — товар недоступен для этого профиля."):
                            time.sleep(2)
                            break
                        time.sleep(1)
                    elif any(x in msg6.lower() for x in ("не залогинен", "нет входа", "not logged")):
                        # Профиль слетел — сразу предлагаем восстановить сессию из куков
                        _console_offer_restore(selected["path"], selected["username"])
                        pause()
                    else:
                        print(f"\n  {R}❌ {msg6}{RST}")
                        pause()
                else:
                    print(f"\n  {R}Неверный выбор.{RST}")
                    time.sleep(2)

            elif action == "1":
                ok = open_chrome(selected["path"])
                if ok:
                    print(f"\n  {G}Chrome запущен.{RST}  "
                          f"{DIM}https://www.flipkart.com/flipkart-black-store{RST}")
                pause()

            elif action == "4":
                url = ("https://www.flipkart.com/search?q=black+memebership"
                       "&sid=mcd&as=on&as-show=on"
                       "&otracker=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
                       "&otracker1=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
                       "&as-pos=1&as-type=RECENT"
                       "&suggestionId=black+memebership%7CVas"
                       "&as-searchtext=black")
                chrome = _find_chrome()
                if chrome:
                    subprocess.Popen([chrome, f"--user-data-dir={selected['path'].resolve()}", url])
                    print(f"\n  {G}Chrome запущен — страница покупки Black Membership.{RST}")
                else:
                    print(f"\n  {R}Chrome не найден.{RST}")
                pause()

            elif action in ("2", "В", "B"):       # Cyrillic В или латинская B
                ts = time.time()
                if _save_meta_field(selected["path"], issued_ts=ts):
                    selected["issued_ts"]  = ts
                    selected["issued_str"] = _fmt_msk(ts)
                    print(f"\n  {B}🔵 Профиль {_disp_phone(selected['username'])} "
                          f"помечен «Выдан».{RST}")
                    print(f"  {DIM}{selected['issued_str']}{RST}")
                time.sleep(1.5)

            elif action in ("3", "И", "I"):       # Cyrillic И или латинская I
                cls()
                header("ПОДТВЕРЖДЕНИЕ", M)
                print(f"  Профиль  : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
                print(f"  Создан   : {G}{selected['login_str']}{RST}")
                if selected.get("issued_str"):
                    print(f"  Выдан    : {B}{selected['issued_str']}{RST}")
                print()
                print(f"  {Y}Запись сохранится в архив, папка профиля будет удалена.{RST}")
                print()
                confirm = input(f"  {BLD}Подтвердить? [Д/Н]: {RST}").strip().lower()
                if confirm in ("д", "y"):
                    used_ts = time.time()
                    ok_arch = _archive_profile(selected["path"], used_ts=used_ts)
                    if ok_arch:
                        print(f"\n  {M}✅ Профиль {_disp_phone(selected['username'])} "
                              f"сохранён в архив, папка удалена.{RST}")
                        print(f"  {DIM}Использован: {_fmt_msk(used_ts)}{RST}")
                    else:
                        print(f"\n  {R}Ошибка архивирования.{RST}")
                    time.sleep(2)
                    break   # выйти из подменю, обновить список

            elif action in ("Н", "N"):
                cls()
                header("ПРИМЕЧАНИЕ К ПРОФИЛЮ", Y)
                _cur_note = selected.get("note") or ""
                print(f"  Профиль  : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
                if _cur_note:
                    print(f"  Текущее  : {Y}{_cur_note}{RST}")
                else:
                    print(f"  Текущее  : {DIM}(нет){RST}")
                print()
                try:
                    new_note = input(f"  {BLD}Новое примечание (Enter = очистить): {RST}").strip()
                except EOFError:
                    new_note = _cur_note
                _save_meta_field(selected["path"], note=new_note)
                selected["note"] = new_note
                if new_note:
                    print(f"\n  {G}✅ Примечание сохранено: {Y}{new_note}{RST}")
                else:
                    print(f"\n  {DIM}Примечание очищено.{RST}")
                time.sleep(1.5)

            elif action in ("К", "K"):
                cls()
                header("ВОССТАНОВЛЕНИЕ ИЗ КУКОВ", C)
                username = selected.get("username", "")
                print(f"  Профиль : {W}{BLD}{_disp_phone(username)}{RST}\n")
                # Ищем JSON в cookies_backup/ по номеру
                bk_dir = Path("cookies_backup")
                candidates = []
                if bk_dir.exists():
                    for f in bk_dir.glob("*.json"):
                        if username in f.name:
                            candidates.append(f)
                # Также смотрим текущую папку
                for f in Path(".").glob(f"cookies*{username}*.json"):
                    if f not in candidates:
                        candidates.append(f)
                if not candidates:
                    print(f"  {Y}JSON куков не найден.{RST}")
                    print(f"  {DIM}Скачайте файл из TG и положите в папку cookies_backup/{RST}")
                    print(f"  {DIM}Имя файла должно содержать номер телефона ({username}){RST}")
                else:
                    print(f"  Найдено файлов куков: {len(candidates)}")
                    for ci, cf in enumerate(candidates, 1):
                        print(f"    [{ci}] {cf}")
                    if len(candidates) == 1:
                        chosen = candidates[0]
                    else:
                        raw_ci = input(f"\n  Выберите файл [1-{len(candidates)}]: ").strip()
                        try:
                            chosen = candidates[int(raw_ci) - 1]
                        except Exception:
                            chosen = candidates[0]
                    print(f"\n  {DIM}Восстанавливаю сессию из {chosen.name}...{RST}\n")
                    ok, msg = asyncio.run(
                        _restore_profile_from_cookies(chosen, username))
                    if ok:
                        print(f"  {G}✅ Профиль восстановлен: {msg}{RST}")
                        print(f"  {DIM}Браузер закрыт. Откройте профиль через пункт 1.{RST}")
                    else:
                        print(f"  {R}❌ Не удалось восстановить: {msg}{RST}")
                pause()
                break

            elif action in ("Л", "L"):
                cls()
                header("ПРОВЕРКА ВХОДА", C)
                print(f"  Профиль : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
                if _profile_login_check_flow(selected):
                    break   # профиль удалён / в архиве — обновить список

            elif action == "9":
                cls()
                header("УДАЛЕНИЕ ПРОФИЛЯ", R)
                print(f"  Профиль  : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
                print(f"  Создан   : {G}{selected['login_str']}{RST}")
                print()
                print(f"  {R}{BLD}Профиль будет удалён безвозвратно!{RST}")
                print()
                confirm = input(f"  {BLD}Подтвердить удаление? [Д/Н]: {RST}").strip().lower()
                if confirm == "д":
                    deleted = False
                    try:
                        shutil.rmtree(str(selected["path"]))
                        deleted = True
                        print(f"\n  {M}🗑 Профиль {_disp_phone(selected['username'])} удалён.{RST}")
                    except Exception as exc:
                        print(f"\n  {R}Ошибка удаления: {exc}{RST}")
                    time.sleep(2)
                    if deleted:
                        break  # профиля больше нет — выходим из подменю


def _load_archive_records() -> list[dict]:
    """Загружает все JSON-записи из папки архива."""
    records: list[dict] = []
    if not USED_PROFILES_DIR.exists():
        return records
    for f in USED_PROFILES_DIR.glob("record_*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Обогащаем строками дат
            for key, fmt_key in [
                ("login_ts", "login_str"),
                ("issued_ts", "issued_str"),
                ("used_ts", "used_str"),
                ("subscription_bought_ts", "subscription_bought_str"),
                ("subscription_expires_ts", "subscription_expires_str"),
            ]:
                if key in data and data[key]:
                    data.setdefault(fmt_key, _fmt_msk(float(data[key])))
            records.append(data)
        except Exception:
            pass
    records.sort(key=lambda x: x.get("used_ts") or x.get("login_ts") or 0, reverse=True)
    return records


def archive_record_file(record: dict) -> Path:
    """Путь к JSON-записи архива для профиля."""
    phone = str(record.get("username", "?"))
    ts_int = int(record.get("used_ts") or 0)
    return USED_PROFILES_DIR / f"record_{phone}_{ts_int}.json"


def restore_archive_record(record: dict) -> tuple[bool, str]:
    """Восстанавливает метаданные профиля в chrome_profiles_done/.

    Убирает метки использования (used_ts/used_str) — восстановленный профиль
    снова «живой», а не архивный. Куки-сессия заливается отдельно
    (_restore_profile_from_cookies) — тогда Chrome откроется уже залогиненным.
    """
    phone = str(record.get("username", "?"))
    rec_path = archive_record_file(record)
    if (DONE_PROFILES_DIR / f"profile_{phone}").exists():
        return False, "Профиль уже есть в chrome_profiles_done"
    if (PROFILES_DIR / f"profile_{phone}").exists():
        return False, "Профиль уже есть в chrome_profiles"
    if not rec_path.exists():
        return False, f"Запись не найдена: {rec_path.name}"
    try:
        profile_dir = DONE_PROFILES_DIR / f"profile_{phone}"
        profile_dir.mkdir(parents=True, exist_ok=True)
        meta = {k: v for k, v in record.items() if k not in ("used_ts", "used_str")}
        _atomic_write_text(
            profile_dir / ".profile_meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2),
        )
        rec_path.unlink()
        return True, f"Восстановлен → chrome_profiles_done/profile_{phone}"
    except Exception as exc:
        return False, str(exc)


def delete_archive_record(record: dict) -> tuple[bool, str]:
    """Удаляет JSON-запись из архива."""
    rec_path = archive_record_file(record)
    try:
        if rec_path.exists():
            rec_path.unlink()
        return True, "Запись удалена из архива"
    except Exception as exc:
        return False, str(exc)


def screen_used():
    """Архив использованных профилей — интерактивный список."""
    while True:
        cls()
        header("АРХИВ  —  ИСПОЛЬЗОВАННЫЕ ПРОФИЛИ", M)

        records = _load_archive_records()

        if not records:
            print(f"  {DIM}Архив пуст.{RST}")
            print(f"  {DIM}Используйте пункт 3 → «И» чтобы перенести профиль в архив.{RST}")
            pause()
            return

        section(f"Всего в архиве: {len(records)} записей  (новые сначала)")
        print()

        for i, r in enumerate(records, 1):
            phone    = r.get("username", "?")
            created  = r.get("login_str",  "—")
            issued   = r.get("issued_str", "—")
            used     = r.get("used_str",   "—")
            months   = r.get("subscription_months")
            bought   = r.get("subscription_bought_str", "—")
            expires  = r.get("subscription_expires_str", "—")
            ck_file  = Path("cookies_backup") / f"cookies_{phone}.json"
            ck_mark  = f"  {G}[куки✓]{RST}" if ck_file.exists() else ""

            print(f"  {BLD}{Y}[{i:>2}]{RST}  {W}+91 {phone}{RST}{ck_mark}")
            print(f"         {DIM}Создан   :{RST} {created}")
            print(f"         {DIM}Выдан    :{RST} {issued}")
            print(f"         {DIM}Архив    :{RST} {used}")
            if months:
                tariff = f"{months} мес. · {'365 дн.' if months == 12 else '90 дн.'}"
                print(f"         {G}Подписка :{RST} {tariff}  →  куплена {bought}")
                print(f"         {G}Истекает :{RST} {expires}")
            print()

        print(f"  {DIM}Папка: {USED_PROFILES_DIR.resolve()}{RST}")
        print()
        opt("0", "Назад", R)
        print()

        choice = input(f"  {BLD}Выберите профиль [1-{len(records)}] или 0: {RST}").strip()
        if choice in ("0", ""):
            return

        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(records)):
                raise ValueError
        except ValueError:
            print(f"\n  {R}Неверный номер.{RST}")
            time.sleep(2)
            continue

        # ── Детальный вид архивного профиля ──────────────────────────────────
        r = records[idx]
        _screen_used_detail(r)


def _screen_used_detail(r: dict) -> None:
    """Детальный экран архивного профиля с действиями."""
    while True:
        cls()
        phone   = r.get("username", "?")
        ts_int  = int(r.get("used_ts") or 0)
        rec_path = USED_PROFILES_DIR / f"record_{phone}_{ts_int}.json"

        header(f"АРХИВ  —  +91 {phone}", M)
        print()
        print(f"  {DIM}Создан   :{RST} {r.get('login_str',  '—')}")
        print(f"  {DIM}Выдан    :{RST} {r.get('issued_str', '—')}")
        print(f"  {DIM}Архив    :{RST} {r.get('used_str',   '—')}")
        months = r.get("subscription_months")
        if months:
            tariff = f"{months} мес. · {'365 дн.' if months == 12 else '90 дн.'}"
            print(f"  {G}Подписка :{RST} {tariff}  →  куплена {r.get('subscription_bought_str','—')}")
            print(f"  {G}Истекает :{RST} {r.get('subscription_expires_str','—')}")
        email = r.get("buyer_email") or ""
        if email:
            print(f"  {DIM}Email    :{RST} {email}")
        note = r.get("note") or ""
        if note:
            print(f"  {Y}Примечание:{RST} {note}")
        print()

        ck_file = Path("cookies_backup") / f"cookies_{phone}.json"
        has_ck  = ck_file.exists()
        restored_done = (DONE_PROFILES_DIR / f"profile_{phone}").exists()
        restored_live = (PROFILES_DIR      / f"profile_{phone}").exists()
        already_there = restored_done or restored_live

        section("Действия")
        if already_there:
            where = "chrome_profiles_done" if restored_done else "chrome_profiles"
            print(f"  {DIM}Профиль уже восстановлен → {where}/profile_{phone}{RST}")
        else:
            opt("В", "Восстановить профиль  (создать папку профиля с метаданными)", G)

        if has_ck:
            opt("К", f"Экспорт куки  ({len(json.loads(ck_file.read_text(encoding='utf-8')))} шт. → файл + текст)", C)
        else:
            print(f"  {DIM}Куки не сохранены  (экспортируйте из живого профиля перед архивацией){RST}")

        opt("Д", "Удалить запись из архива навсегда", R)
        print()
        opt("0", "Назад к списку", R)
        print()

        action = input(f"  {BLD}Действие: {RST}").strip().upper()

        if action in ("0", ""):
            return

        # ── Восстановить ─────────────────────────────────────────────────────
        if action == "В":
            if already_there:
                print(f"\n  {Y}Профиль уже существует.{RST}")
                time.sleep(2)
                continue
            if not rec_path.exists():
                print(f"\n  {R}Запись архива не найдена: {rec_path.name}{RST}")
                time.sleep(2)
                return
            try:
                profile_dir = DONE_PROFILES_DIR / f"profile_{phone}"
                profile_dir.mkdir(parents=True, exist_ok=True)
                meta_file   = profile_dir / ".profile_meta.json"
                _atomic_write_text(meta_file, json.dumps(r, ensure_ascii=False, indent=2))
                rec_path.unlink()
                print(f"\n  {G}✅ Профиль восстановлен → chrome_profiles_done/profile_{phone}{RST}")
                print(f"  {DIM}Теперь используйте «К» (восстановить из куков) или войдите заново.{RST}")
                time.sleep(3)
                return
            except Exception as e:
                print(f"\n  {R}Ошибка восстановления: {e}{RST}")
                time.sleep(3)
                return

        # ── Экспорт куки ─────────────────────────────────────────────────────
        if action == "К":
            if not has_ck:
                print(f"\n  {Y}Файл куков не найден.{RST}")
                time.sleep(2)
                continue
            try:
                raw  = ck_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                print(f"\n  {G}Файл:{RST} {ck_file.resolve()}")
                print(f"  {G}Куков:{RST} {len(data)} шт.")
                print()
                print(f"  {DIM}--- JSON (первые 1500 символов) ---{RST}")
                snippet = raw[:1500]
                print(f"  {W}{snippet}{RST}")
                if len(raw) > 1500:
                    print(f"  {DIM}... (обрезано, полный файл выше){RST}")
            except Exception as e:
                print(f"\n  {R}Ошибка чтения куков: {e}{RST}")
            pause()
            continue

        # ── Удалить запись ───────────────────────────────────────────────────
        if action == "Д":
            confirm = input(f"\n  {R}Удалить запись +91 {phone} из архива? (Д/Н): {RST}").strip().upper()
            if confirm == "Д":
                try:
                    if rec_path.exists():
                        rec_path.unlink()
                    print(f"\n  {G}Запись удалена.{RST}")
                    time.sleep(1.5)
                    return
                except Exception as e:
                    print(f"\n  {R}Ошибка: {e}{RST}")
                    time.sleep(2)
            continue


# ── Данные для генерации индийских адресов ───────────────────────────────────

# Координаты городов (lat, lon) — для ctx.set_geolocation() чтобы Flipkart
# получил корректный ответ и не выдавал "Request timed out"
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "New Delhi":   (28.6139, 77.2090),
    "Delhi":       (28.6517, 77.2219),
    "Mumbai":      (19.0760, 72.8777),
    "Bengaluru":   (12.9716, 77.5946),
    "Chennai":     (13.0827, 80.2707),
    "Kolkata":     (22.5726, 88.3639),
    "Hyderabad":   (17.3850, 78.4867),
    "Pune":        (18.5204, 73.8567),
    "Ahmedabad":   (23.0225, 72.5714),
    "Jaipur":      (26.9124, 75.7873),
    "Lucknow":     (26.8467, 80.9462),
    "Patna":       (25.5941, 85.1376),
    "Ernakulam":   ( 9.9816, 76.2999),
    "Kochi":       ( 9.9312, 76.2673),
    "Coimbatore":  (11.0168, 76.9558),
    "Chandigarh":  (30.7333, 76.7794),
    "Nagpur":      (21.1458, 79.0882),
    "Indore":      (22.7196, 75.8577),
    "Bhopal":      (23.2599, 77.4126),
    "Vadodara":    (22.3072, 73.1812),
    "Surat":       (21.1702, 72.8311),
    "Noida":       (28.5355, 77.3910),
    "Gurgaon":     (28.4595, 77.0266),
    "Visakhapatnam":(17.6868, 83.2185),
    "Bhubaneswar": (20.2961, 85.8245),
    "Agra":        (27.1767, 78.0081),
    "Varanasi":    (25.3176, 82.9739),
    "Kanpur":      (26.4499, 80.3319),
}

_IND_PINCODES = [
    ("110001","New Delhi","Delhi"),        ("110011","New Delhi","Delhi"),
    ("110051","Delhi","Delhi"),            ("110092","Delhi","Delhi"),
    ("400001","Mumbai","Maharashtra"),     ("400028","Mumbai","Maharashtra"),
    ("400053","Mumbai","Maharashtra"),     ("400063","Mumbai","Maharashtra"),
    ("560001","Bengaluru","Karnataka"),    ("560011","Bengaluru","Karnataka"),
    ("560038","Bengaluru","Karnataka"),    ("560095","Bengaluru","Karnataka"),
    ("600001","Chennai","Tamil Nadu"),     ("600006","Chennai","Tamil Nadu"),
    ("600017","Chennai","Tamil Nadu"),     ("600040","Chennai","Tamil Nadu"),
    ("700001","Kolkata","West Bengal"),    ("700019","Kolkata","West Bengal"),
    ("700029","Kolkata","West Bengal"),    ("700064","Kolkata","West Bengal"),
    ("500001","Hyderabad","Telangana"),    ("500016","Hyderabad","Telangana"),
    ("500032","Hyderabad","Telangana"),    ("500072","Hyderabad","Telangana"),
    ("411001","Pune","Maharashtra"),       ("411007","Pune","Maharashtra"),
    ("411021","Pune","Maharashtra"),       ("411041","Pune","Maharashtra"),
    ("380001","Ahmedabad","Gujarat"),      ("380006","Ahmedabad","Gujarat"),
    ("380015","Ahmedabad","Gujarat"),      ("380058","Ahmedabad","Gujarat"),
    ("302001","Jaipur","Rajasthan"),       ("302012","Jaipur","Rajasthan"),
    ("302017","Jaipur","Rajasthan"),       ("302020","Jaipur","Rajasthan"),
    ("226001","Lucknow","Uttar Pradesh"),  ("226010","Lucknow","Uttar Pradesh"),
    ("226016","Lucknow","Uttar Pradesh"),  ("226022","Lucknow","Uttar Pradesh"),
    ("800001","Patna","Bihar"),            ("800020","Patna","Bihar"),
    ("682001","Ernakulam","Kerala"),       ("682020","Kochi","Kerala"),
    ("682030","Kochi","Kerala"),           ("641001","Coimbatore","Tamil Nadu"),
    ("641011","Coimbatore","Tamil Nadu"),  ("160017","Chandigarh","Chandigarh"),
    ("440001","Nagpur","Maharashtra"),     ("440010","Nagpur","Maharashtra"),
    ("452001","Indore","Madhya Pradesh"),  ("452006","Indore","Madhya Pradesh"),
    ("462001","Bhopal","Madhya Pradesh"),  ("462011","Bhopal","Madhya Pradesh"),
    ("390001","Vadodara","Gujarat"),       ("390007","Vadodara","Gujarat"),
    ("395001","Surat","Gujarat"),          ("395002","Surat","Gujarat"),
    ("201301","Noida","Uttar Pradesh"),    ("201305","Noida","Uttar Pradesh"),
    ("122001","Gurugram","Haryana"),       ("122016","Gurugram","Haryana"),
    ("121001","Faridabad","Haryana"),      ("248001","Dehradun","Uttarakhand"),
    ("781001","Guwahati","Assam"),         ("751001","Bhubaneswar","Odisha"),
    ("492001","Raipur","Chhattisgarh"),    ("834001","Ranchi","Jharkhand"),
    ("620001","Tiruchirappalli","Tamil Nadu"), ("625001","Madurai","Tamil Nadu"),
    ("530001","Visakhapatnam","Andhra Pradesh"), ("522001","Guntur","Andhra Pradesh"),
]

_IND_FIRST = [
    "Rahul","Amit","Rajesh","Suresh","Pradeep","Arun","Vikram","Sanjay","Deepak",
    "Ravi","Manish","Ankit","Rohit","Gaurav","Vinay","Karan","Ajay","Nitin","Vivek",
    "Naveen","Praveen","Manoj","Dinesh","Hemant","Saurabh","Pankaj","Arjun","Rohan",
    "Tushar","Sumit","Shubham","Akash","Rishabh","Harsh","Priya","Anjali","Pooja",
    "Sneha","Kavya","Divya","Sunita","Meena","Anita","Nisha","Preeti","Neha","Shreya",
    "Ritika","Monika","Swati","Pallavi","Kavitha","Shweta","Tanvi","Sonam","Bhavna",
    "Ashok","Sunil","Alok","Kapil","Varun","Mohit","Tarun","Rajan","Sachin","Sandeep",
]

_IND_LAST = [
    "Sharma","Verma","Gupta","Singh","Kumar","Patel","Shah","Mehta","Joshi","Nair",
    "Reddy","Rao","Pillai","Iyer","Sinha","Mishra","Tiwari","Pandey","Dubey","Yadav",
    "Saxena","Bhat","Kaur","Malhotra","Kapoor","Chauhan","Aggarwal","Agarwal","Bose",
    "Das","Chopra","Arora","Bansal","Goel","Mittal","Khanna","Sethi","Bhatt","Choudhary",
    "Shukla","Tripathi","Pathak","Jain","Khatri","Bajaj","Oberoi","Dhawan","Kohli","Gill",
]

_IND_BUILD_NAMES = [
    "Sunflower Apartments","Green Park Residency","Lotus Tower","Silver Heights",
    "Rainbow Colony","Shanti Nagar Complex","Laxmi Vihar","Krishna Enclave",
    "Sai Residency","Om Shanti Apartments","Paradise Complex","Sunrise Apartments",
    "Garden View Heights","Blue Bird Residency","Royal Residency","Anand Vihar",
    "Ganesh Apartment","Saraswati Bhavan","Vijay Nagar Complex","Indira Enclave",
    "Mahalaxmi Tower","Durga Residency","Radha Krishna Apartments","Sri Balaji Tower",
    "New Ashok Nagar Society","Patel Nagar Colony","Ambedkar Housing Society",
    "Unity Apartments","Harmony Heights","Emerald Court","Diamond Plaza","Pearl Tower",
]

_IND_BUILD_PREFIX = ["Flat","House","Door","Block","Plot","Unit","Room","Shop"]

_IND_AREAS = [
    "Sector 12","Sector 15","Sector 21","Sector 7","Sector 28",
    "Phase 1","Phase 2","Phase 3","Civil Lines","Model Town",
    "Anna Nagar","T Nagar","Adyar","Velachery","Mylapore",
    "Koramangala","Indiranagar","JP Nagar","HSR Layout","Whitefield",
    "Banjara Hills","Jubilee Hills","Madhapur","Gachibowli","Kukatpally",
    "Andheri East","Bandra West","Dadar","Kurla","Powai",
    "Salt Lake","Park Street","Behala","Ballygunge","Jadavpur",
    "Vaishali Nagar","Malviya Nagar","Raja Park","Mansarovar","Bapu Nagar",
    "Lajpat Nagar","Rajouri Garden","Pitampura","Rohini","Dwarka",
    "Gomti Nagar","Hazratganj","Aliganj","Vikas Nagar","Indira Nagar",
]

_IND_ROAD_TYPES = [
    "Main Road","Cross Road","1st Street","2nd Street","3rd Street","4th Street",
    "Avenue","Lane","Ring Road","Bypass Road","Link Road","Service Road",
    "North Road","South Road","East Road","West Road","Old Road","New Road",
]


def _gen_indian_address() -> dict:
    """Генерирует случайный реальный индийский адрес."""
    pincode, city, state = random.choice(_IND_PINCODES)
    name    = f"{random.choice(_IND_FIRST)} {random.choice(_IND_LAST)}"
    num     = random.randint(1, 999)
    bname   = random.choice(_IND_BUILD_NAMES)
    prefix  = random.choice(_IND_BUILD_PREFIX)
    house   = f"{prefix} {num}, {bname}"
    area    = random.choice(_IND_AREAS)
    rtype   = random.choice(_IND_ROAD_TYPES)
    rnum    = random.randint(1, 120)
    road    = f"{rnum}, {area}, {rtype}"
    return {"name": name, "pincode": pincode, "city": city, "state": state,
            "house": house, "road": road}


async def _flipkart_is_logged_in(profile_path: Path) -> bool:
    """Headless-проверка: сессия профиля ещё активна на Flipkart?"""
    from playwright.async_api import async_playwright as _apw
    try:
        if _vpn_extension_dir():
            if not await _ensure_extension_in_profile(profile_path):
                return False
            await _vpn_chrome_cooldown(extra=0.5)
        async with _apw() as _pw2:
            _ctx2 = await _pw2.chromium.launch_persistent_context(
                str(profile_path),
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                ignore_https_errors=True,
            )
            _pg2 = _ctx2.pages[0] if _ctx2.pages else await _ctx2.new_page()
            try:
                await _pg2.goto("https://www.flipkart.com",
                                timeout=20_000, wait_until="domcontentloaded")
                await _pg2.wait_for_timeout(3_000)
                _has_login_btn = await _pg2.evaluate("""() => {
                    for (const el of document.querySelectorAll(
                            'button,a,div,span,[role="button"]')) {
                        const t = (el.innerText || '').trim();
                        if (t !== 'Login') continue;
                        const r = el.getBoundingClientRect();
                        if (r.width > 20 && r.height > 8) return true;
                    }
                    return false;
                }""")
                return not _has_login_btn
            finally:
                try:
                    await _ctx2.close()
                except Exception:
                    pass
    except Exception:
        return True  # при ошибке не удаляем


async def _check_recent_black_orders(page) -> list:
    """
    Открывает страницу заказов и ищет ЛЮБЫЕ заказы Flipkart BLACK.
    Возвращает список найденных строк с описанием заказа.
    """
    import re as _re_ord
    found = []
    try:
        await page.goto("https://www.flipkart.com/account/orders?link=home_orders",
                        wait_until="domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(2_000)
        try:
            await page.wait_for_function(
                "() => document.body.innerText.toLowerCase().includes('order')",
                timeout=8_000)
        except Exception:
            pass
        # Извлекаем карточки заказов через DOM: ищем элементы с текстом "flipkart black"
        # и берём ближайший текст о дате/статусе из того же блока
        js_orders = await page.evaluate("""() => {
            const results = [];
            // Ищем все элементы, текст которых содержит "flipkart black"
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const seen = new Set();
            let node;
            while ((node = walker.nextNode())) {
                const txt = node.textContent || ‘’;
                if (!txt.toLowerCase().includes(‘flipkart black’)) continue;
                // Поднимаемся до блока-контейнера (до 8 уровней)
                let block = node.parentElement;
                for (let i = 0; i < 8 && block; i++) {
                    const h = block.getBoundingClientRect().height;
                    if (h > 60 || block.tagName === ‘ARTICLE’) break;
                    block = block.parentElement;
                }
                if (!block || seen.has(block)) continue;
                seen.add(block);
                const blockText = (block.innerText || block.textContent || ‘’).replace(/\\s+/g, ‘ ‘).trim();
                results.push(blockText.slice(0, 300));
            }
            return results;
        }""")

        for block_text in (js_orders or []):
            if "flipkart black" not in block_text.lower():
                continue
            # Форматы: "Delivered Today, Jun 28" / "Delivered on Mon, 14 Jun ‘24" / "Ordered on May 25"
            _DATE_RE = (
                r"(delivered|ordered|cancelled|return(?:ed)?)"
                r"\s+(?:on\s+)?"
                r"((?:Today|Tomorrow|Yesterday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.]\s*)?"
                r"(\d{1,2}\s+[A-Za-z]{3,9}(?:\s*[‘’]?\d{2,4})?|[A-Za-z]{3,9}\s+\d{1,2}(?:[,\s]+\d{4})?)"
            )
            m_date = _re_ord.search(_DATE_RE, block_text, _re_ord.I)
            status = ""
            if m_date:
                _day = (m_date.group(2) or "").rstrip(", ").strip()
                _dt  = m_date.group(3).strip()
                status = m_date.group(1).capitalize() + " " + (f"{_day}, {_dt}" if _day else _dt)
            # Первая строка блока = название товара
            first_line = block_text.splitlines()[0].strip()[:60] if block_text else ""
            desc = first_line or "Flipkart Black"
            if status:
                desc += f" ({status})"
            found.append(desc)

        # Fallback: если JS не нашёл — парсим innerText
        if not found:
            page_text = ""
            try:
                page_text = await page.evaluate("() => document.body.innerText")
            except Exception:
                pass
            lines = page_text.splitlines()
            for i, line in enumerate(lines):
                if "flipkart black" not in line.lower():
                    continue
                context_text = " ".join(lines[max(0, i-5):i+10])
                m_date = _re_ord.search(
                    r"(delivered|ordered|cancelled|return(?:ed)?)"
                    r"\s+(?:on\s+)?"
                    r"((?:Today|Tomorrow|Yesterday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.]\s*)?"
                    r"(\d{1,2}\s+[A-Za-z]{3,9}(?:\s*[‘’]?\d{2,4})?|[A-Za-z]{3,9}\s+\d{1,2}(?:[,\s]+\d{4})?)",
                    context_text, _re_ord.I)
                status = ""
                if m_date:
                    _day = (m_date.group(2) or "").rstrip(", ").strip()
                    _dt  = m_date.group(3).strip()
                    status = m_date.group(1).capitalize() + " " + (f"{_day}, {_dt}" if _day else _dt)
                if line.strip():
                    desc = f"{line.strip()[:60]}"
                    if status:
                        desc += f" ({status})"
                    found.append(desc)
    except Exception:
        pass
    return found


@_serialize_purchase
async def _do_fill_address(profile_path: Path, addr: dict,
                           stop_at_payment: bool = False) -> tuple[bool, str]:
    """Открывает профиль, проверяет вход и заполняет форму адреса через Buy Now."""
    # Актуальный способ оплаты из файла (GUI мог переключить в другом процессе)
    with contextlib.suppress(Exception):
        _pay_method[0] = _load_pay_method()
    # Как bot.py: сброс sticky-cancel после предыдущего Stop/shutdown
    _purchase_cancel.clear()
    _clear_filled_email()
    use_vpn, proxy, net_err = await _resolve_profile_scenario_network(profile_path)
    if net_err:
        return False, net_err
    set_profile_op_stage(profile_path, "Сеть / браузер")
    if not use_vpn and not proxy:
        # Лёгкая проба (сокет/httpx) не совпадает с TLS-отпечатком реального Chrome
        # и даёт ложное «недоступно», хотя браузер Flipkart открывает. Поэтому
        # не блокируем сценарий — открываем браузер, у него устойчивая навигация
        # и детект Access Denied решают по факту.
        if await _flipkart_direct_accessible():
            print(f"  {G}Flipkart доступен (личный VPN на ПК).{RST}")
        else:
            print(f"  {DIM}Проба недоступности — открываю браузер напрямую "
                  f"(личный VPN на ПК)…{RST}")

    if _is_profile_locked(profile_path):
        print(f"  {Y}Профиль занят — закрываю Chrome и очищаю локи...{RST}")
        _clear_stale_profile_locks(profile_path)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "playwright не установлен  (pip install playwright)"

    pw = await async_playwright().start()
    ctx = None
    _keep_open = False
    try:
        _pre_inject_chrome_prefs(profile_path)
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            **_browser_launch_kw(
                phone=_phone_from_path(profile_path),
                profile_path=profile_path, use_vpn=use_vpn, proxy=proxy,
            ))
        _mark_browser_network(ctx, use_vpn=use_vpn, proxy=proxy)
        await _close_extension_startup_tabs(ctx)
        await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
        page = await _main_work_page(ctx)
        await _maximize_window(ctx, page)
        if use_vpn:
            if not await _vpn_connect_for_profile(ctx, profile_path):
                return False, "VPN не подключился — заполнение адреса отменено"
            await _dismiss_all_veepn_welcome(ctx)
            await _close_vpn_extension_tabs(ctx, await _vpn_ext_id(ctx))
            page = await _main_work_page(ctx)
            with contextlib.suppress(Exception):
                await page.bring_to_front()
        set_profile_op_stage(profile_path, "Открытие Flipkart")
        _stealth2 = _build_stealth_js_m()
        if _stealth2:
            await ctx.add_init_script(_stealth2)

        _phone_label = _phone_from_path(profile_path)

        if not ctx.pages:
            page = await ctx.new_page()

        async def _open_home() -> tuple[bool, str]:
            nonlocal page
            ok, pg, err = await _navigate_flipkart_resilient(
                ctx, page, "https://www.flipkart.com",
                label=_phone_label, profile_path=profile_path,
            )
            page = pg
            return ok, err

        ok_nav, _nav_err = await _open_home()
        if not ok_nav:
            page = await _main_work_page(ctx)
            ok_nav, _nav_err = await _open_home()
        if not ok_nav:
            return False, f"Не удалось открыть Flipkart: {_nav_err}"
        page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)
        await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
        await page.wait_for_timeout(1_500)

        # Проверяем — нет ли уже купленного Black Membership
        _recent_orders = await _check_recent_black_orders(page)
        if _recent_orders:
            _order_info = "; ".join(_recent_orders[:3])
            print(f"\n  {Y}╔══ ВНИМАНИЕ: уже куплено! ════════════════════════════════╗{RST}")
            print(f"  {Y}║  {_order_info[:70]}{RST}")
            print(f"  {Y}╚═══════════════════════════════════════════════════════════╝{RST}")
            _orders_confirm_ev.clear()
            _orders_confirm_choice[0] = None
            _tg_send_direct_kb(
                f"⚠️ *Уже куплено!*\n\n"
                f"Профиль `{_phone_label}` — найден заказ *Flipkart BLACK*:\n"
                f"_{_order_info[:200]}_\n\n"
                f"Что делать?",
                {"inline_keyboard": [
                    [{"text": "✅ Продолжить заполнение", "callback_data": f"fill:orders_ok:{_phone_label}"}],
                    [{"text": "🗑 Удалить профиль", "callback_data": f"fill:orders_del:{_phone_label}"}],
                ]}
            )
            print(f"  {Y}Жду ответа в Telegram (60 сек)...{RST}")
            _wait_dl = asyncio.get_event_loop().time() + 60
            while asyncio.get_event_loop().time() < _wait_dl:
                if _orders_confirm_ev.is_set():
                    break
                await asyncio.sleep(1)
            if _orders_confirm_choice[0] is False:
                # Удаляем профиль
                print(f"  {R}Удаляю профиль {_phone_label}...{RST}")
                try:
                    await ctx.close()
                except Exception:
                    pass
                try:
                    await pw.stop()
                except Exception:
                    pass
                import shutil as _shutil
                try:
                    _shutil.rmtree(str(profile_path), ignore_errors=True)
                except Exception:
                    pass
                _tg_send_direct(f"🗑 Профиль `{_phone_label}` удалён (дубль заказа)")
                return False, "Профиль удалён — дублирующий заказ"
            else:
                print(f"  {G}Продолжаю заполнение...{RST}")
                # Возвращаемся на главную для Buy Now (resilient: Access Denied / VPN)
                ok_home, _home_err = await _open_home()
                if not ok_home:
                    return False, f"Не удалось вернуться на главную: {_home_err}"
                await page.wait_for_timeout(1_000)

        product_url = _profile_url(profile_path)
        if await _page_logged_out(page):
            return False, _NOT_LOGGED_IN_MSG
        ok_prod, page, _prod_err = await _navigate_flipkart_resilient(
            ctx, page, product_url, label=_phone_label, profile_path=profile_path,
        )
        if not ok_prod:
            return False, f"Не открылась страница товара: {_prod_err}"
        if await _page_logged_out(page):
            return False, _NOT_LOGGED_IN_MSG

        # Buy Now создаёт реальную сессию чекаута (прямой URL формы не работает)
        set_profile_op_stage(profile_path, "Buy Now")
        err = await _click_buy_now(page, product_url, skip_goto=True)
        if err:
            return False, err

        # После нажатия Buy Now браузер всегда оставляем открытым
        _keep_open = True
        set_profile_op_stage(profile_path, "Заполнение адреса")

        async def _fill_addr_and_wait():
            """Заполняет форму адреса и ждёт viewcheckout."""
            lat, lon = _CITY_COORDS.get(addr.get("city", ""), (20.5937, 78.9629))
            await ctx.set_geolocation({"latitude": lat, "longitude": lon})
            await _maximize_window(ctx, page)
            if not await _fill_address_form(page, addr):
                return False
            with contextlib.suppress(Exception):
                _save_meta_field(profile_path, **_profile_addr_meta(addr))
            try:
                await page.wait_for_url("**/viewcheckout**", timeout=10_000)
            except Exception:
                pass
            await page.wait_for_timeout(1_000)
            return True

        async def _click_continue():
            import random as _r
            cont = page.locator(
                "button:has-text('Continue'), a:has-text('Continue'), "
                "[role='button']:has-text('Continue'), "
                "button:has-text('Deliver Here'), button:has-text('Deliver here'), "
                "button:has-text('Place order'), button:has-text('Place Order'), "
                "button:has-text('PLACE ORDER')"
            ).last
            if await cont.count() > 0:
                await _human_click(page, cont, before=_r.uniform(0.1, 0.25))
                await page.wait_for_timeout(900)
            else:
                # Fallback: координатный клик по видимой Continue / Deliver Here
                try:
                    bb = await page.evaluate(r"""() => {
                        const want = ['continue', 'deliver here', 'place order'];
                        for (const el of document.querySelectorAll(
                            'button,a,[role="button"]')) {
                            const t = (el.innerText || '').trim().toLowerCase();
                            if (!want.some(w => t === w || t.startsWith(w))) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 40 || r.height < 12 || el.offsetParent === null)
                                continue;
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                        return null;
                    }""")
                    if bb:
                        await page.mouse.click(bb["x"], bb["y"])
                        await page.wait_for_timeout(900)
                except Exception:
                    pass

        # ── Шаг A: если сразу попали на форму адреса ────────────────────────
        if "changeShippingAddress" in page.url or "add/form" in page.url:
            print(f"  Заполняю форму адреса...")
            if not await _fill_addr_and_wait():
                return False, "Кнопка Save Address не найдена в форме адреса"

        # ── Шаг B: viewcheckout → email → Continue → payments ───────────────
        set_profile_op_stage(profile_path, "Чекаут / Continue")
        async def _oos_delete_return(retry_done: bool = False):
            """OOS — профиль НЕ удаляем сами. Закрываем браузер и сообщаем
            вызывающему кодом OUT_OF_STOCK; удаление — только после подтверждения."""
            nonlocal _keep_open
            _keep_open = False  # браузер закрыть, профиль оставить
            return False, "OUT_OF_STOCK_2" if retry_done else "OUT_OF_STOCK"

        async def _oos_try_new_addr() -> bool:
            """Пробует нажать Change и заполнить новый адрес при OOS.
            Возвращает True если после смены OOS исчез."""
            try:
                _change_btn = page.locator(
                    "button:has-text('Change'), [role='button']:has-text('Change'), "
                    "button:has-text('Try Another Address'), a:has-text('Try Another Address')"
                ).first
                if await _change_btn.count() > 0:
                    await _change_btn.click()
                    await page.wait_for_timeout(1_500)
            except Exception:
                pass
            if "changeShippingAddress" not in page.url and "add/form" not in page.url:
                return False
            _new_addr = _gen_indian_address()
            _lat2, _lon2 = _CITY_COORDS.get(_new_addr.get("city", ""), (20.5937, 78.9629))
            await ctx.set_geolocation({"latitude": _lat2, "longitude": _lon2})
            if not await _fill_address_form(page, _new_addr):
                return False
            try:
                await page.wait_for_url("**/viewcheckout**", timeout=10_000)
            except Exception:
                pass
            await page.wait_for_timeout(1_000)
            try:
                _b2 = (await page.evaluate("() => (document.body?.textContent || '').toLowerCase()"))
                return not any(p in _b2 for p in _OOS_PHRASES)
            except Exception:
                return False

        if "viewcheckout" in page.url:
            body = (await page.evaluate(
                "() => (document.body && document.body.textContent) || ''")).lower()
            if any(p in body for p in _OOS_PHRASES):
                print(f"  {R}✘ Currently out of stock — с этим профилем ничего не сделать{RST}")
                print(f"  {Y}→ удалите профиль и возьмите следующий доступный{RST}")
                return await _oos_delete_return(retry_done=True)

            reached = await _viewcheckout_to_payments(page, profile_path)
            if reached == "OUT_OF_STOCK":
                print(f"  {R}✘ Currently out of stock — удалите профиль, следующий{RST}")
                return await _oos_delete_return(retry_done=True)

            if not reached and "address-map" in page.url:
                # Set Location привёл на карту, но навигация назад не завершилась —
                # ждём ещё и пробуем нажать Confirm + go_back
                print(f"  Всё ещё на address-map — жду возврата...")
                try:
                    await page.wait_for_url("**/viewcheckout**", timeout=10_000)
                    reached = await _viewcheckout_to_payments(page, profile_path)
                except Exception:
                    if "address-map" in page.url:
                        print(f"  address-map: нажимаю Back...")
                        await page.go_back()
                        await page.wait_for_timeout(3_000)
                        reached = await _viewcheckout_to_payments(page, profile_path)
            if reached == "OUT_OF_STOCK":
                return await _oos_delete_return(retry_done=True)

            if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url) \
                    and "address-map" not in page.url:
                print(f"  Flipkart запросил адрес — заполняю...")
                if not await _fill_addr_and_wait():
                    return False, "Кнопка Save Address не найдена (после Continue)"
                reached = await _viewcheckout_to_payments(page, profile_path)
            if reached == "OUT_OF_STOCK":
                return await _oos_delete_return(retry_done=True)
            if reached == "CAPTCHA":
                _keep_open = False
                return False, "Капча Flipkart зависла (Are you a human?) — не удалось пройти даже после обновлений. Попробуйте запустить ещё раз позже."

        # ── Шаг C: проверяем payments ────────────────────────────────────────
        # Не закрываем браузер и не стопаем процесс: F5/товар → Buy Now → Continue
        if "payments" not in page.url:
            for _pr in range(1, _PAYMENTS_REACH_ROUNDS + 1):
                if "payments" in page.url:
                    break
                _ckcancel()
                _keep_open = True
                print(
                    f"  {Y}Нет страницы оплаты — обновляю товар и повторяю "
                    f"Buy Now / Continue ({_pr}/{_PAYMENTS_REACH_ROUNDS})…{RST}"
                )
                err_r = await _click_buy_now(page, product_url, skip_goto=False)
                if err_r:
                    if err_r == _NOT_LOGGED_IN_MSG or "не залогинен" in err_r.lower():
                        return False, err_r
                    print(f"  {DIM}повтор Buy Now: {err_r}{RST}")
                    continue
                if "changeShippingAddress" in page.url or "add/form" in page.url:
                    if not await _fill_addr_and_wait():
                        continue
                if "viewcheckout" in page.url or (
                    "checkout" in (page.url or "") and "payments" not in (page.url or "")
                ):
                    body_r = ""
                    with contextlib.suppress(Exception):
                        body_r = (await page.evaluate(
                            "() => (document.body && document.body.textContent) || ''"
                        )).lower()
                    if any(p in body_r for p in _OOS_PHRASES):
                        return await _oos_delete_return(retry_done=True)
                    reached = await _viewcheckout_to_payments(page, profile_path)
                    if reached == "OUT_OF_STOCK":
                        return await _oos_delete_return(retry_done=True)
                    if reached == "CAPTCHA":
                        _keep_open = True
                        return False, (
                            "Капча Flipkart зависла (Are you a human?) — "
                            "не удалось пройти даже после обновлений. "
                            "Попробуйте запустить ещё раз позже."
                        )
                    if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url) \
                            and "address-map" not in page.url:
                        if await _fill_addr_and_wait():
                            reached = await _viewcheckout_to_payments(page, profile_path)
                    if reached is True or "payments" in page.url:
                        break
                try:
                    await page.wait_for_url("**/payments**", timeout=8_000)
                except Exception:
                    pass

        if "payments" not in page.url:
            # Браузер оставляем открытым — пользователь / следующий прогон
            _keep_open = True
            _cur_c = page.url.split("?")[0].rstrip("/")
            if _cur_c in ("https://www.flipkart.com", "https://flipkart.com", "https://m.flipkart.com"):
                if await _page_logged_out(page):
                    return False, _NOT_LOGGED_IN_MSG
                return False, ("Оформление сбросило на главную Flipkart — сессия слетела "
                               "или сработала бот-защита. Повторите позже / восстановите вход.")
            if await _page_logged_out(page):
                return False, _NOT_LOGGED_IN_MSG
            return False, f"Не удалось перейти на оплату после {_PAYMENTS_REACH_ROUNDS} повторов (URL: {_cur_c})"

        if stop_at_payment:
            import time as _t_sap
            set_profile_op_stage(profile_path, "Страница оплаты")
            with contextlib.suppress(Exception):
                page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)
                await _maximize_window(ctx, page)
                await page.bring_to_front()
            print(f"  {G}✔ Страница оплаты (+91 {_phone_label}){RST}")
            _save_meta_field(
                profile_path,
                prepared_ts=_t_sap.time(),
                status="email_completed",
                **_profile_addr_meta(addr),
                **({"buyer_email": _get_filled_email()} if _get_filled_email() else {}),
            )
            _keep_open = False
            return True, f"{addr.get('name', '')} | {addr.get('pincode', '')} {addr.get('city', '')} → ✅ готов"

        _pp_phone = ""
        try:
            _parts = profile_path.name.split("_")
            _num = next((p for p in reversed(_parts) if len(p) >= 10 and p.isdigit()), "")
            if _num:
                _pp_phone = _num[-10:]
        except Exception:
            pass
        try:
            await _send_cookies_tg(ctx, profile_path.name, _pp_phone)
        except Exception as _cke:
            print(f"  TG cookies: {_cke}")
        # pay_method.txt: gift — тот же путь, что и в _do_buy_membership
        _pm = _pay_method[0] if _pay_method[0] in ("gift", "card") else _load_pay_method()
        _pay_ok = await _do_payments_page(
            page, gift=(_pm == "gift"), profile_path=profile_path,
        )
        try:
            _em = _get_filled_email()
            _save_meta_field(
                profile_path,
                status="email_completed",
                **_profile_addr_meta(addr),
                **({"buyer_email": _em} if _em else {}),
            )
        except Exception:
            pass
        if _pm == "gift":
            if _pay_ok is True:
                try:
                    await _handle_post_payment(page, ctx, profile_path, phone_number=_pp_phone)
                except Exception as _pp_e:
                    print(f"  Post-payment: {_pp_e}")
                await _close_browser_session(ctx, pw, profile_path, disconnect_vpn=True)
                ctx = None
                return True, f"{addr['name']} | {addr.get('pincode','')} {addr.get('city','')} → ✅ гифт-оплата"
            if _pay_ok == "gift_insufficient":
                _keep_open = False
                return False, "Не хватает подарочных карт для оплаты"
            _keep_open = False
            return False, "Оплата подарочными картами не удалась"
        try:
            await _handle_post_payment(page, ctx, profile_path, phone_number=_pp_phone)
        except Exception as _pp_e:
            print(f"  Post-payment: {_pp_e}")
        # Пост-пеймент завершён — закрываем браузер
        await _close_browser_session(ctx, pw, profile_path, disconnect_vpn=True)
        ctx = None
        return True, f"{addr['name']} | {addr.get('pincode','')} {addr.get('city','')} → ✅ оплата запущена"
    except _PurchaseCancelled:
        print(f"  {Y}🛑 Выполнение отменено пользователем — закрываю браузер.{RST}")
        _keep_open = False
        return False, "CANCELLED"
    except Exception as exc:
        # Если в процессе нажали «Остановить» — браузер убит, await Playwright упал
        # с ошибкой. Трактуем как чистую отмену, а не как ошибку выполнения.
        if _purchase_cancel.is_set():
            print(f"  {Y}🛑 Остановлено пользователем — браузер закрыт.{RST}")
            _keep_open = False
            return False, "CANCELLED"
        msg = str(exc)
        _keep_open = False
        return False, msg
    finally:
        set_profile_op_stage(profile_path, "")
        if not _keep_open:
            await _close_browser_session(ctx, pw, profile_path, disconnect_vpn=True)
        else:
            with contextlib.suppress(Exception):
                await _vpn_disconnect(ctx)
            _unregister_purchase_profile(profile_path)


def screen_fill_address():
    """Автоматически заполнить рандомный индийский адрес в выбранных профилях."""
    print(f"  {DIM}Проверка доступности Flipkart...{RST}")
    if not _is_flipkart_accessible_sync():
        print(f"\n  {R}⚠ Flipkart недоступен{RST}")
        print(f"  {Y}Повторите попытку позже.{RST}")
        pause()
        return
    print(f"  {G}Flipkart доступен.{RST}")
    while True:
        cls()
        header("ЗАПОЛНИТЬ АДРЕС  —  ИНДИЯ", Y)

        profiles = _load_done_profiles()

        if not profiles:
            print(f"  {DIM}Нет профилей с успешным входом.{RST}")
            print(f"  {DIM}Запустите автоматизацию (пункт 1).{RST}")
            pause()
            return

        section(f"Профили  [{len(profiles)} шт.]")
        print()
        for i, p in enumerate(profiles, 1):
            mark = f"{B}🔵 Выдан{RST}" if p.get("issued_ts") else f"{G}● Доступен{RST}"
            print(
                f"  {BLD}{Y}[{i:>2}]{RST}  {W}{_disp_phone(p['username']):<14}{RST}  "
                f"{DIM}{p['login_str']:<25}{RST}  {mark}"
            )
        print()
        print(f"  {DIM}⚠  Закройте Chrome для этого профиля перед запуском!{RST}")
        print()
        opt("А", "Заполнить ВСЕ профили", G)
        opt("0", "Назад", R)
        print()

        choice = input(
            f"  {BLD}Выберите профиль [1-{len(profiles)}] или А/0: {RST}"
        ).strip().upper()

        if choice == "0" or choice == "":
            return

        if choice in ("А", "A"):
            to_fill = profiles
        else:
            try:
                idx = int(choice) - 1
                if not (0 <= idx < len(profiles)):
                    raise ValueError
                to_fill = [profiles[idx]]
            except ValueError:
                print(f"\n  {R}Неверный номер.{RST}")
                time.sleep(3)
                continue

        print()
        _ff_oos = []  # профили с OOS — подтверждение удаления в конце
        for p in to_fill:
            addr = _gen_indian_address()
            print(f"  {C}{_disp_phone(p['username'])}{RST}")
            print(f"    Имя    : {W}{addr['name']}{RST}")
            print(f"    Индекс : {W}{addr['pincode']}{RST}  {DIM}({addr['city']}, {addr['state']}){RST}")
            print(f"    Адрес1 : {DIM}{addr['house']}{RST}")
            print(f"    Адрес2 : {DIM}{addr['road']}{RST}")
            print(f"  {DIM}  Открываю браузер...{RST}")
            ok, msg = asyncio.run(_do_fill_address(p["path"], addr))
            if ok:
                print(f"  {G}  ✅ Адрес сохранён: {msg}{RST}")
            elif msg in ("OUT_OF_STOCK", "OUT_OF_STOCK_2"):
                _ff_oos.append(p)
                print(f"  {R}  ✘ Out of stock — профиль НЕ удалён{RST}")
            else:
                print(f"  {R}  ❌ Ошибка: {msg}{RST}")
            print()
            time.sleep(0.5)

        if _ff_oos:
            print(f"  {R}🚫 Out of stock: {len(_ff_oos)} профил(ей){RST}")
            for _op in _ff_oos:
                print(f"     • {_disp_phone(_op['username'])}")
            _ans = input(f"\n  {BLD}Удалить эти {len(_ff_oos)} профил(ей) с OOS? [Д/Н]: {RST}").strip().upper()
            if _ans in ("Д", "ДА", "Y", "YES"):
                import shutil as _shff
                for _op in _ff_oos:
                    _shff.rmtree(str(_op["path"]), ignore_errors=True)
                print(f"  {Y}🗑 Удалено: {len(_ff_oos)}{RST}")
            else:
                print(f"  {G}Профили оставлены.{RST}")

        pause()
        return


_BLACK_URLS = {
    3:  ("https://www.flipkart.com/flipkart-black-3-months-membership/p/itmaacb5a37224f3"
         "?pid=XVZHES63WKZK7FUM&lid=LSTXVZHES63WKZK7FUMBUWY7G&marketplace=FLIPKART"
         "&q=black+memebership&store=mcd&spotlightTagId=default_BestsellerId_mcd"
         "&srno=s_1_1&otracker=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
         "&otracker1=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
         "&fm=search-autosuggest&iid=ec6c550e-8297-4bb2-83b5-b0d33bdc7656"
         ".XVZHES63WKZK7FUM.SEARCH&ppt=sp&ppn=sp"
         "&ssid=oglqkugkog0000001781433224445&qH=6e96dc2cdd18f97e"
         "&ov_redirect=true&ov_redirect=true"),
    12: ("https://www.flipkart.com/flipkart-black-12-months-membership/p/itm8600c62a8a210"
         "?pid=XVZGYWT68RSFDUBX&lid=LSTXVZGYWT68RSFDUBXFZNBQK&marketplace=FLIPKART"
         "&q=black+memebership&store=mcd&spotlightTagId=default_FkPickId_mcd"
         "&srno=s_1_2&otracker=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
         "&otracker1=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
         "&fm=search-autosuggest&iid=ec6c550e-8297-4bb2-83b5-b0d33bdc7656"
         ".XVZGYWT68RSFDUBX.SEARCH&ppt=sp&ppn=sp"
         "&ssid=oglqkugkog0000001781433224445&qH=6e96dc2cdd18f97e"
         "&ov_redirect=true&ov_redirect=true"),
}


async def _fill_address_form(page, addr: dict) -> bool:
    """Заполняет форму адреса на текущей странице. Возвращает True если Save нажата.

    Поддерживает checkout (Full Name / House No / Road) и Manage Addresses
    (Name / 10-digit mobile / Locality / Address Area and Street / SAVE).
    В addr опционально: phone (10 цифр профиля).
    """
    async def _fill(hint: str, value: str, delay: int = 40) -> bool:
        focused = await page.evaluate("""(hint) => {
            const h = hint.toLowerCase();
            function activate(inp) {
                inp.scrollIntoView({block: 'center'});
                inp.focus();
                inp.dispatchEvent(new MouseEvent('click',
                    {bubbles: true, cancelable: true, view: window}));
                return true;
            }
            const fields = [...document.querySelectorAll('input, textarea')];
            // 1) Точный placeholder
            for (const inp of fields) {
                const ph = (inp.placeholder || '').trim().toLowerCase();
                if (ph === h) return activate(inp);
            }
            // 2) placeholder содержит hint
            for (const inp of fields) {
                const ph = (inp.placeholder || '').toLowerCase();
                if (ph.includes(h)) return activate(inp);
            }
            for (const label of document.querySelectorAll('label')) {
                if (!label.textContent.toLowerCase().includes(h)) continue;
                if (label.htmlFor) {
                    const inp = document.getElementById(label.htmlFor);
                    if (inp) return activate(inp);
                }
                let root = label.parentElement;
                for (let i = 0; i < 5 && root; i++, root = root.parentElement) {
                    const inp = root.querySelector('input:not([type=hidden]), textarea');
                    if (inp) return activate(inp);
                }
                label.click();
                return true;
            }
            return false;
        }""", hint)
        if not focused:
            return False
        await page.wait_for_timeout(200)
        await page.keyboard.press("Control+a")
        await page.keyboard.type(value, delay=delay)
        return True

    # Ждём загрузки формы
    try:
        await page.wait_for_selector("input, textarea", state="visible", timeout=10_000)
    except Exception:
        pass
    await page.wait_for_timeout(800)

    # НЕ кликаем «Use my location» — это вызывает «Request timed out».
    import random as _r

    phone10 = "".join(ch for ch in str(addr.get("phone") or "") if ch.isdigit())[-10:]
    # Manage Addresses: Name + mobile + Locality + Address (Area and Street)
    is_manage = "account/addresses" in (page.url or "")
    if not is_manage:
        with contextlib.suppress(Exception):
            is_manage = bool(await page.evaluate("""() => {
                const phs = [...document.querySelectorAll('input, textarea')]
                    .map(i => (i.placeholder || '').toLowerCase());
                return phs.some(p => p.includes('10-digit') || p === 'locality'
                    || p.includes('area and street'));
            }"""))

    if is_manage:
        print(f"  {DIM}форма Manage Addresses — заполняю все поля"
              f"{(' +phone ' + phone10) if phone10 else ''}…{RST}")
        await _fill("Name", addr["name"])
        await page.wait_for_timeout(200)
        if phone10:
            ok_ph = await _fill("10-digit", phone10, delay=50)
            if not ok_ph:
                await _fill("mobile", phone10, delay=50)
            await page.wait_for_timeout(200)
        # Валидный 6-значный пинкод (на скрине часто остаётся мусор вроде 60001)
        pin = str(addr.get("pincode") or "")
        if len(pin) != 6 or not pin.isdigit():
            pin = _IND_PINCODES[0][0]
            addr["pincode"], addr["city"], addr["state"] = (
                _IND_PINCODES[0][0], _IND_PINCODES[0][1], _IND_PINCODES[0][2]
            )
        await _fill("Pincode", pin, delay=70)
        await page.wait_for_timeout(3_500)
        locality = addr.get("locality") or random.choice(_IND_AREAS)
        await _fill("Locality", locality)
        await page.wait_for_timeout(150)
        line = addr.get("address_line") or f"{addr['house']}, {addr['road']}"
        ok_area = await _fill("Area and Street", line, delay=25)
        if not ok_area:
            await _fill("Address", line, delay=25)
        await page.wait_for_timeout(200)
        # City/State обычно авто после pincode; добить если пусто
        with contextlib.suppress(Exception):
            city_empty = await page.evaluate("""() => {
                for (const inp of document.querySelectorAll('input')) {
                    const ph = (inp.placeholder || '').toLowerCase();
                    if (ph.includes('city') || ph.includes('district') || ph.includes('town'))
                        return !(inp.value || '').trim();
                }
                return false;
            }""")
            if city_empty:
                await _fill("City", addr["city"])
        # Alternate Phone — очистить
        with contextlib.suppress(Exception):
            await page.evaluate("""() => {
                for (const inp of document.querySelectorAll('input')) {
                    const ph = (inp.placeholder || '').toLowerCase();
                    if (!ph.includes('alternate')) continue;
                    inp.focus();
                    inp.value = '';
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""")
        # Home
        with contextlib.suppress(Exception):
            home = page.get_by_text(re.compile(r"^Home$", re.I)).first
            if await home.count() > 0:
                await home.click(timeout=2000)
                await page.wait_for_timeout(150)
    else:
        # Checkout-форма
        await _fill("Full Name", addr["name"])
        await page.wait_for_timeout(300)
        if phone10:
            await _fill("10-digit", phone10, delay=50)
            await page.wait_for_timeout(200)

        await _fill("Pincode", addr["pincode"], delay=80)
        await page.wait_for_timeout(4_000)

        state_filled = await page.evaluate("""() => {
            for (const inp of document.querySelectorAll('input')) {
                const ph = (inp.placeholder || inp.getAttribute('aria-label') || '').toLowerCase();
                if (ph.includes('state')) return (inp.value || '').trim().length > 0;
            }
            return false;
        }""")
        if not state_filled:
            await _fill("State", addr["state"])
            await page.wait_for_timeout(300)
            await _fill("City", addr["city"])
            await page.wait_for_timeout(300)

        await _fill("House No", addr["house"])
        await page.wait_for_timeout(150)
        await _fill("Road name", addr["road"])
        await page.wait_for_timeout(150)

        try:
            await page.evaluate("""() => {
                for (const inp of document.querySelectorAll(
                        'input[type=tel], input[type=number], input[type=text]')) {
                    const ph = (inp.placeholder || inp.getAttribute('aria-label') || '').toLowerCase();
                    const lbl = (() => {
                        let el = inp.parentElement;
                        for (let i = 0; i < 5 && el; i++, el = el.parentElement) {
                            const l = el.querySelector('label');
                            if (l) return l.textContent.toLowerCase();
                        }
                        return '';
                    })();
                    if (ph.includes('alternate') || lbl.includes('alternate')) {
                        inp.focus();
                        inp.value = '';
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }
            }""")
            await page.wait_for_timeout(200)
        except Exception:
            pass

        try:
            radio = page.locator("input[type='radio']").first
            if await radio.count() > 0:
                await _human_click(page, radio, before=_r.uniform(0.05, 0.15))
                await page.wait_for_timeout(200)
        except Exception:
            pass

    # Checkout: «Save Address»; Manage Addresses: «SAVE»
    save_loc = page.get_by_text("Save Address", exact=True).first
    if await save_loc.count() == 0:
        save_loc = page.get_by_text(re.compile(r"^Save(\s+Address)?$", re.I)).first
    if await save_loc.count() == 0:
        return False

    _RED_ERR_JS = """() => {
        // Геонимы — не ошибки валидации, даже если красного цвета (автозаполнение)
        const GEO = new Set([
            'maharashtra','delhi','karnataka','tamilnadu','gujarat','rajasthan',
            'westbengal','andhrapradesh','madhyapradesh','uttarpradesh','punjab',
            'haryana','bihar','odisha','telangana','kerala','jharkhand','uttarakhand',
            'himachalpradesh','assam','goa','chhattisgarh','chandigarh',
            'jammuandkashmir','ladakh','tripura','manipur','meghalaya','mizoram',
            'nagaland','sikkim','arunachalpradesh','pondicherry','puducherry'
        ]);
        for (const el of document.querySelectorAll('div,span,p,label')) {
            const txt = (el.innerText || '').trim();
            if (!txt || txt.length > 120) continue;
            const m = window.getComputedStyle(el).color
                .match(/rgb[a]?\\(\\s*(\\d+),\\s*(\\d+),\\s*(\\d+)/);
            if (m && +m[1] > 150 && +m[2] < 80 && +m[3] < 80) {
                const norm = txt.toLowerCase().replace(/[\\s&]+/g, '');
                if (GEO.has(norm)) continue;
                // Короткий текст только из букв — вероятно название города/района
                if (/^[a-z\\s]+$/i.test(txt) && txt.split(/\\s+/).length <= 3 && txt.length <= 25) continue;
                return txt;
            }
        }
        return '';
    }"""

    async def _click_any_confirm() -> str:
        """Жмёт ЛЮБУЮ всплывающую кнопку подтверждения (Confirm/Update/OK/Proceed/
        Yes/Done) сразу, как появилась. Возвращает текст нажатой кнопки или ''."""
        try:
            _dlg = await page.evaluate(r"""() => {
                const want = ['confirm','update address','update','ok','okay',
                              'proceed','yes','done','continue anyway','got it'];
                for (const el of document.querySelectorAll('button, a, div, span, [role="button"], input[type="submit"]')) {
                    const t = (el.innerText || el.value || el.textContent || '').trim().toLowerCase();
                    if (!want.includes(t)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 30 || r.height < 12 || el.offsetParent === null) continue;
                    return {x: r.x + r.width/2, y: r.y + r.height/2, t};
                }
                return null;
            }""")
            if _dlg:
                await page.mouse.click(_dlg["x"], _dlg["y"])
                print(f"  {G}✔ Нажал «{_dlg['t']}» (всплывающий диалог){RST}")
                await page.wait_for_timeout(1_000)
                return _dlg["t"]
        except Exception:
            pass
        return ""

    for _save_try in range(4):
        await _human_click(page, save_loc, before=_r.uniform(0.1, 0.25))
        await page.wait_for_timeout(1_800)

        # Общий обработчик: любой всплывающий Confirm/Update/OK/Proceed — жмём сразу.
        # Если после нажатия появился ещё один диалог — жмём и его (до 3 подряд).
        _confirmed = False
        for _dc in range(3):
            _pressed = await _click_any_confirm()
            if not _pressed:
                break
            _confirmed = True
            # ушли с формы / кнопка Save пропала — адрес принят
            if await save_loc.count() == 0 or not await save_loc.is_visible():
                break
            if any(s in page.url for s in ("viewcheckout", "payments", "changeShipping")):
                break

        # Диалог "Update with these details?" — нажимаем CONFIRM (спец. запас)
        try:
            if "#dialogBoxOpen" in page.url or await page.locator("text=Update with these details?").count() > 0:
                _confirm = page.get_by_text("CONFIRM", exact=True).first
                if await _confirm.count() > 0 and await _confirm.is_visible():
                    await _human_click(page, _confirm, before=0.3)
                    print(f"  {G}✔ CONFIRM нажат (диалог Update with these details){RST}")
                    _confirmed = True
                    for _ in range(4):
                        await page.wait_for_timeout(1_000)
                        if await save_loc.count() == 0 or not await save_loc.is_visible():
                            break
                        if any(s in page.url for s in ("viewcheckout", "payments", "changeShipping")):
                            break
        except Exception:
            pass

        # Диалог "Incorrect address" — выбираем первый вариант и нажимаем CONFIRM
        if not _confirmed:
            try:
                _incorr = page.locator("text=Incorrect address")
                if await _incorr.count() > 0 and await _incorr.first.is_visible():
                    # Если ни один вариант не выбран — выбираем первый
                    _radios = page.locator('input[type="radio"]')
                    if await _radios.count() > 0:
                        _checked = page.locator('input[type="radio"]:checked')
                        if await _checked.count() == 0:
                            await _radios.first.click()
                            await page.wait_for_timeout(300)
                    _confirm2 = page.get_by_text("CONFIRM", exact=True).first
                    if await _confirm2.count() > 0 and await _confirm2.is_visible():
                        await _human_click(page, _confirm2, before=0.3)
                        print(f"  {G}✔ CONFIRM нажат (диалог Incorrect address){RST}")
                        _confirmed = True
                        for _ in range(4):
                            await page.wait_for_timeout(1_000)
                            if await save_loc.count() == 0 or not await save_loc.is_visible():
                                break
                            if any(s in page.url for s in ("viewcheckout", "payments", "changeShipping")):
                                break
            except Exception:
                pass

        # Если кнопка исчезла или ушли со страницы формы — адрес принят
        if await save_loc.count() == 0 or not await save_loc.is_visible():
            break
        if any(s in page.url for s in ("viewcheckout", "payments", "changeShipping")):
            break

        # После CONFIRM не проверяем ошибки — просто повторяем Save в след. итерации
        if _confirmed:
            await page.wait_for_timeout(500)
            continue

        red_err = ""
        try:
            red_err = await page.evaluate(_RED_ERR_JS)
        except Exception:
            pass

        # "Request timed out" — это ошибка геолокации, не валидации формы, игнорируем
        if not red_err or "timed out" in red_err.lower() or "try again" in red_err.lower():
            await page.wait_for_timeout(500)
            break

        print(f"  {Y}Адрес: ошибка валидации ({red_err!r}) — правлю поля, попытка {_save_try+1}/4...{RST}")

        # Попытка 1: проверяем State/City — если пусты, заполняем вручную
        if _save_try == 0:
            sf = await page.evaluate("""() => {
                for (const inp of document.querySelectorAll('input')) {
                    const ph = (inp.placeholder || '').toLowerCase();
                    if (ph.includes('state')) return (inp.value || '').trim().length > 0;
                }
                return false;
            }""")
            if not sf:
                await _fill("State", addr["state"])
                await page.wait_for_timeout(300)
                await _fill("City", addr["city"])
                await page.wait_for_timeout(300)
        # Попытка 2: запасной Delhi pincode
        if _save_try == 1:
            await _fill("Pincode", "110001", delay=80)
            await page.wait_for_timeout(2_000)
            await _fill("State", "Delhi")
            await page.wait_for_timeout(200)
            await _fill("City", "New Delhi")
            await page.wait_for_timeout(200)
        # Попытка 3: упрощаем имя
        if _save_try == 2:
            clean_name = "".join(c for c in addr.get("name", "Test User") if c.isalpha() or c == " ").strip()
            await _fill("Full Name", clean_name or "Test User")
            await page.wait_for_timeout(300)

        await page.wait_for_timeout(500)

    return True


async def _page_logged_out(page) -> bool:
    """Проверяет ТЕКУЩУЮ открытую страницу Flipkart: виден ли элемент «Login»
    (т.е. в профиле НЕТ входа в аккаунт). Лёгкая проверка без запуска браузера."""
    try:
        return bool(await page.evaluate("""() => {
            for (const el of document.querySelectorAll(
                    'button,a,div,span,[role="button"]')) {
                const t = (el.innerText || '').trim();
                if (t !== 'Login') continue;
                const r = el.getBoundingClientRect();
                if (r.width > 20 && r.height > 8) return true;
            }
            return false;
        }"""))
    except Exception:
        return False


_NOT_LOGGED_IN_MSG = "Профиль не залогинен — в аккаунт нет входа (Buy Now недоступен)"

# Если Buy Now / Continue не дали payments — F5 + повтор без закрытия браузера
_BUY_NOW_TO_CHECKOUT_ROUNDS = 5
_PAYMENTS_REACH_ROUNDS = 4


async def _click_buy_now(page, url: str, skip_goto: bool = False) -> str | None:
    """
    Переходит на страницу товара и нажимает Buy Now.
    skip_goto=True: страница уже загружена, навигация не нужна.
    Возвращает строку ошибки или None при успехе.
    """
    import re as _re

    _SUCCESS_PARTS = ("viewcheckout", "changeShippingAddress", "add/form", "payments")

    if not skip_goto:
        # Страница товара тяжёлая — не падаем по таймауту: даже частично
        # загруженной страницы хватает для клика Buy Now
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
        except Exception:
            pass
        # Ждём networkidle чтобы ov_redirect=true редиректы успели завершиться
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
    else:
        # Уже на странице — ждём стабилизации
        try:
            await page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass

    # Если Flipkart уже отредиректил на checkout/payments — Buy Now не нужен
    if any(s in page.url for s in _SUCCESS_PARTS):
        return None

    _BUY_CSS = (
        "button:has-text('Buy now'), button:has-text('Buy Now'), button:has-text('BUY NOW'), "
        "a:has-text('Buy now'), a:has-text('Buy Now'), a:has-text('BUY NOW'), "
        "[role='button']:has-text('Buy now'), [role='button']:has-text('Buy Now'), "
        "[role='button']:has-text('BUY NOW')"
    )

    async def _buy_now_target() -> dict | None:
        """Ищет жёлтую Buy Now; возвращает bbox если видна в viewport."""
        with contextlib.suppress(Exception):
            return await page.evaluate("""() => {
                const want = (t) => /^buy\\s*now$/i.test((t || '').replace(/\\s+/g, ' ').trim());
                const isYellow = (el) => {
                    const bg = window.getComputedStyle(el).backgroundColor;
                    const m = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                    if (!m) return false;
                    return +m[1] > 180 && +m[2] > 100 && +m[3] < 100;
                };
                const cands = [];
                for (const el of document.querySelectorAll(
                        'button, a, div, span, [role="button"]')) {
                    const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!want(t) && !(isYellow(el) && /buy/i.test(t) && t.length < 24))
                        continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 40 || r.height < 14) continue;
                    cands.push({
                        x: r.x + r.width / 2, y: r.y + r.height / 2,
                        w: r.width, h: r.height,
                        inView: r.top >= 0 && r.bottom <= innerHeight + 8
                            && r.left >= 0 && r.right <= innerWidth + 8,
                        yellow: isYellow(el),
                        t,
                    });
                }
                cands.sort((a, b) => (b.yellow - a.yellow) || (b.inView - a.inView)
                    || (b.y - a.y));
                return cands[0] || null;
            }""")
        return None

    # Скроллим вниз, пока не появится жёлтая Buy Now внизу
    print(f"  {DIM}Buy Now: ищу кнопку (скролл вниз при необходимости)…{RST}")
    target = None
    for _s in range(28):
        target = await _buy_now_target()
        if target and target.get("inView"):
            break
        if target and not target.get("inView"):
            # Есть на странице — к ней
            with contextlib.suppress(Exception):
                await page.evaluate(
                    """(y) => window.scrollTo({top: Math.max(0, y - 200), behavior: 'instant'})""",
                    float(target["y"]) + await page.evaluate("() => window.scrollY"),
                )
            await page.wait_for_timeout(350)
            target = await _buy_now_target()
            if target and target.get("inView"):
                break
        await page.mouse.wheel(0, 520)
        await page.wait_for_timeout(280)

    with contextlib.suppress(Exception):
        await page.wait_for_selector(_BUY_CSS, state="visible", timeout=2_000)

    landed = page.url

    async def _try_click(loc) -> bool:
        try:
            if await loc.count() > 0:
                await loc.last.scroll_into_view_if_needed()
                await loc.last.click(force=True)
                return True
        except Exception:
            pass
        return False

    # Кнопка Buy Now у Flipkart — <div> (не <button>), поэтому CSS/role-селекторы
    # её часто не видят. Клик по самому DOM-элементу устойчив к смещению вёрстки
    # (инфобар --no-sandbox, попап Google-переводчика и т.п.), в отличие от клика
    # по координатам мыши — те промахиваются, если сверху появилась панель.
    async def _buy_now_handle():
        try:
            handle = await page.evaluate_handle("""() => {
                const want = (t) => /^buy\\s*now$/i.test((t||'').replace(/\\s+/g,' ').trim());
                const isYellow = (el) => {
                    const m = window.getComputedStyle(el).backgroundColor
                        .match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                    return m && +m[1] > 180 && +m[2] > 100 && +m[3] < 100;
                };
                let best = null, bestScore = -1;
                for (const el of document.querySelectorAll(
                        'button, a, div, span, [role=\"button\"]')) {
                    const t = (el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ').trim();
                    const textHit = want(t);
                    if (!textHit && !(isYellow(el) && /buy/i.test(t) && t.length < 24))
                        continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 40 || r.height < 14) continue;
                    // приоритет: точное «buy now» + жёлтый + ниже по странице
                    const score = (textHit ? 100 : 0) + (isYellow(el) ? 40 : 0) + r.top / 100;
                    if (score > bestScore) { bestScore = score; best = el; }
                }
                return best;
            }""")
            return handle.as_element()
        except Exception:
            return None

    clicked = False
    el0 = await _buy_now_handle()
    if el0 is not None:
        print(f"  {DIM}Buy Now: клик по элементу…{RST}")
        try:
            await el0.scroll_into_view_if_needed()
            await page.wait_for_timeout(200)
            await el0.click(timeout=5_000)
            clicked = True
            await page.wait_for_timeout(400)
        except Exception:
            with contextlib.suppress(Exception):
                await el0.click(force=True)
                clicked = True
                await page.wait_for_timeout(400)

    # Резерв: клик по координатам центра найденной кнопки
    if not clicked and target and target.get("x"):
        print(f"  {DIM}Buy Now: клик по координатам «{target.get('t', 'Buy Now')}»…{RST}")
        with contextlib.suppress(Exception):
            await page.mouse.click(float(target["x"]), float(target["y"]))
            clicked = True
            await page.wait_for_timeout(400)

    if not clicked:
        clicked = (
            await _try_click(page.locator(_BUY_CSS))
            or await _try_click(page.get_by_role("button", name=_re.compile(r"buy\s*now", _re.I)))
            or await _try_click(page.get_by_role("link", name=_re.compile(r"buy\s*now", _re.I)))
        )

    if not clicked:
        el = None
        # 3) JS: ищем по innerText == "buy now" среди ВСЕХ элементов
        try:
            handle = await page.evaluate_handle("""() => {
                const tags = ['button','a','div','span','li','p','[role="button"]'];
                for (const sel of tags) {
                    for (const el of document.querySelectorAll(sel)) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (t === 'buy now') {
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 10) return el;
                        }
                    }
                }
                return null;
            }""")
            el = handle.as_element()
        except Exception:
            pass

        if el is None:
            # 4) Последний резерв: жёлтая кнопка по цвету фона (Flipkart = #FFB300 / #FB641B)
            try:
                handle = await page.evaluate_handle("""() => {
                    for (const el of document.querySelectorAll('button, a, div, [role="button"]')) {
                        const bg = window.getComputedStyle(el).backgroundColor;
                        const m  = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                        if (m) {
                            const [r, g, b] = [+m[1], +m[2], +m[3]];
                            if (r > 180 && g > 130 && b < 100) {
                                const rc = el.getBoundingClientRect();
                                if (rc.width > 60 && rc.height > 20) return el;
                            }
                        }
                    }
                    return null;
                }""")
                el = handle.as_element()
            except Exception:
                pass

        if el is None:
            # Кнопки нет — частая причина: в профиле вообще нет входа в аккаунт.
            if await _page_logged_out(page):
                return _NOT_LOGGED_IN_MSG
            return "Кнопка 'Buy now' не найдена на странице"
        try:
            await el.scroll_into_view_if_needed()
            await el.click(force=True)
        except Exception as e:
            return f"Ошибка клика по 'Buy now': {e}"

    # Ждём навигации после клика — URL или checkout-контент на странице
    _CHECKOUT_DOM = """() => {
        const url = location.href;
        if (url.includes('viewcheckout') || url.includes('payments') ||
                url.includes('changeShippingAddress') || url.includes('add/form'))
            return true;
        const text = (document.body && document.body.innerText || '').toLowerCase();
        return text.includes('delivery address') || text.includes('select delivery')
            || text.includes('order summary') || text.includes('place order')
            || text.includes('payment') || text.includes('add new address');
    }"""
    try:
        await page.wait_for_function(_CHECKOUT_DOM, timeout=20_000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # Успех ТОЛЬКО если реально дошли до checkout (URL из _SUCCESS_PARTS или
    # checkout-контент в DOM). Раньше успехом считался ЛЮБОЙ уход со страницы
    # (page.url != landed) — из-за этого редирект на главную/логин (слетевшая
    # сессия, недоступный товар, бот-защита) выдавался за успех, а дальше
    # вылезало немое «Не удалось перейти на оплату (URL: flipkart.com/)».
    async def _on_checkout() -> bool:
        # URL любого чекаута (в т.ч. новый формат /checkout/<hash>) или checkout-DOM.
        # Главная (flipkart.com/), товар (/p/itm…) и логин слова "checkout" не содержат.
        if any(s in page.url for s in _SUCCESS_PARTS) or "checkout" in page.url:
            return True
        try:
            return bool(await page.evaluate(_CHECKOUT_DOM))
        except Exception:
            return False

    if await _on_checkout():
        return None

    # Повторный клик — иногда первый клик попадает в неактивное состояние кнопки
    async def _second_click_buy() -> bool:
        ok = (
            await _try_click(page.locator(_BUY_CSS))
            or await _try_click(page.get_by_role("button", name=_re.compile(r"buy\s*now", _re.I)))
        )
        if not ok:
            el2 = await _buy_now_handle()
            if el2 is not None:
                with contextlib.suppress(Exception):
                    await el2.click(force=True)
                    ok = True
        if not ok:
            return False
        try:
            await page.wait_for_function(_CHECKOUT_DOM, timeout=15_000)
            await page.wait_for_timeout(500)
        except Exception:
            pass
        return await _on_checkout()

    if await _second_click_buy():
        return None

    # Не дошли до чекаута — обновляем страницу товара и жмём Buy Now снова
    # (браузер не закрываем, процесс не останавливаем до исчерпания раундов / Stop)
    for _rnd in range(1, _BUY_NOW_TO_CHECKOUT_ROUNDS):
        _ckcancel()
        if await _page_logged_out(page):
            return _NOT_LOGGED_IN_MSG
        print(
            f"  {Y}Buy Now не дал переход на оплату — обновляю страницу "
            f"и жму снова ({_rnd + 1}/{_BUY_NOW_TO_CHECKOUT_ROUNDS})…{RST}"
        )
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            with contextlib.suppress(Exception):
                await page.reload(wait_until="domcontentloaded", timeout=15_000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=6_000)
        await page.wait_for_timeout(600)
        if any(s in page.url for s in _SUCCESS_PARTS) or "checkout" in page.url:
            return None

        # снова ищем и кликаем Buy Now
        target = None
        for _s in range(20):
            target = await _buy_now_target()
            if target and target.get("inView"):
                break
            if target and not target.get("inView"):
                with contextlib.suppress(Exception):
                    await page.evaluate(
                        """(y) => window.scrollTo({top: Math.max(0, y - 200), behavior: 'instant'})""",
                        float(target["y"]) + await page.evaluate("() => window.scrollY"),
                    )
                await page.wait_for_timeout(300)
                target = await _buy_now_target()
                if target and target.get("inView"):
                    break
            await page.mouse.wheel(0, 520)
            await page.wait_for_timeout(250)

        clicked_r = False
        el_r = await _buy_now_handle()
        if el_r is not None:
            try:
                await el_r.scroll_into_view_if_needed()
                await el_r.click(timeout=5_000)
                clicked_r = True
            except Exception:
                with contextlib.suppress(Exception):
                    await el_r.click(force=True)
                    clicked_r = True
        if not clicked_r and target and target.get("x"):
            with contextlib.suppress(Exception):
                await page.mouse.click(float(target["x"]), float(target["y"]))
                clicked_r = True
        if not clicked_r:
            clicked_r = (
                await _try_click(page.locator(_BUY_CSS))
                or await _try_click(
                    page.get_by_role("button", name=_re.compile(r"buy\s*now", _re.I))
                )
            )
        if clicked_r:
            try:
                await page.wait_for_function(_CHECKOUT_DOM, timeout=18_000)
            except Exception:
                pass
            await page.wait_for_timeout(400)
            if await _on_checkout():
                print(f"  {G}✔ Buy Now → чекаут с попытки {_rnd + 1}{RST}")
                return None
            if await _second_click_buy():
                print(f"  {G}✔ Buy Now → чекаут (повторный клик, раунд {_rnd + 1}){RST}")
                return None

    # До checkout не дошли — диагностируем причину по итоговой странице
    if await _page_logged_out(page):
        return _NOT_LOGGED_IN_MSG
    _cur = page.url.split("?")[0].rstrip("/")
    if _cur in ("https://www.flipkart.com", "https://flipkart.com", "https://m.flipkart.com"):
        return ("Buy Now вернул на главную Flipkart — товар недоступен по этой "
                "ссылке, сессия слетела или сработала бот-защита "
                f"(после {_BUY_NOW_TO_CHECKOUT_ROUNDS} попыток)")
    return (
        f"Клик по 'Buy now' не дал перехода на оплату после "
        f"{_BUY_NOW_TO_CHECKOUT_ROUNDS} попыток (страница: {_cur[:60]})"
    )


_OOS_PHRASES = frozenset({
    "currently out of stock", "out of stock for",
    "not deliverable", "item is not deliverable",
})


async def _membership_oos_form_ready(page) -> bool:
    """Форма адреса доступна (URL или поле Pincode / Full Name)."""
    u = page.url or ""
    if "changeShippingAddress" in u or "add/form" in u:
        return True
    if "account/addresses" in u:
        with contextlib.suppress(Exception):
            if await page.locator(
                "input[placeholder*='Pincode' i], input[placeholder*='pincode' i], "
                "input[placeholder*='Full Name' i]"
            ).count() > 0:
                return True
    with contextlib.suppress(Exception):
        if await page.locator(
            "input[placeholder*='Pincode' i], input[placeholder*='pincode' i], "
            "input[placeholder*='Full Name' i], input[name*='pincode' i]"
        ).count() > 0:
            return True
    return False


async def _membership_open_address_editor(page) -> str:
    """Открыть смену адреса на OOS/viewcheckout. Возвращает способ: href|click|radio|pin|acc|''."""
    checkout_url = page.url or ""

    async def _ensure_add_form_on_addresses() -> bool:
        """На /account/addresses открыть Add New Address (реальный mouse + Edit)."""
        if "account/addresses" not in (page.url or ""):
            return False
        print(f"  {DIM}адрес: Manage Addresses — Add New…{RST}")
        # Дождаться полной прорисовки (сразу после редиректа кнопки ещё нет)
        with contextlib.suppress(Exception):
            await page.wait_for_selector(
                "text=/ADD\\s+A\\s+NEW\\s+ADDRESS/i", timeout=12_000,
            )
        await page.wait_for_timeout(600)

        async def _form_inputs() -> bool:
            if await _membership_oos_form_ready(page):
                return True
            with contextlib.suppress(Exception):
                n = await page.evaluate("""() => {
                    const inputs = [...document.querySelectorAll('input:not([type=hidden])')];
                    return inputs.filter(i => {
                        const r = i.getBoundingClientRect();
                        return r.width > 40 && r.height > 10;
                    }).length;
                }""")
                return n >= 3
            return False

        if await _form_inputs():
            return True

        # 1) Playwright / mouse по «ADD A NEW ADDRESS»
        for attempt in range(4):
            with contextlib.suppress(Exception):
                loc = page.get_by_text(
                    re.compile(r"ADD\s+A\s+NEW\s+ADDRESS", re.I)
                ).first
                if await loc.count() > 0:
                    await loc.scroll_into_view_if_needed()
                    box = await loc.bounding_box()
                    if box:
                        print(f"  {DIM}адрес: mouse ADD A NEW ADDRESS…{RST}")
                        await page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2,
                        )
                    else:
                        await loc.click(timeout=3000, force=True)
                    await page.wait_for_timeout(1500)
                    if await _form_inputs():
                        return True
            await asyncio.sleep(0.4)

        # 2) Edit первого адреса → смена пинкода
        with contextlib.suppress(Exception):
            edits = page.get_by_text(re.compile(r"^Edit$", re.I))
            if await edits.count() > 0:
                print(f"  {DIM}адрес: Edit существующего…{RST}")
                await edits.first.click(timeout=3000, force=True)
                await page.wait_for_timeout(1500)
                if await _form_inputs():
                    return True

        return await _form_inputs()


    async def _click_try_another() -> bool:
        with contextlib.suppress(Exception):
            box = await page.evaluate("""() => {
                for (const el of document.querySelectorAll(
                        'a, button, span, div, [role="button"]')) {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!/try\\s+another\\s+address/i.test(t) || t.length > 40) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) continue;
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2, t};
                }
                return null;
            }""")
            if box and box.get("x"):
                print(f"  {DIM}адрес: клик «{box.get('t')}»…{RST}")
                await page.mouse.click(float(box["x"]), float(box["y"]))
                await page.wait_for_timeout(2000)
                return True
        with contextlib.suppress(Exception):
            loc = page.get_by_text(re.compile(r"Try\s+Another\s+Address", re.I)).first
            if await loc.count() > 0 and await loc.is_visible():
                print(f"  {DIM}адрес: locator Try Another Address…{RST}")
                await loc.click(timeout=3000, force=True)
                await page.wait_for_timeout(2000)
                return True
        return False

    async def _click_checkout_change_only() -> bool:
        """Change внутри viewcheckout, без ссылок на /account/addresses."""
        boxes = await page.evaluate("""() => {
            const out = [];
            const badHref = (el) => {
                const a = el.closest('a');
                const h = (a && (a.getAttribute('href') || a.href)) || '';
                return /account\\/addresses/i.test(h);
            };
            const push = (el, t, score) => {
                if (badHref(el)) return;
                const r = el.getBoundingClientRect();
                if (r.width < 4 || r.height < 4 || r.bottom < 50 || r.top > innerHeight - 10)
                    return;
                out.push({score, x: r.x + r.width / 2, y: r.y + r.height / 2, t});
            };
            for (const el of document.querySelectorAll('a, button, span, div, [role="button"]')) {
                const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                const tl = t.toLowerCase();
                if (tl === 'change' || tl === 'edit' || tl === 'change address')
                    push(el, t, 10);
            }
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let n;
            while ((n = walker.nextNode())) {
                const raw = (n.textContent || '').trim();
                if (!/^change$/i.test(raw)) continue;
                const el = n.parentElement;
                if (!el || badHref(el)) continue;
                push(el, raw, 9);
            }
            out.sort((a, b) => b.score - a.score);
            const seen = new Set();
            const uniq = [];
            for (const b of out) {
                const k = Math.round(b.x) + ':' + Math.round(b.y);
                if (seen.has(k)) continue;
                seen.add(k);
                uniq.push(b);
            }
            return uniq.slice(0, 5);
        }""")
        for box in (boxes or []):
            if not box or not box.get("x"):
                continue
            print(f"  {DIM}адрес: клик checkout «{box.get('t', '?')}»…{RST}")
            await page.mouse.click(float(box["x"]), float(box["y"]))
            await page.wait_for_timeout(1600)
            u = page.url or ""
            if "account/addresses" in u:
                if await _ensure_add_form_on_addresses():
                    return True
                # Вернуться на чекаут
                with contextlib.suppress(Exception):
                    await page.go_back()
                    await page.wait_for_timeout(1200)
                continue
            if await _membership_oos_form_ready(page):
                return True
            with contextlib.suppress(Exception):
                add = await page.evaluate("""() => {
                    for (const el of document.querySelectorAll(
                            'a,button,div,span,[role="button"]')) {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        if (t === 'add new address' || t === 'add address'
                                || t === '+ add new address' || t === 'add a new address') {
                            el.click(); return true;
                        }
                    }
                    return false;
                }""")
                if add:
                    await page.wait_for_timeout(1200)
                    if await _membership_oos_form_ready(page):
                        return True
        return await _membership_oos_form_ready(page)

    # 0) Уже на Manage Addresses
    if "account/addresses" in (page.url or ""):
        if await _ensure_add_form_on_addresses():
            return "acc"

    # 1) href на changeShippingAddress (не account)
    with contextlib.suppress(Exception):
        href = await page.evaluate("""() => {
            for (const a of document.querySelectorAll('a[href]')) {
                const h = a.getAttribute('href') || '';
                if (h.includes('account/addresses')) continue;
                if (h.includes('changeShippingAddress') || h.includes('add/form')
                        || h.includes('shippingAddress'))
                    return a.href;
            }
            return '';
        }""")
        if href and href.startswith("http"):
            print(f"  {DIM}адрес: переход по href…{RST}")
            await page.goto(href, wait_until="domcontentloaded", timeout=25_000)
            await page.wait_for_timeout(1200)
            if await _membership_oos_form_ready(page):
                return "href"

    # 2) Try Another Address (OOS CTA)
    await _click_try_another()
    if await _membership_oos_form_ready(page):
        return "pin"
    # После Try Another — Add New / radio в drawer
    with contextlib.suppress(Exception):
        await page.evaluate("""() => {
            for (const el of document.querySelectorAll('a,button,div,span,[role="button"]')) {
                const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                if (t === 'add new address' || t === 'add address' || t === '+ add new address') {
                    el.click(); return;
                }
            }
        }""")
        await page.wait_for_timeout(1000)
    if await _membership_oos_form_ready(page):
        return "pin"

    # 3) Change только checkout (без account links)
    if await _click_checkout_change_only():
        u = page.url or ""
        return "acc" if "account/addresses" in u else (
            "pin" if "changeShippingAddress" not in u else "href"
        )

    # 4) Fallback: намеренно Manage Addresses → Add New
    if "viewcheckout" in checkout_url or "checkout" in checkout_url:
        with contextlib.suppress(Exception):
            go_acc = await page.evaluate("""() => {
                for (const a of document.querySelectorAll('a[href*="account/addresses"]')) {
                    a.click(); return true;
                }
                for (const el of document.querySelectorAll('a,button,div,span')) {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (t === 'change' || t.includes('deliver to')) {
                        const a = el.closest('a');
                        if (a && /account\\/addresses/i.test(a.href || '')) {
                            a.click(); return true;
                        }
                    }
                }
                return false;
            }""")
            if go_acc:
                await page.wait_for_timeout(2000)
                if await _ensure_add_form_on_addresses():
                    return "acc"

    # 5) Radio / Deliver Here
    for _ in range(10):
        if await _membership_oos_form_ready(page):
            return "pin"
        with contextlib.suppress(Exception):
            action = await page.evaluate("""() => {
                const radios = [...document.querySelectorAll('input[type=radio]')];
                if (radios.length >= 2) {
                    const cur = radios.findIndex(r => r.checked);
                    radios[(cur + 1) % radios.length].click();
                    return 'radio';
                }
                for (const el of document.querySelectorAll('a,button,div,span,[role="button"]')) {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (t === 'add new address' || t === 'add address') { el.click(); return 'add'; }
                    if (t === 'deliver here' || t === 'save and deliver here') {
                        el.click(); return 'deliver';
                    }
                }
                return '';
            }""")
            if action == "radio":
                await page.wait_for_timeout(800)
                with contextlib.suppress(Exception):
                    await page.evaluate("""() => {
                        for (const el of document.querySelectorAll(
                                'button, div, a, span, [role="button"]')) {
                            const t = (el.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                            if (t === 'deliver here' || t === 'continue') {
                                el.click(); return;
                            }
                        }
                    }""")
                await page.wait_for_timeout(1200)
                return "radio"
            if action == "add":
                await page.wait_for_timeout(1000)
            elif action == "deliver":
                return "radio"
        await asyncio.sleep(0.3)

    return "acc" if await _membership_oos_form_ready(page) else ""



async def _membership_recover_oos(page, ctx, *, attempts: int = 3,
                                  phone: str = "") -> tuple[bool, str]:
    """OOS / not deliverable на viewcheckout → Change / Try Another Address → новый пинкод.

    До `attempts` попыток с разными городами. `phone` — 10 цифр профиля для Manage Addresses.
    Возвращает (ok, addr_msg).
    """
    addr_msg = ""
    phone10 = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
    for i in range(1, attempts + 1):
        print(f"  {Y}⚠ OOS/доставка — смена адреса ({i}/{attempts})…{RST}")
        how = await _membership_open_address_editor(page)
        if how == "radio":
            await page.wait_for_timeout(1000)
            try:
                body = (await page.evaluate(
                    "() => (document.body?.textContent || '').toLowerCase()"))
                if not any(p in body for p in _OOS_PHRASES):
                    print(f"  {G}✔ OOS снят другим сохранённым адресом{RST}")
                    return True, addr_msg or "Адрес: сохранённый (radio)"
            except Exception:
                pass
            if not await _membership_oos_form_ready(page):
                how = await _membership_open_address_editor(page)

        if not await _membership_oos_form_ready(page):
            with contextlib.suppress(Exception):
                dump = await page.evaluate("""() => {
                    const out = [];
                    for (const el of document.querySelectorAll('a,button,span,div')) {
                        const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (!t || t.length > 60) continue;
                        if (/change|address|deliver|edit|pincode/i.test(t))
                            out.push(t.slice(0, 50));
                        if (out.length >= 12) break;
                    }
                    return {url: location.href.slice(0, 120), texts: out};
                }""")
                print(f"  {DIM}форма адреса не открылась ({how or '—'}) "
                      f"url={dump.get('url','')[:80]} "
                      f"cands={dump.get('texts')}{RST}")
            with contextlib.suppress(Exception):
                _dbg = Path("debug")
                _dbg.mkdir(exist_ok=True)
                await page.screenshot(path=str(_dbg / f"oos_addr_{i}.png"), full_page=False)
            continue

        a = _gen_indian_address()
        prefer = _IND_PINCODES[(i * 11 + 3) % len(_IND_PINCODES)]
        a["pincode"], a["city"], a["state"] = prefer[0], prefer[1], prefer[2]
        if phone10:
            a["phone"] = phone10
        a["locality"] = random.choice(_IND_AREAS)
        a["address_line"] = f"{a['house']}, {a['road']}"
        lat, lon = _CITY_COORDS.get(a["city"], (20.5937, 78.9629))
        with contextlib.suppress(Exception):
            await ctx.set_geolocation({"latitude": lat, "longitude": lon})
        print(f"  {DIM}новый адрес: {a['pincode']} {a['city']} phone={phone10 or '—'} ({how}){RST}")
        if not await _fill_address_form(page, a):
            print(f"  {Y}⚠ Save Address не сработал{RST}")
            continue
        addr_msg = f"Адрес: {a['name']} | {a['pincode']} {a['city']}"
        # После Manage Addresses — назад на чекаут
        if "account/addresses" in (page.url or ""):
            print(f"  {DIM}адрес сохранён — возврат на viewcheckout…{RST}")
            with contextlib.suppress(Exception):
                await page.go_back()
                await page.wait_for_timeout(1500)
            if "viewcheckout" not in (page.url or "") and "checkout" not in (page.url or ""):
                with contextlib.suppress(Exception):
                    await page.go_back()
                    await page.wait_for_timeout(1500)
            if "viewcheckout" not in (page.url or ""):
                with contextlib.suppress(Exception):
                    await page.goto(
                        "https://www.flipkart.com/viewcheckout?loginFlow=false",
                        wait_until="domcontentloaded", timeout=25_000,
                    )
                    await page.wait_for_timeout(1500)
        try:
            await page.wait_for_url(
                re.compile(r"viewcheckout|checkout|payments"), timeout=18_000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(1200)
        try:
            body = (await page.evaluate(
                "() => (document.body?.textContent || '').toLowerCase()"))
            if not any(p in body for p in _OOS_PHRASES):
                print(f"  {G}✔ OOS снят новым адресом ({a['city']}){RST}")
                return True, addr_msg
            print(f"  {Y}⚠ после адреса всё ещё OOS — следующая попытка{RST}")
        except Exception:
            pass
    return False, addr_msg



def _random_gmail() -> str:
    """Генерирует случайный реалистичный email @gmail.com."""
    first = random.choice([
        "rahul", "amit", "priya", "sunita", "vikram", "anita", "rajesh", "pooja",
        "suresh", "neha", "arun", "kavita", "manish", "ritu", "sanjay", "deepa",
        "akash", "shruti", "rohit", "swati", "vikas", "anjali", "manoj", "nisha",
        "arjun", "sneha", "kiran", "mohan", "geeta", "ramesh", "lalita", "dinesh",
    ])
    last = random.choice([
        "kumar", "sharma", "singh", "gupta", "verma", "patel", "mehta", "joshi",
        "yadav", "mishra", "pandey", "dubey", "tiwari", "chauhan", "nair", "reddy",
        "iyer", "bose", "das", "roy", "chopra", "malhotra", "saxena", "kapoor",
    ])
    sep = random.choice([".", "_", ""])
    num = random.randint(10, 9999)
    pos = random.randint(0, 2)
    if pos == 0:
        user = f"{first}{sep}{last}{num}"
    elif pos == 1:
        user = f"{first}{num}{sep}{last}"
    else:
        user = f"{first}{sep}{last}"
    return f"{user}@gmail.com"


# Если задан — используется вместо случайного gmail при оплате (для конкретного покупателя)
_override_email: str = ""
# Email с последнего fill на чекауте — per-task/thread ContextVar (не общий global)
import contextvars as _contextvars_email
_cv_filled_email: _contextvars_email.ContextVar[str] = _contextvars_email.ContextVar(
    "subhub_filled_email", default=""
)


def _set_filled_email(email: str) -> None:
    _cv_filled_email.set((email or "").strip())


def _get_filled_email() -> str:
    return (_cv_filled_email.get() or "").strip()


def _clear_filled_email() -> None:
    _cv_filled_email.set("")


def _to_gmail(email: str) -> str:
    """Приводит почту к @gmail.com: берёт имя (часть до @) и подставляет
    @gmail.com вместо любого другого домена. Если домен уже gmail.com — без
    изменений. Нужно, чтобы в профиль всегда вводился gmail, даже если
    покупатель в GGSell указал почту на другом домене. Возвращает '' если
    имя выделить не удалось (тогда вызывающий берёт случайный gmail)."""
    local = (email or "").strip().split("@", 1)[0].strip()
    if not local:
        return ""
    return f"{local}@gmail.com"

# Немедленная отмена выполнения заказа: бот ставит флаг, покупка/заполнение его
# проверяют в долгих ожиданиях/циклах и сразу прерываются (с закрытием браузера).
import threading as _threading_pc
_purchase_cancel = _threading_pc.Event()

# Этап текущего сценария по профилю (phone10 → текст) — GUI читает для блока «В процессе».
_profile_op_stages: dict[str, str] = {}


def _profile_stage_key(phone_or_path) -> str:
    if phone_or_path is None:
        return ""
    with contextlib.suppress(Exception):
        ph = _phone_from_path(Path(phone_or_path))
        if ph:
            digits = "".join(c for c in ph if c.isdigit())
            return digits[-10:] if len(digits) >= 10 else digits
    digits = "".join(c for c in str(phone_or_path) if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else (digits or str(phone_or_path))


def set_profile_op_stage(phone_or_path, stage: str) -> None:
    """Пишет этап сценария для GUI (пустая строка — сброс)."""
    key = _profile_stage_key(phone_or_path)
    if not key:
        return
    with _app_lock:
        if stage:
            _profile_op_stages[key] = stage
        else:
            _profile_op_stages.pop(key, None)


def get_profile_op_stage(phone: str) -> str:
    key = _profile_stage_key(phone)
    if not key:
        return ""
    with _app_lock:
        return _profile_op_stages.get(key, "")


def stop_profile_op(profile_path) -> int:
    """Стоп сценария по профилю: флаг отмены + kill Chrome этого профиля."""
    _purchase_cancel.set()
    killed = 0
    with contextlib.suppress(Exception):
        killed = int(_kill_chrome_for_profile(profile_path) or 0)
    set_profile_op_stage(profile_path, "Остановка…")
    return killed


# Реестр профилей, по которым ПРЯМО СЕЙЧАС идёт покупка/заполнение (path → refcount).
# Нужен чтобы кнопка «Остановить» могла мгновенно убить Chrome активной операции:
# кооперативный флаг не прерывает долгие await Playwright (ожидание 3DS/OTP/навигации),
# а убийство браузера роняет их сразу. Заполняется декоратором _serialize_purchase.
_active_purchase_profiles: dict = {}
_app_lock = _threading_pc.Lock()

def _register_purchase_profile(pp) -> None:
    if pp is None:
        return
    # Ключ resolve() — совпадение с kill по пути (относительный vs absolute).
    k = str(Path(pp).resolve())
    with _app_lock:
        _active_purchase_profiles[k] = _active_purchase_profiles.get(k, 0) + 1

def _unregister_purchase_profile(pp) -> None:
    if pp is None:
        return
    k = str(Path(pp).resolve())
    with _app_lock:
        n = _active_purchase_profiles.get(k, 0) - 1
        if n <= 0:
            _active_purchase_profiles.pop(k, None)
        else:
            _active_purchase_profiles[k] = n

def _stop_active_purchases() -> int:
    """Мгновенно прерывает текущие покупки/заполнения: ставит флаг отмены и убивает
    Chrome всех активных профилей, чтобы зависшие await Playwright сразу упали.
    Возвращает число убитых Chrome-процессов. Блокирующая (psutil) — звать в потоке."""
    _purchase_cancel.set()
    with _app_lock:
        paths = list(_active_purchase_profiles.keys())
    killed = 0
    for p in paths:
        try:
            k = _kill_chrome_for_profile(p)
            if isinstance(k, int) and k > 0:
                killed += k
        except Exception:
            pass
    return killed


def disconnect_vpn_on_shutdown() -> int:
    """Выход из приложения / конец сценария: остановить Flipkart-run и закрыть Chrome с VPN."""
    killed = _stop_active_purchases()
    with _app_lock:
        _active_purchase_profiles.clear()
    # Иначе флаг залипает после Run/Stop и Profiles «До оплаты»/«Купить» сразу CANCELLED
    _purchase_cancel.clear()
    if killed:
        print(f"  {DIM}VPN: закрыто Chrome-сессий: {killed}{RST}")
    return killed

# Переключение карты во время ожидания 3DS OTP (TG-кнопка → бот устанавливает флаг)
_switch_card_choice: list = [-1]  # [0] — позиция карты в _ordered_pay
_switch_card_ev = _threading_pc.Event()
_3ds_card_options: list = []      # список {"pos": int, "card": dict} для TG-кнопок

# Подтверждение при найденных дублях заказов (TG Да/Нет → menu.py ждёт ответа)
_orders_confirm_ev = _threading_pc.Event()
_orders_confirm_choice: list = [None]  # True=продолжить, False=удалить профиль

# Подтверждение использования «крупных» гифт-карт (>= GIFT_CONFIRM_THRESHOLD).
# Если мелких (50/100/200/250) не хватает — бот спрашивает разрешение через TG.
GIFT_CONFIRM_THRESHOLD = 500
_gift_big_ev = _threading_pc.Event()
_gift_big_choice: list = [None]  # True=разрешить крупные, False=нет


class _PurchaseCancelled(Exception):
    """Выполнение прервано пользователем."""
    pass


def _ckcancel() -> None:
    if _purchase_cancel.is_set():
        raise _PurchaseCancelled()


async def _cancellable_wait(page, ms: int) -> None:
    """page.wait_for_timeout с проверкой отмены каждые 0.5с."""
    _waited = 0
    while _waited < ms:
        _ckcancel()
        _step = min(500, ms - _waited)
        try:
            await page.wait_for_timeout(_step)
        except Exception:
            await asyncio.sleep(_step / 1000)
        _waited += _step


async def _fill_email_input(page) -> bool:
    """
    Находит email-input на странице (уже видимый), заполняет email покупателя (если задан)
    или случайным @gmail.com, нажимает Save/Submit/Continue. Возвращает True если заполнен.
    """
    email_input = page.locator(
        "input[type='email'], input[placeholder*='email' i], input[placeholder*='Email' i]"
    )
    if await email_input.count() == 0:
        return False
    email = (_to_gmail(_override_email) or _random_gmail()) if _override_email else _random_gmail()
    inp = email_input.first
    await inp.scroll_into_view_if_needed()
    await _human_click(page, inp)
    # Очищаем поле, если Flipkart его уже предзаполнил (например, сохранённой
    # ранее почтой покупателя на не-gmail домене) — иначе ввод пойдёт «поверх».
    try:
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(100)
    except Exception:
        pass
    await _human_type(page, email)
    await page.wait_for_timeout(300)
    # Сохраняем через Enter (надёжнее, чем искать кнопку Save)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(600)
    # Доп. попытка через JS-клик Save/Submit (кнопка может быть DIV)
    try:
        await page.evaluate("""() => {
            const kw = ['save', 'submit', 'done', 'ok'];
            for (const el of document.querySelectorAll('div, button, a, span')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!kw.includes(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width >= 30 && r.height >= 10) { el.click(); break; }
            }
        }""")
    except Exception:
        pass
    await page.wait_for_timeout(500)
    print(f"  Email: {email}")
    _set_filled_email(email)
    return True


async def _handle_email_on_page(page) -> bool:
    """
    Обрабатывает email на viewcheckout/payments:
    — если уже есть input → заполняем сразу
    — если есть "Add Email" (может быть DIV) → JS-клик, ждём input, заполняем.
    Возвращает True если email был обработан.
    """
    # Проверяем есть ли уже видимый input
    if await _fill_email_input(page):
        return True

    # Кликаем "Add Email" через JS (DIV, span, a — любой тег)
    try:
        clicked = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t !== 'add email') continue;
                const r = el.getBoundingClientRect();
                if (r.width > 5 && r.width < 300 && r.height > 5) {
                    el.click(); return true;
                }
            }
            return false;
        }""")
    except Exception:
        clicked = False

    if clicked:
        # Ждём появления email input (до 3с)
        for _ in range(6):
            await page.wait_for_timeout(500)
            if await _fill_email_input(page):
                return True

    return False


async def _handle_email_popup(page) -> bool:
    """Обратная совместимость — вызывает _handle_email_on_page."""
    return await _handle_email_on_page(page)


async def _human_click(page, element, *, before: float = 0.0) -> None:
    """Click with human-like mouse arc — moves near element first, then to it."""
    import random as _r
    try:
        if before > 0:
            await asyncio.sleep(before)
        box = await element.bounding_box()
        if box:
            cx = box["x"] + box["width"]  * _r.uniform(0.3, 0.7)
            cy = box["y"] + box["height"] * _r.uniform(0.3, 0.7)
            # Промежуточная точка — симуляция дуги движения мыши
            await page.mouse.move(cx + _r.uniform(-60, 60), cy + _r.uniform(-30, 30))
            await asyncio.sleep(_r.uniform(0.04, 0.10))
            await page.mouse.move(cx, cy)
            await asyncio.sleep(_r.uniform(0.02, 0.06))
            await page.mouse.click(cx, cy)
        else:
            await element.click()
    except Exception:
        try:
            await element.click()
        except Exception:
            pass


async def _human_type(page, text: str, *, clear: bool = True) -> None:
    """Type text with per-character random delay (25–90ms) to mimic human typing."""
    import random as _r
    if clear:
        await page.keyboard.press("Control+a")
        await asyncio.sleep(0.05)
    for ch in text:
        await page.keyboard.type(ch)
        await asyncio.sleep(_r.uniform(0.025, 0.09))


async def _fill_billing_address_popup(page, card: dict) -> bool:
    """
    Заполняет попап «Add Address» который появляется после ввода карты:
      Country (dropdown) → Zipcode → State → City → Billing address → Pay ₹XXX.
    Возвращает True если попап был найден и заполнен.
    """
    import random as _r

    # Ждём попап (до 5 сек) — ищем по заголовку модала или наличию Country-поля
    try:
        await page.wait_for_selector(
            "input[placeholder*='Zipcode' i], input[placeholder*='Zip' i], "
            "select[name*='country' i], div[class*='modal' i] input",
            state="visible", timeout=5_000,
        )
    except Exception:
        return False  # попапа нет — ничего делать не надо

    await page.wait_for_timeout(_r.uniform(300, 600))

    # ── 1. Country dropdown ───────────────────────────────────────────────────
    country = card.get("country", "USA").strip()
    country_aliases = [country, "United States", "United States of America", "US"]

    # Сначала пробуем нативный <select>
    country_select = page.locator("select[name*='country' i], select[id*='country' i]").first
    if await country_select.count() > 0:
        for alias in country_aliases:
            try:
                await country_select.select_option(label=alias)
                break
            except Exception:
                continue
    else:
        # Кастомный dropdown (DIV/SPAN) — открываем через mouse.click по координатам
        bbox = None
        try:
            bbox = await page.evaluate("""() => {
                // Ищем первый видимый элемент с текстом "Select" (триггер дропдауна Country)
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t !== 'Select') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= 60 && r.height >= 15) return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
        except Exception:
            pass
        if bbox:
            await page.mouse.click(bbox["x"], bbox["y"])
            await page.wait_for_timeout(700)
            # Ищем и кликаем нужную страну в открытом дропдауне
            for alias in country_aliases:
                try:
                    opt_bbox = await page.evaluate(f"""() => {{
                        for (const el of document.querySelectorAll('*')) {{
                            const t = (el.innerText || el.textContent || '').trim();
                            if (t !== {repr(alias)}) continue;
                            const r = el.getBoundingClientRect();
                            if (r.width >= 30 && r.height >= 10) return {{x: r.x + r.width/2, y: r.y + r.height/2}};
                        }}
                        return null;
                    }}""")
                    if opt_bbox:
                        await page.mouse.click(opt_bbox["x"], opt_bbox["y"])
                        await page.wait_for_timeout(400)
                        break
                except Exception:
                    continue
    await page.wait_for_timeout(300)

    # ── 2. Zipcode ────────────────────────────────────────────────────────────
    zip_inp = page.locator(
        "input[placeholder*='Zipcode' i], input[placeholder*='Zip' i], "
        "input[placeholder*='Postal' i], input[name*='zip' i]"
    ).first
    if await zip_inp.count() > 0:
        await _human_click(page, zip_inp, before=_r.uniform(0.1, 0.2))
        for ch in card.get("zipcode", ""):
            await page.keyboard.type(ch)
            await asyncio.sleep(_r.uniform(0.05, 0.11))
        await page.wait_for_timeout(300)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(_r.randint(200, 400))

    # ── 3. State ──────────────────────────────────────────────────────────────
    state_inp = page.locator(
        "input[placeholder*='State' i], input[name*='state' i]"
    ).first
    if await state_inp.count() > 0:
        await _human_click(page, state_inp, before=_r.uniform(0.1, 0.2))
        for ch in card.get("state", ""):
            await page.keyboard.type(ch)
            await asyncio.sleep(_r.uniform(0.04, 0.09))
        await page.wait_for_timeout(250)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(_r.randint(150, 350))

    # ── 4. City ───────────────────────────────────────────────────────────────
    city_inp = page.locator(
        "input[placeholder*='City' i], input[name*='city' i]"
    ).first
    if await city_inp.count() > 0:
        await _human_click(page, city_inp, before=_r.uniform(0.1, 0.2))
        for ch in card.get("city", ""):
            await page.keyboard.type(ch)
            await asyncio.sleep(_r.uniform(0.04, 0.09))
        await page.wait_for_timeout(250)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(_r.randint(150, 350))

    # ── 5. Billing address ────────────────────────────────────────────────────
    addr_inp = page.locator(
        "textarea[placeholder*='Address' i], input[placeholder*='Address' i], "
        "textarea[placeholder*='address' i]"
    ).first
    if await addr_inp.count() > 0:
        await _human_click(page, addr_inp, before=_r.uniform(0.1, 0.2))
        for ch in card.get("address", ""):
            await page.keyboard.type(ch)
            await asyncio.sleep(_r.uniform(0.03, 0.08))
        await page.wait_for_timeout(350)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(_r.randint(300, 550))

    # ── 6. Pay ₹XXX (retry при красных ошибках валидации) ────────────────────
    _BILL_RED_JS = """() => {
        for (const el of document.querySelectorAll('div,span,p,label')) {
            const txt = (el.innerText || '').trim();
            if (!txt || txt.length > 120) continue;
            const m = window.getComputedStyle(el).color
                .match(/rgb[a]?\\(\\s*(\\d+),\\s*(\\d+),\\s*(\\d+)/);
            if (m && +m[1] > 150 && +m[2] < 80 && +m[3] < 80) return txt;
        }
        return '';
    }"""
    pay_btn = page.locator(
        "button:has-text('Pay ₹'), button:has-text('PAY'), button:has-text('Pay')"
    ).last
    if await pay_btn.count() > 0:
        btn_text = (await pay_btn.inner_text()).strip()
        print(f"  Адрес заполнен, нажимаю «{btn_text}»")
        for _pay_try in range(3):
            await _human_click(page, pay_btn, before=_r.uniform(0.3, 0.5))
            await page.wait_for_timeout(2_000)
            red_err = ""
            try:
                red_err = await page.evaluate(_BILL_RED_JS)
            except Exception:
                pass
            if not red_err:
                break
            print(f"  {Y}Billing: ошибка ({red_err!r}) — повтор {_pay_try+1}/3...{RST}")
            await page.wait_for_timeout(500)

    # ── 7. Paytm — выбор валюты ──────────────────────────────────────────────
    gw_result = await _handle_paytm_currency_page(page)

    if gw_result == "declined":
        return "declined"
    if gw_result == "otp_required":
        return "otp_required"  # карта принята, нужен OTP
    if gw_result == "otp_timeout":
        return "otp_timeout"  # время ожидания OTP истекло — оплата не прошла
    if gw_result is False:
        return "declined"  # Pay INR button not found → treat as decline, retry
    return True


async def _handle_paytm_currency_page(page) -> bool:
    """
    Обрабатывает страницу выбора валюты (Paytm / PayGlocal / любой шлюз).
    Всегда выбирает INR / INDIAN RUPEE → нажимает кнопку Pay INR → ждёт 3DS.
    Работает через mouse.click по координатам (элементы — кастомные React-компоненты).
    """
    import random as _r

    _PAYMENT_DOMAINS = ("paytmpayments.com", "payglocal.in", "payglocal.com",
                        "razorpay.com", "ccavenue.com", "billdesk.com",
                        "juspay.in", "payu.in", "payu.com")

    # Ждём перехода на платёжный шлюз (страница ИЛИ iframe, до 15 сек)
    def _all_frame_urls():
        try:
            return [page.url] + [f.url for f in page.frames if f.url]
        except Exception:
            return [page.url]

    _deadline = 15
    _found_gw = False
    for _wt in range(_deadline * 2):
        if any(d in u for u in _all_frame_urls() for d in _PAYMENT_DOMAINS):
            _found_gw = True
            break
        await asyncio.sleep(0.5)
    if not _found_gw:
        return False

    await page.wait_for_timeout(_r.uniform(700, 1_200))

    # Определяем домен шлюза (может быть iframe, а не page.url)
    _gw_url = page.url
    for _fu in _all_frame_urls():
        if any(d in _fu for d in _PAYMENT_DOMAINS):
            _gw_url = _fu
            break
    print(f"  Платёжный шлюз: {_gw_url.split('/')[2] if '/' in _gw_url else _gw_url} — выбираю INR...")

    # Ждём появления опций валюты (INR / Indian Rupee / Pay ₹)
    try:
        await page.wait_for_function("""() => {
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || el.textContent || '').trim().toUpperCase();
                if (t.includes('INDIAN RUPEE') || t.includes('INR') || t.includes('₹')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 30) return true;
                }
            }
            return false;
        }""", timeout=12_000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # Закрываем браузерные попапы (Сохранить адрес? и т.д.) нажатием Escape
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass

    # PayU MCP: если виден «Change Currency» — кликаем, чтобы раскрыть INR опции
    try:
        _cc_btn = await page.evaluate("""() => {
            for (const el of document.querySelectorAll(
                    'button,[role="button"],a,div,span,p')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t !== 'change currency') continue;
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 8) continue;
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }
            return null;
        }""")
        if _cc_btn:
            print("  PayU MCP: кликаю «Change Currency»...")
            await page.mouse.move(_cc_btn["x"], _cc_btn["y"])
            await page.wait_for_timeout(400)
            await page.mouse.click(_cc_btn["x"], _cc_btn["y"])
            # Ждём появления INR опции (до 8 сек) после раскрытия меню валют
            try:
                await page.wait_for_function("""() => {
                    for (const el of document.querySelectorAll('*')) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if ((t.includes('indian rupee') || t === 'inr') && t.length < 60) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 20 && r.height > 8) return true;
                        }
                    }
                    return false;
                }""", timeout=8_000)
            except Exception:
                pass
            await page.wait_for_timeout(600)
    except Exception:
        pass

    # PayGlocal: ждём пока кнопка «Pay ₹» покажет реальную сумму (≥100)
    # Ищем во всех фреймах, т.к. UI может быть в iframe.
    if "payglocal" in _gw_url:
        _pg_ready = False
        for _wfr in range(24):  # до 12 секунд
            for _pfr in [page.main_frame] + list(page.frames):
                try:
                    _pg_ready = await _pfr.evaluate("""() => {
                        for (const el of document.querySelectorAll('*')) {
                            const raw = (el.innerText || '').trim();
                            const lines = raw.split('\\n').slice(0, 4).map(l => l.trim()).filter(l => l);
                            const tl = lines.join(' ').replace(/\\s+/g, ' ').toLowerCase();
                            if (!tl.startsWith('pay') || (!tl.includes('₹') && !tl.includes('inr'))) continue;
                            const m = tl.match(/₹\\s*([\\d,]+)/);
                            if (m && parseInt(m[1].replace(/,/g, '')) >= 100) return true;
                            // Сумма в отдельном элементе: ищем div с "1,499" рядом с кнопкой Pay
                            if (tl.includes('inr') && !tl.includes('sgd')) return true;
                        }
                        return false;
                    }""")
                    if _pg_ready:
                        break
                except Exception:
                    pass
            if _pg_ready:
                break
            await asyncio.sleep(0.5)
        await page.wait_for_timeout(600)

    import re as _re_pay

    # JS для поиска Pay-кнопки с INR.
    # Приоритет: кнопка с точным текстом "Pay ₹XXX INR" → затем любая Pay+INR кнопка.
    # НЕ кликаем по строке/строке с курсом — только по кнопке оплаты.
    _PAY_INR_JS = """() => {
        // 1. Ищем кнопку/ссылку где текст начинается с "Pay" И содержит "INR"
        //    Это именно кнопка "Pay ₹343.00 INR" или "Pay ₹1,499.00 INR"
        const candidates = [];
        for (const el of document.querySelectorAll(
                'button, a, [role="button"], div[class*="btn"], div[class*="pay"]')) {
            const raw = (el.innerText || '').trim();
            // Берём первые 4 строки (как в _VIS_PAY_JS — на случай встроенного нумпада)
            const lines = raw.split('\\n').slice(0, 4).map(l => l.trim()).filter(l => l);
            const t = lines.join(' ').replace(/\\s+/g, ' ').toLowerCase();
            if (!t.startsWith('pay')) continue;
            if (!t.includes('inr') && !t.includes('₹')) continue;
            if (t.includes('apple') || t.includes('google') || t.includes('sgd') || t.includes('usd')) continue;
            // Не пропускаем amount=0 — может быть numpad-структура кнопки PayGlocal
            const r = el.getBoundingClientRect();
            if (r.width < 40 || r.height < 12) continue;
            const s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') continue;
            candidates.push({x: r.x + r.width/2, y: r.y + r.height/2, t: t.substring(0, 50),
                             area: r.width * r.height});
        }
        if (candidates.length > 0) {
            // Берём кнопку с наибольшей площадью (но не слишком большой — не вся секция)
            candidates.sort((a,b) => a.area - b.area);
            // Предпочитаем среднего размера кнопку (не самую маленькую, не огромную секцию)
            const btn = candidates.find(c => c.area < 50000) || candidates[0];
            return btn;
        }
        // 2. Fallback: ищем в любых элементах (включая span/div)
        let best = null, bestArea = -Infinity;
        for (const el of document.querySelectorAll('*')) {
            const raw = (el.innerText || el.textContent || '').trim();
            const lines = raw.split('\\n').slice(0, 4).map(l => l.trim()).filter(l => l);
            const t = lines.join(' ').replace(/\\s+/g, ' ').toLowerCase();
            if (!t.startsWith('pay')) continue;
            if (!t.includes('inr') && !t.includes('₹')) continue;
            if (t.includes('apple') || t.includes('google') || t.includes('sgd') ||
                t.includes('usd') || t.includes('eur')) continue;
            // Не пропускаем amount=0 — может быть numpad-структура PayGlocal
            const r = el.getBoundingClientRect();
            if (r.width < 40 || r.height < 12 || r.width * r.height > 80000) continue;
            const s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') continue;
            const area = r.width * r.height;
            if (area > bestArea) {
                best = {x: r.x + r.width/2, y: r.y + r.height/2, t: t.substring(0, 50)};
                bestArea = area;
            }
        }
        return best;
    }"""

    # 3DS-домены + хелпер (определяем ДО цикла, чтобы не было UnboundLocalError)
    _3DS_DOMS = ("cardinalcommerce.com", "3dsecure", "verify.visa.com",
                 "mastercard.com/ac", "payvision.com", "acspage", "cruise",
                 "hitrust.com", "acs-auth", "challenge/brw", "threeDSecure", "threedsecure")

    def _is_3ds_page(url: str) -> bool:
        return (any(d in url for d in _3DS_DOMS)
                or "StepUp" in url or "stepup" in url.lower()
                or "3ds" in url.lower())

    def _has_otp_text(body: str) -> bool:
        b = body.lower()
        return ("otp" in b or "one-time" in b or "verification code" in b
                or "authentication" in b or "please get" in b
                or "enter the code" in b or "enter code" in b
                or "security code" in b or "passcode" in b
                or "sent to your" in b or "sent to mobile" in b)

    async def _page_all_text() -> str:
        parts = []
        try:
            parts.append(await page.evaluate("() => document.body.innerText || ''"))
        except Exception:
            pass
        for fr in page.frames:
            try:
                parts.append(await fr.evaluate("() => document.body.innerText || ''"))
            except Exception:
                pass
        return " ".join(parts)

    async def _has_otp_input() -> bool:
        _otp_inp_js = """() => {
            const sels = [
                'input[name*="otp" i]', 'input[name*="code" i]',
                'input[placeholder*="otp" i]', 'input[placeholder*="code" i]',
                'input[type="tel"]', 'input[type="number"][maxlength]',
                'input[maxlength="6"]', 'input[maxlength="4"]',
                'input[type="password"]',
            ];
            if (sels.some(s => { const el = document.querySelector(s); return el && el.offsetParent !== null; }))
                return true;
            // UQPAY / hitrust: любой видимый незаблокированный текстовый input
            if (location.href.includes('hitrust.com') || location.href.includes('acs-auth')) {
                for (const inp of document.querySelectorAll('input[type="text"], input:not([type])')) {
                    if (inp.offsetParent !== null && !inp.readOnly && !inp.disabled) return true;
                }
            }
            return false;
        }"""
        for fr in [page] + list(page.frames):
            try:
                if await fr.evaluate(_otp_inp_js):
                    return True
            except Exception:
                pass
        return False

    # Внешний цикл: до 3 попыток (на случай отклонения платежа и возврата)
    pay_clicked = False
    for pay_attempt in range(3):
        if pay_attempt > 0:
            # После отклонения ждём появления элементов нового экрана
            try:
                await page.wait_for_function("""() => {
                    for (const el of document.querySelectorAll('*')) {
                        const t = (el.innerText || el.textContent || '').trim().toUpperCase();
                        if (t.includes('INDIAN RUPEE') || t.includes('INR') || t.includes('₹'))
                            return true;
                    }
                    return false;
                }""", timeout=8_000)
            except Exception:
                pass

        # JS для поиска ВИДИМОЙ кнопки Pay INR/₹
        # PayGlocal: кнопка структурирована как "Pay\n₹343.00\nINR\n<numpad digits>"
        # Первые 4 строки = "Pay ₹343.00 INR" (без нумпада).
        _VIS_PAY_JS = """() => {
            for (const el of document.querySelectorAll('*')) {
                const raw = (el.innerText || '').trim();
                if (!raw) continue;
                // Берём первые 4 строки — достаточно для "Pay ₹343.00 INR", исключает нумпад
                const lines = raw.split('\\n').slice(0, 4).map(l => l.trim()).filter(l => l);
                const combined = lines.join(' ').replace(/\\s+/g, ' ');
                const tl = combined.toLowerCase();
                const firstLine = lines[0] || '';

                if (!firstLine.toLowerCase().startsWith('pay')) continue;
                if (tl.includes('apple') || tl.includes('google')) continue;

                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 15) continue;
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                if (parseFloat(s.opacity || '1') < 0.1) continue;

                // Текст содержит INR/₹ (строки 1-4 вместе) — INR-кнопка
                const hasINR = tl.includes('inr') || tl.includes('₹') || tl.includes('rupee');
                // НЕ содержит SGD/USD (избегаем кнопку для SGD)
                const notSGD = !tl.includes('sgd') && !tl.includes('s$') && !tl.includes('usd');
                // НЕ пропускаем amount=0 — numpad-структура PayGlocal

                if (hasINR && notSGD) {
                    return {x: r.x + r.width/2, y: r.y + r.height/2, txt: combined.substring(0,60)};
                }
            }
            return null;
        }"""

        # JS: кликаем INDIAN RUPEE радио напрямую (Paytm selectCurrencyPage)
        # 1) Находим radio input в секции с "INDIAN RUPEE" / "rupee"
        # 2) Кликаем его через JS (гарантирует выбор, даже если mouse.click мимо)
        # 3) Возвращаем координаты для дополнительного mouse.click
        _INR_RADIO_JS = """() => {
            // Шаг 1: ищем radio INPUT внутри контейнера с текстом "indian rupee"
            for (const inp of document.querySelectorAll('input[type="radio"]')) {
                const parent = inp.closest('li, div, label, tr') || inp.parentElement;
                if (!parent) continue;
                const ptxt = (parent.innerText || parent.textContent || '').toLowerCase();
                if (!ptxt.includes('indian rupee') && !ptxt.includes('rupee')) continue;
                if (ptxt.includes('singapore') || ptxt.includes('sgd')) continue;
                // Кликаем через JS для гарантии выбора
                try {
                    inp.click();
                    inp.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                    inp.checked = true;
                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                } catch(e) {}
                const r = inp.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    return {x: r.x + r.width/2, y: r.y + r.height/2, txt: 'radio-inr'};
                // Если скрыт — вернём координаты label/контейнера
                const pr = parent.getBoundingClientRect();
                if (pr.width > 20 && pr.height > 10)
                    return {x: pr.x + pr.width/2, y: pr.y + pr.height/2, txt: 'container-inr'};
            }
            // Шаг 2: ищем текстовый элемент "INDIAN RUPEE" и кликаем его
            for (const el of document.querySelectorAll('*')) {
                const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (txt !== 'indian rupee' && !txt.startsWith('indian rupee')) continue;
                if (txt.length > 80) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8 || r.width > 700) continue;
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                try { el.click(); } catch(e) {}
                return {x: r.x + r.width/2, y: r.y + r.height/2, txt: txt.substring(0,30)};
            }
            // Шаг 3: PayU MCP-стиль "Choose currency" — карточка "INR ₹XXX" как div/li/button
            // Ищем карточку с "INR" которая НЕ начинается с "pay" (не кнопка Pay)
            for (const el of document.querySelectorAll('div, li, tr, [role="option"], [role="radio"], span, button, [role="button"]')) {
                const txt = (el.innerText || el.textContent || '').trim();
                if (txt.length < 2 || txt.length > 60) continue;
                const tl = txt.toLowerCase();
                if (!tl.includes('inr') && !tl.includes('indian')) continue;
                if (/\\b(sgd|usd|eur|gbp|aed|aud|s\\$)\\b/i.test(txt)) continue;
                if (tl.startsWith('pay')) continue;  // не кнопка Pay
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 12) continue;
                const s = window.getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                try { el.click(); } catch(e) {}
                return {x: r.x + r.width/2, y: r.y + r.height/2, txt: 'payu-inr-row'};
            }
            return null;
        }"""

        # Debug: скриншот + список видимых кнопок (только первый pay_attempt)
        if pay_attempt == 0:
            try:
                _all_els = await page.evaluate("""() => {
                    const res = [];
                    for (const el of document.querySelectorAll('button,[role="button"],a')) {
                        const s = window.getComputedStyle(el);
                        if (s.display === 'none' || s.visibility === 'hidden') continue;
                        const raw = (el.innerText||'').trim();
                        // Первые 4 строки для отображения (как в _VIS_PAY_JS)
                        const lines = raw.split('\\n').slice(0,4).map(l=>l.trim()).filter(l=>l);
                        const combined = lines.join(' ').replace(/\\s+/g,' ').trim();
                        if (!combined || combined.length < 2) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 20 || r.height < 8) continue;
                        res.push(combined.substring(0,60));
                    }
                    return res;
                }""")
                print(f"  [DBG] Видимые кнопки: {_all_els}")
            except Exception:
                pass

        # На PayGlocal прокручиваем к кнопке Pay перед поиском
        if "payglocal" in page.url:
            try:
                await page.evaluate("""() => {
                    for (const el of document.querySelectorAll('button,a,[role="button"],*')) {
                        const t = (el.innerText || '').toLowerCase().trim();
                        if (t.startsWith('pay') && (t.includes('₹') || t.includes('inr'))) {
                            el.scrollIntoView({behavior: 'instant', block: 'center'});
                            return;
                        }
                    }
                    window.scrollTo(0, Math.round(document.body.scrollHeight / 2));
                }""")
                await page.wait_for_timeout(400)
            except Exception:
                pass

        # ── Change Currency / Pay in another currency → USD flow ─────────────
        # Если на странице есть "Change Currency" или "Pay in another currency" —
        # кликаем через Playwright locator (правильно учитывает iframe-координаты),
        # выбираем USD, нажимаем Pay.
        pay_clicked = False
        _CC_PHRASES = [
            "pay in another currency", "in another currency",
            "change currency", "change curr",
        ]
        for _fr_cc in [page.main_frame] + list(page.frames):
            if pay_clicked:
                break
            try:
                _cc_loc = None
                for _phrase in _CC_PHRASES:
                    # Ищем именно кликабельные элементы (a/button/span/role=button)
                    import re as _re_cc
                    _pat = _re_cc.compile(_phrase, _re_cc.I)
                    for _sel_cc in [
                        f"a", "button", "[role='link']", "[role='button']", "span", "p"
                    ]:
                        _loc = _fr_cc.locator(_sel_cc).filter(has_text=_pat).first
                        try:
                            if await _loc.is_visible(timeout=200):
                                _cc_loc = _loc
                                print(f"  Найден '{_phrase}' ({_sel_cc}) — кликаю...")
                                break
                        except Exception:
                            pass
                    if _cc_loc:
                        break
                if _cc_loc is None:
                    # JS-фолбэк: ищем напрямую в DOM фрейма
                    try:
                        _bbox = await _fr_cc.evaluate("""() => {
                            for (const el of document.querySelectorAll(
                                    'a,button,span,[role="button"],[role="link"]')) {
                                const t = (el.textContent || '').toLowerCase();
                                if (t.includes('another currency') || t.includes('change currency'))
                                {
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 10 && r.height > 5)
                                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                                }
                            }
                            return null;
                        }""")
                        if _bbox:
                            print(f"  JS-клик по 'another currency' в фрейме {_fr_cc.url[:40]}...")
                            await _fr_cc.evaluate("""() => {
                                for (const el of document.querySelectorAll(
                                        'a,button,span,[role="button"],[role="link"]')) {
                                    const t = (el.textContent || '').toLowerCase();
                                    if (t.includes('another currency') || t.includes('change currency'))
                                    { el.click(); return; }
                                }
                            }""")
                            await page.wait_for_timeout(1_500)
                        else:
                            continue
                    except Exception:
                        continue
                else:
                    await _cc_loc.click(timeout=5_000)
                # Ждём появления модалки со списком валют (до 5 сек)
                try:
                    await _fr_cc.wait_for_function("""() => {
                        for (const el of document.querySelectorAll('*')) {
                            const t = (el.innerText || el.textContent || '').trim().toUpperCase();
                            if ((t.includes('INR') || t.includes('INDIAN RUPEE')
                                    || t.includes('SELECT A CURRENCY'))
                                    && el.getBoundingClientRect().width > 10) return true;
                        }
                        return false;
                    }""", timeout=5_000)
                except Exception:
                    pass
                await page.wait_for_timeout(500)
                try:
                    await page.screenshot(path="debug/debug_currency_modal.png")
                    print("  Скриншот валютной модалки: debug_currency_modal.png")
                except Exception:
                    pass
                # Ищем и кликаем INR (следующий по списку после дефолтной валюты)
                _inr_clicked = False
                for _sel in [
                    "li:has-text('INR')", "li:has-text('Indian Rupee')", "li:has-text('INDIAN RUPEE')",
                    "[role='option']:has-text('INR')", "[role='listitem']:has-text('INR')",
                    "tr:has-text('INR')", "tr:has-text('Indian Rupee')",
                    "div:has-text('Indian Rupee')", "span:has-text('Indian Rupee')",
                    "button:has-text('INR')", "div:has-text('INR')", "span:has-text('INR')",
                ]:
                    try:
                        _inr_loc = _fr_cc.locator(_sel).filter(has_not_text="Pay").first
                        if await _inr_loc.is_visible(timeout=500):
                            _txt = (await _inr_loc.inner_text()).strip()[:40]
                            print(f"  Выбираю INR: {_txt!r}")
                            await _inr_loc.click(timeout=3_000)
                            _inr_clicked = True
                            break
                    except Exception:
                        pass
                if not _inr_clicked:
                    # JS-фолбэк: ищем INR/Indian Rupee в списке
                    try:
                        _inr_bb = await _fr_cc.evaluate("""() => {
                            for (const el of document.querySelectorAll('li,tr,[role="option"],[role="listitem"],div,span')) {
                                const t = (el.textContent || '').trim().toLowerCase();
                                if ((t.includes('indian rupee') || t === 'inr') && t.length < 60) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 20 && r.height > 8) {
                                        el.click();
                                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                                    }
                                }
                            }
                            return null;
                        }""")
                        if _inr_bb:
                            print(f"  Выбираю INR (JS-фолбэк)")
                            await page.mouse.click(_inr_bb["x"], _inr_bb["y"])
                            _inr_clicked = True
                    except Exception:
                        pass
                if not _inr_clicked:
                    print(f"  {Y}INR не найден в списке валют — пропускаем{RST}")
                    break
                # Ждём обновления кнопки Pay (до 3 сек)
                await page.wait_for_timeout(1_000)
                # Ищем кнопку Pay через locator в том же фрейме
                _pay_inr_loc = None
                for _psel in [
                    "button:has-text('Pay')", "[role='button']:has-text('Pay')",
                    "a:has-text('Pay')",
                ]:
                    try:
                        _ploc = _fr_cc.locator(_psel).filter(
                            has_not_text="another"
                        ).filter(has_not_text="Apple").filter(
                            has_not_text="Google"
                        ).filter(has_not_text="Change").first
                        if await _ploc.is_visible(timeout=500):
                            _pay_inr_loc = _ploc
                            break
                    except Exception:
                        pass
                if _pay_inr_loc:
                    _ptxt = (await _pay_inr_loc.inner_text()).strip()[:60]
                    print(f"  Нажимаю Pay INR: {_ptxt!r}")
                    # Ждём пока backdrop-оверлей (tw-bg-backdrop-drawer) исчезнет
                    try:
                        await _fr_cc.wait_for_selector(
                            ".tw-bg-backdrop-drawer",
                            state="hidden", timeout=5_000
                        )
                    except Exception:
                        pass
                    try:
                        await _pay_inr_loc.click(timeout=5_000)
                    except Exception as _click_ex:
                        print(f"  {Y}Обычный клик Pay заблокирован ({_click_ex}), пробуем JS-клик{RST}")
                        await _pay_inr_loc.evaluate("el => el.click()")
                    pay_clicked = True
                    await page.wait_for_timeout(2_000)
                else:
                    print(f"  {Y}Кнопка Pay не найдена после выбора INR{RST}")
                break  # нашли Change Currency — больше фреймы не перебираем
            except Exception as _cc_ex:
                print(f"  {Y}Change Currency фрейм: {_cc_ex}{RST}")
                continue

        if pay_clicked:
            break  # переходим к ожиданию 3DS / результата

        # Стратегия (frame-aware):
        # 1) СНАЧАЛА выбираем INDIAN RUPEE радио/строку (Paytm/PayU/PayGlocal)
        #    — ищем во всех фреймах через Playwright locators
        # 2) ПОТОМ кликаем Pay INR кнопку — тоже во всех фреймах
        for attempt in range(3):
            # A: выбираем INDIAN RUPEE — ищем радио ИЛИ строку выбора валюты во всех фреймах
            _inr_selected = False
            for _fr in [page.main_frame] + list(page.frames):
                if _inr_selected:
                    break
                try:
                    # Пробуем INR radio через JS в конкретном фрейме
                    _inr_radio_result = None
                    try:
                        _inr_radio_result = await _fr.evaluate(_INR_RADIO_JS)
                    except Exception:
                        pass
                    if _inr_radio_result:
                        _inr_x = _inr_radio_result.get("x", 0)
                        _inr_y = _inr_radio_result.get("y", 0)
                        print(f"  Выбираю INDIAN RUPEE (frame, attempt {attempt+1}): {_inr_radio_result.get('txt','')!r}")
                        # Реальный mouse.click — Angular SPA игнорирует JS el.click() из evaluate
                        if _inr_x and _inr_y:
                            try:
                                await page.mouse.move(_inr_x, _inr_y)
                                await page.wait_for_timeout(200)
                                await page.mouse.click(_inr_x, _inr_y)
                            except Exception:
                                pass
                        # Дополнительно: radio-click через JS (Paytm/Airtel)
                        try:
                            await _fr.evaluate("""() => {
                                for (const inp of document.querySelectorAll('input[type="radio"]')) {
                                    const p = inp.closest('li,div,label,tr') || inp.parentElement;
                                    if (!p) continue;
                                    const t = (p.innerText||'').toLowerCase();
                                    if ((t.includes('indian rupee')||t.includes('rupee'))&&!t.includes('sgd')&&!t.includes('singapore')) {
                                        inp.click();
                                        inp.dispatchEvent(new Event('change',{bubbles:true}));
                                        return;
                                    }
                                }
                            }""")
                        except Exception:
                            pass
                        # Ждём пока кнопка Pay обновится до ₹ / INR (PayU MCP меняет текст асинхронно)
                        try:
                            await page.wait_for_function("""() => {
                                for (const el of document.querySelectorAll('*')) {
                                    const raw = (el.innerText || '').trim();
                                    const lines = raw.split('\\n').slice(0, 4).map(l => l.trim()).filter(l => l);
                                    const tl = lines.join(' ').toLowerCase();
                                    if (tl.startsWith('pay') && (tl.includes('\\u20b9') || tl.includes('inr'))
                                            && !tl.includes('sgd') && !tl.includes('s$')) return true;
                                }
                                return false;
                            }""", timeout=6_000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(500)
                        _inr_selected = True
                    else:
                        # Пробуем кликнуть текстовый элемент "Indian Rupee" / "INR ₹"
                        for _kw in ["Indian Rupee", "INR ₹", "inr"]:
                            try:
                                _inr_el = _fr.locator(
                                    f"*:has-text('{_kw}')"
                                ).filter(has_not_text="SGD").filter(has_not_text="Pay").first
                                if await _inr_el.count() > 0:
                                    _bb = await _inr_el.bounding_box()
                                    if _bb and _bb["width"] > 20 and _bb["height"] > 8:
                                        print(f"  Выбираю INR: клик «{_kw}» (attempt {attempt+1})")
                                        await page.mouse.click(_bb["x"] + _bb["width"] / 2, _bb["y"] + _bb["height"] / 2)
                                        await page.wait_for_timeout(1_200)
                                        _inr_selected = True
                                        break
                            except Exception:
                                continue
                except Exception:
                    continue

            # B: ищем и кликаем Pay INR кнопку — во всех фреймах через Playwright locators
            for _fr in [page.main_frame] + list(page.frames):
                if pay_clicked:
                    break
                try:
                    import re as _re_btn
                    # Включаем div-кнопки (PayU MCP использует div, не <button>)
                    _all_btns = _fr.locator(
                        "button, [role='button'], a, "
                        "div[class*='btn'], div[class*='pay'], div[class*='Pay'], "
                        "div[class*='submit'], div[class*='cta']"
                    )
                    _btn_count = await _all_btns.count()
                    for _bi in range(min(_btn_count, 60)):
                        try:
                            _b = _all_btns.nth(_bi)
                            _raw = (await _b.inner_text()).strip()
                            _lines = _raw.split("\n")[:4]
                            _t = " ".join(l.strip() for l in _lines if l.strip()).lower()
                            if not _t.startswith("pay"):
                                continue
                            if "inr" not in _t and "₹" not in _t:
                                continue
                            if any(x in _t for x in ("apple", "google", "sgd", "s$", "usd", "eur")):
                                continue
                            # НЕ фильтруем по amount=0 — PayGlocal/PayU кнопка
                            # может содержать "0" из numpad-структуры, реальная сумма загружена
                            _bb = await _b.bounding_box()
                            if not _bb or _bb["width"] < 40 or _bb["height"] < 12:
                                continue
                            print(f"  Нажимаю Pay INR: {_t[:50]!r} (frame: {_fr.url[:40]})")
                            await _b.scroll_into_view_if_needed()
                            await page.wait_for_timeout(300)
                            await page.mouse.click(_bb["x"] + _bb["width"] / 2, _bb["y"] + _bb["height"] / 2)
                            pay_clicked = True
                            await page.wait_for_timeout(2_000)
                            break
                        except Exception:
                            continue
                except Exception:
                    continue

            if pay_clicked:
                break

            # Fallback: старый JS-подход на случай нестандартного шлюза
            cp = None
            for _fr2 in [page.main_frame] + list(page.frames):
                try:
                    cp = await _fr2.evaluate(_PAY_INR_JS)
                    if not cp:
                        cp = await _fr2.evaluate(_VIS_PAY_JS)
                    if cp:
                        txt = (cp.get("txt") or cp.get("t") or "?")[:40]
                        print(f"  Нажимаю Pay INR (JS fallback): {txt!r}")
                        await _fr2.evaluate(
                            f"() => {{ const els = document.querySelectorAll('button,[role=\"button\"],a');"
                            f"for(const el of els){{ const t=(el.innerText||'').toLowerCase();"
                            f"if(t.startsWith('pay')&&(t.includes('inr')||t.includes('₹'))&&!t.includes('sgd')){{el.click();return;}} }} }}"
                        )
                        pay_clicked = True
                        await page.wait_for_timeout(2_000)
                        break
                except Exception:
                    continue
            if pay_clicked:
                break

            # Pay INR не найдена даже после выбора радио
            print(f"  Pay INR не найдена (attempt {attempt+1}) — скриншот...")
            try:
                await page.screenshot(path=f"debug/debug_pay_{pay_attempt}_{attempt}.png")
                print(f"    Скриншот: debug_pay_{pay_attempt}_{attempt}.png")
            except Exception:
                pass
            await page.wait_for_timeout(2_000)

        if not pay_clicked:
            print(f"  {Y}⚠ Кнопка Pay INR не найдена — ищу любую Pay-кнопку (SGD/USD/др.)...{RST}")
            try:
                await page.screenshot(path="debug/debug_pay_notfound.png")
                print("  Скриншот: debug_pay_notfound.png")
            except Exception:
                pass
            # Fallback: кликаем на единственную/первую видимую кнопку Pay (SGD/USD/др.)
            # Только если нет INR — исключаем Apple Pay, Google Pay, "Pay in another currency"
            _fallback_clicked = False
            for _fr3 in [page.main_frame] + list(page.frames):
                if _fallback_clicked:
                    break
                try:
                    _all_fb = _fr3.locator("button, [role='button']")
                    _fb_cnt = await _all_fb.count()
                    for _fi in range(_fb_cnt):
                        try:
                            _fb = _all_fb.nth(_fi)
                            _ft = (await _fb.inner_text()).strip().lower()
                            if not _ft.startswith("pay"):
                                continue
                            if any(x in _ft for x in (
                                "apple", "google", "in another", "another currency"
                            )):
                                continue
                            _fbb = await _fb.bounding_box()
                            if not _fbb or _fbb["width"] < 40 or _fbb["height"] < 12:
                                continue
                            print(f"  Fallback Pay-кнопка: {_ft[:50]!r}")
                            await _fb.scroll_into_view_if_needed()
                            await page.wait_for_timeout(300)
                            await page.mouse.click(
                                _fbb["x"] + _fbb["width"] / 2,
                                _fbb["y"] + _fbb["height"] / 2
                            )
                            _fallback_clicked = True
                            pay_clicked = True
                            await page.wait_for_timeout(2_000)
                            break
                        except Exception:
                            continue
                except Exception:
                    continue
            if not pay_clicked:
                return False

        # Шаг 3: ждём пока страница уйдёт на 3DS (до 30 сек) или вернётся на Flipkart
        try:
            await page.wait_for_url(
                lambda u: _is_3ds_page(u) or "flipkart.com" in u or "payglocal" in u,
                timeout=30_000
            )
        except Exception:
            pass
        # Дополнительно ждём загрузку самой 3DS-страницы
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass

        _otp_required = False

        for _3ds_try in range(12):
            cur_url_3ds = page.url
            if not _is_3ds_page(cur_url_3ds):
                # Если страница ещё не навигировала — даём ещё 3 сек
                if _3ds_try == 0:
                    await page.wait_for_timeout(3_000)
                    cur_url_3ds = page.url
                if not _is_3ds_page(cur_url_3ds):
                    break
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3_000)
            except Exception:
                pass
            cur_url_3ds = page.url
            if not _is_3ds_page(cur_url_3ds):
                break

            all_text = await _page_all_text()
            print(f"  3DS ({_3ds_try+1}): {cur_url_3ds[:80]}")

            # Есть поле ввода OTP → пробуем получить из Telegram, иначе ждём вручную
            if await _has_otp_input():
                print(f"  {G}✅ 3DS: поле ввода OTP обнаружено{RST}")
                _otp_required = True

                print(f"  {Y}💬 Перешлите SMS с кодом своему Telegram боту!{RST}")
                _3ds_otp = await _get_3ds_otp_from_telegram()

                if _3ds_otp:
                    print(f"  {G}✅ OTP из Telegram получен — ввожу автоматически{RST}")
                    _otp_inp_sel = (
                        "input[name*='otp' i], input[name*='code' i], "
                        "input[placeholder*='otp' i], input[placeholder*='code' i], "
                        "input[maxlength='6'], input[maxlength='4'], "
                        "input[type='tel'], input[type='number'][maxlength]"
                    )
                    _entered = False
                    for _fr in [page] + list(page.frames):
                        try:
                            _inp = _fr.locator(_otp_inp_sel).first
                            if await _inp.count() > 0:
                                await _inp.click()
                                await _inp.fill(_3ds_otp)
                                await page.wait_for_timeout(300)
                                # Enter — надёжнее синтетического клика на React-кнопки
                                try:
                                    await _inp.press("Enter")
                                except Exception:
                                    pass
                                await page.wait_for_timeout(400)
                                _entered = True
                                # Backup: кликаем Submit после ввода OTP
                                _stxt = await _submit_click(page, frame=_fr)
                                if _stxt:
                                    print(f"  3DS: нажал Submit «{_stxt[:20]}» после OTP")
                                break
                        except Exception:
                            pass

                    if _entered:
                        try:
                            await page.wait_for_url(
                                lambda u: "flipkart.com" in u, timeout=60_000
                            )
                            print(f"  {G}✅ OTP подтверждён автоматически — вернулись на Flipkart{RST}")
                            _tg_send_direct(f"✅ *OTP* `{_3ds_otp}` *введён успешно* — возврат на Flipkart")
                            _otp_required = False
                        except Exception:
                            print(f"  {Y}⚠ Навигация после OTP не завершилась — жду вручную{RST}")
                else:
                    print(f"  {Y}⏳ OTP из Telegram не получен — введите код на сайте вручную{RST}")

                break

            # Есть текст про OTP (но нет поля) → страница «отправки» кода
            if _has_otp_text(all_text):
                print(f"  {G}✅ 3DS: страница подтверждения OTP{RST}")
                _otp_required = True
                # После 2 итераций кликания без результата — перестаём слепо жать Submit
                # и ждём пока пользователь введёт OTP вручную (до 15 мин)
                if _3ds_try >= 2:
                    print(f"  {Y}⏳ OTP-страница не меняется — ждём ввода вручную (до 15 мин){RST}")
                    try:
                        _tg_send_direct(
                            f"⏳ *Введите OTP* на странице 3DS верификации вручную.\n"
                            f"_(Код пришёл на почту/телефон привязанный к карте)_"
                        )
                    except Exception:
                        pass
                    try:
                        await page.wait_for_url(
                            lambda u: "flipkart.com" in u, timeout=900_000
                        )
                        print(f"  {G}✅ OTP подтверждён вручную — вернулись на Flipkart{RST}")
                        _otp_required = False
                    except Exception:
                        print(f"  {Y}⚠ Timeout ожидания OTP — считаю отклонённым{RST}")
                    break

            # Ищем и нажимаем Next/Submit
            _btn_clicked = False
            try:
                await page.wait_for_selector(
                    "button, input[type='submit'], [role='button']",
                    state="visible", timeout=5_000
                )
            except Exception:
                pass

            _btn_selectors = [
                "button:has-text('Next')",
                "button:has-text('Submit')",
                "button:has-text('Continue')",
                "button:has-text('Proceed')",
                "button:has-text('OK')",
                "input[type='submit']",
                "button[type='submit']",
                # UQPAY / hitrust специфичные
                "button:has-text('NEXT')",
                "button:has-text('SUBMIT')",
                "[role='button']:has-text('Next')",
                "[role='button']:has-text('Submit')",
                "a:has-text('Next')",
                "a:has-text('Submit')",
                "[class*='btn']:has-text('Next')",
                "[class*='btn']:has-text('Submit')",
                "input[type='button']",
            ]
            for sel in _btn_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.wait_for(state="visible", timeout=2_000)
                        _bb = await btn.bounding_box()
                        if _bb:
                            await page.mouse.click(
                                _bb["x"] + _bb["width"] / 2,
                                _bb["y"] + _bb["height"] / 2,
                            )
                        else:
                            await btn.click()
                        txt = ""
                        try:
                            txt = (await btn.inner_text()).strip()
                        except Exception:
                            pass
                        print(f"  3DS: нажал «{txt or sel[:30]}»")
                        _btn_clicked = True
                        break
                except Exception:
                    pass

            if not _btn_clicked:
                # Пробуем iframe-кнопки (cross-origin)
                for fr in page.frames:
                    if _btn_clicked:
                        break
                    for _isel in _btn_selectors:
                        try:
                            _fb = fr.locator(_isel).first
                            if await _fb.count() > 0:
                                _bb = await _fb.bounding_box()
                                if _bb:
                                    await page.mouse.click(
                                        _bb["x"] + _bb["width"] / 2,
                                        _bb["y"] + _bb["height"] / 2,
                                    )
                                else:
                                    await _fb.click()
                                _fbtxt = _isel
                                try:
                                    _fbtxt = (await _fb.inner_text()).strip()
                                except Exception:
                                    pass
                                print(f"  3DS iframe: нажал «{_fbtxt[:30]}»")
                                _btn_clicked = True
                                break
                        except Exception:
                            pass

            if not _btn_clicked:
                # JS-fallback: нажимаем любую видимую кнопку / submit через JS
                try:
                    _js_result = await page.evaluate("""() => {
                        const words = ['Next','Submit','Continue','Proceed','OK','NEXT','SUBMIT'];
                        // 1. Ищем по тексту
                        for (const tag of ['button','a','input']) {
                            for (const el of document.querySelectorAll(tag)) {
                                const t = (el.textContent || el.value || '').trim();
                                if (words.some(w => t.toLowerCase() === w.toLowerCase())) {
                                    el.click(); return 'clicked:' + t;
                                }
                            }
                        }
                        // 2. Нажимаем первую видимую кнопку
                        for (const el of document.querySelectorAll('button,[type=submit],[role=button]')) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                el.click(); return 'clicked_first:' + (el.textContent||'').trim();
                            }
                        }
                        // 3. Submit формы
                        const f = document.querySelector('form');
                        if (f) { f.submit(); return 'form_submit'; }
                        return null;
                    }""")
                    if _js_result:
                        print(f"  3DS JS: {_js_result}")
                        _btn_clicked = True
                except Exception:
                    pass

            if not _btn_clicked:
                # Enter как последний шанс (форма уже заполнена)
                try:
                    await page.keyboard.press("Enter")
                    print(f"  3DS: Enter (fallback)")
                    _btn_clicked = True
                except Exception:
                    pass

            if not _btn_clicked:
                _3ds_body_low = all_text.lower()
                _is_processing = (
                    "processing" in _3ds_body_low or "please wait" in _3ds_body_low
                    or "do not close" in _3ds_body_low or "do not refresh" in _3ds_body_low)
                if _is_processing:
                    print(f"  3DS: обработка — ждём авто-редиректа...")
                    await page.wait_for_timeout(4_000)
                    continue
                else:
                    print(f"  3DS: кнопка не найдена — ждём авто-навигации...")
                    await page.wait_for_timeout(3_000)

            # Ждём навигации после клика
            try:
                await page.wait_for_url(
                    lambda url: not _is_3ds_page(url), timeout=8_000
                )
                break
            except Exception:
                await page.wait_for_timeout(2_000)

        # OTP поле обнаружено — ждём завершения верификации пользователем (до 15 мин)
        if _otp_required:
            print(f"  {G}Карта принята — ожидаю ввода OTP и возврата на Flipkart (до 15 мин)...{RST}")
            try:
                await page.wait_for_url(
                    lambda u: "flipkart.com" in u, timeout=900_000
                )
                print(f"  {G}✅ OTP подтверждён — вернулись на Flipkart{RST}")
                # Не возвращаем "otp_required" — продолжаем нормальный поток
            except Exception:
                print(f"  {Y}⚠ Timeout ожидания OTP — считаю отклонённым{RST}")
                return "otp_required"

        # Ждём результата платежа (до 45 сек) с проверкой кнопки «Остановить»
        _pay_wait_dl = asyncio.get_event_loop().time() + 45
        while asyncio.get_event_loop().time() < _pay_wait_dl:
            _ckcancel()
            try:
                _cur = page.url
                if ("flipkart.com" in _cur or "/retry" in _cur or "/error" in _cur
                        or "cardinalcommerce.com" in _cur or "StepUp" in _cur
                        or "3dsecure" in _cur or "stepup" in _cur.lower()
                        or _is_3ds_page(_cur)):
                    break
                _body_chk = await page.evaluate(
                    "() => document.body ? document.body.innerText.toLowerCase() : ''")
                if "declined" in _body_chk:
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
        declined = False
        _insufficient_funds = False
        try:
            cur_url = page.url
            if "/retry" in cur_url or "/error" in cur_url:
                try:
                    _bl = await page.evaluate("() => document.body.innerText.toLowerCase()")
                    if "insufficient fund" in _bl:
                        _insufficient_funds = True
                        print(f"  {Y}⚠ Недостаточно средств на карте (PayGlocal){RST}")
                except Exception:
                    pass
                declined = True
            elif "uiscoop.flipkart.com" in cur_url and (
                    "errorMessage" in cur_url or "retryAllowed" in cur_url):
                # Paytm confirmation-page с ошибкой (например "security reasons")
                import urllib.parse as _up
                _err = ""
                if "errorMessage" in cur_url:
                    _err = _up.unquote(cur_url.split("errorMessage=")[-1].split("&")[0][:80])
                print(f"  {Y}⚠ Paytm ошибка подтверждения: {_err or 'неизвестна'}{RST}")
                try:
                    await page.screenshot(path=f"debug/debug_gateway_{pay_attempt}.png")
                except Exception:
                    pass
                declined = True
            elif any(d in cur_url for d in _PAYMENT_DOMAINS):
                # Остались на шлюзе — платёж не перенаправил (тихое отклонение)
                body_text = await page.evaluate("() => document.body.innerText.toLowerCase()")
                if "declined" in body_text or "payment failed" in body_text or "was declined" in body_text:
                    declined = True
                elif "/challenge" not in cur_url:
                    # Не 3DS и не flipkart.com — вероятно тихое отклонение
                    print(f"  {Y}⚠ Остались на шлюзе ({cur_url[:60]}) — скорее всего отклонено{RST}")
                    try:
                        await page.screenshot(path=f"debug/debug_gateway_{pay_attempt}.png")
                    except Exception:
                        pass
                    declined = True
        except Exception:
            pass

        if declined:
            _reason = "Недостаточно средств" if _insufficient_funds else "Платёж отклонён"
            print(f"  {Y}⚠ {_reason} — возвращаюсь на Flipkart...{RST}")
            # Не используем go_back() — POST-страницы дают ERR_CACHE_MISS
            try:
                await page.goto("https://www.flipkart.com",
                                wait_until="domcontentloaded", timeout=10_000)
            except Exception:
                pass
            return "insufficient_funds" if _insufficient_funds else "declined"
        else:
            break  # успешно нажали Pay или 3DS

    # После оплаты может открыться 3DS верификация
    _3ds_res = await _handle_3ds_verification(page)
    if _3ds_res == "otp_timeout":
        return "otp_timeout"
    if _3ds_res == "declined":
        return "declined"
    if _3ds_res == "switch_card":
        print(f"  {Y}⚠ Смена карты — возвращаюсь на Flipkart...{RST}")
        if "flipkart.com" not in page.url:
            try:
                await page.goto("https://www.flipkart.com",
                                wait_until="domcontentloaded", timeout=10_000)
            except Exception:
                pass
        return "switch_card"

    # Проверяем что платёж завершился и вернулись на Flipkart
    if "flipkart.com" not in page.url:
        if _is_3ds_page(page.url) or "cardinal" in page.url.lower():
            print(f"  {Y}⏳ Всё ещё на 3DS странице — перехожу на flipkart-black-store...{RST}")
            try:
                await page.goto("https://www.flipkart.com/flipkart-black-store",
                                wait_until="domcontentloaded", timeout=20_000)
                print(f"  {G}✅ Перешли на flipkart-black-store{RST}")
            except Exception:
                return "declined"
        else:
            print(f"  {Y}⚠ После оплаты не вернулись на Flipkart ({page.url[:60]}) — отклонён{RST}")
            try:
                await page.screenshot(path="debug/debug_pay_nofk.png")
            except Exception:
                pass
            return "declined"

    return True


async def _mouse_click(page, element) -> bool:
    """Кликнуть найденный элемент через mouse.click() по bounding_box координатам."""
    try:
        _bb = await element.bounding_box()
        if _bb and _bb["width"] > 0:
            await page.mouse.click(
                _bb["x"] + _bb["width"] / 2,
                _bb["y"] + _bb["height"] / 2,
            )
            return True
        await element.click()
        return True
    except Exception:
        return False


async def _submit_click(page, frame=None) -> str | None:
    """Найти кнопку Submit по конкретному селектору и кликнуть через mouse.click()."""
    _sub_sels = [
        "a#btnSubmit", "a.gobtn",
        "button:has-text('SUBMIT')", "button:has-text('Submit')",
        "button:has-text('Confirm')", "button:has-text('OK')",
        "input[value='SUBMIT']", "input[value='Submit']",
        "button[type='submit']", "input[type='submit']",
    ]
    _ctx = frame or page
    for _sel in _sub_sels:
        try:
            _el = _ctx.locator(_sel).first
            if await _el.count() > 0 and await _el.is_visible():
                await _mouse_click(page, _el)
                _txt = ""
                try:
                    _txt = (await _el.inner_text()).strip() or (await _el.get_attribute("value") or "")
                except Exception:
                    pass
                return _txt or _sel.split(":")[0]
        except Exception:
            continue
    return None


async def _handle_3ds_verification(page) -> bool:
    """
    Обрабатывает 3DS Transaction Verification (hitrust.com / visa / mc).
    Нажимает «Next», затем ждёт пока пользователь введёт OTP-код вручную.
    """
    import random as _r

    def _build_card_rows_3ds() -> list:
        rows = []
        for _opt in _3ds_card_options[:]:
            _nm = (_opt["card"].get("nickname") or _opt["card"].get("name")
                   or _mask_card(_opt["card"].get("number", "")))
            rows.append([{"text": f"💳 {_nm}", "callback_data": f"pay:switch:{_opt['pos']}"}])
        return rows

    # Ранняя проверка: PayGlocal /retry или /error = карта отклонена банком сразу
    _cur_url_early = page.url
    if ("payglocal" in _cur_url_early and
            ("/retry" in _cur_url_early or "/error" in _cur_url_early)):
        print(f"  {Y}⚠ PayGlocal: карта отклонена банком ({_cur_url_early[:70]}){RST}")
        try:
            _card_rows = _build_card_rows_3ds()
            _msg = "❌ *Карта отклонена банком*\n\nБот автоматически повторит попытку."
            if _card_rows:
                _msg += "\n\n_Доступные карты:_"
                _tg_send_direct_kb(_msg, {"inline_keyboard": _card_rows})
            else:
                _tg_send_direct(_msg)
        except Exception:
            pass
        return "declined"

    # Если уже на 3DS/ACS странице — не ждём навигации
    _3ds_doms = ("cardinalcommerce.com", "3dsecure", "verify.visa.com",
                 "mastercard.com/ac", "hitrust.com", "acs-auth", "challenge",
                 "threeDSecure", "threedsecure", "StepUp", "stepup")
    _on_3ds = any(d in page.url for d in _3ds_doms) or "3ds" in page.url.lower()
    if not _on_3ds:
        # Ждём 3DS страницы (hitrust, visa, mastercard ACS) — до 15 сек, с проверкой /retry
        try:
            _3ds_patterns = ("cardinalcommerce", "3dsecure", "verify.visa", "mastercard",
                             "hitrust", "acs-auth", "challenge", "threeDSecure", "threedsecure", "StepUp", "stepup")
            await page.wait_for_url(
                lambda u: (any(pat in u for pat in _3ds_patterns) or "3ds" in u.lower()
                           or ("payglocal" in u and ("/retry" in u or "/error" in u))),
                timeout=15_000
            )
        except Exception:
            pass
        # Повторная проверка после ожидания: попали на /retry?
        _cur_url_after = page.url
        if ("payglocal" in _cur_url_after and
                ("/retry" in _cur_url_after or "/error" in _cur_url_after)):
            print(f"  {Y}⚠ PayGlocal /retry после навигации — карта отклонена{RST}")
            try:
                _card_rows = _build_card_rows_3ds()
                _msg = "❌ *Карта отклонена банком*\n\nБот автоматически повторит попытку."
                if _card_rows:
                    _msg += "\n\n_Доступные карты:_"
                    _tg_send_direct_kb(_msg, {"inline_keyboard": _card_rows})
                else:
                    _tg_send_direct(_msg)
            except Exception:
                pass
            return "declined"
        if not (any(pat in page.url for pat in _3ds_patterns) or "3ds" in page.url.lower()):
            try:
                await page.wait_for_function(
                    "() => document.title.includes('Verification') || "
                    "      document.title.includes('Secure') || "
                    "      document.body.innerText.includes('Transaction Verification')",
                    timeout=8_000,
                )
            except Exception:
                return False

    # На CardinalCommerce StepUp ждём 5 сек перед Next — страница должна прогрузиться,
    # чтобы клик корректно запросил OTP-код у банка
    _cur_url_pre = page.url
    if "cardinalcommerce.com" in _cur_url_pre or "StepUp" in _cur_url_pre or "stepup" in _cur_url_pre.lower():
        print("  3DS: CardinalCommerce StepUp — жду 5 сек перед Next...")
        await page.wait_for_timeout(5_000)
    else:
        await page.wait_for_timeout(_r.uniform(600, 1_000))
    print("  3DS: страница верификации открыта — нажимаю Next...")

    # Ищем «Next» по тексту во всех фреймах
    _next_clicked = False
    _next_sel = (
        "button:has-text('Next'), button:has-text('NEXT'), "
        "input[value='Next'], input[value='NEXT'], input[type='submit'], "
        "a:has-text('Next'), a:has-text('NEXT'), "
        "[role='button']:has-text('Next'), [class*='btn']:has-text('Next')"
    )
    for _nfr in [page] + list(page.frames):
        try:
            _nb = _nfr.locator(_next_sel).first
            if await _nb.count() > 0:
                _nbb = await _nb.bounding_box()
                if _nbb:
                    await page.mouse.click(
                        _nbb["x"] + _nbb["width"] / 2,
                        _nbb["y"] + _nbb["height"] / 2,
                    )
                else:
                    await _nb.click()
                _next_clicked = True
                break
        except Exception:
            pass

    if not _next_clicked:
        # Ещё раз после паузы
        await page.wait_for_timeout(2_000)
        for _nfr in [page] + list(page.frames):
            try:
                _nb2 = _nfr.locator(_next_sel).first
                if await _nb2.count() > 0:
                    _nbb2 = await _nb2.bounding_box()
                    if _nbb2:
                        await page.mouse.click(
                            _nbb2["x"] + _nbb2["width"] / 2,
                            _nbb2["y"] + _nbb2["height"] / 2,
                        )
                    else:
                        await _nb2.click()
                    _next_clicked = True
                    print("  3DS: Next нажат (повторная попытка)")
                    break
            except Exception:
                pass

    if not _next_clicked:
        # JS-fallback: нажать любую кнопку или сабмитнуть форму
        try:
            _jsr = await page.evaluate("""() => {
                const words = ['Next','Submit','Continue','NEXT','SUBMIT'];
                for (const tag of ['button','a','input']) {
                    for (const el of document.querySelectorAll(tag)) {
                        const t = (el.textContent || el.value || '').trim();
                        if (words.some(w => t.toLowerCase() === w.toLowerCase())) {
                            el.click(); return 'js:' + t;
                        }
                    }
                }
                for (const el of document.querySelectorAll('button,[type=submit],[role=button]')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        el.click(); return 'js_first:' + (el.textContent||'').trim();
                    }
                }
                const f = document.querySelector('form');
                if (f) { f.submit(); return 'form_submit'; }
                return null;
            }""")
            if _jsr:
                print(f"  3DS: Next через JS — {_jsr}")
                _next_clicked = True
        except Exception:
            pass

    if not _next_clicked:
        try:
            await page.keyboard.press("Enter")
            print("  3DS: Next через Enter (fallback)")
            _next_clicked = True
        except Exception:
            pass

    if _next_clicked:
        await page.wait_for_timeout(1_200)

    # Код запрошен у банка именно сейчас (нажали Next). С этого момента ждём
    # СВЕЖИЙ OTP: один раз сбрасываем старые коды, чтобы не подхватить код от
    # прошлой операции, и дальше уже не очищаем.
    try:
        (_HERE / "data" / "tg_otp_3ds.json").write_text("[]", encoding="utf-8")
    except Exception:
        pass
    # Ждём поле ввода OTP — ищем во всех фреймах (может быть в cross-origin iframe)
    otp_inp = None
    _otp_frame_found = None
    _otp_dl = asyncio.get_event_loop().time() + 12
    while asyncio.get_event_loop().time() < _otp_dl:
        # Смена карты через TG может прийти ещё до появления OTP-поля — реагируем сразу
        if _switch_card_ev.is_set():
            print(f"  {Y}⚠ Смена карты из TG (до появления OTP-поля) — прерываю{RST}")
            return "switch_card"
        for _fr in [page] + list(page.frames):
            try:
                _c = _fr.locator(
                    "input[placeholder*='code' i], input[placeholder*='OTP' i], "
                    "input[type='text'], input[type='number'], input[type='tel']"
                ).first
                if await _c.count() > 0 and await _c.is_visible():
                    otp_inp = _c
                    _otp_frame_found = _fr
                    break
            except Exception:
                pass
        if otp_inp:
            break
        await page.wait_for_timeout(500)

    if otp_inp:
        # OTP-поле найдено → уведомляем что код отправлен банком
        try:
            _card_rows = _build_card_rows_3ds()
            print(f"  3DS: _3ds_card_options = {len(_card_rows)} карт(ы)")
            if _card_rows:
                _tg_send_direct_kb(
                    "📲 *Код подтверждения отправлен на карту*\n\n"
                    "Перешлите код сюда — бот введёт его автоматически.\n\n"
                    "_Или выберите другую карту:_",
                    {"inline_keyboard": _card_rows},
                )
            else:
                _tg_send_direct(
                    "📲 *Код подтверждения отправлен на карту*\n\n"
                    "Перешлите код сюда — бот введёт его автоматически."
                )
            print("  3DS: уведомление TG отправлено")
        except Exception as _ntf_err:
            print(f"  {R}3DS: ошибка отправки уведомления TG: {_ntf_err}{RST}")

        # Пробуем получить OTP автоматически из Telegram
        otp_code = await _get_3ds_otp_from_telegram()

        if otp_code == "switch_card":
            return "switch_card"

        if otp_code:
            print(f"  3DS OTP из Telegram: ***{otp_code[-2:]}")

            # OTP-поле уже найдено выше (_otp_frame_found / otp_inp)
            _otp_inp_all = otp_inp
            _otp_frame   = _otp_frame_found
            _sub_sel = (
                "a#btnSubmit, a.gobtn, "
                "button:has-text('SUBMIT'), button:has-text('Submit'), "
                "input[value='SUBMIT'], input[value='Submit'], "
                "button[type='submit'], input[type='submit'], "
                "a:has-text('SUBMIT'), a:has-text('Submit')"
            )

            _inp_to_use = _otp_inp_all or otp_inp
            try:
                bb_inp = await _inp_to_use.bounding_box()
                if bb_inp:
                    await page.mouse.click(
                        bb_inp["x"] + bb_inp["width"] / 2,
                        bb_inp["y"] + bb_inp["height"] / 2
                    )
                else:
                    await _inp_to_use.click()
            except Exception:
                pass
            await _inp_to_use.fill(otp_code)
            print("  3DS: код введён — жду 5 секунд перед нажатием Submit...")
            await page.wait_for_timeout(5_000)

            submit_clicked = False

            for _fr in ([_otp_frame] if _otp_frame else []) + [page] + list(page.frames):
                _stxt = await _submit_click(page, frame=_fr)
                if _stxt:
                    print(f"  3DS: SUBMIT нажат «{_stxt[:20]}»")
                    submit_clicked = True
                    await page.wait_for_timeout(2_000)
                    break
        else:
            # Код из Telegram не пришёл — ждём код или ручного ввода (до 15 мин)
            print()
            print(f"  {Y}══════════════════════════════════════════════════{RST}")
            print(f"  {Y}  3DS OTP: введи код в браузере и нажми SUBMIT.  {RST}")
            print(f"  {Y}══════════════════════════════════════════════════{RST}")
            print()
            _otp_tgt = asyncio.get_event_loop().time() + 900  # 15 мин
            import re as _re3d
            import json as _json3d
            _OTP_FILE_3D = _HERE / "data" / "tg_otp_3ds.json"
            # Файл НЕ очищаем — код мог прийти до появления поля 3DS (сброс старых
            # кодов делается один раз в начале оплаты в _enter_card_on_payments).
            _otp_submitted = False
            while asyncio.get_event_loop().time() < _otp_tgt:
                _ckcancel()
                # Пользователь выбрал другую карту через TG
                if _switch_card_ev.is_set():
                    print(f"  {Y}⚠ Смена карты из TG — прерываю ожидание OTP{RST}")
                    return "switch_card"
                if "flipkart.com" in page.url:
                    print(f"  {G}✅ 3DS подтверждён — Flipkart{RST}")
                    _tg_send_direct("✅ *3DS подтверждён* — возврат на Flipkart")
                    _otp_submitted = True
                    break
                # Читаем OTP из файла (bot.py пишет туда входящие коды)
                try:
                    _codes = _json3d.loads(_OTP_FILE_3D.read_text(encoding="utf-8"))
                    if _codes:
                        _nc = _codes.pop(0)
                        _OTP_FILE_3D.write_text(_json3d.dumps(_codes, ensure_ascii=False), encoding="utf-8")
                        print(f"  {G}✅ OTP из Telegram получен — ввожу...{RST}")
                        _tg_send_direct(f"🔑 *OTP* `{_nc}` *— ввожу в форму...*")
                        for _frl in [page] + list(page.frames):
                            try:
                                _il = _frl.locator(
                                    "input[name*='otp' i], input[name*='code' i], "
                                    "input[placeholder*='otp' i], "
                                    "input[maxlength='6'], input[maxlength='4'], "
                                    "input[type='tel'], input[type='number'][maxlength]"
                                ).first
                                if await _il.count() > 0:
                                    await _il.click()
                                    await _il.fill(_nc)
                                    await page.wait_for_timeout(300)
                                    try:
                                        await _il.press("Enter")
                                    except Exception:
                                        pass
                                    await page.wait_for_timeout(400)
                                    _stxt2 = await _submit_click(page, frame=_frl)
                                    if _stxt2:
                                        print(f"  3DS: SUBMIT нажат «{_stxt2[:20]}»")
                                    _otp_submitted = True
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass
                if _otp_submitted:
                    break
                await asyncio.sleep(3)
            # Цикл завершился — проверяем причину
            if not _otp_submitted and "flipkart.com" not in page.url:
                print(f"  {R}❌ Оплата не прошла — время ожидания OTP (15 мин) истекло{RST}")
                return "otp_timeout"
    else:
        print(f"  {Y}⚠ Поле ввода OTP не найдено — проверяю отказ карты...{RST}")
        # URL /retry или /error от PayGlocal — однозначный признак отказа
        _h3ds_url = page.url
        _is_declined = "/retry" in _h3ds_url or "/error" in _h3ds_url
        if not _is_declined:
            try:
                _body_low = ""
                for _fr in [page] + list(page.frames):
                    try:
                        _body_low += (await _fr.evaluate(
                            "() => (document.body && document.body.innerText || '').toLowerCase()")) or ""
                    except Exception:
                        pass
                _is_declined = any(d in _body_low for d in (
                    "declined", "payment failed", "was declined", "card provider",
                    "try another", "contact your bank"))
            except Exception:
                pass
        if _is_declined:
            print(f"  {Y}⚠ Карта отклонена банком (URL: {_h3ds_url[:60]}){RST}")
            try:
                _card_rows = _build_card_rows_3ds()
                _msg_declined = "❌ *Карта отклонена банком*\n\nБот автоматически повторит попытку."
                if _card_rows:
                    _msg_declined += "\n\n_Доступные карты:_"
                    _tg_send_direct_kb(_msg_declined, {"inline_keyboard": _card_rows})
                else:
                    _tg_send_direct(_msg_declined)
                print("  3DS: уведомление об отказе карты отправлено в TG")
            except Exception as _ntf_err2:
                print(f"  {R}3DS: ошибка уведомления об отказе: {_ntf_err2}{RST}")
            return "declined"
        print(f"  {Y}⚠ Действуй вручную.{RST}")

    # Ждём возврата на Flipkart (до 60 сек), если ещё не там
    if "flipkart.com" not in page.url:
        try:
            await page.wait_for_url(lambda u: "flipkart.com" in u, timeout=60_000)
            print(f"  3DS пройден, возврат на Flipkart.")
        except Exception:
            print(f"  3DS: редиректа нет — перехожу на flipkart-black-store...")
            try:
                await page.goto("https://www.flipkart.com/flipkart-black-store",
                                wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                pass

    return True


async def _get_3ds_otp_from_telegram() -> str | None:
    """
    Читает 3DS OTP из файла data/tg_otp_3ds.json, который пишет bot.py.
    bot.py перехватывает входящие сообщения с 4-8 цифрами и сохраняет туда.
    Fallback: прямой polling getUpdates (если bot.py не запущен).
    """
    import re as _re
    import httpx as _httpx
    import yaml as _yaml
    import json as _json

    _OTP_FILE = _HERE / "data" / "tg_otp_3ds.json"

    try:
        with open("config.yaml", encoding="utf-8") as _f:
            _cfg = _yaml.safe_load(_f) or {}
    except Exception:
        _cfg = {}

    wait_sec = int(_cfg.get("telegram_otp", {}).get("wait_timeout", 1200))
    print(f"  {Y}Жду OTP из Telegram (до {wait_sec // 60} мин)...{RST}")

    # Файл НЕ очищаем — код мог прийти ещё до появления поля 3DS. Старые коды
    # сбрасываются один раз в начале оплаты (_enter_card_on_payments).

    deadline = asyncio.get_running_loop().time() + wait_sec

    # Основной путь: читаем из файла, который пишет bot.py
    while asyncio.get_running_loop().time() < deadline:
        _ckcancel()
        # Пользователь выбрал другую карту через TG
        if _switch_card_ev.is_set():
            print(f"  {Y}⚠ Смена карты из TG — прерываю ожидание OTP{RST}")
            return "switch_card"
        try:
            codes = _json.loads(_OTP_FILE.read_text(encoding="utf-8"))
            if codes:
                code = codes.pop(0)
                # Обновляем файл (убираем использованный код)
                _OTP_FILE.write_text(_json.dumps(codes, ensure_ascii=False), encoding="utf-8")
                print("  3DS OTP получен")
                _tg_send_direct(f"🔑 *OTP получен:* `{code}` — ввожу на странице...")
                return code
        except Exception:
            pass
        await asyncio.sleep(2)

    return None


async def _enter_card_on_payments(page, card: dict, _decline_attempt: int = 0) -> bool:
    """
    На странице payments (flipkart.com/payments):
      1. Кликает «Credit / Debit / ATM Card» в левой панели.
      2. Ждёт форму (Card Number / Valid Thru / CVV) в центре.
      3. Заполняет поля и нажимает «Pay ₹XXX» или «Add Address & Pay».
    Возвращает True если карта была введена.
    """
    import random as _r

    # ── 1. Кликаем «Credit / Debit / ATM Card» в левой панели ────────────────
    # Ждём загрузки списка способов оплаты через wait_for_function
    try:
        await page.wait_for_function("""() => {
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t.includes('credit') && t.includes('debit')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 30) return true;
                }
            }
            return false;
        }""", timeout=15_000)
    except Exception:
        pass

    # Кликаем через Playwright mouse (как Continue — элемент может быть SPAN/DIV)
    bbox = None
    try:
        bbox = await page.evaluate("""() => {
            const kw = ['credit / debit / atm card', 'credit/debit/atm card', 'credit card'];
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!kw.some(k => t === k)) continue;
                const r = el.getBoundingClientRect();
                if (r.width >= 50 && r.height >= 10) return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
            // Fallback: содержит слова Credit+Debit, небольшой элемент
            for (const el of document.querySelectorAll('span, li, div, a')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!t.includes('credit') || !t.includes('debit')) continue;
                if (t.length > 80) continue;
                const r = el.getBoundingClientRect();
                if (r.width >= 50 && r.height >= 10) return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
            return null;
        }""")
    except Exception:
        pass

    if bbox:
        await page.mouse.click(bbox["x"], bbox["y"])
        await page.wait_for_timeout(1_500)
    else:
        return False

    # ── 2. Ждём поле «Card Number» (placeholder «XXXX XXXX XXXX XXXX») ────────
    num_inp = page.locator(
        "input[placeholder='XXXX XXXX XXXX XXXX'], "
        "input[placeholder*='XXXX' i], "
        "input[placeholder*='Card Number' i]"
    ).first
    try:
        await num_inp.wait_for(state="visible", timeout=8_000)
    except Exception:
        return False

    # ── 3. Card Number — retry до 3 раз ──────────────────────────────────────
    raw = card.get("number", "").replace(" ", "").replace("-", "")
    card_val = ""
    for _fa in range(3):
        await _human_click(page, num_inp, before=_r.uniform(0.1, 0.25))
        await num_inp.click(click_count=3)
        await page.wait_for_timeout(100)
        for i, ch in enumerate(raw):
            await page.keyboard.type(ch)
            await asyncio.sleep(_r.uniform(0.05, 0.11))
            if (i + 1) % 4 == 0 and i + 1 < len(raw):
                await asyncio.sleep(_r.uniform(0.08, 0.18))
        await page.wait_for_timeout(500)
        card_val = (await num_inp.input_value()).replace(" ", "")
        if card_val:
            break
        print(f"  {Y}⚠ Номер карты не введён (попытка {_fa+1}/3) — повторяю{RST}")
    if not card_val:
        await page.screenshot(path="debug/debug_card_fail.png")
        print(f"  {Y}⚠ Не удалось ввести номер карты — скриншот: debug_card_fail.png{RST}")
        return False
    # Tab + явный dispatch blur/change — React-формы могут слушать синтетические события
    try:
        await num_inp.dispatch_event("blur")
        await num_inp.dispatch_event("change")
    except Exception:
        pass
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(_r.randint(300, 600))

    # ── 4. Valid Thru — retry до 3 раз ───────────────────────────────────────
    exp_raw = card.get("expiry", "").replace(" ", "")  # "06/31"
    exp_digits = exp_raw.replace("/", "")               # "0631"
    valid_inp = page.locator(
        "input[placeholder='MM / YY'], "
        "input[placeholder='MM/YY'], "
        "input[placeholder*='MM' i], "
        "input[placeholder*='Valid' i], "
        "input[placeholder*='Expiry' i]"
    ).first
    valid_val = "ok"
    if await valid_inp.count() > 0:
        for _fa in range(3):
            await _human_click(page, valid_inp, before=_r.uniform(0.15, 0.3))
            await valid_inp.click(click_count=3)
            await page.wait_for_timeout(120)
            for ch in exp_digits:
                await page.keyboard.type(ch)
                await asyncio.sleep(_r.uniform(0.07, 0.15))
            await page.wait_for_timeout(300)
            valid_val = (await valid_inp.input_value())
            if valid_val:
                break
            print(f"  {Y}⚠ Дата карты не введена (попытка {_fa+1}/3) — повторяю{RST}")
        if not valid_val:
            await page.screenshot(path="debug/debug_card_fail.png")
            print(f"  {Y}⚠ Не удалось ввести дату — debug_card_fail.png{RST}")
            return False
    # Tab + dispatch blur/change для срока
    try:
        await valid_inp.dispatch_event("blur")
        await valid_inp.dispatch_event("change")
    except Exception:
        pass
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(_r.randint(250, 500))

    # ── 5. CVV — retry до 3 раз ──────────────────────────────────────────────
    cvv_inp = page.locator(
        "input[placeholder='CVV'], "
        "input[placeholder*='CVV' i], "
        "input[placeholder*='CVC' i]"
    ).first
    cvv_val = "ok"
    if await cvv_inp.count() > 0:
        for _fa in range(3):
            await _human_click(page, cvv_inp, before=_r.uniform(0.1, 0.25))
            await cvv_inp.click(click_count=3)
            await page.wait_for_timeout(100)
            for ch in card.get("cvv", ""):
                await page.keyboard.type(ch)
                await asyncio.sleep(_r.uniform(0.09, 0.18))
            await page.wait_for_timeout(500)
            cvv_val = (await cvv_inp.input_value())
            if cvv_val:
                break
            print(f"  {Y}⚠ CVV не введён (попытка {_fa+1}/3) — повторяю{RST}")
        if not cvv_val:
            await page.screenshot(path="debug/debug_card_fail.png")
            print(f"  {Y}⚠ Не удалось ввести CVV — debug_card_fail.png{RST}")
            return False
    # Tab + dispatch blur/change для CVV — финальный триггер валидации формы
    try:
        await cvv_inp.dispatch_event("blur")
        await cvv_inp.dispatch_event("change")
    except Exception:
        pass
    await page.keyboard.press("Tab")
    await page.wait_for_timeout(_r.randint(600, 1_000))

    # ── 6. Лог введённых данных ───────────────────────────────────────────────
    print(f"  Карта: {_mask_card(raw)} | Срок: {valid_val} | CVV: {'*'*len(cvv_val) if cvv_val != 'ok' else 'skip'}")

    # ── 7. Нажимаем Pay-кнопку — retry до 3 раз ──────────────────────────────
    # Возможные тексты: «Pay ₹343», «Add Address & Pay», «PAY»
    pay_btn = page.locator(
        "button:has-text('Add Address & Pay'), "
        "button:has-text('Pay ₹'), "
        "button:has-text('PAY'), "
        "button:has-text('Pay')"
    ).last

    # Ждём пока кнопка станет активной (не disabled) — до 5 сек
    try:
        await page.wait_for_function("""() => {
            const sels = ['button[class*="pay" i]','button[class*="btn" i]',
                          'button','[role="button"]'];
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    const t = (el.innerText || '').toLowerCase();
                    if (!t.includes('pay') && !t.includes('address')) continue;
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width > 60 && r.height > 20) return true;
                }
            }
            return false;
        }""", timeout=5_000)
    except Exception:
        pass

    pay_clicked_ok = False
    for _fa in range(3):
        if await pay_btn.count() == 0:
            await page.wait_for_timeout(1_000)
            continue
        btn_text = (await pay_btn.inner_text()).strip()
        btn_text_short = btn_text.split('\n')[0].strip()
        print(f"  Нажимаю: «{btn_text_short}» (попытка {_fa+1}/3)")
        try:
            await _human_click(page, pay_btn, before=_r.uniform(0.5, 0.9))
            pay_clicked_ok = True
        except Exception as _e:
            print(f"  {Y}⚠ Ошибка клика по Pay: {_e}{RST}")
        await page.wait_for_timeout(1_200)
        # URL изменился — ушли с payments → шлюз
        if "flipkart.com/payments" not in page.url:
            break
        # "Add Address & Pay" открывает попап — это нормально, не retry
        if "Add Address & Pay" in btn_text_short or "Address" in btn_text_short:
            pay_clicked_ok = True
            break
        pay_clicked_ok = False
    if not pay_clicked_ok:
        await page.screenshot(path="debug/debug_card_fail.png")
        print(f"  {Y}⚠ Pay-кнопка не сработала — debug_card_fail.png{RST}")

    print(f"  Карта введена: {_mask_card(raw)}")

    # Сохраняем URL перед переходом на платёжный шлюз (нужен для возврата при decline)
    _saved_payments_url = page.url

    # ── 8. Если появился попап «Add Address» — заполняем ─────────────────────
    popup_result = await _fill_billing_address_popup(page, card)

    # ── 9. Платёжный шлюз (PayGlocal / PayU / Paytm): выбор INR, Pay ────────
    gw_result = None
    if not popup_result:
        gw_result = await _handle_paytm_currency_page(page)
    elif popup_result == "declined":
        gw_result = "declined"
    elif popup_result == "otp_required":
        gw_result = "otp_required"
    elif popup_result == "otp_timeout":
        gw_result = "otp_timeout"

    # ── 10. Карта отклонена — возвращаемся на payments и повторяем ввод ──────
    if gw_result == "declined":
        if _decline_attempt >= 4:
            print(f"  {Y}⚠ Карта отклонена 5 раз — прекращаю попытки с этой картой{RST}")
            return False
        print(f"  {Y}Карта отклонена ({_decline_attempt+1}/5) — возвращаюсь на payments...{RST}")
        # Пауза перед повтором: банк может блокировать слишком быстрые попытки (velocity)
        _retry_pause = 3_000 + _decline_attempt * 2_000  # 3s, 5s, 7s, 9s
        await page.wait_for_timeout(_retry_pause)
        # Проверяем — удалось ли вернуться на Flipkart
        if "flipkart.com" not in page.url:
            # Fallback: переходим напрямую по сохранённому URL
            if "flipkart.com" in _saved_payments_url:
                print(f"  go_back не сработал — перехожу напрямую на payments...")
                try:
                    await page.goto(_saved_payments_url, wait_until="domcontentloaded",
                                    timeout=15_000)
                    await page.wait_for_timeout(2_000)
                except Exception as _e:
                    print(f"  {Y}⚠ goto payments failed: {_e}{RST}")
        if "flipkart.com" in page.url:
            print("  Повторяю ввод карты...")
            return await _enter_card_on_payments(page, card, _decline_attempt + 1)
        print(f"  {Y}⚠ Не удалось вернуться на Flipkart после отклонения{RST}")
        return False

    # Недостаточно средств на карте — передаём выше для смены карты
    if gw_result == "insufficient_funds":
        return "insufficient_funds"

    # OTP-верификация — карта принята, ждём ручного подтверждения
    if gw_result == "otp_required":
        print(f"  {G}✅ Карта принята, браузер открыт для ввода OTP{RST}")
        return "otp_required"

    # Время ожидания OTP истекло — оплата не прошла
    if gw_result == "otp_timeout":
        return "otp_timeout"

    # Если шлюз вернул False (Pay INR не найдена) — сообщаем как отклонение
    if gw_result is False:
        print(f"  {Y}⚠ Кнопка Pay INR не найдена на шлюзе — treat as decline{RST}")
        return False

    return True


async def _send_cookies_tg(ctx, profile_name: str, phone: str = "") -> None:
    """Отправляет куки flipkart.com в Telegram в форматах:
    1) Документ (JSON файл)
    2) JSON текст в <code> для копирования на телефоне (чанками)
    """
    import json as _jm, io
    try:
        tg_token = ""
        try:
            tg_token = _get_telegram_token()
        except Exception:
            return
        if not tg_token:
            return

        chat_ids: list[int] = []
        if TG_SUBSCRIBERS_FILE.exists():
            try:
                d = _jm.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
                chat_ids = [int(c) for c in (d.get("chats", []) if isinstance(d, dict) else d)]
            except Exception:
                pass
        if not chat_ids:
            return

        raw = await ctx.cookies()
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

        cookies_json = _jm.dumps(cookies_out, ensure_ascii=False, indent=2)
        cookies_json_compact = _jm.dumps(cookies_out, ensure_ascii=False, separators=(",", ":"))

        # Локальный бэкап куков на диск — для восстановления профиля без TG
        try:
            _bk_dir = Path("cookies_backup")
            _bk_dir.mkdir(exist_ok=True)
            _bk_name = f"cookies_{phone or profile_name.replace('_','')}.json"
            (_bk_dir / _bk_name).write_text(cookies_json, encoding="utf-8")
        except Exception:
            pass

        label_phone = (phone if phone else profile_name).replace("+91", "").replace("profile_", "").strip()
        phone_code = f"<code>{label_phone}</code>"
        caption = f"🍪 Файл кук <code>{label_phone}</code> ({len(cookies_out)} шт.)"
        fname = f"cookies_{phone or profile_name.replace('_','')}.json"

        def escape_html(t: str) -> str:
            return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        safe_json = escape_html(cookies_json_compact)
        MAX_CHUNK = 4000
        json_chunks = [safe_json[i:i+MAX_CHUNK] for i in range(0, len(safe_json), MAX_CHUNK)]

        import httpx as _hx
        async with _hx.AsyncClient(timeout=15, trust_env=False) as _s:
            api = f"https://api.telegram.org/bot{tg_token}"
            for cid in chat_ids:
                try:
                    # 1. Отправка файла
                    await _s.post(f"{api}/sendDocument",
                        data={"chat_id": str(cid), "caption": caption, "parse_mode": "HTML"},
                        files={"document": (fname, io.BytesIO(cookies_json.encode("utf-8")), "application/json")})

                    # 2. Отправка JSON кук текстом
                    for i, chunk in enumerate(json_chunks):
                        header = f"Куки {label_phone} ({len(cookies_out)} шт.)"
                        if len(json_chunks) > 1:
                            header += f" (часть {i+1}/{len(json_chunks)})"
                        msg = f"{header}\n<pre><code class=\"language-json\">{chunk}</code></pre>"
                        await _s.post(f"{api}/sendMessage",
                                      json={"chat_id": cid, "text": msg, "parse_mode": "HTML"})
                    print(f"  TG cookies [{cid}]: файл + компактный JSON ({len(cookies_out)} кук)")
                except Exception as _ce:
                    print(f"  TG cookies [{cid}]: {_ce}")
    except Exception as _e:
        print(f"  TG cookies: {_e}")


async def _restore_profile_from_cookies(cookies_json_path: Path, phone: str,
                                        profile_path: Path | None = None) -> tuple[bool, str]:
    """
    Импортирует куки Flipkart из JSON-файла в Chrome-профиль (обновляет сессию).
    Проверяет что вход выполнен (имя пользователя на странице).
    profile_path — обновить именно этот профиль (иначе ищется/создаётся по номеру).
    Возвращает (ok, message).
    """
    from playwright.async_api import async_playwright
    import json as _jc

    try:
        raw = _jc.loads(cookies_json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"Не удалось прочитать JSON: {e}"

    # Нормализуем формат куков для Playwright
    pw_cookies = []
    for c in raw:
        sam = c.get("sameSite") or c.get("same_site") or "no_restriction"
        sam_map = {"no_restriction": "None", "lax": "Lax", "strict": "Strict",
                   "Lax": "Lax", "Strict": "Strict", "None": "None"}
        exp = c.get("expirationDate") or c.get("expires") or -1
        pw_c = {
            "name":     c["name"],
            "value":    c["value"],
            "domain":   c.get("domain", ".flipkart.com"),
            "path":     c.get("path", "/"),
            "secure":   bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
            "sameSite": sam_map.get(str(sam), "None"),
        }
        if exp and exp > 0:
            pw_c["expires"] = int(exp)
        pw_cookies.append(pw_c)

    if not pw_cookies:
        return False, "JSON пустой — куки не найдены"

    # Определяем папку профиля
    phone_digits = "".join(filter(str.isdigit, phone))
    if profile_path is not None:
        # Явно указанный профиль (обновляем сессию на месте, без дублей)
        print(f"  Обновляю сессию профиля: {Path(profile_path).name}")
        profile_path = Path(profile_path)
    else:
        existing = sorted(DONE_PROFILES_DIR.glob(f"profile_*{phone_digits}"))
        if existing:
            profile_path = existing[0]
            print(f"  Профиль уже существует: {profile_path.name} — перезаписываю куки")
        else:
            idx = len(list(DONE_PROFILES_DIR.glob("profile_*"))) + 1
            profile_path = DONE_PROFILES_DIR / f"profile_{idx:04d}_{phone_digits}"

    profile_path.mkdir(parents=True, exist_ok=True)

    pw = None
    ctx = None
    try:
        if _vpn_extension_dir():
            if not await _ensure_extension_in_profile(profile_path):
                return False, "VPN-расширение не установлено в профиль"
            await _vpn_chrome_cooldown(extra=0.5)
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile_path)
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            **_browser_launch_kw(phone=phone_digits, profile_path=profile_path))
        page = await _main_work_page(ctx)
        _register_purchase_profile(profile_path)
        if _vpn_extension_dir() and not await _vpn_connect_on_use(ctx, profile_path):
            return False, "VPN не подключился — восстановление куков отменено"

        # Открываем Flipkart чтобы установить домен, затем добавляем куки
        await page.goto("https://www.flipkart.com/", wait_until="domcontentloaded", timeout=20_000)
        await ctx.add_cookies(pw_cookies)
        await page.reload(wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        await page.wait_for_timeout(3_000)

        # Проверяем вход НАДЁЖНО: по наличию видимой кнопки "Login".
        # (innerText-эвристика давала ложный успех — "orders"/"login" есть и у гостя,
        # а fallback «любой flipkart-URL без /login» проходил почти всегда.)
        try:
            _has_login_btn = await page.evaluate("""() => {
                for (const el of document.querySelectorAll(
                        'button,a,div,span,[role="button"]')) {
                    const t = (el.innerText || '').trim();
                    if (t !== 'Login') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width > 20 && r.height > 8) return true;
                }
                return false;
            }""")
        except Exception:
            _has_login_btn = False
        body = (await page.evaluate("() => document.body?.innerText || ''")).lower()
        _acct = any(w in body for w in ["my account", "my profile", "logout",
                                        "мой аккаунт", "выйти"])
        logged_in = _acct or not _has_login_btn

        if not logged_in:
            return False, "Куки не дали входа — сессия недействительна (нужен свежий вход)"

        # Сохраняем метаданные — СЛИВАЕМ с существующими, чтобы не потерять
        # link_history, привязку к заказу (issued_invoice_id), ссылки и т.п.
        _meta_file = profile_path / ".profile_meta.json"
        try:
            meta = json.loads(_meta_file.read_text(encoding="utf-8")) if _meta_file.exists() else {}
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}
        meta.update({
            "username": phone_digits,
            "login_ts": time.time(),
            "site_url": "https://www.flipkart.com/account/login",
            "profile_name": profile_path.name,
            "restored_from_cookies": str(cookies_json_path.name),
        })
        _atomic_write_text(_meta_file, json.dumps(meta, ensure_ascii=False, indent=2))

        return True, profile_path.name

    except Exception as e:
        return False, str(e)
    finally:
        await _close_browser_session(ctx, pw, profile_path, disconnect_vpn=True)


async def _handle_post_payment(page, ctx, profile_path: "Path", phone_number: str = "", months: int = 3) -> dict:
    """
    После успешной оплаты:
    1. Ждём 45 сек
    2. Открываем flipkart.com/flipkart-black-store → дата подписки
    3. Нажимаем Activate Now (refresh пока не появится)
    4. Копируем URL активации
    5. Сокращаем через clck.ru
    6. Уведомляем Telegram + консоль
    7. Сохраняем в профиль
    """
    import json as _json
    import httpx as _httpx

    result: dict = {"valid_till": "", "short_link": "", "activation_url": "", "paid": False}

    # Ждем до 10 секунд перенаправления на Flipkart и прогрузки страницы после оплаты
    print("  Ожидание перенаправления и прогрузки страницы после оплаты (лимит 10 сек)...")
    for _sec in range(10):
        cur_url = page.url
        if "flipkart.com" in cur_url:
            try:
                body_text = (await page.evaluate("() => document.body.innerText") or "").lower()
                # Если уже на странице успеха или ошибки, выходим из ожидания
                if ("orderresponse" in cur_url 
                        or "welcome to black" in body_text 
                        or "flipkart-black-store" in cur_url
                        or "payment failed" in body_text
                        or "payment unsuccessful" in body_text
                        or "transaction failed" in body_text
                        or any(kw in cur_url for kw in ("/payments", "payment-failed", "checkout-failed", "payment_failure"))):
                    break
            except Exception:
                pass
        await page.wait_for_timeout(1_000)

    # Проверяем что вернулись на Flipkart (шлюз должен был перенаправить)
    if "flipkart.com" not in page.url:
        print(f"  {Y}⚠ Оплата не подтверждена: URL={page.url[:60]}{RST}")
        result["error"] = f"no_redirect:{page.url[:60]}"
        return result

    cur_url = page.url
    print(f"  Flipkart URL после оплаты: {cur_url[:100]}")
    try:
        await page.screenshot(path="debug/debug_after_payment.png")
    except Exception:
        pass

    # «Due to inactivity … unable to process the transaction» на странице оплаты —
    # по факту оплата ПРОШЛА (страница просто протухла). Не считаем провалом:
    # идём на flipkart-black-store, ждём 90 сек, обновляем и забираем ссылку.
    _inactivity = False
    try:
        _bt0 = (await page.evaluate("() => document.body.innerText") or "").lower()
        if ("due to inactivity" in _bt0
                or "unable to process the transaction" in _bt0):
            _inactivity = True
            print(f"  {G}✅ «Due to inactivity» — оплата прошла, иду на black-store за ссылкой...{RST}")
    except Exception:
        pass

    # Ранняя проверка: если вернулись на страницу payments или Paytm error — отклонён
    # (кроме случая «Due to inactivity» — там оплата прошла).
    _FK_FAIL_URLS = ("/payments", "payment-failed", "checkout-failed", "payment_failure")
    if not _inactivity and any(kw in cur_url for kw in _FK_FAIL_URLS):
        print(f"  {Y}⚠ Redirect на страницу оплаты/ошибки — платёж отклонён{RST}")
        result["error"] = f"payment_fail_redirect:{cur_url[:60]}"
        _send_tg_error(phone_number, "Redirect на страницу ошибки — платёж отклонён")
        return result
    if "uiscoop.flipkart.com" in cur_url and "errorMessage" in cur_url:
        import urllib.parse as _up
        _err = _up.unquote(cur_url.split("errorMessage=")[-1].split("&")[0][:80])
        print(f"  {Y}⚠ Paytm ошибка: {_err}{RST}")
        result["error"] = f"paytm_error:{_err[:40]}"
        _send_tg_error(phone_number, f"Paytm ошибка: {_err[:60]}")
        return result

    # Проверяем текст страницы на признаки отклонения
    try:
        body_text = (await page.evaluate("() => document.body.innerText") or "").lower()
        _FAIL_KW = ("payment failed", "payment unsuccessful", "order not placed",
                    "transaction failed", "payment could not be processed")
        if any(kw in body_text for kw in _FAIL_KW):
            print(f"  {Y}⚠ Страница содержит признаки отклонения платежа{RST}")
            result["error"] = "payment_page_text_failure"
            _send_tg_error(phone_number, "Платёж отклонён (payment failed на странице)")
            return result
    except Exception:
        pass

    # Успех ТОЛЬКО если Flipkart перебросил на страницу подтверждения заказа
    # («Welcome to BLACK» / orderresponse). Просто нахождение на flipkart-black-store
    # успехом НЕ считается: туда 3DS-хендлер переходит и при неудаче (например, не
    # хватило денег) — иначе ранее это давало ложный «оплата прошла».
    _black_banner_link = ""
    _banner_confirmed = False
    try:
        _order_html = (await page.evaluate("() => document.documentElement.innerHTML")) or ""
        _order_body = (await page.evaluate("() => (document.body.innerText||'')")).lower()
        if "orderresponse" in cur_url or "welcome to black" in _order_body:
            _banner_confirmed = True
            print(f"  {G}✅ «Welcome to BLACK» — оплата подтверждена{RST}")
            result["paid"] = True
            # Ищем точную ссылку на Black Store в HTML
            import re as _re_bp
            _m = _re_bp.search(r'href="([^"]*flipkart-black-store[^"]*)"', _order_html)
            if _m:
                _black_banner_link = _m.group(1)
                if _black_banner_link.startswith("/"):
                    _black_banner_link = "https://www.flipkart.com" + _black_banner_link
                print(f"  {G}🔗 Ссылка на Black Store из баннера: {_black_banner_link[:80]}{RST}")
    except Exception:
        pass

    # Нет баннера успешной оплаты — транзакция НЕ успешна (например, не хватило денег).
    # Исключение: «Due to inactivity» после Submit — оплата прошла, но баннер не
    # показали; тогда идём на black-store за ссылкой (успех подтвердит подписка/Activate).
    if not _banner_confirmed and not _inactivity:
        print(f"  {Y}⚠ Нет баннера успешной оплаты — транзакция не подтверждена{RST}")
        result["error"] = "no_success_banner"
        _send_tg_error(phone_number, "Нет баннера успешной оплаты — оплата не прошла")
        return result

    # ── 1. Открываем flipkart-black-store (или используем текущую страницу) ──
    _black_url = _black_banner_link or "https://www.flipkart.com/flipkart-black-store"
    if "flipkart-black-store" in cur_url:
        # Уже там (перешли из 3DS-хендлера) — повторно не ждём
        black_page = page
    else:
        print(f"  Оплата подтверждена — ждём 90 сек для активации membership...")
        await _cancellable_wait(page, 90_000)
        black_page = await ctx.new_page()
        try:
            await black_page.goto(_black_url, wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass
        await black_page.wait_for_timeout(3_000)

    # ── 2. Читаем дату подписки ───────────────────────────────────────────────
    _VALID_TILL_JS = """() => {
        for (const el of document.querySelectorAll('*')) {
            const t = (el.innerText || el.textContent || '').trim();
            if (!/membership valid till/i.test(t) || t.length > 200) continue;
            // "31 May 2025" / "31-May-2025" / "31/May/2025"
            let m = t.match(/\\d{1,2}[\\s\\-/]+[A-Za-z]+[\\s\\-/]+\\d{2,4}/);
            if (m) return m[0];
            // "31/05/2025" / "31-05-2025"
            m = t.match(/\\d{1,2}[\\s\\-/]+\\d{1,2}[\\s\\-/]+\\d{2,4}/);
            if (m) return m[0];
            // "May 31, 2025" / "May 31 2025"
            m = t.match(/[A-Za-z]+[\\s]+\\d{1,2}[,\\s]+\\d{2,4}/);
            if (m) return m[0];
            // Bare date digits anywhere in the element text
            m = t.match(/\\d{1,2}[\\s\\-/]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[A-Za-z]*[\\s\\-/]+\\d{2,4}/i);
            if (m) return m[0];
        }
        return '';
    }"""
    valid_till = ""
    for _ in range(8):
        try:
            valid_till = (await black_page.evaluate(_VALID_TILL_JS) or "").strip()
            if valid_till:
                break
        except Exception:
            pass
        await black_page.wait_for_timeout(2_000)
    result["valid_till"] = valid_till
    if valid_till:
        result["paid"] = True
        result["button_seen"] = f"Membership valid till {valid_till}"

    # ── 3. Нажимаем Activate Now (JS-клик, refresh если Explore Now) ───────────
    _ACTIVATE_JS = """() => {
        for (const img of document.querySelectorAll('img[width="1200"]')) {
            if (img.getAttribute('height') !== '213') continue;
            if (!(img.src||'').includes('/promos/')) continue;
            let el = img.parentElement;
            for (let i = 0; i < 6 && el; i++) {
                if (el.style && el.style.cursor === 'pointer') {
                    el.scrollIntoView({behavior:'instant', block:'center'});
                    el.click(); return 'img-1200x213';
                }
                el = el.parentElement;
            }
            img.scrollIntoView({behavior:'instant', block:'center'});
            img.click(); return 'img-direct';
        }
        return null;
    }"""

    async def _click_activate_now_js() -> str | None:
        """JS-клик по Activate Now (PNG img 1200x213). Возвращает activation_url или None."""
        try:
            _new_page_ev = asyncio.ensure_future(ctx.wait_for_event("page", timeout=10_000))
            _method = await black_page.evaluate(_ACTIVATE_JS)
            if not _method:
                _new_page_ev.cancel()
                return None
            print(f"  {G}✅ Activate Now нажата ({_method}){RST}")
            try:
                _tab = await _new_page_ev
                await _tab.wait_for_load_state("domcontentloaded", timeout=12_000)
                return _tab.url
            except Exception:
                _new_page_ev.cancel()
                await black_page.wait_for_timeout(3_000)
                if "flipkart-black-store" not in black_page.url:
                    return black_page.url
        except Exception as _je:
            print(f"  {Y}⚠ JS-клик ошибка: {_je}{RST}")
        return None

    import time as _time_act
    activation_url = ""
    _act_start = _time_act.time()
    _act_timeout = 90  # секунд
    while True:
        _elapsed = int(_time_act.time() - _act_start)
        if _elapsed >= _act_timeout:
            break

        # Скролл для загрузки lazy-элементов
        for _sp in [0.3, 0.6, 1.0, 0.0]:
            try:
                await black_page.evaluate(f"window.scrollTo(0, document.body.scrollHeight*{_sp})")
                await black_page.wait_for_timeout(300)
            except Exception:
                pass

        # Пробуем JS-клик по Activate Now
        _url_js = await _click_activate_now_js()
        if _url_js:
            result["paid"] = True
            result["button_seen"] = "Activate Now"
            activation_url = _url_js
            result["activation_url"] = activation_url
            print(f"  {G}🔗 Ссылка: {activation_url[:80]}...{RST}")
            break

        # Кнопка не найдена — проверяем Explore Now (reload)
        try:
            _hp = await black_page.evaluate("() => document.documentElement.innerHTML")
            if "black-youtube-premium-benefit-faq-store" in _hp:
                result["paid"] = True
                result["button_seen"] = "Explore Now"
                print(f"  «Explore Now» — обновляю страницу ({_elapsed} сек)...")
                try:
                    await black_page.reload(wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass
                await black_page.wait_for_timeout(3_000)
                continue
        except Exception:
            pass

        print(f"  Ожидание кнопки Activate Now... ({_elapsed} сек / {_act_timeout} сек)")
        try:
            await black_page.reload(wait_until="domcontentloaded", timeout=15_000)
        except Exception:
            pass
        await black_page.wait_for_timeout(5_000)

    # ── 4. Сокращаем через clck.ru ────────────────────────────────────────────
    short_link = activation_url
    if activation_url:
        clck_page = await ctx.new_page()
        try:
            await clck_page.goto("https://clck.ru/", wait_until="domcontentloaded", timeout=15_000)
            await clck_page.wait_for_timeout(2_000)

            inp = clck_page.locator("input[name='url'], input[type='url'], input[type='text']").first
            if await inp.count() > 0:
                await inp.fill(activation_url)
                await clck_page.wait_for_timeout(400)
                # Пробуем кнопку «Сократить» иначе Enter
                sokratit_clicked = False
                try:
                    sbt = await clck_page.evaluate("""() => {
                        for (const el of document.querySelectorAll('button, input[type="submit"], a')) {
                            const t = (el.innerText || el.value || el.textContent || '').trim();
                            if (/сократ/i.test(t) || /shorten/i.test(t) || /submit/i.test(t.toLowerCase())) {
                                const r = el.getBoundingClientRect();
                                if (r.width >= 20 && r.height >= 8)
                                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                            }
                        }
                        return null;
                    }""")
                    if sbt:
                        await clck_page.mouse.click(sbt["x"], sbt["y"])
                        sokratit_clicked = True
                except Exception:
                    pass
                if not sokratit_clicked:
                    await inp.press("Enter")
                await clck_page.wait_for_timeout(4_000)

            _SHORT_JS = """() => {
                // Ищем короткую ссылку clck.ru в полях, ссылках и тексте
                for (const el of document.querySelectorAll('input, a, .result, .short-url, [class*="result"], span, div')) {
                    const v = (el.value || el.href || el.innerText || el.textContent || '').trim();
                    const m = v.match(/(?:https?:\\/\\/)?clck\\.ru\\/[A-Za-z0-9_-]+/);
                    if (m && m[0].length > 12) return m[0];
                }
                return '';
            }"""
            short = (await clck_page.evaluate(_SHORT_JS) or "").strip()
            if short:
                short_link = short if short.startswith("http") else "https://" + short

            if not short:
                surl = clck_page.url
                if "clck.ru/" in surl and surl != "https://clck.ru/":
                    short_link = surl
        except Exception as e:
            print(f"  clck.ru ошибка: {e}")
        finally:
            try:
                await clck_page.close()
            except Exception:
                pass

    result["short_link"] = short_link

    # ── 5. Консольный вывод ───────────────────────────────────────────────────
    _full_url  = result.get("activation_url", "")
    _link      = short_link or _full_url
    _has_short = short_link and short_link != _full_url
    _btn_seen  = result.get("button_seen", "не найдена")
    _tariff    = "₹1,499 · 12 мес." if months == 12 else "₹343 · 3 мес."

    print(f"\n  {'='*55}")
    print(f"  ✅ Membership valid till: {valid_till or '—'}")
    print(f"  Кнопка на странице: {_btn_seen}")
    if _full_url:
        print(f"  Ссылка активации:  {_full_url}")
    if _has_short:
        print(f"  Короткая ссылка:   {short_link}")
    elif not _full_url:
        print(f"  Ссылка активации:  не получена")
    print(f"  {'='*55}\n")

    # ── 6. Уведомление в Telegram ─────────────────────────────────────────────
    try:
        tg_token = _get_telegram_token() if _tg_notify_enabled() else ""
    except Exception:
        tg_token = ""

    # Автоматически сохраняем ссылку и время её получения в профиль
    _recv_str = ""
    try:
        if _link:
            import time as _t_bl
            _now_ts = _t_bl.time()
            _save_meta_field(
                profile_path,
                black_activation_link=_full_url or _link,
                black_short_link=short_link if _has_short else "",
                link_received_ts=_now_ts,
            )
            _recv_str = _fmt_msk(_now_ts)
    except Exception:
        pass

    _till_line  = f"\n📅 Действует до: <b>{valid_till}</b>" if valid_till else ""
    _recv_line  = f"\n🕒 Ссылка получена: <code>{_recv_str}</code>" if _recv_str else ""
    _url_line   = (f"\n\n🔗 <a href=\"{_full_url}\">{_full_url}</a>" if _full_url else
                   (f"\n\n🔗 {short_link}" if short_link else "\n\n⚠️ Ссылка активации не получена"))
    _short_line = (f"\n🔗 {short_link}" if _has_short else "")
    msg = (
        f"🎉 <b>Flipkart Black Membership</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Номер: <code>{_disp_phone(phone_number)}</code>\n"
        f"💳 Тариф: {_tariff}"
        f"{_till_line}"
        f"{_recv_line}"
        f"{_url_line}"
        f"{_short_line}"
    )
    _tg_parse_mode = "HTML"

    if tg_token and result.get("paid"):
        try:
            subs_path = TG_SUBSCRIBERS_FILE
            chat_ids: list = []
            if subs_path.exists():
                d = _json.loads(subs_path.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    chat_ids = [int(c) for c in d.get("chats", [])]
                elif isinstance(d, list):
                    chat_ids = [int(c) for c in d]
            _reply_markup_black = _json.dumps({"inline_keyboard": [
                [{"text": "👤 Перейти в профиль",
                  "callback_data": f"profile:menu:{phone_number}:active"}],
                [{"text": "📤 Отправить получателю",
                  "callback_data": f"profile:send_to_buyer:{phone_number}:0"}],
            ]}) if _link else None
            async with _httpx.AsyncClient(timeout=10, trust_env=False) as _sess:
                for cid in chat_ids:
                    try:
                        _payload_black = {"chat_id": cid, "text": msg,
                                          "parse_mode": _tg_parse_mode,
                                          "disable_web_page_preview": True}
                        if _reply_markup_black:
                            _payload_black["reply_markup"] = _reply_markup_black
                        resp = await _sess.post(
                            f"https://api.telegram.org/bot{tg_token}/sendMessage",
                            json=_payload_black,
                        )
                        if resp.status_code == 200:
                            print(f"  TG [{cid}]: отправлено")
                        else:
                            print(f"  TG [{cid}]: ошибка {resp.status_code}")
                    except Exception as te:
                        print(f"  TG [{cid}]: {te}")
        except Exception as tge:
            print(f"  TG рассылка: {tge}")
    elif tg_token:
        print(f"  TG: оплата не подтверждена — уведомление не отправлено")

    # ── 7. Сохраняем в профиль → статус «оплачен, готов к выдаче» ───────────
    try:
        import time as _time_m
        _bought_ts = _time_m.time()
        _expire_days = 365 if months == 12 else 90
        _expire_ts = _bought_ts + _expire_days * 86400
        _save_meta_field(
            profile_path,
            black_valid_till=valid_till,
            black_activation_link=_link,
            black_short_link=short_link if _has_short else "",
            subscription_months=months,
            subscription_bought_ts=_bought_ts,
            subscription_expires_ts=_expire_ts,
            paid_ready=True,
            button_seen=_btn_seen,
        )
        print(f"  Профиль обновлён: {profile_path / '.profile_meta.json'}")
        print(f"  Подписка: {months} мес. · до {_fmt_msk(_expire_ts)}")
    except Exception as se:
        print(f"  Ошибка сохранения профиля: {se}")

    return result


async def _read_order_total(page) -> int:
    """Читает «Total Amount ₹XXX» на странице оплаты. 0 если не найдено."""
    try:
        return int(await page.evaluate(r"""() => {
            const body = document.body ? document.body.innerText : '';
            // «Total Amount ₹343» / «Amount Payable ₹343»
            let m = body.match(/(?:total amount|amount payable|total payable|to pay)[^\d₹]*₹?\s*([\d,]+)/i);
            if (!m) m = body.match(/₹\s*([\d,]+)/);  // первый ₹-номинал как запас
            return m ? parseInt(m[1].replace(/,/g, ''), 10) : 0;
        }"""))
    except Exception:
        return 0


async def _gift_place_order_bbox(page):
    """Координаты жёлтой кнопки «Place Order» (появляется когда баланса хватает)."""
    try:
        return await page.evaluate(r"""() => {
            for (const el of document.querySelectorAll('button, a, div, span, [role="button"]')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t !== 'place order' && !(t.includes('place order') && t.length < 30)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 15) continue;
                return {x: r.x + r.width/2, y: r.y + r.height/2};
            }
            return null;
        }""")
    except Exception:
        return None


async def _click_place_order(page) -> bool:
    """Надёжно нажимает «Place Order»: locator-клик (авто-скролл) → JS click →
    клик по координатам. Возвращает True, если удалось кликнуть."""
    # 1. Playwright locator — сам скроллит к элементу и кликает по центру
    for _s in ("button:has-text('Place Order')", "button:has-text('PLACE ORDER')",
               "[role='button']:has-text('Place Order')",
               "a:has-text('Place Order')"):
        try:
            _l = page.locator(_s).last
            if (await _l.count() > 0) and (await _l.is_visible()):
                try:
                    await _l.scroll_into_view_if_needed(timeout=3_000)
                except Exception:
                    pass
                await _l.click(timeout=5_000)
                return True
        except Exception:
            pass
    # 2. JS: находим элемент по тексту и жмём .click()
    try:
        _ok = await page.evaluate(r"""() => {
            for (const el of document.querySelectorAll('button, a, div, span, [role="button"]')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t !== 'place order' && !(t.includes('place order') && t.length < 30)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 15) continue;
                el.scrollIntoView({block:'center'});
                el.click();
                return true;
            }
            return false;
        }""")
        if _ok:
            return True
    except Exception:
        pass
    # 3. Клик по координатам (со скроллом к центру)
    try:
        _bb = await _gift_place_order_bbox(page)
        if _bb:
            await page.mouse.click(_bb["x"], _bb["y"])
            return True
    except Exception:
        pass
    return False


async def _select_gift_cards_pay_method(page) -> bool:
    """Кликает слева «Have a Flipkart Gift Card?» (или «Gift Cards»).

    На payments без баланса на аккаунте пункт выглядит именно так
    («Have a Flipkart Gift Card?» + иконка подарка) — не «Gift Cards».
    """
    try:
        await page.wait_for_function(
            """() => {
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (t.includes('have a flipkart gift card')
                        || t === 'gift cards' || t === 'gift card'
                        || t === 'pay by gift card') {
                        const r = el.getBoundingClientRect();
                        if (r.width > 30 && r.height > 8) return true;
                    }
                }
                return false;
            }""",
            timeout=8_000,
        )
    except Exception:
        pass

    bbox = None
    try:
        bbox = await page.evaluate(r"""() => {
            const isSummary = (t) => /[₹]|rs\.?\s*\d|−|–/.test(t) && /\d/.test(t);
            const pick = (el) => {
                const r = el.getBoundingClientRect();
                if (r.width < 40 || r.height < 10 || el.offsetParent === null) return null;
                // Левая панель payment options — обычно x < 40% ширины окна
                if (r.x > window.innerWidth * 0.45) return null;
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            };
            // 1) Точный текст со скриншота
            const prefer = [
                'have a flipkart gift card?',
                'have a flipkart gift card',
            ];
            for (const want of prefer) {
                for (const el of document.querySelectorAll(
                    'span, div, li, a, button, label, [role="button"], p'
                )) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (t !== want && !t.startsWith(want)) continue;
                    if (t.length > 60 || isSummary(t)) continue;
                    if (/^use\s+gift/.test(t)) continue;
                    const bb = pick(el);
                    if (bb) return bb;
                }
            }
            // 2) Короткий пункт слева «Gift Cards»
            const exact = [
                'gift cards', 'gift card', 'pay by gift card', 'pay with gift card',
            ];
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!exact.includes(t) || isSummary(t) || /^use\s+gift/.test(t)) continue;
                const bb = pick(el);
                if (bb) return bb;
            }
            // 3) Fallback: содержит have a flipkart gift card
            for (const el of document.querySelectorAll(
                'span, div, li, a, button, label, [role="button"]'
            )) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!t.includes('have a flipkart gift card')) continue;
                if (t.length > 80 || isSummary(t)) continue;
                const bb = pick(el);
                if (bb) return bb;
            }
            return null;
        }""")
    except Exception:
        pass

    if not bbox:
        return False
    await page.mouse.click(bbox["x"], bbox["y"])
    await page.wait_for_timeout(1_200)
    return True


async def _use_gift_cards_checkbox_state(page) -> dict:
    """Есть ли галочка «Use Gift Cards» (баланс уже на аккаунте).

    return: {present: bool, checked: bool}
    """
    try:
        return await page.evaluate(r"""() => {
            const hit = (t) => /use\s+gifts?\s*cards?/i.test(t || '');
            for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                const box = cb.closest('div,label,li,section') || cb.parentElement;
                const t = (box ? box.innerText : '') || '';
                if (!hit(t)) continue;
                return {present: true, checked: !!cb.checked};
            }
            for (const el of document.querySelectorAll('[role="checkbox"]')) {
                const box = el.closest('div,label,li,section') || el.parentElement;
                const t = (box ? box.innerText : '') || '';
                if (!hit(t)) continue;
                return {
                    present: true,
                    checked: el.getAttribute('aria-checked') === 'true',
                };
            }
            return {present: false, checked: false};
        }""")
    except Exception:
        return {"present": False, "checked": False}


async def _tick_use_gift_cards(page) -> bool:
    """Ставит галочку «Use Gift Cards», если есть и снята. True = кликнули."""
    try:
        return bool(await page.evaluate(r"""() => {
            const hit = (t) => /use\s+gifts?\s*cards?/i.test(t || '');
            for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                const box = cb.closest('div,label,li,section') || cb.parentElement;
                const t = (box ? box.innerText : '') || '';
                if (!hit(t) || cb.checked) continue;
                cb.click();
                return true;
            }
            for (const el of document.querySelectorAll('[role="checkbox"]')) {
                const box = el.closest('div,label,li,section') || el.parentElement;
                const t = (box ? box.innerText : '') || '';
                if (!hit(t) || el.getAttribute('aria-checked') === 'true') continue;
                el.click();
                return true;
            }
            return false;
        }"""))
    except Exception:
        return False


async def _do_gift_card_payment(page, profile_path=None) -> bool | str:
    """Оплата подарочными картами (Flipkart Gift Card).

    UX payments:
      1) Если есть галочка «Use Gift Cards» — включаем баланс аккаунта,
         карты из хранилища только на остаток.
      2) Если галочки нет — слева «Gift Cards» → появляются поля ввода.
      3) После каждой добавленной карты, если поле снова скрыто —
         снова жмём «Gift Cards» слева.
    """
    import re as _rg
    await page.wait_for_timeout(1_000)

    _orig_total = await _read_order_total(page)
    if _orig_total <= 0:
        try:
            with open("config.yaml", encoding="utf-8") as _f:
                _orig_total = int((yaml.safe_load(_f) or {}).get("gift", {}).get("order_total", 343))
        except Exception:
            _orig_total = 343
    print(f"  {C}🎁 Оплата гифт-картами. Цена ₹{_orig_total}{RST}")

    # ── Шаг 0: сначала галочка «Use Gift Cards» (уже использованные на аккаунте),
    # потом добираем остаток картами из хранилища.
    _cb = {"present": False, "checked": False}
    for _ in range(12):
        _cb = await _use_gift_cards_checkbox_state(page)
        if _cb.get("present"):
            break
        # Иногда блок баланса появляется с задержкой после загрузки payments
        await page.wait_for_timeout(350)
    if _cb.get("present"):
        if not _cb.get("checked"):
            if await _tick_use_gift_cards(page):
                print(f"  {G}✔ Use Gift Cards — применяю уже использованный баланс{RST}")
            else:
                print(f"  {Y}⚠ Use Gift Cards есть, но не удалось включить{RST}")
        else:
            print(f"  {G}✔ Use Gift Cards уже включена{RST}")
        # Ждём пересчёт Total после галочки (Gift Cards −₹X → остаток)
        for _ in range(14):
            await page.wait_for_timeout(400)
            _rem_wait = await _read_order_total(page)
            if await _gift_place_order_bbox(page):
                break
            if 0 < _rem_wait < _orig_total:
                break
            st2 = await _use_gift_cards_checkbox_state(page)
            if st2.get("present") and not st2.get("checked"):
                await _tick_use_gift_cards(page)
    else:
        # Галочки нет — открываем способ оплаты Gift Cards слева
        if await _select_gift_cards_pay_method(page):
            print(f"  {G}✔ Have a Flipkart Gift Card? (слева) — открываю поля{RST}")
        else:
            print(f"  {Y}⚠ «Have a Flipkart Gift Card?» слева не найдена — пробую поля напрямую{RST}")

    _rem = await _read_order_total(page)
    if (await _gift_place_order_bbox(page)) or _rem <= 0:
        print(f"  {G}💰 Хватает уже применённого гифт-баланса — карты из хранилища не нужны{RST}")
        total = 0
    else:
        total = _rem
        if _rem < _orig_total:
            print(f"  {G}Баланс аккаунта учтён, осталось покрыть ₹{_rem} — добираю картами{RST}")
        # После галочки поля ввода часто скрыты — сразу открываем для добора
        if _cb.get("present") and total > 0:
            with contextlib.suppress(Exception):
                await _select_gift_cards_pay_method(page)
                await page.wait_for_timeout(600)
    # Гифт-картами платим кратно 50 — остаток к покрытию округляется ВВЕРХ
    _gift_need = -(-int(total) // 50) * 50 if total > 0 else 0

    # Сначала пытаемся закрыть остаток МЕЛКИМИ картами (< GIFT_CONFIRM_THRESHOLD).
    # Крупные (>= порога, обычно ₹500+) — только с подтверждением пользователя.
    _all_gc = _load_gift_cards()
    def _bal_of(pred):
        return sum(int(c.get("denom") or 0) for c in _all_gc
                   if not c.get("used") and c.get("number") and c.get("pin") and pred(int(c.get("denom") or 0)))
    _small_bal = _bal_of(lambda d: 0 < d < GIFT_CONFIRM_THRESHOLD)
    _total_bal = _bal_of(lambda d: d > 0)
    _allow_big = False

    async def _ask_big_gift_confirm(need_amt: int, small_now: int) -> bool:
        """Спросить в TG разрешение на карты ≥ GIFT_CONFIRM_THRESHOLD. True = да."""
        _all_now = _load_gift_cards()
        _big = sorted({int(c.get("denom") or 0) for c in _all_now
                       if not c.get("used") and c.get("number") and c.get("pin")
                       and int(c.get("denom") or 0) >= GIFT_CONFIRM_THRESHOLD}, reverse=True)
        if not _big:
            return False
        _big_lbl = ", ".join(f"₹{d}" for d in _big)
        print(f"  {Y}Мелких не хватает (₹{small_now} из ₹{need_amt}). Спрашиваю подтверждение на крупные…{RST}")
        _gift_big_ev.clear()
        _gift_big_choice[0] = None
        _tg_send_direct_kb(
            f"🎁 *Не хватает мелких гифт-карт*\n\n"
            f"Осталось покрыть: *₹{need_amt}*\n"
            f"Мелкими (до ₹{GIFT_CONFIRM_THRESHOLD}) есть: *₹{small_now}*\n\n"
            f"Использовать карты от *₹{GIFT_CONFIRM_THRESHOLD}*?  ({_big_lbl})",
            {"inline_keyboard": [[
                {"text": "✅ Да, использовать", "callback_data": "gift:big_yes"},
                {"text": "🚫 Нет", "callback_data": "gift:big_no"},
            ]]})
        _wait_dl = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < _wait_dl:
            _ckcancel()
            if _gift_big_ev.is_set():
                break
            await asyncio.sleep(1)
        return _gift_big_choice[0] is True

    if total <= 0:
        pass
    elif _small_bal >= total:
        print(f"  {G}Мелких гифт-карт достаточно (₹{_small_bal}) — крупные (≥₹{GIFT_CONFIRM_THRESHOLD}) не трогаю{RST}")
    elif _total_bal >= total:
        if await _ask_big_gift_confirm(total, _small_bal):
            _allow_big = True
            print(f"  {G}✔ Разрешено использовать крупные гифт-карты{RST}")
        else:
            print(f"  {Y}✘ Крупные не подтверждены — отмена оплаты гифт-картами{RST}")
            _tg_send_direct("🎁 Оплата гифт-картами отменена — использование крупных карт не подтверждено.")
            return "gift_insufficient"
    else:
        _rep, _bal, _need_r, _short = _gift_shortage_report(total)
        print(f"  {R}✘ Гифт-карт не хватает:{RST}")
        for _ln in _rep.split("\n"):
            print(f"  {R}  {_ln}{RST}")
        _tg_send_direct(f"🎁 *Не хватает гифт-карт*\n\n{_rep}\n\nДобавьте карты и повторите.")
        return "gift_insufficient"

    # ── Хелперы под реальный UX страницы оплаты ───────────────────────────────
    _NUM_SEL = (
        "input[placeholder*='voucher number' i], "
        "input[placeholder*='gift card number' i], "
        "input[placeholder*='gift card' i], "
        "input[placeholder*='enter voucher' i], "
        "input[name*='voucher' i], "
        "input[placeholder*='voucher' i]"
    )
    _PIN_SEL = (
        "input[placeholder*='voucher pin' i], "
        "input[placeholder*='gift card pin' i], "
        "input[placeholder*='security code' i], "
        "input[name*='pin' i], "
        "input[placeholder*='pin' i]"
    )

    async def _voucher_visible():
        try:
            _l = page.locator(_NUM_SEL).first
            return (await _l.count() > 0) and (await _l.is_visible())
        except Exception:
            return False

    async def _ensure_voucher_fields() -> bool:
        """Поле ввода видно? Иначе снова Gift Cards слева (+ Add Gift Card)."""
        if await _voucher_visible():
            return True
        # После применения карты секция часто сворачивается — снова Gift Cards
        if await _select_gift_cards_pay_method(page):
            for _ in range(10):
                await page.wait_for_timeout(300)
                if await _voucher_visible():
                    return True
        # Иногда нужен ещё «Add Gift Card» / «Have a Flipkart Gift Card?»
        try:
            bb = await page.evaluate(r"""() => {
                const want = [
                    'add gift card', 'have a flipkart gift card?',
                    'add a gift card', 'apply gift card', 'redeem gift card',
                ];
                for (const el of document.querySelectorAll(
                    'a,button,div,span,[role="button"]'
                )) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (!want.some(w => t === w || t.includes(w))) continue;
                    if (/use\s+gift/.test(t)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 25 || r.height < 8 || el.offsetParent === null) continue;
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
            if bb:
                await page.mouse.click(bb["x"], bb["y"])
                for _ in range(10):
                    await page.wait_for_timeout(300)
                    if await _voucher_visible():
                        return True
        except Exception:
            pass
        return await _voucher_visible()

    async def _click_add_submit():
        """Клик по кнопке «Add Gift Card» в форме/модалке (отправка купона)."""
        for _s in ("button:has-text('Add Gift Card')", "button:has-text('Add gift card')",
                   "[role='button']:has-text('Add Gift Card')"):
            try:
                _l = page.locator(_s).last
                if (await _l.count() > 0) and (await _l.is_visible()):
                    await _l.click()
                    return True
            except Exception:
                pass
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
        return False

    async def _ensure_use_checkbox():
        """Держит галочку Use Gift Cards включённой (если она есть)."""
        st = await _use_gift_cards_checkbox_state(page)
        if st.get("present") and not st.get("checked"):
            await _tick_use_gift_cards(page)

    async def _read_gift_applied():
        """Сумма, зачисленная гифт-картами (строка «Gift Cards −₹X» в сводке справа)."""
        try:
            return int(await page.evaluate(r"""() => {
                const b = document.body ? document.body.innerText : '';
                // «Gift Cards  -₹7» / «Gift Card −₹100»
                const m = b.match(/gift\s*cards?\s*[:\-–—]*\s*-?\s*₹?\s*([\d,]+)/i);
                return m ? parseInt(m[1].replace(/,/g,''),10) : 0;
            }"""))
        except Exception:
            return 0

    applied = 0
    applied_sum = 0
    _tried_bad: set = set()   # номера, отклонённые НЕ как «already used» — не повторяем
    _guard = 0
    _enough = False
    while _guard < 40:
        _guard += 1
        _ckcancel()
        # ГЛАВНОЕ: как только баланса хватает и появилась Place Order — карты
        # больше НЕ добавляем (и если остаток уже покрыт).
        if await _gift_place_order_bbox(page):
            _enough = True
            print(f"  {G}💰 Хватает — кнопка Place Order доступна, гифт-карты больше не использую{RST}")
            break
        if applied_sum >= total or (await _read_order_total(page)) <= 0:
            _enough = True
            print(f"  {G}💰 Сумма покрыта — гифт-карты больше не использую{RST}")
            break
        # Доступные карты (использованные уже удалены из хранилища), кроме «плохих».
        # Крупные (>= порога) — только если пользователь подтвердил (_allow_big).
        _avail = [gc for gc in _load_gift_cards()
                  if not gc.get("used")
                  and str(gc.get("number") or "").strip()
                  and str(gc.get("pin") or "").strip()
                  and int(gc.get("denom") or 0) > 0
                  and (_allow_big or int(gc.get("denom") or 0) < GIFT_CONFIRM_THRESHOLD)
                  and str(gc.get("number")).strip() not in _tried_bad]
        _need = total - applied_sum
        _sel, _ = _select_gift_cards(_need, _avail)
        if not _sel:
            _avail.sort(key=lambda x: int(x.get("denom") or 0), reverse=True)
            _sel = [_avail[0]] if _avail else []
        if not _sel:
            # Частый кейс: мелкие уже потрачены, осталась ₹500+ — спроси крупную снова
            _big_left = [
                gc for gc in _load_gift_cards()
                if not gc.get("used")
                and str(gc.get("number") or "").strip()
                and str(gc.get("pin") or "").strip()
                and int(gc.get("denom") or 0) >= GIFT_CONFIRM_THRESHOLD
                and str(gc.get("number")).strip() not in _tried_bad
            ]
            _big_bal = sum(int(c.get("denom") or 0) for c in _big_left)
            if not _allow_big and _big_bal >= max(1, _need):
                _small_now = sum(
                    int(c.get("denom") or 0) for c in _load_gift_cards()
                    if not c.get("used") and c.get("number") and c.get("pin")
                    and 0 < int(c.get("denom") or 0) < GIFT_CONFIRM_THRESHOLD
                    and str(c.get("number")).strip() not in _tried_bad
                )
                if await _ask_big_gift_confirm(_need, _small_now):
                    _allow_big = True
                    print(f"  {G}✔ Крупные разрешены — продолжаю на остаток ₹{_need}{RST}")
                    continue
                print(f"  {Y}✘ На остаток ₹{_need} остались только крупные (₹{_big_bal}) — не подтверждены{RST}")
                _tg_send_direct(
                    f"🎁 *Остаток ₹{_need}*\n\n"
                    f"Мелких карт больше нет. В хранилище крупные: *₹{_big_bal}*.\n"
                    f"Подтверждение не получено — оплата остановлена."
                )
                break
            _rep2, _b2, _n2, _s2 = _gift_shortage_report(_need)
            # Не вводим в заблуждение: если short==0, но выбрать нечего — явная причина
            if _s2 <= 0 and _b2 > 0:
                print(f"  {R}✘ Карты в хранилище есть (₹{_b2}), но применить не удалось{RST}")
                _tg_send_direct(
                    f"🎁 *Не удалось применить гифт-карты*\n\n"
                    f"Применено на ₹{applied_sum}, осталось покрыть ₹{_need}.\n"
                    f"В хранилище: ₹{_b2}, но подходящих для авто-оплаты нет "
                    f"(крупные без подтверждения / отклонённые).\n\n{_rep2}"
                )
            else:
                print(f"  {R}✘ Гифт-карт не хватает на остаток:{RST}")
                for _ln in _rep2.split("\n"):
                    print(f"  {R}  {_ln}{RST}")
                _tg_send_direct(
                    f"🎁 *Не хватает гифт-карт*\n\nПрименено на ₹{applied_sum}, "
                    f"осталось покрыть ₹{_need}.\n\n{_rep2}"
                )
            break
        c = _sel[0]
        _num = str(c.get("number") or "").strip()
        _pin = str(c.get("pin") or "").strip()
        _dn  = int(c.get("denom") or 0)
        print(f"  🎁 Карта {_mask_gift(_num)} (₹{_dn}); осталось покрыть ₹{_need}...")

        # Снимок «до»: сколько уже зачислено гифтом и текущий Total (для детекции успеха)
        _applied_before = await _read_gift_applied()
        _total_before = await _read_order_total(page)

        # 1. Поле ввода: если скрыто после прошлой карты — снова Gift Cards слева
        await _ensure_use_checkbox()
        _field_ready = False
        for _o in range(5):
            if await _ensure_voucher_fields():
                _field_ready = True
                break
            await page.wait_for_timeout(400)
        if not _field_ready:
            print(f"  {R}✘ Поле ввода не появилось после Gift Cards — прекращаю{RST}")
            break

        # 2. Заполняем номер и PIN
        try:
            _num_inp = page.locator(_NUM_SEL).first
            _pin_inp = page.locator(_PIN_SEL).first
            await _num_inp.click()
            await _num_inp.fill("")
            await _num_inp.type(_num, delay=25)
            await _pin_inp.click()
            await _pin_inp.fill("")
            await _pin_inp.type(_pin, delay=25)
            await page.wait_for_timeout(250)
        except Exception as _fe:
            print(f"  {Y}⚠ Не удалось заполнить поля: {_fe}{RST}")
            _tried_bad.add(_num)
            continue

        # 3. Отправляем купон кнопкой «Add Gift Card»
        await _click_add_submit()

        # 4. Ждём РЕЗУЛЬТАТ: зачисление (Gift Cards ↑ / Total ↓) ИЛИ явную ошибку.
        # Сводка справа обновляется с задержкой, поэтому поллим до ~9 сек и выходим
        # СРАЗУ как увидели изменение — иначе реально списанная карта ошибочно
        # считалась «не зачислилась».
        async def _err_now():
            try:
                return await page.evaluate(r"""() => {
                    const b = (document.body ? document.body.innerText : '').toLowerCase();
                    if (b.includes('another account') ||
                        (b.includes('already') && b.includes('gift card'))) return 'already';
                    const pat = ['invalid','incorrect','not valid','expired','wrong',
                                 'does not','unable','cannot be applied','no balance','not a valid'];
                    for (const p of pat) if (b.includes(p)) return 'other';
                    return '';
                }""")
            except Exception:
                return ""

        _success = False
        _errcat = ""
        _applied_after, _total_after = _applied_before, _total_before
        for _wpoll in range(18):   # до ~9 сек
            await page.wait_for_timeout(500)
            _aa = await _read_gift_applied()
            _tt = await _read_order_total(page)
            if _aa > _applied_before or (_total_before > 0 and 0 < _tt < _total_before):
                _success = True
                _applied_after, _total_after = _aa, _tt
                break
            _errcat = await _err_now()
            if _errcat:
                # Стейл-ошибка на странице: ещё раз сверим зачисление
                await page.wait_for_timeout(400)
                _aa2 = await _read_gift_applied()
                _tt2 = await _read_order_total(page)
                if _aa2 > _applied_before or (_total_before > 0 and 0 < _tt2 < _total_before):
                    _success = True
                    _applied_after, _total_after = _aa2, _tt2
                    _errcat = ""
                break

        if _success:
            _delta = max(0, _applied_after - _applied_before)
            if _delta <= 0 and _total_before > _total_after > 0:
                _delta = _total_before - _total_after
            applied += 1
            if _applied_after > applied_sum:
                applied_sum = _applied_after
            else:
                applied_sum += _delta if _delta > 0 else _dn
            print(f"  {G}✔ Карта {_mask_gift(_num)} зачислена (+₹{_delta or _dn}). "
                  f"Гифтом покрыто ₹{applied_sum}, Total ₹{_total_after}{RST}")
            with contextlib.suppress(Exception):
                _mark_gift_used(c, profile_path, status="used")
            await _ensure_use_checkbox()
            # После успеха поле часто пропадает — перед следующей картой снова Gift Cards
            continue

        if _errcat == "already":
            print(f"  {Y}Карта уже использована — помечаю и беру следующую{RST}")
            with contextlib.suppress(Exception):
                _mark_gift_used(c, profile_path, status="used_elsewhere")
            continue

        print(f"  {Y}⚠ Карта не зачислилась ({_errcat or 'timeout'}) — пробую другую{RST}")
        _tried_bad.add(_num)
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")
        continue


    # ── Place Order ───────────────────────────────────────────────────────────
    await _ensure_use_checkbox()
    # Ждём появления «Place Order» (обычно уже есть — ловим сразу)
    _po = await _gift_place_order_bbox(page)
    for _pw in range(6):
        if _po:
            break
        await page.wait_for_timeout(500)
        await _ensure_use_checkbox()
        _po = await _gift_place_order_bbox(page)
    if not _po:
        print(f"  {R}✘ Кнопка Place Order не появилась — баланса не хватило или карты отклонены{RST}")
        _tg_send_direct(f"🎁 *Оплата гифт-картами не завершена*\n\nПрименено карт: {applied}. "
                        f"Кнопка Place Order не появилась.")
        return "gift_failed"

    # Жмём «Place Order» надёжно (locator/JS/координаты); повторяем, если не ушли со страницы
    _placed = False
    for _try in range(3):
        _ckcancel()
        print(f"  {G}Нажимаю Place Order (попытка {_try + 1}/3)...{RST}")
        _clicked = await _click_place_order(page)
        if not _clicked:
            await page.wait_for_timeout(1_500)
            continue
        try:
            await page.wait_for_url(lambda u: "payments" not in u, timeout=20_000)
            _placed = True
            break
        except Exception:
            # Ещё на payments — возможно клик не сработал; проверим и повторим
            await page.wait_for_timeout(1_500)
            if "payments" not in page.url:
                _placed = True
                break
    await page.wait_for_timeout(3_000)
    if _placed or "payments" not in page.url:
        print(f"  {G}✅ Заказ оформлен гифт-картами (URL: {page.url.split('?')[0]}){RST}")
        return True
    print(f"  {R}✘ Нажал Place Order, но страница не сменилась — оплата не подтверждена{RST}")
    _tg_send_direct("🎁 *Place Order нажат, но переход не произошёл* — проверьте заказ вручную.")
    return "gift_failed"


async def _do_payments_page(page, card: dict | None = None,
                            gift: bool = False, profile_path=None) -> bool:
    """
    На странице payments (flipkart.com/payments):
      - gift=True — оплата подарочными картами (Flipkart Gift Card).
      - Если передана карта — выбирает «Credit / Debit / ATM Card»,
        заполняет реквизиты и нажимает «Pay» / «Add Address & Pay».
      - Без карты — оставляет страницу открытой (UPI по умолчанию).
    """
    await page.wait_for_timeout(800)

    if gift:
        return await _do_gift_card_payment(page, profile_path=profile_path)

    if card:
        return await _enter_card_on_payments(page, card)

    # Без карты: страница остаётся открытой для ручного UPI-платежа
    print("  Страница оплаты открыта (UPI / выбери способ вручную)")
    return True


async def _handle_set_location_on_viewcheckout(page) -> bool:
    """
    Пока на viewcheckout видна кнопка «Set Location»:
      1. Кликаем «Set Location» → открывается /address-map/ страница
      2. На каждой следующей странице ищем одну из 3 кнопок:
         «Update address», «Confirm», «Ok»/«Done» — нажимаем найденную
         (повторяем 3 раза чтобы пройти все шаги карты)
      3. Повторяем весь цикл пока «Set Location» не исчезнет с viewcheckout
    Возвращает True если кнопка была найдена хотя бы раз.
    """
    import random as _r

    _FIND_SET_LOC_JS = """() => {
        // Точное совпадение "Set Location" в любом элементе
        for (const el of document.querySelectorAll('*')) {
            const t = (el.innerText || '').trim();
            if (t.toLowerCase() !== 'set location') continue;
            const r = el.getBoundingClientRect();
            if (r.width >= 20 && r.height >= 8)
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        // Fallback: жёлтая кнопка содержащая "set location"
        for (const el of document.querySelectorAll('button, div, a, [role="button"]')) {
            const t = (el.innerText || '').toLowerCase();
            if (!t.includes('set location')) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 60 || r.height < 20) continue;
            const bg = window.getComputedStyle(el).backgroundColor;
            const m = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
            if (m && +m[1] > 180 && +m[2] > 100 && +m[3] < 80)
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        return null;
    }"""

    # 3 кнопки которые могут появиться на странице карты
    _MAP_BTNS_JS = """() => {
        const targets = ['update address', 'confirm', 'ok', 'done', 'save'];
        for (const el of document.querySelectorAll('button, div, a, [role="button"], input[type="submit"]')) {
            const t = (el.innerText || el.value || '').trim().toLowerCase();
            if (!targets.some(k => t === k || t.includes(k) && t.length < 25)) continue;
            const r = el.getBoundingClientRect();
            if (r.width >= 40 && r.height >= 15)
                return {x: r.x + r.width / 2, y: r.y + r.height / 2, text: t};
        }
        return null;
    }"""

    async def _search_location_by_pincode(pg):
        """Нажимает Change (рядом с Area/Locality), вводит пинкод в оверлей поиска карты."""
        try:
            _pincode = await pg.evaluate("""() => {
                const m = (document.body.innerText || '').match(/\\b(\\d{6})\\b/);
                return m ? m[1] : "400001";
            }""")
            # Нажать Change рядом с полем Area/Locality (не в шапке сайта)
            # Ищем кнопку Change, у которой y > 150px (не в header)
            _chg = await pg.evaluate("""() => {
                for (const el of document.querySelectorAll(
                        'button,a,span,div,[role="button"]')) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t !== 'change') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 10 || r.height < 8) continue;
                    if (r.y < 150) continue;  // пропускаем элементы в шапке
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
            if not _chg:
                return False
            print(f"  карта: нажимаю «Change» (locality)...")
            await pg.mouse.click(_chg["x"], _chg["y"])
            await pg.wait_for_timeout(1_500)

            # Поле поиска появляется как оверлей на карте
            # Placeholder: "Search by area, name, street." — содержит 'area' или 'street'
            _sf = await pg.evaluate("""() => {
                for (const el of document.querySelectorAll('input')) {
                    const ph = (el.placeholder || '').toLowerCase();
                    if (!ph.includes('area') && !ph.includes('street')) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= 80 && r.height >= 10)
                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
            if not _sf:
                return False
            print(f"  карта: пишу пинкод {_pincode} в поле поиска...")
            await pg.mouse.click(_sf["x"], _sf["y"])
            await pg.wait_for_timeout(300)
            await pg.keyboard.press("Control+a")
            await pg.keyboard.press("Delete")
            await pg.keyboard.type(_pincode, delay=80)
            await pg.wait_for_timeout(2_500)

            # Выбор подсказки: сначала клавиатурой (↓↓ Enter), потом позиционный клик
            print(f"  карта: выбираю подсказку (↓↓ Enter)...")
            await pg.keyboard.press("ArrowDown")
            await pg.wait_for_timeout(250)
            await pg.keyboard.press("ArrowDown")   # вторая подсказка (длиннее)
            await pg.wait_for_timeout(250)
            await pg.keyboard.press("Enter")
            await pg.wait_for_timeout(2_500)

            # Если оверлей поиска ещё виден — кликаем позиционно (~90px ниже низа поля)
            _inp_r2 = await pg.evaluate("""() => {
                const inp = document.querySelector(
                    'input[placeholder*="area" i], input[placeholder*="street" i]');
                if (!inp) return null;
                const r = inp.getBoundingClientRect();
                if (r.width < 10 || r.height < 10) return null;
                return {cx: r.x + r.width / 2, bottom: r.bottom};
            }""")
            if _inp_r2:
                print(f"  карта: клик позиционно (90px ниже поля)...")
                await pg.mouse.click(_inp_r2["cx"], _inp_r2["bottom"] + 90)
                await pg.wait_for_timeout(2_000)

            # Диалог "Your address has been updated" → нажимаем «Update»
            _upd = await pg.evaluate("""() => {
                for (const el of document.querySelectorAll(
                        'button,div,a,[role="button"]')) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t !== 'update') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= 40 && r.height >= 15)
                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
            if _upd:
                print("  карта: нажимаю «Update» (диалог)...")
                await pg.mouse.click(_upd["x"], _upd["y"])
                await pg.wait_for_timeout(1_500)

            # Нажать Confirm (в оверлее)
            _conf = await pg.evaluate("""() => {
                for (const el of document.querySelectorAll(
                        'button,div,a,[role="button"]')) {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (t !== 'confirm') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= 40 && r.height >= 15)
                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
            if _conf:
                print("  карта: нажимаю «confirm»...")
                await pg.mouse.click(_conf["x"], _conf["y"])
                await pg.wait_for_timeout(2_000)

            # После Confirm поле Area/Locality могло получить текст на местном языке.
            # Находим его (смежное с кнопкой Change) и заменяем на английский текст.
            try:
                _area_fld = await pg.evaluate("""() => {
                    // Ищем Change-кнопку (y > 150), потом ближайший input/textarea в контейнере
                    for (const chg of document.querySelectorAll(
                            'button,a,span,div,[role="button"]')) {
                        const t = (chg.innerText || '').trim().toLowerCase();
                        if (t !== 'change') continue;
                        const cr = chg.getBoundingClientRect();
                        if (cr.y < 150) continue;
                        // ищем input/textarea-сосед в той же секции
                        const sect = chg.closest('div,section,fieldset') || chg.parentElement;
                        if (!sect) continue;
                        const inp = sect.querySelector('input,textarea');
                        if (inp) {
                            const r = inp.getBoundingClientRect();
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                    }
                    return null;
                }""")
                if _area_fld:
                    _eng_text = f"Main Road {_pincode}"
                    print(f"  карта: заменяю Area на английский: «{_eng_text}»...")
                    await pg.mouse.click(_area_fld["x"], _area_fld["y"])
                    await pg.wait_for_timeout(200)
                    await pg.keyboard.press("Control+a")
                    await pg.keyboard.press("Delete")
                    await pg.keyboard.type(_eng_text, delay=60)
                    await pg.wait_for_timeout(300)
            except Exception:
                pass

            return True
        except Exception as _sle:
            print(f"  карта: поиск пинкода: {_sle}")
            return False

    handled = False
    for _outer in range(3):
        try:
            loc_bbox = await page.evaluate(_FIND_SET_LOC_JS)
        except Exception:
            loc_bbox = None

        if not loc_bbox:
            break

        handled = True
        print(f"  Set Location: нажимаю (попытка {_outer + 1})...")
        await page.mouse.click(loc_bbox["x"], loc_bbox["y"])
        await page.wait_for_timeout(_r.uniform(1_500, 2_500))

        # На address-map: сначала пробуем Submit/Update/Confirm (если пинкод уже задан),
        # и только если не сработало — вводим пинкод через Change
        if "address-map" in page.url or "changeShipping" in page.url:
            _quick_btn = None
            try:
                _quick_btn = await page.evaluate(_MAP_BTNS_JS)
            except Exception:
                pass
            if _quick_btn:
                print(f"  карта: пробую «{_quick_btn.get('text', '?')}» сразу (без Change)...")
                await page.mouse.click(_quick_btn["x"], _quick_btn["y"])
                await page.wait_for_timeout(_r.uniform(1_800, 2_500))
            if "address-map" in page.url or "changeShipping" in page.url:
                await _search_location_by_pincode(page)

        # Перед нажатием «Update address» чистим поле Alternate phone —
        # Flipkart предзаполняет его номером аккаунта с +91 (например,
        # +917204960944), что вызывает ошибку валидации и блокирует сохранение.
        if "address-map" in page.url or "changeShipping" in page.url:
            try:
                await page.evaluate("""() => {
                    for (const inp of document.querySelectorAll('input')) {
                        const ph = (inp.placeholder || inp.getAttribute('aria-label') || '').toLowerCase();
                        const lbl = (() => {
                            let el = inp.parentElement;
                            for (let i = 0; i < 5 && el; i++, el = el.parentElement) {
                                const l = el.querySelector('label');
                                if (l) return l.textContent.toLowerCase();
                            }
                            return '';
                        })();
                        if (ph.includes('alternate') || lbl.includes('alternate')) {
                            inp.focus();
                            inp.value = '';
                            inp.dispatchEvent(new Event('input', {bubbles: true}));
                            inp.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                    }
                }""")
                await page.wait_for_timeout(200)
            except Exception:
                pass

        # Затем Update address
        for _btn_step in range(3):
            if "viewcheckout" in page.url:
                break
            try:
                btn = await page.evaluate(_MAP_BTNS_JS)
            except Exception:
                btn = None
            if btn:
                print(f"  карта: нажимаю «{btn.get('text', '?')}»...")
                await page.mouse.click(btn["x"], btn["y"])
                await page.wait_for_timeout(_r.uniform(1_200, 2_000))
            else:
                await page.wait_for_timeout(1_500)

        if "address-map" in page.url or "changeShipping" in page.url:
            print("  Set Location: нажимаю Back...")
            try:
                await page.go_back()
                await page.wait_for_timeout(2_000)
            except Exception:
                pass

        if "viewcheckout" not in page.url:
            try:
                await page.wait_for_url("**/viewcheckout**", timeout=8_000)
            except Exception:
                pass
        await page.wait_for_timeout(1_000)

    if handled:
        print("  Set Location: обработан.")
    return handled


async def _viewcheckout_to_payments(page, profile_path: Path | None = None) -> bool:
    """
    Общий хелпер: на viewcheckout обрабатывает email, кликает Continue,
    ждёт переход на payments. Retry до 3 раз.
    Возвращает True если страница payments загружена.
    profile_path — если задан, buyer_email пишется в .profile_meta.json.
    """
    import random as _r

    def _persist_email_if_any() -> None:
        em = _get_filled_email()
        if profile_path and em:
            with contextlib.suppress(Exception):
                _save_meta_field(profile_path, buyer_email=em)

    _CHECKOUT_URL_PARTS = ("viewcheckout", "payments", "changeShippingAddress", "add/form")
    if "viewcheckout" not in page.url:
        if "changeShippingAddress" in page.url or "add/form" in page.url:
            print(f"  {DIM}Обнаружена страница адреса, ждём 5 секунд для прогрузки...{RST}")
            await page.wait_for_timeout(5_000)
        if "payments" in page.url:
            return True
        # Новый формат Flipkart: /checkout/<hash> без 'viewcheckout' в URL
        # Если URL содержит /checkout/ и ни одного известного сегмента — обрабатываем как viewcheckout
        if "checkout" in page.url and not any(p in page.url for p in _CHECKOUT_URL_PARTS):
            print(f"  {DIM}Новый формат checkout URL — обрабатываю как viewcheckout...{RST}")
            # проваливаемся ниже для обработки Continue
        else:
            return "payments" in page.url

    # Ждём пока viewcheckout прогрузит контент — Continue на Flipkart это <DIV>, не <button>
    # Поэтому ждём через wait_for_function по точному innerText
    print(f"  {DIM}Ждём загрузки viewcheckout...{RST}")
    try:
        await page.wait_for_function("""() => {
            if (location.href.includes('changeShippingAddress') || location.href.includes('add/form')) return true;
            // Капча «Are you a human?» — не ждём 40с, сразу выходим (обработается ниже)
            const _t = (document.title || '').toLowerCase();
            const _bi = (document.body?.innerText || '').toLowerCase();
            if (_t.includes('captcha') || _bi.includes('are you a human')) return true;
            const body = (document.body?.textContent || '').toLowerCase();
            if (body.includes('currently out of stock') || body.includes('out of stock for') ||
                body.includes('not deliverable') || body.includes('try another address')) return true;

            // Если видна кнопка "Set Location" - выходим мгновенно, чтобы не ждать Continue
            for (const el of document.querySelectorAll('button, div, a, span, [role="button"]')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (t === 'set location') return true;
            }
            
            const kw = ['continue', 'place order'];
            for (const el of document.querySelectorAll('div, button, a, span, [role="button"]')) {
                const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                if (!kw.some(k => t === k)) continue;
                const r = el.getBoundingClientRect();
                if (r.width >= 40 && r.height >= 15) return true;
            }
            return false;
        }""", timeout=40_000)
    except Exception:
        pass
    await page.wait_for_timeout(300)

    # OOS-проверка сразу после загрузки (Continue никогда не появится)
    if "viewcheckout" in page.url:
        try:
            _body_oos = (await page.evaluate(
                "() => (document.body?.textContent || '').toLowerCase()"))
            if any(p in _body_oos for p in _OOS_PHRASES):
                return "OUT_OF_STOCK"
        except Exception:
            pass

    async def _mouse_click_continue(page) -> bool:
        """Находит Continue/Place Order и кликает Playwright mouse (React реагирует на реальный click)."""
        # JS bbox — ищем жёлтую кнопку по тексту и координатам
        try:
            bbox = await page.evaluate("""() => {
                const kw = ['continue', 'place order', 'continue to payment'];
                const all = [...document.querySelectorAll('div, button, a, span, [role="button"]')];
                // 1. Жёлтый фон + точный текст
                let found = all.find(el => {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (!kw.some(k => t === k)) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 40 || r.height < 15) return false;
                    const bg = window.getComputedStyle(el).backgroundColor;
                    const m = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                    return m && +m[1] > 180 && +m[2] > 100 && +m[3] < 80;
                });
                // 2. Любой размер + точный текст
                if (!found) found = all.find(el => {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (!kw.some(k => t === k)) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 40 && r.height >= 15;
                });
                // 3. includes() — кнопка с дополнительным текстом внутри (напр. "399\\nContinue")
                if (!found) found = all.find(el => {
                    const t = (el.innerText || '').trim().toLowerCase();
                    if (!kw.some(k => t.includes(k))) return false;
                    if (kw.some(k => t === k)) return false; // уже проверили точное совпадение
                    const r = el.getBoundingClientRect();
                    if (r.width < 60 || r.height < 20) return false;
                    const bg = window.getComputedStyle(el).backgroundColor;
                    const m = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                    return m && +m[1] > 180 && +m[2] > 100 && +m[3] < 80;
                });
                if (!found) return null;
                const r = found.getBoundingClientRect();
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }""")
            if bbox:
                await page.mouse.click(bbox["x"], bbox["y"])
                return True
        except Exception:
            pass
        return False

    async def _is_captcha(page) -> bool:
        """Страница Flipkart reCAPTCHA «Are you a human? Confirming...»."""
        try:
            return bool(await page.evaluate("""() => {
                const t = (document.title || '').toLowerCase();
                const b = (document.body?.innerText || '').toLowerCase();
                return t.includes('captcha') || t.includes('recaptcha')
                    || b.includes('are you a human')
                    || (b.includes('confirming') && b.length < 300);
            }"""))
        except Exception:
            return False

    for attempt in range(4):
        print(f"  {DIM}viewcheckout→payments попытка {attempt + 1}/4, URL: {page.url[:60]}{RST}")

        # 0a. Капча «Are you a human?» — часто зависает на «Confirming...».
        # Ждём авто-подтверждение, иначе обновляем страницу (до 5 раз).
        if await _is_captcha(page):
            _cap_passed = False
            for _cap in range(5):
                print(f"  {Y}⚠ Капча Flipkart — жду авто-подтверждение / обновляю ({_cap + 1}/5)...{RST}")
                # до 12 сек: challenge может подтвердиться сам
                for _ in range(12):
                    _ckcancel()
                    await page.wait_for_timeout(1_000)
                    if not await _is_captcha(page):
                        _cap_passed = True
                        break
                if _cap_passed:
                    break
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=20_000)
                except Exception:
                    pass
                await page.wait_for_timeout(2_000)
            if not _cap_passed and await _is_captcha(page):
                print(f"  {R}✘ Капча не проходит после обновлений — прекращаю{RST}")
                return "CAPTCHA"
            print(f"  {G}✔ Капча пройдена — продолжаю{RST}")
            if "payments" in page.url:
                return True
            # даём странице догрузиться после капчи и повторяем цикл
            await page.wait_for_timeout(1_500)
            continue

        # OOS — Continue никогда не появится, дальше нет смысла
        if "viewcheckout" in page.url:
            try:
                _body_oos2 = (await page.evaluate(
                    "() => (document.body?.textContent || '').toLowerCase()"))
                if any(p in _body_oos2 for p in _OOS_PHRASES):
                    return "OUT_OF_STOCK"
            except Exception:
                pass

        # 0. Set Location — проверяем и обрабатываем на каждой попытке
        if "viewcheckout" in page.url:
            await _handle_set_location_on_viewcheckout(page)

        if "viewcheckout" not in page.url:
            break

        # 1. Email — проверяем на КАЖДОЙ попытке пока "Add Email" / "Email ID required" видно
        _email_needed = await page.evaluate("""() => {
            const body = (document.body && document.body.innerText || '').toLowerCase();
            return body.includes('add email') || body.includes('email id required');
        }""")
        if _email_needed:
            print(f"  {DIM}Email ещё не добавлен — заполняем (попытка {attempt + 1})...{RST}")
            await _handle_email_on_page(page)
            await page.wait_for_timeout(800)
            # Проверяем что email действительно принят (строка исчезла)
            _still_needed = await page.evaluate("""() => {
                const body = (document.body && document.body.innerText || '').toLowerCase();
                return body.includes('add email') || body.includes('email id required');
            }""")
            if _still_needed:
                print(f"  {BLD}Email всё ещё не принят — пробуем ещё раз{RST}")
                await _handle_email_on_page(page)
                await page.wait_for_timeout(1_000)
            _persist_email_if_any()

        # 2. Прокрутим к кнопке и кликаем Continue (body может быть null при навигации)
        try:
            await page.evaluate("() => { if (document.body) window.scrollTo(0, document.body.scrollHeight); }")
        except Exception:
            pass
        # На первой попытке даём странице дополнительно прогрузиться (до 10 сек)
        if attempt == 0:
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                await page.wait_for_timeout(10_000)
        else:
            await page.wait_for_timeout(600)
        clicked = await _mouse_click_continue(page)
        print(f"  {DIM}Continue клик: {'да' if clicked else 'нет'} (попытка {attempt + 1}/4){RST}")
        if not clicked and attempt == 0:
            try:
                _scr_path = f"debug/viewcheckout_debug_{attempt}.png"
                await page.screenshot(path=_scr_path)
                print(f"  {DIM}Скриншот: {_scr_path}{RST}")
            except Exception:
                pass
        await page.wait_for_timeout(1_500)

        if "payments" in page.url:
            _persist_email_if_any()
            return True
        if "viewcheckout" not in page.url:
            break  # ушли на add/form или другую страницу

        # Кнопка Continue не найдена — обновляем страницу (F5) и ждём снова
        if not clicked and "viewcheckout" in page.url:
            print(f"  {DIM}Кнопка Continue не найдена — обновляю страницу (F5)...{RST}")
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15_000)
            except Exception:
                pass
            try:
                await page.wait_for_function("""() => {
                    if (location.href.includes('changeShippingAddress') || location.href.includes('add/form')) return true;
                    const body = (document.body?.textContent || '').toLowerCase();
                    if (body.includes('currently out of stock') || body.includes('out of stock for') ||
                        body.includes('not deliverable') || body.includes('try another address')) return true;
                    for (const el of document.querySelectorAll('button, div, a, span, [role="button"]')) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (t === 'set location') return true;
                    }
                    const kw = ['continue', 'place order'];
                    for (const el of document.querySelectorAll('div, button, a, span, [role="button"]')) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (!kw.some(k => t === k)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width >= 40 && r.height >= 15) return true;
                    }
                    return false;
                }""", timeout=20_000)
            except Exception:
                pass
            await page.wait_for_timeout(1_000)

        # Ещё на viewcheckout — подождём и повторим
        await page.wait_for_timeout(_r.uniform(600, 1_000))

    if "payments" not in page.url and ("changeShippingAddress" in page.url or "add/form" in page.url):
        print(f"  {DIM}Перешли на страницу адреса, ждём 5 секунд для прогрузки...{RST}")
        await page.wait_for_timeout(5_000)
        return False

    # Финальное ожидание
    try:
        await page.wait_for_url("**/payments**", timeout=15_000)
    except Exception:
        pass
    return "payments" in page.url and "login" not in page.url


async def _viewcheckout_continue(page) -> str | None:
    """
    Ждёт viewcheckout, проверяет out-of-stock, нажимает Continue.
    Если после Continue появляется попап с email — заполняет и нажимает Continue повторно.
    Возвращает: None — успех; "OUT_OF_STOCK" — нет доставки;
                URL (строка) — viewcheckout не открылся.
    """
    import random as _r
    try:
        await page.wait_for_url("**/viewcheckout**", timeout=10_000)
    except Exception:
        pass
    await page.wait_for_timeout(800)
    if "viewcheckout" not in page.url:
        return page.url
    body = (await page.evaluate(
        "() => (document.body && document.body.textContent) || ''")).lower()
    if any(p in body for p in _OOS_PHRASES):
        return "OUT_OF_STOCK"

    _CONT_SEL = (
        "button:has-text('Continue'), a:has-text('Continue'), "
        "button:has-text('Place order'), button:has-text('Place Order'), "
        "button:has-text('PLACE ORDER')"
    )
    cont = page.locator(_CONT_SEL).last
    if await cont.count() > 0:
        await _human_click(page, cont, before=_r.uniform(0.1, 0.3))
        await page.wait_for_timeout(900)

    # После Place order Flipkart может показать поле email
    if await _handle_email_on_page(page):
        cont2 = page.locator(_CONT_SEL).last
        if await cont2.count() > 0:
            await _human_click(page, cont2, before=_r.uniform(0.1, 0.2))
            await page.wait_for_timeout(900)

    try:
        await page.wait_for_url("**/payments**", timeout=20_000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    return None


_BLACK_SEARCH_URL = (
    "https://www.flipkart.com/search?q=black+memebership&sid=mcd&as=on&as-show=on"
    "&otracker=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
    "&otracker1=AS_QueryStore_OrganicAutoSuggest_1_5_na_na_ps"
    "&as-pos=1&as-type=RECENT&suggestionId=black+memebership%7CVas&as-searchtext=black"
)


async def _navigate_search_buy(page, months: int) -> str | None:
    """Переходит напрямую на страницу продукта Black Membership и нажимает Buy Now.
    Возвращает строку ошибки или None при успехе (URL сменился на checkout).
    """
    return await _click_buy_now(page, _BLACK_URLS[months])



# ── Прямой доступ к Flipkart (без VPN) ───────────────────────────────────────
# Кэш фоновой проверки: если Flipkart открывается напрямую (интернет/VPN уже
# есть на уровне системы), расширение и VPN при входе не нужны. TTL — чтобы
# длинные запуски перепроверяли доступность и переключались при обрыве.
_FK_DIRECT_CACHE: dict = {"ok": None, "ts": 0.0}
_FK_DIRECT_TTL_OK = 300.0    # доступен — перепроверка раз в 5 минут
_FK_DIRECT_TTL_FAIL = 60.0   # недоступен — перепроверка уже через минуту


_FK_PROBE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _flipkart_direct_probe(attempts: int = 3, delay: float = 1.5) -> bool:
    """Проверяет, отдаёт ли Flipkart реальную страницу с этого IP (личный VPN на ПК).

    Раньше проба слала сырой HTTPS-HEAD обычным сокетом — Akamai сбрасывает такой
    «безбраузерный» запрос, и проба давала ложное «недоступно», хотя настоящий
    Chrome страницу открывал. Теперь пробуем httpx с браузерным UA: доступно =
    200 + контент Flipkart и без Access Denied. TLS-сброс/reset = недоступно.
    Один сбой не решает — недоступность подтверждаем после нескольких попыток.
    """
    try:
        import httpx as _hx
    except Exception:
        # httpx нет — не блокируем сценарий ложным «недоступно»
        return True
    for i in range(max(1, attempts)):
        if i:
            time.sleep(delay)
        try:
            with _hx.Client(
                timeout=10.0, follow_redirects=True, trust_env=False,
                headers={"User-Agent": _FK_PROBE_UA,
                         "Accept-Language": "en-IN,en;q=0.9"},
            ) as c:
                r = c.get("https://www.flipkart.com/account/login?ret=/")
                body = r.text[:3000].lower()
                if (r.status_code == 200 and "flipkart" in body
                        and "access denied" not in body):
                    return True
        except Exception:
            pass
    return False


async def _flipkart_direct_accessible(force: bool = False) -> bool:
    """Тихая проверка доступности Flipkart с хоста (HTTPS, без браузера и VPN)."""
    now = time.monotonic()
    ttl = _FK_DIRECT_TTL_OK if _FK_DIRECT_CACHE["ok"] else _FK_DIRECT_TTL_FAIL
    if (not force and _FK_DIRECT_CACHE["ok"] is not None
            and now - _FK_DIRECT_CACHE["ts"] < ttl):
        return bool(_FK_DIRECT_CACHE["ok"])
    try:
        ok = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _flipkart_direct_probe),
            timeout=60.0,
        )
    except Exception:
        ok = False
    _FK_DIRECT_CACHE.update(ok=ok, ts=time.monotonic())
    return ok


# ── Прокси: авто-подбор бесплатных публичных прокси под Flipkart ──────────────
# Когда Flipkart недоступен напрямую (свой VPN на ПК выключен), пробуем открыть
# его через прокси — тогда расширение VeePN и экранные клики не нужны. Большинство
# бесплатных прокси Flipkart отдаёт 403 (дата-центр), поэтому кандидатов проверяем
# и держим в кэше только те, что реально отдают страницу (200 + контент Flipkart).
_FREE_PROXY_CACHE_FILE = _DATA / "free_proxies.json"
_FREE_PROXY_TTL = 900.0           # 15 мин — публичные прокси быстро «умирают»
_FREE_PROXY_PROBE_TIMEOUT = 4.0   # секунды на один probe (медленные отсекаем рано)
_FREE_PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies"
    "&protocol=http&proxy_format=ipport&format=text&timeout=8000",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]
_free_proxy_pick_i = 0
_free_proxy_pick_lock = threading.Lock()


def _proxy_config() -> dict:
    """Секция proxy из config.yaml (безопасно, с дефолтами)."""
    try:
        import yaml as _y
        cfg_path = _HERE / "config.yaml"
        with open(cfg_path, encoding="utf-8") as _fh:
            cfg = _y.safe_load(_fh) or {}
        p = cfg.get("proxy") or {}
        raw_list = p.get("list") or []
        plist = [
            str(x).strip() for x in raw_list
            if isinstance(x, (str, int)) and str(x).strip()
        ]
        return {
            "enabled": bool(p.get("enabled", False)),
            "mode": str(p.get("mode", "auto_free")).strip() or "auto_free",
            "server": str(p.get("server", "")).strip(),
            "username": str(p.get("username", "")).strip(),
            "password": str(p.get("password", "")).strip(),
            "list": plist,
        }
    except Exception:
        return {
            "enabled": False, "mode": "auto_free", "server": "",
            "username": "", "password": "", "list": [],
        }


def _proxy_enabled() -> bool:
    return bool(_proxy_config().get("enabled"))


_SMS_PROVIDER_ORDER = ("grizzly", "pvapins", "auto")
_SMS_PROVIDER_LABEL = {
    "grizzly": "GrizzlySMS ✓  · PVAPins выкл",
    "pvapins": "PVAPins ✓  · Grizzly выкл",
    "auto":    "Auto: Grizzly → PVAPins",
}


def _sms_provider() -> str:
    """Текущий SMS-провайдер из config.yaml → sms.provider."""
    try:
        from sms_failover import _sms_provider_mode
        import yaml as _y
        cfg = _y.safe_load((_HERE / "config.yaml").read_text(encoding="utf-8")) or {}
        return _sms_provider_mode(cfg)
    except Exception:
        return "auto"


def _set_sms_provider(mode: str) -> bool:
    """Пишет sms.provider в config.yaml (grizzly | pvapins | auto) точечно."""
    mode = str(mode).strip().lower()
    if mode not in _SMS_PROVIDER_ORDER:
        return False
    cfg_path = _HERE / "config.yaml"
    try:
        text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    except Exception:
        return False
    # Только внутри секции sms: … до следующего корневого ключа
    m = re.search(r"(?m)^(sms:\s*\n)(.*?)(?=^[a-zA-Z_][\w]*:|\Z)", text, re.S)
    if m:
        head, body = m.group(1), m.group(2)
        new_body, n = re.subn(
            r"(?m)^([ \t]*provider:\s*)(grizzly|pvapins|auto)\s*(#.*)?$",
            rf"\g<1>{mode}\g<3>",
            body,
            count=1,
        )
        if n == 0:
            new_body = f"  provider: {mode}\n" + body
        new_text = text[: m.start()] + head + new_body + text[m.end() :]
    else:
        sep = "" if text.endswith("\n") or not text else "\n"
        new_text = text + f"{sep}sms:\n  provider: {mode}\n"
    try:
        cfg_path.write_text(new_text, encoding="utf-8")
        return True
    except Exception as exc:
        print(f"  {Y}⚠ Не удалось сохранить sms.provider: {exc}{RST}")
        return False


def _cycle_sms_provider() -> str:
    """Цикл: Grizzly → PVAPins → Auto → …"""
    cur = _sms_provider()
    try:
        idx = _SMS_PROVIDER_ORDER.index(cur)
    except ValueError:
        idx = -1
    nxt = _SMS_PROVIDER_ORDER[(idx + 1) % len(_SMS_PROVIDER_ORDER)]
    if not _set_sms_provider(nxt):
        return cur
    return nxt


def _sms_provider_menu_label() -> str:
    return _SMS_PROVIDER_LABEL.get(_sms_provider(), _SMS_PROVIDER_LABEL["auto"])


def _set_proxy_enabled(enabled: bool) -> bool:
    """Пишет proxy.enabled в config.yaml точечно (сохраняет комментарии вокруг блока)."""
    cfg_path = _HERE / "config.yaml"
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except Exception:
        return False
    # Только первая строка `enabled:` внутри секции proxy: … до следующего корневого ключа
    m = re.search(r"(?m)^(proxy:\s*\n)(.*?)(?=^[a-zA-Z_][\w]*:|\Z)", text, re.S)
    if not m:
        return False
    head, body = m.group(1), m.group(2)
    new_body, n = re.subn(
        r"(?m)^([ \t]*enabled:\s*)(true|false|True|False)\s*$",
        rf"\g<1>{'true' if enabled else 'false'}",
        body,
        count=1,
    )
    if n == 0:
        # нет строки enabled — вставим после proxy:
        new_body = f"  enabled: {'true' if enabled else 'false'}\n" + body
    new_text = text[: m.start()] + head + new_body + text[m.end() :]
    try:
        cfg_path.write_text(new_text, encoding="utf-8")
        return True
    except Exception:
        return False


async def _resolve_flipkart_launch_network(
    *, allow_proxy: bool = True, allow_vpn_extension: bool = False,
) -> tuple[bool, dict | None]:
    """План сети для Flipkart: (use_vpn, proxy). use_vpn всегда False —
    VPN-расширения удалены из проекта.

    Порядок: прокси (тумблер прокси ВКЛ) → direct (личный VPN на ПК).
    Прокси ВЫКЛ → всегда direct (личный VPN на ПК).
    """
    want_proxy = allow_proxy and _proxy_enabled()
    if want_proxy:
        proxy = await _select_proxy_for_launch_async()
        if proxy:
            return False, proxy
        print(f"  {Y}⚠ Прокси включён, но живой не найден — direct / VPN на ПК{RST}")
    return False, None


def _mark_browser_network(ctx, *, use_vpn: bool = False, proxy: dict | None = None) -> None:
    """Помечает контекст: HTTP-прокси / direct → навигейшн не включает VeepN."""
    if ctx is None:
        return
    via = bool(proxy and proxy.get("server"))
    with contextlib.suppress(Exception):
        ctx._subhub_via_proxy = via
        ctx._subhub_skip_vpn = via or (not use_vpn)


def _context_skip_vpn(context) -> bool:
    """True → не включать VeepN (прокси Playwright или явный direct)."""
    marked = getattr(context, "_subhub_skip_vpn", None)
    if marked is not None:
        return bool(marked)
    # без метки: тумблер VPN ВЫКЛ или прокси ВКЛ → VeepN не трогаем
    return _proxy_enabled() or not _vpn_enabled()


async def _resolve_profile_scenario_network(
    profile_path: Path | None = None,
    *,
    allow_vpn_extension: bool = True,
) -> tuple[bool, dict | None, str | None]:
    """Сеть для сценариев готового профиля (Chrome / адрес / покупка / активация).

    Порядок: прокси (если тумблер прокси ВКЛ) → direct (личный VPN на ПК).
    VPN-расширения удалены — use_vpn всегда False.
    Если Flipkart доступен прокси или личным VPN — сценарий продолжается.

    Returns (use_vpn, proxy, err).
    """
    _, proxy = await _resolve_flipkart_launch_network(
        allow_vpn_extension=allow_vpn_extension,
    )
    if proxy:
        print(f"  {G}Сеть: прокси {proxy.get('server')}{RST}")
        return False, proxy, None
    if _proxy_enabled():
        print(f"  {G}Сеть: без живого прокси — direct (личный VPN на ПК){RST}")
    else:
        print(f"  {G}Сеть: direct (личный VPN на ПК / напрямую){RST}")
    return False, None, None


def _fetch_free_proxy_candidates() -> list[str]:
    """Скачивает списки публичных HTTP-прокси из нескольких источников (host:port)."""
    out: list[str] = []
    try:
        import httpx as _hx
        import concurrent.futures as _cf
    except Exception:
        return out

    def _one(url: str) -> list[str]:
        local: list[str] = []
        try:
            r = _hx.get(url, timeout=8, trust_env=False)
            if r.status_code != 200:
                return local
            for line in r.text.splitlines():
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    local.append(line.split("//")[-1])
        except Exception:
            pass
        return local

    with _cf.ThreadPoolExecutor(max_workers=len(_FREE_PROXY_SOURCES) or 1) as ex:
        for part in ex.map(_one, _FREE_PROXY_SOURCES):
            out.extend(part)
    seen: set = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# Страны, с которых Flipkart обычно шлёт OTP (CN/RU free DC — «Something's not right»)
_OTP_GEO_OK = frozenset({"IN", "US", "CA", "SG", "GB", "DE", "FR", "NL"})


def _proxy_serves_flipkart(proxy: str, timeout: float = _FREE_PROXY_PROBE_TIMEOUT) -> bool:
    """True, если через прокси Flipkart отдаёт настоящую страницу (200 + контент)."""
    return _proxy_flipkart_latency(proxy, timeout=timeout) is not None


def _proxy_country_code(proxy: str, timeout: float = 3.5) -> str:
    """ISO country code через прокси (ip-api) или ''."""
    try:
        import httpx as _hx
        with _hx.Client(
            proxy=f"http://{proxy}", timeout=timeout,
            trust_env=False, follow_redirects=True,
        ) as c:
            r = c.get(
                "http://ip-api.com/json/?fields=status,countryCode",
                timeout=timeout,
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("status") == "success":
                    return str(d.get("countryCode") or "").upper()
    except Exception:
        pass
    return ""


def _proxy_flipkart_latency(
    proxy: str, timeout: float = _FREE_PROXY_PROBE_TIMEOUT,
) -> float | None:
    """Latency (сек) до рабочей страницы Flipkart или None."""
    try:
        import httpx as _hx
        t0 = time.monotonic()
        with _hx.Client(
            proxy=f"http://{proxy}", timeout=timeout,
            trust_env=False, follow_redirects=True,
        ) as c:
            # login легче главной витрины — быстрее probe
            r = c.get("https://www.flipkart.com/account/login?ret=/", timeout=timeout)
            body = r.text[:2000].lower()
            if (r.status_code == 200 and "flipkart" in body
                    and "access denied" not in body):
                return time.monotonic() - t0
    except Exception:
        return None
    return None


def _proxy_otp_score(proxy: str) -> float | None:
    """Сортировочный ключ (меньше = лучше) или None если прокси не подходит для OTP."""
    lat = _proxy_flipkart_latency(proxy)
    if lat is None:
        return None
    cc = _proxy_country_code(proxy)
    if cc and cc not in _OTP_GEO_OK:
        return None  # CN и прочие — Flipkart режет Request OTP
    if cc == "IN":
        return lat
    if cc in ("US", "CA", "SG"):
        return 10.0 + lat
    if cc in _OTP_GEO_OK:
        return 20.0 + lat
    # гео неизвестно — низкий приоритет (часто мёртвые/CN без ответа ip-api)
    return 50.0 + lat


def _validate_free_proxies(cands: list[str], *, want: int = 6,
                           max_workers: int = 80, budget: float = 35.0,
                           max_candidates: int = 600) -> list[str]:
    """Параллельно проверяет кандидатов; IN/US первыми, без geo CN и т.п.

    ThreadPoolExecutor при обычном выходе ждёт ВСЕ задачи — shutdown(cancel_futures).
    """
    import concurrent.futures as _cf
    scored: list[tuple[float, str]] = []
    if not cands:
        return []
    random.shuffle(cands)
    cands = cands[:max_candidates]
    t0 = time.monotonic()
    ex = _cf.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = {ex.submit(_proxy_otp_score, p): p for p in cands}
        for fut in _cf.as_completed(futs):
            p = futs[fut]
            try:
                sc = fut.result()
                if sc is not None:
                    scored.append((sc, p))
            except Exception:
                pass
            if len(scored) >= want or (time.monotonic() - t0) > budget:
                break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    scored.sort(key=lambda x: x[0])
    return [p for _, p in scored]


def _load_free_proxy_cache() -> list[str]:
    try:
        raw = json.loads(_FREE_PROXY_CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - float(raw.get("ts", 0)) < _FREE_PROXY_TTL:
            return [p for p in raw.get("proxies", []) if isinstance(p, str)]
    except Exception:
        pass
    return []


def _save_free_proxy_cache(proxies: list[str]) -> None:
    try:
        _FREE_PROXY_CACHE_FILE.write_text(
            json.dumps({"proxies": proxies, "ts": time.time()}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


_free_proxy_refresh_lock = threading.Lock()


def _get_free_proxies(min_count: int = 1) -> list[str]:
    """Возвращает пул рабочих для Flipkart бесплатных прокси (кэш → иначе подбор)."""
    cached = _load_free_proxy_cache()
    if len(cached) >= min_count:
        return cached
    with _free_proxy_refresh_lock:
        cached = _load_free_proxy_cache()
        if len(cached) >= min_count:
            return cached
        print(f"  {DIM}Прокси: подбираю IN/US под Flipkart OTP…{RST}")
        t0 = time.monotonic()
        cands = _fetch_free_proxy_candidates()
        working = _validate_free_proxies(cands, want=6)
        elapsed = time.monotonic() - t0
        if working:
            _save_free_proxy_cache(working)
            print(
                f"  {G}✔ Прокси: {len(working)} для OTP за {elapsed:.0f}s "
                f"(IN/US первыми){RST}"
            )
        else:
            print(
                f"  {Y}⚠ Прокси: IN/US под OTP не нашлось за {elapsed:.0f}s "
                f"— Proxy6 / VeePN{RST}"
            )
        return working


def prefetch_free_proxies() -> None:
    """Фоновый прогрев пула, чтобы вход не ждал первый подбор."""
    if not _proxy_enabled():
        return
    cfg = _proxy_config()
    if cfg.get("mode") == "manual" or cfg.get("server"):
        return
    with contextlib.suppress(Exception):
        if _select_proxy_for_launch():
            return
        _get_free_proxies(min_count=1)


def _mark_free_proxy_dead(proxy: str) -> None:
    """Убирает нерабочий прокси из кэша, чтобы следующий поток его не брал."""
    try:
        left = [p for p in _load_free_proxy_cache() if p != proxy]
        _save_free_proxy_cache(left)
    except Exception:
        pass


def _take_free_proxy() -> str | None:
    """Берёт следующий прокси из кэша по кругу (быстрые — в начале списка)."""
    global _free_proxy_pick_i
    proxies = _get_free_proxies(min_count=1)
    if not proxies:
        return None
    with _free_proxy_pick_lock:
        i = _free_proxy_pick_i % len(proxies)
        _free_proxy_pick_i = i + 1
        return proxies[i]


def _proxy6_api_key() -> str:
    try:
        key = (_read_secrets().get("proxy6") or {}).get("api_key", "").strip()
    except Exception:
        key = ""
    if not key:
        try:
            import yaml as _y
            cfg = _y.safe_load(
                (_HERE / "config.yaml").read_text(encoding="utf-8")
            ) or {}
            key = str((cfg.get("proxy6") or {}).get("api_key") or "").strip()
        except Exception:
            key = ""
    if not key or key.upper().startswith("YOUR_"):
        return ""
    return key


def _select_proxy6() -> dict | None:
    """Активный прокси Proxy6 (предпочтительно India) → Playwright proxy dict."""
    key = _proxy6_api_key()
    if not key:
        return None
    try:
        import httpx as _hx
        country = "in"
        with contextlib.suppress(Exception):
            import yaml as _y
            cfg = _y.safe_load(
                (_HERE / "config.yaml").read_text(encoding="utf-8")
            ) or {}
            country = str((cfg.get("proxy6") or {}).get("country") or "in").strip().lower() or "in"
        r = _hx.get(
            f"https://px6.link/api/{key}/getproxy",
            params={"state": "active", "nokey": ""},
            timeout=12,
            trust_env=False,
        )
        data = r.json()
        if data.get("status") != "yes":
            return None
        items = data.get("list") or []
        if isinstance(items, dict):
            items = list(items.values())
        if not items:
            return None
        # India first, then others
        def _rank(it: dict) -> tuple:
            cc = str(it.get("country") or "").lower()
            return (0 if cc == country else 1, str(it.get("host") or ""))

        items = sorted([x for x in items if isinstance(x, dict)], key=_rank)
        p = items[0]
        host = str(p.get("host") or p.get("ip") or "").strip()
        port = str(p.get("port") or "").strip()
        user = str(p.get("user") or "").strip()
        pw = str(p.get("pass") or p.get("password") or "").strip()
        if not host or not port:
            return None
        # HTTP для Playwright; SOCKS в type=socks — тоже пробуем http schema
        out: dict = {
            "server": f"http://{host}:{port}",
            "_free_host": f"{host}:{port}",
            "_source": "proxy6",
        }
        if user:
            out["username"] = user
            out["password"] = pw
        print(f"  {G}✔ Proxy6: {host}:{port} ({p.get('country') or '?'}){RST}")
        return out
    except Exception as exc:
        print(f"  {Y}⚠ Proxy6: {exc}{RST}")
        return None


def _select_proxy_from_list(cfg: dict) -> dict | None:
    """proxy.list из config — host:port или URL."""
    lst = cfg.get("list") or []
    if not lst:
        return None
    raw = random.choice(lst)
    s = str(raw).strip()
    if "://" not in s:
        s = f"http://{s}"
    # user:pass@host:port
    host_tag = s.split("@")[-1].replace("http://", "").replace("https://", "")
    out: dict = {"server": s if s.startswith("http") else f"http://{s}",
                 "_free_host": host_tag, "_source": "list"}
    return out


def _select_proxy_for_launch() -> dict | None:
    """Playwright proxy: manual → list → Proxy6(IN) → auto_free (IN/US).

    Бесплатные CN-прокси Flipkart открывает, но Request OTP режет —
    для OTP берём только подходящую географию / платный Proxy6.
    """
    cfg = _proxy_config()
    if not cfg["enabled"]:
        return None
    if cfg["mode"] == "manual" or cfg["server"]:
        if not cfg["server"]:
            return None
        out = {"server": cfg["server"]}
        if cfg["username"]:
            out["username"] = cfg["username"]
            out["password"] = cfg["password"]
        return out
    # Явный список в config
    picked = _select_proxy_from_list(cfg)
    if picked:
        return picked
    # Proxy6 India (если ключ в secrets/config)
    picked = _select_proxy6()
    if picked:
        return picked
    if cfg["mode"] in ("proxy6", "proxy6_only"):
        return None
    server = _take_free_proxy()
    if not server:
        return None
    return {"server": f"http://{server}", "_free_host": server, "_source": "auto_free"}


async def _select_proxy_for_launch_async() -> dict | None:
    """Асинхронная обёртка — подбор прокси в отдельном потоке (сеть блокирует)."""
    try:
        return await asyncio.get_running_loop().run_in_executor(
            None, _select_proxy_for_launch)
    except Exception:
        return None


async def _check_flipkart_accessible() -> bool:
    """Проверка VPN + Flipkart на отдельном ping-профиле (не трогает рабочие профили)."""
    if not _vpn_extension_dir():
        try:
            _rd, _wr = await asyncio.wait_for(
                asyncio.open_connection("www.flipkart.com", 443),
                timeout=10.0,
            )
            _wr.close()
            try:
                await _wr.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    profile = _VPN_PING_PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    if not _profile_has_vpn_extension(profile):
        _install_extension_filesystem(profile, force=True)  # только ping-профиль

    from playwright.async_api import async_playwright
    pw = None
    ctx = None
    try:
        await _vpn_chrome_cooldown(extra=0.5)
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile)
        kw = _vpn_browser_launch_kw(profile)
        with _chrome_window_hider():
            ctx = await pw.chromium.launch_persistent_context(str(profile.resolve()), **kw)
        await _close_extension_startup_tabs(ctx)
        if not await _vpn_connect_on_use(ctx, profile, ping_check=True):
            return False
        page = await _main_work_page(ctx)
        return await _verify_flipkart_reachable(page)
    except Exception:
        return False
    finally:
        await _close_browser_session(ctx, pw, disconnect_vpn=True)


def _is_flipkart_accessible_sync() -> bool:
    try:
        return asyncio.run(_check_flipkart_accessible())
    except Exception:
        return False


@_serialize_purchase
async def _do_buy_membership(profile_path: Path, months: int, card: dict | None = None,
                             _skip_ping: bool = False,
                             _existing_ctx=None, _existing_page=None) -> tuple[bool, str]:
    """Buy Now → адрес (если нужен) → viewcheckout → Continue → оплата.

    Если `_existing_ctx` уже открыл Flipkart — VPN не трогаем, продолжаем сценарий.
    """
    with contextlib.suppress(Exception):
        _pay_method[0] = _load_pay_method()
    _purchase_cancel.clear()
    _clear_filled_email()
    use_vpn, proxy = False, None
    if not _skip_ping and _existing_ctx is None:
        use_vpn, proxy, net_err = await _resolve_profile_scenario_network(profile_path)
        if net_err:
            return False, net_err
        set_profile_op_stage(profile_path, "Покупка · сеть / браузер")
        if not use_vpn and not proxy:
            # Лёгкая проба не совпадает с TLS-отпечатком реального Chrome и даёт
            # ложное «недоступно» — не блокируем, браузер решает по факту
            # (устойчивая навигация + детект Access Denied).
            if await _flipkart_direct_accessible():
                print(f"  {G}Flipkart доступен (личный VPN на ПК).{RST}")
            else:
                print(f"  {DIM}Проба недоступности — открываю браузер напрямую "
                      f"(личный VPN на ПК)…{RST}")

    if _is_profile_locked(profile_path) and _existing_ctx is None:
        print(f"  {Y}Профиль занят — закрываю Chrome и очищаю локи...{RST}")
        _clear_stale_profile_locks(profile_path)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "playwright не установлен  (pip install playwright)"

    pw = None
    ctx = _existing_ctx
    _keep_open = False
    _owns_ctx = ctx is None
    try:
        if ctx is None:
            pw = await async_playwright().start()
            _pre_inject_chrome_prefs(profile_path)
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_path.resolve()),
                **_browser_launch_kw(
                    phone=_phone_from_path(profile_path),
                    profile_path=profile_path, use_vpn=use_vpn, proxy=proxy,
                ))
            _mark_browser_network(ctx, use_vpn=use_vpn, proxy=proxy)
            await _close_extension_startup_tabs(ctx)
            await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
            page = await _main_work_page(ctx)
            await _maximize_window(ctx, page)
            if use_vpn:
                if not await _vpn_connect_for_profile(ctx, profile_path):
                    return False, "VPN не подключился — покупка отменена (Flipkart недоступен без VPN)"
                await _dismiss_all_veepn_welcome(ctx)
                await _close_vpn_extension_tabs(ctx, await _vpn_ext_id(ctx))
                page = await _main_work_page(ctx)
                with contextlib.suppress(Exception):
                    await page.bring_to_front()
            _product_url = _BLACK_URLS.get(months) or _BLACK_URLS[3]
            _via = "прокси" if proxy else ("VPN" if use_vpn else "direct")
            print(f"  {DIM}{_via} → страница {months} мес.…{RST}")
            ok_nav, page, nav_err = await _navigate_flipkart_resilient(
                ctx, page, _product_url,
                label=_phone_from_path(profile_path), profile_path=profile_path,
            )
            if not ok_nav:
                return False, f"Flipkart недоступен после {_via} — покупка отменена: {nav_err}"
            page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)
            print(f"  {G}✔ Flipkart OK — {_via} не трогаем, продолжаем покупку{RST}")
        else:
            # Уже открытый браузер — всё равно идём на страницу тарифа (не зависаем на главной)
            page = _existing_page or await _main_work_page(ctx)
            print(f"  {G}✔ Продолжаем сценарий в том же Chrome (VPN как есть){RST}")
            await _dismiss_all_veepn_welcome(ctx)
            await _close_vpn_extension_tabs(ctx, await _vpn_ext_id(ctx))
            with contextlib.suppress(Exception):
                await page.bring_to_front()
            _product_url = _BLACK_URLS.get(months) or _BLACK_URLS[3]
            on_product = (
                "flipkart-black" in (page.url or "") and "/p/" in (page.url or "")
            )
            if not on_product:
                print(f"  {DIM}открываю страницу {months} мес.…{RST}")
                ok_nav, page, nav_err = await _navigate_flipkart_resilient(
                    ctx, page, _product_url,
                    label=_phone_from_path(profile_path), profile_path=profile_path,
                )
                if not ok_nav:
                    # Без resilient — прямой goto (VPN уже жив)
                    with contextlib.suppress(Exception):
                        await page.goto(
                            _product_url, wait_until="domcontentloaded", timeout=45_000,
                        )
                        await page.wait_for_timeout(800)
                    if "flipkart-black" not in (page.url or ""):
                        return False, (
                            f"Flipkart недоступен — покупка отменена: {nav_err}"
                        )
            page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)

        _stealth = _build_stealth_js_m()
        if _stealth:
            await ctx.add_init_script(_stealth)

        # Проверяем — нет ли уже купленного Black Membership
        _bm_phone_label = _phone_from_path(profile_path)
        _bm_orders = await _check_recent_black_orders(page)
        if _bm_orders:
            _bm_info = "; ".join(_bm_orders[:3])
            print(f"\n  {Y}╔══ ВНИМАНИЕ: уже куплено! ════════════════════════════════╗{RST}")
            print(f"  {Y}║  {_bm_info[:70]}{RST}")
            print(f"  {Y}╚═══════════════════════════════════════════════════════════╝{RST}")
            _orders_confirm_ev.clear()
            _orders_confirm_choice[0] = None
            _tg_send_direct_kb(
                f"⚠️ *Уже куплено!*\n\n"
                f"Профиль `{_bm_phone_label}` — найден заказ *Flipkart BLACK*:\n"
                f"_{_bm_info[:200]}_\n\n"
                f"Что делать?",
                {"inline_keyboard": [
                    [{"text": "✅ Продолжить покупку", "callback_data": f"fill:orders_ok:{_bm_phone_label}"}],
                    [{"text": "🗑 Удалить профиль",   "callback_data": f"fill:orders_del:{_bm_phone_label}"}],
                ]}
            )
            print(f"  {Y}Жду ответа в Telegram (12 сек, иначе продолжаю)…{RST}")
            _bm_dl = asyncio.get_event_loop().time() + 12
            while asyncio.get_event_loop().time() < _bm_dl:
                if _orders_confirm_ev.is_set():
                    break
                await asyncio.sleep(0.5)
            if _orders_confirm_choice[0] is None:
                _orders_confirm_choice[0] = True  # нет ответа — продолжаем покупку
            if _orders_confirm_choice[0] is False:
                print(f"  {R}Удаляю профиль {_bm_phone_label}...{RST}")
                _keep_open = False
                import shutil as _sh_bm
                _sh_bm.rmtree(str(profile_path), ignore_errors=True)
                _tg_send_direct(f"🗑 Профиль `{_bm_phone_label}` удалён (дублирующий заказ)")
                return False, "Профиль удалён — дублирующий заказ"
            print(f"  {G}Продолжаю покупку...{RST}")

        # Не залогинен → сразу cookies_backup (один раз), без вопроса
        _session_restored = False

        async def _try_auto_restore_then_product() -> bool:
            nonlocal page, _session_restored
            if _session_restored:
                return False
            if not await _auto_restore_flipkart_session(ctx, page, Path(profile_path)):
                return False
            _session_restored = True
            _pu = _BLACK_URLS.get(months) or _BLACK_URLS[3]
            with contextlib.suppress(Exception):
                await page.goto(_pu, wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_timeout(800)
            page = await _keep_only_flipkart_tabs(ctx, prefer_page=page)
            return True

        if await _page_logged_out(page):
            if not await _try_auto_restore_then_product():
                return False, _NOT_LOGGED_IN_MSG

        # Уже на странице товара — Buy Now; иначе переход на тариф
        set_profile_op_stage(profile_path, "Buy Now")
        _on_product = "flipkart-black" in (page.url or "") and "/p/" in (page.url or "")
        if _on_product:
            print(f"  {DIM}Уже на странице товара — Buy Now…{RST}")
            err = await _click_buy_now(page, _BLACK_URLS[months], skip_goto=True)
        else:
            # После orders-check страница могла смениться — снова товар без VPN-сброса
            _product_url = _BLACK_URLS.get(months) or _BLACK_URLS[3]
            if "flipkart.com" in (page.url or "").lower():
                with contextlib.suppress(Exception):
                    await page.goto(_product_url, wait_until="domcontentloaded", timeout=45_000)
                    await page.wait_for_timeout(800)
                if "flipkart-black" in (page.url or "") and "/p/" in (page.url or ""):
                    err = await _click_buy_now(page, _BLACK_URLS[months], skip_goto=True)
                else:
                    err = await _navigate_search_buy(page, months)
            else:
                err = await _navigate_search_buy(page, months)
        if err and (err == _NOT_LOGGED_IN_MSG or "не залогинен" in err.lower()):
            if await _try_auto_restore_then_product():
                err = await _click_buy_now(page, _BLACK_URLS[months], skip_goto=False)
        if err:
            return False, err

        # После Buy Now страница ещё может быть в переходе — ждём чекаут URL
        _CHECKOUT_PARTS = ("viewcheckout", "changeShippingAddress", "payments", "add/form")
        if not any(s in page.url for s in _CHECKOUT_PARTS):
            print(f"  {DIM}Страница после Buy Now ещё загружается, жду чекаут...{RST}")
            try:
                await page.wait_for_function("""() => {
                    const url = location.href;
                    if (url.includes('viewcheckout') || url.includes('changeShippingAddress') ||
                            url.includes('payments') || url.includes('add/form'))
                        return true;
                    // Попап логина вместо чекаута — тоже выходим чтобы не ждать зря
                    const body = (document.body && document.body.innerText || '').toLowerCase();
                    return body.includes('log in to complete') || body.includes('phone number');
                }""", timeout=20_000)
            except Exception:
                pass
            await page.wait_for_timeout(1_000)
            # Если так и не попали на чекаут — проверяем: возможно попап логина
            if not any(s in page.url for s in _CHECKOUT_PARTS):
                _login_popup = await page.evaluate("""() => {
                    const b = (document.body && document.body.innerText || '').toLowerCase();
                    return b.includes('log in to complete') || b.includes('phone number');
                }""")
                if _login_popup:
                    if await _try_auto_restore_then_product():
                        err2 = await _click_buy_now(page, _BLACK_URLS[months], skip_goto=False)
                        if err2:
                            return False, err2
                    else:
                        return False, _NOT_LOGGED_IN_MSG
        addr_msg = ""

        async def _fill_addr_bm():
            nonlocal addr_msg
            a = _gen_indian_address()
            _ph = "".join(ch for ch in str(_bm_phone_label or "") if ch.isdigit())[-10:]
            if _ph:
                a["phone"] = _ph
            a["locality"] = random.choice(_IND_AREAS)
            a["address_line"] = f"{a['house']}, {a['road']}"
            lat, lon = _CITY_COORDS.get(a["city"], (20.5937, 78.9629))
            await ctx.set_geolocation({"latitude": lat, "longitude": lon})
            await _maximize_window(ctx, page)
            if not await _fill_address_form(page, a):
                return False
            addr_msg = f"Адрес: {a['name']} | {a['pincode']} {a['city']}"
            with contextlib.suppress(Exception):
                _save_meta_field(profile_path, **_profile_addr_meta(a))
            # Ждём viewcheckout или payments — не больше 20с
            if "viewcheckout" not in page.url and "payments" not in page.url:
                try:
                    await page.wait_for_url(
                        "**/viewcheckout**", timeout=20_000
                    )
                except Exception:
                    pass
            await page.wait_for_timeout(1_000)
            return True

        # ── Шаг A: сразу попали на форму адреса ─────────────────────────────
        if "changeShippingAddress" in page.url or "add/form" in page.url:
            print(f"  Заполняю форму адреса...")
            if not await _fill_addr_bm():
                return False, "Кнопка Save Address не найдена"

        # ── Шаг B: viewcheckout → email → Continue → payments ───────────────
        set_profile_op_stage(profile_path, "Чекаут / Continue")
        if "viewcheckout" in page.url or (
            "checkout" in (page.url or "") and "payments" not in (page.url or "")
        ):
            # Ждём стабилизации SPA перед любым evaluate (иначе "context destroyed")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
            await page.wait_for_timeout(500)
            try:
                body = (await page.evaluate(
                    "() => (document.body && document.body.textContent) || ''")).lower()
            except Exception:
                body = ""
            if any(p in body for p in _OOS_PHRASES):
                print(f"  {R}✘ Currently out of stock — с этим профилем уже ничего не сделать{RST}")
                print(f"  {Y}→ удалите профиль и возьмите следующий доступный{RST}")
                return False, "OUT_OF_STOCK|DELETE_PROFILE"

            reached = await _viewcheckout_to_payments(page, profile_path)
            if reached == "OUT_OF_STOCK":
                print(f"  {R}✘ Currently out of stock — с этим профилем уже ничего не сделать{RST}")
                print(f"  {Y}→ удалите профиль и возьмите следующий доступный{RST}")
                return False, "OUT_OF_STOCK|DELETE_PROFILE"

            # Set Location увёл на address-map, но навигация назад не завершилась
            if not reached and "address-map" in page.url:
                print(f"  Всё ещё на address-map — жду возврата на viewcheckout...")
                try:
                    await page.wait_for_url("**/viewcheckout**", timeout=10_000)
                    reached = await _viewcheckout_to_payments(page, profile_path)
                except Exception:
                    if "address-map" in page.url:
                        print(f"  address-map: нажимаю Back...")
                        await page.go_back()
                        await page.wait_for_timeout(3_000)
                        reached = await _viewcheckout_to_payments(page, profile_path)
            if reached == "OUT_OF_STOCK":
                print(f"  {R}✘ Currently out of stock — удалите профиль, следующий{RST}")
                return False, "OUT_OF_STOCK|DELETE_PROFILE"

            # После Continue мог появиться запрос адреса
            if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url) \
                    and "address-map" not in page.url:
                print(f"  Flipkart запросил адрес после Continue — заполняю...")
                if not await _fill_addr_bm():
                    return False, "Кнопка Save Address не найдена (после Continue)"
                reached = await _viewcheckout_to_payments(page, profile_path)
                if reached == "OUT_OF_STOCK":
                    print(f"  {R}✘ Currently out of stock — удалите профиль, следующий{RST}")
                    return False, "OUT_OF_STOCK|DELETE_PROFILE"
            if reached == "CAPTCHA":
                return False, "Капча Flipkart зависла (Are you a human?) — не удалось пройти даже после обновлений. Попробуйте ещё раз позже."

        # Номер телефона нужен для TG-ошибок ниже
        _pp_phone = ""
        try:
            _pp_parts = profile_path.name.split("_")
            _pp_num = next((p for p in reversed(_pp_parts) if len(p) >= 10 and p.isdigit()), "")
            if _pp_num:
                _pp_phone = _pp_num[-10:]
        except Exception:
            pass

        # ── Шаг C: проверяем что попали на payments ──────────────────────────
        # Если URL ещё в переходе — ждём; иначе F5/товар → Buy Now → Continue (браузер открыт)
        if "payments" not in page.url:
            print(f"  {DIM}Ждём загрузки страницы оплаты...{RST}")
            try:
                await page.wait_for_url("**/payments**", timeout=15_000)
            except Exception:
                pass
        if "payments" not in page.url:
            _product_retry = _BLACK_URLS.get(months) or _BLACK_URLS[3]
            for _pr in range(1, _PAYMENTS_REACH_ROUNDS + 1):
                if "payments" in page.url:
                    break
                _ckcancel()
                _keep_open = True
                print(
                    f"  {Y}Нет страницы оплаты — обновляю товар и повторяю "
                    f"Buy Now / Continue ({_pr}/{_PAYMENTS_REACH_ROUNDS})…{RST}"
                )
                err_r = await _click_buy_now(page, _product_retry, skip_goto=False)
                if err_r:
                    if err_r == _NOT_LOGGED_IN_MSG or "не залогинен" in err_r.lower():
                        return False, err_r
                    print(f"  {DIM}повтор Buy Now: {err_r}{RST}")
                    continue
                if "changeShippingAddress" in page.url or "add/form" in page.url:
                    if not await _fill_addr_bm():
                        continue
                if "viewcheckout" in page.url or (
                    "checkout" in (page.url or "") and "payments" not in (page.url or "")
                ):
                    body_r = ""
                    with contextlib.suppress(Exception):
                        body_r = (await page.evaluate(
                            "() => (document.body && document.body.textContent) || ''"
                        )).lower()
                    if any(p in body_r for p in _OOS_PHRASES):
                        return False, "OUT_OF_STOCK|DELETE_PROFILE"
                    reached = await _viewcheckout_to_payments(page, profile_path)
                    if reached == "OUT_OF_STOCK":
                        return False, "OUT_OF_STOCK|DELETE_PROFILE"
                    if reached == "CAPTCHA":
                        return False, (
                            "Капча Flipkart зависла (Are you a human?) — "
                            "не удалось пройти даже после обновлений. "
                            "Попробуйте ещё раз позже."
                        )
                    if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url) \
                            and "address-map" not in page.url:
                        if await _fill_addr_bm():
                            reached = await _viewcheckout_to_payments(page, profile_path)
                    if reached is True or "payments" in page.url:
                        break
                try:
                    await page.wait_for_url("**/payments**", timeout=8_000)
                except Exception:
                    pass
        if "payments" not in page.url:
            _keep_open = True
            _send_tg_error(
                _pp_phone,
                f"Не удалось перейти на оплату после {_PAYMENTS_REACH_ROUNDS} повторов "
                f"({page.url.split('?')[0].split('/')[-1]}) — браузер оставлен открытым",
            )
            return True, (f"{'✅ ' + addr_msg if addr_msg else '✅ Адрес уже был сохранён'}"
                          f" → ⚠️ Оплата не загрузилась после {_PAYMENTS_REACH_ROUNDS} повторов "
                          f"({page.url.split('?')[0].split('/')[-1]}), браузер оставлен открытым")

        # Отправляем куки в Telegram (до оплаты — чтобы можно было войти с телефона)
        try:
            await _send_cookies_tg(ctx, profile_path.name, _pp_phone)
        except Exception as _cke:
            print(f"  TG cookies: {_cke}")

        # Загружаем все карты — пробуем каждую пока не пройдёт оплата
        import json as _jj
        _all_cards: list = []
        try:
            if CARDS_FILE.exists():
                _all_cards = _jj.loads(CARDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

        # Сортируем карты в соответствии с установленным порядком (data/card_order.json)
        try:
            _order_file = _DATA / "card_order.json"
            if _order_file.exists():
                _order = _jj.loads(_order_file.read_text(encoding="utf-8"))
                if _order and isinstance(_order, list):
                    _sorted = []
                    for _idx in _order:
                        if 0 <= _idx < len(_all_cards):
                            _sorted.append(_all_cards[_idx])
                    # Добавляем карты, которые не были упомянуты в порядке
                    for _c in _all_cards:
                        if _c not in _sorted:
                            _sorted.append(_c)
                    if _sorted:
                        _all_cards = _sorted
                        _seq_dbg = " → ".join(str(i + 1) for i in _order
                                              if isinstance(i, int) and 0 <= i < len(_all_cards))
                        print(f"  {G}💳 Порядок карт применён: {_seq_dbg}{RST}")
        except Exception as _ex:
            print(f"  Ошибка применения порядка карт: {_ex}")

        # ── Оплата ПОДАРОЧНЫМИ картами (вместо перебора банковских) ──────────
        if _pay_method[0] == "gift":
            _gift_res = await _do_payments_page(page, gift=True, profile_path=profile_path)
            if _gift_res is True:
                try:
                    _post_result = await _handle_post_payment(
                        page, ctx, profile_path, phone_number=_pp_phone, months=months)
                except Exception as _pp_e:
                    print(f"  Post-payment: {_pp_e}")
                    _post_result = {}
                _keep_open = False
                if _post_result.get("paid"):
                    return True, "Оплачено подарочными картами"
                return True, "Гифт-оплата отправлена (подтверждение активации не получено)"
            if _gift_res == "gift_insufficient":
                _keep_open = False
                return False, "Не хватает подарочных карт для оплаты"
            _keep_open = False
            return False, "Оплата подарочными картами не удалась"

        if card:
            _rest = [c for c in _all_cards if c.get("number") != card.get("number")]
            _cards_seq = [card] + _rest
        else:
            _cards_seq = _all_cards or [None]

        _payments_url_saved = page.url
        _post_result: dict = {}
        _ci = 0
        _first_entry = True  # первая карта — уже на странице оплаты, навигация не нужна
        while _ci < len(_cards_seq):
            _ctry = _cards_seq[_ci]
            _ckcancel()
            if not _first_entry:
                _nick = (_ctry.get("nickname") or _ctry.get("number", "")[-4:]) if _ctry else "—"
                print(f"\n  Карта {_ci+1}/{len(_cards_seq)}: {_nick} — пробую...")
                # Возвращаемся на страницу ввода карты (после отказа/3DS/смены карты).
                # Раньше было привязано к _ci>0 → при смене на карту позиции 0 возврат
                # не срабатывал и бот пытался вводить карту на чужой странице.
                cur = page.url
                if "flipkart.com/payments" not in cur:
                    try:
                        await page.goto(_payments_url_saved,
                                        wait_until="domcontentloaded", timeout=15_000)
                        await page.wait_for_timeout(2_000)
                    except Exception:
                        pass
            _first_entry = False

            # Заполняем кнопки смены карты для 3DS OTP уведомления
            try:
                _3ds_card_options[:] = [
                    {"pos": _pi, "card": _pc}
                    for _pi, _pc in enumerate(_cards_seq)
                    if _pi != _ci and _pc
                ][:4]
                _switch_card_ev.clear()
                _switch_card_choice[0] = -1
            except Exception:
                pass

            _pay_done = await _do_payments_page(page, card=_ctry)

            if _pay_done == "otp_required":
                print(f"  {G}✅ Карта принята — браузер открыт для ввода OTP{RST}")
                break  # Браузер оставляем открытым, не пробуем следующую карту

            if _pay_done == "otp_timeout":
                print(f"  {R}❌ Оплата не прошла — время ожидания 3DS OTP истекло{RST}")
                _send_tg_error(_pp_phone, "Оплата не прошла — время ожидания 3DS OTP (15 мин) истекло")
                return False, "Оплата не прошла — время ожидания 3DS OTP истекло"

            if _pay_done == "switch_card":
                # Пользователь выбрал конкретную карту через TG
                _chosen = _switch_card_choice[0]
                if 0 <= _chosen < len(_cards_seq):
                    _nick_ch = (_cards_seq[_chosen].get("nickname")
                                or _cards_seq[_chosen].get("number", "")[-4:]) if _cards_seq[_chosen] else "—"
                    print(f"  {G}💳 Смена карты по запросу TG → {_nick_ch} (позиция {_chosen + 1}){RST}")
                    _tg_send_direct(f"🔄 *Возврат на страницу оплаты* — ввожу карту {_nick_ch}…")
                    _ci = _chosen
                else:
                    _ci += 1
                # Форсируем возврат на страницу ввода карты (сбрасываем «первый вход»)
                _first_entry = False
                continue

            if _pay_done in ("declined", "insufficient_funds"):
                _is_last_card = _ci + 1 >= len(_cards_seq)
                _reason_lbl = "Недостаточно средств" if _pay_done == "insufficient_funds" else "Карта отклонена"
                print(f"  {Y}{_reason_lbl} — {'пробую следующую карту' if not _is_last_card else 'карты закончились'}{RST}")
                if _is_last_card:
                    _send_tg_error(_pp_phone, f"{_reason_lbl} — все карты исчерпаны")
                _ci += 1
                continue

            if _pay_done:
                # Оплата запущена — идём на black-store только если Flipkart подтвердил
                try:
                    _post_result = await _handle_post_payment(
                        page, ctx, profile_path, phone_number=_pp_phone, months=months)
                except Exception as _pp_e:
                    print(f"  Post-payment: {_pp_e}")
                    _post_result = {}

                if _post_result.get("paid"):
                    break  # ✅ оплата подтверждена
                # Флипкарт не подтвердил — пробуем следующую карту
                _is_last_card = _ci + 1 >= len(_cards_seq)
                print(f"  {Y}Оплата не подтверждена — {'пробую следующую карту' if not _is_last_card else 'карты закончились'}{RST}")
                if _is_last_card:
                    _send_tg_error(_pp_phone, "Оплата не подтверждена — все карты исчерпаны")
            else:
                _is_last_card = _ci + 1 >= len(_cards_seq)
                print(f"  {Y}Карта не прошла — {'пробую следующую' if not _is_last_card else 'карты закончились'}{RST}")
                if _is_last_card:
                    _send_tg_error(_pp_phone, "Карта не прошла — все карты исчерпаны")

            _ci += 1

        base = f"✅ {addr_msg}" if addr_msg else "✅ Адрес уже был сохранён"
        if _post_result.get("paid"):
            _keep_open = False
            vt = _post_result.get("valid_till", "")
            return True, base + f" → ✅ Оплата прошла{(' (до ' + vt + ')') if vt else ''}"
        return True, base + (" → ⚠️ Оплата не подтверждена, браузер оставлен открытым"
                              if _keep_open else " → ⚠️ Оплата не подтверждена")
    except _PurchaseCancelled:
        print(f"  {Y}🛑 Выполнение отменено пользователем — закрываю браузер.{RST}")
        _keep_open = False
        return False, "CANCELLED"
    except Exception as exc:
        # Нажали «Остановить» → браузер убит, await упал. Это отмена, не ошибка.
        if _purchase_cancel.is_set():
            print(f"  {Y}🛑 Остановлено пользователем — браузер закрыт.{RST}")
            _keep_open = False
            return False, "CANCELLED"
        msg = str(exc)
        _keep_open = False
        return False, msg
    finally:
        set_profile_op_stage(profile_path, "")
        if not _keep_open and _owns_ctx:
            await _close_browser_session(
                ctx, pw, profile_path, disconnect_vpn=True,
            )
        elif not _keep_open and ctx and not _owns_ctx:
            # Чужой ctx (same session) — всё равно выключить VPN перед выходом сценария
            with contextlib.suppress(Exception):
                await _vpn_disconnect(ctx)


def screen_buy_membership():
    """Купить Black Membership: выбрать профиль и тариф, автоматически нажать Buy now."""
    while True:
        cls()
        header("КУПИТЬ BLACK MEMBERSHIP", Y)

        profiles = _load_done_profiles()

        if not profiles:
            print(f"  {DIM}Нет профилей с успешным входом.{RST}")
            pause()
            return

        section(f"Профили  [{len(profiles)} шт.]")
        print()
        for i, p in enumerate(profiles, 1):
            no_meta = p.get("login_ts") is None
            if p.get("issued_ts"):
                mark = f"{B}🔵 Выдан{RST}"
            elif no_meta:
                mark = f"{R}⚠ Нет данных{RST}"
            else:
                mark = f"{G}● Доступен{RST}"
            login_col = R if no_meta else DIM
            print(
                f"  {BLD}{Y}[{i:>2}]{RST}  {W}{_disp_phone(p['username']):<14}{RST}  "
                f"{login_col}{p['login_str']:<40}{RST}  {mark}"
            )
        print()
        no_data_count = sum(1 for p in profiles if p.get("login_ts") is None)
        if no_data_count:
            opt("9", f"Удалить все {no_data_count} профиля без данных", R)
        opt("0", "Назад", R)
        print()

        choice = input(
            f"  {BLD}Выберите профиль [1-{len(profiles)}], 9 или 0: {RST}"
        ).strip()

        if choice == "0" or choice == "":
            return

        if choice == "9" and no_data_count:
            import shutil as _sh, os as _os, stat as _stat
            def _rm_err2(func, path, exc_info):
                try:
                    _os.chmod(path, _stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            _locked2 = 0
            for p in profiles:
                if p.get("login_ts") is None:
                    _sh.rmtree(p["path"], onerror=_rm_err2)
                    if p["path"].exists():
                        print(f"  {Y}Файлы заняты — завершаю Chrome для {p['path'].name}...{RST}")
                        _kill_chrome_for_profile(p["path"])
                        time.sleep(2)
                        _sh.rmtree(p["path"], onerror=_rm_err2)
                    if not p["path"].exists():
                        print(f"  {G}Удалён: {p['path'].name}{RST}")
                    else:
                        print(f"  {R}Не удалён: {p['path'].name}{RST}")
                        _locked2 += 1
            if _locked2:
                print(f"\n  {Y}{_locked2} папок не удалось удалить{RST}")
            time.sleep(1.5)
            continue

        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(profiles)):
                raise ValueError
            selected = profiles[idx]
        except ValueError:
            print(f"\n  {R}Неверный номер.{RST}")
            time.sleep(3)
            continue

        # Выбор тарифа
        cls()
        header("ВЫБОР ТАРИФА", Y)
        print(f"  Профиль : {W}{BLD}{_disp_phone(selected['username'])}{RST}")
        print(f"  Вход    : {DIM}{selected['login_str']}{RST}")
        print()
        opt("1", "3 месяца  — ₹343  (скидка 20%)", G)
        opt("2", "12 месяцев — ₹1,499", C)
        print()
        opt("0", "Назад", R)
        print()

        tariff = input(f"  {BLD}Тариф [1/2/0]: {RST}").strip()
        if tariff == "0" or tariff == "":
            continue
        elif tariff == "1":
            months = 3
            label = "3 месяца / ₹343"
        elif tariff == "2":
            months = 12
            label = "12 месяцев / ₹1,499"
        else:
            print(f"\n  {R}Неверный выбор.{RST}")
            time.sleep(3)
            continue

        print(f"\n  {DIM}Запускаю браузер — {label}...{RST}")
        print(f"  {DIM}Карты берутся по установленному порядку (data/card_order.json){RST}")
        print(f"  {DIM}(если понадобится адрес — заполнится автоматически){RST}\n")
        ok, msg = asyncio.run(_do_buy_membership(selected["path"], months, card=None))
        if ok:
            print(f"  {G}{msg}{RST}")
        elif msg.startswith("OUT_OF_STOCK"):
            print(f"  {R}✘ Currently out of stock — с этим профилем уже ничего не сделать.{RST}")
            print(f"  {Y}→ Рекомендуется удалить профиль и купить со следующим доступным.{RST}")
            print()
            confirm = input(f"  {BLD}Удалить профиль сейчас? [Д/Н]: {RST}").strip().lower()
            if confirm in ("д", "y"):
                try:
                    shutil.rmtree(str(selected["path"]))
                    print(f"\n  {M}🗑 Профиль удалён.{RST}")
                except Exception as exc:
                    print(f"\n  {R}Ошибка удаления: {exc}{RST}")
        else:
            print(f"  {R}❌ Ошибка: {msg}{RST}")
        pause()
        return


_OTP_SEL = (
    "input[type='text']:not([readonly]):not([placeholder*='Mobile']):not([placeholder*='mobile']):not([placeholder*='Email']):not([placeholder*='email']):not([placeholder*='search']):not([placeholder*='Search']), "
    "input[type='number']:not([readonly]):not([placeholder*='Mobile']):not([placeholder*='mobile']):not([placeholder*='Email']):not([placeholder*='email']):not([placeholder*='search']):not([placeholder*='Search']), "
    "input[type='tel']:not([readonly]):not([placeholder*='Mobile']):not([placeholder*='mobile']):not([placeholder*='Email']):not([placeholder*='email']):not([placeholder*='search']):not([placeholder*='Search']), "
    "input[class*='r4vIwl']:not([readonly]), input.r4vIwl:not([readonly]), "
    "input[placeholder*='OTP'], input[placeholder*='otp']"
)
_PHONE_FIELD_JS = """
    () => {
        const el = [...document.querySelectorAll('input')].find(i => {
            const ph = (i.placeholder || '').toLowerCase();
            const tt = (i.title   || '').toLowerCase();
            if (ph.includes('search') || tt.includes('search')) return false;
            const r = i.getBoundingClientRect();
            return r.top > 40 && r.height > 10 && r.width > 50;
        });
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
    }
"""


async def _flipkart_phase1(page, login_url: str, phone_10: str) -> str:
    """
    Navigate to login_url, enter phone_10, click CONTINUE, wait for OTP field.
    Returns: "ok" | "blocked" | "error:<msg>"
    """
    try:
        cur = (page.url or "").lower()
    except Exception:
        cur = ""
    # Уже на login (после proxy/VPN open) — не грузим Flipkart повторно
    need_goto = "flipkart.com" not in cur or "login" not in cur
    if need_goto:
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=18_000)
        except Exception as exc:
            return f"error:goto failed: {exc}"

    if await _flipkart_page_access_denied(page):
        return "error:Access Denied (прокси не подошёл)"

    # Bot-challenge
    bc_dl = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < bc_dl:
        try:
            txt = await page.evaluate("() => document.body?.innerText?.slice(0,200) || ''")
        except Exception:
            break
        if not any(w in txt.lower() for w in ("recaptcha", "human", "confirming")):
            break
        await asyncio.sleep(1.0)

    # Find phone input
    rect = None
    fd_dl = asyncio.get_running_loop().time() + 12
    while asyncio.get_running_loop().time() < fd_dl:
        try:
            rect = await page.evaluate(_PHONE_FIELD_JS)
        except Exception:
            pass
        if rect:
            break
        await asyncio.sleep(0.3)
    if rect is None:
        try:
            _cur = page.url
            _body = (await page.evaluate("() => document.body?.innerText?.slice(0,300) || ''")).replace("\n", " ")
            return f"error:поле телефона не найдено | url={_cur} | page={_body[:200]}"
        except Exception:
            return "error:поле телефона не найдено"

    # Enter phone — human-like: move to near field, pause, move to field, click, type variably
    import random as _r
    try:
        await page.mouse.move(rect["x"] + _r.uniform(-50, 50), rect["y"] + _r.uniform(-25, 25))
        await asyncio.sleep(_r.uniform(0.05, 0.12))
        await page.mouse.click(rect["x"], rect["y"])
        await asyncio.sleep(0.12)
        await page.keyboard.press("Control+a")
        for ch in phone_10:
            await page.keyboard.type(ch)
            await asyncio.sleep(_r.uniform(0.03, 0.08))
        await asyncio.sleep(0.15)
    except Exception as exc:
        return f"error:ввод телефона: {exc}"

    actual = await page.evaluate("() => document.activeElement?.value || ''")
    if actual != phone_10:
        try:
            actual = await page.evaluate(
                """([x, y, ph]) => {
                    let el = document.elementFromPoint(x, y);
                    if (!el || el.tagName !== 'INPUT')
                        el = [...document.querySelectorAll('input')].find(i => {
                            const r = i.getBoundingClientRect();
                            return r.top > 40 && r.height > 10 && r.width > 50;
                        });
                    if (!el) return '';
                    el.focus();
                    const s = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    s.call(el, ph);
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return el.value;
                }""",
                [rect["x"], rect["y"], phone_10],
            )
        except Exception:
            pass
    if actual != phone_10:
        return f"error:не удалось ввести номер (got '{actual}')"

    # Click CONTINUE — human-like
    submit_sel = ("button:has-text('CONTINUE'), button:has-text('Continue'), "
                  "button:has-text('Request OTP')")
    try:
        btn = page.locator(submit_sel).last
        await page.wait_for_selector(submit_sel, timeout=10_000)
        await asyncio.sleep(_r.uniform(0.1, 0.3))
        await _human_click(page, btn)
    except Exception as exc:
        return f"error:CONTINUE не найдена: {exc}"

    # Handle unregistered numbers (Sign Up screen redirect)
    try:
        await asyncio.sleep(1.5)  # Wait for page/toast to update
        body_text = await page.evaluate("() => document.body?.innerText || ''")
        if "new here" in body_text.lower() or "not registered" in body_text.lower() or "sign up" in body_text.lower():
            print(f"  {Y}Обнаружен экран регистрации нового пользователя ('new here' / 'not registered'). Нажимаем CONTINUE повторно...{RST}")
            btn = page.locator(submit_sel).last
            await _human_click(page, btn)
            await asyncio.sleep(1.5)
    except Exception as exc:
         print(f"  {Y}Ошибка повторного нажатия CONTINUE для нового номера: {exc}{RST}")

    # Ждём ответа: либо OTP-поле, либо toast «Maximum attempts reached».
    # Toast появляется через 1–2s: первые 5s опрашиваем каждые 0.15s (быстрая фаза),
    # затем каждые 0.5s до 20s.
    _BLOCKED_WORDS = ("maximum attempts", "retry in 24", "blocked account",
                      "accountvalidation@flipkart")
    # Акamai / антифрод по IP прокси — OTP не уходит, остаёмся на login
    def _otp_rejected(txt: str) -> bool:
        t = txt.lower()
        return (
            "something's not right" in t
            or "somethings not right" in t
            or ("something" in t and "not right" in t and "try again" in t)
        )
    otp_appeared = False
    _loop_t = asyncio.get_running_loop()
    poll_dl = _loop_t.time() + 12
    _fast_until = _loop_t.time() + 5
    while _loop_t.time() < poll_dl:
        try:
            body = await page.evaluate(
                "() => (document.body?.innerText || document.body?.textContent || '')"
            )
            bl = body.lower()
            if any(w in bl for w in _BLOCKED_WORDS):
                return "blocked"
            if _otp_rejected(bl):
                return "error:OTP отклонён (Something's not right — прокси/IP)"
        except Exception:
            pass
        try:
            el = await page.query_selector(_OTP_SEL)
            if el and await el.is_visible():
                ph = ((await el.get_attribute("placeholder")) or "").lower()
                val = (await el.input_value()).strip()
                if "mobile" in ph or "email" in ph or "search" in ph:
                    pass
                elif len(val) >= 10 and val.isdigit():
                    pass
                else:
                    otp_appeared = True
                    break
        except Exception:
            pass
        await asyncio.sleep(0.15 if _loop_t.time() < _fast_until else 0.4)

    if not otp_appeared:
        try:
            body = await page.evaluate("() => document.body?.textContent || ''")
            bl = body.lower()
            if any(w in bl for w in _BLOCKED_WORDS):
                return "blocked"
            if _otp_rejected(bl):
                return "error:OTP отклонён (Something's not right — прокси/IP)"
        except Exception:
            pass
        try:
            txt2 = await page.evaluate("() => document.body?.innerText?.slice(0,200) || ''")
        except Exception:
            txt2 = ""
        return f"error:OTP поле не появилось ({txt2[:80]!r})"

    return "ok"


async def _enter_otp_on_page(page, otp_code: str, *, timeout_redirect: float = 22.0) -> bool:
    """
    Единый хелпер ввода OTP для всех сценариев входа.
    Ищет поле OTP, вводит код посимвольно (триггерит React onChange),
    кликает VERIFY, ждёт редиректа.
    Возвращает True если страница ушла с login-URL.
    """
    import random as _rn_otp

    _otp_el = None
    _dl = asyncio.get_event_loop().time() + 12
    while asyncio.get_event_loop().time() < _dl:
        for _fr in [page] + list(page.frames):
            try:
                _c = _fr.locator(_OTP_SEL).first
                if await _c.count() > 0 and await _c.is_visible():
                    _otp_el = _c
                    break
            except Exception:
                pass
        if _otp_el:
            break
        await page.wait_for_timeout(250)

    if _otp_el:
        try:
            _bb = await _otp_el.bounding_box()
            if _bb:
                await page.mouse.click(_bb["x"] + _bb["width"] / 2,
                                       _bb["y"] + _bb["height"] / 2)
            else:
                await _otp_el.click()
            await page.wait_for_timeout(150)
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Delete")
            for _ch in otp_code:
                await page.keyboard.type(_ch)
                await page.wait_for_timeout(int(_rn_otp.uniform(55, 105)))
                if "login" not in page.url.lower():
                    return True   # Flipkart auto-submitted after last digit
        except Exception as _oe:
            print(f"  {Y}OTP ввод: {_oe}{RST}")
            try:
                await page.keyboard.type(otp_code, delay=80)
            except Exception:
                pass
    else:
        print(f"  {Y}OTP поле не найдено — пробую keyboard{RST}")
        try:
            await page.keyboard.type(otp_code, delay=80)
        except Exception:
            pass

    if "login" not in page.url.lower():
        return True

    # Ждём пока React обновит state, потом кликаем VERIFY
    await page.wait_for_timeout(400)
    _otp_verify_sel = (
        "button:has-text('VERIFY'), button:has-text('Verify'), "
        "button:has-text('LOGIN'),  button:has-text('CONTINUE'), "
        "button:has-text('Continue'), button:has-text('Signup'), "
        "button:has-text('Sign up'), button:has-text('SIGNUP')"
    )
    _clicked_v = False
    for _fr in [page] + list(page.frames):
        try:
            _ob = _fr.locator(_otp_verify_sel).first
            if await _ob.is_visible():
                _obb = await _ob.bounding_box()
                if _obb:
                    await page.mouse.click(_obb["x"] + _obb["width"] / 2,
                                           _obb["y"] + _obb["height"] / 2)
                else:
                    await _ob.click()
                _clicked_v = True
                break
        except Exception:
            pass
    if not _clicked_v:
        try:
            if _otp_el:
                await _otp_el.press("Enter")
            else:
                await page.keyboard.press("Enter")
        except Exception:
            pass

    if "login" not in page.url.lower():
        return True

    # Ждём редирект
    await page.wait_for_timeout(1_200)
    if "login" in page.url.lower():
        try:
            await page.wait_for_url(
                lambda u: "login" not in u.lower(),
                timeout=int(timeout_redirect * 1_000))
        except Exception:
            pass

    return "login" not in page.url.lower()


async def _do_all_in_one(months: int, headless: bool = False, card: dict | None = None, skip_purchase: bool = False, max_par_override: int | None = None, intercept_mode: bool = False, stop_at_email: bool = False, _pay_lock: "asyncio.Lock | None" = None) -> tuple[bool, str]:
    """
    Полный цикл: GrizzlySMS номер → вход в Flipkart → адрес → Buy Now → Continue.
    При «Maximum attempts reached» отменяет номер и пробует следующий (до 5 раз).
    headless=True: браузер без окна; после успеха открывает профиль в видимом Chrome.
    Скорость: параллельный поиск номера (parallel_get_slots) + price_tiers как в main.py.

    _pay_lock: общий asyncio.Lock от вызывающего, который запускает несколько
    _do_all_in_one параллельно. Берётся ТОЛЬКО на фазу покупки (после входа:
    Buy Now → адрес → оплата), которая трогает общие синглтоны (_3ds_card_options,
    _switch_card_choice, _orders_confirm_choice). Поиск номеров, вход и проверка
    OTP остаются параллельными — лок берётся уже после успешного входа.
    """
    _pay_lock_held = False
    try:
        from playwright.async_api import async_playwright
        import yaml
        from grizzly_sms import (
            GrizzlySMSError,
            NumberUnavailableError,
            InsufficientBalanceError,
        )
        from sms_failover import build_sms_client
    except ImportError as e:
        return False, f"Зависимость не установлена: {e}  (pip install playwright httpx pyyaml)"

    try:
        with open("config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except Exception as exc:
        return False, f"Ошибка чтения config.yaml: {exc}"

    gsms        = cfg.get("grizzlysms", {})
    _secrets    = _read_secrets()
    service     = gsms.get("service", "xt")
    country     = gsms.get("country", 22)
    max_price   = gsms.get("max_price", 0.15)
    poll_int    = float(gsms.get("poll_interval", 3))
    gn_timeout  = float(gsms.get("get_number_timeout", 120))
    slots       = int(gsms.get("parallel_get_slots", 3))
    poll_delay  = float(gsms.get("get_number_retry_delay", 2.0))
    price_tiers  = gsms.get("price_tiers")   # None → max_price весь timeout
    cycle_prices = bool(gsms.get("cycle_prices", False))

    try:
        sms_client = build_sms_client(_secrets, cfg)
    except ValueError as exc:
        return False, str(exc)

    login_url = cfg.get("site", {}).get("url", "https://www.flipkart.com/account/login?ret=/")
    url       = _BLACK_URLS[months]
    # Loguru по умолчанию пишет весь DEBUG в stderr — перенастраиваем на INFO в stdout
    try:
        from loguru import logger as _lgu
        import sys as _sys
        _lgu.remove()
        _lgu.add(_sys.stdout, level="INFO",
                 format="  <level>{message}</level>",
                 colorize=False)
    except Exception:
        pass

    _failed_cancels: list = []  # IDs, которые не удалось отменить → фоновый повтор

    # Фоновый прогрев: доступен ли Flipkart напрямую (без VPN). Результат
    # кэшируется — к моменту запуска браузера решение уже готово.
    if _vpn_extension_dir():
        with contextlib.suppress(Exception):
            asyncio.create_task(_flipkart_direct_accessible())

    async def _tg_cancel_notify(ph: str, reason: str = "") -> None:
        """Шлёт TG-уведомление об отмене номера + остаток баланса GrizzlySMS."""
        if not _tg_notify_enabled():
            return
        try:
            import httpx as _hx_c, json as _jc
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jc.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _nc = [int(c) for c in _sd.get("chats", [])
                   if _ss.get(str(c), {}).get("buy_number", False)]
            if not _nc:
                return
            try:
                _bal = await sms_client.get_balance()
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

    async def _send_tg_otp(ph: str, code: str, label_suffix: str = "") -> None:
        """Отправляет OTP-код в Telegram."""
        if not _tg_notify_enabled():
            return
        try:
            import httpx as _hx_otp, json as _jo
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jo.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _chats = [int(c) for c in _sd.get("chats", [])
                      if _ss.get(str(c), {}).get("otp_code", True)]
            if not _chats:
                return
            _msg = f"🔑 *OTP получен{label_suffix}*\n\n`{ph}`\nКод: `{code}`"
            async with _hx_otp.AsyncClient(timeout=8, trust_env=False) as _client:
                for _chat in _chats:
                    try:
                        await _client.post(
                            f"https://api.telegram.org/bot{_tok}/sendMessage",
                            json={"chat_id": _chat, "text": _msg, "parse_mode": "Markdown"})
                    except Exception:
                        pass
        except Exception:
            pass

    async def _send_cookies_to_tg(ctx, phone_10: str) -> None:
        """Отправляет куки в Telegram (файл и текст) через общий хелпер."""
        try:
            await _send_cookies_tg(ctx, f"profile_{phone_10}", phone_10)
        except Exception:
            pass

    async def _send_tg_login_ok(ph: str) -> None:
        """Отправляет TG-уведомление об успешном входе."""
        if not _tg_notify_enabled():
            return
        try:
            import httpx as _hx_lo, json as _jlo
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jlo.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _nc = [int(c) for c in _sd.get("chats", [])
                   if _ss.get(str(c), {}).get("buy_number", False)]
            if not _nc:
                return
            async with _hx_lo.AsyncClient(timeout=8, trust_env=False) as _client:
                for _c in _nc:
                    try:
                        await _client.post(
                            f"https://api.telegram.org/bot{_tok}/sendMessage",
                            json={"chat_id": _c,
                                  "text": f"✅ *Вход выполнен*\n\n`{ph}`\n_Профиль готов_",
                                  "parse_mode": "Markdown"})
                    except Exception:
                        pass
        except Exception:
            pass

    async def _send_tg_buy(ph: str) -> None:
        """Отправляет TG-уведомление о покупке номера."""
        if not _tg_notify_enabled():
            return
        try:
            import httpx as _hx_b, json as _jb
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jb.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _nc = [int(c) for c in _sd.get("chats", [])
                   if _ss.get(str(c), {}).get("buy_number", False)]
            if not _nc:
                return
            async with _hx_b.AsyncClient(timeout=8, trust_env=False) as _client:
                for _c in _nc:
                    try:
                        await _client.post(
                            f"https://api.telegram.org/bot{_tok}/sendMessage",
                            json={"chat_id": _c,
                                  "text": f"📞 *Номер на Flipkart*\n\n`{ph}`\n_Жду OTP..._",
                                  "parse_mode": "Markdown"})
                    except Exception:
                        pass
        except Exception:
            pass

    try:
        # ── Отменяем все старые активации перед стартом ───────────────────────
        try:
            existing = await sms_client.get_active_activations()
            if existing:
                print(f"  {Y}Найдено {len(existing)} старых активаций — отменяю...{RST}")
                cancelled_old = 0
                for _act in existing:
                    _aid = str(_act.get("activationId") or _act.get("id") or "")
                    _aph = str(_act.get("phoneNumber") or _act.get("phone") or "")
                    if not _aid:
                        continue
                    try:
                        await sms_client.cancel(_aid)
                        print(f"  {DIM}  ✓ +{_aph} ({_aid}){RST}")
                        cancelled_old += 1
                    except Exception:
                        pass
                if cancelled_old:
                    try:
                        bal = await sms_client.get_balance()
                        print(f"  {G}Отменено {cancelled_old} активаций. 💰 ${bal:.4f}{RST}")
                    except Exception:
                        pass
            else:
                print(f"  {DIM}Старых активаций нет.{RST}")
        except Exception as exc:
            print(f"  {DIM}Проверка старых активаций: {exc}{RST}")

        # Проверяем доступность Flipkart один раз перед стартом (только если нет VPN —
        # с VPN пинг делается в CLI; повторный пинг здесь ломает VPN в рабочем браузере).
        if not _vpn_extension_dir():
            print(f"  {DIM}Проверка доступности Flipkart...{RST}")
            if not await _check_flipkart_accessible():
                return False, "Flipkart недоступен — запуск отменён"
            print(f"  {G}Flipkart доступен.{RST}")

        attempt = 0
        _vpn_fail_streak = 0
        _MAX_VPN_FAIL_STREAK = 5
        while True:
            attempt += 1
            pw           = await async_playwright().start()
            ctx          = None
            phone_id     = None
            profile_path: Path | None = None
            _keep_open   = False
            _try_next    = False
            _del_profile = False

            try:
                # ── 1. Получаем номер GrizzlySMS (параллельные слоты + price_tiers) ─
                print(f"\n  {C}[Попытка {attempt}] Ищу номер (Grizzly→PVAPins) "
                      f"({slots} слотов)...{RST}")
                try:
                    phone_id, phone, cost_gn = await sms_client.get_number_parallel(
                        service=service,
                        country=country,
                        max_price=max_price,
                        parallel_slots=slots,
                        poll_delay=poll_delay,
                        timeout=gn_timeout,
                        price_tiers=price_tiers,
                        cycle=True,
                    )
                except InsufficientBalanceError:
                    print(f"  {R}Недостаточно средств на балансе SMS — отменяю активные номера...{RST}")
                    try:
                        _acts = await sms_client.get_active_activations()
                        _n_cancelled = 0
                        for _a in _acts:
                            _aid = str(_a.get("activationId") or _a.get("id") or "")
                            if _aid:
                                try:
                                    await sms_client.cancel(_aid)
                                    _n_cancelled += 1
                                except Exception:
                                    pass
                        _new_bal = await sms_client.get_balance()
                        print(f"  {Y}Отменено {_n_cancelled} активаций. 💰 Баланс: ${_new_bal:.4f} — повторяю поиск...{RST}")
                    except Exception as _be:
                        print(f"  {Y}Ошибка при отмене активаций: {_be}{RST}")
                    await asyncio.sleep(5)
                    _try_next = True
                    continue
                except NumberUnavailableError as exc:
                    return False, f"Нет номеров: {exc}"
                except GrizzlySMSError as exc:
                    return False, f"SMS ошибка: {exc}"

                phone_10 = phone.lstrip("+")
                if phone_10.startswith("91") and len(phone_10) > 10:
                    phone_10 = phone_10[2:]
                phone_10 = phone_10[-10:]
                print(f"  {G}Номер получен: +91 {phone_10}  (id={phone_id}, цена={cost_gn}){RST}")

                # Регистрируем аренду немедленно, чтобы избежать утечек
                _grizzly_module.register_rental(phone_id, phone_10, time.monotonic(), pw=pw, login_url=login_url, months=months, intercept_mode=intercept_mode)

                # ── 2. Профиль и браузер ─────────────────────────────────────
                profile_path = DONE_PROFILES_DIR / f"profile_{phone_10}"
                profile_path.mkdir(parents=True, exist_ok=True)
                _pre_inject_chrome_prefs(profile_path)

                _grizzly_module.update_rental_browser(phone_id, profile_path=profile_path)

                # Вход без VeepN-расширения: прокси или прямой доступ (VPN на ПК)
                _, _proxy = await _resolve_flipkart_launch_network(
                    allow_vpn_extension=False,
                )
                if _proxy:
                    print(f"  {G}Вход через прокси {_proxy.get('server')} (без расширения).{RST}")
                else:
                    print(f"  {G}Вход без прокси и без расширения (VPN на ПК / напрямую).{RST}")

                _register_purchase_profile(profile_path)
                ctx = await pw.chromium.launch_persistent_context(
                    str(profile_path.resolve()),
                    **_browser_launch_kw(
                        headless=headless, phone=phone_10,
                        profile_path=profile_path,
                        use_vpn=False, proxy=_proxy,
                    ),
                )
                _mark_browser_network(ctx, use_vpn=False, proxy=_proxy)
                # без VeepN — не ждём вкладки расширения
                await asyncio.sleep(0.6 if _proxy else 1.2)
                await _close_extension_startup_tabs(ctx)
                _grizzly_module.update_rental_browser(phone_id, ctx=ctx)

                await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
                page = await _main_work_page(ctx)
                _grizzly_module.update_rental_browser(phone_id, page=page)
                if not headless:
                    await _maximize_window(ctx, page)

                # Access Denied = прокси не подошёл → другой прокси / напрямую
                _open_ok = False
                for _prx_try in range(4 if _proxy else 1):
                    print(f"  {DIM}→ Flipkart (прокси/direct)…{RST}")
                    ok, page = await _open_flipkart_page(
                        ctx, login_url, label=phone_10, work_page=page,
                    )
                    if ok:
                        _open_ok = True
                        break
                    if _proxy and _proxy.get("_free_host"):
                        _mark_free_proxy_dead(_proxy["_free_host"])
                    print(
                        f"  {Y}⚠ Access Denied / прокси не подошёл "
                        f"({_proxy.get('server') if _proxy else 'direct'}) — "
                        f"попытка {_prx_try + 1}/4…{RST}"
                    )
                    with contextlib.suppress(Exception):
                        await ctx.close()
                    ctx = None
                    _, _proxy = await _resolve_flipkart_launch_network(
                        allow_vpn_extension=False,
                    )
                    if _proxy:
                        print(f"  {DIM}другой прокси: {_proxy.get('server')}{RST}")
                    else:
                        print(f"  {DIM}прямой доступ (без прокси){RST}")
                    ctx = await pw.chromium.launch_persistent_context(
                        str(profile_path.resolve()),
                        **_browser_launch_kw(
                            headless=headless, phone=phone_10,
                            profile_path=profile_path,
                            use_vpn=False, proxy=_proxy,
                        ),
                    )
                    _mark_browser_network(ctx, use_vpn=False, proxy=_proxy)
                    await asyncio.sleep(0.5)
                    await _close_extension_startup_tabs(ctx)
                    await ctx.grant_permissions(
                        ["geolocation"], origin="https://www.flipkart.com",
                    )
                    page = await _main_work_page(ctx)
                    if not headless:
                        await _maximize_window(ctx, page)
                if not _open_ok:
                    print(f"  {R}Flipkart недоступен (прокси/прямой доступ) — без расширения{RST}")
                    _try_next = True
                    _del_profile = True
                    _grizzly_module.mark_failed(phone_id)
                    continue
                _grizzly_module.update_rental_browser(phone_id, page=page)
                print(
                    f"  {G}Flipkart доступен через "
                    f"{'прокси' if _proxy else 'прямой доступ'}.{RST}"
                )

                stealth = _build_stealth_js_m()
                if stealth:
                    await ctx.add_init_script(stealth)

                def _on_new_page(p):
                    async def _check():
                        try:
                            await p.wait_for_load_state("domcontentloaded", timeout=5_000)
                            u = (p.url or "").lower()
                            if (
                                "terms" in u
                                or _is_vpn_junk_url(u)
                                or u.startswith("chrome-extension://")
                            ):
                                await p.close()
                                await page.bring_to_front()
                        except Exception:
                            pass
                    asyncio.create_task(_check())
                ctx.on("page", _on_new_page)

                # ── 3. Phase1: вход первым номером ───────────────────────────
                print(f"  {DIM}Ввожу номер +91 {phone_10}...{RST}")
                p1 = await _flipkart_phase1(page, login_url, phone_10)
                if p1 == "blocked":
                    print(f"  {Y}⚠ Maximum attempts +91 {phone_10} — удаляю профиль, беру другой...{RST}")
                    _try_next = True
                    _del_profile = True
                    _grizzly_module.mark_failed(phone_id)
                    continue
                if p1 != "ok":
                    # Без расширения: другой прокси или следующий номер
                    if _proxy and p1.startswith("error:") and _proxy.get("_free_host"):
                        _mark_free_proxy_dead(_proxy["_free_host"])
                        print(f"  {Y}⚠ Вход не удался — другой прокси / номер…{RST}")
                        _try_next = True
                        _del_profile = True
                        _grizzly_module.mark_failed(phone_id)
                        continue
                    return False, p1.removeprefix("error:")

                _grizzly_module.mark_phase1_ok(phone_id)
                await _send_tg_buy(phone_10)

                # ── 3b. Параллельный поиск номеров + ожидание OTP ───────────
                # Все номера ищутся СРАЗУ в фоне, OTP полится у всех параллельно.
                # _active: [act_id, phone_10, page, bought_at_monotonic, ctx]
                _active    = [[phone_id, phone_10, page, time.monotonic(), ctx]]
                _pending   : list = []
                _buy_tasks : list = []
                otp_code     = None
                _loser_tasks: list = []
                win_id       = phone_id
                win_ph     = phone_10
                win_page   = page
                win_ctx    = ctx

                if max_par_override is not None:
                    max_par = max_par_override
                else:
                    max_par = int(gsms.get("max_parallel_numbers", 15))
                num_lifetime = float(gsms.get("number_lifetime_seconds", 150.0))
                hard_dl      = time.monotonic() + max(300.0, num_lifetime * 2)

                _BLKW    = ("maximum attempts", "retry in 24", "blocked account",
                            "accountvalidation@flipkart")
                _num_seq  = [2]
                _tier_seq = [0]  # смещение стартового тира для каждой задачи
                stop_spawning = [False]

                async def _bg_buy() -> None:
                    n = _num_seq[0]; _num_seq[0] += 1
                    # Распределяем задачи по разным стартовым ценам
                    t_off = _tier_seq[0]; _tier_seq[0] += 1
                    if price_tiers and len(price_tiers) > 1:
                        s = t_off % len(price_tiers)
                        _tiers = price_tiers[s:] + price_tiers[:s]
                    else:
                        _tiers = price_tiers

                    n_ctx = None
                    npage = None
                    nid = None
                    nph10 = None
                    n_profile_path = None
                    phase1_done = False
                    try:
                        nid, nph, ncost = await sms_client.get_number_parallel(
                            service=service, country=country,
                            max_price=max_price, parallel_slots=slots,
                            poll_delay=poll_delay, timeout=max(60.0, hard_dl - time.monotonic()),
                            price_tiers=_tiers, cycle=True,
                        )
                        nph10 = nph.lstrip("+")
                        if nph10.startswith("91") and len(nph10) > 10:
                            nph10 = nph10[2:]
                        nph10 = nph10[-10:]
                        print(f"  {G}Номер #{n}: +91 {nph10} (${ncost:.3f}){RST}")

                        # Регистрируем аренду немедленно, чтобы избежать утечек
                        _grizzly_module.register_rental(nid, nph10, time.monotonic(), pw=pw, login_url=login_url, months=months, intercept_mode=intercept_mode)

                        n_profile_path = DONE_PROFILES_DIR / f"profile_{nph10}"
                        n_profile_path.mkdir(parents=True, exist_ok=True)
                        _pre_inject_chrome_prefs(n_profile_path)
                        _grizzly_module.update_rental_browser(nid, profile_path=n_profile_path)

                        # Вход нового номера — только прокси/direct, без VeepN
                        n_ok = False
                        _skip_hosts_n: set[str] = set()
                        for _att_n in range(4):
                            _, _proxy_n = await _resolve_flipkart_launch_network(
                                allow_proxy=True, allow_vpn_extension=False,
                            )
                            if (
                                _proxy_n
                                and _proxy_n.get("_free_host")
                                and _proxy_n["_free_host"] in _skip_hosts_n
                            ):
                                _proxy_n = None
                            try:
                                if n_ctx is not None:
                                    with contextlib.suppress(Exception):
                                        await n_ctx.close()
                                    n_ctx = None
                                n_ctx = await pw.chromium.launch_persistent_context(
                                    str(n_profile_path.resolve()),
                                    **_browser_launch_kw(
                                        headless=headless, phone=nph10,
                                        profile_path=n_profile_path,
                                        use_vpn=False, proxy=_proxy_n,
                                    ),
                                )
                                _mark_browser_network(n_ctx, use_vpn=False, proxy=_proxy_n)
                                _grizzly_module.update_rental_browser(nid, ctx=n_ctx)
                                await n_ctx.grant_permissions(
                                    ["geolocation"], origin="https://www.flipkart.com",
                                )
                                npage = n_ctx.pages[0] if n_ctx.pages else await n_ctx.new_page()
                                _grizzly_module.update_rental_browser(nid, page=npage)
                                ok, npage = await _open_flipkart_page(
                                    n_ctx, login_url, label=nph10,
                                )
                                if ok and not await _flipkart_page_access_denied(npage):
                                    _grizzly_module.update_rental_browser(nid, page=npage)
                                    n_ok = True
                                    break
                                if _proxy_n and _proxy_n.get("_free_host"):
                                    _mark_free_proxy_dead(_proxy_n["_free_host"])
                                    _skip_hosts_n.add(_proxy_n["_free_host"])
                                if _att_n >= 2:
                                    # последний заход — direct
                                    with contextlib.suppress(Exception):
                                        await n_ctx.close()
                                    n_ctx = await pw.chromium.launch_persistent_context(
                                        str(n_profile_path.resolve()),
                                        **_browser_launch_kw(
                                            headless=headless, phone=nph10,
                                            profile_path=n_profile_path,
                                            use_vpn=False, proxy=None,
                                        ),
                                    )
                                    _grizzly_module.update_rental_browser(nid, ctx=n_ctx)
                                    await n_ctx.grant_permissions(
                                        ["geolocation"], origin="https://www.flipkart.com",
                                    )
                                    npage = (
                                        n_ctx.pages[0] if n_ctx.pages
                                        else await n_ctx.new_page()
                                    )
                                    ok, npage = await _open_flipkart_page(
                                        n_ctx, login_url, label=nph10,
                                    )
                                    if ok and not await _flipkart_page_access_denied(npage):
                                        _grizzly_module.update_rental_browser(nid, page=npage)
                                        n_ok = True
                                    break
                            except Exception as _ne:
                                print(f"  {Y}⚠ Номер #{n} сеть: {_ne}{RST}")
                                continue
                        if not n_ok:
                            print(f"  {R}Flipkart недоступен — номер #{n} отменён{RST}")
                            _grizzly_module.mark_failed(nid)
                            if n_ctx is not None:
                                with contextlib.suppress(Exception):
                                    await n_ctx.close()
                            return
                        if stealth:
                            await n_ctx.add_init_script(stealth)

                        def _on_n_page(p):
                            async def _check():
                                try:
                                    await p.wait_for_load_state("domcontentloaded", timeout=5_000)
                                    if "terms" in p.url.lower():
                                        await p.close()
                                        await npage.bring_to_front()
                                except Exception:
                                    pass
                            asyncio.create_task(_check())
                        n_ctx.on("page", _on_n_page)

                        r2 = await _flipkart_phase1(npage, login_url, nph10)
                        if r2 == "ok":
                            phase1_done = True
                            _grizzly_module.mark_phase1_ok(nid)
                            await _send_tg_buy(nph10)
                            _pending.append([nid, nph10, npage, time.monotonic(), n_ctx])
                            print(f"  {G}Номер #{n} готов, жду OTP...{RST}")
                        else:
                            print(f"  {Y}Номер #{n} не прошёл ({r2}){RST}")
                            # Прокси не открыл Flipkart — убрать из пула, чтобы
                            # следующие потоки взяли другой прокси/VeePN
                            if _proxy_n and r2.startswith("error:") and _proxy_n.get("_free_host"):
                                _mark_free_proxy_dead(_proxy_n["_free_host"])
                            _grizzly_module.mark_failed(nid)
                            if n_ctx:
                                try: await n_ctx.close()
                                except Exception: pass
                            if n_profile_path:
                                try:
                                    import shutil as _sh
                                    _sh.rmtree(n_profile_path, ignore_errors=True)
                                except Exception: pass
                    except asyncio.CancelledError:
                        if nid and not phase1_done:
                            _grizzly_module.mark_failed(nid)
                        if n_ctx:
                            try: await n_ctx.close()
                            except Exception: pass
                        if n_profile_path:
                            try:
                                import shutil as _sh
                                _sh.rmtree(n_profile_path, ignore_errors=True)
                            except Exception: pass
                    except Exception as exc:
                        print(f"  {Y}Доп. номер #{n}: {exc}{RST}")
                        if isinstance(exc, InsufficientBalanceError) or "NO_BALANCE" in str(exc) or "Недостаточно средств" in str(exc):
                            print(f"  {R}Недостаточно средств (доп. номер) — отменяю все активные номера...{RST}")
                            try:
                                _acts = await sms_client.get_active_activations()
                                _n_c = 0
                                for _a in _acts:
                                    _aid = str(_a.get("activationId") or _a.get("id") or "")
                                    if _aid:
                                        try:
                                            await sms_client.cancel(_aid)
                                            _n_c += 1
                                        except Exception:
                                            pass
                                _nb = await sms_client.get_balance()
                                print(f"  {Y}Отменено {_n_c} активаций. 💰 Баланс: ${_nb:.4f} — продолжаю...{RST}")
                            except Exception as _cbe:
                                print(f"  {Y}Ошибка при отмене: {_cbe}{RST}")
                            await asyncio.sleep(5)
                            # Не устанавливаем stop_spawning — внешний цикл запустит новую задачу
                        if nid:
                            _grizzly_module.mark_failed(nid)
                        if n_ctx:
                            try: await n_ctx.close()
                            except Exception: pass
                        if n_profile_path:
                            try:
                                import shutil as _sh
                                _sh.rmtree(n_profile_path, ignore_errors=True)
                            except Exception: pass
                    finally:
                        if nid and not phase1_done:
                            _grizzly_module.mark_failed(nid)

                try:
                    async def _poll_entry(entry):
                        a_id, a_ph, a_pg, _, a_ctx = entry
                        try:
                            txt = await a_pg.evaluate(
                                "() => (document.body?.innerText || '').toLowerCase()"
                            )
                            if any(w in txt for w in _BLKW):
                                return ("blocked", a_id, a_ph, a_pg, None)
                        except Exception:
                            pass
                        try:
                            st = await sms_client.get_status(a_id)
                            return (st["type"], a_id, a_ph, a_pg, st.get("code"))
                        except Exception:
                            return ("error", a_id, a_ph, a_pg, None)

                    for _ in range(max_par - 1):
                        if not stop_spawning[0]:
                            _buy_tasks.append(asyncio.create_task(_bg_buy()))
                    print(f"  {DIM}Запущен поиск {max_par} номеров параллельно "
                          f"(лайфтайм {int(num_lifetime)}s)...{RST}")

                    while time.monotonic() < hard_dl and otp_code is None:
                        _now = time.monotonic()

                        if _pending:
                            _active.extend(_pending)
                            _pending.clear()

                        _buy_tasks = [t for t in _buy_tasks if not t.done()]
                        total_ifl  = len(_active) + len(_buy_tasks) + len(_pending)
                        if total_ifl < max_par and not stop_spawning[0]:
                            for _ in range(max_par - total_ifl):
                                _buy_tasks.append(asyncio.create_task(_bg_buy()))

                        expired = [e for e in _active if _now - e[3] >= num_lifetime]
                        if expired:
                            async def _do_expire(e_id, e_ph, e_pg, e_ctx):
                                try:
                                    _est = await sms_client.get_status(e_id)
                                    if _est.get("type") == "OK" and _est.get("code"):
                                        _grizzly_module.mark_otp_received(e_id)
                                        if intercept_mode:
                                            print(f"  {G}+91 {e_ph}: OTP на таймауте получен (перехват) → завершаю{RST}")
                                            await _send_tg_otp(e_ph, _est['code'], " (перехват)")
                                            try: await sms_client.complete(e_id)
                                            except Exception: pass
                                            _grizzly_module.mark_completed(e_id)
                                        else:
                                            print(f"  {G}+91 {e_ph}: OTP на таймауте получен → фон{RST}")
                                            _submit_bg_login(
                                                (_read_secrets().get("pvapins") or {}).get("api_key", "").strip()
                                                if str(e_id).startswith("pva:")
                                                else (_read_secrets().get("grizzlysms") or {}).get("api_key", "").strip(),
                                                e_id, _est["code"], login_url, months,
                                                             phone_10=e_ph)
                                            _grizzly_module.mark_completed(e_id)
                                        try:
                                            if e_ctx: await e_ctx.close()
                                        except Exception:
                                            try: await e_pg.close()
                                            except Exception: pass
                                        return
                                except Exception: pass
                                print(f"  {Y}+91 {e_ph}: таймаут — отменяю в фоне (retry 10s){RST}")
                                _grizzly_module.mark_failed(e_id)
                                try:
                                    if e_ctx: await e_ctx.close()
                                except Exception:
                                    try: await e_pg.close()
                                    except Exception: pass
                                try:
                                    import shutil as _sh
                                    _sh.rmtree(DONE_PROFILES_DIR / f"profile_{e_ph}", ignore_errors=True)
                                except Exception: pass
                            await asyncio.gather(*[_do_expire(e[0], e[1], e[2], e[4]) for e in expired])
                            _active = [e for e in _active if _now - e[3] < num_lifetime]

                        if not _active:
                            if _buy_tasks:
                                await asyncio.sleep(poll_int)
                                continue
                            break

                        poll_res = await asyncio.gather(*[_poll_entry(e) for e in list(_active)])

                        for res in poll_res:
                            rtype, a_id, a_ph, a_pg, a_code = res
                            if rtype == "blocked":
                                print(f"  {Y}⚠ +91 {a_ph}: Maximum attempts — отменяю{RST}")
                                _grizzly_module.mark_failed(a_id)
                                a_ctx = next((e[4] for e in _active if e[0] == a_id), None)
                                try:
                                    if a_ctx: await a_ctx.close()
                                except Exception:
                                    try: await a_pg.close()
                                    except Exception: pass
                                try:
                                    import shutil as _sh
                                    _sh.rmtree(DONE_PROFILES_DIR / f"profile_{a_ph}", ignore_errors=True)
                                except Exception: pass
                                _active = [e for e in _active if e[0] != a_id]
                                if a_id == phone_id:
                                    _del_profile = True
                                    _try_next = True
                            elif rtype == "OK" and a_code and otp_code is None:
                                otp_code = a_code
                                _grizzly_module.mark_otp_received(a_id)
                                win_id, win_ph, win_page = a_id, a_ph, a_pg
                                win_ctx = next((e[4] for e in _active if e[0] == a_id), None)
                                print(f"  {G}OTP для +91 {a_ph}: ***{otp_code[-2:]}{RST}")
                                await _send_tg_otp(a_ph, otp_code)
                            elif rtype == "CANCEL":
                                print(f"  {DIM}+91 {a_ph}: отменён GrizzlySMS{RST}")
                                _grizzly_module.mark_completed(a_id)
                                a_ctx = next((e[4] for e in _active if e[0] == a_id), None)
                                _active = [e for e in _active if e[0] != a_id]
                                try:
                                    if a_ctx: await a_ctx.close()
                                except Exception:
                                    try: await a_pg.close()
                                    except Exception: pass
                                try:
                                    import shutil as _sh
                                    _sh.rmtree(DONE_PROFILES_DIR / f"profile_{a_ph}", ignore_errors=True)
                                except Exception: pass

                        if otp_code:
                            async def _do_loser(o_id, o_ph, o_pg, o_ctx):
                                has_otp = False
                                _loser_login_ok = False
                                _deadline = time.monotonic() + 180.0
                                try:
                                    while time.monotonic() < _deadline:
                                        try:
                                            _lst = await sms_client.get_status(o_id)
                                        except Exception:
                                            await asyncio.sleep(poll_int)
                                            continue

                                        if _lst.get("type") == "OK" and _lst.get("code"):
                                            has_otp = True
                                            if intercept_mode:
                                                print(f"  {G}[BG] +91 {o_ph}: OTP получен (перехват) → завершаю{RST}")
                                                await _send_tg_otp(o_ph, _lst['code'], " (перехват)")
                                                try: await sms_client.complete(o_id)
                                                except Exception: pass
                                                _grizzly_module.mark_completed(o_id)
                                                _loser_login_ok = True
                                            else:
                                                _loser_otp = _lst["code"]
                                                print(f"  {G}[BG] +91 {o_ph}: OTP получен — вхожу{RST}")
                                                try:
                                                    _loser_login_ok = await _enter_otp_on_page(
                                                        o_pg, _loser_otp, timeout_redirect=22.0)
                                                except Exception as _le:
                                                    print(f"  {Y}[BG] +91 {o_ph} ошибка входа: {_le}{RST}")
                                                if _loser_login_ok:
                                                    try: await sms_client.complete(o_id)
                                                    except Exception: pass
                                                    _grizzly_module.mark_completed(o_id)
                                                    try:
                                                        _lp = DONE_PROFILES_DIR / f"profile_{o_ph}"
                                                        (_lp / ".profile_meta.json").write_text(
                                                            json.dumps({"username": o_ph, "login_ts": time.time(),
                                                                        "otp_code": _loser_otp, "source": "parallel_loser"},
                                                                       ensure_ascii=False), encoding="utf-8")
                                                        _grizzly_module._STATS["profiles_saved"] += 1
                                                    except Exception: pass
                                                    print(f"  {G}[BG✓] Профиль +91 {o_ph} сохранён{RST}")
                                                    try:
                                                        await _send_tg_login_ok(o_ph)
                                                    except Exception: pass
                                                else:
                                                    print(f"  {Y}[BG] +91 {o_ph} вход не прошёл — отменяю{RST}")
                                                    _grizzly_module.mark_failed(o_id)
                                            break

                                        elif _lst.get("type") in ("CANCEL", "UNKNOWN"):
                                            break

                                        # Пробуем отменить (кулдаун мог пройти)
                                        try:
                                            await sms_client.cancel(o_id)
                                            break
                                        except Exception:
                                            pass

                                        await asyncio.sleep(poll_int)
                                except Exception: pass
                                if not has_otp:
                                    _grizzly_module.mark_failed(o_id)
                                if not _loser_login_ok:
                                    try:
                                        if o_ctx: await o_ctx.close()
                                    except Exception:
                                        try: await o_pg.close()
                                        except Exception: pass
                                if not has_otp or intercept_mode or not _loser_login_ok:
                                    try:
                                        import shutil as _sh
                                        _sh.rmtree(DONE_PROFILES_DIR / f"profile_{o_ph}", ignore_errors=True)
                                    except Exception: pass
                            losers = [e for e in _active if e[0] != win_id]
                            _loser_tasks = [asyncio.create_task(_do_loser(e[0], e[1], e[2], e[4])) for e in losers]
                            for t in _buy_tasks:
                                t.cancel()
                            _buy_tasks.clear()
                            break

                        if _try_next:
                            break

                        if not _active and not _buy_tasks:
                            return False, "Все номера отменены GrizzlySMS"

                        phones_str = " | ".join(f"+91 {e[1]}" for e in _active)
                        buy_str    = f" +{len(_buy_tasks)}" if _buy_tasks else ""
                        print(f"  {DIM}[{phones_str}]{buy_str}{RST}", end="\r")
                        await asyncio.sleep(poll_int)

                    if not otp_code:
                        for t in _buy_tasks:
                            t.cancel()
                        _buy_tasks.clear()
                        for o_id, o_ph, o_pg, _, o_ctx in list(_active) + list(_pending):
                            _fin_otp = False
                            try:
                                _fst = await sms_client.get_status(o_id)
                                if _fst.get("type") == "OK" and _fst.get("code"):
                                    _fin_otp = True
                                    _grizzly_module.mark_otp_received(o_id)
                                    if intercept_mode:
                                        print(f"  {G}+91 {o_ph}: финальный OTP получен (перехват) → завершаю{RST}")
                                        await _send_tg_otp(o_ph, _fst['code'], " (перехват)")
                                        try: await sms_client.complete(o_id)
                                        except Exception: pass
                                        _grizzly_module.mark_completed(o_id)
                                    else:
                                        print(f"  {G}+91 {o_ph}: финальный OTP получен → фон{RST}")
                                        _submit_bg_login(
                                            (_read_secrets().get("pvapins") or {}).get("api_key", "").strip()
                                            if str(o_id).startswith("pva:")
                                            else (_read_secrets().get("grizzlysms") or {}).get("api_key", "").strip(),
                                            o_id, _fst["code"], login_url, months, phone_10=o_ph)
                                        _grizzly_module.mark_completed(o_id)
                            except Exception: pass
                            if not _fin_otp:
                                _grizzly_module.mark_failed(o_id)
                            try:
                                if o_ctx: await o_ctx.close()
                            except Exception:
                                try: await o_pg.close()
                                except Exception: pass
                            if not _fin_otp or intercept_mode:
                                try:
                                    import shutil as _sh
                                    _sh.rmtree(DONE_PROFILES_DIR / f"profile_{o_ph}", ignore_errors=True)
                                except Exception: pass
                        _active.clear()
                        _pending.clear()
                        if not _try_next:
                            print(f"  {R}OTP не пришёл — пробую новый номер...{RST}")
                        _try_next = True
                        continue
                finally:
                    # Clean up all background buy tasks
                    for t in _buy_tasks:
                        if not t.done():
                            t.cancel()
                    _buy_tasks.clear()

                    # _active: закрываем ctx (_do_loser уже обработал их профили)
                    for entry in list(_active):
                        o_id = entry[0]; o_pg = entry[2]
                        o_ctx = entry[4] if len(entry) > 4 else None
                        if o_id != win_id:
                            _grizzly_module.mark_failed(o_id)
                            try:
                                if o_ctx: await o_ctx.close()
                                elif o_pg: await o_pg.close()
                            except Exception:
                                pass

                    # _pending: закрываем ctx И удаляем папку (OTP не пришёл — в аккаунты не попали)
                    for entry in list(_pending):
                        o_id = entry[0]; o_ph = entry[1]; o_pg = entry[2]
                        o_ctx = entry[4] if len(entry) > 4 else None
                        _grizzly_module.mark_failed(o_id)
                        try:
                            if o_ctx: await o_ctx.close()
                            elif o_pg: await o_pg.close()
                        except Exception:
                            pass
                        try:
                            import shutil as _sh_pd
                            _sh_pd.rmtree(DONE_PROFILES_DIR / f"profile_{o_ph}", ignore_errors=True)
                        except Exception:
                            pass
                    _pending.clear()

                # Победитель определён
                is_first_winner = (win_id == _active[0][0])
                phone_id = win_id
                phone_10 = win_ph
                page     = win_page
                profile_path = DONE_PROFILES_DIR / f"profile_{phone_10}"

                if intercept_mode:
                    # В режиме перехвата (Подбор аккаунта TG)
                    print(f"  {G}✔ OTP получен для +91 {phone_10} (отправлено в TG){RST}")
                    try:
                        await sms_client.complete(phone_id)
                    except Exception:
                        pass
                    _grizzly_module.mark_completed(phone_id)
                    
                    # Закрываем и удаляем профиль
                    try:
                        if win_ctx: await win_ctx.close()
                    except Exception:
                        try: await win_page.close()
                        except Exception: pass
                    try:
                        import shutil as _sh
                        _sh.rmtree(profile_path, ignore_errors=True)
                    except Exception: pass
                    
                    # Отменяем остальные фоновые задачи покупки
                    for t in _buy_tasks:
                        t.cancel()
                    _buy_tasks.clear()
                    
                    # Чистим оставшиеся неактивные номера
                    losers = [e for e in _active if e[0] != win_id]
                    # Запускаем фоновые задачи очистки в фоне без ожидания
                    for e in losers:
                        asyncio.create_task(_do_loser(e[0], e[1], e[2], e[4]))
                    
                    return True, f"OTP отправлен в TG: +91 {phone_10}"

                if not is_first_winner:
                    ctx = win_ctx

                # ── 3c. Вводим OTP и завершаем вход ──────────────────────────
                print(f"  {DIM}Ввожу OTP для +91 {phone_10}...{RST}")
                _login_ok = await _enter_otp_on_page(page, otp_code)

                if not _login_ok:
                    _try_next = True
                    print(f"  {R}Вход не выполнен после OTP (+91 {phone_10}) — пробую новый номер...{RST}")
                    _grizzly_module.mark_failed(phone_id)
                    continue

                try:
                    await sms_client.complete(phone_id)
                except Exception:
                    pass
                _grizzly_module.mark_completed(phone_id)

                if _loser_tasks:
                    await asyncio.gather(*_loser_tasks, return_exceptions=True)
                    _loser_tasks = []

                try:
                    (profile_path / ".profile_meta.json").write_text(
                        json.dumps({
                            "username": phone_10,
                            "login_ts": time.time(),
                            "otp_code": otp_code,
                            "site_url": url.split("?")[0],
                        }, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    _grizzly_module._STATS["profiles_saved"] += 1
                except Exception:
                    pass
                print(f"  {G}✔ Вход выполнен: +91 {phone_10}{RST}")
                try:
                    await _send_tg_login_ok(phone_10)
                except Exception:
                    pass
                # Re-grant после входа — страница сменилась на Flipkart главную
                await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")

                # Сразу после входа — проверяем нет ли уже купленного Black Membership
                _lo_orders = await _check_recent_black_orders(page)
                if _lo_orders:
                    _lo_info = "; ".join(_lo_orders[:3])
                    print(f"\n  {Y}╔══ ВНИМАНИЕ: уже куплено! ════════════════════════════════╗{RST}")
                    print(f"  {Y}║  {_lo_info[:70]}{RST}")
                    print(f"  {Y}╚═══════════════════════════════════════════════════════════╝{RST}")
                    if skip_purchase:
                        # Режим входа — просто уведомляем TG и сохраняем профиль
                        _tg_send_direct(
                            f"ℹ️ *Вход выполнен (есть заказ BLACK)*\n\n"
                            f"Профиль `{phone_10}` — найден заказ *Flipkart BLACK*:\n"
                            f"_{_lo_info[:200]}_\n\n"
                            f"Профиль сохранён."
                        )
                    else:
                        _orders_confirm_ev.clear()
                        _orders_confirm_choice[0] = None
                        _tg_send_direct_kb(
                            f"⚠️ *Уже куплено!*\n\n"
                            f"Профиль `{phone_10}` — найден заказ *Flipkart BLACK*:\n"
                            f"_{_lo_info[:200]}_\n\n"
                            f"Что делать?",
                            {"inline_keyboard": [
                                [{"text": "✅ Продолжить покупку", "callback_data": f"fill:orders_ok:{phone_10}"}],
                                [{"text": "🗑 Удалить профиль",   "callback_data": f"fill:orders_del:{phone_10}"}],
                            ]}
                        )
                        print(f"  {Y}Жду ответа в Telegram (60 сек)...{RST}")
                        _lo_dl = asyncio.get_event_loop().time() + 60
                        while asyncio.get_event_loop().time() < _lo_dl:
                            if _orders_confirm_ev.is_set():
                                break
                            await asyncio.sleep(1)
                        if _orders_confirm_choice[0] is False:
                            print(f"  {R}Удаляю профиль {phone_10}...{RST}")
                            _keep_open = False
                            import shutil as _sh_lo
                            _sh_lo.rmtree(str(profile_path), ignore_errors=True)
                            _tg_send_direct(f"🗑 Профиль `{phone_10}` удалён (дублирующий заказ)")
                            return False, "Профиль удалён — дублирующий заказ"
                        print(f"  {G}Продолжаю покупку...{RST}")
                        # Возвращаемся на главную для дальнейшей навигации
                        try:
                            await page.goto("https://www.flipkart.com",
                                            wait_until="domcontentloaded", timeout=12_000)
                        except Exception:
                            pass

                if skip_purchase:
                    try:
                        await _send_cookies_to_tg(ctx, phone_10)
                    except Exception:
                        pass
                    _keep_open = True
                    return True, f"Вход выполнен: +91 {phone_10}"

                # ── 4. Buy Now на странице товара ────────────────────────────
                # Фаза покупки трогает общие синглтоны — сериализуем её среди
                # параллельных _do_all_in_one (вход/поиск номеров уже позади).
                if _pay_lock is not None and not _pay_lock_held:
                    if _pay_lock.locked():
                        print(f"  {DIM}Покупка занята другим аккаунтом — жду очереди...{RST}")
                    await _pay_lock.acquire()
                    _pay_lock_held = True
                print(f"  {DIM}Перехожу на страницу товара...{RST}")
                err = await _click_buy_now(page, url)
                if err:
                    return False, err

                addr_msg = ""
                addr_oi  = None  # адрес, использованный при оформлении

                async def _fill_oi():
                    nonlocal addr_msg, addr_oi
                    addr_oi = _gen_indian_address()
                    lat, lon = _CITY_COORDS.get(addr_oi["city"], (20.5937, 78.9629))
                    await ctx.set_geolocation({"latitude": lat, "longitude": lon})
                    await _maximize_window(ctx, page)
                    if not await _fill_address_form(page, addr_oi):
                        return False
                    addr_msg = f"{addr_oi['name']} | {addr_oi['pincode']} {addr_oi['city']}"
                    print(f"  {G}✔ Адрес сохранён: {addr_msg}{RST}")
                    with contextlib.suppress(Exception):
                        _save_meta_field(profile_path, **_profile_addr_meta(addr_oi))
                    try:
                        await page.wait_for_url("**/viewcheckout**", timeout=10_000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1_500)
                    return True

                async def _cont_oi():
                    import random as _r
                    c = page.locator(
                        "button:has-text('Continue'), a:has-text('Continue'), "
                        "button:has-text('Place order'), button:has-text('Place Order'), "
                        "button:has-text('PLACE ORDER')"
                    ).last
                    if await c.count() > 0:
                        await _human_click(page, c, before=_r.uniform(0.1, 0.25))
                        await page.wait_for_timeout(900)

                # ── 5. Форма адреса (если сразу попали) ──────────────────────
                if "changeShippingAddress" in page.url or "add/form" in page.url:
                    print(f"  {DIM}Чекаут открыл форму адреса — заполняю...{RST}")
                    if not await _fill_oi():
                        _try_next = True
                        continue

                if stop_at_email:
                    if "viewcheckout" in page.url:
                        await _handle_set_location_on_viewcheckout(page)
                        _email_needed = await page.evaluate("""() => {
                            const body = (document.body && document.body.innerText || '').toLowerCase();
                            return body.includes('add email') || body.includes('email id required');
                        }""")
                        if _email_needed:
                            print(f"  {DIM}Заполняю email...{RST}")
                            await _handle_email_on_page(page)
                            await page.wait_for_timeout(1000)
                            em = _get_filled_email()
                            if em:
                                with contextlib.suppress(Exception):
                                    _save_meta_field(profile_path, buyer_email=em)
                    try:
                        await sms_client.complete(phone_id)
                    except Exception:
                        pass
                    _grizzly_module.mark_completed(phone_id)
                    try:
                        _meta_path = profile_path / ".profile_meta.json"
                        _meta: dict = {}
                        if _meta_path.exists():
                            with contextlib.suppress(Exception):
                                _meta = json.loads(_meta_path.read_text(encoding="utf-8")) or {}
                        if not isinstance(_meta, dict):
                            _meta = {}
                        _meta.update({
                            "username": phone_10,
                            "login_ts": _meta.get("login_ts") or time.time(),
                            "site_url": url.split("?")[0],
                            "status": "email_completed",
                        })
                        em2 = _get_filled_email()
                        if em2:
                            _meta["buyer_email"] = em2
                        if addr_oi:
                            _meta.update(_profile_addr_meta(addr_oi))
                        _atomic_write_text(
                            _meta_path, json.dumps(_meta, ensure_ascii=False, indent=2),
                        )
                        _invalidate_done_profiles_cache()
                        _grizzly_module._STATS["profiles_saved"] += 1
                    except Exception:
                        pass
                    print(f"  {G}✔ Вход выполнен, адрес и почта сохранены: +91 {phone_10}{RST}")
                    try:
                        await _send_tg_login_ok(phone_10)
                    except Exception:
                        pass
                    try:
                        await _send_cookies_to_tg(ctx, phone_10)
                    except Exception:
                        pass
                    _keep_open = True
                    return True, f"Вход выполнен, адрес и почта сохранены: +91 {phone_10}"

                # ── 6. Viewcheckout → email → Continue → payments ─────────────
                if "viewcheckout" in page.url:
                    body = (await page.evaluate(
                        "() => (document.body && document.body.textContent) || ''")).lower()
                    if any(p in body for p in _OOS_PHRASES):
                        print(f"  {Y}OOS для этого пинкода — пробую новый номер...{RST}")
                        _try_next = True
                        continue
                    print(f"  {DIM}Нажимаю Continue на чекауте...{RST}")

                    reached = await _viewcheckout_to_payments(page, profile_path)

                    if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url):
                        print(f"  {DIM}Flipkart запросил адрес — заполняю...{RST}")
                        if not await _fill_oi():
                            _try_next = True
                            continue
                        reached = await _viewcheckout_to_payments(page, profile_path)

                # ── 7. Проверяем payments ──────────────────────────────────────
                if "payments" not in page.url:
                    _try_next = True
                    print(f"  {R}Чекаут не открылся ({page.url.split('?')[0].split('/')[-1]}) — пробую заново{RST}")
                    continue

                # ── 8. Payments: Continue → жёлтая кнопка → email → Continue ─
                print(f"  {DIM}Страница оплаты — заполняю...{RST}")

                # Оплата ПОДАРОЧНЫМИ картами (если выбран этот способ) — минуя банковские
                if _pay_method[0] == "gift":
                    _g_res = await _do_payments_page(page, gift=True, profile_path=profile_path)
                    if _g_res is not True:
                        print(f"  {R}❌ Оплата гифт-картами не удалась ({_g_res}){RST}")
                        _tg_send_direct(f"❌ *Оплата гифт-картами не прошла* (+91 {phone_10})")
                        _try_next = True
                        continue
                    try:
                        await _handle_post_payment(page, ctx, profile_path, phone_number=phone_10)
                    except Exception as _pp_e:
                        print(f"  Post-payment: {_pp_e}")
                    try:
                        await _send_cookies_to_tg(ctx, phone_10)
                    except Exception:
                        pass
                    _keep_open = True
                    print(f"  {DIM}Закрываю браузер профиля +91 {phone_10}...{RST}")
                    try: await ctx.close()
                    except Exception: pass
                    try: await pw.stop()
                    except Exception: pass
                    suffix = f" | {addr_msg}" if addr_msg else ""
                    return True, f"✅ +91 {phone_10}{suffix} → ✅ Готово (гифт-карты)"

                # Строим полный упорядоченный список карт
                _cards_av = _load_cards()
                _ord = _load_card_order()
                _ordered_pay: list = []  # [(raw_idx, card_dict), ...]
                if _cards_av and _ord and isinstance(_ord, list):
                    for _oi in _ord:
                        if isinstance(_oi, int) and 0 <= _oi < len(_cards_av):
                            _ordered_pay.append((_oi, _cards_av[_oi]))
                if not _ordered_pay and _cards_av:
                    _ordered_pay = list(enumerate(_cards_av))

                _cur_pay_pos = 0
                if card is None and _ordered_pay:
                    card = _ordered_pay[0][1]
                if card:
                    print(f"  {G}💳 Карта по порядку: {card.get('nickname') or card.get('name') or _mask_card(card.get('number',''))}{RST}")

                # Передаём остальные карты для TG-кнопок во время ожидания 3DS OTP
                try:
                    _cur_card_num = (card or {}).get("number", "")
                    _3ds_card_options[:] = [
                        {"pos": _p, "card": _c}
                        for _p, (_, _c) in enumerate(_ordered_pay)
                        if _p != _cur_pay_pos
                    ][:4]
                    # Если в порядке карт только одна — показываем все карты из cards.json
                    if not _3ds_card_options:
                        _3ds_card_options[:] = [
                            {"pos": _pi, "card": _pc}
                            for _pi, _pc in enumerate(_cards_av)
                            if _pc.get("number", "") != _cur_card_num
                        ][:4]
                    _switch_card_ev.clear()
                    _switch_card_choice[0] = -1
                except Exception:
                    pass

                _pay_res = await _do_payments_page(page, card=card)

                # Цикл смены карты при недостатке средств или выборе из TG
                _all_cards_exhausted = False
                for _sw_attempt in range(len(_ordered_pay)):
                    if _pay_res not in ("insufficient_funds", "switch_card"):
                        break
                    if _pay_res == "switch_card" and 0 <= _switch_card_choice[0] < len(_ordered_pay):
                        _cur_pay_pos = _switch_card_choice[0]
                    else:
                        _cur_pay_pos += 1
                    if _cur_pay_pos >= len(_ordered_pay):
                        _all_cards_exhausted = True
                        break
                    card = _ordered_pay[_cur_pay_pos][1]
                    _nm = card.get("nickname") or card.get("name") or _mask_card(card.get("number", ""))
                    print(f"  {G}💳 Смена карты → {_nm}{RST}")
                    _tg_send_direct(f"🔄 *Смена карты:* {_nm}")
                    try:
                        _cur_card_num2 = card.get("number", "")
                        _3ds_card_options[:] = [
                            {"pos": _p, "card": _c}
                            for _p, (_, _c) in enumerate(_ordered_pay)
                            if _p != _cur_pay_pos
                        ][:4]
                        if not _3ds_card_options:
                            _3ds_card_options[:] = [
                                {"pos": _pi, "card": _pc}
                                for _pi, _pc in enumerate(_cards_av)
                                if _pc.get("number", "") != _cur_card_num2
                            ][:4]
                        _switch_card_ev.clear()
                        _switch_card_choice[0] = -1
                    except Exception:
                        pass
                    if "payments" not in page.url:
                        try:
                            await _viewcheckout_to_payments(page, profile_path)
                        except Exception:
                            pass
                    _pay_res = await _do_payments_page(page, card=card)

                if _all_cards_exhausted:
                    print(f"  {R}❌ Все карты исчерпаны — прекращаю оплату{RST}")
                    _tg_send_direct("❌ *Все карты исчерпаны* — оплата не прошла")
                    _try_next = True
                    continue
                try:
                    await _handle_post_payment(page, ctx, profile_path, phone_number=phone_10)
                except Exception as _pp_e:
                    print(f"  Post-payment: {_pp_e}")

                try:
                    await _send_cookies_to_tg(ctx, phone_10)
                except Exception:
                    pass

                # Ссылка отправлена — закрываем браузер
                _keep_open = True  # чтобы finally не дублировал закрытие
                print(f"  {DIM}Закрываю браузер профиля +91 {phone_10}...{RST}")
                try:
                    await ctx.close()
                except Exception:
                    pass
                try:
                    await pw.stop()
                except Exception:
                    pass

                suffix = f" | {addr_msg}" if addr_msg else ""
                return True, f"✅ +91 {phone_10}{suffix} → ✅ Готово"

            except Exception as exc:
                print(f"  {R}Ошибка: {exc} — пробую следующий номер...{RST}")
                _try_next = True
                if phone_id:
                    _grizzly_module.mark_failed(phone_id)

            finally:
                if not _keep_open:
                    await _close_browser_session(
                        ctx, pw, profile_path, disconnect_vpn=True,
                    )
                    ctx = None
                else:
                    with contextlib.suppress(Exception):
                        await _vpn_disconnect(ctx)
                    _unregister_purchase_profile(profile_path)
                _no_meta = profile_path is not None and not (profile_path / ".profile_meta.json").exists()
                if profile_path and profile_path.exists() and (_del_profile or _no_meta):
                    import shutil as _sh
                    try:
                        _sh.rmtree(profile_path, ignore_errors=True)
                        print(f"  {DIM}Профиль +91 {phone_10} удалён (вход не выполнен).{RST}")
                    except Exception:
                        pass

    finally:
        if _pay_lock_held:
            try:
                _pay_lock.release()
            except Exception:
                pass
        await sms_client.close()

    return False, "Прервано"


async def _do_purchases_parallel(
    target: int,
    months: int,
    headless: bool,
    card,
    max_concurrent: int,
) -> tuple[int, int]:
    """Запускает до max_concurrent покупок параллельно. Возвращает (успехов, всего).
    pay_lock сериализует фазу покупки/оплаты: поиск номеров/вход/OTP идут
    параллельно, а покупка — по одной (общие синглтоны 3DS/смены карты)."""
    sem      = asyncio.Semaphore(max_concurrent)
    lock     = asyncio.Lock()
    pay_lock = asyncio.Lock()
    success  = 0
    done     = 0

    async def one(idx: int) -> None:
        nonlocal success, done
        async with sem:
            ok, msg = await _do_all_in_one(months, headless=headless, card=card,
                                           _pay_lock=pay_lock)
            async with lock:
                done += 1
                if ok:
                    success += 1
                    print(f"\n  {G}{BLD}✔ [{idx}/{target}] {msg}{RST}")
                else:
                    print(f"\n  {R}❌ [{idx}/{target}] {msg}{RST}")

    results = await asyncio.gather(*[one(i + 1) for i in range(target)], return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            print(f"  {R}[!] Ошибка в параллельной задаче: {res}{RST}")
    return success, done


def screen_all_in_one():
    """Всё в одном: GrizzlySMS номер → вход → адрес → Buy Now → оплата."""
    cls()
    header("ВСЁ В ОДНОМ: ВХОД + АДРЕС + ПОКУПКА", G)
    print()
    print(f"  {DIM}1 аккаунт = номер +91 → OTP → вход → адрес → покупка Membership{RST}")
    print(f"  {Y}Параллельный поиск номеров + автозамена при блокировке.{RST}")
    print()

    # ── Сколько аккаунтов/покупок ─────────────────────────────────────────────
    section("Сколько аккаунтов создать?")
    print()
    target_count: int | None = None
    while True:
        raw = input(
            f"  {BLD}Количество?{RST} {DIM}(Enter = из config.yaml){RST}: "
        ).strip()
        if raw == "":
            break
        if raw.isdigit() and int(raw) > 0:
            target_count = int(raw)
            break
        print(f"  {R}  Введите целое число больше 0{RST}")
    if target_count:
        print(f"\n  {G}Цель: {BLD}{target_count}{RST}{G} аккаунт(ов){RST}")
    else:
        print(f"\n  {DIM}Количество из config.yaml (auto_accounts){RST}")

    # ── Тариф ─────────────────────────────────────────────────────────────────
    print()
    section("Тариф Black Membership")
    print()
    opt("1", "3 месяца  — ₹343", G)
    opt("2", "12 месяцев — ₹1,499", C)
    print()
    while True:
        tariff = input(f"  {BLD}Тариф [1/2, Enter = 3 мес.]: {RST}").strip()
        if tariff in ("", "1"):
            months, label = 3,  "3 месяца / ₹343"
            break
        if tariff == "2":
            months, label = 12, "12 месяцев / ₹1,499"
            break
        print(f"  {R}Введите 1 или 2{RST}")

    # ── Режим браузера ────────────────────────────────────────────────────────
    print()
    section("Режим браузера")
    print()
    opt("1", "🌑 Фоновый (без окна)  — быстрее, меньше ресурсов", G)
    opt("2", "🖥  Обычный  (с окном)  — видно процесс в реальном времени", C)
    print()
    while True:
        mode_ch = input(f"  {BLD}Режим [1/2, Enter = фоновый]: {RST}").strip()
        if mode_ch in ("", "1", "2"):
            break
        print(f"  {R}Введите 1 или 2{RST}")
    headless = (mode_ch != "2")
    mode_lbl = "фоновый (без окна)" if headless else "обычный (с окном)"
    print(f"\n  {DIM}Режим: {mode_lbl}{RST}")

    # ── Карта ─────────────────────────────────────────────────────────────────
    # Карта не запрашивается — оплата идёт по единому порядку (data/card_order.json)
    selected_card = None
    print()
    print(f"  {DIM}Карты берутся по установленному порядку (data/card_order.json){RST}")
    print(f"  {DIM}Для остановки нажмите  {RST}{BLD}{Y}Ctrl+C{RST}")
    print()

    # Определяем количество аккаунтов
    if target_count:
        _n = target_count
    else:
        try:
            import yaml as _yaml
            with open("config.yaml", encoding="utf-8") as _fh:
                _n = int((_yaml.safe_load(_fh) or {}).get("auto_accounts", 1))
        except Exception:
            _n = 1

    # ── Проверка доступности Flipkart перед запуском ──────────────────────────
    print(f"  {DIM}Проверка доступности Flipkart...{RST}")
    if not _is_flipkart_accessible_sync():
        print(f"\n  {R}⚠ Flipkart недоступен{RST}")
        print(f"  {Y}Повторите попытку позже.{RST}")
        pause()
        return
    print(f"  {G}Flipkart доступен.{RST}")

    section(f"Запуск: {_n} аккаунт(ов) | {label} | {mode_lbl}")
    print()

    ok_count = 0
    for i in range(_n):
        print(f"\n  [{i+1}/{_n}] Полный цикл — {label}...")
        try:
            ok, msg = asyncio.run(_do_all_in_one(months, headless=headless,
                                                  card=selected_card))
            print(f"  [{i+1}/{_n}] {'✅' if ok else '❌'} {msg}")
            if ok:
                ok_count += 1
            else:
                # Новый аккаунт не вышел — пробуем оплату на всех существующих профилях
                _done_pfs = (sorted(DONE_PROFILES_DIR.glob("profile_*"))
                             if DONE_PROFILES_DIR.exists() else [])
                if _done_pfs:
                    print(f"\n  {Y}⟳ Пробую оплату на {len(_done_pfs)} существующих профилях...{RST}")
                    _fallback_ok = False
                    for _pp in _done_pfs:
                        try:
                            print(f"  {DIM}  → {_pp.name}{RST}")
                            _pok, _pmsg = asyncio.run(
                                _do_buy_membership(_pp, months, card=selected_card, _skip_ping=True))
                            print(f"  {'✅' if _pok else '❌'} {_pp.name}: {_pmsg}")
                            if _pok:
                                ok_count += 1
                                _fallback_ok = True
                                break
                        except KeyboardInterrupt:
                            raise
                        except Exception as _pe:
                            print(f"  ❌ {_pp.name}: {_pe}")
                    if not _fallback_ok:
                        print(f"  {R}  Ни один из существующих профилей не смог оплатить.{RST}")
        except KeyboardInterrupt:
            print(f"\n\n  {Y}[!] Остановка по Ctrl+C...{RST}")
            break
        except BaseException as exc:
            print(f"  [{i+1}/{_n}] ❌ Ошибка: {type(exc).__name__}: {exc}")

    print()
    section(f"Итого: {ok_count}/{_n} аккаунтов обработано")
    pause()


def screen_purge():
    import shutil as _sh
    cls()
    header("УДАЛЕНИЕ УСТАРЕВШИХ ПРОФИЛЕЙ", Y)
    section("Поиск и удаление...")
    print()

    removed = 0
    for _purge_dir in [USED_PROFILES_DIR, BACKUP_PROFILES_DIR]:
        if _purge_dir.exists():
            for _p in list(_purge_dir.iterdir()):
                if _p.is_dir():
                    try:
                        _sh.rmtree(_p)
                        removed += 1
                    except Exception:
                        pass

    if removed > 0:
        print(f"  {G}Удалено профилей: {BLD}{removed}{RST}")
    else:
        print(f"  {DIM}Устаревших профилей не найдено.{RST}")

    pause()


def screen_logs():
    log_path = _AUTOMATION_LOG
    if not log_path.exists():
        cls()
        header("ЛОГИ", B)
        print(f"  {R}Файл automation.log не найден.{RST}")
        print(f"  {DIM}Запустите автоматизацию — лог появится автоматически.{RST}")
        pause()
        return

    # Показываем последние 250 строк
    cls()
    header("ЛОГИ  (последние 250 строк)", B)
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    recent = lines[-250:]

    for line in recent:
        # Окрашиваем по уровню
        if "SUCCESS" in line or "успешно" in line.lower():
            print(f"  {G}{line}{RST}")
        elif "ERROR" in line or "ошибка" in line.lower() or "провал" in line.lower():
            print(f"  {R}{line}{RST}")
        elif "WARNING" in line or "предупреждение" in line.lower():
            print(f"  {Y}{line}{RST}")
        elif "КОД" in line or "SMS" in line or "НОМЕР" in line:
            print(f"  {G}{BLD}{line}{RST}")
        else:
            print(f"  {DIM}{line}{RST}")

    print()
    print(f"  {DIM}Файл: {log_path.resolve()}{RST}")
    print()

    choice = input(f"  {BLD}[O]{RST} Открыть в Notepad  {BLD}[Enter]{RST} Назад: ").strip().lower()
    if choice == "o":
        subprocess.Popen(["notepad.exe", str(log_path)])


def _deps_ok() -> bool:
    """Быстрая проверка без сети: все пакеты импортируются и Chromium установлен."""
    return _deps_ok_full()


_DEPS_OK_MARKER = _HERE / "data" / "deps_ok.json"


def _chromium_ok(fast: bool = True) -> bool:
    """Проверка браузера для автоматизации.

    Приложение запускает Playwright с channel="chrome" — то есть на обычном
    Google Chrome. Если Chrome установлен, бандл-Chromium от Playwright скачивать
    не нужно (иначе стартовая проверка каждый раз тянула бы ~150 МБ и вешала старт).
    fast=True — быстрый путь по кэшу пути exe, без запуска node-драйвера Playwright.
    """
    # Google Chrome достаточно — этого хватает для channel="chrome"
    with contextlib.suppress(Exception):
        if _find_chrome():
            return True
    if fast:
        with contextlib.suppress(Exception):
            import playwright as _pwpkg
            raw = json.loads(_DEPS_OK_MARKER.read_text(encoding="utf-8"))
            exe = raw.get("chromium_exe") or ""
            same_ver = raw.get("playwright") == getattr(_pwpkg, "__version__", "")
            if exe and same_ver and os.path.exists(exe):
                return True
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as _pw:
            _exe = _pw.chromium.executable_path
        ok = bool(_exe and os.path.exists(_exe))
        if ok:
            with contextlib.suppress(Exception):
                import playwright as _pwpkg
                _DEPS_OK_MARKER.parent.mkdir(parents=True, exist_ok=True)
                _DEPS_OK_MARKER.write_text(
                    json.dumps({
                        "chromium_exe": str(_exe),
                        "playwright": getattr(_pwpkg, "__version__", ""),
                    }),
                    encoding="utf-8")
        return ok
    except Exception:
        return False


def _deps_ok_full() -> bool:
    """Все пакеты из requirements.txt + Chromium."""
    try:
        import httpx, loguru, yaml  # noqa: F401
        from PIL import Image  # noqa: F401
        import openpyxl  # noqa: F401
        import playwright  # noqa: F401
    except ImportError:
        return False
    return _chromium_ok()


def ensure_dependencies(log_fn=None) -> tuple[bool, str]:
    """Проверить и при необходимости установить зависимости (тихо, без UI)."""
    if _deps_ok_full():
        return True, "Зависимости в порядке"

    req = _HERE / "requirements.txt"
    if log_fn:
        log_fn("Установка пакетов из requirements.txt…")
    pip_ok = run([sys.executable, "-m", "pip", "install", "-r", str(req)]) == 0

    if not _chromium_ok(fast=False):
        if log_fn:
            log_fn("Установка Chromium (Playwright)…")
        pw_ok = run([sys.executable, "-m", "playwright", "install", "chromium"]) == 0
    else:
        pw_ok = True

    if _deps_ok_full():
        return True, "Зависимости установлены"
    if not pip_ok:
        return False, "pip install не удался — проверьте интернет"
    if not pw_ok:
        return False, "Chromium не установился"
    return False, "Не все зависимости установлены"


def screen_install(auto: bool = False):
    if auto:
        ensure_dependencies()
        return  # тихая установка при старте

    if _deps_ok_full():
        return  # всё уже установлено — пропускаем без очистки экрана

    cls()
    header("УСТАНОВКА ЗАВИСИМОСТЕЙ", M)

    section("1/2 — Python пакеты")
    print()
    print(f"  {DIM}Устанавливаем отсутствующие пакеты (без принудительного обновления)...{RST}")
    print()

    pip_ok = run([
        sys.executable, "-m", "pip", "install",
        "playwright", "loguru", "pyyaml", "httpx",
    ]) == 0

    if not pip_ok:
        print()
        print(f"  {R}[!] Часть пакетов не установилась.{RST}")
        print(f"  {Y}    Возможные причины:{RST}")
        print(f"  {DIM}    • Нет подключения к интернету / DNS не работает{RST}")
        print(f"  {DIM}    • Блокировка VPN или корпоративным прокси{RST}")
        print()
        print(f"  {Y}    Попробуйте вручную в терминале:{RST}")
        print(f"  {W}    pip install playwright loguru pyyaml httpx{RST}")
        print(f"  {DIM}    или с альтернативным зеркалом:{RST}")
        print(f"  {W}    pip install playwright loguru pyyaml httpx -i https://pypi.tuna.tsinghua.edu.cn/simple/{RST}")
        if not auto:
            pause()
        return

    print()
    section("2/2 — Chromium браузер")
    print()
    pw_ok = run([sys.executable, "-m", "playwright", "install", "chromium"]) == 0

    print()
    if pip_ok and pw_ok:
        print(f"\n{G}  ✅ Установка завершена!{RST}")
    else:
        print(f"\n{Y}  [!] Chromium не установился. Запустите вручную:{RST}")
        print(f"  {W}    python -m playwright install chromium{RST}")
    if not auto:
        pause()


# ── Стартовая очистка ─────────────────────────────────────────────────────────

def _notify_tg_update(commits: list[str]) -> None:
    """Отправляет уведомление об обновлении всем TG-подписчикам (синхронно)."""
    if not _tg_notify_enabled():
        return
    try:
        import urllib.request as _ur, urllib.parse as _up
        token = _get_telegram_token()
        if not token or not TG_SUBSCRIBERS_FILE.exists():
            return
        subs = json.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8")) or {}
        chat_ids = subs.get("chats", [])
        if not chat_ids:
            return
        lines_txt = "\n".join(f"▸ `{c}`" for c in commits[:10])
        extra = f"\n_...и ещё {len(commits) - 10}_" if len(commits) > 10 else ""
        text = (
            f"⬆️ *Новое обновление!*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"Новых коммитов: *{len(commits)}*\n\n"
            f"{lines_txt}{extra}\n\n"
            "_Откройте бот и нажмите «Обновить»_"
        )
        _api = f"https://api.telegram.org/bot{token}"
        opener = _ur.build_opener(_ur.ProxyHandler({}))
        for cid in chat_ids:
            try:
                opener.open(
                    _ur.Request(
                        f"{_api}/sendMessage",
                        data=_up.urlencode({
                            "chat_id": cid, "text": text, "parse_mode": "Markdown",
                        }).encode(),
                    ), timeout=8)
            except Exception:
                pass
    except Exception:
        pass


def _tg_send_direct(text: str) -> None:
    """Шлёт сообщение всем подписчикам напрямую через urllib (без бота)."""
    if not _tg_notify_enabled():
        return
    try:
        import urllib.request as _ur2, urllib.parse as _up2
        if not TG_SUBSCRIBERS_FILE.exists():
            return
        _tok = _get_telegram_token()
        _subs = (json.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8")) or {}).get("chats", [])
        if not _tok or not _subs:
            return
        _api = f"https://api.telegram.org/bot{_tok}"
        _op  = _ur2.build_opener(_ur2.ProxyHandler({}))
        for _cid in _subs:
            try:
                _op.open(_ur2.Request(f"{_api}/sendMessage",
                    data=_up2.urlencode({"chat_id": _cid, "text": text,
                                         "parse_mode": "Markdown"}).encode()), timeout=8)
            except Exception:
                pass
    except Exception:
        pass


def _tg_send_direct_kb(text: str, keyboard: dict) -> None:
    """Шлёт сообщение с inline keyboard всем подписчикам напрямую через urllib."""
    if not _tg_notify_enabled():
        return
    try:
        import urllib.request as _ur3, urllib.parse as _up3
        if not TG_SUBSCRIBERS_FILE.exists():
            return
        _tok = _get_telegram_token()
        _subs = (json.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8")) or {}).get("chats", [])
        if not _tok or not _subs:
            return
        _api = f"https://api.telegram.org/bot{_tok}"
        _op3 = _ur3.build_opener(_ur3.ProxyHandler({}))
        for _cid in _subs:
            try:
                _payload = _up3.urlencode({
                    "chat_id": _cid,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": json.dumps(keyboard),
                }).encode()
                _op3.open(_ur3.Request(f"{_api}/sendMessage", data=_payload), timeout=8)
            except Exception:
                pass
    except Exception:
        pass


def _update_notify_loop() -> None:
    """Простой поток: каждые 5 минут проверяет GitHub и шлёт TG-уведомление."""
    _notified: set = set()
    time.sleep(20)
    _tg_send_direct("🟢 *Мониторинг обновлений запущен*\n_Уведомления о пушах активны._")
    while True:
        try:
            commits = _http_check_updates()
            if commits:
                new = [c for c in commits if c.split()[0] not in _notified]
                if new:
                    _notify_tg_update(new)
                    _notified.update(c.split()[0] for c in new)
        except Exception:
            pass
        time.sleep(300)


def _check_updates_bg() -> None:
    """Фоновая проверка. В GUI — только HTTP (без вспышек git/cmd)."""
    global _update_available, _update_commits, _update_checked, _update_checked_at
    _cwd = _HERE
    lines: list[str] = []
    _git_ok = False
    use_git = (_cwd / ".git").exists()
    try:
        import winproc as _wp
        if _wp.is_gui_host():
            use_git = False
    except Exception:
        pass
    if use_git:
        try:
            import winproc
            _fr = winproc.run([_GIT, "fetch", "--quiet", "origin"],
                                 capture_output=True, timeout=20, cwd=_cwd)
            if _fr.returncode == 0:
                r = winproc.run([_GIT, "log", "HEAD..FETCH_HEAD", "--oneline", "--no-color"],
                                   capture_output=True, text=True, timeout=10, cwd=_cwd)
                lines   = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
                _git_ok = True
        except Exception:
            pass
    if not _git_ok:
        lines = _http_check_updates()
    _update_available = bool(lines)
    _update_commits   = lines
    _update_checked   = True
    _update_checked_at = time.time()
    # Синхронизируем с bot-модулем (он хранит канонические значения для UI)
    try:
        _bot_module._update_available = _update_available
        _bot_module._update_commits   = list(lines)
        _bot_module._update_checked   = True
        _bot_module._update_checked_at = _update_checked_at
    except Exception:
        pass


def _do_git_update() -> tuple[bool, str]:
    """Применяет обновление: git pull если есть .git, иначе HTTP-скачивание."""
    global _update_available, _update_commits
    _cwd = _HERE
    # Пробуем git только если есть .git папка
    if (_cwd / ".git").exists():
        try:
            import winproc
            _branch = "master"
            try:
                _rb = winproc.run([_GIT, "rev-parse", "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True, timeout=5, cwd=_cwd)
                if _rb.returncode == 0:
                    _branch = _rb.stdout.strip() or "master"
            except Exception:
                pass
            r_fetch = winproc.run([_GIT, "fetch", "origin", _branch],
                                     capture_output=True, text=True, timeout=60, cwd=_cwd,
                                     encoding="utf-8", errors="replace")
            if r_fetch.returncode != 0:
                err = r_fetch.stderr.strip() or r_fetch.stdout.strip() or "git fetch не удался"
                return False, err
            r_merge = winproc.run([_GIT, "merge", "--ff-only", f"origin/{_branch}"],
                                     capture_output=True, text=True, timeout=60, cwd=_cwd,
                                     encoding="utf-8", errors="replace")
            if r_merge.returncode == 0:
                _update_available = False
                _update_commits   = []
                try:
                    _bot_module._update_available = False
                    _bot_module._update_commits   = []
                except Exception:
                    pass
                _init_secrets()
                _migrate_config()
                return True, r_merge.stdout.strip() or "Версия уже актуальна"
            err = r_merge.stderr.strip() or r_merge.stdout.strip() or "git merge не удался"
            return False, err
        except OSError:
            pass  # git не установлен
        except Exception as e:
            return False, str(e)
    # HTTP fallback (ZIP-установка или git недоступен)
    return _http_do_update()


def screen_update() -> None:
    """Экран обновления из GitHub."""
    cls()
    header("ОБНОВЛЕНИЕ", G)
    try:
        commits = _http_check_updates()
    except Exception as _he:
        print(f"  {R}❌ Ошибка: {_he}{RST}")
        pause()
        return

    print()
    if not commits:
        print(f"  {G}✅ У вас уже последняя версия — обновлений нет.{RST}")
        pause()
        return

    print(f"  {Y}Доступно новых коммитов: {BLD}{len(commits)}{RST}")
    print()
    for c in commits[:15]:
        print(f"  {DIM}  • {c}{RST}")
    if len(commits) > 15:
        print(f"  {DIM}  ...и ещё {len(commits) - 15} коммитов{RST}")
    print()

    try:
        ans = input(f"  {BLD}Обновить сейчас? [Д / Н]: {RST}").strip().upper()
    except KeyboardInterrupt:
        return
    if ans not in ("Д", "ДА"):
        print(f"  {DIM}Отменено.{RST}")
        pause()
        return

    print(f"  {DIM}Применяю обновление...{RST}")
    ok, msg = _do_git_update()
    if ok:
        print(f"  {G}✅ Обновление применено! Перезапускаю...{RST}")
        if msg:
            for _line in msg.splitlines():
                print(f"  {DIM}  {_line}{RST}")
        time.sleep(2)
        _exit_code[0] = 42  # чтобы finally в __main__ вышел с кодом 42 → menu.bat перезапустит
        sys.exit(42)
    else:
        print(f"  {R}❌ Ошибка: {msg}{RST}")
    pause()


def _prompt_update_if_available() -> None:
    """При старте: если есть обновления — показывает список и предлагает Д/Н."""
    # Ждём завершения фоновой проверки (до 5 сек после TG-ожидания)
    for _ in range(10):
        if _update_checked:
            break
        time.sleep(0.5)
    if not _update_available:
        return
    commits = _update_commits or []
    cls()
    header("ДОСТУПНО ОБНОВЛЕНИЕ", Y)
    print()
    print(f"  {Y}Новых коммитов: {BLD}{len(commits)}{RST}")
    print()
    for c in commits[:15]:
        print(f"  {DIM}  • {c}{RST}")
    if len(commits) > 15:
        print(f"  {DIM}  ...и ещё {len(commits) - 15} коммитов{RST}")
    print()
    try:
        ans = input(f"  {BLD}Скачать обновление? [Д / Н]: {RST}").strip().upper()
    except KeyboardInterrupt:
        return
    if ans not in ("Д", "ДА"):
        return
    print(f"\n  {DIM}Применяю обновление...{RST}")
    ok, msg = _do_git_update()
    if ok:
        print(f"  {G}✅ Обновление применено! Перезапускаю...{RST}")
        if msg:
            for _line in msg.splitlines():
                print(f"  {DIM}  {_line}{RST}")
        time.sleep(2)
        _exit_code[0] = 42
        sys.exit(42)
    else:
        print(f"  {R}❌ Ошибка: {msg}{RST}")
        time.sleep(3)


# ── Карты ─────────────────────────────────────────────────────────────────────

def _load_cards() -> list:
    if CARDS_FILE.exists():
        try:
            return json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_cards(cards: list) -> None:
    _atomic_write_text(CARDS_FILE, json.dumps(cards, ensure_ascii=False, indent=2))
    runtime_touch("cards")


# ── Подарочные карты (Flipkart Gift Card) ────────────────────────────────────
def _load_gift_cards() -> list:
    """Список гифт-карт: [{denom:int, number:str, pin:str, used:bool, used_ts}]."""
    if GIFT_CARDS_FILE.exists():
        try:
            v = json.loads(GIFT_CARDS_FILE.read_text(encoding="utf-8"))
            return v if isinstance(v, list) else []
        except Exception:
            pass
    return []


def _save_gift_cards(cards: list) -> None:
    _atomic_write_text(GIFT_CARDS_FILE, json.dumps(cards, ensure_ascii=False, indent=2))
    runtime_touch("gift_cards")


def _parse_gift_cards(text: str, default_denom: int | None = None) -> tuple[list, list]:
    """Парсит гифт-карты из текста/CSV. Возвращает (список_карт, ошибки)."""
    import re as _re
    denoms = set(GIFT_DENOMS)
    out, errs = [], []
    for _ln in text.splitlines():
        s = _ln.strip()
        if not s:
            continue
        low = s.lower()
        if (("серия" in low or "series" in low)
                or ("pin" in low and ("дата" in low or "expir" in low or "истеч" in low))
                or ("flipkart" in low and "inr" in low)):
            continue
        s2 = _re.sub(r"\d{4}[-/.]\d{2}[-/.]\d{2}", " ", s)
        s2 = _re.sub(r"\d{2}[-/.]\d{2}[-/.]\d{2,4}", " ", s2)
        m = _re.search(r"\b(\d{14,19})\b", s2)
        number = m.group(1) if m else ""
        rest = s2.replace(number, " ", 1) if number else s2
        denom = default_denom
        for t in _re.findall(r"\b(\d{2,4})\b", rest):
            if int(t) in denoms:
                denom = int(t)
                rest = rest.replace(t, " ", 1)
                break
        pin = ""
        for t in _re.findall(r"\b(\d{4,8})\b", rest):
            pin = t
            break
        if not number:
            errs.append(f"«{s[:40]}» — не найден номер (14–19 цифр)")
            continue
        if not pin:
            errs.append(f"«{s[:40]}» — не найден PIN (4–8 цифр)")
            continue
        if not denom:
            errs.append(f"«{s[:40]}» — не указан номинал")
            continue
        out.append({"denom": int(denom), "number": number, "pin": pin, "used": False})
    return out, errs


def _gift_bytes_to_text(fname: str, raw: bytes) -> tuple[str, str]:
    """Извлекает текст из файла гифт-карт (HTML/Excel/CSV/TXT)."""
    import re as _re2
    _low = (fname or "").lower()
    _sniff = raw[:400].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if (_sniff.startswith(b"<html") or _sniff.startswith(b"<table")
            or b"excel.sheet" in _sniff or b"<table" in raw[:2000].lower()):
        try:
            html = raw.decode("utf-8", "replace")
            _lines = []
            for _tr in _re2.findall(r"<tr[^>]*>(.*?)</tr>", html, _re2.I | _re2.S):
                _cells = _re2.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", _tr, _re2.I | _re2.S)
                _vals = [_re2.sub(r"<[^>]+>", "", c).strip() for c in _cells]
                _vals = [v for v in _vals if v]
                if _vals:
                    _lines.append(" ".join(_vals))
            if _lines:
                return "\n".join(_lines), ""
        except Exception as _he:
            return "", f"Не удалось прочитать HTML-таблицу: {_he}"
    if _low.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            import io as _io2
            import openpyxl as _oxl
            wb = _oxl.load_workbook(_io2.BytesIO(raw), read_only=True, data_only=True)
            _lines = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        _lines.append(" ".join(cells))
            return "\n".join(_lines), ""
        except ImportError:
            return "", "Excel требует openpyxl (pip install openpyxl)"
        except Exception as _xe:
            return "", f"Не удалось прочитать Excel: {_xe}"
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc), ""
        except Exception:
            continue
    return raw.decode("utf-8", "replace"), ""


def _add_gift_cards_from_text(text: str, default_denom: int | None = None) -> dict:
    """Добавляет гифт-карты из текста. Возвращает {added, dup, errs, balance}."""
    parsed, errs = _parse_gift_cards(text, default_denom)
    existing = _load_gift_cards()
    _have = {str(c.get("number")) for c in existing}
    _now = time.time()
    added = dup = 0
    for c in parsed:
        if str(c["number"]) in _have:
            dup += 1
            continue
        c["added_ts"] = _now
        existing.append(c)
        _have.add(str(c["number"]))
        added += 1
    if added:
        _save_gift_cards(existing)
    return {
        "added": added, "dup": dup, "errs": errs,
        "balance": _gift_balance(existing), "total": len(existing),
    }


def _mask_gift(number: str) -> str:
    n = "".join(ch for ch in str(number) if ch.isalnum())
    return f"…{n[-4:]}" if len(n) >= 4 else (n or "?")


def _gift_balance(cards: list | None = None) -> int:
    """Сумма номиналов неиспользованных гифт-карт."""
    cards = cards if cards is not None else _load_gift_cards()
    return sum(int(c.get("denom") or 0) for c in cards
               if not c.get("used") and c.get("number") and c.get("pin"))


def _select_gift_cards(total: int, cards: list | None = None):
    """Подбирает набор НЕиспользованных гифт-карт с суммой >= total и МИНИМАЛЬНЫМ
    превышением (меньше «сгорает» баланса), при равенстве — меньше карт.
    Возвращает (список_карт, сумма_набора) или (None, доступный_баланс)."""
    cards = cards if cards is not None else _load_gift_cards()
    unused = [c for c in cards
              if not c.get("used")
              and str(c.get("number") or "").strip()
              and str(c.get("pin") or "").strip()
              and int(c.get("denom") or 0) > 0]
    units = [int(c.get("denom")) // 50 for c in unused]  # номиналы кратны 50
    total_u = -(-int(total) // 50)  # ceil(total/50)
    max_s = sum(units)
    if max_s < total_u:
        return None, max_s * 50
    # 0/1-рюкзак: для каждой достижимой суммы — набор индексов с наим. числом карт
    dp: list = [None] * (max_s + 1)
    dp[0] = []
    for i, u in enumerate(units):
        if u <= 0:
            continue
        for s in range(max_s, u - 1, -1):
            if dp[s - u] is not None:
                cand = dp[s - u] + [i]
                if dp[s] is None or len(cand) < len(dp[s]):
                    dp[s] = cand
    best_s = next((s for s in range(total_u, max_s + 1) if dp[s] is not None), None)
    if best_s is None:
        return None, max_s * 50
    picked = [unused[i] for i in dp[best_s]]
    if len(picked) > 15:
        # Flipkart: не более 15 карт за транзакцию — жадно по убыванию (меньше карт)
        unused.sort(key=lambda c: int(c.get("denom") or 0), reverse=True)
        picked, acc = [], 0
        for c in unused:
            if acc >= total:
                break
            picked.append(c)
            acc += int(c.get("denom") or 0)
        if len(picked) > 15 or acc < total:
            return None, _gift_balance(cards)
        return picked, acc
    return picked, best_s * 50


def _gift_shortage_report(need_amount: int):
    """Отчёт по складу vs остатку заказа.
    «Не хватает» = сколько ДОБАВИТЬ в хранилище (need − bal), не «осталось покрыть».
    Возвращает (текст, баланс, округл_нужно, нехватка_на_складе)."""
    cards = [c for c in _load_gift_cards()
             if not c.get("used") and c.get("number") and c.get("pin")
             and int(c.get("denom") or 0) > 0]
    bal = sum(int(c.get("denom")) for c in cards)
    need = -(-int(need_amount) // 50) * 50   # округление вверх до кратного 50
    short = max(0, need - bal)
    by: dict = {}
    for c in cards:
        d = int(c.get("denom"))
        by[d] = by.get(d, 0) + 1
    breakdown = "  ·  ".join(f"₹{d}×{by[d]}" for d in sorted(by, reverse=True)) or "карт нет"
    lines = [
        f"Осталось покрыть: ₹{need}" + (
            f"  (цена ₹{need_amount}, гифт-картами кратно 50)"
            if need != need_amount else ""
        ),
        f"В хранилище: ₹{bal}  →  {breakdown}",
    ]
    if short > 0:
        lines.append(
            f"Не хватает на складе: ₹{short}  "
            f"(добавьте карт на эту сумму, напр. {max(1, short // 50)}×₹50)"
        )
    else:
        # bal >= need: сумма карт ок, но оплата могла встать (крупные без ОК / брак)
        lines.append(
            f"На складе хватает (₹{bal} ≥ ₹{need}) — дело не в сумме карт"
        )
    return "\n".join(lines), bal, need, short


_PAY_METHOD_FILE = _DATA / "pay_method.txt"

def _load_pay_method() -> str:
    """Способ оплаты из файла: "card" | "gift". По умолчанию "card"."""
    try:
        v = _PAY_METHOD_FILE.read_text(encoding="utf-8").strip().lower()
        return "gift" if v == "gift" else "card"
    except Exception:
        return "card"

def _save_pay_method(m: str) -> None:
    _pay_method[0] = "gift" if str(m).lower() == "gift" else "card"
    try:
        _atomic_write_text(_PAY_METHOD_FILE, _pay_method[0])
    except Exception:
        pass
    runtime_touch("pay_method")

# При импорте модуля восстанавливаем сохранённый способ оплаты
try:
    _pay_method[0] = _load_pay_method()
except Exception:
    pass


def _load_gift_used() -> list:
    if GIFT_USED_FILE.exists():
        try:
            v = json.loads(GIFT_USED_FILE.read_text(encoding="utf-8"))
            return v if isinstance(v, list) else []
        except Exception:
            pass
    return []


def _mark_gift_used(card: dict, profile_path=None, status: str = "used") -> None:
    """Помечает гифт-карту использованной: удаляет из хранилища и пишет в аудит-лог.
    status="used" — применена к этому профилю (пишется и в мету профиля).
    status="used_elsewhere" — уже использована/добавлена на ДРУГОМ аккаунте
    (Flipkart отклонил): удаляем и логируем, но в баланс профиля НЕ пишем."""
    import time as _t_gu
    num = str(card.get("number") or "").strip()
    pin = str(card.get("pin") or "").strip()
    denom = int(card.get("denom") or 0)
    ts = _t_gu.time()
    prof_name = ""
    try:
        prof_name = Path(profile_path).name if profile_path else ""
    except Exception:
        prof_name = ""
    # 1. Удаляем из хранилища (по номеру)
    try:
        remaining = [c for c in _load_gift_cards()
                     if str(c.get("number") or "").strip() != num]
        _save_gift_cards(remaining)
    except Exception:
        pass
    # 2. Аудит-лог использованных
    try:
        log = _load_gift_used()
        log.append({"denom": denom, "number": num, "pin": pin, "used_ts": ts,
                    "used_str": _fmt_msk(ts), "profile": prof_name, "status": status})
        _atomic_write_text(GIFT_USED_FILE, json.dumps(log, ensure_ascii=False, indent=2))
    except Exception:
        pass
    # 3. Запись в мету профиля — только если карта реально применена к этому профилю
    if profile_path and status == "used":
        try:
            _mf = Path(profile_path) / ".profile_meta.json"
            _meta = json.loads(_mf.read_text(encoding="utf-8")) if _mf.exists() else {}
            if not isinstance(_meta, dict):
                _meta = {}
            _gcu = _meta.get("gift_cards_used")
            if not isinstance(_gcu, list):
                _gcu = []
            _gcu.append({"denom": denom, "number": num, "pin": pin,
                         "used_ts": ts, "used_str": _fmt_msk(ts)})
            _meta["gift_cards_used"] = _gcu
            _atomic_write_text(_mf, json.dumps(_meta, ensure_ascii=False, indent=2))
        except Exception:
            pass


def _mask_card(number: str) -> str:
    n = number.replace(" ", "").replace("-", "")
    return f"**** **** **** {n[-4:]}" if len(n) >= 4 else number


def _format_card_line(c: dict, idx: int) -> str:
    name = c.get("nickname") or c.get("name", "Карта")
    mask = _mask_card(c.get("number", ""))
    exp  = c.get("expiry", "??/??")
    return f"[{idx}]  {W}{name}{RST}   {DIM}{mask}   exp {exp}{RST}"


def _ask_card_fields() -> dict | None:
    """Запрашивает данные карты + платёжный адрес. Возвращает dict или None при отмене."""
    print(f"\n  {DIM}Введите данные карты (или пустую строку для отмены):{RST}")
    nick = input(f"  Название (напр. Visa Gold): ").strip()
    if not nick:
        return None
    number = input(f"  Номер карты (16 цифр):     ").strip().replace(" ", "").replace("-", "")
    if len(number) < 13:
        print(f"  {R}Неверный номер.{RST}")
        return None
    expiry = input(f"  Срок действия (MM/YY):     ").strip()
    if "/" not in expiry:
        print(f"  {R}Введите в формате MM/YY.{RST}")
        return None
    cvv     = input(f"  CVV/CVC (3-4 цифры):       ").strip()
    name    = input(f"  Имя на карте (лат.):        ").strip().upper()
    print(f"\n  {DIM}Платёжный адрес карты (billing address):{RST}")
    country = input(f"  Страна (напр. USA):         ").strip() or "USA"
    zipcode = input(f"  Zipcode / Индекс:           ").strip()
    state   = input(f"  State / Штат:               ").strip()
    city    = input(f"  City / Город:               ").strip()
    address = input(f"  Billing Address (улица):    ").strip()
    return {
        "nickname": nick, "number": number, "expiry": expiry,
        "cvv": cvv, "name": name, "country": country,
        "zipcode": zipcode, "state": state, "city": city, "address": address,
    }


def _load_card_order() -> list:
    """Единый порядок карт (0-based индексы) — применяется для всех покупок."""
    f = _DATA / "card_order.json"
    try:
        if f.exists():
            v = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(v, list):
                return v
    except Exception:
        pass
    return []


def _save_card_order(order: list) -> None:
    f = _DATA / "card_order.json"
    try:
        _atomic_write_text(f, json.dumps(order, ensure_ascii=False))
    except Exception:
        pass


def screen_gift_cards():
    """Просмотр подарочных карт: остаток, номиналы, что есть, история активаций."""
    while True:
        cls()
        header("ПОДАРОЧНЫЕ КАРТЫ (Flipkart Gift Card)", C)
        cards = _load_gift_cards()
        used  = _load_gift_used()
        bal   = _gift_balance(cards)
        print()

        # Сводка по номиналам
        by_denom: dict = {}
        for c in cards:
            if c.get("number") and c.get("pin"):
                d = int(c.get("denom") or 0)
                by_denom[d] = by_denom.get(d, 0) + 1
        section(f"В хранилище: {W}{BLD}{sum(by_denom.values())} шт.{RST}  ·  баланс {W}{BLD}₹{bal}{RST}")
        _pm_cur = _load_pay_method()
        _pm_txt = f"{G}🎁 подарочные карты{RST}" if _pm_cur == "gift" else f"{C}💳 банковская карта{RST}"
        print(f"  Способ оплаты покупки: {_pm_txt}   {DIM}(переключить — «С»){RST}")
        print()
        if by_denom:
            for d in sorted(by_denom, reverse=True):
                print(f"    ₹{d:<5} × {by_denom[d]}")
        else:
            print(f"    {DIM}Карт нет. Добавляйте через TG-бота: «Другое» → «🎁 Гифт-карты».{RST}")
        print()

        # Список доступных карт с датой добавления
        if cards:
            section(f"Доступные карты  [{len(cards)} шт.]")
            print()
            for i, c in enumerate(sorted(cards, key=lambda x: -int(x.get("denom") or 0)), 1):
                _added = _fmt_msk(c["added_ts"]) if c.get("added_ts") else "—"
                print(f"  [{i:2}] ₹{int(c.get('denom') or 0):<5} "
                      f"серия {C}{c.get('number','')}{RST}  PIN {C}{c.get('pin','')}{RST}"
                      f"   {DIM}добавлена: {_added}{RST}")
            print()

        # История активаций / использования
        if used:
            section(f"История использования  [{len(used)} шт.]  (новые сверху)")
            print()
            for u in list(reversed(used)):
                _st = f"{Y}↩ др.аккаунт{RST}" if u.get("status") == "used_elsewhere" else f"{G}✔ применена{RST}"
                _when = u.get("used_str") or (_fmt_msk(u["used_ts"]) if u.get("used_ts") else "—")
                _pr = u.get("profile") or "—"
                _pin_u = f" PIN {u.get('pin')}" if u.get("pin") else ""
                print(f"  {_st}  ₹{int(u.get('denom') or 0):<5} серия {u.get('number','')}{_pin_u}"
                      f"   {DIM}{_when}  ·  {_pr}{RST}")
            if len(used) > 25:
                print(f"  {DIM}…ещё {len(used)-25}{RST}")
            print()

        print(f"  {DIM}Добавление карт — через TG-бота (текстом или файлом CSV/Excel).{RST}")
        print()
        _pm_sw = "на 💳 карту" if _pm_cur == "gift" else "на 🎁 гифт-карты"
        opt("С", f"Переключить способ оплаты {_pm_sw}", Y)
        opt("Д", "Удалить ВСЕ карты из хранилища", R)
        opt("0", "Назад", DIM)
        print()
        try:
            ch = input(f"  {BLD}Выбор: {RST}").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return
        if ch in ("0", ""):
            return
        if ch in ("с", "c", "s"):
            _save_pay_method("card" if _pm_cur == "gift" else "gift")
            continue
        if ch in ("д", "d"):
            try:
                _cf = input(f"  {R}Удалить все {len(cards)} карт (баланс ₹{bal})? [Д/Н]: {RST}").strip().lower()
            except (KeyboardInterrupt, EOFError):
                _cf = "н"
            if _cf in ("д", "y", "yes", "да"):
                _save_gift_cards([])
                print(f"  {Y}🗑 Хранилище гифт-карт очищено.{RST}")
                time.sleep(1.5)


def screen_cards():
    """Управление картами для оплаты."""
    while True:
        cls()
        header("КАРТЫ ДЛЯ ОПЛАТЫ", C)
        cards = _load_cards()
        order = _load_card_order()
        print()
        if cards:
            section(f"Сохранённые карты ({len(cards)} шт.)")
            print()
            for i, c in enumerate(cards, 1):
                print(f"  {_format_card_line(c, i)}")
            print()
            if order:
                _seq = " → ".join(str(i + 1) for i in order if 0 <= i < len(cards))
                print(f"  {G}Порядок попытки оплаты: {_seq}{RST}  {DIM}(для всех покупок){RST}")
            else:
                print(f"  {DIM}Порядок не задан — карты берутся по списку{RST}")
        else:
            print(f"  {DIM}Карт нет — добавьте через [1]{RST}")
        print()
        opt("1", "Добавить карту", G)
        if cards:
            opt("2", "Удалить карту", R)
            opt("3", "Изменить порядок карт (для всех покупок)", Y)
            if order:
                opt("4", "Сбросить порядок к умолчанию", DIM)
        print()
        opt("0", "Назад", R)
        print()
        choice = input(f"  {BLD}Выбор: {RST}").strip()
        if choice == "0" or choice == "":
            return
        elif choice == "1":
            card = _ask_card_fields()
            if card:
                cards.append(card)
                _save_cards(cards)
                print(f"\n  {G}✅ Карта «{card['nickname']}» добавлена.{RST}")
                time.sleep(1.2)
        elif choice == "2" and cards:
            nums = input(f"  Номер карты для удаления [1-{len(cards)}]: ").strip()
            try:
                idx = int(nums) - 1
                if 0 <= idx < len(cards):
                    removed = cards.pop(idx)
                    _save_cards(cards)
                    print(f"\n  {Y}Карта «{removed['nickname']}» удалена.{RST}")
                    time.sleep(1.0)
            except ValueError:
                pass
        elif choice == "3" and cards:
            print(f"\n  {DIM}Введите порядок карт числами через пробел (напр. 1 3 2).{RST}")
            print(f"  {DIM}Сначала пробуется первая указанная, затем следующие.{RST}")
            raw = input(f"  {BLD}Порядок: {RST}").strip()
            import re as _re_co
            tokens = [t for t in _re_co.split(r"[\s,;]+", raw) if t]
            new_order, seen, bad = [], set(), False
            for t in tokens:
                try:
                    n = int(t) - 1
                except ValueError:
                    bad = True; break
                if not (0 <= n < len(cards)) or n in seen:
                    bad = True; break
                new_order.append(n); seen.add(n)
            if bad or not new_order:
                print(f"\n  {R}Неверный ввод. Пример: 1 3 2{RST}")
                time.sleep(1.5)
            else:
                _save_card_order(new_order)
                _seq = " → ".join(str(i + 1) for i in new_order)
                print(f"\n  {G}✅ Порядок сохранён: {_seq}{RST}")
                time.sleep(1.2)
        elif choice == "4" and order:
            _save_card_order([])
            print(f"\n  {Y}Порядок сброшен к умолчанию.{RST}")
            time.sleep(1.0)


# ── GGSell: панель продавца ──────────────────────────────────────────────────

def _ggs_keys() -> tuple[str, int]:
    gs = (_read_secrets().get("ggsel") or {})
    try:
        sid = int(gs.get("seller_id") or 0)
    except Exception:
        sid = 0
    return gs.get("api_key", "").strip(), sid


def _ggs_st(status: str) -> str:
    """Цветная метка статуса заказа."""
    s = str(status or "").lower()
    if not s:
        return ""
    col = (G if s in ("paid", "success", "complete", "completed", "delivered", "sent")
           else (R if s in ("refund", "refunded", "canceled", "cancelled", "error")
                 else Y))
    return f"{col}{status}{RST}"


def _ggs_parse_order(order: dict) -> dict:
    import re as _re
    product = order.get("product") or {}
    name = (order.get("product_name") or order.get("name") or order.get("offer_title")
            or product.get("name") or "YouTube Premium")
    buyer = order.get("buyer") or order.get("buyer_info") or {}
    email = (buyer.get("email") or order.get("buyer_email") or order.get("email") or "")
    # цена покупателя: из product (last-orders) или полей заказа
    price = ""
    _pu = product.get("price_usd") or order.get("price_usd")
    _pr = product.get("price_rub") or order.get("price_rub")
    if _pu:
        price = f"${_pu}"
    elif _pr:
        price = f"{_pr}₽"
    sum_buy = (order.get("sum_t") or order.get("sum") or order.get("amount_t")
               or order.get("amount") or order.get("total") or price)
    sum_sell = (order.get("sum_seller") or order.get("seller_sum")
                or order.get("seller_reward_amount") or order.get("payout") or "")
    status = str(order.get("status") or order.get("state") or "")
    date = str(order.get("date") or order.get("created_at") or "").replace("T", " ")[:16]
    try:
        inv = int(order.get("invoice_id") or order.get("id") or 0)
    except Exception:
        inv = 0
    opts: list[tuple[str, str]] = []
    for s in (order.get("selected_options") or []):
        s = str(s).strip()
        if ": " in s:
            k, v = s.split(": ", 1)
            v = _re.sub(r"\s*\(\+[\d.]+\s*RUB\)", "", v).strip()
            if k.strip() and v:
                opts.append((k.strip(), v))
    if not opts:
        for o in (order.get("options") or []):
            k = (o.get("name") or o.get("title") or "").strip()
            v = (o.get("user_data") or o.get("value") or "").strip()
            if k and v:
                opts.append((k, v))
    ns = str(name)
    if len(ns) > 46:
        ns = ns[:45] + "…"
    return {"inv": inv, "name": ns, "email": str(email), "sum_buy": sum_buy,
            "sum_sell": sum_sell, "status": status, "date": date, "options": opts,
            "price": price}


async def _ggs_overview(api_key: str, seller_id: int):
    from ggsell.client import GGSellClient
    async with GGSellClient(api_key, seller_id) as cli:
        try:
            bal = await cli.get_balance_info()
        except Exception:
            bal = {"free": 0.0, "lock": 0.0, "plus": 0.0}
        orders: list = []
        try:
            orders = await cli.get_orders_v1(limit=25)
        except Exception:
            orders = []
        if not orders:
            try:
                orders = await cli.get_last_orders()
            except Exception:
                orders = []
        return bal, orders


async def _ggs_order_full(api_key: str, seller_id: int, order_id: int):
    """Детали заказа (buyer_email, status, options, reward) + сообщения."""
    from ggsell.client import GGSellClient
    async with GGSellClient(api_key, seller_id) as cli:
        try:
            v2 = await cli.get_order_info_v2(order_id)
        except Exception:
            v2 = {}
        try:
            msgs = await cli.get_messages(order_id)
        except Exception:
            msgs = []
        return v2, msgs


async def _ggs_send_msg(api_key: str, seller_id: int, order_id: int, text: str) -> bool:
    from ggsell.client import GGSellClient
    async with GGSellClient(api_key, seller_id) as cli:
        return await cli.send_message(order_id, text)


async def _ggs_reviews(api_key: str, seller_id: int, limit: int = 40):
    from ggsell.client import GGSellClient
    async with GGSellClient(api_key, seller_id) as cli:
        try:
            return await cli.get_reviews(limit=limit)
        except Exception:
            return []


# Шаблоны ответов покупателю (имя в ggsel_templates.json → подпись в меню)
_GGS_REPLY_TEMPLATES = [
    ("msg_greeting",     "Приветствие"),
    ("msg_wait",         "Просьба подождать"),
    ("msg_review_promo", "Просьба об отзыве"),
    ("msg_template",     "Выдача ссылки  (с {link})"),
]


def _ggs_pick_reply(inv: int) -> str:
    """Меню: выбрать шаблон ответа или ввести свой текст. Возвращает текст (или '')."""
    from ggsell.monitor import get_template as _gt
    cls()
    header(f"СООБЩЕНИЕ — ЗАКАЗ #{inv}", G)
    print(f"  {DIM}Выберите шаблон или напишите свой текст:{RST}\n")
    for ti, (tname, tlabel) in enumerate(_GGS_REPLY_TEMPLATES, 1):
        preview = _gt(tname).replace("\n", " ").strip()[:58]
        print(f"  {BLD}{Y}[{ti}]{RST} {W}{tlabel}{RST}  {DIM}{preview}…{RST}")
    print(f"  {BLD}{Y}[0]{RST} {W}Свой текст{RST}")
    print()
    try:
        pick = input(f"  {BLD}Шаблон [1-{len(_GGS_REPLY_TEMPLATES)}] или 0: {RST}").strip()
    except EOFError:
        return ""
    text = ""
    if pick.isdigit() and 1 <= int(pick) <= len(_GGS_REPLY_TEMPLATES):
        text = _gt(_GGS_REPLY_TEMPLATES[int(pick) - 1][0])
    if text:
        print(f"\n  {DIM}Текст шаблона:{RST}")
        print(f"  {W}{text}{RST}")
        print(f"\n  {DIM}Enter — отправить как есть, или введите новый текст (замените {{link}} и т.п.):{RST}")
    else:
        print(f"\n  {DIM}Введите текст сообщения:{RST}")
    try:
        edited = input("  > ").strip()
    except EOFError:
        edited = ""
    if edited:
        text = edited
    return text


def _ggs_reviews_screen(api_key: str, seller_id: int) -> None:
    cls()
    header("GGSELL — ОТЗЫВЫ", C)
    print(f"  {DIM}Загружаю отзывы…{RST}")
    try:
        reviews = asyncio.run(_ggs_reviews(api_key, seller_id))
    except Exception as e:
        print(f"\n  {R}Ошибка: {e}{RST}")
        pause()
        return
    cls()
    header("GGSELL — ОТЗЫВЫ", C)
    if not isinstance(reviews, list) or not reviews:
        print(f"\n  {DIM}Отзывов нет.{RST}")
        pause()
        return
    good = sum(1 for r in reviews if str(r.get("type") or r.get("rating")
                                         or r.get("mark") or "").lower() in ("good", "5", "positive", "+"))
    section(f"Отзывы  [{len(reviews)}]   {G}👍 {good}{RST}  {R}👎 {len(reviews) - good}{RST}")
    print()
    for r in reviews[:30]:
        mark = str(r.get("type") or r.get("rating") or r.get("mark") or "").lower()
        pos = mark in ("good", "5", "4", "positive", "+")
        icon = f"{G}👍{RST}" if pos else f"{R}👎{RST}"
        txt = str(r.get("text") or r.get("message") or r.get("comment") or r.get("content") or "").strip()
        date = str(r.get("date") or r.get("created_at") or r.get("date_add") or "")[:16].replace("T", " ")
        inv = r.get("invoice_id") or r.get("order_id") or r.get("id") or ""
        reply = str(r.get("answer") or r.get("reply") or r.get("seller_answer") or "").strip()
        print(f"  {icon}  {DIM}{date}{RST}  {W}#{inv}{RST}")
        if txt:
            print(f"      {W}{txt[:120]}{RST}")
        if reply:
            print(f"      {DIM}↳ ваш ответ: {reply[:100]}{RST}")
    print()
    pause()


def _ggs_order_detail(api_key: str, seller_id: int, order: dict) -> None:
    inv = int(order.get("invoice_id") or order.get("id") or 0)
    while True:
        cls()
        header(f"GGSELL — ЗАКАЗ #{inv}", C)
        print(f"  {DIM}Загружаю детали заказа…{RST}")
        try:
            v2, msgs = asyncio.run(_ggs_order_full(api_key, seller_id, inv))
        except Exception as e:
            v2, msgs = {}, []
            print(f"  {R}Не удалось загрузить детали: {e}{RST}")
        # объединяем тонкий заказ из списка с богатыми полями v2
        merged = dict(order)
        if isinstance(v2, dict):
            merged.update({k: val for k, val in v2.items() if val not in (None, "")})
        p = _ggs_parse_order(merged)
        cls()
        header(f"GGSELL — ЗАКАЗ #{inv}", C)
        print(f"  Покупатель: {C}{p['email'] or '—'}{RST}")
        print(f"  Товар     : {W}{p['name']}{RST}")
        print(f"  Статус    : {p['status'] or '—'}   {DIM}{p['date']}{RST}")
        if p["sum_buy"] or p["sum_sell"]:
            print(f"  Суммы     : покупка {p['sum_buy'] or '—'}  ·  "
                  f"{G}продавцу {p['sum_sell'] or '—'}{RST}")
        for k, v in p["options"]:
            print(f"  {DIM}{k}:{RST} {W}{v}{RST}")
        section("Переписка с покупателем")
        print()
        if not msgs:
            print(f"  {DIM}Сообщений нет.{RST}")
        try:
            from ggsell.monitor import is_own_sent as _own_sent
        except Exception:
            _own_sent = None
        for m in (msgs[-15:] if isinstance(msgs, list) else []):
            txt = str(m.get("message") or m.get("text") or m.get("content") or "").strip()
            ts = str(m.get("date_written") or m.get("date")
                     or m.get("created_at") or "")[:16].replace("T", " ")
            is_seller = bool(m.get("is_seller") or m.get("is_seller_msg")
                             or m.get("sender") == "seller" or m.get("type") == "seller")
            if not is_seller and _own_sent is not None and txt:
                try:
                    is_seller = _own_sent(inv, txt)
                except Exception:
                    pass
            tag = f"{G}🏪 Вы{RST}" if is_seller else f"{C}👤 Покупатель{RST}"
            print(f"  [{tag}{DIM} {ts}{RST}]")
            if txt:
                print(f"    {W}{txt}{RST}")
        print()
        opt("С", "Написать сообщение покупателю", G)
        opt("R", "Обновить", C)
        opt("0", "Назад", R)
        print()
        ch = input(f"  {BLD}Действие [С/R/0]: {RST}").strip().upper()
        if ch in ("0", ""):
            return
        if ch in ("R", "К", "K"):
            continue
        if ch in ("С", "C", "S"):
            text = _ggs_pick_reply(inv)
            if not text:
                continue
            try:
                ok = asyncio.run(_ggs_send_msg(api_key, seller_id, inv, text))
            except Exception as e:
                ok = False
                print(f"  {R}Ошибка отправки: {e}{RST}")
            print(f"  {G}✅ Отправлено покупателю{RST}" if ok
                  else f"  {R}❌ Не отправлено{RST}")
            time.sleep(1.3)


def screen_ggsell() -> None:
    """Панель продавца GGSell: баланс, заказы, переписка с покупателями."""
    api_key, seller_id = _ggs_keys()
    if not api_key or not seller_id:
        cls()
        header("GGSELL — ПАНЕЛЬ ПРОДАВЦА", C)
        print(f"\n  {Y}GGSell не настроен.{RST}")
        print(f"  {DIM}Заполните secrets.yaml → ggsel.api_key и ggsel.seller_id{RST}")
        pause()
        return

    while True:
        cls()
        header("GGSELL — ПАНЕЛЬ ПРОДАВЦА", C)
        print(f"  {DIM}Загружаю данные GGSell…{RST}")
        try:
            bal, orders = asyncio.run(_ggs_overview(api_key, seller_id))
        except Exception as e:
            print(f"\n  {R}Ошибка GGSell API: {e}{RST}")
            pause()
            return

        cls()
        header("GGSELL — ПАНЕЛЬ ПРОДАВЦА", C)
        free = float(bal.get("free") or 0.0)
        lock = float(bal.get("lock") or 0.0)
        plus = float(bal.get("plus") or 0.0)
        print(f"  💵 Баланс: {G}{BLD}${free:,.2f}{RST}    "
              f"{DIM}холд ${lock:,.2f}   ·   плюс ${plus:,.2f}{RST}")
        shown = orders[:20] if isinstance(orders, list) else []
        section(f"Последние заказы  [{len(orders) if isinstance(orders, list) else 0}]")
        print()
        if not shown:
            print(f"  {DIM}Заказов нет.{RST}")
        for i, o in enumerate(shown, 1):
            p = _ggs_parse_order(o)
            # список (last-orders) тонкий: показываем покупателя если есть, иначе цену
            mid = p["email"] or p["price"] or "—"
            print(f"  {BLD}{Y}[{i:>2}]{RST}  {W}#{p['inv']}{RST}  "
                  f"{DIM}{p['date']:<16}{RST}  {C}{mid}{RST}"
                  + (f"   {_ggs_st(p['status'])}" if p["status"] else "")
                  + (f"   {G}+${p['sum_sell']}{RST}" if p["sum_sell"] else ""))
            if p["name"]:
                print(f"        {DIM}{p['name']}{RST}")
        print()
        opt("О", "Отзывы покупателей", C)
        opt("R / Enter", "Обновить", C)
        opt("0", "Назад", R)
        print()
        ch = input(f"  {BLD}Заказ [1-{len(shown)}], О, R или 0: {RST}").strip().upper()
        if ch == "0":
            return
        if ch in ("", "R", "К", "K"):
            continue
        if ch in ("О", "O"):
            _ggs_reviews_screen(api_key, seller_id)
            continue
        try:
            oi = int(ch) - 1
            if not (0 <= oi < len(shown)):
                raise ValueError
        except ValueError:
            continue
        _ggs_order_detail(api_key, seller_id, shown[oi])


def screen_stop_all() -> None:
    """Показывает запущенные процессы и активные номера; позволяет всё остановить."""
    import grizzly as _gz
    while True:
        cls()
        header("ПРОЦЕССЫ И НОМЕРА", R)
        running, st = shared_automation_running()
        pid = int(st.get("automation_pid") or 0)
        mode = st.get("automation_mode") or ""
        owner = st.get("automation_owner") or ""
        print()
        if running and pid:
            print(f"  {G}▶ Автоматизация ЗАПУЩЕНА{RST}  {DIM}(pid {pid}"
                  + (f", режим {mode}" if mode else "")
                  + (f", запуск: {owner}" if owner else "") + f"){RST}")
        else:
            print(f"  {DIM}○ Активного процесса автоматизации нет.{RST}")

        print(f"  {DIM}Проверяю активные номера GrizzlySMS…{RST}")
        try:
            act = _gz.fetch_active_rentals_status_blocking(timeout=12)
        except Exception as e:
            act = {"error": str(e)}
        if act.get("error"):
            print(f"  Активные номера: {Y}? ({act['error']}){RST}")
        else:
            n = int(act.get("total") or 0)
            bal = act.get("balance")
            print(f"  Активных номеров: {(R if n else G)}{BLD}{n}{RST}"
                  + (f"   {DIM}баланс ${bal:.4f}{RST}"
                     if isinstance(bal, (int, float)) else ""))
        print()
        opt("1", "🛑 ОСТАНОВИТЬ ВСЁ  (процесс + Chrome + отменить номера)", R)
        opt("2", "Обновить", C)
        opt("0", "Назад", DIM)
        print()
        ch = input(f"  {BLD}Выбор [1/2/0]: {RST}").strip()
        if ch in ("0", ""):
            return
        if ch == "2":
            continue
        if ch == "1":
            print(f"\n  {Y}Останавливаю всё…{RST}")
            res = stop_all_automation("кнопка «Остановить всё»")
            if res.get("killed_proc"):
                print(f"  {G}✔ Процесс автоматизации остановлен{RST}")
            else:
                print(f"  {DIM}○ Активного процесса автоматизации не было{RST}")
            print(f"  {G}✔ Chrome бота закрыт: {res.get('chrome', 0)}{RST}")
            c = res.get("cancel") or {}
            if c.get("error"):
                print(f"  {R}✘ Отмена номеров: {c['error']}{RST}")
            else:
                print(f"  {G}✔ Номеров отменено: {c.get('cancelled', 0)} "
                      f"из {c.get('total', 0)}{RST}")
                _cbal = c.get("balance")
                if isinstance(_cbal, (int, float)):
                    print(f"  {DIM}Баланс GrizzlySMS: ${_cbal:.4f}{RST}")
            pause()
            return


def screen_main():
    """Главное меню."""
    while True:
        cls()
        header()
        print(f"  {_tg_status_line()}")
        _upd_avail   = _bot_module._update_available
        _upd_commits = _bot_module._update_commits
        if _upd_avail:
            print(f"  {Y}⚡ Доступно обновление! [{BLD}{len(_upd_commits)} коммитов{RST}{Y}]  →  нажмите [У]{RST}")
        # Видимый индикатор: идёт ли автоматизация (в т.ч. зависший процесс)
        _auto_running, _auto_st = shared_automation_running()
        if _auto_running:
            print(f"  {R}▶ Автоматизация запущена{RST} "
                  f"{DIM}(pid {_auto_st.get('automation_pid')}){RST}  "
                  f"{R}→ [С] остановить всё{RST}")
        print()
        section("АВТОМАТИЗАЦИЯ", G)
        opt("1", "Запуск | Вход на ПК", G)
        opt("2", "Запуск | Подбор аккаунта TG", G)
        opt("8", "Запуск | Вход с данными", G)
        opt("9", "Запуск | Полный цикл", G)

        section("ПРОФИЛИ", C)
        opt("3", "Открыть сохранённый профиль Chrome", C)
        opt("П", "Проверить активацию всех выданных", G)
        opt("К", "Восстановить профиль из JSON куков (cookies_backup/)", C)
        opt("6", "🟡 Архив профилей", M)

        section("GGSELL", C)
        opt("7", "🛒 Панель продавца  (баланс · заказы · переписка)", C)

        section("НАСТРОЙКИ", B)
        opt("М", f"SMS-провайдер: {_sms_provider_menu_label()}", Y)
        opt("С", "🛑 Остановить процессы / отменить все номера", R)
        opt("0", "Карты для оплаты (добавить / удалить)", C)
        _gc_bal = _gift_balance()
        opt("Г", f"🎁 Подарочные карты  {DIM}(баланс ₹{_gc_bal}){RST}", C)
        opt("4", "Просмотреть логи (automation.log)", B)
        opt("5", "Установить / обновить зависимости", M)
        _upd_lbl = (f"Обновить до последней версии  {Y}[{len(_upd_commits)} новых]{RST}"
                    if _upd_avail else "Проверить обновления")
        opt("У", _upd_lbl, Y if _upd_avail else DIM)

        print()
        opt("В", "Выход", R)
        print()
        print(f"  {DIM}  Ctrl+C — остановка и возврат в меню  |  В — выход{RST}")
        print()

        try:
            choice = input(f"\n  {BLD}Выберите [0-9 / Q]: {RST}").strip().upper()
        except KeyboardInterrupt:
            continue
        except EOFError:
            # На Windows Ctrl+C иногда переводит stdin в EOF — пробуем сбросить
            try:
                if os.name == "nt":
                    sys.stdin = open("CON:", "r",
                                     encoding=getattr(sys.stdin, "encoding", None) or "utf-8",
                                     errors="replace")
            except Exception:
                sys.exit(0)
            continue

        try:
            if choice == "1":
                screen_run_auto(tg_mode="login")
            elif choice == "2":
                screen_run_auto(tg_mode="intercept")
            elif choice == "8":
                screen_run_auto(tg_mode="login", stop_at_email=True)
            elif choice == "3":
                screen_profiles()
            elif choice == "4":
                screen_logs()
            elif choice == "5":
                screen_install()
            elif choice == "6":
                screen_used()
            elif choice == "7":
                screen_ggsell()
            elif choice in ("М", "M"):
                nxt = _cycle_sms_provider()
                print(f"\n  {G}✓ SMS: {_SMS_PROVIDER_LABEL.get(nxt, nxt)}{RST}")
                time.sleep(0.8)
            elif choice in ("С", "C", "S"):
                screen_stop_all()
            elif choice == "9":
                screen_all_in_one()
            elif choice in ("П", "P"):
                screen_check_all_activated()
            elif choice in ("К", "K"):
                screen_restore_from_cookies()
            elif choice == "0":
                screen_cards()
            elif choice in ("Г", "G", "г"):
                screen_gift_cards()
            elif choice in ("У", "U"):
                screen_update()
            elif choice in ("В", "Q"):
                cls()
                print(f"\n{C}{BLD}  До свидания!{RST}\n")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            pass


def _init_secrets() -> None:
    """secrets.yaml — единственный источник API-ключей.

    При первом запуске извлекает ключи из config.yaml в secrets.yaml.
    После миграции config.yaml больше не содержит секреты.
    """
    import yaml as _yaml

    script_dir      = _HERE
    secrets_path    = script_dir / "secrets.yaml"
    secrets_example = script_dir / "secrets.yaml.example"
    cfg_path        = script_dir / "config.yaml"
    example_path    = script_dir / "config.yaml.example"

    _SECRET_KEYS = [
        ("grizzlysms", "api_key"),
        ("pvapins", "api_key"),
        ("telegram", "token"),
        ("proxy6", "api_key"),
        ("github", "token"),
        ("ggsel", "api_key"),
    ]
    _PLACEHOLDERS = {
        "", "YOUR_GRIZZLYSMS_API_KEY", "YOUR_TELEGRAM_BOT_TOKEN",
        "YOUR_PROXY6_API_KEY",
    }

    def _real(val) -> bool:
        if val is None:
            return False
        s = str(val).strip()
        return (
            bool(s)
            and not s.upper().startswith(("YOUR_", "ВАШ_"))
            and s not in _PLACEHOLDERS
        )

    if not cfg_path.exists() and example_path.exists():
        shutil.copy(example_path, cfg_path)
        print(f"  {Y}[Конфиг] Создан config.yaml из шаблона.{RST}")

    if not cfg_path.exists():
        return

    with open(cfg_path, encoding="utf-8") as f:
        cfg = _yaml.safe_load(f) or {}

    if not secrets_path.exists():
        extracted: dict = {}
        for section, key in _SECRET_KEYS:
            val = (cfg.get(section) or {}).get(key)
            if _real(val):
                extracted.setdefault(section, {})[key] = val
        if extracted:
            with open(secrets_path, "w", encoding="utf-8") as f:
                _yaml.dump(extracted, f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
            print(f"  {G}[Секреты] API-ключи перенесены в secrets.yaml{RST}")

    if not secrets_path.exists():
        return

    with open(secrets_path, encoding="utf-8") as f:
        secrets = _yaml.safe_load(f) or {}

    # Если secrets.yaml содержит заглушки, а config.yaml — реальные ключи, обновляем secrets.yaml
    _sec_changed = False
    for section, key in _SECRET_KEYS:
        cfg_val = (cfg.get(section) or {}).get(key)
        sec_val = (secrets.get(section) or {}).get(key)
        if _real(cfg_val) and not _real(sec_val):
            secrets.setdefault(section, {})[key] = cfg_val
            _sec_changed = True
    if _sec_changed:
        _tmp_s = secrets_path.with_suffix(".yaml.tmp")
        try:
            with open(_tmp_s, "w", encoding="utf-8") as f:
                _yaml.dump(secrets, f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
            _tmp_s.replace(secrets_path)
            print(f"  {G}[Секреты] secrets.yaml обновлён реальными ключами из config.yaml.{RST}")
        except Exception as _we:
            try: _tmp_s.unlink(missing_ok=True)
            except Exception: pass
            print(f"  {Y}[Секреты] Не удалось обновить secrets.yaml: {_we}{RST}")

    # После успешной миграции удаляем секреты из legacy config.yaml.
    try:
        with open(secrets_path, encoding="utf-8") as f:
            _disk_secrets = _yaml.safe_load(f) or {}
    except Exception:
        _disk_secrets = {}
    _cfg_changed = False
    for section, key in _SECRET_KEYS:
        section_data = cfg.get(section)
        cfg_value = section_data.get(key) if isinstance(section_data, dict) else None
        disk_value = (_disk_secrets.get(section) or {}).get(key)
        if (
            isinstance(section_data, dict)
            and key in section_data
            and (not _real(cfg_value) or _real(disk_value))
        ):
            section_data.pop(key, None)
            if not section_data:
                cfg.pop(section, None)
            _cfg_changed = True
    if _cfg_changed:
        _tmp_c = cfg_path.with_suffix(".yaml.tmp")
        try:
            with open(_tmp_c, "w", encoding="utf-8") as f:
                _yaml.dump(cfg, f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
            _tmp_c.replace(cfg_path)
            print(f"  {G}[Секреты] Ключи удалены из config.yaml после миграции.{RST}")
        except Exception as _we:
            with contextlib.suppress(Exception):
                _tmp_c.unlink(missing_ok=True)
            print(f"  {Y}[Секреты] Не удалось очистить config.yaml: {_we}{RST}")

    # Кэшируем secrets в глобальной переменной — единственный источник ключей
    global _SECRETS
    _SECRETS = secrets


def _check_setup() -> None:
    """Проверяет заполненность secrets.yaml. При первом запуске — показывает
    инструкцию и выходит (menu.bat не перезапускает при sys.exit(0))."""
    import yaml as _yaml

    script_dir   = _HERE
    secrets_path = script_dir / "secrets.yaml"
    example_path = script_dir / "secrets.yaml.example"

    def _real(val) -> bool:
        if val is None:
            return False
        s = str(val).strip()
        return (
            bool(s)
            and not s.upper().startswith(("YOUR_", "ВАШ_"))
            and s not in {"", "null", "~"}
        )

    secrets: dict = {}
    if secrets_path.exists():
        try:
            with open(secrets_path, encoding="utf-8") as _f:
                secrets = _yaml.safe_load(_f) or {}
        except Exception:
            pass

    grizzly_ok  = _real((secrets.get("grizzlysms") or {}).get("api_key"))
    telegram_ok = _real((secrets.get("telegram")   or {}).get("token"))

    if grizzly_ok and telegram_ok:
        return  # всё настроено — продолжаем нормальный запуск

    # ── Первый запуск: сначала проверяем обновления ───────────────────────────
    cls()
    header("ПЕРВЫЙ ЗАПУСК", Y)
    print(f"\n  {DIM}Проверяю обновления перед настройкой...{RST}")
    try:
        _upd = _http_check_updates()
        if _upd:
            print(f"  {G}Найдено обновлений: {len(_upd)}{RST}")
            _ok_u, _msg_u = _http_do_update()
            if _ok_u:
                print(f"  {G}✓ Обновление применено. Перезапуск...{RST}")
                time.sleep(2)
                _exit_code[0] = 42  # finally выйдет с кодом 42 → menu.bat перезапустит
                sys.exit(42)  # menu.bat перезапустит
            print(f"  {Y}Не удалось применить: {_msg_u}{RST}")
        else:
            print(f"  {G}✓ Версия актуальна.{RST}")
    except Exception:
        print(f"  {DIM}Нет связи — пропускаю проверку обновлений.{RST}")

    # ── Экран настройки ───────────────────────────────────────────────────────
    cls()
    header("НАСТРОЙКА — ПЕРВЫЙ ЗАПУСК", Y)
    print()

    file_exists = secrets_path.exists()

    if not file_exists:
        if example_path.exists():
            print(f"  {Y}⚠  Файл secrets.yaml не найден.{RST}")
            print()
            print(f"  {C}Шаг 1.{RST}  Откройте файл шаблона:")
            print(f"          {DIM}{example_path}{RST}")
        else:
            print(f"  {R}❌ Ни secrets.yaml, ни secrets.yaml.example не найдены.{RST}")
            print(f"     Скачайте репозиторий заново.")
            print()
            pause()
            sys.exit(0)
    else:
        print(f"  {Y}⚠  Не заполнены обязательные ключи в secrets.yaml{RST}")
        print()
        print(f"  Файл: {C}{secrets_path}{RST}")

    print()
    print(f"  {'Шаг 2.' if not file_exists else 'Заполните:'} {RST}Вставьте свои ключи:")
    print()

    if not grizzly_ok:
        print(f"  {R}✗  grizzlysms:{RST}")
        print(f"       api_key: ВАШ_КЛЮЧ    {DIM}← grizzlysms.com → Баланс → API{RST}")
    else:
        print(f"  {G}✓  grizzlysms.api_key — заполнен{RST}")

    print()

    if not telegram_ok:
        print(f"  {R}✗  telegram:{RST}")
        print(f"       token: ВАШ_ТОКЕН    {DIM}← @BotFather в Telegram → /newbot{RST}")
    else:
        print(f"  {G}✓  telegram.token — заполнен{RST}")

    print()

    if not file_exists:
        print(f"  {C}Шаг 3.{RST}  Переименуйте файл — удалите \".example\" из названия:")
        print(f"          {DIM}secrets.yaml.example  →  secrets.yaml{RST}")
        print()
        print(f"  {C}Шаг 4.{RST}  Снова запустите {C}menu.bat{RST}")
    else:
        print(f"  После заполнения ключей снова запустите {C}menu.bat{RST}")

    print()
    pause()
    sys.exit(0)


def _migrate_config() -> None:
    """Добавляет в config.yaml новые параметры из config.yaml.example.
    Существующие значения пользователя не изменяются — только дополнение."""
    import yaml as _yaml

    cfg_path     = _HERE / "config.yaml"
    example_path = _HERE / "config.yaml.example"
    if not cfg_path.exists() or not example_path.exists():
        return

    def _deep_merge(user: dict, defaults: dict) -> list[str]:
        added: list[str] = []
        for key, val in defaults.items():
            if key not in user:
                user[key] = val
                added.append(key)
            elif isinstance(val, dict) and isinstance(user.get(key), dict):
                sub = _deep_merge(user[key], val)
                added.extend(f"{key}.{k}" for k in sub)
        return added

    try:
        with open(example_path, encoding="utf-8") as f:
            defaults = _yaml.safe_load(f) or {}
        with open(cfg_path, encoding="utf-8") as f:
            user = _yaml.safe_load(f) or {}
        added = _deep_merge(user, defaults)
        if added:
            _tmp_m = cfg_path.with_suffix(".yaml.tmp")
            try:
                with open(_tmp_m, "w", encoding="utf-8") as f:
                    _yaml.dump(user, f, allow_unicode=True,
                               default_flow_style=False, sort_keys=False)
                _tmp_m.replace(cfg_path)
                print(f"  {G}[Обновление конфига] Добавлены новые параметры: "
                      f"{', '.join(added)}{RST}")
                print(f"  {DIM}Заполните их в config.yaml если нужно.{RST}")
            except Exception as _we:
                try: _tmp_m.unlink(missing_ok=True)
                except Exception: pass
                print(f"  {Y}[Предупреждение] Не удалось записать config.yaml: {_we}{RST}")
    except Exception as _e:
        print(f"  {Y}[Предупреждение] Не удалось обновить config.yaml: {_e}{RST}")


def _startup_cleanup() -> None:
    """При каждом запуске: удаляем старые логи, использованные профили, убиваем Chrome."""
    import grizzly as _gz, threading as _thr

    # Убиваем Chrome в фоне — не блокирует запуск меню
    _thr.Thread(target=_gz.kill_all_bot_chrome, daemon=True, name="chrome-kill").start()

    # Логи (automation*.log, test_run.log и любые *.log рядом со скриптом)
    script_dir = _HERE
    deleted_logs = 0
    for log_file in script_dir.glob("*.log"):
        try:
            log_file.unlink()
            deleted_logs += 1
        except Exception:
            pass

    if deleted_logs:
        print(f"  {DIM}[Очистка] удалено логов: {deleted_logs}{RST}")


# ── Точка входа ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Регистрация Windows Console Control Handler для чистой очистки номеров при остановке процесса
    try:
        import ctypes
        if os.name == "nt":
            PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
            def _win32_ctrl_handler(ctrl_type):
                # ctrl_type: 0=CTRL_C, 1=CTRL_BREAK, 2=CTRL_CLOSE (закрытие окна)
                if ctrl_type in (0, 1):
                    # Ctrl+C / Ctrl+Break — явно прерываем главный поток.
                    # return False не гарантирует KeyboardInterrupt для CTRL_BREAK:
                    # дефолтный Windows-обработчик убивает процесс без finally.
                    import _thread
                    _thread.interrupt_main()
                    return True
                if ctrl_type == 2:
                    # Закрытие окна — Python до finally не доходит, запускаем вручную.
                    # СНАЧАЛА убиваем дочерний процесс автоматизации, иначе он
                    # продолжит покупать номера уже после закрытия консоли.
                    try:
                        _kill_automation_proc()
                    except Exception:
                        pass
                    try:
                        import grizzly as _gz
                        _gz.cleanup_all_rentals_on_exit()
                    except Exception:
                        pass
                return False
            global _win32_handler_ref
            _win32_handler_ref = PHANDLER_ROUTINE(_win32_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_win32_handler_ref, True)
    except Exception:
        pass


    # Принудительный UTF-8 вывод — работает даже если cmd запущен без chcp 65001
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Подавляем шум от Playwright-тасков при завершении asyncio.run()
    # (CancelledError → TargetClosedError в pending-тасках — не ошибка логики)
    import asyncio as _aio, logging as _logging, warnings as _warnings

    _SUPPRESS_EXC = ("CancelledError", "TargetClosedError", "ConnectionClosedError",
                     "ConnectionResetError", "BrokenPipeError")
    _SUPPRESS_MSG = ("TargetClosed", "Task was destroyed", "exception was never retrieved",
                     "unclosed transport", "EPIPE", "broken pipe")

    def _quiet_exc_handler(loop, context):
        exc = context.get("exception")
        name = type(exc).__name__ if exc else ""
        if name in _SUPPRESS_EXC:
            return
        msg = context.get("message", "")
        if any(s in msg for s in _SUPPRESS_MSG):
            return
        if exc and any(s in str(exc) for s in _SUPPRESS_MSG):
            return
        loop.default_exception_handler(context)

    try:
        _aio.get_event_loop().set_exception_handler(_quiet_exc_handler)
    except Exception:
        pass

    # asyncio логгер тоже печатает "Future exception was never retrieved" — фильтруем
    class _AsyncioQuietFilter(_logging.Filter):
        def filter(self, record):
            m = record.getMessage()
            return not any(s in m for s in _SUPPRESS_MSG)
    _logging.getLogger("asyncio").addFilter(_AsyncioQuietFilter())

    # ResourceWarning "unclosed transport" при GC — уже отфильтровано в bot.py,
    # но menu.py тоже использует asyncio напрямую
    _warnings.filterwarnings("ignore", message="unclosed transport", category=ResourceWarning)

    # Python 3.14: ValueError "I/O operation on closed pipe" внутри __del__ ProactorBasePipeTransport.
    # Происходит при форматировании repr для warning-а — warnings.filterwarnings не перехватывает.
    # sys.unraisablehook — единственный способ подавить.
    _orig_unraisablehook = sys.unraisablehook
    def _quiet_unraisablehook(u):
        if (isinstance(u.exc_value, ValueError)
                and "closed pipe" in str(u.exc_value)
                and (u.object is None or "Transport" in type(u.object).__name__)):
            return
        _orig_unraisablehook(u)
    sys.unraisablehook = _quiet_unraisablehook

    _cli = sys.argv[1:]
    _exit_code[0] = 0

    try:
        if "--fill-to-payment" in _cli:
            _init_secrets()
            _migrate_config()
            _start_log_tee()

            def _get_cli_fill(flag: str, default: str = "") -> str:
                try:
                    return _cli[_cli.index(flag) + 1]
                except (ValueError, IndexError):
                    return default

            _phone_arg = _get_cli_fill("--phone", "").strip()
            _profiles = _load_done_profiles(force=True)
            if not _profiles:
                print(f"{R}Нет профилей с входом.{RST}")
                _exit_code[0] = 1
            else:
                _target = None
                if _phone_arg:
                    _digits = "".join(c for c in _phone_arg if c.isdigit())[-10:]
                    for _p in _profiles:
                        if _digits and _digits in str(_p.get("username", "")):
                            _target = _p
                            break
                        if _digits and _digits in _p["path"].name:
                            _target = _p
                            break
                    if not _target:
                        print(f"{R}Профиль +91 {_digits} не найден.{RST}")
                        _exit_code[0] = 1
                        _target = None
                else:
                    for _p in _profiles:
                        if _p.get("issued_ts") or _p.get("prepared_ts"):
                            continue
                        if _p.get("status") in ("activated", "explore_now", "activate_now"):
                            continue
                        if _p.get("login_ts"):
                            _target = _p
                            break
                if _target is None and _exit_code[0] == 1:
                    pass
                elif not _target:
                    _target = _profiles[0]
                if _target:
                    _path = Path(_target["path"])
                    print(f"\n  {C}▶ До оплаты: {_target.get('username') or _path.name}{RST}\n")
                    _addr = _gen_indian_address()
                    async def _run_fill_to_pay():
                        asyncio.get_running_loop().set_exception_handler(_quiet_exc_handler)
                        return await _do_fill_address(_path, _addr, stop_at_payment=True)
                    try:
                        _ok, _msg = asyncio.run(_run_fill_to_pay())
                        print(f"\n  {'✅' if _ok else '❌'} {_msg}\n")
                        _exit_code[0] = 0 if _ok else 1
                    except KeyboardInterrupt:
                        print(f"\n{Y}  Остановлено.{RST}")
                        _exit_code[0] = 130

        elif "--full-cycle" in _cli or "--login-only" in _cli:
            _mode_lc   = "full" if "--full-cycle" in _cli else "login"
            _init_secrets()
            _migrate_config()
            _start_log_tee()   # дублируем весь вывод в automation.log для ТГ-бота

            def _get_cli(flag: str, default: str) -> str:
                try:
                    return _cli[_cli.index(flag) + 1]
                except (ValueError, IndexError):
                    return default

            _headless  = "--headless" in _cli
            _intercept = "--intercept" in _cli
            _stop_at_email = "--stop-at-email" in _cli
            _tariffs_s = _get_cli("--tariffs", "3")
            _accounts  = int(_get_cli("--accounts", "0"))  # 0 = из конфига

            # Разворачиваем список тарифов / количество аккаунтов
            if "," in _tariffs_s:
                _tariff_list = [int(t) for t in _tariffs_s.split(",") if t.isdigit()]
            else:
                single = int(_tariffs_s) if _tariffs_s.isdigit() else 3
                if _accounts == 0:
                    try:
                        import yaml as _yaml
                        with open("config.yaml", encoding="utf-8") as _fh:
                            _cfg = _yaml.safe_load(_fh)
                        _accounts = int(_cfg.get("auto_accounts", 1))
                    except Exception:
                        _accounts = 1
                _tariff_list = [single] * _accounts

            _skip = (_mode_lc == "login")
            _label = "Вход" if _skip else "Полный цикл"

            async def _run_cycle():
                # Подавляем TargetClosedError / ConnectionClosedError из Playwright-тасков
                # на ЭТОМ event loop (asyncio.run создаёт новый, поэтому ставим здесь)
                asyncio.get_running_loop().set_exception_handler(_quiet_exc_handler)

                total = len(_tariff_list)
                import grizzly as _gz_s
                _gz_s.reset_run_stats()

                # ── Проверка Flipkart (без VPN — отдельный браузер не нужен) ──────
                if _vpn_extension_dir():
                    # Фоновая проверка прямого доступа: если Flipkart открывается
                    # без VPN — расширение не ставим и VPN не включаем
                    with contextlib.suppress(Exception):
                        asyncio.create_task(_flipkart_direct_accessible())
                    print(f"  {DIM}Проверка Flipkart в фоне: VPN подключится, только если сайт недоступен напрямую{RST}")
                else:
                    print(f"  {DIM}Проверка доступности Flipkart...{RST}")
                    if await _check_flipkart_accessible():
                        print(f"  {G}Flipkart доступен.{RST}")
                    else:
                        _ping_msg = "Flipkart недоступен"
                        print(f"\n  {R}⚠ {_ping_msg}{RST}")
                        print(f"  {Y}Номера покупаться не будут. Повторите позже.{RST}")
                        try:
                            _send_tg_error("", f"🌐 {_ping_msg}")
                        except Exception:
                            pass
                        _exit_code[0] = 2
                        return

                # max_concurrent_accounts из config.yaml
                try:
                    import yaml as _yrc
                    with open("config.yaml", encoding="utf-8") as _frc:
                        _cfg_rc = _yrc.safe_load(_frc) or {}
                    max_conc = int(_cfg_rc.get("max_concurrent_accounts", 5))
                    gsms_cfg = _cfg_rc.get("grizzlysms", {})
                    max_p_n = int(gsms_cfg.get("max_parallel_numbers", 15))
                except Exception:
                    max_conc = 5
                    max_p_n = 15
                max_conc = max(1, min(max_conc, total))
                max_par_per_flow = max(1, max_p_n // max_conc)

                sem = asyncio.Semaphore(max_conc)
                # Сериализует фазу покупки среди параллельных потоков (вход/OTP
                # остаются параллельными — лок берётся уже после входа).
                _pay_lock_rc = asyncio.Lock()
                results: list = [None] * total

                async def _one(idx: int, mths: int) -> None:
                    async with sem:
                        print(f"\n  [{idx+1}/{total}] {_label} — {mths} мес. [старт]")
                        try:
                            ok, msg = await _do_all_in_one(mths, headless=_headless,
                                                           skip_purchase=_skip,
                                                           max_par_override=max_par_per_flow,
                                                           intercept_mode=_intercept,
                                                           stop_at_email=_stop_at_email,
                                                           _pay_lock=_pay_lock_rc)
                            results[idx] = (ok, msg)
                            print(f"  [{idx+1}/{total}] {'✅' if ok else '❌'} {msg}")
                        except BaseException as exc:
                            results[idx] = (False, str(exc))
                            if not isinstance(exc, Exception):
                                print(f"  [{idx+1}/{total}] ⚠ Прервано ({type(exc).__name__})")
                            else:
                                print(f"  [{idx+1}/{total}] ❌ Ошибка: {exc}")

                # Баланс до покупки номеров
                try:
                    from sms_failover import build_sms_client as _bsc
                    import yaml as _y_bal
                    _cfg_bal = {}
                    try:
                        with open("config.yaml", encoding="utf-8") as _fb:
                            _cfg_bal = _y_bal.safe_load(_fb) or {}
                    except Exception:
                        pass
                    _cl_s = _bsc(_read_secrets(), _cfg_bal)
                    _gz_s._STATS["balance_start"] = await _cl_s.get_balance()
                    await _cl_s.close()
                except Exception:
                    pass

                _interrupted = False
                try:
                    await asyncio.gather(*[_one(i, m) for i, m in enumerate(_tariff_list)])
                except asyncio.CancelledError:
                    _interrupted = True

                # Баланс после завершения задач
                try:
                    from sms_failover import build_sms_client as _bsc2
                    import yaml as _y_bal2
                    _cfg_bal2 = {}
                    try:
                        with open("config.yaml", encoding="utf-8") as _fb2:
                            _cfg_bal2 = _y_bal2.safe_load(_fb2) or {}
                    except Exception:
                        pass
                    _cl_s2 = _bsc2(_read_secrets(), _cfg_bal2)
                    _gz_s._STATS["balance_end"] = await _cl_s2.get_balance()
                    await _cl_s2.close()
                except Exception:
                    pass

                ok_count = sum(1 for r in results if r and r[0])
                fail_count = total - ok_count

                st = _gz_s.get_run_stats()
                b_start = st["balance_start"]
                b_end   = st["balance_end"]
                spent   = round(b_start - b_end, 4) if (b_start is not None and b_end is not None) else None

                # ── Консольная статистика ──────────────────────────────────────
                print(f"\n  {'─'*48}")
                print(f"  📊 {BLD}Итоги запуска{RST}")
                print(f"  {'─'*48}")
                print(f"  💾  Профилей сохранено : {G}{BLD}{st['profiles_saved']}{RST}")
                print(f"  ✅  Успешных аккаунтов : {G}{ok_count}/{total}{RST}")
                print(f"  ❌  Неудачных          : {R}{fail_count}{RST}")
                print(f"  📞  Номеров куплено    : {st['numbers_bought']}")
                print(f"  ✔   Отменено           : {G}{st['numbers_cancelled']}{RST}")
                if st["numbers_bad_action"]:
                    print(f"  ⚠   Не найдено (BAD)  : {Y}{st['numbers_bad_action']}{RST}")
                if b_start is not None:
                    print(f"  💰  Баланс до          : ${b_start:.4f}")
                if b_end is not None:
                    print(f"  💰  Баланс после       : ${b_end:.4f}")
                if spent is not None:
                    print(f"  💸  Потрачено          : {R}${spent:.4f}{RST}")
                if _interrupted:
                    print(f"  {Y}⚠  Остановлено досрочно (Ctrl+C){RST}")
                print(f"  {'─'*48}\n")

                # ── TG-уведомление ─────────────────────────────────────────────
                try:
                    import json as _jst, urllib.request as _urst
                    _tg_tok_st = _get_telegram_token() if _tg_notify_enabled() else ""
                    _subs_st = TG_SUBSCRIBERS_FILE
                    if _tg_tok_st and _subs_st.exists():
                        _sd_st = _jst.loads(_subs_st.read_text(encoding="utf-8"))
                        _cids_st = [int(c) for c in _sd_st.get("chats", [])]
                        _tg_lines = [
                            "📊 <b>Итоги запуска</b>" + (" ⚠️ прервано" if _interrupted else ""),
                            "━━━━━━━━━━━━━━━━━━━",
                            f"💾 Профилей сохранено: <b>{st['profiles_saved']}</b>",
                            f"✅ Успешных аккаунтов: <b>{ok_count}/{total}</b>",
                            f"❌ Неудачных: <b>{fail_count}</b>",
                            f"📞 Номеров куплено: <b>{st['numbers_bought']}</b>",
                            f"✔️ Отменено: <b>{st['numbers_cancelled']}</b>",
                        ]
                        if st["numbers_bad_action"]:
                            _tg_lines.append(f"⚠️ Уже не существовали: <b>{st['numbers_bad_action']}</b>")
                        if b_start is not None and b_end is not None:
                            _tg_lines.append(f"💰 Баланс: <b>${b_start:.4f}</b> → <b>${b_end:.4f}</b>")
                        if spent is not None:
                            _tg_lines.append(f"💸 Потрачено: <b>${spent:.4f}</b>")
                        _msg_st = "\n".join(_tg_lines)
                        for _cid_st in _cids_st:
                            try:
                                _req_st = _urst.Request(
                                    f"https://api.telegram.org/bot{_tg_tok_st}/sendMessage",
                                    data=_jst.dumps({"chat_id": _cid_st, "text": _msg_st,
                                                     "parse_mode": "HTML"}).encode(),
                                    headers={"Content-Type": "application/json"},
                                )
                                _urst.urlopen(_req_st, timeout=8)
                            except Exception:
                                pass
                except Exception:
                    pass

            try:
                asyncio.run(_run_cycle())
            except KeyboardInterrupt:
                print(f"\n{Y}  Остановлено.{RST}")

        else:
            # ── Обычный режим — интерактивное меню ─────────────────────────────────
            try:
                _init_secrets()
                _check_setup()       # проверка ключей — выходит если не заполнены
                _migrate_config()
                _startup_cleanup()
                _start_log_tee()     # дублируем вывод в automation.log
                # Отменяем «хвосты» GrizzlySMS до запуска монитора
                _grizzly_module.startup_cleanup_active_rentals("старт консоли")
                # Фоновый монитор GrizzlySMS — сканирует активные номера с первой секунды
                _grizzly_module.start_global_monitor()
                # Фоновый монитор GGSell — следит за новыми заказами
                try:
                    from ggsell.monitor import start_monitor as _ggsel_start
                    _gs = (_read_secrets().get("ggsel") or {})
                    _gs_key = _gs.get("api_key", "").strip()
                    _gs_sid = int(_gs.get("seller_id") or 0)
                    _ggsel_start(_gs_key, _gs_sid)
                except Exception as _e:
                    pass  # GGSell не обязателен
                # TG-бот запускается вместе с консолью
                _tg_started = ensure_tg_bot("console")
                # Фоновая проверка обновлений (один раз при старте)
                threading.Thread(target=_check_updates_bg, daemon=True, name="update-check").start()
                screen_install(auto=True)
                # Ждём первый ответ от Telegram API (макс 12 сек)
                for _ in range(24):
                    if _bot_module._tg_status != "starting":
                        break
                    time.sleep(0.5)
                _prompt_update_if_available()
                screen_main()
            except KeyboardInterrupt:
                cls()
                print(f"\n{C}  Выход.{RST}\n")
    finally:
        import signal as _sig
        try:
            _sig.signal(_sig.SIGINT, _sig.SIG_IGN)
        except Exception:
            pass
        # СНАЧАЛА гасим дочерний процесс автоматизации — иначе он продолжит
        # покупать номера уже после выхода из консоли.
        try:
            _kill_automation_proc()
        except Exception:
            pass
        try:
            import grizzly as _gz
            _gz.cleanup_all_rentals_on_exit()
        except (KeyboardInterrupt, Exception) as _e:
            if not isinstance(_e, KeyboardInterrupt):
                print(f"Ошибка при очистке номеров при выходе: {_e}")
        # Завершаемся жёстко через os._exit, минуя финализацию интерпретатора.
        # Иначе демон-потоки (Telegram-поллинг, aiohttp) продолжают писать в
        # stdout во время shutdown и роняют процесс фатальной ошибкой
        # _enter_buffered_busy (гонка за блокировку буфера вывода).
        for _stream in (sys.stdout, sys.stderr):
            try:
                _stream.flush()
            except Exception:
                pass
        os._exit(_exit_code[0])
