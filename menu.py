"""
Interactive console menu for Login Automation
Запуск: python menu.py  (или двойной клик menu.bat)
"""

import asyncio
import os
os.makedirs("debug", exist_ok=True)
import random
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
        _k32.SetConsoleTitleW("Flipkart Automation")
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

PROFILES_DIR        = Path("./chrome_profiles")
DONE_PROFILES_DIR   = Path("./chrome_profiles_done")
USED_PROFILES_DIR   = Path("./chrome_profiles_used")
BACKUP_PROFILES_DIR = Path("./chrome_profiles_backup")
_HERE               = Path(__file__).parent
_AUTOMATION_LOG     = _HERE / "automation.log"


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


def _send_tg_activation(phone: str, act_url: str, short_url: str = "",
                        valid_till: str = "", login_str: str = "",
                        issued_str: str = "") -> None:
    """Отправляет ссылку активации YouTube Premium в Telegram (синхронно)."""
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

# ── Heartbeat для серверного режима ───────────────────────────────────────────
# Пишет файл data/console_heartbeat.json каждые 30 сек, пока этот процесс жив.
# bot.py на сервере читает его чтобы понять — запущена ли консоль локально.
def _start_heartbeat():
    import threading as _th, time as _ti, json as _js
    _hb_file = _DATA / "console_heartbeat.json"

    def _beat():
        while True:
            try:
                _hb_file.write_text(_js.dumps({"ts": _ti.time(), "pid": os.getpid()}),
                                    encoding="utf-8")
            except Exception:
                pass
            _ti.sleep(30)

    t = _th.Thread(target=_beat, daemon=True, name="console-heartbeat")
    t.start()

_start_heartbeat()

# ── Git executable (Windows PATH может не включать git) ───────────────────────
def _find_git() -> str:
    found = shutil.which("git")
    if found:
        return found
    for _p in [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        r"D:\Git\cmd\git.exe",
        r"D:\Git\bin\git.exe",
        r"E:\Git\cmd\git.exe",
    ]:
        if Path(_p).exists():
            return _p
    return "git"

_GIT = _find_git()

# ── GitHub HTTP-обновление (работает без git, только stdlib) ──────────────────
_GH_OWNER = "crownfall90-dot"
_GH_REPO  = "flipkart-automation"

def _parse_git_remote() -> tuple[str, str, str]:
    """Читает .git/config → (owner, repo, token). Fallback: secrets.yaml → константы."""
    try:
        import re
        cfg = (Path(__file__).parent / ".git" / "config").read_text(encoding="utf-8", errors="replace")
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
            (Path(__file__).parent / "secrets.yaml").read_text(encoding="utf-8")
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
        _here = Path(__file__).parent
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
        _here = Path(__file__).parent
        _FILES = [
            "menu.py", "bot.py", "main.py", "menu.bat", "grizzly_sms.py",
            "proxy.py", "grizzly.py", "requirements.txt", ".gitignore",
            "config.yaml.example", "secrets.yaml.example", "secrets1.yaml.example",
            "ggsell/__init__.py", "ggsell/bot_ggsell.py", "ggsell/client.py", "ggsell/monitor.py"
        ]
        updated = []
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
                tgt.write_bytes(save)
                updated.append(fname)
            except Exception:
                pass
        # Обновляем локальный SHA чтобы следующий check показал 0 коммитов
        try:
            ref  = _j.loads(_gh_get(
                f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/master", token))
            sha  = ref["object"]["sha"]
            # Пишем в ._update_sha (ZIP-установки) и в .git если есть
            (_here / "._update_sha").write_text(sha)
            head = _here / ".git" / "refs" / "heads" / "master"
            if head.exists():
                head.write_text(sha + "\n")
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
import proxy as _proxy_module
from proxy import (
    _proxy_cfg_path, _read_proxy_cfg, _write_proxy_cfg, _load_proxy_list,
    _proxy_server_bare, _is_proxy_error, _mark_proxy_failed, _mark_proxy_ok,
    _phone_from_path, _pick_proxy, _lp_pipe, _lp_handle,
    _start_local_auth_proxy, _stop_local_auth_proxy,
    _p6_cfg, _p6_write_cfg, _p6_api, _p6_balance, _p6_buy, _p6_getlist,
    _p6_prolong, _p6_buy_affordable,
)

import grizzly as _grizzly_module
from grizzly import (
    _get_bg_loop, _submit_bg_cancel, _submit_bg_login,
    _bg_login_with_otp,
)

import bot as _bot_module
from bot import _tg_status_line, _menu_tg_bot_thread

# module-level fallback values (перезаписываются _check_updates_bg при проверке)
_update_available: bool = False
_update_commits:   list = []
_update_checked:   bool = False


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


def run(cmd: list[str]) -> int:
    """Запускает команду, выводит вывод в реальном времени. Возвращает exit code."""
    proc = subprocess.run(cmd)
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


def _console_offer_restore(profile_path, username: str) -> bool:
    """Консоль: при not_logged_in предлагает восстановить сессию из бэкапа куков.
    Возвращает True, если восстановление удалось."""
    phone = "".join(ch for ch in str(username) if ch.isdigit())[-10:] or str(username)
    bk = Path("cookies_backup") / f"cookies_{phone}.json"
    print(f"\n  {Y}🔒 Профиль не залогинен — вход слетел.{RST}")
    if not bk.exists():
        print(f"  {DIM}Бэкапа куков нет (cookies_backup/cookies_{phone}.json).{RST}")
        print(f"  {DIM}Нужны свежие куки — пункт «К» в главном меню.{RST}")
        return False
    try:
        ans = input(f"  {BLD}Восстановить сессию из сохранённых куков? [Д/Н]: {RST}").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return False
    if ans not in ("Д", "ДА", "Y", "YES"):
        return False
    print(f"  {DIM}Восстанавливаю сессию из куков…{RST}")
    try:
        ok, msg = asyncio.run(_restore_profile_from_cookies(bk, phone, Path(profile_path)))
    except Exception as e:
        ok, msg = False, str(e)
    if ok:
        print(f"  {G}✅ Сессия восстановлена. Запустите операцию снова.{RST}")
        return True
    print(f"  {R}❌ Куки не дали входа: {msg}. Нужны свежие куки (пункт «К»).{RST}")
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


def _load_done_profiles() -> list[dict]:
    """Возвращает список профилей из DONE_PROFILES_DIR у которых есть .profile_meta.json."""
    import time as _t
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
    return profiles


def _profile_url(profile_path: Path) -> str:
    """
    Возвращает URL для открытия профиля.
    Если профиль залогинен → главная страница Flipkart (не будет показан экран входа).
    Если сессии нет → страница входа из config.yaml.
    """
    meta_file = profile_path / ".profile_meta.json"
    if meta_file.exists():
        # Есть сохранённая сессия — открываем целевую страницу
        return "https://www.flipkart.com/flipkart-black-store"

    # Нет сессии — читаем URL входа из config.yaml
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


def open_chrome(profile_path: Path) -> bool:
    """Открывает Chrome с профилем и прокси-туннелем (если настроен)."""
    chrome = _find_chrome()
    if not chrome:
        print(f"\n{R}  Chrome не найден. Запустите вручную:{RST}")
        print(f"  chrome.exe --user-data-dir=\"{profile_path.resolve()}\"")
        return False
    url  = _profile_url(profile_path)
    args = [chrome, f"--user-data-dir={profile_path.resolve()}"]
    _ext = _vpn_extension_dir()   # VPN PLY → грузим расширение и в ручном Chrome
    if _ext:
        args.append(f"--load-extension={_ext}")
    proxy = _pick_proxy()
    if proxy:
        server = proxy.get("server", "")
        uname  = proxy.get("username", "")
        pwd    = proxy.get("password", "")
        bare   = _proxy_server_bare(server)
        if server and uname and pwd and ":" in bare:
            up_host, up_port_str = bare.rsplit(":", 1)
            try:
                import base64 as _b64, socket as _sock
                auth_b64  = _b64.b64encode(f"{uname}:{pwd}".encode()).decode()
                up_port   = int(up_port_str)
                ready_evt = threading.Event()
                def _tunnel_thread():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    async def _run():
                        srv = await asyncio.start_server(
                            lambda r, w: _lp_handle(r, w, up_host, up_port, auth_b64),
                            "127.0.0.1", local_port)
                        ready_evt.set()
                        async with srv:
                            await srv.serve_forever()
                    loop.run_until_complete(_run())
                with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as _s:
                    _s.bind(("127.0.0.1", 0))
                    local_port = _s.getsockname()[1]
                threading.Thread(target=_tunnel_thread, daemon=True, name="chrome-proxy").start()
                ready_evt.wait(timeout=2.0)
                args.append(f"--proxy-server=http://127.0.0.1:{local_port}")
                print(f"  {DIM}Прокси: {bare} (авто-авторизация){RST}")
            except Exception as _pe:
                print(f"  {Y}⚠ Прокси-туннель не запустился: {_pe} — Chrome без прокси{RST}")
        elif server:
            args.append(f"--proxy-server={server}")
            print(f"  {DIM}Прокси: {bare}{RST}")
    args.append(url)
    subprocess.Popen(args)
    return True


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


def _vpn_extension_dir() -> str | None:
    """Путь к распакованному VPN-расширению для загрузки в Chrome.
    Ищет папку с manifest.json: vpn_extension/ в корне (или её единственную
    подпапку с manifest.json — как распаковывается .crx). Возвращает абсолютный
    путь строкой или None, если расширения нет."""
    try:
        base = Path(__file__).parent / "vpn_extension"
        if not base.exists():
            return None
        if (base / "manifest.json").exists():
            return str(base.resolve())
        # .crx часто распаковывается в подпапку (например vpn_extension/<id>/manifest.json)
        for sub in base.iterdir():
            if sub.is_dir() and (sub / "manifest.json").exists():
                return str(sub.resolve())
    except Exception:
        pass
    return None


async def _vpn_ext_id(context) -> str | None:
    """ID загруженного VPN-расширения (из service worker / background page)."""
    try:
        for sw in context.service_workers:
            if sw.url.startswith("chrome-extension://"):
                return sw.url.split("/")[2]
    except Exception:
        pass
    try:
        for bp in context.background_pages:
            if bp.url.startswith("chrome-extension://"):
                return bp.url.split("/")[2]
    except Exception:
        pass
    try:
        sw = await context.wait_for_event("serviceworker", timeout=8_000)
        return sw.url.split("/")[2]
    except Exception:
        return None


async def _ensure_vpn_connected(context) -> bool:
    """VPNLY: открывает попап и жмёт «Подключить»/«Connect» (страну не меняем —
    берётся оптимальная по умолчанию, напр. Germany). Ждёт статус «Защищено».
    Возврат True если подключились. Тихо выходит, если расширения нет."""
    if not _vpn_extension_dir():
        return False
    eid = await _vpn_ext_id(context)
    if not eid:
        print(f"  {Y}⚠ VPN: не удалось определить ID расширения — пропускаю{RST}")
        return False

    _CONNECTED = ("защищено", "отключить", "protected", "disconnect", "connected")
    _NOTCONN   = ("не защищено", "не подключено", "подключить", "not protected",
                  "not connected", "disconnected")

    async def _status(pop) -> str:
        try:
            b = (await pop.evaluate("() => document.body ? document.body.innerText : ''")).lower()
        except Exception:
            return ""
        # «отключить»/«disconnect» = уже подключены (кнопка отключения)
        if any(s in b for s in ("защищено", "отключить", "disconnect")) and "не защищено" not in b:
            return "connected"
        if any(s in b for s in _NOTCONN):
            return "disconnected"
        if "connected" in b and "disconnected" not in b and "not connected" not in b:
            return "connected"
        return "unknown"

    async def _click_connect(pop) -> bool:
        # Точный текст кнопки «Подключить»/«Connect»
        for _t in ("Подключить", "Connect", "CONNECT", "Подключиться", "Turn on"):
            try:
                _loc = pop.get_by_text(_t, exact=True).first
                if await _loc.count() > 0 and await _loc.is_visible():
                    await _loc.click(timeout=1_500)
                    return True
            except Exception:
                pass
        # Fallback: крупная нижняя кнопка (оранжевая) по координатам через JS
        try:
            _bb = await pop.evaluate(r"""() => {
                const want = ['подключить','connect','подключиться','turn on'];
                let best=null;
                for (const el of document.querySelectorAll('button,a,div,span,[role="button"]')) {
                    const t=(el.innerText||el.textContent||'').trim().toLowerCase();
                    if (!want.some(w=>t===w || (t.includes(w)&&t.length<20))) continue;
                    const r=el.getBoundingClientRect();
                    if (r.width<60||r.height<20||el.offsetParent===null) continue;
                    if (!best || r.top>best.top) best={x:r.x+r.width/2,y:r.y+r.height/2,top:r.top};
                }
                return best;
            }""")
            if _bb:
                await pop.mouse.click(_bb["x"], _bb["y"])
                return True
        except Exception:
            pass
        return False

    pop = None
    try:
        pop = await context.new_page()
        await pop.goto(f"chrome-extension://{eid}/popup.html",
                       wait_until="domcontentloaded", timeout=15_000)
        await pop.wait_for_timeout(2_500)

        if await _status(pop) == "connected":
            print(f"  {G}✔ VPN уже подключён{RST}")
            return True

        # Жмём «Подключить» (до 3 попыток) и ждём статус «Защищено» (VPN стартует не мгновенно)
        for _try in range(3):
            await _click_connect(pop)
            for _ in range(20):   # до ~20 сек ждём подключения
                await pop.wait_for_timeout(1_000)
                if await _status(pop) == "connected":
                    print(f"  {G}✔ VPN подключён{RST}")
                    return True
            print(f"  {Y}VPN ещё не подключился — повтор ({_try + 1}/3)…{RST}")

        print(f"  {Y}⚠ VPN: не удалось подтвердить подключение (проверьте вручную){RST}")
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


def _browser_launch_kw(headless: bool = False, use_proxy: bool = True,
                       force_proxy: bool = False, phone: str = "",
                       skip_servers: set | None = None,
                       forced_proxy_dict: dict | None = None,
                       use_bundled_chromium: bool = False,
                       local_proxy_port: int | None = None) -> dict:
    """Возвращает kwargs для launch_persistent_context.
    local_proxy_port — порт локального туннеля (без диалога авторизации Chrome).
    forced_proxy_dict — конкретный прокси dict (минует _pick_proxy)."""
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
    # ── VPN-расширение (VPNLY): грузим в КАЖДЫЙ профиль, если папка есть ──
    # Кладём распакованное расширение (с manifest.json) в vpn_extension/ в корне
    # проекта — тогда оно ставится и включается для каждого запуска Chrome.
    # ВАЖНО: расширения не работают в старом headless — форсируем видимый режим
    # либо новый headless (--headless=new), когда расширение загружается.
    try:
        _ext_dir = _vpn_extension_dir()
        if _ext_dir:
            args.append(f"--disable-extensions-except={_ext_dir}")
            args.append(f"--load-extension={_ext_dir}")
            if headless:
                # старый headless не поддерживает расширения → включаем новый
                args.append("--headless=new")
                headless = False  # Playwright запустит окно, но с --headless=new оно скрыто
    except Exception:
        pass

    if not headless:
        args.append("--start-maximized")
        kw["no_viewport"] = True
    else:
        kw["viewport"] = vp
    kw["headless"] = headless
    chrome = _find_chrome()
    if chrome and not use_bundled_chromium:
        kw["executable_path"] = chrome
    if local_proxy_port is not None:
        kw["proxy"] = {"server": f"http://127.0.0.1:{local_proxy_port}"}
        print(f"  {DIM}Прокси: туннель 127.0.0.1:{local_proxy_port}{RST}")
    elif forced_proxy_dict is not None:
        server   = forced_proxy_dict.get("server", "")
        username = forced_proxy_dict.get("username", "")
        password = forced_proxy_dict.get("password", "")
        if username and password:
            kw["proxy"] = {"server": server, "username": username, "password": password}
        else:
            kw["proxy"] = {"server": server}
        print(f"  {DIM}Прокси (оплата): {_proxy_server_bare(server)}{RST}")
    elif use_proxy or force_proxy:
        _proxy_enabled = bool((_read_proxy_cfg() or {}).get("enabled"))
        if _proxy_enabled:
            proxy = _pick_proxy(force=force_proxy, phone=phone, skip_servers=skip_servers)
            if proxy:
                server   = proxy.get("server", "")
                username = proxy.get("username", "")
                password = proxy.get("password", "")
                if username and password:
                    kw["proxy"] = {"server": server, "username": username, "password": password}
                else:
                    kw["proxy"] = {"server": server}
                print(f"  {DIM}Прокси: {server}{RST}")
            else:
                print(f"  {Y}⚠ proxy.enabled=true но список прокси пуст{RST}")
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



def screen_proxy():
    """Управление прокси: список, добавление, удаление, вкл/выкл."""
    while True:
        cls()
        header("ПРОКСИ", Y)
        pcfg   = _read_proxy_cfg()
        p6cfg  = _p6_cfg()
        enabled = bool(pcfg.get("enabled"))
        proxies_raw = pcfg.get("list") or []
        single = pcfg.get("server", "")
        p6key  = p6cfg.get("api_key", "").strip()

        # Собираем единый список для отображения
        display: list[dict] = []
        if proxies_raw:
            display = [p for p in proxies_raw if p and p.get("server")]
        elif single:
            display = [{"server": single,
                        "username": pcfg.get("username", ""),
                        "password": pcfg.get("password", "")}]

        status_str = f"{G}ВКЛ{RST}" if enabled else f"{R}ВЫКЛ{RST}"
        print(f"  Статус прокси: {status_str}  ({len(display)} шт.)")
        # Прокси принудительно отключены глобально (proxy.py:_read_proxy_cfg).
        # Честно предупреждаем, чтобы переключатель «вкл» не вводил в заблуждение.
        print(f"  {R}{BLD}⛔ ПРОКСИ ВРЕМЕННО ОТКЛЮЧЕНЫ ГЛОБАЛЬНО{RST}")
        print(f"  {DIM}   Настройки сохраняются, но к браузеру НЕ применяются "
              f"(включён глобальный обход).{RST}")
        print(f"  {Y}⚠  Flipkart требует индийский IP — используйте прокси Индия{RST}\n")

        if display:
            for i, p in enumerate(display, 1):
                u   = p.get("username", "")
                exp = p.get("expires", "")
                exp_str = f"  {DIM}до {exp[:10]}{RST}" if exp else ""
                ctry = p.get("country", "")
                ctry_str = f" [{ctry}]" if ctry else ""
                auth = f"  {DIM}({u}){RST}" if u else ""
                print(f"  [{i}] {p['server']}{ctry_str}{auth}{exp_str}")
        else:
            print(f"  {DIM}  Список пуст{RST}")

        # ── Proxy6.net блок ──────────────────────────────────────────────────
        print()
        print(f"  {C}{BLD}── Proxy6.net ────────────────────────────────{RST}")
        if p6key:
            key_hint = p6key[:6] + "…"
            p6_count  = p6cfg.get("default_count",  10)
            p6_period = p6cfg.get("default_period",  7)
            p6_country = p6cfg.get("country", "in").upper()
            print(f"  API ключ : {G}{key_hint}{RST}  "
                  f"По умолч.: {C}{p6_count} шт.{RST} · "
                  f"{C}{p6_period} дн.{RST} · "
                  f"{C}{p6_country}{RST}")
            print(f"  {G}[А]{RST}  Автопокупка индийских прокси ({p6_count} шт. · {p6_period} дн.)")
            print(f"  {C}[С]{RST}  Синхронизировать активные из Proxy6 → заменить список")
            print(f"  {B}[Б]{RST}  Показать баланс Proxy6")
            print(f"  {Y}[Н]{RST}  Настроить количество / период / ключ")
        else:
            print(f"  {R}  API ключ не настроен{RST}")
            print(f"  {Y}[Н]{RST}  Ввести API ключ Proxy6.net")

        # ── Ручное управление ────────────────────────────────────────────────
        print()
        print(f"  {C}── Ручное ──────────────────────────────────────{RST}")
        print(f"  {G}[Д]{RST}  Добавить прокси вручную (ip:port:user:pass)")
        print(f"  {R}[У]{RST}  Удалить прокси (номер)")
        toggle_lbl = f"{R}Выключить{RST}" if enabled else f"{G}Включить{RST}"
        print(f"  {Y}[В]{RST}  {toggle_lbl} прокси")
        print(f"  {DIM}[Q]{RST}  Назад")
        print()

        try:
            act = input(f"  {BLD}Действие: {RST}").strip().upper()
        except KeyboardInterrupt:
            return

        if act in ("Q", ""):
            return

        # ── Proxy6 автопокупка ───────────────────────────────────────────────
        elif act in ("А", "A") and p6key:
            p6_count  = p6cfg.get("default_count",  10)
            p6_period = p6cfg.get("default_period",  7)
            p6_country = p6cfg.get("country", "in")
            p6_type    = p6cfg.get("type", "http")
            print(f"\n  {C}Покупаю {p6_count} прокси ({p6_country.upper()}, "
                  f"{p6_period} дн., {p6_type})...{RST}")
            try:
                new_proxies, buy_msg = _p6_buy_affordable(
                    p6key, p6_count, p6_period, country=p6_country, proxy_type=p6_type)
                if not new_proxies:
                    print(f"  {R}Proxy6 вернул пустой список — проверьте баланс и доступность{RST}")
                    time.sleep(3)
                    continue
                # После покупки сразу синхронизируем — берём полный актуальный список
                print(f"  {DIM}Синхронизирую актуальный список...{RST}")
                try:
                    active = _p6_getlist(p6key, state="active")
                except Exception:
                    active = new_proxies  # fallback: хотя бы только что купленные
                pcfg_new = dict(pcfg)
                pcfg_new["list"] = active
                pcfg_new.pop("server", None)
                pcfg_new.pop("username", None)
                pcfg_new.pop("password", None)
                pcfg_new["enabled"] = True
                _write_proxy_cfg(pcfg_new)
                exp0 = new_proxies[0].get("expires", "")[:10] if new_proxies else ""
                print(f"\n  {G}✅ {buy_msg}{RST}" + (f"  (до {exp0})" if exp0 else ""))
                print(f"  {DIM}В списке {len(active)} активных прокси{RST}")
            except Exception as exc:
                print(f"\n  {R}❌ Ошибка Proxy6: {exc}{RST}")
            time.sleep(3)

        # ── Proxy6 синхронизация ─────────────────────────────────────────────
        elif act in ("С", "C") and p6key:
            print(f"\n  {C}Загружаю активные прокси из Proxy6...{RST}")
            try:
                active = _p6_getlist(p6key, state="active")
                if not active:
                    print(f"  {Y}Активных прокси в аккаунте нет{RST}")
                    time.sleep(3)
                    continue
                pcfg_new = dict(pcfg)
                pcfg_new["list"] = active
                pcfg_new.pop("server", None)
                pcfg_new.pop("username", None)
                pcfg_new.pop("password", None)
                pcfg_new["enabled"] = True
                _write_proxy_cfg(pcfg_new)
                print(f"\n  {G}✅ Синхронизировано: {len(active)} прокси{RST}")
                for ap in active:
                    exp = ap.get("expires", "")[:10]
                    print(f"     {DIM}{ap['server']}  до {exp}{RST}")
            except Exception as exc:
                print(f"\n  {R}❌ Ошибка: {exc}{RST}")
            time.sleep(3)

        # ── Proxy6 баланс ────────────────────────────────────────────────────
        elif act in ("Б", "B", "Z") and p6key:
            print(f"\n  {C}Запрашиваю баланс Proxy6...{RST}")
            try:
                bal, cur = _p6_balance(p6key)
                print(f"\n  {G}💰 Баланс Proxy6: {BLD}{bal} {cur}{RST}")
            except Exception as exc:
                print(f"\n  {R}❌ Ошибка: {exc}{RST}")
            time.sleep(3)

        # ── Настройки Proxy6 ─────────────────────────────────────────────────
        elif act in ("Н", "N"):
            cls()
            header("НАСТРОЙКИ PROXY6", C)
            p6new = dict(p6cfg)
            print(f"  Текущий API ключ: {p6key[:8] + '…' if p6key else R + 'не задан' + RST}")
            print(f"  Количество по умолч.: {p6cfg.get('default_count', 10)}")
            print(f"  Период (дней):        {p6cfg.get('default_period', 7)}")
            print(f"  Страна:               {p6cfg.get('country', 'in').upper()}")
            print(f"  Тип:                  {p6cfg.get('type', 'http')}")
            print()
            try:
                v = input(f"  {BLD}API ключ (Enter = не менять): {RST}").strip()
                _new_p6_key = v if v else None
                v = input(f"  {BLD}Кол-во прокси [{p6cfg.get('default_count',10)}]: {RST}").strip()
                if v.isdigit() and int(v) > 0: p6new["default_count"] = int(v)
                v = input(f"  {BLD}Период дней   [{p6cfg.get('default_period',7)}]: {RST}").strip()
                if v.isdigit() and int(v) > 0: p6new["default_period"] = int(v)
                v = input(f"  {BLD}Страна        [{p6cfg.get('country','in')}]: {RST}").strip()
                if v: p6new["country"] = v.lower()
                v = input(f"  {BLD}Тип (http/socks5) [{p6cfg.get('type','http')}]: {RST}").strip()
                if v in ("http", "https", "socks5"): p6new["type"] = v
            except KeyboardInterrupt:
                continue
            p6new.pop("api_key", None)   # api_key не пишем в config.yaml
            _p6_write_cfg(p6new)
            if _new_p6_key:
                _write_secret("proxy6", "api_key", _new_p6_key)
            print(f"\n  {G}✅ Сохранено{RST}")
            time.sleep(1.5)

        # ── Добавить вручную ─────────────────────────────────────────────────
        elif act in ("Д", "D"):
            print(f"\n  Формат: ip:port:user:pass  или  ip:port")
            try:
                raw = input(f"  {BLD}Прокси: {RST}").strip()
            except KeyboardInterrupt:
                continue
            if not raw:
                continue
            parts = raw.split(":")
            if len(parts) >= 4:
                host, port, user, pwd = parts[0], parts[1], parts[2], ":".join(parts[3:])
            elif len(parts) == 2:
                host, port, user, pwd = parts[0], parts[1], "", ""
            else:
                print(f"  {R}Неверный формат{RST}")
                time.sleep(3)
                continue
            server = f"http://{host}:{port}"
            new_entry: dict = {"server": server}
            if user: new_entry["username"] = user
            if pwd:  new_entry["password"] = pwd
            new_list = display + [new_entry]
            pcfg_new = dict(pcfg)
            pcfg_new["list"] = new_list
            pcfg_new.pop("server", None)
            pcfg_new.pop("username", None)
            pcfg_new.pop("password", None)
            pcfg_new["enabled"] = True
            _write_proxy_cfg(pcfg_new)
            print(f"  {G}✅ Добавлено: {server}{RST}")
            time.sleep(1.2)

        elif act == "У":
            if not display:
                print(f"  {R}Список пуст{RST}")
                time.sleep(3)
                continue
            try:
                num = int(input(f"  {BLD}Номер для удаления [1-{len(display)}]: {RST}").strip())
            except (ValueError, KeyboardInterrupt):
                continue
            if not 1 <= num <= len(display):
                print(f"  {R}Неверный номер{RST}")
                time.sleep(3)
                continue
            removed = display.pop(num - 1)
            pcfg_new = dict(pcfg)
            pcfg_new["list"] = display
            pcfg_new.pop("server", None)
            pcfg_new.pop("username", None)
            pcfg_new.pop("password", None)
            _write_proxy_cfg(pcfg_new)
            print(f"  {R}Удалено: {removed['server']}{RST}")
            time.sleep(1.2)

        elif act in ("В", "V"):
            pcfg_new = dict(pcfg)
            pcfg_new["enabled"] = not enabled
            _write_proxy_cfg(pcfg_new)
            # Сбрасываем кэш — при следующем обращении читаем свежие настройки
            _proxy_module._proxy_cache_loaded = False
            _proxy_module._proxy_list_cache   = None
            lbl = f"{G}включён{RST}" if not enabled else f"{R}выключен{RST}"
            print(f"  Прокси {lbl}")
            time.sleep(1)


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
            args = [sys.executable, str(Path(__file__).parent / "main.py")]
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
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(args, creationflags=creationflags)
        while proc.poll() is None:
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
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
        return True
    except Exception as exc:
        print(f"\n  {R}Ошибка записи метаданных: {exc}{RST}")
        return False


def _kill_chrome_for_profile(profile_path) -> int:
    """Завершает Chrome-процессы, использующие указанную папку профиля."""
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
        # psutil недоступен — убиваем через PowerShell по имени папки
        try:
            ps_cmd = (
                f"Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" | "
                f"Where-Object {{$_.CommandLine -like '*{folder_name}*'}} | "
                f"ForEach-Object {{Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}}"
            )
            subprocess.run(["powershell", "-Command", ps_cmd],
                           capture_output=True, timeout=8)
            killed = -1  # неизвестно сколько
        except Exception:
            pass
    return killed


def _find_chrome_pids_for_profile(profile_path: Path) -> list:
    """Возвращает PID-ы Chrome-процессов, запущенных с данным user-data-dir."""
    import subprocess as _sp
    profile_str = str(profile_path.resolve()).lower()
    pids: list = []
    try:
        r = _sp.run(
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
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile_path)
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            **_browser_launch_kw(headless=headless, force_proxy=True, phone=username))
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        if not headless:
            await _maximize_window(ctx, page)
        await _ensure_vpn_connected(ctx)   # VPN PLY → USA (если расширение есть)

        try:
            await page.goto("https://www.flipkart.com/flipkart-black-store",
                            wait_until="domcontentloaded", timeout=12_000)
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

        # Проверка блокировки по IP (Akamai Access Denied — нужен индийский прокси)
        try:
            _page_title = await page.title()
            _page_text  = await page.evaluate("() => document.body?.innerText || ''")
            if "access denied" in _page_text.lower() or "access denied" in _page_title.lower():
                result["status"] = "access_denied"
                result["error"]  = "Access Denied — нужен индийский IP"
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
        _proxy_enabled = False
        try:
            _proxy_enabled = bool((_read_proxy_cfg() or {}).get("enabled"))
        except Exception:
            pass
        if _proxy_enabled and _is_proxy_error(e):
            _mark_proxy_failed(_proxy_module._last_proxy_server)
            result["error"] = ("PROXY_DEAD: Прокси недоступен — обновите список "
                               "(Прокси → [А] или [С])")
        return result
    finally:
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


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
        print()
        opt("0", "Назад", R)
        print()

        choice = input(f"  {BLD}Выберите профиль [1-{len(profiles)}], А, 9 или 0: {RST}").strip().upper()
        if choice == "0" or choice == "":
            return

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
                    elif msg6.startswith("PROXY_DEAD:"):
                        print(f"\n  {R}❌ {msg6[11:].strip()}{RST}")
                        print(f"  {Y}Перейдите в меню Прокси и нажмите [А] для покупки новых.{RST}")
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
                           _skip_proxies: set | None = None,
                           _retry_n: int = 0,
                           _use_proxy: bool | None = None,
                           stop_at_payment: bool = False) -> tuple[bool, str]:
    """Открывает профиль, проверяет вход и заполняет форму адреса через Buy Now."""
    if _retry_n == 0:
        print(f"  {DIM}Проверка доступности Flipkart...{RST}")
        if not await _check_flipkart_accessible():
            _ping_msg = "Flipkart недоступен"
            print(f"\n  {R}⚠ {_ping_msg}{RST}")
            print(f"  {Y}Покупка Membership невозможна. Повторите позже.{RST}")
            return False, _ping_msg
        print(f"  {G}Flipkart доступен.{RST}")

    if _use_proxy is None:
        _use_proxy = bool((_read_proxy_cfg() or {}).get("enabled", False))
    _MAX_PROXY_RETRIES = 3
    if _skip_proxies is None:
        _skip_proxies = set()
    if _is_profile_locked(profile_path):
        print(f"  {Y}Профиль занят — закрываю Chrome и очищаю локи...{RST}")
        _clear_stale_profile_locks(profile_path)
    elif _retry_n > 0:
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
            **_browser_launch_kw(use_proxy=_use_proxy, force_proxy=_use_proxy,
                                  phone=_phone_from_path(profile_path),
                                  skip_servers=_skip_proxies))
        _stealth2 = _build_stealth_js_m()
        if _stealth2:
            await ctx.add_init_script(_stealth2)
        await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await _maximize_window(ctx, page)
        await _ensure_vpn_connected(ctx)   # VPN PLY → USA (если расширение есть)

        # Начальная навигация с одним ретраем — бывает разовый таймаут загрузки
        _nav_ok = False
        for _nav_try in range(2):
            try:
                await page.goto("https://www.flipkart.com",
                                wait_until="domcontentloaded", timeout=25_000)
                _nav_ok = True
                break
            except Exception as _nav_e:
                if _nav_try == 0:
                    print(f"  {Y}⚠ Главная не загрузилась ({_nav_e}) — повтор...{RST}")
                    await page.wait_for_timeout(2_000)
                else:
                    return False, f"Не удалось открыть Flipkart (таймаут навигации): {_nav_e}"
        # Повторяем grant ПОСЛЕ навигации — в persistent context важен порядок
        await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
        await page.wait_for_timeout(2_000)

        # Проверяем — нет ли уже купленного Black Membership
        _phone_label = _phone_from_path(profile_path)
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
                # Возвращаемся на главную для Buy Now
                await page.goto("https://www.flipkart.com",
                                wait_until="domcontentloaded", timeout=15_000)
                await page.wait_for_timeout(1_000)

        # Buy Now создаёт реальную сессию чекаута (прямой URL формы не работает)
        err = await _click_buy_now(page, _BLACK_URLS[3])
        if err:
            return False, err

        # После нажатия Buy Now браузер всегда оставляем открытым
        _keep_open = True

        async def _fill_addr_and_wait():
            """Заполняет форму адреса и ждёт viewcheckout."""
            lat, lon = _CITY_COORDS.get(addr.get("city", ""), (20.5937, 78.9629))
            await ctx.set_geolocation({"latitude": lat, "longitude": lon})
            await _maximize_window(ctx, page)
            if not await _fill_address_form(page, addr):
                return False
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
                "button:has-text('Place order'), button:has-text('Place Order'), "
                "button:has-text('PLACE ORDER')"
            ).last
            if await cont.count() > 0:
                await _human_click(page, cont, before=_r.uniform(0.1, 0.25))
                await page.wait_for_timeout(900)

        # ── Шаг A: если сразу попали на форму адреса ────────────────────────
        if "changeShippingAddress" in page.url or "add/form" in page.url:
            print(f"  Заполняю форму адреса...")
            if not await _fill_addr_and_wait():
                return False, "Кнопка Save Address не найдена в форме адреса"

        # ── Шаг B: viewcheckout → email → Continue → payments ───────────────
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
                print(f"  {Y}⚠ OOS — пробую сменить адрес...{RST}")
                if not await _oos_try_new_addr():
                    return await _oos_delete_return(retry_done=True)
                print(f"  {G}✔ Новый адрес принят, OOS исчез{RST}")

            reached = await _viewcheckout_to_payments(page)
            if reached == "OUT_OF_STOCK":
                print(f"  {Y}⚠ OOS после Continue — пробую сменить адрес...{RST}")
                if not await _oos_try_new_addr():
                    return await _oos_delete_return(retry_done=True)
                print(f"  {G}✔ Новый адрес принят, OOS исчез{RST}")
                reached = await _viewcheckout_to_payments(page)
                if reached == "OUT_OF_STOCK":
                    return await _oos_delete_return(retry_done=True)

            if not reached and "address-map" in page.url:
                # Set Location привёл на карту, но навигация назад не завершилась —
                # ждём ещё и пробуем нажать Confirm + go_back
                print(f"  Всё ещё на address-map — жду возврата...")
                try:
                    await page.wait_for_url("**/viewcheckout**", timeout=10_000)
                    reached = await _viewcheckout_to_payments(page)
                except Exception:
                    if "address-map" in page.url:
                        print(f"  address-map: нажимаю Back...")
                        await page.go_back()
                        await page.wait_for_timeout(3_000)
                        reached = await _viewcheckout_to_payments(page)
            if reached == "OUT_OF_STOCK":
                return await _oos_delete_return(retry_done=True)

            if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url) \
                    and "address-map" not in page.url:
                print(f"  Flipkart запросил адрес — заполняю...")
                if not await _fill_addr_and_wait():
                    return False, "Кнопка Save Address не найдена (после Continue)"
                reached = await _viewcheckout_to_payments(page)
            if reached == "OUT_OF_STOCK":
                return await _oos_delete_return(retry_done=True)
            if reached == "CAPTCHA":
                _keep_open = False
                return False, "Капча Flipkart зависла (Are you a human?) — не удалось пройти даже после обновлений. Попробуйте запустить ещё раз позже."

        # ── Шаг C: проверяем payments ────────────────────────────────────────
        if "payments" not in page.url:
            # Провал — закрываем браузер (иначе брошенные окна убьются жёстко → EPIPE)
            _keep_open = False
            _cur_c = page.url.split("?")[0].rstrip("/")
            if _cur_c in ("https://www.flipkart.com", "https://flipkart.com", "https://m.flipkart.com"):
                if await _page_logged_out(page):
                    return False, _NOT_LOGGED_IN_MSG
                return False, ("Оформление сбросило на главную Flipkart — сессия слетела "
                               "или сработала бот-защита. Повторите позже / восстановите вход.")
            if await _page_logged_out(page):
                return False, _NOT_LOGGED_IN_MSG
            return False, f"Не удалось перейти на оплату (URL: {_cur_c})"

        if stop_at_payment:
            import time as _t_sap
            _save_meta_field(
                profile_path,
                prepared_ts=_t_sap.time(),
                address_pincode=addr.get("pincode", ""),
                address_city=addr.get("city", ""),
                status="email_completed",
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
        await _do_payments_page(page)
        try:
            _save_meta_field(
                profile_path,
                address_pincode=addr.get("pincode", ""),
                address_city=addr.get("city", ""),
                status="email_completed"
            )
        except Exception:
            pass
        try:
            await _handle_post_payment(page, ctx, profile_path, phone_number=_pp_phone)
        except Exception as _pp_e:
            print(f"  Post-payment: {_pp_e}")
        # Пост-пеймент завершён — закрываем браузер
        try:
            await ctx.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass
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
        if _use_proxy and _is_proxy_error(exc) and not _keep_open and _retry_n < _MAX_PROXY_RETRIES:
            svr = _proxy_module._last_proxy_server
            if svr:
                _mark_proxy_failed(svr)
                _skip_proxies.add(_proxy_server_bare(svr))
            print(f"  {Y}⚠ Прокси недоступен — пробую следующий "
                  f"({_retry_n + 1}/{_MAX_PROXY_RETRIES})...{RST}")
            return await _do_fill_address(profile_path, addr, _skip_proxies, _retry_n + 1, _use_proxy, stop_at_payment)
        if _use_proxy and _is_proxy_error(exc) and not _keep_open:
            print(f"  {Y}⚠ Все прокси недоступны — пробую без прокси...{RST}")
            return await _do_fill_address(profile_path, addr, set(), 1, _use_proxy=False, stop_at_payment=stop_at_payment)
        # На ошибке закрываем браузер (не оставляем висеть → иначе жёсткое убийство даёт EPIPE)
        _keep_open = False
        return False, msg
    finally:
        # Закрываем контекст и драйвер аккуратно (graceful), чтобы Node-драйвер
        # Playwright не падал с EPIPE от убитого извне браузера.
        if not _keep_open:
            if ctx:
                try: await ctx.close()
                except Exception: pass
            try: await pw.stop()
            except Exception: pass


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
    """Заполняет форму адреса на текущей странице. Возвращает True если Save Address нажата."""
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
            for (const label of document.querySelectorAll('label')) {
                if (!label.textContent.toLowerCase().includes(h)) continue;
                if (label.htmlFor) {
                    const inp = document.getElementById(label.htmlFor);
                    if (inp) return activate(inp);
                }
                let root = label.parentElement;
                for (let i = 0; i < 5 && root; i++, root = root.parentElement) {
                    const inp = root.querySelector('input:not([type=hidden])');
                    if (inp) return activate(inp);
                }
                label.click();
                return true;
            }
            for (const inp of document.querySelectorAll('input')) {
                if ((inp.placeholder || '').toLowerCase().includes(h))
                    return activate(inp);
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
        await page.wait_for_selector("input", state="visible", timeout=10_000)
    except Exception:
        pass
    await page.wait_for_timeout(800)

    # НЕ кликаем «Use my location» — это вызывает «Request timed out».
    # Заполняем всё вручную.

    import random as _r
    await _fill("Full Name", addr["name"])
    await page.wait_for_timeout(300)

    # Пинкод → Flipkart должен автоподставить State/City
    await _fill("Pincode", addr["pincode"], delay=80)
    await page.wait_for_timeout(4_000)   # ждём автозаполнение State/City по пинкоду

    # Проверяем, заполнился ли State автоматически
    state_filled = await page.evaluate("""() => {
        for (const inp of document.querySelectorAll('input')) {
            const ph = (inp.placeholder || inp.getAttribute('aria-label') || '').toLowerCase();
            if (ph.includes('state')) return (inp.value || '').trim().length > 0;
        }
        return false;
    }""")
    if not state_filled:
        # Заполняем State и City вручную из addr
        await _fill("State", addr["state"])
        await page.wait_for_timeout(300)
        await _fill("City", addr["city"])
        await page.wait_for_timeout(300)

    await _fill("House No", addr["house"])
    await page.wait_for_timeout(150)
    await _fill("Road name", addr["road"])
    await page.wait_for_timeout(150)

    # Очищаем поле "Alternate phone number" — Flipkart предзаполняет его
    # номером аккаунта с +91 (например, +917204960944), что вызывает ошибку
    # валидации «Enter a valid 10-digit mobile number» и блокирует сохранение.
    # Поле необязательное, поэтому просто стираем его содержимое.
    try:
        await page.evaluate("""() => {
            for (const inp of document.querySelectorAll('input[type=tel], input[type=number], input[type=text]')) {
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

    # Тип адреса (Home/Work radio)
    try:
        radio = page.locator("input[type='radio']").first
        if await radio.count() > 0:
            await _human_click(page, radio, before=_r.uniform(0.05, 0.15))
            await page.wait_for_timeout(200)
    except Exception:
        pass

    save_loc = page.get_by_text("Save Address", exact=True).first
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
    try:
        await page.wait_for_selector(_BUY_CSS, state="visible", timeout=5_000)
    except Exception:
        pass

    import random as _rbn
    await asyncio.sleep(_rbn.uniform(3.0, 5.0))

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

    clicked = (
        # 1) CSS has-text на button / a / role=button
        await _try_click(page.locator(_BUY_CSS))
        or
        # 2) Playwright get_by_role — находит по aria-name или видимому тексту
        await _try_click(page.get_by_role("button", name=_re.compile(r"buy\s*now", _re.I)))
        or
        await _try_click(page.get_by_role("link",   name=_re.compile(r"buy\s*now", _re.I)))
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
    clicked2 = (
        await _try_click(page.locator(_BUY_CSS))
        or await _try_click(page.get_by_role("button", name=_re.compile(r"buy\s*now", _re.I)))
    )
    if clicked2:
        try:
            await page.wait_for_function(_CHECKOUT_DOM, timeout=15_000)
            await page.wait_for_timeout(500)
        except Exception:
            pass
        if await _on_checkout():
            return None

    # До checkout не дошли — диагностируем причину по итоговой странице
    if await _page_logged_out(page):
        return _NOT_LOGGED_IN_MSG
    _cur = page.url.split("?")[0].rstrip("/")
    if _cur in ("https://www.flipkart.com", "https://flipkart.com", "https://m.flipkart.com"):
        return ("Buy Now вернул на главную Flipkart — товар недоступен по этой "
                "ссылке, сессия слетела или сработала бот-защита")
    return f"Клик по 'Buy now' не дал перехода на оплату (страница: {_cur[:60]})"


_OOS_PHRASES = frozenset({
    "currently out of stock", "out of stock for",
    "not deliverable", "item is not deliverable",
})


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

# Реестр профилей, по которым ПРЯМО СЕЙЧАС идёт покупка/заполнение (path → refcount).
# Нужен чтобы кнопка «Остановить» могла мгновенно убить Chrome активной операции:
# кооперативный флаг не прерывает долгие await Playwright (ожидание 3DS/OTP/навигации),
# а убийство браузера роняет их сразу. Заполняется декоратором _serialize_purchase.
_active_purchase_profiles: dict = {}
_app_lock = _threading_pc.Lock()

def _register_purchase_profile(pp) -> None:
    if pp is None:
        return
    k = str(pp)
    with _app_lock:
        _active_purchase_profiles[k] = _active_purchase_profiles.get(k, 0) + 1

def _unregister_purchase_profile(pp) -> None:
    if pp is None:
        return
    k = str(pp)
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
                    print(f"  {G}✅ OTP из Telegram: {_3ds_otp} — ввожу автоматически{RST}")
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
        (Path(__file__).parent / "data" / "tg_otp_3ds.json").write_text("[]", encoding="utf-8")
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
            print(f"  3DS OTP из Telegram: {otp_code}")

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
            _OTP_FILE_3D = Path(__file__).parent / "data" / "tg_otp_3ds.json"
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
                        print(f"  {G}✅ OTP из Telegram: {_nc} — ввожу...{RST}")
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

    _OTP_FILE = Path(__file__).parent / "data" / "tg_otp_3ds.json"

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
                print(f"  3DS OTP получен: {code}")
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
        pw = await async_playwright().start()
        _pre_inject_chrome_prefs(profile_path)
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()), **_browser_launch_kw(phone=phone_digits))
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await _ensure_vpn_connected(ctx)   # VPN PLY → USA (если расширение есть)

        # Открываем Flipkart чтобы установить домен, затем добавляем куки
        await page.goto("https://www.flipkart.com/", wait_until="domcontentloaded", timeout=20_000)
        await ctx.add_cookies(pw_cookies)
        await page.reload(wait_until="domcontentloaded", timeout=20_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        await page.wait_for_timeout(3_000)

        # Проверяем вход
        body = (await page.evaluate("() => document.body?.innerText || ''")).lower()
        logged_in = any(w in body for w in ["my account", "my profile", "logout",
                                             "мой аккаунт", "выйти", "orders"])
        if not logged_in:
            cur_url = page.url
            logged_in = "flipkart.com" in cur_url and "login" not in cur_url

        if not logged_in:
            return False, "Куки не дали входа — возможно устарели"

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
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


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
        tg_token = _get_telegram_token()
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


async def _do_gift_card_payment(page, profile_path=None) -> bool | str:
    """Оплата подарочными картами (Flipkart Gift Card).
    Читает сумму заказа, подбирает набор гифт-карт, по очереди вводит
    номер+PIN → «Add Gift Card» (баланс начисляется), затем «Place Order».
    Каждую УСПЕШНО добавленную карту сразу помечает использованной.
    Возврат: True — оплачено; "gift_insufficient" — не хватает гифт-карт;
    "gift_failed" — карты отклонены / не удалось оформить."""
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

    # ── Шаг 0: сначала ПРИМЕНЯЕМ уже имеющийся гифт-баланс галочкой «Use Gift Card».
    # Карты из хранилища добавляем ТОЛЬКО если этого не хватит (на остаток).
    try:
        await page.evaluate(r"""() => {
            for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                const box = cb.closest('div,label,li,section') || cb.parentElement;
                const t = (box ? box.innerText : '') || '';
                if (/use gift card/i.test(t) && !cb.checked) { cb.click(); return; }
            }
            for (const el of document.querySelectorAll('[role="checkbox"]')) {
                const box = el.closest('div,label,li,section') || el.parentElement;
                const t = (box ? box.innerText : '') || '';
                if (/use gift card/i.test(t) && el.getAttribute('aria-checked') !== 'true') { el.click(); return; }
            }
        }""")
    except Exception:
        pass
    await page.wait_for_timeout(1_500)

    _rem = await _read_order_total(page)
    if (await _gift_place_order_bbox(page)) or _rem <= 0:
        # Существующего гифт-баланса уже хватает — карты не нужны, идём к Place Order
        print(f"  {G}💰 Хватает уже применённого гифт-баланса — карты из хранилища не нужны{RST}")
        total = 0
    else:
        total = _rem
        if _rem < _orig_total:
            print(f"  {G}Применён имеющийся гифт-баланс, осталось покрыть ₹{_rem}{RST}")
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

    if total <= 0:
        # Остаток уже покрыт имеющимся балансом — карты не нужны, сразу к Place Order
        pass
    elif _small_bal >= total:
        print(f"  {G}Мелких гифт-карт достаточно (₹{_small_bal}) — крупные (≥₹{GIFT_CONFIRM_THRESHOLD}) не трогаю{RST}")
    elif _total_bal >= total:
        # Мелких мало, но с крупными хватает — спрашиваем подтверждение через TG
        _big = sorted({int(c.get("denom") or 0) for c in _all_gc
                       if not c.get("used") and c.get("number") and c.get("pin")
                       and int(c.get("denom") or 0) >= GIFT_CONFIRM_THRESHOLD}, reverse=True)
        _big_lbl = ", ".join(f"₹{d}" for d in _big) or f"≥₹{GIFT_CONFIRM_THRESHOLD}"
        print(f"  {Y}Мелких не хватает (₹{_small_bal} из ₹{total}). Спрашиваю подтверждение на крупные…{RST}")
        _gift_big_ev.clear()
        _gift_big_choice[0] = None
        _tg_send_direct_kb(
            f"🎁 *Не хватает мелких гифт-карт*\n\n"
            f"Сумма заказа: *₹{total}*\n"
            f"Мелкими (до ₹{GIFT_CONFIRM_THRESHOLD}) есть только: *₹{_small_bal}*\n\n"
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
        if _gift_big_choice[0] is True:
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
    _NUM_SEL = ("input[placeholder*='voucher number' i], input[placeholder*='gift card number' i], "
                "input[placeholder*='voucher' i]")
    _PIN_SEL = ("input[placeholder*='voucher pin' i], input[placeholder*='gift card pin' i], "
                "input[placeholder*='pin' i]")

    async def _voucher_visible():
        try:
            _l = page.locator(_NUM_SEL).first
            return (await _l.count() > 0) and (await _l.is_visible())
        except Exception:
            return False

    async def _click_add_opener():
        """Клик по ссылке «Add Gift Card» (открывает модалку ввода купона).
        НЕ трогаем «Use Gift Card» — это подпись строки с галочкой (её ставит
        отдельный _ensure_use_checkbox), клик по ней снял бы галочку."""
        try:
            bb = await page.evaluate(r"""() => {
                const want = ['add gift card', 'have a flipkart gift card?'];
                for (const el of document.querySelectorAll('a,button,div,span,[role="button"]')) {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (!want.includes(t)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 25 || r.height < 8 || el.offsetParent === null) continue;
                    return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
                return null;
            }""")
            if bb:
                await page.mouse.click(bb["x"], bb["y"])
                return True
        except Exception:
            pass
        return False

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
        """Ставит галочку «Use Gift Card», если она есть и снята."""
        try:
            await page.evaluate(r"""() => {
                for (const cb of document.querySelectorAll('input[type="checkbox"]')) {
                    const box = cb.closest('div,label,li,section') || cb.parentElement;
                    const t = (box ? box.innerText : '') || '';
                    if (/use gift card/i.test(t) && !cb.checked) { cb.click(); return; }
                }
                for (const el of document.querySelectorAll('[role="checkbox"]')) {
                    const box = el.closest('div,label,li,section') || el.parentElement;
                    const t = (box ? box.innerText : '') || '';
                    if (/use gift card/i.test(t) && el.getAttribute('aria-checked') !== 'true') { el.click(); return; }
                }
            }""")
        except Exception:
            pass

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
            _rep2, _b2, _n2, _s2 = _gift_shortage_report(_need)
            print(f"  {R}✘ Гифт-карт не хватает на остаток:{RST}")
            for _ln in _rep2.split("\n"):
                print(f"  {R}  {_ln}{RST}")
            _msg2 = (f"🎁 *Не хватает гифт-карт*\n\nПрименено на ₹{applied_sum}, "
                     f"осталось покрыть ₹{_need}.\n\n{_rep2}")
            _tg_send_direct(_msg2)
            break
        c = _sel[0]
        _num = str(c.get("number") or "").strip()
        _pin = str(c.get("pin") or "").strip()
        _dn  = int(c.get("denom") or 0)
        print(f"  🎁 Карта {_mask_gift(_num)} (₹{_dn}); осталось покрыть ₹{_need}...")

        # Снимок «до»: сколько уже зачислено гифтом и текущий Total (для детекции успеха)
        _applied_before = await _read_gift_applied()
        _total_before = await _read_order_total(page)

        # 1. Открываем форму ввода: жмём «Add Gift Card» и ЖДЁМ появления поля.
        _field_ready = await _voucher_visible()
        for _o in range(4):
            if _field_ready:
                break
            await _click_add_opener()
            for _ in range(12):   # до ~4.2 сек ждём поле, но ловим сразу как появилось
                await page.wait_for_timeout(350)
                if await _voucher_visible():
                    _field_ready = True
                    break
        if not _field_ready:
            print(f"  {R}✘ Поле ввода гифт-карты не появилось после «Add Gift Card» — прекращаю{RST}")
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
                # ошибка — но перепроверим зачисление ещё раз (ошибка могла остаться
                # от прошлой карты, а эта на самом деле зачислилась)
                _aa2 = await _read_gift_applied()
                _tt2 = await _read_order_total(page)
                if _aa2 > _applied_before or (_total_before > 0 and 0 < _tt2 < _total_before):
                    _success = True
                    _applied_after, _total_after = _aa2, _tt2
                    _errcat = ""
                break

        # Уже использована / добавлена на ДРУГОМ аккаунте → пометить, удалить, взять следующую
        if not _success and _errcat == "already":
            print(f"  {Y}↩ Карта {_mask_gift(_num)} уже использована на другом аккаунте — "
                  f"помечаю и удаляю, беру следующую{RST}")
            _mark_gift_used(c, profile_path, status="used_elsewhere")
            _tg_send_direct(f"🎁 Карта {_mask_gift(_num)} (₹{_dn}) уже использована на другом "
                            f"аккаунте — удалена, пробую следующую.")
            try: await page.keyboard.press("Escape")
            except Exception: pass
            continue

        if not _success:
            _why = "другой аккаунт" if _errcat == "already" else (_errcat or "нет зачисления")
            print(f"  {Y}⚠ Карта {_mask_gift(_num)} не зачислилась ({_why}) — пропускаю{RST}")
            _tried_bad.add(_num)
            try: await page.keyboard.press("Escape")
            except Exception: pass
            continue

        # Успех — сумма реально зачислена гифт-картой → помечаем использованной.
        # applied_sum берём из фактически зачисленного справа (точнее суммы номиналов).
        applied += 1
        applied_sum = _applied_after if _applied_after > applied_sum else (applied_sum + _dn)
        _mark_gift_used(c, profile_path, status="used")
        print(f"  {G}✔ Карта {_mask_gift(_num)} зачислена (₹{_dn}). "
              f"Гифтом покрыто ₹{applied_sum}, Total сейчас ₹{_total_after}{RST}")

        # Быстрая проверка: не хватает ли уже (Place Order / остаток покрыт).
        # Перечитываем зачисленную сумму и Total — сводка справа обновляется с
        # задержкой, поэтому проверяем в цикле, чтобы НЕ добавить лишнюю карту.
        await _ensure_use_checkbox()
        for _poc in range(4):   # до ~2 сек, но выходим сразу как увидели
            _ca = await _read_gift_applied()
            if _ca > applied_sum:
                applied_sum = _ca
            _ct = await _read_order_total(page)
            if await _gift_place_order_bbox(page) or applied_sum >= total or _ct <= 0:
                _enough = True
                break
            await page.wait_for_timeout(500)
        if _enough:
            print(f"  {G}💰 Баланса достаточно — Place Order доступна, карты больше не добавляю{RST}")
            break

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


async def _viewcheckout_to_payments(page) -> bool:
    """
    Общий хелпер: на viewcheckout обрабатывает email, кликает Continue,
    ждёт переход на payments. Retry до 3 раз.
    Возвращает True если страница payments загружена.
    """
    import random as _r

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


async def _check_flipkart_accessible() -> bool:
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


def _is_flipkart_accessible_sync() -> bool:
    try:
        return asyncio.run(_check_flipkart_accessible())
    except Exception:
        return False


@_serialize_purchase
async def _do_buy_membership(profile_path: Path, months: int, card: dict | None = None,
                             _skip_proxies: set | None = None,
                             _retry_n: int = 0,
                             _use_proxy: bool | None = None,
                             _forced_proxy: dict | None = None,
                             _skip_proxy_loop: bool = False,
                             _skip_ping: bool = False) -> tuple[bool, str]:
    """Buy Now → адрес (если нужен) → viewcheckout → Continue → оплата.
    Прокси используется для всего (навигация + оплата) через встроенный Playwright
    Chromium — CDP Fetch.authRequired обрабатывает авторизацию без диалога."""
    if _retry_n == 0 and not _skip_ping:
        print(f"  {DIM}Проверка доступности Flipkart...{RST}")
        if not await _check_flipkart_accessible():
            _ping_msg = "Flipkart недоступен"
            print(f"\n  {R}⚠ {_ping_msg}{RST}")
            print(f"  {Y}Покупка Membership невозможна. Повторите позже.{RST}")
            return False, _ping_msg
        print(f"  {G}Flipkart доступен.{RST}")

    if _use_proxy is None:
        _use_proxy = bool((_read_proxy_cfg() or {}).get("enabled", False))
    _MAX_PROXY_RETRIES = 3
    _auto_close = _forced_proxy is not None   # proxy-попытки всегда закрывают браузер
    if _skip_proxies is None:
        _skip_proxies = set()
    if _is_profile_locked(profile_path):
        print(f"  {Y}Профиль занят — закрываю Chrome и очищаю локи...{RST}")
        _clear_stale_profile_locks(profile_path)
    elif _retry_n > 0:
        _clear_stale_profile_locks(profile_path)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "playwright не установлен  (pip install playwright)"

    # Подавляем ConnectionResetError от Windows ProactorEventLoop при закрытии прокси-туннеля
    _loop = asyncio.get_event_loop()
    _orig_exc_handler = _loop.get_exception_handler()
    def _suppress_conn_reset(loop, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        (_orig_exc_handler(loop, ctx) if _orig_exc_handler
         else loop.default_exception_handler(ctx))
    _loop.set_exception_handler(_suppress_conn_reset)

    pw = await async_playwright().start()
    ctx = None
    _keep_open = False
    _local_proxy_port: int | None = None
    try:
        # Выбираем прокси и поднимаем локальный туннель (без диалога авторизации Chrome)
        _chosen_proxy: dict | None = None
        _last_proxy_server = ""
        _proxy_cfg_enabled = bool((_read_proxy_cfg() or {}).get("enabled"))
        if _forced_proxy is not None:
            _chosen_proxy = _forced_proxy
        elif _use_proxy and _proxy_cfg_enabled:
            _chosen_proxy = _pick_proxy(force=True,
                                         phone=_phone_from_path(profile_path),
                                         skip_servers=_skip_proxies)
        if _chosen_proxy:
            _last_proxy_server = _chosen_proxy.get("server", "")
            _pu = _chosen_proxy.get("username", "")
            _pp = _chosen_proxy.get("password", "")
            if _pu and _pp:
                _bare = _proxy_server_bare(_last_proxy_server)
                try:
                    _ph, _pport_s = _bare.rsplit(":", 1)
                    _local_proxy_port = await _start_local_auth_proxy(
                        _ph, int(_pport_s), _pu, _pp)
                    print(f"  {DIM}Туннель: 127.0.0.1:{_local_proxy_port} -> {_bare}{RST}")
                except Exception as _te:
                    print(f"  {Y}⚠ Туннель не запущен: {_te}{RST}")
        _pre_inject_chrome_prefs(profile_path)
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            **_browser_launch_kw(
                use_proxy=False,
                phone=_phone_from_path(profile_path),
                local_proxy_port=_local_proxy_port,
                forced_proxy_dict=_chosen_proxy if not _local_proxy_port else None,
                use_bundled_chromium=True))
        _stealth = _build_stealth_js_m()
        if _stealth:
            await ctx.add_init_script(_stealth)
        await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await _maximize_window(ctx, page)
        await _ensure_vpn_connected(ctx)   # VPN PLY → USA (если расширение есть)

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
            print(f"  {Y}Жду ответа в Telegram (60 сек)...{RST}")
            _bm_dl = asyncio.get_event_loop().time() + 60
            while asyncio.get_event_loop().time() < _bm_dl:
                if _orders_confirm_ev.is_set():
                    break
                await asyncio.sleep(1)
            if _orders_confirm_choice[0] is False:
                print(f"  {R}Удаляю профиль {_bm_phone_label}...{RST}")
                _keep_open = False
                import shutil as _sh_bm
                _sh_bm.rmtree(str(profile_path), ignore_errors=True)
                _tg_send_direct(f"🗑 Профиль `{_bm_phone_label}` удалён (дублирующий заказ)")
                return False, "Профиль удалён — дублирующий заказ"
            print(f"  {G}Продолжаю покупку...{RST}")

        # Как пункт 4: открываем страницу поиска, выбираем нужный продукт, жмём Buy Now
        err = await _navigate_search_buy(page, months)
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
                    return False, _NOT_LOGGED_IN_MSG

        addr_msg = ""

        async def _fill_addr_bm():
            nonlocal addr_msg
            a = _gen_indian_address()
            lat, lon = _CITY_COORDS.get(a["city"], (20.5937, 78.9629))
            await ctx.set_geolocation({"latitude": lat, "longitude": lon})
            await _maximize_window(ctx, page)
            if not await _fill_address_form(page, a):
                return False
            addr_msg = f"Адрес: {a['name']} | {a['pincode']} {a['city']}"
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
        if "viewcheckout" in page.url:
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
                # Пробуем сменить адрес один раз перед сдачей
                print(f"  {Y}⚠ Не доставляется — пробую сменить адрес...{RST}")
                _oos_addr_ok = False
                try:
                    _try_btn = page.locator(
                        "button:has-text('Try Another Address'), "
                        "[role='button']:has-text('Try Another Address'), "
                        "a:has-text('Try Another Address')"
                    )
                    if await _try_btn.count() > 0:
                        await _try_btn.first.click()
                        await page.wait_for_timeout(1_500)
                except Exception:
                    pass
                if "changeShippingAddress" in page.url or "add/form" in page.url:
                    _oos_addr_ok = await _fill_addr_bm()
                if not _oos_addr_ok:
                    return False, f"OUT_OF_STOCK|{addr_msg}"
                # Перепроверяем после смены адреса
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
                    return False, f"OUT_OF_STOCK|{addr_msg}"

            reached = await _viewcheckout_to_payments(page)
            if reached == "OUT_OF_STOCK":
                return False, f"OUT_OF_STOCK|{addr_msg}"

            # Set Location увёл на address-map, но навигация назад не завершилась
            if not reached and "address-map" in page.url:
                print(f"  Всё ещё на address-map — жду возврата на viewcheckout...")
                try:
                    await page.wait_for_url("**/viewcheckout**", timeout=10_000)
                    reached = await _viewcheckout_to_payments(page)
                except Exception:
                    if "address-map" in page.url:
                        print(f"  address-map: нажимаю Back...")
                        await page.go_back()
                        await page.wait_for_timeout(3_000)
                        reached = await _viewcheckout_to_payments(page)
            if reached == "OUT_OF_STOCK":
                return False, f"OUT_OF_STOCK|{addr_msg}"

            # После Continue мог появиться запрос адреса
            if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url) \
                    and "address-map" not in page.url:
                print(f"  Flipkart запросил адрес после Continue — заполняю...")
                if not await _fill_addr_bm():
                    return False, "Кнопка Save Address не найдена (после Continue)"
                reached = await _viewcheckout_to_payments(page)
                if reached == "OUT_OF_STOCK":
                    return False, f"OUT_OF_STOCK|{addr_msg}"
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
        # Если URL ещё в переходе — ждём до 15 сек пока не появится payments
        if "payments" not in page.url:
            print(f"  {DIM}Ждём загрузки страницы оплаты...{RST}")
            try:
                await page.wait_for_url("**/payments**", timeout=15_000)
            except Exception:
                pass
        if "payments" not in page.url:
            _keep_open = not _auto_close
            _send_tg_error(_pp_phone, f"Не удалось перейти на страницу оплаты ({page.url.split('?')[0].split('/')[-1]})")
            return True, (f"{'✅ ' + addr_msg if addr_msg else '✅ Адрес уже был сохранён'}"
                          f" → ⚠️ Оплата не загрузилась ({page.url.split('?')[0].split('/')[-1]})"
                          f", браузер {'оставлен открытым' if _keep_open else 'закрыт'}")

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
            _keep_open = False  # оплата прошла → закрываем браузер автоматически
            vt = _post_result.get("valid_till", "")
            if _last_proxy_server:
                _mark_proxy_ok(_last_proxy_server)
            return True, base + f" → ✅ Оплата прошла{(' (до ' + vt + ')') if vt else ''}"
        _keep_open = not _auto_close
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
        if _use_proxy and _is_proxy_error(exc) and _retry_n < _MAX_PROXY_RETRIES:
            svr = _last_proxy_server
            if svr:
                _mark_proxy_failed(svr)
                _skip_proxies.add(_proxy_server_bare(svr))
            print(f"  {Y}⚠ Прокси недоступен — пробую следующий "
                  f"({_retry_n + 1}/{_MAX_PROXY_RETRIES})...{RST}")
            return await _do_buy_membership(profile_path, months, card,
                                            _skip_proxies, _retry_n + 1, _use_proxy,
                                            _skip_ping=True)
        if _use_proxy and _is_proxy_error(exc):
            print(f"  {Y}⚠ Все прокси недоступны — пробую без прокси...{RST}")
            return await _do_buy_membership(profile_path, months, card,
                                            set(), 1, _use_proxy=False,
                                            _skip_ping=True)
        _keep_open = False  # ошибка — закрываем браузер (иначе EPIPE при жёстком убийстве)
        return False, msg
    finally:
        _loop.set_exception_handler(_orig_exc_handler)
        if _local_proxy_port:
            try: await _stop_local_auth_proxy(_local_proxy_port)
            except Exception: pass
        if not _keep_open:
            if ctx:
                try: await ctx.close()
                except Exception: pass
            try: await pw.stop()
            except Exception: pass


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
        elif msg.startswith("PROXY_DEAD:"):
            print(f"  {R}❌ {msg[11:].strip()}{RST}")
            print(f"  {Y}Перейдите в меню Прокси и нажмите [А] для покупки новых.{RST}")
        elif msg.startswith("OUT_OF_STOCK"):
            addr_info = msg.split("|", 1)[1] if "|" in msg else ""
            if addr_info:
                print(f"  {G}✔ {addr_info}{RST}")
            print(f"  {R}✘ Currently out of stock для этого пинкода.{RST}")
            print(f"  {Y}Этот профиль не подходит для покупки Black Membership.{RST}")
            print()
            confirm = input(f"  {BLD}Удалить профиль и купить с новым аккаунтом автоматически? [Д/Н]: {RST}").strip().lower()
            if confirm in ("д", "y"):
                try:
                    shutil.rmtree(str(selected["path"]))
                    print(f"\n  {M}🗑 Профиль удалён.{RST}")
                except Exception as exc:
                    print(f"\n  {R}Ошибка удаления: {exc}{RST}")
                print(f"\n  {DIM}Запускаю полный цикл: номер → вход → адрес → покупка...{RST}\n")
                ok2, msg2 = asyncio.run(_do_all_in_one(months, headless=False, card=None))
                if ok2:
                    print(f"\n  {G}{BLD}{msg2}{RST}")
                else:
                    print(f"\n  {R}❌ {msg2}{RST}")
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
        await page.goto(login_url, wait_until="domcontentloaded", timeout=7_000)
    except Exception as exc:
        return f"error:goto failed: {exc}"

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
    otp_appeared = False
    _loop_t = asyncio.get_running_loop()
    poll_dl = _loop_t.time() + 20  # макс 20s ждём
    _fast_until = _loop_t.time() + 5  # первые 5s — быстрый опрос
    while _loop_t.time() < poll_dl:
        # Проверяем блокировку — innerText захватывает только видимый текст
        try:
            body = await page.evaluate(
                "() => (document.body?.innerText || document.body?.textContent || '')"
            )
            if any(w in body.lower() for w in _BLOCKED_WORDS):
                return "blocked"
        except Exception:
            pass
        # Проверяем появление OTP-поля
        try:
            el = await page.query_selector(_OTP_SEL)
            if el and await el.is_visible():
                otp_appeared = True
                break
        except Exception:
            pass
        await asyncio.sleep(0.15 if _loop_t.time() < _fast_until else 0.5)

    if not otp_appeared:
        # Последняя проверка на блокировку (на случай если toast появился позже)
        try:
            body = await page.evaluate("() => document.body?.textContent || ''")
            if any(w in body.lower() for w in _BLOCKED_WORDS):
                return "blocked"
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
            GrizzlySMSClient,
            GrizzlySMSError,
            NumberUnavailableError,
            InsufficientBalanceError,
        )
    except ImportError as e:
        return False, f"Зависимость не установлена: {e}  (pip install playwright httpx pyyaml)"

    try:
        with open("config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except Exception as exc:
        return False, f"Ошибка чтения config.yaml: {exc}"

    gsms        = cfg.get("grizzlysms", {})
    api_key     = (_read_secrets().get("grizzlysms") or {}).get("api_key", "").strip()
    service     = gsms.get("service", "xt")
    country     = gsms.get("country", 22)
    max_price   = gsms.get("max_price", 0.15)
    poll_int    = float(gsms.get("poll_interval", 3))
    gn_timeout  = float(gsms.get("get_number_timeout", 120))
    slots       = int(gsms.get("parallel_get_slots", 3))
    poll_delay  = float(gsms.get("get_number_retry_delay", 2.0))
    price_tiers  = gsms.get("price_tiers")   # None → max_price весь timeout
    cycle_prices = bool(gsms.get("cycle_prices", False))

    if not api_key:
        return False, "GrizzlySMS api_key не задан в secrets.yaml"

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

    sms_client = GrizzlySMSClient(api_key, http_timeout=30)
    _failed_cancels: list = []  # IDs, которые не удалось отменить → фоновый повтор

    async def _tg_cancel_notify(ph: str, reason: str = "") -> None:
        """Шлёт TG-уведомление об отмене номера + остаток баланса GrizzlySMS."""
        try:
            import httpx as _hx_c, json as _jc
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jc.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _nc = [int(c) for c in _sd.get("chats", [])
                   if _ss.get(str(c), {}).get("buy_number", True)]
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
        try:
            import httpx as _hx_lo, json as _jlo
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jlo.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _nc = [int(c) for c in _sd.get("chats", [])
                   if _ss.get(str(c), {}).get("buy_number", True)]
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
        try:
            import httpx as _hx_b, json as _jb
            _tok = _get_telegram_token()
            if not _tok or not TG_SUBSCRIBERS_FILE.exists():
                return
            _sd = _jb.loads(TG_SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
            _ss = _sd.get("settings", {})
            _nc = [int(c) for c in _sd.get("chats", [])
                   if _ss.get(str(c), {}).get("buy_number", True)]
            if not _nc:
                return
            async with _hx_b.AsyncClient(timeout=8, trust_env=False) as _client:
                for _c in _nc:
                    try:
                        await _client.post(
                            f"https://api.telegram.org/bot{_tok}/sendMessage",
                            json={"chat_id": _c,
                                  "text": f"📞 *Куплен номер*\n\n`{ph}`\n_Жду OTP..._",
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

        # Проверяем доступность Flipkart один раз перед стартом.
        # Повторные попытки (следующий номер, карта и т.д.) проверку не делают.
        print(f"  {DIM}Проверка доступности Flipkart...{RST}")
        if not await _check_flipkart_accessible():
            return False, "Flipkart недоступен — запуск отменён"
        print(f"  {G}Flipkart доступен.{RST}")

        attempt = 0
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
                print(f"\n  {C}[Попытка {attempt}] Ищу номер GrizzlySMS "
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
                    print(f"  {R}Недостаточно средств на балансе GrizzlySMS — отменяю все активные номера...{RST}")
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
                    return False, f"GrizzlySMS ошибка: {exc}"

                phone_10 = phone.lstrip("+")
                if phone_10.startswith("91") and len(phone_10) > 10:
                    phone_10 = phone_10[2:]
                phone_10 = phone_10[-10:]
                print(f"  {G}Номер получен: +91 {phone_10}  (id={phone_id}, цена={cost_gn}){RST}")

                # Регистрируем аренду немедленно, чтобы избежать утечек
                _grizzly_module.register_rental(phone_id, phone_10, time.monotonic(), pw=pw, login_url=login_url, months=months, intercept_mode=intercept_mode)

                # TG: уведомление о покупке номера
                await _send_tg_buy(phone_10)

                # ── 2. Профиль и браузер ─────────────────────────────────────
                profile_path = DONE_PROFILES_DIR / f"profile_{phone_10}"
                profile_path.mkdir(parents=True, exist_ok=True)
                _pre_inject_chrome_prefs(profile_path)

                _grizzly_module.update_rental_browser(phone_id, profile_path=profile_path)

                ctx = await pw.chromium.launch_persistent_context(
                    str(profile_path.resolve()),
                    **_browser_launch_kw(headless=headless, phone=phone_10))
                _grizzly_module.update_rental_browser(phone_id, ctx=ctx)

                stealth = _build_stealth_js_m()
                if stealth:
                    await ctx.add_init_script(stealth)
                await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                _grizzly_module.update_rental_browser(phone_id, page=page)
                if not headless:
                    await _maximize_window(ctx, page)
                await _ensure_vpn_connected(ctx)   # VPN PLY → USA (если расширение есть)

                def _on_new_page(p):
                    async def _check():
                        try:
                            await p.wait_for_load_state("domcontentloaded", timeout=5_000)
                            if "terms" in p.url.lower():
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
                    return False, p1.removeprefix("error:")

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

                        # TG: уведомление о покупке номера
                        await _send_tg_buy(nph10)

                        n_profile_path = DONE_PROFILES_DIR / f"profile_{nph10}"
                        n_profile_path.mkdir(parents=True, exist_ok=True)
                        _pre_inject_chrome_prefs(n_profile_path)
                        _grizzly_module.update_rental_browser(nid, profile_path=n_profile_path)

                        n_ctx = await pw.chromium.launch_persistent_context(
                            str(n_profile_path.resolve()),
                            **_browser_launch_kw(headless=headless, phone=nph10)
                        )
                        _grizzly_module.update_rental_browser(nid, ctx=n_ctx)

                        if stealth:
                            await n_ctx.add_init_script(stealth)
                        await n_ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")
                        npage = n_ctx.pages[0] if n_ctx.pages else await n_ctx.new_page()
                        _grizzly_module.update_rental_browser(nid, page=npage)

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
                            _pending.append([nid, nph10, npage, time.monotonic(), n_ctx])
                            print(f"  {G}Номер #{n} готов, жду OTP...{RST}")
                        else:
                            print(f"  {Y}Номер #{n} не прошёл ({r2}){RST}")
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
                                        if intercept_mode:
                                            print(f"  {G}+91 {e_ph}: 2 мин OTP={_est['code']} (перехват) → завершаю{RST}")
                                            await _send_tg_otp(e_ph, _est['code'], " (перехват)")
                                            try: await sms_client.complete(e_id)
                                            except Exception: pass
                                            _grizzly_module.mark_completed(e_id)
                                        else:
                                            print(f"  {G}+91 {e_ph}: 2 мин OTP={_est['code']} → фон{RST}")
                                            _submit_bg_login(api_key, e_id, _est["code"], login_url, months,
                                                             phone_10=e_ph)
                                            _grizzly_module.mark_completed(e_id)
                                        try:
                                            if e_ctx: await e_ctx.close()
                                        except Exception:
                                            try: await e_pg.close()
                                            except Exception: pass
                                        return
                                except Exception: pass
                                print(f"  {Y}+91 {e_ph}: 2 мин — отменяю в фоне (retry 10s){RST}")
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
                                win_id, win_ph, win_page = a_id, a_ph, a_pg
                                win_ctx = next((e[4] for e in _active if e[0] == a_id), None)
                                print(f"  {G}OTP для +91 {a_ph}: {otp_code}{RST}")
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
                                                print(f"  {G}[BG] +91 {o_ph} OTP={_lst['code']} (перехват) → завершаю{RST}")
                                                await _send_tg_otp(o_ph, _lst['code'], " (перехват)")
                                                try: await sms_client.complete(o_id)
                                                except Exception: pass
                                                _grizzly_module.mark_completed(o_id)
                                                _loser_login_ok = True
                                            else:
                                                _loser_otp = _lst["code"]
                                                print(f"  {G}[BG] +91 {o_ph}: OTP {_loser_otp} — вхожу{RST}")
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
                                    if intercept_mode:
                                        print(f"  {G}+91 {o_ph}: финал OTP={_fst['code']} (перехват) → завершаю{RST}")
                                        await _send_tg_otp(o_ph, _fst['code'], " (перехват)")
                                        try: await sms_client.complete(o_id)
                                        except Exception: pass
                                        _grizzly_module.mark_completed(o_id)
                                    else:
                                        print(f"  {G}+91 {o_ph}: финал OTP={_fst['code']} → фон{RST}")
                                        _submit_bg_login(api_key, o_id, _fst["code"], login_url, months, phone_10=o_ph)
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
                    print(f"  {G}✔ OTP получен для +91 {phone_10}: {otp_code} (отправлено в TG){RST}")
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
                print(f"  {DIM}Ввожу OTP {otp_code} для +91 {phone_10}...{RST}")
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
                    try:
                        await sms_client.complete(phone_id)
                    except Exception:
                        pass
                    _grizzly_module.mark_completed(phone_id)
                    try:
                        (profile_path / ".profile_meta.json").write_text(
                            json.dumps({
                                "username": phone_10,
                                "login_ts": time.time(),
                                "site_url": url.split("?")[0],
                                "status": "email_completed"
                            }, ensure_ascii=False),
                            encoding="utf-8",
                        )
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

                    reached = await _viewcheckout_to_payments(page)

                    if not reached and ("changeShippingAddress" in page.url or "add/form" in page.url):
                        print(f"  {DIM}Flipkart запросил адрес — заполняю...{RST}")
                        if not await _fill_oi():
                            _try_next = True
                            continue
                        reached = await _viewcheckout_to_payments(page)

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
                            await _viewcheckout_to_payments(page)
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
                    if ctx:
                        try: await ctx.close()
                        except Exception: pass
                    try: await pw.stop()
                    except Exception: pass
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
            elif "PROXY_DEAD" in msg:
                print(f"  {R}  Прерываю — прокси мёртв.{RST}")
                break
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

    # Показываем последние 60 строк
    cls()
    header("ЛОГИ  (последние 60 строк)", B)
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    recent = lines[-60:]

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
    try:
        import playwright, loguru, yaml, httpx  # noqa: F401
    except ImportError:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as _pw:
            _exe = _pw.chromium.executable_path
        import os
        if not os.path.exists(_exe):
            return False
    except Exception:
        return False
    return True


def screen_install(auto: bool = False):
    if auto and _deps_ok():
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
    """Фоновая проверка. Git если есть .git папка, иначе GitHub API."""
    global _update_available, _update_commits, _update_checked
    _cwd = Path(__file__).parent
    lines: list[str] = []
    _git_ok = False
    if (_cwd / ".git").exists():
        try:
            _fr = subprocess.run([_GIT, "fetch", "--quiet", "origin"],
                                 capture_output=True, timeout=20, cwd=_cwd)
            if _fr.returncode == 0:
                r = subprocess.run([_GIT, "log", "HEAD..FETCH_HEAD", "--oneline", "--no-color"],
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
    # Синхронизируем с bot-модулем (он хранит канонические значения для UI)
    try:
        _bot_module._update_available = _update_available
        _bot_module._update_commits   = list(lines)
        _bot_module._update_checked   = True
    except Exception:
        pass


def _do_git_update() -> tuple[bool, str]:
    """Применяет обновление: git pull если есть .git, иначе HTTP-скачивание."""
    global _update_available, _update_commits
    _cwd = Path(__file__).parent
    # Пробуем git только если есть .git папка
    if (_cwd / ".git").exists():
        try:
            _branch = "master"
            try:
                _rb = subprocess.run([_GIT, "rev-parse", "--abbrev-ref", "HEAD"],
                                     capture_output=True, text=True, timeout=5, cwd=_cwd)
                if _rb.returncode == 0:
                    _branch = _rb.stdout.strip() or "master"
            except Exception:
                pass
            r_fetch = subprocess.run([_GIT, "fetch", "origin", _branch],
                                     capture_output=True, text=True, timeout=60, cwd=_cwd,
                                     encoding="utf-8", errors="replace")
            if r_fetch.returncode != 0:
                err = r_fetch.stderr.strip() or r_fetch.stdout.strip() or "git fetch не удался"
                return False, err
            r_merge = subprocess.run([_GIT, "merge", "--ff-only", f"origin/{_branch}"],
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
    """Подробный отчёт о нехватке гифт-карт под сумму need_amount.
    Возвращает (текст, баланс, округл_нужно, нехватка)."""
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
        f"Нужно: ₹{need}" + (f"  (цена ₹{need_amount}, гифт-картами кратно 50)"
                             if need != need_amount else ""),
        f"В хранилище: ₹{bal}  →  {breakdown}",
        f"Не хватает: ₹{short}  (добавьте карт на эту сумму, напр. {short // 50}×₹50)",
    ]
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

        section("НАСТРОЙКИ", B)
        opt("0", "Карты для оплаты (добавить / удалить)", C)
        _gc_bal = _gift_balance()
        opt("Г", f"🎁 Подарочные карты  {DIM}(баланс ₹{_gc_bal}){RST}", C)
        opt("Р", "Прокси (добавить / удалить / вкл-выкл)  ⛔ врем. отключены", Y)
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
            elif choice in ("Р", "R"):
                screen_proxy()
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
    При каждом запуске подставляет ключи из secrets.yaml в config.yaml,
    чтобы даже после пересоздания config.yaml ключи восстановились.
    """
    import yaml as _yaml

    script_dir      = Path(__file__).parent
    secrets_path    = script_dir / "secrets.yaml"
    secrets_example = script_dir / "secrets.yaml.example"
    cfg_path        = script_dir / "config.yaml"
    example_path    = script_dir / "config.yaml.example"

    _SECRET_KEYS = [
        ("grizzlysms", "api_key"),
        ("telegram", "token"),
        ("proxy6", "api_key"),
    ]
    _PLACEHOLDERS = {
        "", "YOUR_GRIZZLYSMS_API_KEY", "YOUR_TELEGRAM_BOT_TOKEN", "YOUR_PROXY6_API_KEY",
    }

    def _real(val) -> bool:
        if val is None:
            return False
        s = str(val).strip()
        return bool(s) and s not in _PLACEHOLDERS

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

    # Кэшируем secrets в глобальной переменной — единственный источник ключей
    global _SECRETS
    _SECRETS = secrets


def _check_setup() -> None:
    """Проверяет заполненность secrets.yaml. При первом запуске — показывает
    инструкцию и выходит (menu.bat не перезапускает при sys.exit(0))."""
    import yaml as _yaml

    script_dir   = Path(__file__).parent
    secrets_path = script_dir / "secrets.yaml"
    example_path = script_dir / "secrets.yaml.example"

    def _real(val) -> bool:
        if val is None:
            return False
        s = str(val).strip()
        return bool(s) and not s.upper().startswith("YOUR_") and s not in {"", "null", "~"}

    secrets: dict = {}
    if secrets_path.exists():
        try:
            with open(secrets_path, encoding="utf-8") as _f:
                secrets = _yaml.safe_load(_f) or {}
        except Exception:
            pass

    grizzly_ok  = _real((secrets.get("grizzlysms") or {}).get("api_key"))
    telegram_ok = _real((secrets.get("telegram")   or {}).get("token"))
    proxy6_ok   = _real((secrets.get("proxy6")     or {}).get("api_key"))

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

    if not proxy6_ok:
        print(f"  {DIM}○  proxy6:{RST}")
        print(f"  {DIM}     api_key: (необязательно)  ← px6.link → API → Ключ{RST}")
        print(f"  {DIM}   Без прокси всё работает. Прокси нужен для покупок с индийского IP.{RST}")
    else:
        print(f"  {G}✓  proxy6.api_key — заполнен (прокси доступен){RST}")

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

    cfg_path     = Path(__file__).parent / "config.yaml"
    example_path = Path(__file__).parent / "config.yaml.example"
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
    script_dir = Path(__file__).parent
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
        if "--full-cycle" in _cli or "--login-only" in _cli:
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

                # ── Проверка доступности Flipkart перед покупкой номеров ──────
                print(f"  {DIM}Проверка доступности Flipkart...{RST}")
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
                    print(f"  {G}Flipkart доступен.{RST}")
                except Exception as _ping_err:
                    _ping_msg = f"Flipkart недоступен ({type(_ping_err).__name__}): {_ping_err}"
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
                    from grizzly_sms import GrizzlySMSClient as _GC
                    _api_key_s = (_read_secrets().get("grizzlysms") or {}).get("api_key", "")
                    if _api_key_s:
                        _cl_s = _GC(_api_key_s, http_timeout=10)
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
                    _api_key_s2 = (_read_secrets().get("grizzlysms") or {}).get("api_key", "")
                    if _api_key_s2:
                        from grizzly_sms import GrizzlySMSClient as _GC2
                        _cl_s2 = _GC2(_api_key_s2, http_timeout=10)
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
                    _tg_tok_st = _get_telegram_token()
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
                # TG-бот стартует после инициализации секретов (токен уже в config.yaml)
                threading.Thread(target=_menu_tg_bot_thread, daemon=True, name="tg-menu").start()
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
