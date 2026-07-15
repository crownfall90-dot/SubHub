"""
SubHub — десктопное приложение (GUI).
YouTube · GGSELL · DeepSeek · Kling AI
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

_HERE = Path(__file__).parent
os.chdir(_HERE)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)

# ── Design system — dark mode + светлые акценты (контраст на тёмном) ──
# Фон тёмный slate, текст почти белый, акценты — светлые/яркие на CTA.
FONT_UI = "Segoe UI"
FONT_DISPLAY = "Bahnschrift"
FONT_MONO = 11
BG_MAIN = "#0B1020"
BG_SIDEBAR = "#0E1424"
BG_CARD = "#151C2E"
BG_CARD_HOVER = "#1C253A"
BG_ELEVATED = "#1A2236"
BG_SURFACE = "#121929"
BG_NAV_ACTIVE = "#1C253A"
BG_GLASS = "#131A2A"
TEXT_DIM = "#A8B4C8"
TEXT_MUTED = "#7E8BA3"
TEXT_PRIMARY = "#F5F7FB"
TEXT_ON_ACCENT = "#0B1020"   # тёмный текст на светлом акценте
ACCENT = "#C4B5FD"           # светлый violet — читается на тёмном
ACCENT_HOVER = "#DDD6FE"
ACCENT_SOFT = "#2A2448"
ACCENT_CYAN = "#7DD3FC"
SUCCESS = "#34D399"
SUCCESS_FG = "#6EE7B7"
WARNING = "#FBBF24"
ERROR = "#FB7185"
BTN_SECONDARY = "#1A2236"
BTN_SECONDARY_HOVER = "#243049"
BTN_SUCCESS = "#10B981"
BORDER_SUBTLE = "#2A3548"
BORDER_GLOW = "#5B4B8A"
RADIUS_CARD = 14
RADIUS_PIN = 16
RADIUS_BTN = 10
RADIUS_SM = 8
RADIUS_CHIP = 12
_PAD_PAGE = 20
_PAD_CARD = 16
_GAP_CARD = 10
BTN_H = 34
BTN_H_MD = 38
BTN_ICON = 32
SIDEBAR_W = 232
FONT_TITLE = 24
FONT_SECTION = 14
FONT_BODY = 13
FONT_CAPTION = 12
FONT_SMALL = 11
_SIDEBAR_LOG_LINES = 80
_MAIN_LOG_LINES = 800
# Анимации легче: меньше after()-тиков на hover/переходы (иначе UI «плит»)
_ANIM_MS = 28
_ANIM_STEPS = 5
_MAIN_QUEUE_IDLE_MS = 120
_MAIN_QUEUE_BUSY_MS = 40
_LOG_TICK_MS = 1000
_STATUS_TICK_MS = 5000
_GGS_NOTIFY_MS = 2000
_ACTIVATE_POLL_MS = 1000

# Сервисные акценты — светлые, контраст на тёмных карточках
SVC_YOUTUBE = "#67E8F9"  # cyan — YouTube hub CTA / chips
SVC_GGSELL = "#FDBA74"
SVC_DEEPSEEK = "#93C5FD"
SVC_KLING = "#5EEAD4"


# Кэш шрифтов: один CTkFont на (семейство, размер, вес) вместо сотен копий —
# меньше памяти и быстрее построение страниц. Шрифты нигде не мутируются.
_FONT_CACHE: dict[tuple[str, int, str], ctk.CTkFont] = {}


def _cached_font(family: str, size: int, weight: str) -> ctk.CTkFont:
    key = (family, size, weight)
    font = _FONT_CACHE.get(key)
    if font is None:
        font = _FONT_CACHE[key] = ctk.CTkFont(family=family, size=size, weight=weight)
    return font


def _ui_font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return _cached_font(FONT_UI, size, weight)


def _display_font(size: int, weight: str = "bold") -> ctk.CTkFont:
    return _cached_font(FONT_DISPLAY, size, weight)


def _hex_rgb(hex_c: str) -> tuple[int, int, int]:
    h = hex_c.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp_hex(a: str, b: str, t: float) -> str:
    ar, ag, ab = _hex_rgb(a)
    br, bg, bb = _hex_rgb(b)
    return _rgb_hex(
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


def _ease_out(t: float) -> float:
    """Ease-out cubic — быстрый старт, мягкое затухание (дизайн-система: subtle)."""
    return 1.0 - (1.0 - t) ** 3


def _animate_color(
    widget, prop: str, start: str, end: str,
    steps: int = _ANIM_STEPS, interval_ms: int = _ANIM_MS, step: int = 0,
    *, _token: int | None = None,
) -> None:
    """Цветовой tween с отменой по свойству (hover не копит after()-цепочки)."""
    try:
        if not widget.winfo_exists():
            return
    except Exception:
        return
    tokens = getattr(widget, "_anim_tokens", None)
    if not isinstance(tokens, dict):
        tokens = {}
        widget._anim_tokens = tokens  # type: ignore[attr-defined]
    if step == 0:
        tok = int(tokens.get(prop, 0) or 0) + 1
        tokens[prop] = tok
        _token = tok
    elif _token is None or tokens.get(prop) != _token:
        return
    if step >= steps:
        with contextlib.suppress(Exception):
            widget.configure(**{prop: end})
        return
    t = _ease_out((step + 1) / steps)
    with contextlib.suppress(Exception):
        widget.configure(**{prop: _lerp_hex(start, end, t)})
    with contextlib.suppress(Exception):
        widget.after(
            interval_ms,
            lambda: _animate_color(
                widget, prop, start, end, steps, interval_ms, step + 1,
                _token=_token,
            ),
        )

class AutoHideScrollFrame(ctk.CTkScrollableFrame):
    """CTkScrollableFrame, скрывающий скроллбар, когда контент помещается."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Метка «это настоящий скролл-canvas» — для глобального обработчика колеса
        self._parent_canvas._scroll_owner = self
        self._parent_canvas.bind("<Configure>", self._sync_scrollbar, add="+")
        self.bind("<Configure>", self._sync_scrollbar, add="+")

    def _sync_scrollbar(self, _e=None) -> None:
        # ponytail: Configure спамит на каждый сдвиг — троттлим redraw скроллбара
        if getattr(self, "_sb_sync_pending", False):
            return
        self._sb_sync_pending = True

        def _do() -> None:
            self._sb_sync_pending = False
            try:
                first, last = self._parent_canvas.yview()
                if last - first >= 1.0:
                    self._scrollbar.grid_remove()
                else:
                    self._scrollbar.grid()
            except Exception:
                pass

        with contextlib.suppress(Exception):
            self.after(80, _do)


SERVICE_META: dict[str, dict[str, Any]] = {
    "youtube": {
        "title": "YouTube Premium",
        "subtitle": "Flipkart · вход · покупка · VPN",
        "accent": SVC_YOUTUBE,
        "icon": "YT",
        "ready": True,
    },
    "ggsell": {
        "title": "GGSELL",
        "subtitle": "Заказы · мониторинг · доставка",
        "accent": SVC_GGSELL,
        "icon": "GG",
        "ready": True,
    },
    "deepseek": {
        "title": "DeepSeek",
        "subtitle": "Пополнение API-баланса",
        "accent": SVC_DEEPSEEK,
        "icon": "DS",
        "ready": True,
    },
    "kling": {
        "title": "Kling AI",
        "subtitle": "Скоро",
        "accent": SVC_KLING,
        "icon": "KL",
        "ready": False,
    },
}

_GGS_TEMPLATE_META: dict[str, tuple[str, str]] = {
    "msg_greeting": ("Приветствие", "При новом заказе"),
    "msg_wait": ("Ожидание", "Пока готовится ссылка"),
    "msg_template": ("Ссылка готова", "С {link}"),
    "msg_review_promo": ("Промокод", "С {promo_code}"),
    "ds_ask_creds": ("DS: запрос данных", "DeepSeek — почта и пароль"),
    "ds_ask_password": ("DS: запрос пароля", "DeepSeek — пароль отдельно"),
    "ds_processing": ("DS: выполняю", "С {amount}"),
    "ds_done": ("DS: готово", "С {amount} и {balance}"),
    "ds_fail_creds": ("DS: неверные данные", "Вход не удался"),
    "ds_delay": ("DS: задержка", "Ручное выполнение"),
}

_APP_SETTINGS_PATH = _HERE / "data" / "app_settings.json"
_APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "background_mode": True,
    "minimize_to_tray": False,
    "run_at_startup": False,
    "start_minimized": False,
    "notify_ggs_orders": True,
    "notify_ggs_messages": True,
    "notify_telegram": True,
}
_WIN_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_STARTUP_NAME = "SubHub"
APP_NAME = "SubHub"
APP_VENDOR = "Crownfall"
APP_YEAR = "2026"


def _load_app_version() -> str:
    for cand in (_HERE / "VERSION", _HERE / "data" / "VERSION"):
        try:
            v = cand.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if v:
                return v
        except Exception:
            pass
    return "1.4.0"


APP_VERSION = _load_app_version()
APP_COPYRIGHT = f"\u00a9 {APP_YEAR} {APP_VENDOR}. All rights reserved."
APP_TAGLINE = "YouTube \u00b7 GGSELL \u00b7 automation"
_WIN_APP_ID = "Crownfall.SubHub.Desktop.1"


def _set_windows_app_id() -> None:
    """Windows: своя иконка в панели задач вместо pythonw.exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_WIN_APP_ID)
    except Exception:
        pass


def _win_toplevel_hwnd(widget) -> int:
    import ctypes
    wid = widget.winfo_id()
    hwnd = ctypes.windll.user32.GetParent(wid)
    return hwnd if hwnd else wid


def _set_win32_window_icon(hwnd: int, ico_path: str) -> None:
    import ctypes
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x10
    LR_DEFAULTSIZE = 0x40
    WM_SETICON = 0x80
    ICON_SMALL, ICON_BIG = 0, 1
    path = str(Path(ico_path).resolve())
    for cx, cy, kind in ((32, 32, ICON_SMALL), (256, 256, ICON_BIG)):
        hicon = ctypes.windll.user32.LoadImageW(0, path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
        if hicon:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, kind, hicon)
    hicon = ctypes.windll.user32.LoadImageW(
        0, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE,
    )
    if hicon:
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)


_SUBHUB_ROOT = _HERE.resolve()
_SUBHUB_APP_PY = (_HERE / "app.py").resolve()
_WIN_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_GUI_MUTEX_NAME = "Global\\Crownfall.SubHub.GUI.v1"
_ACTIVATE_EVENT_NAME = "Local\\Crownfall.SubHub.Activate.v1"
_gui_mutex_handle: int | None = None
_activate_event_handle: int | None = None


def _cmdline_is_subhub_gui(cmdline: str) -> bool:
    """True для python/pythonw с app.py / _gui_boot.py из этой установки SubHub."""
    if not cmdline:
        return False
    norm = cmdline.lower().replace("/", "\\")
    root = str(_SUBHUB_ROOT).lower().replace("/", "\\")
    app_py = str(_SUBHUB_APP_PY).lower().replace("/", "\\")
    boot = str((_HERE / "scripts" / "_gui_boot.py").resolve()).lower().replace("/", "\\")
    if root not in norm and app_py not in norm and boot not in norm:
        return False
    return (
        "app.py" in norm
        or "_gui_boot.py" in norm
        or boot in norm
    )


def _collect_subhub_gui_pids() -> list[int]:
    """PIDs python/pythonw с app.py из этой папки (без PowerShell — быстрый Win32)."""
    if sys.platform != "win32":
        return [os.getpid()]
    found = _collect_subhub_gui_pids_win32()
    if found is not None:
        return found
    # Fallback: только heartbeat PID (без 3–4s PowerShell)
    hb = _read_app_heartbeat()
    pid = int(hb.get("pid") or 0)
    return [pid] if pid > 0 else []


def _collect_subhub_gui_pids_win32() -> list[int] | None:
    return _collect_pids_win32(("python.exe", "pythonw.exe"), _cmdline_is_subhub_gui)


def _collect_pids_win32(exe_names: tuple[str, ...], cmd_match) -> list[int] | None:
    """Enumerate exe_names via Toolhelp + ProcessCommandLineInformation."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ProcessCommandLineInformation = 60

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, INVALID_HANDLE_VALUE, None):
        return None

    found: list[int] = []
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return None
        while True:
            name = (pe.szExeFile or "").lower()
            pid = int(pe.th32ProcessID or 0)
            if pid > 0 and name in exe_names:
                cmd = _win_process_cmdline(pid, kernel32, ntdll,
                                           PROCESS_QUERY_LIMITED_INFORMATION,
                                           ProcessCommandLineInformation,
                                           UNICODE_STRING)
                if cmd and cmd_match(cmd):
                    found.append(pid)
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(snap)
    return found


def _win_process_cmdline(pid, kernel32, ntdll, access, info_class, unicode_cls) -> str:
    import ctypes
    from ctypes import wintypes

    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            h, info_class, None, 0, ctypes.byref(size),
        )
        # STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
        if size.value <= 0:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        status = ntdll.NtQueryInformationProcess(
            h, info_class, buf, size.value, ctypes.byref(size),
        )
        if status != 0:
            return ""
        us = unicode_cls.from_buffer_copy(buf.raw[:ctypes.sizeof(unicode_cls)])
        if not us.Buffer or us.Length <= 0:
            return ""
        nchars = us.Length // 2
        return ctypes.wstring_at(us.Buffer, nchars)
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(h)


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=12,
                creationflags=_WIN_NO_WINDOW,
            )


def _cmdline_is_subhub_automation(cmdline: str) -> bool:
    """python с main.py/menu.py из этой папки SubHub (не GUI) — автоматизация."""
    if not cmdline:
        return False
    norm = cmdline.lower().replace("/", "\\")
    root = str(_SUBHUB_ROOT).lower().replace("/", "\\")
    if root not in norm or _cmdline_is_subhub_gui(cmdline):
        return False
    return "main.py" in norm or "menu.py" in norm


def _cmdline_is_subhub_chrome(cmdline: str) -> bool:
    """Chrome/Chromium с профилем из этой папки SubHub (браузеры автоматизации)."""
    if not cmdline:
        return False
    norm = cmdline.lower().replace("/", "\\")
    root = str(_SUBHUB_ROOT).lower().replace("/", "\\")
    return "--user-data-dir" in norm and root in norm


def _kill_stale_automation_processes() -> int:
    """Останавливает автоматизацию прошлого сеанса: python main/menu + их Chrome.

    Вызывается при старте приложения: после перезапуска SubHub не должно
    оставаться фоновых процессов поиска номеров и браузеров.
    """
    if sys.platform != "win32":
        return 0
    victims: set[int] = set()
    with contextlib.suppress(Exception):
        victims.update(_collect_pids_win32(
            ("python.exe", "pythonw.exe"), _cmdline_is_subhub_automation) or [])
    with contextlib.suppress(Exception):
        victims.update(_collect_pids_win32(
            ("chrome.exe", "chromium.exe", "headless_shell.exe"),
            _cmdline_is_subhub_chrome) or [])
    victims.discard(os.getpid())
    if victims:
        _kill_pids(sorted(victims))
        with contextlib.suppress(Exception):
            import menu as _m
            _m.clear_automation_proc()
    return len(victims)


def _kill_stale_subhub_processes(*, keep_pid: int | None = None) -> int:
    """Завершить лишние экземпляры SubHub GUI (app.py). Возвращает число убитых."""
    keep = os.getpid() if keep_pid is None else keep_pid
    victims = sorted({p for p in _collect_subhub_gui_pids() if p != keep})
    if victims:
        _kill_pids(victims)
        time.sleep(0.2)
    return len(victims)


def _read_app_heartbeat() -> dict[str, Any]:
    path = _HERE / "data" / "heartbeat_app.json"
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def _try_acquire_gui_mutex() -> bool:
    """Один экземпляр GUI: второй запуск не убивает первый и не гоняется с ним."""
    global _gui_mutex_handle
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    _gui_mutex_handle = kernel32.CreateMutexW(None, True, _GUI_MUTEX_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(_gui_mutex_handle)
        _gui_mutex_handle = None
        return False
    return True


def _release_gui_mutex() -> None:
    global _gui_mutex_handle
    if _gui_mutex_handle and sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.CloseHandle(_gui_mutex_handle)
        _gui_mutex_handle = None


def _request_activate_existing() -> None:
    """Сигнал уже запущенному SubHub: показать окно (event + файл)."""
    if sys.platform != "win32":
        return
    import ctypes
    with contextlib.suppress(Exception):
        h = ctypes.windll.kernel32.CreateEventW(
            None, False, False, _ACTIVATE_EVENT_NAME,
        )
        if h:
            ctypes.windll.kernel32.SetEvent(h)
            ctypes.windll.kernel32.CloseHandle(h)
    with contextlib.suppress(Exception):
        p = _HERE / "data" / "activate.request"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()), encoding="utf-8")


def _activate_subhub_window_win32() -> bool:
    """FindWindow(SubHub) → restore + foreground. True если нашли окно."""
    if sys.platform != "win32":
        return False
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, APP_NAME)
    if not hwnd:
        hwnd = user32.FindWindowW("TkTopLevel", APP_NAME)
    if not hwnd:
        return False
    SW_RESTORE, SW_SHOW = 9, 5
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def _notify_or_activate_existing() -> None:
    """Второй клик: тихо поднять окно. MessageBox — только если окна нет."""
    _request_activate_existing()
    if _activate_subhub_window_win32():
        return
    time.sleep(0.35)
    if _activate_subhub_window_win32():
        return
    if sys.platform != "win32":
        return
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        0,
        "SubHub уже запущен, но окно не найдено.\n"
        "Проверьте иконку в системном трее.\n\n"
        "Если не помогает — диспетчер задач → завершите pythonw.exe,\n"
        "затем снова откройте SubHub.exe",
        "SubHub",
        0x40,
    )


def _kill_orphan_subhub_processes(*, keep_pid: int | None = None) -> int:
    """Убить зависшие app.py; живой второй экземпляр не трогаем (mutex)."""
    keep = os.getpid() if keep_pid is None else keep_pid
    hb = _read_app_heartbeat()
    hb_pid = int(hb.get("pid") or 0)
    hb_fresh = time.time() - float(hb.get("ts") or 0) < 120
    live_peer = hb_pid if hb_fresh and hb_pid != keep else 0

    try:
        import menu as m
        pid_alive = m._pid_alive
    except Exception:
        pid_alive = lambda _p: False  # noqa: E731

    victims = sorted({
        p for p in _collect_subhub_gui_pids()
        if p != keep and not (p == live_peer and pid_alive(p))
    })
    if victims:
        _kill_pids(victims)
        time.sleep(0.2)
    return len(victims)


def _local_repo_sha() -> str:
    """Текущий SHA репозитория (git HEAD или ._update_sha)."""
    try:
        head = _HERE / ".git" / "refs" / "heads" / "master"
        if head.exists():
            return head.read_text(encoding="utf-8").strip()
        packed = _HERE / ".git" / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if "refs/heads/master" in line and not line.startswith("#"):
                    return line.split()[0]
        sha_f = _HERE / "._update_sha"
        if sha_f.exists():
            return sha_f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _app_icon_path() -> Path | None:
    """Путь к иконке приложения: assets/app.ico или app.ico в корне проекта."""
    for rel in ("assets/app.ico", "assets/icon.ico", "app.ico"):
        p = _HERE / rel
        if p.is_file():
            return p
    return None


def _app_icon_png_path() -> Path | None:
    for rel in ("assets/subhub_icon.png", "assets/app.png", "subhub_icon.png"):
        p = _HERE / rel
        if p.is_file():
            return p
    return None


def _regenerate_app_ico() -> Path | None:
    """Пересобрать app.ico из PNG — все размеры для чёткого ярлыка на рабочем столе."""
    png = _app_icon_png_path()
    if not png:
        return _app_icon_path()
    ico = _HERE / "assets" / "app.ico"
    try:
        from PIL import Image, ImageFilter
        src = Image.open(png).convert("RGBA")
        sizes = [16, 24, 32, 48, 64, 128, 256]
        frames: list[Image.Image] = []
        for s in sizes:
            im = src.resize((s, s), Image.Resampling.LANCZOS)
            if s <= 48:
                im = im.filter(ImageFilter.UnsharpMask(radius=0.6, percent=130, threshold=1))
            frames.append(im)
        ico.parent.mkdir(parents=True, exist_ok=True)
        frames[-1].save(
            ico, format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=frames[:-1],
        )
        root_ico = _HERE / "app.ico"
        if root_ico != ico:
            import shutil
            shutil.copy2(ico, root_ico)
        return ico
    except Exception:
        return _app_icon_path()


def _hide_console_window() -> None:
    if sys.platform != "win32" or "--console" in sys.argv:
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _load_app_settings() -> dict[str, Any]:
    path = _APP_SETTINGS_PATH
    data = dict(_APP_SETTINGS_DEFAULTS)
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
    except Exception:
        pass
    return data


def _save_app_settings(data: dict[str, Any]) -> None:
    path = _APP_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(_APP_SETTINGS_DEFAULTS)
    merged.update(data)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def _launcher_path() -> Path:
    """Предпочитаем SubHub.exe в папке приложения, иначе VBS / bat."""
    exe = _HERE / "SubHub.exe"
    if exe.exists():
        return exe
    vbs = _HERE / "app_launch.vbs"
    if vbs.exists():
        return vbs
    return _HERE / "app.bat"


def _set_windows_startup(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_STARTUP_KEY, 0, winreg.KEY_SET_VALUE,
        )
        try:
            if enabled:
                launcher = _launcher_path().resolve()
                suf = launcher.suffix.lower()
                if suf == ".vbs":
                    cmd = f'wscript.exe //nologo "{launcher}"'
                else:
                    cmd = f'"{launcher}"'
                winreg.SetValueEx(key, _WIN_STARTUP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, _WIN_STARTUP_NAME)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _windows_startup_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_STARTUP_KEY, 0, winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, _WIN_STARTUP_NAME)
            return True
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _sync_windows_startup_from_settings(settings: dict[str, Any]) -> bool:
    """Применяет run_at_startup из настроек к реестру Windows."""
    want = bool(settings.get("run_at_startup"))
    have = _windows_startup_enabled()
    if want == have:
        return True
    return _set_windows_startup(want)


class LogSink:
    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()

    def write(self, text: str) -> None:
        if text:
            self._q.put(text)

    def drain(self) -> list[str]:
        items: list[str] = []
        try:
            while True:
                items.append(self._q.get_nowait())
        except queue.Empty:
            pass
        return items


_LEGACY_NAMES = ("Flipkart Automation", "Subscription Hub")


def _cleanup_legacy_branding() -> None:
    """Удаляет старые ярлыки и записи автозапуска после переименования в SubHub."""
    if sys.platform != "win32":
        return
    try:
        desktop = Path.home() / "Desktop"
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf) == 0:
                desktop = Path(buf.value)
        except Exception:
            pass
        for name in _LEGACY_NAMES:
            shortcut = desktop / f"{name}.lnk"
            if shortcut.exists():
                try:
                    shortcut.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_STARTUP_KEY, 0, winreg.KEY_SET_VALUE,
        )
        try:
            for name in _LEGACY_NAMES:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass


class SubHubApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  ·  {APP_VERSION}")
        self.geometry("1320x820")
        self.minsize(1080, 680)
        self.configure(fg_color=BG_MAIN)

        self._log_sink = LogSink()
        self._proc: subprocess.Popen | None = None
        self._proc_thread: threading.Thread | None = None
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        self._current_page = "home"
        self._current_service: str | None = None
        self._profile_rows: list[dict] = []
        self._selected_profile: dict | None = None
        self._selected_archive: dict | None = None
        self._vpn_status_lbl: ctk.CTkLabel | None = None
        self._bg_status_lbl: ctk.CTkLabel | None = None
        self._last_sync_ts: float = 0.0
        self._external_auto = False
        self._restart_requested = False
        self._quitting = False
        self._hidden_to_tray = False
        self._tray_icon = None
        self._tray_ready = False
        self._app_settings = _load_app_settings()
        self._last_update_count = -1
        self._app_start_sha = _local_repo_sha()
        self._update_in_progress = False
        self._startup_done = False
        self._ggs_orders: list[dict] = []
        self._ggs_chat_map: dict[int, str] = {}
        self._ggs_state: dict = {}
        self._ggs_filter = "new"
        self._ggs_selected_id: int | None = None
        self._ggs_loading = False
        self._ggs_refresh_pending = False
        self._ggs_pending_select: int | None = None
        self._ggs_chat_loading = False
        self._ggs_templates_win = None
        self._profile_filter = "all"
        self._prof_busy: set[str] = set()
        self._prof_labels: dict[str, str] = {}
        self._prof_stage_tick: str | None = None
        self._profiles_sig: tuple | None = None
        self._profiles_filter_sig: str | None = None
        self._refresh_jobs: dict[str, str | None] = {}
        self._run_preset_key = "full"
        self._run_log_active = False
        self._vpn_last_check = ""
        self._notif_cards: list[ctk.CTkFrame] = []
        self._notif_unread = 0
        self._home_tiles: list[ctk.CTkFrame] = []
        self._main_queue: queue.Queue[tuple[Callable, tuple, dict]] = queue.Queue()
        self._status_tick_count = 0
        self._log_file_pos = 0
        self._vpn_scan_cache: dict = {}
        self._vpn_scan_cache_ts = 0.0

        _cleanup_legacy_branding()
        self._build_layout()
        self._build_notification_layer()
        # Tk на Windows шлёт колесо виджету с фокусом — скроллим то, что под курсором
        self.bind_all("<MouseWheel>", self._on_global_wheel, add="+")
        self._apply_window_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Не глотать Tk callback exceptions молча — в лог, без падения mainloop
        self.report_callback_exception = self._tk_callback_exception  # type: ignore[method-assign]
        self.show_page("home")
        self.status_lbl.configure(text="Инициализация…")
        self._log("Запуск SubHub…")
        self._want_start_hidden = bool(
            self._app_settings.get("start_minimized")
            and self._app_settings.get("background_mode")
        )
        if not self._want_start_hidden:
            self._ensure_window_visible()
        self.after(400, self._start_activate_watcher)
        self.after(800, self._deferred_orphan_cleanup)
        self.after(1500, self._maybe_show_loading)
        # Логи и статус — сразу, не дожидаясь preflight (иначе при зависшей
        # проверке зависимостей/обновлений интерфейс вечно «Инициализация…»)
        self._start_ticks()
        threading.Thread(target=self._startup_preflight, daemon=True, name="preflight").start()
        # Страховка: если preflight завис (сеть, pip, git) — запускаем
        # бэкенд (TG-бот, GGSell-монитор) принудительно через 45 секунд
        self.after(45000, self._finish_startup)

    def _wheel_target(self, w):
        """Ближайший скроллируемый контейнер вверх по дереву от виджета.

        Только tk.Text и канвасы скролл-фреймов (_scroll_owner) — обычные
        CTk-виджеты тоже canvas'ы, но крутить их нельзя: «уезжает» отрисовка.
        """
        while w is not None:
            if isinstance(w, tk.Text):
                return w
            if isinstance(w, tk.Canvas) and hasattr(w, "_scroll_owner"):
                return w
            pc = getattr(w, "_parent_canvas", None)
            if pc is not None:
                return pc
            w = getattr(w, "master", None)
        return None

    def _on_global_wheel(self, event) -> None:
        """Tk (Windows) шлёт колесо виджету с фокусом — крутим то, что под курсором."""
        if not getattr(event, "delta", 0):
            return
        try:
            under = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        target = self._wheel_target(under)
        if target is None:
            return
        ew = getattr(event, "widget", None)
        if isinstance(ew, tk.Misc) and self._wheel_target(ew) is target:
            return  # штатный обработчик сам прокрутит этот контейнер
        with contextlib.suppress(Exception):
            if isinstance(target, tk.Text):
                target.yview_scroll(-int(event.delta / 40), "units")
            else:
                first, last = target.yview()
                if last - first < 1.0:
                    target.yview_scroll(-int(event.delta / 6), "units")

    def _window_is_iconic(self) -> bool:
        """Окно свёрнуто пользователем (кнопка «свернуть»), не в трей."""
        try:
            st = self.state()
            if isinstance(st, (tuple, list)):
                return "iconic" in st
            return str(st) == "iconic"
        except Exception:
            return False

    def _start_activate_watcher(self) -> None:
        """Повторный клик SubHub.exe → показать окно (event / файл)."""
        if getattr(self, "_activate_watch_started", False):
            return
        self._activate_watch_started = True
        self._activate_req_mtime = 0.0
        if sys.platform == "win32":
            import ctypes
            global _activate_event_handle
            with contextlib.suppress(Exception):
                _activate_event_handle = ctypes.windll.kernel32.CreateEventW(
                    None, False, False, _ACTIVATE_EVENT_NAME,
                )
        self._poll_activate_signal()

    def _deferred_orphan_cleanup(self) -> None:
        def _work() -> None:
            with contextlib.suppress(Exception):
                n = _kill_orphan_subhub_processes()
                if n:
                    self._run_on_main(self._log, f"✓ Закрыто старых процессов: {n}")
            # После перезапуска приложения автоматизация прошлого сеанса
            # (поиск номеров, браузеры) не должна продолжать работать
            with contextlib.suppress(Exception):
                k = _kill_stale_automation_processes()
                if k:
                    self._run_on_main(
                        self._log, f"✓ Остановлена автоматизация прошлого сеанса ({k} проц.)")
        threading.Thread(target=_work, daemon=True, name="orphan-cleanup").start()

    def _poll_activate_signal(self) -> None:
        if self._quitting:
            return
        hit = False
        if sys.platform == "win32" and _activate_event_handle:
            import ctypes
            WAIT_OBJECT_0 = 0
            with contextlib.suppress(Exception):
                if ctypes.windll.kernel32.WaitForSingleObject(
                    _activate_event_handle, 0,
                ) == WAIT_OBJECT_0:
                    hit = True
        req = _HERE / "data" / "activate.request"
        with contextlib.suppress(Exception):
            if req.exists():
                mtime = req.stat().st_mtime
                if mtime > getattr(self, "_activate_req_mtime", 0):
                    self._activate_req_mtime = mtime
                    hit = True
                    with contextlib.suppress(Exception):
                        req.unlink()
        if hit:
            self._show_from_tray()
        self.after(_ACTIVATE_POLL_MS, self._poll_activate_signal)

    def _maybe_show_loading(self) -> None:
        if self._startup_done:
            return
        if self._hidden_to_tray or self._window_is_iconic():
            return
        if not self.winfo_viewable():
            self._ensure_window_visible()
            self._log("⏳ Инициализация, подождите…")

    def _ensure_window_visible(self, *, force: bool = False) -> None:
        """Показать главное окно (не трогать трей и свёрнутое пользователем)."""
        if self._hidden_to_tray and not force:
            return
        if not force and self._window_is_iconic():
            return
        if self._app_settings.get("start_minimized") and not self._startup_done:
            return
        try:
            self.deiconify()
            self.lift()
            if force:
                self.focus_force()
        except Exception:
            pass

    def _startup_preflight(self) -> None:
        """Перед открытием: зависимости и обновления (каждый запуск)."""
        import menu as m

        def log(msg: str) -> None:
            self._run_on_main(self._log, msg)

        try:
            m._init_secrets()
            m._migrate_config()
            m._startup_cleanup()
        except Exception as e:
            self._run_on_main( lambda: self._log(f"⚠ Инициализация: {e}"))

        self._run_on_main( lambda: self._log("📦 Проверка зависимостей…"))
        try:
            ok, dep_msg = m.ensure_dependencies(log_fn=log)
            self._run_on_main( lambda: self._log(f"{'✓' if ok else '⚠'} {dep_msg}"))
        except Exception as e:
            self._run_on_main( lambda: self._log(f"⚠ Зависимости: {e}"))

        # Бэкенд и «SubHub готов» — сразу; проверка обновлений (сеть) уже не задерживает старт
        self._run_on_main( self._finish_startup)

        self._run_on_main( lambda: self._log("🔄 Проверка обновлений…"))
        try:
            m._check_updates_bg()
            n, commits, _, _ = self._get_update_state()
            if n:
                self._run_on_main( lambda: self._log(f"⚡ Доступно обновлений: {n}"))
                for c in commits[:3]:
                    self._run_on_main( lambda line=c: self._log(f"   • {line}"))
            else:
                self._run_on_main( lambda: self._log("✓ Версия актуальна"))
        except Exception as e:
            self._run_on_main( lambda: self._log(f"⚠ Обновления: {e}"))
        self._run_on_main( self._refresh_update_badge)

    def _start_ticks(self) -> None:
        if getattr(self, "_ticks_started", False):
            return
        self._ticks_started = True
        self._poll_main_queue()
        self._tick_logs()
        self._tick_status()

    def _finish_startup(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True
        try:
            import menu as m
            m.register_host_restart(lambda: self._run_on_main( self._restart_app))
        except Exception:
            pass
        self._bootstrap_backend()
        self._refresh_update_badge()
        self._start_ticks()
        self.after(200, self._startup_tray)
        start_hidden = (
            self._app_settings.get("start_minimized")
            and self._app_settings.get("background_mode")
        )
        if not start_hidden:
            self._ensure_window_visible()
            self.update_idletasks()
            self._apply_window_icon()
            self.after(250, self._apply_window_icon)
        self._log("✓ SubHub готов")
        self._bind_hotkeys()
        self.after(_GGS_NOTIFY_MS, self._poll_ggs_notify)
        self.after(2000, self._guard_window_visible)

    def _guard_window_visible(self) -> None:
        """Если окно пропало без явного скрытия — вернуть на экран (не из свёрнутого)."""
        if self._quitting or self._hidden_to_tray or self._window_is_iconic():
            pass
        elif (
            self._app_settings.get("start_minimized")
            and self._app_settings.get("background_mode")
            and not self._startup_done
        ):
            pass
        else:
            try:
                if not self.winfo_viewable():
                    self.deiconify()
                    self.lift()
            except Exception:
                pass
        self.after(3000, self._guard_window_visible)

    def _startup_tray(self) -> None:
        """Иконка SubHub в системном трее — нужна для фонового режима."""
        if not self._app_settings.get("background_mode", True):
            if getattr(self, "_want_start_hidden", False):
                self._ensure_window_visible()
            return
        if not self._ensure_tray():
            self._log("⚠ Трей: pip install pystray Pillow — без иконки нельзя скрыть окно")
            self._hidden_to_tray = False
            self._ensure_window_visible()
            return
        if getattr(self, "_want_start_hidden", False):
            self._hidden_to_tray = True
            self.withdraw()
            self._log("Запущено в фоне — иконка в трее, двойной клик откроет окно")

    # ── Layout ────────────────────────────────────────────────────────────────

    def _apply_window_icon(self) -> None:
        ico = _app_icon_path()
        png = _app_icon_png_path()
        if not ico and not png:
            return
        if ico:
            try:
                self.iconbitmap(str(ico))
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    self.update_idletasks()
                    _set_win32_window_icon(_win_toplevel_hwnd(self), str(ico))
                except Exception:
                    pass
        try:
            from PIL import Image, ImageTk
            src = png or ico
            if not src:
                return
            img = Image.open(src)
            photos = []
            for size in (16, 32, 48, 64, 128, 256):
                photos.append(ImageTk.PhotoImage(
                    img.resize((size, size), Image.Resampling.LANCZOS),
                ))
            self.iconphoto(True, *photos)
            self._icon_photos = photos
        except Exception:
            pass

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(
            self, width=SIDEBAR_W, corner_radius=0, fg_color=BG_SIDEBAR,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", pady=(0, 14), padx=14)

        ctk.CTkFrame(bottom, height=1, fg_color=BORDER_SUBTLE).pack(
            fill="x", pady=(0, 10),
        )
        self.status_lbl = ctk.CTkLabel(
            bottom, text="", font=_ui_font(FONT_SMALL), text_color=TEXT_DIM,
            anchor="w", justify="left",
        )
        self.status_lbl.pack(anchor="w")
        self.app_meta_lbl = ctk.CTkLabel(
            bottom,
            text=f"v{APP_VERSION}  ·  © {APP_YEAR} {APP_VENDOR}",
            font=_ui_font(FONT_SMALL), text_color=TEXT_MUTED, wraplength=210, anchor="w",
            justify="left",
        )
        self.app_meta_lbl.pack(anchor="w", pady=(4, 0))
        self._bg_status_lbl = None

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(20, 14))
        brand_row = ctk.CTkFrame(brand, fg_color="transparent")
        brand_row.pack(fill="x")
        logo_shell = ctk.CTkFrame(
            brand_row, width=44, height=44, corner_radius=14,
            fg_color=BG_SURFACE, border_width=1, border_color=ACCENT,
        )
        logo_shell.pack(side="left")
        logo_shell.pack_propagate(False)
        logo = ctk.CTkFrame(
            logo_shell, width=36, height=36, corner_radius=11,
            fg_color=ACCENT_SOFT,
        )
        logo.place(relx=0.5, rely=0.5, anchor="center")
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo, text="S", font=_display_font(16, "bold"), text_color=ACCENT,
        ).place(relx=0.5, rely=0.5, anchor="center")
        brand_txt = ctk.CTkFrame(brand_row, fg_color="transparent")
        brand_txt.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            brand_txt, text="SubHub", font=_display_font(20, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand_txt, text=APP_TAGLINE, font=_ui_font(FONT_SMALL),
            text_color=TEXT_MUTED,
        ).pack(anchor="w")

        self._vpn_status_lbl = ctk.CTkLabel(
            self.sidebar, text="Сеть…", font=_ui_font(FONT_SMALL),
            text_color=TEXT_DIM, wraplength=210,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP,
            border_width=1, border_color=BORDER_SUBTLE,
            padx=12, pady=7,
        )
        self._vpn_status_lbl.pack(fill="x", padx=14, pady=(0, 12))

        self.nav_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_container.pack(fill="x", padx=10, pady=(4, 6))

        self.sidebar_log_wrap = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_log_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 8))

        ctk.CTkLabel(
            self.sidebar_log_wrap, text="АКТИВНОСТЬ",
            font=_ui_font(FONT_SMALL, "bold"), text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", pady=(0, 6))

        self.sidebar_log = ctk.CTkTextbox(
            self.sidebar_log_wrap,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_SURFACE, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
            text_color=TEXT_MUTED, wrap="word",
            activate_scrollbars=True,
        )
        self.sidebar_log.pack(fill="both", expand=True)
        self.sidebar_log.configure(state="disabled")
        self.sidebar_log.bind("<Button-1>", lambda _e: self.show_page("logs"))

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=_PAD_PAGE, pady=_PAD_PAGE)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._build_home()
        self._build_youtube_hub()
        self._build_ggsell()
        self._build_deepseek()
        self._build_kling()
        self._build_profiles()
        self._build_archive()
        self._build_cards()
        self._build_tools()
        self._build_logs()
        self._build_settings()
        self._render_sidebar(None)

    def _nav_is_active(self, key: str, page: str) -> bool:
        if key == "youtube_hub" and page in ("youtube_hub", "run"):
            return True
        return key == page

    def _apply_nav_state(self, key: str, active: bool) -> None:
        """Единая отрисовка активного/неактивного пункта навигации."""
        btn = self._nav_btns.get(key)
        meta = getattr(self, "_nav_meta", {}).get(key)
        if btn is None or meta is None:
            return
        indicator, tile, letter, accent = meta
        btn.configure(
            fg_color=BG_NAV_ACTIVE if active else "transparent",
            text_color=TEXT_PRIMARY if active else TEXT_DIM,
            font=_ui_font(FONT_BODY, "bold" if active else "normal"),
        )
        indicator.configure(fg_color=accent if active else "transparent")
        tile.configure(
            fg_color=_lerp_hex(accent, BG_SIDEBAR, 0.8) if active else BG_SURFACE,
        )
        letter.configure(text_color=accent if active else TEXT_MUTED)

    def _render_sidebar(self, service: str | None) -> None:
        for w in self.nav_container.winfo_children():
            w.destroy()
        self._nav_btns.clear()
        self._nav_meta: dict[str, tuple] = {}
        accent = SERVICE_META.get(service or "", {}).get("accent", ACCENT)

        if service is None:
            items = [
                ("__sec__", "Разделы"),
                ("home", "Главная"),
                ("cards", "Карты"),
            ]
        elif service == "youtube":
            items = [
                ("__home__", "Назад"),
                ("__sec__", "YouTube Premium"),
                ("youtube_hub", "Обзор"),
                ("profiles", "Профили"),
                ("archive", "Архив"),
                ("cards", "Карты"),
            ]
        elif service == "ggsell":
            items = [
                ("__home__", "Назад"),
                ("__sec__", "GGSELL"),
                ("ggsell", "Заказы"),
            ]
        else:
            meta = SERVICE_META.get(service, {})
            items = [
                ("__home__", "Назад"),
                ("__sec__", meta.get("title", service)),
                (service, meta.get("title", service)),
            ]

        # Общие разделы — всегда внизу списка, своей секцией
        items += [("__sec__", "Система"), ("logs", "Логи"), ("settings", "Настройки")]

        for key, label in items:
            if key == "__sec__":
                ctk.CTkLabel(
                    self.nav_container, text=label.upper(),
                    font=_ui_font(FONT_SMALL, "bold"), text_color=TEXT_MUTED,
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(10, 2))
                continue
            is_active = self._nav_is_active(key, self._current_page)
            cmd = (lambda k=key: self._nav_click(k))
            row = ctk.CTkFrame(self.nav_container, fg_color="transparent", height=38)
            row.pack(fill="x", padx=2, pady=2)
            row.pack_propagate(False)
            indicator = ctk.CTkFrame(row, width=3, corner_radius=0, fg_color="transparent")
            indicator.pack(side="left", fill="y", padx=(0, 5), pady=7)
            # Плитка-«иконка»: первая буква раздела в тонированном квадрате
            tile = ctk.CTkFrame(
                row, width=26, height=26, corner_radius=RADIUS_SM,
                fg_color=BG_SURFACE,
            )
            tile.pack(side="left", padx=(0, 8))
            tile.pack_propagate(False)
            glyph = "←" if key == "__home__" else (label[:1].upper() or "•")
            letter = ctk.CTkLabel(
                tile, text=glyph, font=_ui_font(FONT_CAPTION, "bold"),
                text_color=TEXT_MUTED,
            )
            letter.place(relx=0.5, rely=0.5, anchor="center")
            for w in (tile, letter):
                w.bind("<Button-1>", lambda _e, k=key: self._nav_click(k))
                with contextlib.suppress(Exception):
                    w.configure(cursor="hand2")
            btn = ctk.CTkButton(
                row, text=label, anchor="w", height=34,
                corner_radius=RADIUS_SM,
                fg_color="transparent",
                hover_color=BG_CARD_HOVER,
                text_color=TEXT_DIM,
                font=_ui_font(FONT_BODY),
                command=cmd,
            )
            btn.pack(side="left", fill="both", expand=True)
            self._nav_btns[key] = btn
            self._nav_meta[key] = (indicator, tile, letter, accent)
            self._apply_nav_state(key, is_active)
            if is_active:
                _animate_color(indicator, "fg_color", BG_SURFACE, accent, steps=6)

    def _nav_click(self, key: str) -> None:
        if key == "__home__":
            self._go_home()
            return
        if key in ("profiles", "archive", "youtube_hub", "run"):
            self._current_service = "youtube"
        elif key == "cards":
            # Карты доступны и с главной, и из YouTube — контекст не ломаем
            pass
        self.show_page(key)

    def _open_cards_page(self) -> None:
        # С хаба YouTube — остаёмся в разделе YouTube
        if self._current_service != "youtube":
            self._current_service = "youtube"
        self._render_sidebar("youtube")
        self.show_page("cards")

    def _enter_service(self, service: str) -> None:
        meta = SERVICE_META.get(service, {})
        if not meta.get("ready", False):
            self.show_page(service)
            self._current_service = service
            self._render_sidebar(service)
            return
        self._current_service = service
        self._render_sidebar(service)
        hub = {"youtube": "youtube_hub", "ggsell": "ggsell"}.get(service, service)
        self.show_page(hub)

    def _go_home(self) -> None:
        self._current_service = None
        self._render_sidebar(None)
        self.show_page("home")

    def _page_header(self, parent, title: str, subtitle: str = "", accent: str = ACCENT) -> None:
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, _GAP_CARD))
        row = ctk.CTkFrame(hdr, fg_color="transparent")
        row.pack(fill="x")
        pill = ctk.CTkFrame(
            row, width=3, height=36, corner_radius=0, fg_color=BG_SURFACE,
        )
        pill.pack(side="left", padx=(0, 14))
        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            text_col, text=title, font=_display_font(FONT_TITLE, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                text_col, text=subtitle, font=_ui_font(FONT_BODY),
                text_color=TEXT_MUTED,
            ).pack(anchor="w", pady=(4, 0))
        _animate_color(pill, "fg_color", BG_SURFACE, accent, steps=10)


    def _workspace_bar(
        self, parent, title: str, *, accent: str = ACCENT, row: int | None = 0,
    ) -> ctk.CTkFrame:
        """Компактная шапка рабочей вкладки: заголовок слева, действия справа."""
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        if row is None:
            bar.pack(fill="x", pady=(0, 8))
        else:
            bar.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkFrame(
            left, width=3, height=22, corner_radius=0, fg_color=accent,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            left, text=title, font=_display_font(FONT_SECTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right")
        return right

    def _animate_accent_line(self, line: ctk.CTkFrame, accent: str) -> None:
        _animate_color(line, "fg_color", BG_SURFACE, accent, steps=8)

    def _section_title(self, parent, text: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(2, 10))
        ctk.CTkLabel(
            row, text=text.upper(), font=_ui_font(FONT_SMALL, "bold"),
            text_color=TEXT_MUTED,
        ).pack(side="left")
        rule = ctk.CTkFrame(row, height=1, fg_color=BORDER_SUBTLE)
        rule.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=6)

    def _bind_smooth_card_hover(
        self, card: ctk.CTkFrame, base: str = BG_CARD, hover: str = BG_CARD_HOVER,
    ) -> None:
        """Hover без tween — мгновенный цвет, иначе after()-цепочки вешают UI."""
        card._ui_base_color = base  # type: ignore[attr-defined]

        def _in(_e=None) -> None:
            with contextlib.suppress(Exception):
                card.configure(fg_color=hover)

        def _out(_e=None) -> None:
            with contextlib.suppress(Exception):
                card.configure(fg_color=base)

        card.bind("<Enter>", _in)
        card.bind("<Leave>", _out)

    def _animate_page_enter(self, page: ctk.CTkFrame) -> None:
        """Лёгкий вход страницы: короткий слайд, без каскадного fade карточек."""
        if getattr(page, "_static", False):
            return
        with contextlib.suppress(Exception):
            sb = getattr(page, "_scrollbar", None)
            if sb is not None:
                sb.configure(button_color=BORDER_SUBTLE, button_hover_color=ACCENT)

        self._page_anim_seq = getattr(self, "_page_anim_seq", 0) + 1
        seq = self._page_anim_seq
        slide_px, steps = 10, 4

        def slide(step: int = 0) -> None:
            if self._quitting:
                return
            try:
                if not page.winfo_exists():
                    return
            except Exception:
                return
            if seq != self._page_anim_seq:
                with contextlib.suppress(Exception):
                    page.grid_configure(pady=(0, 0))
                return
            offset = round(slide_px * (1.0 - _ease_out((step + 1) / steps)))
            with contextlib.suppress(Exception):
                page.grid(row=0, column=0, sticky="nsew", pady=(offset, 0))
            if step + 1 < steps:
                self.after(_ANIM_MS, lambda: slide(step + 1))

        slide()

    def _ghost_btn(
        self, parent, text: str, command: Callable, *,
        accent: str = TEXT_PRIMARY, width: int | None = None, anchor: str = "center",
    ) -> ctk.CTkButton:
        kw: dict[str, Any] = dict(
            text=text, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color="transparent", border_width=1, border_color=BORDER_SUBTLE,
            hover_color=BG_CARD_HOVER, text_color=TEXT_PRIMARY, command=command,
            anchor=anchor,
        )
        if width is not None:
            kw["width"] = width
        return ctk.CTkButton(parent, **kw)

    def _make_card_clickable(
        self, card: ctk.CTkFrame, on_click: Callable, accent: str,
    ) -> None:
        """Pinterest-поведение: кликабельна вся карточка, hover — подсветка рамки."""

        def _bind(w) -> None:
            with contextlib.suppress(Exception):
                w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda _e: on_click())
            for ch in w.winfo_children():
                _bind(ch)

        _bind(card)

        def _in(_e=None) -> None:
            if getattr(card, "_hov", False):
                return
            card._hov = True  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                card.configure(border_color=accent, fg_color=BG_CARD_HOVER)

        def _out(e) -> None:
            # Уход на дочерний виджет — не «выход» из карточки
            with contextlib.suppress(Exception):
                w = self.winfo_containing(e.x_root, e.y_root)
                while w is not None and w is not card:
                    w = getattr(w, "master", None)
                if w is card:
                    return
            card._hov = False  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                card.configure(border_color=BORDER_SUBTLE, fg_color=BG_CARD)

        card.bind("<Enter>", _in)
        card.bind("<Leave>", _out)

    def _pin_card(
        self, parent, *, accent: str, icon: str, title: str, subtitle: str = "",
        cover_h: int = 84, badge: str = "", arrow: bool = True,
    ) -> ctk.CTkFrame:
        """Карточка-«пин»: тонированная обложка с монограммой, текст, без кнопок."""
        card = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_PIN,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", pady=(0, _GAP_CARD))

        if cover_h > 0:
            cover = ctk.CTkFrame(
                card, height=cover_h, corner_radius=RADIUS_CARD,
                fg_color=_lerp_hex(accent, BG_MAIN, 0.90),
            )
            cover.pack(fill="x", padx=6, pady=(6, 0))
            cover.pack_propagate(False)
            ctk.CTkLabel(
                cover, text=icon, font=_display_font(22, "bold"),
                text_color=accent,
            ).place(relx=0.5, rely=0.5, anchor="center")
            if badge:
                ctk.CTkLabel(
                    cover, text=badge, font=_ui_font(FONT_SMALL, "bold"),
                    fg_color=BG_GLASS, corner_radius=RADIUS_CHIP,
                    text_color=TEXT_DIM, padx=8, pady=2,
                ).place(relx=1.0, rely=0.0, x=-10, y=10, anchor="ne")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(10, 12))
        head = ctk.CTkFrame(body, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(
            head, text=title, font=_display_font(15, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left")
        if arrow:
            ctk.CTkLabel(
                head, text="→", font=_ui_font(FONT_BODY, "bold"),
                text_color=accent,
            ).pack(side="right")
        if subtitle:
            ctk.CTkLabel(
                body, text=subtitle, font=_ui_font(FONT_CAPTION),
                text_color=TEXT_MUTED, anchor="w", justify="left", wraplength=250,
            ).pack(fill="x", pady=(2, 0))

        card.body = body  # type: ignore[attr-defined]
        self._home_tiles.append(card)
        return card

    def _pin_stat(
        self, parent, row: int, col: int, title: str, value: str,
        accent: str = ACCENT, sub: str = "",
    ) -> ctk.CTkLabel:
        """Мини-метрика внутри пин-карточки (сетка 2×2)."""
        box = ctk.CTkFrame(
            parent, fg_color=BG_ELEVATED, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        box.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        ctk.CTkLabel(
            box, text=title, text_color=TEXT_MUTED, font=_ui_font(FONT_SMALL),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(7, 0))
        lbl = ctk.CTkLabel(
            box, text=value, font=_display_font(16, "bold"), text_color=accent,
            anchor="w",
        )
        lbl.pack(fill="x", padx=10, pady=(0, 0 if sub else 7))
        if sub:
            sub_lbl = ctk.CTkLabel(
                box, text=sub, text_color=TEXT_DIM, font=_ui_font(FONT_SMALL),
                anchor="w",
            )
            sub_lbl.pack(fill="x", padx=10, pady=(0, 7))
            lbl.sub_label = sub_lbl
        return lbl

    def _page(self, name: str) -> ctk.CTkScrollableFrame:
        frame = AutoHideScrollFrame(self.content, fg_color="transparent")
        self._pages[name] = frame
        return frame

    def show_page(self, name: str) -> None:
        if name not in self._pages:
            self._log(f"Неизвестная страница: {name}")
            return
        youtube_pages = {"run", "profiles", "archive", "youtube_hub", "tools"}
        home_pages = {"home"}
        if name == "run":
            name = "youtube_hub"
        if name == "cards":
            # Карты: сайдбар главной ИЛИ YouTube — как сейчас открыт раздел
            if self._current_service == "youtube":
                self._render_sidebar("youtube")
            else:
                self._current_service = None
                self._render_sidebar(None)
        elif name in home_pages:
            if self._current_service is not None:
                self._current_service = None
                self._render_sidebar(None)
        elif name in youtube_pages and self._current_service != "youtube":
            self._current_service = "youtube"
            self._render_sidebar("youtube")
        elif name == "ggsell" and self._current_service != "ggsell":
            self._current_service = "ggsell"
            self._render_sidebar("ggsell")
        elif name in ("deepseek", "kling"):
            self._current_service = name
            self._render_sidebar(name)

        page = self._pages[name]
        prev = self._pages.get(self._current_page)
        if prev is not None and prev is not page:
            prev.grid_forget()
        page.grid(row=0, column=0, sticky="nsew", pady=(0, 0))
        self._current_page = name
        self._animate_page_enter(page)

        for k in list(self._nav_btns):
            self._apply_nav_state(k, self._nav_is_active(k, name))

        refresh = {
            "home": lambda: (self._refresh_home_ggsell(), self._refresh_update_badge()),
            "youtube_hub": lambda: (
                self._refresh_youtube_hub(), self._refresh_run_page(),
                self._sync_run_page_status(), self._refresh_update_badge(),
            ),
            "run": lambda: (
                self._refresh_youtube_hub(), self._refresh_run_page(),
                self._sync_run_page_status(),
            ),
            "ggsell": self._refresh_ggsell,
            "profiles": self._refresh_profiles,
            "cards": self._refresh_cards,
            "archive": self._refresh_archive,
            "tools": lambda: None,
            "logs": lambda: None,
            "deepseek": self._refresh_deepseek,
            "settings": lambda: (
                self._refresh_settings_keys(), self._refresh_update_badge(),
                self._sync_settings_switches(), self._refresh_grizzly_status(),
                self._sync_windows_startup(),
                self._update_grizzly_cancel_btn(False),
            ),
        }
        fn = refresh.get(name)
        if fn:
            fn()
        if name == "ggsell":
            self._notif_unread = 0
            self._update_notif_badge()
        self.after(50, self._ensure_window_visible)

    def _build_notification_layer(self) -> None:
        # Пустой CTkFrame имеет размер 200×200 по умолчанию — размещаем слой
        # только когда есть карточки уведомлений, иначе он висит тёмным
        # квадратом поверх контента.
        self._notif_layer = ctk.CTkFrame(self.content, fg_color="transparent")
        self._notif_badge = ctk.CTkLabel(
            self.sidebar, text="", font=_ui_font(FONT_SMALL, "bold"),
            fg_color=ACCENT, corner_radius=RADIUS_CHIP, text_color=TEXT_ON_ACCENT,
            padx=8, pady=2,
        )

    def _update_notif_badge(self) -> None:
        if not hasattr(self, "_notif_badge"):
            return
        if self._notif_unread > 0:
            self._notif_badge.configure(text=f"🔔 {self._notif_unread}")
            self._notif_badge.pack(after=self._vpn_status_lbl, fill="x", padx=12, pady=(0, 6))
        else:
            self._notif_badge.pack_forget()

    def _poll_ggs_notify(self) -> None:
        if self._quitting:
            return
        try:
            from ggsell.monitor import gui_notify_queue
            while True:
                try:
                    item = gui_notify_queue.get_nowait()
                except Exception:
                    break
                self._handle_ggs_notify(item)
        except Exception:
            pass
        self.after(_GGS_NOTIFY_MS, self._poll_ggs_notify)

    def _handle_ggs_notify(self, item: dict) -> None:
        kind = item.get("type") or ""
        if kind == "new_order":
            if not self._app_settings.get("notify_ggs_orders", True):
                return
            inv = int(item.get("invoice_id") or 0)
            order = item.get("order") or {}
            try:
                from ggsell.gui_orders import parse_order
                p = parse_order(order)
            except Exception:
                p = {"name_short": "Заказ", "email": "", "sum_buy": ""}
            email = item.get("buyer_email") or p.get("email") or ""
            title = f"Новый заказ #{inv}"
            parts = [str(p.get("name_short") or "YouTube Premium")]
            if email:
                parts.append(email)
            if p.get("sum_buy"):
                parts.append(f"{p['sum_buy']}₽")
            body = " · ".join(parts)
            self._push_notification(
                title, body, SVC_GGSELL,
                lambda i=inv: self._open_ggs_order(i),
            )
            self._schedule_refresh("ggsell", self._refresh_ggsell)
        elif kind == "new_message":
            if not self._app_settings.get("notify_ggs_messages", True):
                return
            inv = int(item.get("invoice_id") or 0)
            msg = item.get("message") or {}
            is_seller = bool(item.get("is_seller"))
            text = str(msg.get("text") or msg.get("message") or msg.get("body") or "").strip()
            if len(text) > 140:
                text = text[:140] + "…"
            if is_seller:
                title = f"Сообщение отправлено · #{inv}"
                accent = TEXT_MUTED
            else:
                title = f"Сообщение покупателя · #{inv}"
                accent = SVC_GGSELL
            body = text or "(без текста)"
            self._push_notification(
                title, body, accent,
                lambda i=inv: self._open_ggs_order(i),
            )
            if self._current_page == "ggsell" and self._ggs_selected_id == inv:
                self.after(300, lambda i=inv: self._ggsell_load_chat(i))
        elif kind == "ds_status":
            inv = int(item.get("invoice_id") or 0)
            text = str(item.get("text") or "").replace("`", "")
            self._push_notification(
                f"DeepSeek · заказ #{inv}" if inv else "DeepSeek",
                text, SVC_DEEPSEEK,
                (lambda i=inv: self._open_ggs_order(i)) if inv else None,
            )

    def _pulse_notif_border(self, card: ctk.CTkFrame, accent: str, step: int = 0) -> None:
        if not card.winfo_exists():
            return
        if step >= 6:
            card.configure(border_color=BORDER_SUBTLE, border_width=1)
            return
        t = abs(3 - step) / 3
        card.configure(
            border_color=_lerp_hex(accent, BORDER_SUBTLE, 1 - t),
            border_width=2 if t > 0.3 else 1,
        )
        self.after(45, lambda: self._pulse_notif_border(card, accent, step + 1))

    def _push_notification(
        self, title: str, body: str, accent: str,
        on_click: Callable | None = None,
    ) -> None:
        self._notif_unread += 1
        self._update_notif_badge()
        self._log(f"🔔 {title}: {body[:80]}")

        if not self._notif_cards:
            self._notif_layer.place(relx=1.0, rely=0.0, anchor="ne", x=-4, y=4)

        card = ctk.CTkFrame(
            self._notif_layer, fg_color=BG_CARD, corner_radius=RADIUS_CHIP,
            border_width=2, border_color=accent, width=340,
        )
        card.pack(fill="x", pady=4)
        card.pack_propagate(False)
        self._pulse_notif_border(card, accent)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=title, font=_ui_font(FONT_BODY, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            top, text="×", width=28, height=28, corner_radius=14,
            fg_color="transparent", hover_color=BG_NAV_ACTIVE,
            command=lambda c=card: self._dismiss_notification(c),
        ).pack(side="right")

        lbl = ctk.CTkLabel(
            inner, text=body, font=_ui_font(FONT_SMALL),
            text_color=TEXT_DIM, anchor="w", justify="left", wraplength=300,
        )
        lbl.pack(fill="x", pady=(4, 0))

        def _click(_e=None) -> None:
            if on_click:
                on_click()
            self._dismiss_notification(card)

        for w in (card, inner, lbl):
            w.bind("<Button-1>", _click)
            w.configure(cursor="hand2")

        self._notif_cards.append(card)
        while len(self._notif_cards) > 5:
            old = self._notif_cards.pop(0)
            self._dismiss_notification(old, count_unread=False)
        self.after(14000, lambda c=card: self._dismiss_notification(c))

        if sys.platform == "win32":
            self._try_windows_toast(title, body)

    def _toast(self, title: str, body: str, accent: str = ACCENT) -> None:
        """Короткий toast без увеличения badge (успех/ошибка операций)."""
        self._log(f"○ {title}: {body[:80]}")
        if not hasattr(self, "_notif_layer"):
            return
        if not self._notif_cards:
            self._notif_layer.place(relx=1.0, rely=0.0, anchor="ne", x=-4, y=4)
        card = ctk.CTkFrame(
            self._notif_layer, fg_color=BG_CARD, corner_radius=RADIUS_CHIP,
            border_width=1, border_color=accent, width=320,
        )
        card.pack(fill="x", pady=4)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(
            inner, text=title, font=_ui_font(FONT_BODY, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            inner, text=body, font=_ui_font(FONT_SMALL),
            text_color=TEXT_DIM, anchor="w", wraplength=290, justify="left",
        ).pack(fill="x")
        self._notif_cards.append(card)
        self.after(5000, lambda c=card: self._dismiss_notification(c, count_unread=False))

    def _bind_hotkeys(self) -> None:
        self.bind_all("<Control-r>", self._hotkey_refresh)
        self.bind_all("<Control-R>", self._hotkey_refresh)
        self.bind_all("<F5>", self._hotkey_refresh)
        self.bind_all("<Control-Shift-s>", self._hotkey_stop)
        self.bind_all("<Control-Shift-S>", self._hotkey_stop)
        self.bind_all("<Escape>", self._hotkey_escape)

    def _hotkey_refresh(self, _e=None):
        page = getattr(self, "_current_page", "")
        if page == "ggsell":
            self._refresh_ggsell()
            self._toast("Обновлено", "Заказы GGSELL", ACCENT)
        elif page in ("youtube_hub", "run", "profiles"):
            self._refresh_youtube_hub()
            if page == "run" and hasattr(self, "_refresh_run_page"):
                self._refresh_run_page()
            self._toast("Обновлено", "YouTube", ACCENT)
        elif page == "home":
            self._refresh_home_ggsell()
            self._refresh_update_badge()
        return "break"

    def _hotkey_stop(self, _e=None):
        self._stop_run()
        self._toast("Стоп", "Сценарий остановлен", WARNING)
        return "break"

    def _hotkey_escape(self, _e=None):
        while self._notif_cards:
            self._dismiss_notification(self._notif_cards[-1], count_unread=False)
        return "break"

    def _dismiss_notification(self, card: ctk.CTkFrame, count_unread: bool = True) -> None:
        try:
            if card in self._notif_cards:
                self._notif_cards.remove(card)
            card.destroy()
            if not self._notif_cards:
                self._notif_layer.place_forget()
            if count_unread and self._notif_unread > 0:
                self._notif_unread -= 1
                self._update_notif_badge()
        except Exception:
            pass

    def _try_windows_toast(self, title: str, body: str) -> None:
        try:
            import ctypes
            flags = 0x00000010  # NIIF_INFO
            ni = ctypes.c_wchar * 256
            nid = type("NOTIFY", (), {})()
            # Fallback: flash taskbar
            hwnd = self.winfo_id()
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    def _open_ggs_order(self, inv_id: int) -> None:
        self._ggs_selected_id = inv_id
        self._ggs_pending_select = inv_id
        self._ggs_filter = "all"
        for key, btn in getattr(self, "_ggs_filter_btns", {}).items():
            self._sync_chip_btn(btn, key == "all")
        self._enter_service("ggsell")
        self.show_page("ggsell")
        self._notif_unread = 0
        self._update_notif_badge()
        if hasattr(self, "_set_ggs_section"):
            self._set_ggs_section("orders")
        self._schedule_refresh("ggsell", self._refresh_ggsell)

    def _card(self, parent, title: str, accent: str | None = None) -> ctk.CTkFrame:
        shell = ctk.CTkFrame(
            parent, fg_color=BG_SURFACE, corner_radius=RADIUS_PIN,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        shell.pack(fill="x", pady=(0, _GAP_CARD))
        f = ctk.CTkFrame(
            shell, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
        f.pack(fill="x", padx=2, pady=2)
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=_PAD_CARD, pady=(16, 8))
        if accent:
            ctk.CTkFrame(
                hdr, width=6, height=6, corner_radius=3, fg_color=accent,
            ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            hdr, text=title, font=_display_font(FONT_SECTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.pack(fill="x", padx=_PAD_CARD, pady=(0, 16))
        return inner

    def _settings_block(self, parent, title: str, accent: str | None = None) -> ctk.CTkFrame:
        """Компактная карточка для страницы настроек (без лишних отступов)."""
        card = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", pady=(0, 6))
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(7, 2))
        if accent:
            ctk.CTkFrame(
                hdr, width=4, height=4, corner_radius=2, fg_color=accent,
            ).pack(side="left", padx=(0, 7))
        ctk.CTkLabel(
            hdr, text=title, font=_ui_font(FONT_CAPTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=(0, 8))
        return inner

    def _settings_btn(
        self, parent, text: str, command: Callable, *, primary: bool = False,
    ) -> ctk.CTkButton:
        return self._toolbar_btn(parent, text, command, primary=primary)

    def _chip_btn(self, parent, text: str, active: bool, command=None, accent: str = ACCENT) -> ctk.CTkButton:
        btn = ctk.CTkButton(
            parent, text=text, height=34, corner_radius=RADIUS_CHIP,
            font=_ui_font(FONT_CAPTION, "bold"),
            fg_color=accent if active else "transparent",
            border_width=0 if active else 1,
            border_color=BORDER_SUBTLE,
            hover_color=self._btn_hover(accent) if active else BG_CARD_HOVER,
            text_color=TEXT_ON_ACCENT if active else TEXT_DIM,
            command=command,
        )
        btn._chip_accent = accent  # type: ignore[attr-defined]
        return btn

    def _sync_chip_btn(self, btn: ctk.CTkButton, active: bool) -> None:
        accent = getattr(btn, "_chip_accent", ACCENT)
        btn.configure(
            fg_color=accent if active else "transparent",
            hover_color=self._btn_hover(accent) if active else BG_CARD_HOVER,
            text_color=TEXT_ON_ACCENT if active else TEXT_DIM,
            font=_ui_font(FONT_BODY, "bold" if active else "normal"),
            border_width=0 if active else 1,
            border_color=BORDER_SUBTLE,
        )

    def _toolbar_btn(
        self, parent, text: str, command: Callable, *, primary: bool = False, width: int | None = None,
    ) -> ctk.CTkButton:
        kw: dict[str, Any] = dict(
            text=text, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold" if primary else "normal"),
            command=command,
        )
        if width is not None:
            kw["width"] = width
        if primary:
            kw.update(
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                text_color=TEXT_ON_ACCENT, border_width=0,
            )
        else:
            kw.update(
                fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
                text_color=TEXT_PRIMARY, border_width=1, border_color=BORDER_SUBTLE,
            )
        return ctk.CTkButton(parent, **kw)

    def _detail_panel(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        return f

    def _bind_row_hover(self, row: ctk.CTkFrame, base: str = BG_CARD) -> None:
        """Подсветка строки списка при наведении; выделенные строки не трогаем."""
        def _in(_e=None):
            try:
                if row.cget("fg_color") == base:
                    row.configure(fg_color=BG_CARD_HOVER)
            except Exception:
                pass

        def _out(_e=None):
            try:
                if (row.cget("fg_color") == BG_CARD_HOVER
                        and not int(row.cget("border_width") or 0)):
                    row.configure(fg_color=base)
            except Exception:
                pass

        row.bind("<Enter>", _in)
        row.bind("<Leave>", _out)

    def _btn_hover(self, color: str) -> str:
        return {
            ACCENT: ACCENT_HOVER,
            SVC_YOUTUBE: "#A5F3FC",
            SVC_GGSELL: "#FED7AA",
            SVC_DEEPSEEK: "#BFDBFE",
            SVC_KLING: "#99F6E4",
            SUCCESS: SUCCESS_FG,
            BTN_SUCCESS: SUCCESS_FG,
            ERROR: "#FDA4AF",
            BTN_SECONDARY: BTN_SECONDARY_HOVER,
            TEXT_PRIMARY: ACCENT_HOVER,
        }.get(color, BTN_SECONDARY_HOVER)

    def _toolbar(self, parent) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(0, _GAP_CARD))
        return bar

    def _action_grid(self, parent, actions: list[tuple], cols: int = 2) -> ctk.CTkFrame:
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x")
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1)
        for i, item in enumerate(actions):
            txt, cmd, color = item[0], item[1], item[2]
            r, c = divmod(i, cols)
            is_primary = color in (ACCENT, BTN_SUCCESS, SVC_YOUTUBE, SVC_GGSELL, TEXT_PRIMARY)
            ctk.CTkButton(
                grid, text=txt, height=BTN_H, corner_radius=RADIUS_BTN,
                font=_ui_font(FONT_CAPTION, "bold"),
                fg_color=color if is_primary else "transparent",
                border_width=0 if is_primary else 1,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_ON_ACCENT if is_primary else TEXT_DIM,
                hover_color=self._btn_hover(color) if is_primary else BG_CARD_HOVER,
                command=cmd,
            ).grid(row=r, column=c, sticky="ew", padx=3, pady=3)
        return grid

    def _list_panel(self, parent, height: int | None = 420) -> ctk.CTkScrollableFrame:
        kw: dict[str, Any] = {
            "fg_color": BG_SURFACE, "corner_radius": RADIUS_CARD,
            "border_width": 1, "border_color": BORDER_SUBTLE,
        }
        if height is not None:
            kw["height"] = height
        return AutoHideScrollFrame(parent, **kw)

    def _page_fill(self, name: str) -> ctk.CTkFrame:
        """Страница с растягиванием списка до низа окна."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        self._pages[name] = frame
        return frame

    def _action_btn(self, parent, text: str, cmd: Callable, color: str = ACCENT, **kw) -> ctk.CTkButton:
        # Светлые заливки → тёмный текст; тёмные (secondary) → светлый.
        light_solid = color in (ACCENT, SUCCESS, BTN_SUCCESS, ERROR, WARNING, TEXT_PRIMARY)
        dark_solid = color == BTN_SECONDARY
        solid = light_solid or dark_solid
        on_fg = TEXT_ON_ACCENT if light_solid else TEXT_PRIMARY
        # tuple (light, dark) — CTk иначе может подменить цвет темы и текст «пропадает»
        fg_pair = (on_fg, on_fg)
        defaults: dict[str, Any] = dict(
            height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_CAPTION, "bold"),
            fg_color=color if solid else "transparent",
            border_width=0 if solid else 1,
            border_color=BORDER_SUBTLE,
            text_color=fg_pair if solid else (TEXT_DIM, TEXT_DIM),
            text_color_disabled=fg_pair if light_solid else (TEXT_MUTED, TEXT_MUTED),
            hover_color=self._btn_hover(color) if solid else BG_CARD_HOVER,
            command=cmd,
        )
        defaults.update(kw)
        return ctk.CTkButton(parent, text=text, **defaults)

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _stat_box(self, parent, col: int, title: str, value: str,
                  accent: str = ACCENT, sub: str = "") -> ctk.CTkLabel:
        """Компактная метрика: одна тонкая плитка, мало вертикали."""
        box = ctk.CTkFrame(
            parent, fg_color=BG_ELEVATED, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        box.grid(row=0, column=col, sticky="ew", padx=3, pady=0)
        left = ctk.CTkFrame(box, width=3, corner_radius=2, fg_color=accent)
        left.pack(side="left", fill="y", padx=(0, 0), pady=6)
        body = ctk.CTkFrame(box, fg_color="transparent")
        body.pack(side="left", fill="x", expand=True, padx=(8, 10), pady=6)
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(
            row, text=title, text_color=TEXT_MUTED, font=_ui_font(FONT_SMALL),
            anchor="w",
        ).pack(side="left")
        lbl = ctk.CTkLabel(
            row, text=value, font=_display_font(15, "bold"), text_color=TEXT_PRIMARY,
            anchor="e",
        )
        lbl.pack(side="right")
        if sub:
            sub_lbl = ctk.CTkLabel(
                body, text=sub, text_color=TEXT_DIM, font=_ui_font(FONT_SMALL),
                anchor="w",
            )
            sub_lbl.pack(anchor="w", pady=(1, 0))
            lbl.sub_label = sub_lbl
        return lbl

    def _service_row(self, parent, service: str) -> None:
        """Сервис на главной: горизонтальная карточка — обложка, текст, стрелка."""
        meta = SERVICE_META[service]
        accent = meta["accent"]
        card = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_PIN,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", pady=(0, _GAP_CARD))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=10)
        row.grid_columnconfigure(1, weight=1)

        cover = ctk.CTkFrame(
            row, width=56, height=56, corner_radius=RADIUS_CARD,
            fg_color=_lerp_hex(accent, BG_MAIN, 0.90),
        )
        cover.grid(row=0, column=0)
        cover.grid_propagate(False)
        ctk.CTkLabel(
            cover, text=meta["icon"], font=_display_font(16, "bold"),
            text_color=accent,
        ).place(relx=0.5, rely=0.5, anchor="center")

        txt = ctk.CTkFrame(row, fg_color="transparent")
        txt.grid(row=0, column=1, sticky="w", padx=(12, 0))
        ctk.CTkLabel(
            txt, text=meta["title"], font=_display_font(15, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            txt, text=meta["subtitle"], font=_ui_font(FONT_CAPTION),
            text_color=TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        ctk.CTkLabel(
            row, text="Открыть →", font=_ui_font(FONT_CAPTION, "bold"),
            text_color=accent,
        ).grid(row=0, column=2, padx=(0, 6))

        self._home_tiles.append(card)
        self._make_card_clickable(
            card, lambda s=service: self._enter_service(s), accent)

    def _build_home(self) -> None:
        """Главная: витрина GGSELL во всю ширину, ниже — сервисы по очереди.

        Статичная страница: без скролла и анимаций входа — всё помещается.
        """
        p = ctk.CTkFrame(self.content, fg_color="transparent")
        p._static = True  # type: ignore[attr-defined]
        self._pages["home"] = p
        self._home_tiles.clear()

        hero = ctk.CTkFrame(p, fg_color="transparent")
        hero.pack(fill="x", pady=(2, 12))
        ctk.CTkLabel(
            hero, text="Главная", font=_display_font(FONT_TITLE, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            hero, text="GGSELL и сервисы — карточка целиком кликабельна",
            font=_ui_font(FONT_CAPTION), text_color=TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # GGSELL — главная витрина со статистикой в один ряд
        gg_meta = SERVICE_META["ggsell"]
        gg = self._pin_card(
            p, accent=gg_meta["accent"], icon=gg_meta["icon"],
            title=gg_meta["title"], subtitle=gg_meta["subtitle"], cover_h=96,
        )
        stats = ctk.CTkFrame(gg.body, fg_color="transparent")
        stats.pack(fill="x", pady=(10, 0))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="ggs")
        self.home_ggs_orders = self._pin_stat(
            stats, 0, 0, "Выдано", "—", gg_meta["accent"], sub="сегодня: —")
        self.home_ggs_monitor = self._pin_stat(
            stats, 0, 1, "Монитор", "—", SUCCESS, sub="—")
        self.home_ggs_balance = self._pin_stat(
            stats, 0, 2, "Баланс", "—", gg_meta["accent"], sub="GGSell API")
        self.home_ggs_refunds = self._pin_stat(
            stats, 0, 3, "Возвраты", "—", ERROR)
        self._make_card_clickable(
            gg, lambda: self._enter_service("ggsell"), gg_meta["accent"])

        # Сервисы — по очереди, во всю ширину (ready=False скрыты)
        self._section_title(p, "Сервисы")
        for svc in ("youtube", "deepseek", "kling"):
            if SERVICE_META[svc].get("ready", False):
                self._service_row(p, svc)

    def _build_youtube_hub(self) -> None:
        """YouTube Premium: cinematic OLED — бренд, метрики, док запуска (без скролла)."""
        p = ctk.CTkFrame(self.content, fg_color="transparent")
        p._static = True  # type: ignore[attr-defined]
        self._pages["youtube_hub"] = p
        self._pages["run"] = p
        yt = SVC_YOUTUBE
        line = _lerp_hex(BORDER_SUBTLE, BG_CARD, 0.35)
        dock = _lerp_hex(yt, BG_SURFACE, 0.93)
        mono = ctk.CTkFont(family="Consolas", size=18, weight="bold")
        mono_sm = ctk.CTkFont(family="Consolas", size=FONT_CAPTION)

        def _rule(parent) -> None:
            ctk.CTkFrame(parent, height=1, fg_color=line, corner_radius=0).pack(fill="x")

        shell = ctk.CTkFrame(
            p, fg_color=BG_SURFACE, corner_radius=RADIUS_PIN,
            border_width=1, border_color=_lerp_hex(BORDER_SUBTLE, BG_MAIN, 0.25),
        )
        shell.pack(fill="both", expand=True)
        ctk.CTkFrame(shell, height=2, fg_color=yt, corner_radius=0).pack(fill="x")

        # ── Brand hero ──────────────────────────────────────────────────────
        hero = ctk.CTkFrame(shell, fg_color="transparent")
        hero.pack(fill="x", padx=20, pady=(16, 12))
        left = ctk.CTkFrame(hero, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        brand = ctk.CTkFrame(left, fg_color="transparent")
        brand.pack(anchor="w")
        ctk.CTkLabel(
            brand, text="YouTube", font=_display_font(26, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")
        ctk.CTkLabel(
            brand, text=" Premium", font=_display_font(26, "bold"),
            text_color=yt,
        ).pack(side="left")
        right = ctk.CTkFrame(hero, fg_color="transparent")
        right.pack(side="right")
        self.run_stat_state = ctk.CTkLabel(
            right, text="ГОТОВ", font=_ui_font(FONT_SMALL, "bold"),
            text_color=SUCCESS, fg_color=_lerp_hex(SUCCESS, BG_SURFACE, 0.88),
            corner_radius=RADIUS_SM, border_width=1,
            border_color=_lerp_hex(SUCCESS, line, 0.4),
            padx=12, pady=5,
        )
        self.run_stat_state.pack(side="left", padx=(0, 10))
        self.run_stat_state.sub_label = self.run_stat_state
        self._ghost_btn(right, "Карты", self._open_cards_page, width=72).pack(
            side="left", padx=(0, 6),
        )
        self._ghost_btn(
            right, "Профили", lambda: self.show_page("profiles"), width=84,
        ).pack(side="left")

        _rule(shell)

        # ── Metrics ticker ──────────────────────────────────────────────────
        ticker = ctk.CTkFrame(shell, fg_color="transparent")
        ticker.pack(fill="x", padx=12, pady=10)
        ticker.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="ytk")

        def _tick(col: int, label: str, accent: str) -> ctk.CTkLabel:
            cell = ctk.CTkFrame(ticker, fg_color="transparent")
            cell.grid(row=0, column=col, sticky="nsew", padx=8)
            if col > 0:
                ctk.CTkFrame(
                    cell, width=1, height=36, fg_color=line, corner_radius=0,
                ).place(x=0, rely=0.5, anchor="w")
            inner = ctk.CTkFrame(cell, fg_color="transparent")
            inner.pack(fill="x", padx=(10 if col else 4, 4))
            ctk.CTkLabel(
                inner, text=label, font=_ui_font(FONT_SMALL, "bold"),
                text_color=TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            val = ctk.CTkLabel(
                inner, text="—", font=mono, text_color=TEXT_PRIMARY, anchor="w",
            )
            val.pack(anchor="w", pady=(2, 0))
            ctk.CTkFrame(
                inner, width=16, height=2, fg_color=accent, corner_radius=1,
            ).pack(anchor="w", pady=(6, 0))
            return val

        self.dash_profiles = _tick(0, "ПРОФИЛИ", yt)
        self.dash_cards = _tick(1, "КАРТЫ", ACCENT)
        self.dash_gift = _tick(2, "ГИФТ", WARNING)
        self.dash_tg = _tick(3, "TELEGRAM", SUCCESS)

        _rule(shell)

        # ── Body: сценарий на всю ширину ────────────────────────────────────
        body = ctk.CTkFrame(shell, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(12, 8))

        controls = ctk.CTkFrame(body, fg_color="transparent")
        controls.pack(fill="both", expand=True)

        ctk.CTkLabel(
            controls, text="СЦЕНАРИЙ", font=_ui_font(FONT_SMALL, "bold"),
            text_color=TEXT_MUTED, anchor="w",
        ).pack(fill="x", pady=(0, 6))

        preset_row = ctk.CTkFrame(controls, fg_color="transparent")
        preset_row.pack(fill="x")
        self._run_preset_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("full", "Полный"),
            ("payment", "До оплаты"),
            ("login_pc", "Вход ПК"),
            ("tg_intercept", "Telegram"),
            ("email", "До email"),
        ):
            btn = self._chip_btn(
                preset_row, label, key == "full",
                lambda k=key: self._select_run_preset(k), accent=yt,
            )
            btn.configure(height=28, corner_radius=RADIUS_SM)
            btn.pack(side="left", padx=(0, 5))
            self._run_preset_btns[key] = btn

        self.run_pay_chip = self.dash_pay_chip = ctk.CTkLabel(
            preset_row, text="Оплата…", font=_ui_font(FONT_SMALL, "bold"),
            fg_color=ACCENT_SOFT, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
            padx=10, pady=5, text_color=ACCENT, cursor="hand2",
        )
        self.run_pay_chip.pack(side="right")
        self.run_pay_chip.bind("<Button-1>", lambda _e: self._toggle_pay_method())

        form = ctk.CTkFrame(controls, fg_color="transparent")
        form.pack(fill="x", pady=(14, 0))
        form.grid_columnconfigure(0, weight=3)
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(2, weight=2)

        def _field(parent, label: str) -> ctk.CTkFrame:
            box = ctk.CTkFrame(parent, fg_color="transparent")
            ctk.CTkLabel(
                box, text=label, font=_ui_font(FONT_SMALL, "bold"),
                text_color=TEXT_MUTED, anchor="w",
            ).pack(fill="x", pady=(0, 4))
            return box

        f_mode = _field(form, "РЕЖИМ")
        f_mode.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.run_mode = ctk.CTkComboBox(
            f_mode, height=34, font=_ui_font(FONT_CAPTION), state="readonly",
            values=[
                "Полный цикл (вход + покупка)",
                "До оплаты (существующий профиль)",
                "Только вход на ПК",
                "Вход + Telegram (перехват)",
                "Вход с данными (до email)",
            ],
            command=lambda _v: self._on_run_param_change(),
            fg_color=BG_CARD, border_color=BORDER_SUBTLE,
            button_color=BG_ELEVATED, button_hover_color=BG_CARD_HOVER,
        )
        self.run_mode.pack(fill="x")
        self.run_mode.set("Полный цикл (вход + покупка)")

        f_n = _field(form, "N")
        f_n.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.run_accounts = ctk.CTkEntry(
            f_n, height=34, font=mono_sm, placeholder_text="авто",
            fg_color=BG_CARD, border_color=BORDER_SUBTLE,
        )
        self.run_accounts.pack(fill="x")
        self.run_accounts.bind("<KeyRelease>", lambda _e: self._on_run_param_change())

        f_tar = _field(form, "ТАРИФ")
        f_tar.grid(row=0, column=2, sticky="ew")
        self.run_tariff = ctk.CTkComboBox(
            f_tar, height=34, font=_ui_font(FONT_CAPTION), state="readonly",
            values=["3 месяца (₹343)", "12 месяцев (₹1,499)"],
            command=lambda _v: self._on_run_param_change(),
            fg_color=BG_CARD, border_color=BORDER_SUBTLE,
            button_color=BG_ELEVATED, button_hover_color=BG_CARD_HOVER,
        )
        self.run_tariff.pack(fill="x")
        self.run_tariff.set("3 месяца (₹343)")

        self.run_headless = ctk.CTkCheckBox(
            controls, text="Фоновый режим", font=_ui_font(FONT_CAPTION),
            command=self._on_run_param_change, width=150,
            fg_color=yt, hover_color=self._btn_hover(yt),
            checkmark_color=TEXT_ON_ACCENT,
        )
        self.run_headless.pack(anchor="w", pady=(12, 0))

        # ── Launch dock — нижняя полоса (не сайдбар) ─────────────────────────
        dock_f = ctk.CTkFrame(
            shell, fg_color=dock, corner_radius=RADIUS_CARD,
            border_width=1, border_color=_lerp_hex(yt, line, 0.55),
        )
        dock_f.pack(fill="x", padx=16, pady=(0, 8))
        di = ctk.CTkFrame(dock_f, fg_color="transparent")
        di.pack(fill="x", padx=14, pady=10)

        left_dock = ctk.CTkFrame(di, fg_color="transparent")
        left_dock.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            left_dock, text="ЗАПУСК", font=_ui_font(FONT_SMALL, "bold"),
            text_color=yt, anchor="w",
        ).pack(anchor="w")

        st = ctk.CTkFrame(left_dock, fg_color="transparent")
        st.pack(fill="x", pady=(6, 0))
        self.run_status_dot = ctk.CTkFrame(
            st, width=8, height=8, corner_radius=4, fg_color=SUCCESS,
        )
        self.run_status_dot.pack(side="left", padx=(0, 8))
        self.run_status = ctk.CTkLabel(
            st, text="Готов к запуску", text_color=TEXT_DIM,
            font=_ui_font(FONT_CAPTION, "bold"), anchor="w",
        )
        self.run_status.pack(side="left", fill="x", expand=True)

        self.run_progress = ctk.CTkProgressBar(
            left_dock, height=3, corner_radius=2, mode="indeterminate",
            progress_color=yt,
        )
        self.run_progress.pack(fill="x", pady=(8, 0))
        self.run_progress.pack_forget()

        btns = ctk.CTkFrame(di, fg_color="transparent")
        btns.pack(side="right", padx=(12, 0))
        self.run_start_btn = ctk.CTkButton(
            btns, text="Запустить", height=40, width=140,
            corner_radius=RADIUS_BTN, font=_display_font(15, "bold"),
            fg_color=yt, hover_color=self._btn_hover(yt),
            text_color=TEXT_ON_ACCENT, command=self._start_run,
        )
        self.run_start_btn.pack(side="left")
        self.run_stop_btn = self._action_btn(
            btns, "Стоп", self._stop_run, color=ERROR, height=40, width=88,
        )
        self.run_stop_btn.configure(state="disabled")
        self.run_stop_btn.pack(side="left", padx=(8, 0))

        self.run_stat_profiles = self.dash_profiles
        self.run_stat_vpn = ctk.CTkLabel(p, text="")
        self.run_stat_balance = ctk.CTkLabel(p, text="")

        _rule(shell)

        # ── Footer: только действия + баланс ────────────────────────────────
        sr = ctk.CTkFrame(shell, fg_color="transparent")
        sr.pack(fill="x", padx=16, pady=(8, 12))
        # скрытый чип — обновляется фоном, в сайдбаре уже есть VPN
        self.dash_vpn_chip = ctk.CTkLabel(sr, text="")
        self._ghost_btn(
            sr, "Отменить номера", self._cancel_grizzly_numbers_ui, width=128,
        ).pack(side="left")
        self._ghost_btn(
            sr, "Удалить tmp", self._purge_temp_profiles_ui, width=96,
        ).pack(side="left", padx=(8, 0))
        self.dash_balance = ctk.CTkLabel(
            sr, text="GrizzlySMS…", font=mono_sm, text_color=TEXT_DIM,
        )
        self.dash_balance.pack(side="right")

        self._update_run_cmd_preview()

    def _build_ggsell(self) -> None:
        p = self._page_fill("ggsell")
        # Slim tab bar (row 0) + one fullscreen content pane (row 1)
        p.grid_rowconfigure(2, weight=0)
        p.grid_rowconfigure(3, weight=0)
        p.grid_rowconfigure(1, weight=1)

        self._ggs_section = "orders"
        self._ggs_section_btns: dict[str, ctk.CTkButton] = {}

        nav = ctk.CTkFrame(p, fg_color="transparent")
        nav.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        left = ctk.CTkFrame(nav, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            left, text="GGSELL", font=_display_font(FONT_SECTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left", padx=(0, 14))
        for key, label in (
            ("overview", "Обзор"),
            ("orders", "Заказы"),
            ("monitor", "Мониторинг"),
            ("delivery", "Доставка"),
        ):
            btn = self._chip_btn(
                left, label, key == "orders",
                lambda k=key: self._set_ggs_section(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._ggs_section_btns[key] = btn
        self._toolbar_btn(nav, "Обновить", self._refresh_ggsell, width=96).pack(side="right")

        # ── Обзор: панель управления (скрыта на рабочих вкладках) ───────────
        self.ggs_dash = ctk.CTkFrame(p, fg_color="transparent")
        self.ggs_dash.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(self.ggs_dash, fg_color="transparent")
        hdr.pack(fill="x")
        self._page_header(
            hdr, "GGSELL",
            "Панель управления · статистика и быстрые действия",
            ACCENT,
        )

        stats = ctk.CTkFrame(self.ggs_dash, fg_color="transparent")
        stats.pack(fill="x", pady=(0, _GAP_CARD))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.ggs_stat_orders = self._stat_box(
            stats, 0, "Выдано", "—", ACCENT)
        self.ggs_stat_balance = self._stat_box(
            stats, 1, "Баланс", "—", ACCENT)
        self.ggs_stat_monitor = self._stat_box(
            stats, 2, "Монитор", "—", ACCENT)
        self.ggs_stat_api = self._stat_box(
            stats, 3, "API", "—", ACCENT)

        actions = self._card(self.ggs_dash, "Управление", accent=ACCENT)
        actions.pack(fill="x")
        self._action_grid(actions, [
            ("Обновить", self._refresh_ggsell, ACCENT),
            ("API-ключи", self._open_secrets, BTN_SECONDARY),
            ("data/", lambda: self._open_folder("data"), BTN_SECONDARY),
            ("Шаблоны", self._open_ggsell_templates, BTN_SECONDARY),
        ])

        # ── Заказы + чат (fullscreen) ───────────────────────────────────────
        self.ggs_orders_wrap = ctk.CTkFrame(p, fg_color="transparent")
        self.ggs_orders_wrap.grid_rowconfigure(0, weight=1)
        self.ggs_orders_wrap.grid_columnconfigure(0, weight=1)

        orders_outer = ctk.CTkFrame(
            self.ggs_orders_wrap, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        orders_outer.grid(row=0, column=0, sticky="nsew")
        orders_outer.grid_rowconfigure(3, weight=1)
        orders_outer.grid_columnconfigure(0, weight=1)

        orders_hdr = ctk.CTkFrame(orders_outer, fg_color="transparent")
        orders_hdr.grid(row=0, column=0, sticky="ew", padx=_PAD_CARD, pady=(14, 4))
        dot = ctk.CTkFrame(orders_hdr, width=6, height=6, corner_radius=3, fg_color=ACCENT)
        dot.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            orders_hdr, text="Заказы", font=_ui_font(FONT_SECTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")

        orders_card = ctk.CTkFrame(orders_outer, fg_color="transparent")
        orders_card.grid(row=1, column=0, sticky="ew", padx=_PAD_CARD)
        filt_row = ctk.CTkFrame(orders_card, fg_color="transparent")
        filt_row.pack(fill="x", pady=(0, 4))
        self._ggs_filter_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("new", "Новые"),
            ("issued", "Выданные"),
            ("used", "Архив"),
            ("all", "Все"),
        ):
            btn = self._chip_btn(
                filt_row, label, key == "new",
                lambda k=key: self._set_ggsell_filter(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._ggs_filter_btns[key] = btn

        self.ggs_orders_status = ctk.CTkLabel(
            orders_outer, text="Загрузка…", text_color=TEXT_DIM, anchor="w",
        )
        self.ggs_orders_status.grid(row=2, column=0, sticky="ew", padx=_PAD_CARD, pady=(0, 6))

        body = ctk.CTkFrame(orders_outer, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=_PAD_CARD, pady=(0, 12))
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)

        self.ggs_orders_list = AutoHideScrollFrame(
            body, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.ggs_orders_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.ggs_order_detail = self._detail_panel(body)
        self.ggs_order_detail.grid(row=0, column=1, sticky="nsew")
        self.ggs_order_detail.grid_columnconfigure(0, weight=1)
        self.ggs_order_detail.grid_rowconfigure(2, weight=1)

        detail_hdr = ctk.CTkFrame(self.ggs_order_detail, fg_color="transparent")
        detail_hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        detail_hdr.grid_columnconfigure(0, weight=1)
        self.ggs_detail_title = ctk.CTkLabel(
            detail_hdr, text="Выберите заказ",
            font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY, anchor="w",
        )
        self.ggs_detail_title.grid(row=0, column=0, sticky="w")
        self.ggs_btn_chat_refresh = self._toolbar_btn(
            detail_hdr, "Чат", self._ggsell_refresh_chat, width=72,
        )
        self.ggs_btn_chat_refresh.configure(state="disabled")
        self.ggs_btn_chat_refresh.grid(row=0, column=1, padx=(8, 0))
        self.ggs_btn_seller = self._toolbar_btn(
            detail_hdr, "GGSell", self._ggsell_open_seller, width=80,
        )
        self.ggs_btn_seller.configure(state="disabled")
        self.ggs_btn_seller.grid(row=0, column=2, padx=(6, 0))

        self.ggs_detail_meta = ctk.CTkLabel(
            self.ggs_order_detail, text="—",
            justify="left", anchor="nw", text_color=TEXT_DIM,
            font=_ui_font(FONT_BODY), wraplength=440,
        )
        self.ggs_detail_meta.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        self.ggs_chat_scroll = AutoHideScrollFrame(
            self.ggs_order_detail, fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.ggs_chat_scroll.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self.ggs_chat_placeholder = ctk.CTkLabel(
            self.ggs_chat_scroll, text="Выберите заказ слева",
            text_color=TEXT_MUTED, font=_ui_font(FONT_BODY),
        )
        self.ggs_chat_placeholder.pack(pady=10)

        self.ggs_chat_status = ctk.CTkLabel(
            self.ggs_order_detail, text="", text_color=TEXT_MUTED,
            font=_ui_font(FONT_SMALL), anchor="w",
        )
        self.ggs_chat_status.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 2))

        chat_input = ctk.CTkFrame(self.ggs_order_detail, fg_color="transparent")
        chat_input.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        chat_input.grid_columnconfigure(1, weight=1)
        self.ggs_btn_templates = self._toolbar_btn(
            chat_input, "Шаблоны", self._ggsell_open_templates_modal, width=90,
        )
        self.ggs_btn_templates.configure(state="disabled")
        self.ggs_btn_templates.grid(row=0, column=0, padx=(0, 6))
        self.ggs_chat_input = ctk.CTkTextbox(
            chat_input, height=BTN_H_MD, corner_radius=RADIUS_BTN,
            fg_color=BG_MAIN, border_width=1, border_color=BORDER_SUBTLE,
            font=_ui_font(FONT_BODY), wrap="word",
        )
        self.ggs_chat_input.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.ggs_chat_input.configure(state="disabled")
        self.ggs_chat_input.bind("<Return>", self._ggsell_chat_return)
        self.ggs_chat_input.bind("<Shift-Return>", lambda _e: None)
        self.ggs_btn_chat_send = self._action_btn(
            chat_input, "Отправить", self._ggsell_send_chat, ACCENT, width=110,
        )
        self.ggs_btn_chat_send.configure(state="disabled")
        self.ggs_btn_chat_send.grid(row=0, column=2)
        self._ggs_templates_win = None

        # ── Мониторинг / доставка (fullscreen) ──────────────────────────────
        self.ggs_monitor_wrap = ctk.CTkFrame(p, fg_color="transparent")
        mon_card = self._card(self.ggs_monitor_wrap, "Мониторинг заказов", accent=ACCENT)
        self.ggs_monitor_body = ctk.CTkLabel(
            mon_card, text="—", justify="left", anchor="nw",
            text_color=TEXT_DIM, font=_ui_font(FONT_BODY),
        )
        self.ggs_monitor_body.pack(fill="both", expand=True, padx=4, pady=8)
        mon_btns = ctk.CTkFrame(mon_card, fg_color="transparent")
        mon_btns.pack(fill="x", pady=(8, 0))
        self._action_grid(mon_btns, [
            ("Включить", self._ggs_monitor_start_ui, ACCENT),
            ("Выключить", self._ggs_monitor_stop_ui, ERROR),
            ("Обновить статус", self._refresh_ggs_monitor_panel, BTN_SECONDARY),
            ("Настройки", lambda: self.show_page("settings"), BTN_SECONDARY),
        ], cols=2)

        self.ggs_delivery_wrap = ctk.CTkFrame(p, fg_color="transparent")
        del_card = self._card(self.ggs_delivery_wrap, "Доставка ссылок", accent=ACCENT)
        ctk.CTkLabel(
            del_card,
            text="Авто-выдача работает через монитор: новый заказ → шаблон → ссылка в чат.\n"
                 "Правьте тексты и проверяйте выдачу во вкладке «Заказы».",
            justify="left", anchor="nw", text_color=TEXT_DIM, font=_ui_font(FONT_BODY),
        ).pack(fill="x", padx=4, pady=8)
        self.ggs_delivery_preview = ctk.CTkLabel(
            del_card, text="", justify="left", anchor="nw",
            text_color=TEXT_MUTED, font=_ui_font(FONT_SMALL),
        )
        self.ggs_delivery_preview.pack(fill="x", padx=4, pady=(0, 8))
        del_btns = ctk.CTkFrame(del_card, fg_color="transparent")
        del_btns.pack(fill="x", pady=(8, 0))
        self._action_grid(del_btns, [
            ("Шаблоны", self._open_ggsell_templates, ACCENT),
            ("Превью шаблонов", self._ggs_delivery_preview_templates, BTN_SECONDARY),
            ("API-ключи", self._open_secrets, BTN_SECONDARY),
            ("К заказам", lambda: self._set_ggs_section("orders"), BTN_SECONDARY),
        ])

        self._ggs_section_frames = {
            "overview": self.ggs_dash,
            "orders": self.ggs_orders_wrap,
            "monitor": self.ggs_monitor_wrap,
            "delivery": self.ggs_delivery_wrap,
        }
        self._set_ggs_section("orders")

    def _set_ggs_section(self, section: str) -> None:
        """Переключение вкладок GGSELL: панель управления только в «Обзор»."""
        if section not in getattr(self, "_ggs_section_frames", {}):
            section = "orders"
        self._ggs_section = section
        for key, btn in getattr(self, "_ggs_section_btns", {}).items():
            self._sync_chip_btn(btn, key == section)
        for key, frame in self._ggs_section_frames.items():
            frame.grid_forget()
        frame = self._ggs_section_frames[section]
        frame.grid(row=1, column=0, sticky="nsew")
        if section == "monitor":
            self._refresh_ggs_monitor_panel()
        elif section == "delivery":
            self._ggs_delivery_preview_templates()
        elif section == "orders":
            self._render_ggsell_orders()

    def _refresh_ggs_monitor_panel(self) -> None:
        if not hasattr(self, "ggs_monitor_body"):
            return
        on = self._ggs_monitor_active()
        want = self._ggs_monitor_wanted()
        bg = bool(self._app_settings.get("background_mode", True))
        st = self._ggs_home_stats()
        hint = ""
        if not bg:
            hint = "Включите «Фоновый режим» в Настройках.\n"
        elif not want:
            hint = "Нужен валидный GGSell API-ключ и seller_id в secrets.yaml.\n"
        elif want and not on:
            hint = "API есть — нажмите «Включить» или перезапустите фоновый режим.\n"
        self.ggs_monitor_body.configure(
            text=(
                f"Статус: {'Активен' if on else 'Выключен'}\n\n"
                f"Выдано всего: {st.get('done', 0)}\n"
                f"Сегодня: {st.get('today', 0)}\n"
                f"Возвраты: {st.get('refunded', 0)}\n\n"
                f"{hint}"
                "Новые заказы и сообщения приходят в тосты / уведомления."
            ),
            text_color=SUCCESS if on else TEXT_DIM,
        )

    def _ggs_monitor_start_ui(self) -> None:
        if not self._app_settings.get("background_mode", True):
            self._app_settings["background_mode"] = True
            self._persist_app_settings()
            if hasattr(self, "sw_background"):
                self.sw_background.select()
        self._sync_ggs_monitor()
        self._refresh_ggs_monitor_panel()
        self._refresh_ggsell()
        on = self._ggs_monitor_active()
        self._toast(
            "Монитор GGSELL",
            "Активен — следит за заказами" if on else "Не удалось запустить (проверьте API)",
            SUCCESS if on else WARNING,
        )

    def _ggs_monitor_stop_ui(self) -> None:
        from ggsell.monitor import is_monitor_running, stop_monitor
        if is_monitor_running():
            stop_monitor()
        self._refresh_ggs_monitor_panel()
        self._refresh_ggsell()
        self._toast("Монитор GGSELL", "Выключен", TEXT_DIM)

    def _ggs_delivery_preview_templates(self) -> None:
        if not hasattr(self, "ggs_delivery_preview"):
            return
        try:
            from ggsell.monitor import get_template
            lines = []
            for key, (title, _) in list(_GGS_TEMPLATE_META.items())[:4]:
                raw = (get_template(key) or "").strip().replace("\n", " ")
                if len(raw) > 90:
                    raw = raw[:90] + "…"
                lines.append(f"{title}: {raw or '(пустой)'}")
            self.ggs_delivery_preview.configure(
                text="\n".join(lines) or "Шаблоны не найдены",
            )
        except Exception as e:
            self.ggs_delivery_preview.configure(text=f"Ошибка чтения шаблонов: {e}")

    def _build_deepseek(self) -> None:
        p = self._page("deepseek")
        self._workspace_bar(p, "DeepSeek", accent=ACCENT, row=None)

        form = self._card(p, "Пополнение", accent=ACCENT)
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x")
        inner.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(inner, text="Вход", text_color=TEXT_DIM, font=_ui_font(FONT_BODY)).grid(row=0, column=0, sticky="w", pady=6)
        self.ds_login_method = ctk.CTkSegmentedButton(
            inner, values=["Почта и пароль", "Google"], height=BTN_H,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=BG_SURFACE,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_SURFACE, unselected_hover_color=BG_NAV_ACTIVE,
            command=self._ds_method_changed,
        )
        self.ds_login_method.set("Почта и пароль")
        self.ds_login_method.grid(row=0, column=1, sticky="w", pady=6, padx=(12, 0))

        ctk.CTkLabel(inner, text="Email", text_color=TEXT_DIM, font=_ui_font(FONT_BODY)).grid(row=1, column=0, sticky="w", pady=6)
        self.ds_email = ctk.CTkEntry(inner, height=BTN_H, font=_ui_font(FONT_BODY), placeholder_text="Почта аккаунта DeepSeek")
        self.ds_email.grid(row=1, column=1, sticky="ew", pady=6, padx=(12, 0))

        ctk.CTkLabel(inner, text="Пароль", text_color=TEXT_DIM, font=_ui_font(FONT_BODY)).grid(row=2, column=0, sticky="w", pady=6)
        self.ds_password = ctk.CTkEntry(inner, height=BTN_H, font=_ui_font(FONT_BODY), placeholder_text="Пароль", show="•")
        self.ds_password.grid(row=2, column=1, sticky="ew", pady=6, padx=(12, 0))

        ctk.CTkLabel(inner, text="Сумма, $", text_color=TEXT_DIM, font=_ui_font(FONT_BODY)).grid(row=3, column=0, sticky="w", pady=6)
        self.ds_amount = ctk.CTkEntry(inner, width=140, height=BTN_H, font=_ui_font(FONT_BODY), placeholder_text="напр. 2")
        self.ds_amount.grid(row=3, column=1, sticky="w", pady=6, padx=(12, 0))

        ctk.CTkLabel(inner, text="Карта", text_color=TEXT_DIM, font=_ui_font(FONT_BODY)).grid(row=4, column=0, sticky="w", pady=6)
        self.ds_card_box = ctk.CTkComboBox(
            inner, height=BTN_H, font=_ui_font(FONT_BODY), values=["—"], state="readonly",
        )
        self.ds_card_box.grid(row=4, column=1, sticky="ew", pady=6, padx=(12, 0))

        # Enter в любом поле формы запускает пополнение
        for _w in (self.ds_email, self.ds_password, self.ds_amount):
            _w.bind("<Return>", lambda _e: self._ds_topup_clicked())

        self.ds_btn = self._action_btn(form, "Пополнить", self._ds_topup_clicked, ACCENT)
        self.ds_btn.pack(fill="x", pady=(10, 0))

        log_card = self._card(p, "Ход выполнения", accent=ACCENT)
        self.ds_log = ctk.CTkTextbox(
            log_card, height=240,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE, wrap="word",
        )
        self.ds_log.pack(fill="x")
        self.ds_log.configure(state="disabled")

        self._ds_cards: list[dict] = []
        self._ds_card_labels: list[str] = []
        self._ds_running = False

    def _refresh_deepseek(self) -> None:
        import menu as m
        cards = m._load_cards()
        order = m._load_card_order()
        if order:
            idx = [i for i in order if 0 <= i < len(cards)]
            idx += [i for i in range(len(cards)) if i not in idx]
            cards = [cards[i] for i in idx]
        self._ds_cards = cards
        self._ds_card_labels = [
            f"{c.get('nickname') or c.get('name') or f'Карта {i + 1}'} · {m._mask_card(c.get('number', ''))}"
            for i, c in enumerate(cards)
        ]
        values = self._ds_card_labels or ["Нет карт"]
        cur = self.ds_card_box.get()
        self.ds_card_box.configure(values=values)
        if cur not in values:
            self.ds_card_box.set(values[0])

    def _ds_log_line(self, text: str) -> None:
        self._append_log_widget(self.ds_log, text.rstrip() + "\n")

    def _ds_method_changed(self, value: str) -> None:
        try:
            if value == "Google":
                self.ds_email.configure(placeholder_text="Почта Google-аккаунта")
                self.ds_password.configure(
                    placeholder_text="Пароль Google (можно пусто — вход вручную)")
            else:
                self.ds_email.configure(placeholder_text="Почта аккаунта DeepSeek")
                self.ds_password.configure(placeholder_text="Пароль")
        except Exception:
            pass

    def _ds_topup_clicked(self) -> None:
        if self._ds_running:
            return
        email = self.ds_email.get().strip()
        password = self.ds_password.get()
        method = "google" if self.ds_login_method.get() == "Google" else "password"
        if not email or (not password and method != "google"):
            messagebox.showwarning("DeepSeek", "Укажите email и пароль аккаунта DeepSeek")
            return
        raw = self.ds_amount.get().strip().replace(",", ".").lstrip("$")
        try:
            amount = float(raw)
        except ValueError:
            amount = 0.0
        if amount <= 0:
            messagebox.showwarning("DeepSeek", "Укажите сумму пополнения в долларах (например 2)")
            return
        if not self._ds_cards:
            self._refresh_deepseek()
        if not self._ds_cards:
            messagebox.showwarning("DeepSeek", "Нет сохранённых карт — добавьте карту в разделе «Карты»")
            return
        try:
            card = self._ds_cards[self._ds_card_labels.index(self.ds_card_box.get())]
        except ValueError:
            card = self._ds_cards[0]

        self._ds_running = True
        self.ds_btn.configure(state="disabled", text="Выполняется…")
        try:
            self.ds_log.configure(state="normal")
            self.ds_log.delete("1.0", "end")
            self.ds_log.configure(state="disabled")
        except Exception:
            pass
        self._ds_log_line(
            f"Пополнение ${amount:g} → {email}"
            + (" (вход через Google)" if method == "google" else "")
            + " · при отказе Stripe — следующая карта")

        def _w():
            try:
                import deepseek as ds
                ok, msg = asyncio.run(ds.topup(
                    email, password, amount, card,
                    login_method=method,
                    retry_cards=True,
                    log=lambda s: self._run_on_main( self._ds_log_line, str(s)),
                ))
            except Exception as e:
                ok, msg = False, f"Ошибка: {e}"
            self._run_on_main( self._ds_topup_done, ok, msg)

        threading.Thread(target=_w, daemon=True, name="ds-topup").start()

    def _ds_topup_done(self, ok: bool, msg: str) -> None:
        self._ds_running = False
        self.ds_btn.configure(state="normal", text="Пополнить")
        self._ds_log_line(msg)
        self._log(f"DeepSeek: {msg}")
        if ok:
            self._toast("DeepSeek", msg, SUCCESS)
        else:
            self._toast("DeepSeek", msg, ERROR)
            messagebox.showerror("DeepSeek", msg)

    def _build_kling(self) -> None:
        p = self._page("kling")
        self._build_coming_soon(p, "kling")

    def _build_coming_soon(self, p: ctk.CTkScrollableFrame, service: str) -> None:
        meta = SERVICE_META[service]
        self._workspace_bar(p, meta["title"], accent=meta["accent"], row=None)
        card = ctk.CTkFrame(
            p, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=_PAD_CARD, pady=20)
        dot = ctk.CTkFrame(inner, width=12, height=12, corner_radius=6, fg_color=meta["accent"])
        dot.pack(side="left", padx=(0, 14))
        txt = ctk.CTkFrame(inner, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            txt, text="Скоро", font=_ui_font(22, "bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            txt, text=f"{meta['title']} появится в следующих обновлениях",
            font=_ui_font(FONT_BODY), text_color=TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 0))
        self._ghost_btn(p, "На главную", self._go_home, accent=ACCENT, width=140).pack(pady=12)

    def _build_run(self) -> None:
        """Deprecated: launch lives on youtube_hub overview."""
        return


    def _build_profiles(self) -> None:
        p = self._page_fill("profiles")
        p.grid_rowconfigure(2, weight=1)

        right = self._workspace_bar(p, "Профили", accent=ACCENT, row=0)
        self._toolbar_btn(right, "Обновить", self._refresh_profiles, width=96).pack(side="left")

        toolbar = ctk.CTkFrame(p, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._profile_filter_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("all", "Все"),
            ("noaddr", "Доступные"),
            ("hasaddr", "С данными"),
            ("paid", "Оплаченные"),
            ("active", "Выданные"),
        ):
            btn = self._chip_btn(
                toolbar, label, key == "all",
                lambda k=key: self._set_profile_filter(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._profile_filter_btns[key] = btn

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self.profile_list = AutoHideScrollFrame(
            body, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.profile_list.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.profile_detail = self._detail_panel(body)
        self.profile_detail.grid(row=0, column=1, sticky="nsew")
        self.profile_detail.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.profile_detail, text="Профиль",
            font=_ui_font(FONT_SECTION, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.profile_detail_body = ctk.CTkTextbox(
            self.profile_detail,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
            height=140, wrap="word",
        )
        self.profile_detail_body.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._bind_readonly_copyable(self.profile_detail_body)

        # Блок «в процессе»: этап + стоп (показывается только при busy)
        self.profile_busy_frame = ctk.CTkFrame(
            self.profile_detail, fg_color=ACCENT_SOFT, corner_radius=RADIUS_SM,
            border_width=1, border_color=WARNING,
        )
        self.profile_busy_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.profile_busy_frame.grid_remove()
        ctk.CTkLabel(
            self.profile_busy_frame, text="В процессе",
            font=_ui_font(FONT_SMALL, "bold"), text_color=WARNING, anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 0))
        self.profile_busy_stage = ctk.CTkLabel(
            self.profile_busy_frame, text="",
            font=_ui_font(FONT_CAPTION), text_color=TEXT_PRIMARY, anchor="w",
            wraplength=240, justify="left",
        )
        self.profile_busy_stage.pack(fill="x", padx=10, pady=(2, 6))
        self.profile_busy_stop = self._action_btn(
            self.profile_busy_frame, "Остановить", lambda: None, ERROR,
        )
        self.profile_busy_stop.pack(fill="x", padx=10, pady=(0, 10))

        self.profile_detail_actions = AutoHideScrollFrame(
            self.profile_detail, fg_color="transparent",
        )
        self.profile_detail_actions.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.profile_detail.grid_rowconfigure(3, weight=1)

    def _build_archive(self) -> None:
        p = self._page_fill("archive")
        p.grid_rowconfigure(3, weight=0)
        p.grid_rowconfigure(2, weight=1)

        right = self._workspace_bar(p, "Архив", accent=ACCENT, row=0)
        self.archive_stat_total = ctk.CTkLabel(
            right, text="—", font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_DIM,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=BORDER_SUBTLE, padx=10, pady=4,
        )
        self.archive_stat_total.pack(side="left", padx=(0, 6))
        self.archive_stat_cookies = ctk.CTkLabel(
            right, text="куки —", font=_ui_font(FONT_CAPTION), text_color=TEXT_DIM,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=BORDER_SUBTLE, padx=10, pady=4,
        )
        self.archive_stat_cookies.pack(side="left", padx=(0, 6))
        self.archive_stat_restored = ctk.CTkLabel(
            right, text="живые —", font=_ui_font(FONT_CAPTION), text_color=TEXT_DIM,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=BORDER_SUBTLE, padx=10, pady=4,
        )
        self.archive_stat_restored.pack(side="left", padx=(0, 6))
        self._toolbar_btn(right, "Обновить", self._refresh_archive, width=96).pack(side="left")

        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self._toolbar_btn(
            bar, "Папка архива", lambda: self._open_folder("chrome_profiles_used"),
        ).pack(side="right", padx=(4, 0))

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self.archive_list = AutoHideScrollFrame(
            body, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.archive_list.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.archive_detail = self._detail_panel(body)
        self.archive_detail.grid(row=0, column=1, sticky="nsew")
        self.archive_detail.grid_rowconfigure(2, weight=1)
        self.archive_detail.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.archive_detail, text="Запись архива",
            font=_ui_font(FONT_SECTION, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.archive_detail_body = ctk.CTkTextbox(
            self.archive_detail,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
            height=160, wrap="word",
        )
        self.archive_detail_body.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.archive_detail_body.configure(state="disabled")

        self.archive_detail_actions = AutoHideScrollFrame(
            self.archive_detail, fg_color="transparent",
        )
        self.archive_detail_actions.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 12))

    def _build_cards(self) -> None:
        p = self._page_fill("cards")
        p.grid_rowconfigure(3, weight=0)
        p.grid_rowconfigure(2, weight=1)
        self._cards_tab = "bank"
        self._sel_bank_idx: int | None = None
        self._sel_gift_idx: int | None = None
        self._gift_add_visible = False

        right = self._workspace_bar(p, "Карты", accent=ACCENT, row=0)
        self.cards_stat_bank = ctk.CTkLabel(
            right, text="банк —", font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_DIM,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=BORDER_SUBTLE, padx=10, pady=4,
        )
        self.cards_stat_bank.pack(side="left", padx=(0, 6))
        self.cards_stat_gift = ctk.CTkLabel(
            right, text="гифт —", font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_DIM,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=BORDER_SUBTLE, padx=10, pady=4,
        )
        self.cards_stat_gift.pack(side="left", padx=(0, 6))
        self.cards_stat_balance = ctk.CTkLabel(
            right, text="₹—", font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_DIM,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=BORDER_SUBTLE, padx=10, pady=4,
        )
        self.cards_stat_balance.pack(side="left", padx=(0, 6))
        self.cards_stat_pay = ctk.CTkLabel(
            right, text="оплата —", font=_ui_font(FONT_CAPTION, "bold"), text_color=ACCENT,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, border_width=1,
            border_color=ACCENT, padx=10, pady=4,
        )
        self.cards_stat_pay.pack(side="left")

        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        tabs = ctk.CTkFrame(bar, fg_color="transparent")
        tabs.pack(side="left", fill="x", expand=True)
        self._cards_tab_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("bank", "Банковские"),
            ("gift", "Гифт-карты"),
            ("history", "История"),
        ):
            btn = self._chip_btn(
                tabs, label, key == "bank",
                lambda k=key: self._set_cards_tab(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._cards_tab_btns[key] = btn

        self.cards_toolbar = ctk.CTkFrame(bar, fg_color="transparent")
        self.cards_toolbar.pack(side="right")

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self.cards_list_wrap = ctk.CTkFrame(body, fg_color="transparent")
        self.cards_list_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.cards_list_wrap.grid_rowconfigure(2, weight=1)
        self.cards_list_wrap.grid_columnconfigure(0, weight=1)

        self.gift_add_panel = ctk.CTkFrame(
            self.cards_list_wrap, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        add_hdr = ctk.CTkFrame(self.gift_add_panel, fg_color="transparent")
        add_hdr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            add_hdr, text="Добавить гифт-карты", font=_ui_font(FONT_SECTION, "bold"),
        ).pack(side="left")
        ctk.CTkButton(
            add_hdr, text="×", width=36, height=28, fg_color="transparent",
            hover_color=BG_NAV_ACTIVE, command=self._toggle_gift_add_panel,
        ).pack(side="right")
        add_inner = ctk.CTkFrame(self.gift_add_panel, fg_color="transparent")
        add_inner.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkLabel(add_inner, text="Номинал (₹)", text_color=TEXT_DIM).grid(row=0, column=0, sticky="w")
        import menu as _m
        self.gift_denom = ctk.CTkComboBox(
            add_inner, width=120, height=BTN_H, font=_ui_font(FONT_BODY),
            state="readonly", values=[str(d) for d in _m.GIFT_DENOMS],
        )
        self.gift_denom.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=4)
        self.gift_denom.set("500")
        self.gift_input = ctk.CTkTextbox(
            add_inner, height=100,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.gift_input.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        add_inner.grid_columnconfigure(1, weight=1)
        add_btns = ctk.CTkFrame(add_inner, fg_color="transparent")
        add_btns.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._action_btn(
            add_btns, "Добавить", self._add_gift_cards_manual, SUCCESS,
        ).pack(side="left", padx=(0, 8))
        self._toolbar_btn(add_btns, "Из файла", self._upload_gift_file).pack(side="left")
        self.gift_add_result = ctk.CTkLabel(add_inner, text="", text_color=TEXT_DIM, anchor="w")
        self.gift_add_result.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Сводка очереди гифт-карт — отдельный видимый блок (не кнопка в тулбаре)
        self.gift_order_panel = ctk.CTkFrame(
            self.cards_list_wrap, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )

        self.cards_list = AutoHideScrollFrame(
            self.cards_list_wrap, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        self.cards_list.grid(row=2, column=0, sticky="nsew")

        self.cards_detail = self._detail_panel(body)
        self.cards_detail.grid(row=0, column=1, sticky="nsew")
        self.cards_detail.grid_rowconfigure(1, weight=1)
        self.cards_detail.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.cards_detail, text="Детали", font=_ui_font(FONT_SECTION, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.cards_detail_body = ctk.CTkTextbox(
            self.cards_detail,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE, wrap="word",
        )
        self.cards_detail_body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.cards_detail_body.configure(state="disabled")

        self._selected_gift: dict | None = None

    def _build_tools(self) -> None:
        p = self._page_fill("tools")
        p.grid_rowconfigure(1, weight=1)

        self._workspace_bar(p, "Инструменты", accent=ACCENT, row=0)
        grid_wrap = ctk.CTkFrame(p, fg_color="transparent")
        grid_wrap.grid(row=1, column=0, sticky="nsew")
        grid_card = self._card(grid_wrap, "Действия", accent=ACCENT)
        grid_card.pack(fill="both", expand=True)
        self._action_grid(grid_card, [
            ("Проверить активацию", self._tool_check_activation, BTN_SECONDARY),
            ("Восстановить cookies", self._tool_restore_cookies, BTN_SECONDARY),
            ("Проверить обновления", self._tool_check_updates_now, BTN_SECONDARY),
            ("Бэкап данных", self._backup_data_ui, ACCENT),
            ("Очистить папки архива", self._tool_purge, ERROR),
            ("cookies_backup/", lambda: self._open_folder("cookies_backup"), BTN_SECONDARY),
            ("chrome_profiles/", lambda: self._open_folder("chrome_profiles"), BTN_SECONDARY),
        ], cols=2)

    def _build_logs(self) -> None:
        p = self._page_fill("logs")
        # Текстбокс в строке 1 — только она тянется до низа окна
        p.grid_rowconfigure(1, weight=1)
        p.grid_rowconfigure(2, weight=0)

        right = self._workspace_bar(p, "Логи", accent=ACCENT, row=0)
        self._toolbar_btn(right, "Очистить", self._clear_logs, width=90).pack(side="left", padx=(0, 6))
        self._toolbar_btn(right, "Открыть файл", self._open_log_file, width=110).pack(side="left")

        self.log_text = ctk.CTkTextbox(
            p, font=ctk.CTkFont(family="Consolas", size=FONT_MONO), fg_color=BG_MAIN,
            corner_radius=RADIUS_CARD, border_width=1, border_color=BORDER_SUBTLE,
            wrap="none", activate_scrollbars=True,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self._log_file_pos = 0

    def _build_settings(self) -> None:
        """Настройки: всё на одном экране, без скролла."""
        p = ctk.CTkFrame(self.content, fg_color="transparent")
        p._static = True  # type: ignore[attr-defined]
        self._pages["settings"] = p
        p.grid_columnconfigure((0, 1), weight=1, uniform="set")
        p.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(p, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        right = ctk.CTkFrame(p, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # ── Система ─────────────────────────────────────────────────────────
        sys_inner = self._settings_block(left, "Система")
        sys_grid = ctk.CTkFrame(sys_inner, fg_color="transparent")
        sys_grid.pack(fill="x")
        sys_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)
        for i, (txt, cmd) in enumerate([
            ("Зависимости", self._install_deps),
            ("Папка", lambda: os.startfile(str(_HERE))),
            ("secrets", self._open_secrets),
            ("config", self._open_config),
        ]):
            self._settings_btn(sys_grid, txt, cmd).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 2, 0),
            )

        # ── Обновления + установка ──────────────────────────────────────────
        upd = self._settings_block(left, "Обновления", accent=ACCENT)
        self.settings_upd_info = ctk.CTkLabel(
            upd, text="…", justify="left", anchor="w",
            text_color=TEXT_DIM, font=_ui_font(FONT_CAPTION, "bold"),
        )
        self.settings_upd_info.pack(fill="x")
        self.settings_upd_sub = ctk.CTkLabel(
            upd, text="", justify="left", anchor="w",
            text_color=TEXT_MUTED, font=_ui_font(FONT_SMALL),
        )
        self.settings_upd_sub.pack(fill="x")
        self.settings_upd_list = ctk.CTkLabel(
            upd, text="", justify="left", anchor="nw",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Consolas", size=FONT_SMALL),
            wraplength=420,
        )
        # список коммитов — только когда есть обновления (pack в _refresh_update_badge)
        row1 = ctk.CTkFrame(upd, fg_color="transparent")
        row1.pack(fill="x", pady=(4, 0))
        self._settings_upd_actions = row1
        row1.grid_columnconfigure((0, 1), weight=1)
        self.settings_upd_check_btn = self._settings_btn(
            row1, "Проверить", self._tool_check_updates_now,
        )
        self.settings_upd_check_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self.settings_upd_btn = self._action_btn(
            row1, "Скачать", self._update_and_restart, ACCENT,
        )
        self.settings_upd_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))
        self.settings_upd_btn.grid_remove()

        row2 = ctk.CTkFrame(upd, fg_color="transparent")
        row2.pack(fill="x", pady=(3, 0))
        row2.grid_columnconfigure((0, 1, 2), weight=1)
        self._action_btn(row2, "Setup.exe", self._download_setup_exe_ui, ACCENT).grid(
            row=0, column=0, sticky="ew", padx=(0, 2),
        )
        self._settings_btn(row2, "Установить", self._run_setup_exe_ui).grid(
            row=0, column=1, sticky="ew", padx=2,
        )
        self._settings_btn(row2, "Перезапуск", self._restart_only).grid(
            row=0, column=2, sticky="ew", padx=(2, 0),
        )

        row3 = ctk.CTkFrame(upd, fg_color="transparent")
        row3.pack(fill="x", pady=(3, 0))
        row3.grid_columnconfigure((0, 1, 2, 3), weight=1)
        for i, (txt, cmd) in enumerate([
            ("Ярлык", self._create_desktop_shortcut_ui),
            ("Пуск", self._run_portable_install),
            ("Бэкап", self._backup_data_ui),
            ("Удалить", self._uninstall_from_windows_ui),
        ]):
            self._settings_btn(row3, txt, cmd).grid(
                row=0, column=i, sticky="ew",
                padx=(0 if i == 0 else 2, 0),
            )

        # ── Grizzly ─────────────────────────────────────────────────────────
        gs = self._settings_block(left, "GrizzlySMS", accent=WARNING)
        self.grizzly_cancel_status = ctk.CTkLabel(
            gs, text="Нет активных номеров", justify="left", anchor="w",
            text_color=TEXT_DIM, font=_ui_font(FONT_SMALL),
        )
        self.grizzly_cancel_status.pack(fill="x")
        self._grizzly_cancel_parent = gs
        self.grizzly_btn_slot = None
        self.grizzly_cancel_btn = None
        self._grizzly_active_total = 0
        self._grizzly_btn_shown = False
        self._set_grizzly_cancel_status("Сейчас активных номеров нет", active_total=0)
        self.btn_purge_tmp = self._action_btn(
            gs, "Удалить временные профили",
            self._purge_temp_profiles_ui, ERROR,
        )
        self._refresh_purge_tmp_btn()

        # ── API-ключи ───────────────────────────────────────────────────────
        keys = self._settings_block(right, "API-ключи")
        self._settings_key_labels: dict[str, ctk.CTkLabel] = {}
        for name, section, key in [
            ("GrizzlySMS", "grizzlysms", "api_key"),
            ("Telegram", "telegram", "token"),
            ("GGSell", "ggsel", "api_key"),
        ]:
            row = ctk.CTkFrame(
                keys, fg_color=BG_SURFACE, corner_radius=RADIUS_SM,
                border_width=1, border_color=BORDER_SUBTLE,
            )
            row.pack(fill="x", pady=2)
            icon = ctk.CTkLabel(row, text="…", width=28, font=_ui_font(FONT_CAPTION))
            icon.pack(side="left", padx=(8, 4), pady=3)
            ctk.CTkLabel(
                row, text=name, font=_ui_font(FONT_CAPTION),
                text_color=TEXT_PRIMARY, anchor="w",
            ).pack(side="left", pady=3)
            self._settings_key_labels[f"{section}.{key}"] = icon
        self.settings_keys = None

        # ── Фон ─────────────────────────────────────────────────────────────
        bg = self._settings_block(right, "Фон")
        for sw_attr, text, key, handler in (
            ("sw_background", "Постоянный фон", "background_mode", "_on_setting_background"),
            ("sw_tray", "Закрытие → трей", "minimize_to_tray", "_on_setting_tray"),
            ("sw_startup", "Автозапуск Windows", "run_at_startup", "_on_setting_startup"),
            ("sw_start_min", "Старт свёрнутым", "start_minimized", "_on_setting_start_min"),
        ):
            sw = ctk.CTkSwitch(
                bg, text=text, font=_ui_font(FONT_CAPTION),
                command=getattr(self, handler), height=22,
            )
            sw.pack(anchor="w", pady=1)
            setattr(self, sw_attr, sw)
            if self._app_settings.get(key):
                sw.select()
        self._settings_btn(bg, "В трей", self._minimize_to_tray).pack(fill="x", pady=(4, 0))

        # ── Сеть (Flipkart) ───────────────────────────────────────────────────
        net = self._settings_block(right, "Сеть")
        self.sw_proxy = ctk.CTkSwitch(
            net, text="Прокси для Flipkart", font=_ui_font(FONT_CAPTION),
            command=self._on_setting_proxy, height=22,
        )
        self.sw_proxy.pack(anchor="w", pady=1)
        ctk.CTkLabel(
            net, text="Выкл — личный VPN на ПК (напрямую)",
            font=_ui_font(FONT_SMALL), text_color=TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
        try:
            import menu as _m_proxy
            if _m_proxy._proxy_enabled():
                self.sw_proxy.select()
        except Exception:
            pass

        # ── Уведомления ─────────────────────────────────────────────────────
        notif = self._settings_block(right, "Уведомления", accent=ACCENT)
        for sw_attr, text, key, handler in (
            ("sw_notify_orders", "Заказы GGSELL", "notify_ggs_orders", "_on_setting_notify_orders"),
            ("sw_notify_messages", "Сообщения чата", "notify_ggs_messages", "_on_setting_notify_messages"),
            ("sw_notify_tg", "Telegram-бот", "notify_telegram", "_on_setting_notify_tg"),
        ):
            sw = ctk.CTkSwitch(
                notif, text=text, font=_ui_font(FONT_CAPTION),
                command=getattr(self, handler), height=22,
            )
            sw.pack(anchor="w", pady=1)
            setattr(self, sw_attr, sw)
            if self._app_settings.get(key, True):
                sw.select()

        # ── Подвал ──────────────────────────────────────────────────────────
        foot = ctk.CTkFrame(
            p, fg_color=BG_CARD, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        foot.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        ctk.CTkLabel(
            foot,
            text=f"{APP_NAME} v{APP_VERSION}  ·  {APP_VENDOR}",
            font=_ui_font(FONT_SMALL), text_color=TEXT_MUTED, anchor="w",
        ).pack(side="left", padx=10, pady=6)
        self._settings_btn(foot, "CHANGELOG", self._show_changelog_dialog).pack(
            side="right", padx=(0, 8), pady=4,
        )
        self._settings_btn(foot, "Лицензия", self._show_license_dialog).pack(
            side="right", padx=(0, 4), pady=4,
        )
        self._settings_btn(foot, "О программе", self._show_about_dialog).pack(
            side="right", padx=(0, 4), pady=4,
        )


    def _show_about_dialog(self) -> None:
        messagebox.showinfo(
            f"О {APP_NAME}",
            f"{APP_NAME}  v{APP_VERSION}\n"
            f"{APP_TAGLINE}\n\n"
            f"{APP_COPYRIGHT}\n"
            f"Publisher: {APP_VENDOR}\n\n"
            "Desktop automation for YouTube Premium (Flipkart),\n"
            "GGSELL orders, and subscription services.",
            parent=self,
        )

    def _show_license_dialog(self) -> None:
        lic = _HERE / "LICENSE"
        body = (
            f"{APP_NAME}\n{APP_COPYRIGHT}\n\n"
            "Proprietary software. Unauthorized copying, distribution,\n"
            "or modification is prohibited without written permission\n"
            f"from {APP_VENDOR}."
        )
        if lic.exists():
            try:
                body = lic.read_text(encoding="utf-8")[:4000]
            except Exception:
                pass
        messagebox.showinfo("Лицензия", body, parent=self)

    def _show_changelog_dialog(self) -> None:
        path = _HERE / "CHANGELOG.md"
        body = f"{APP_NAME} v{APP_VERSION}\n\nСм. CHANGELOG.md в корне проекта."
        if path.exists():
            try:
                body = path.read_text(encoding="utf-8")[:5000]
            except Exception:
                pass
        messagebox.showinfo("CHANGELOG", body, parent=self)

    def _sync_settings_switches(self) -> None:
        s = self._app_settings
        for sw, key in (
            (getattr(self, "sw_background", None), "background_mode"),
            (getattr(self, "sw_tray", None), "minimize_to_tray"),
            (getattr(self, "sw_startup", None), "run_at_startup"),
            (getattr(self, "sw_start_min", None), "start_minimized"),
            (getattr(self, "sw_notify_orders", None), "notify_ggs_orders"),
            (getattr(self, "sw_notify_messages", None), "notify_ggs_messages"),
            (getattr(self, "sw_notify_tg", None), "notify_telegram"),
        ):
            if sw is None:
                continue
            if s.get(key):
                sw.select()
            else:
                sw.deselect()
        sw_proxy = getattr(self, "sw_proxy", None)
        if sw_proxy is not None:
            try:
                import menu as _m_proxy
                on = _m_proxy._proxy_enabled()
            except Exception:
                on = False
            if on:
                sw_proxy.select()
            else:
                sw_proxy.deselect()
        self._refresh_purge_tmp_btn()

    def _sync_windows_startup(self) -> None:
        if not _sync_windows_startup_from_settings(self._app_settings):
            self._log("⚠ Не удалось синхронизировать автозагрузку Windows")
            return
        want = bool(self._app_settings.get("run_at_startup"))
        have = _windows_startup_enabled()
        if want != have and hasattr(self, "sw_startup"):
            if want:
                self.sw_startup.select()
            else:
                self.sw_startup.deselect()

    def _persist_app_settings(self) -> None:
        _save_app_settings(self._app_settings)

    def _on_setting_background(self) -> None:
        self.after_idle(self._apply_background_setting)

    def _apply_background_setting(self) -> None:
        self._app_settings["background_mode"] = bool(self.sw_background.get())
        if not self._app_settings["background_mode"]:
            self._app_settings["start_minimized"] = False
            if hasattr(self, "sw_start_min"):
                self.sw_start_min.deselect()
            self._sync_ggs_monitor()
        else:
            self._startup_tray()
            self._sync_ggs_monitor()
        self._persist_app_settings()
        if hasattr(self, "home_ggs_monitor"):
            self._refresh_home_ggsell()
        if hasattr(self, "ggs_stat_monitor"):
            self._refresh_ggsell()

    def _on_setting_tray(self) -> None:
        self.after_idle(self._apply_tray_setting)

    def _apply_tray_setting(self) -> None:
        self._app_settings["minimize_to_tray"] = bool(self.sw_tray.get())
        self._persist_app_settings()

    def _on_setting_startup(self) -> None:
        enabled = bool(self.sw_startup.get())
        if enabled and not _set_windows_startup(True):
            messagebox.showerror("Ошибка", "Не удалось добавить в автозагрузку Windows.")
            self.sw_startup.deselect()
            return
        if not enabled:
            _set_windows_startup(False)
        self._app_settings["run_at_startup"] = enabled
        self._persist_app_settings()

    def _on_setting_start_min(self) -> None:
        if not self._app_settings.get("background_mode"):
            self.sw_start_min.deselect()
            messagebox.showinfo("Подсказка", "Сначала включите «Постоянная работа в фоне».")
            return
        self._app_settings["start_minimized"] = bool(self.sw_start_min.get())
        self._persist_app_settings()

    def _on_setting_notify_orders(self) -> None:
        self._app_settings["notify_ggs_orders"] = bool(self.sw_notify_orders.get())
        self._persist_app_settings()

    def _on_setting_notify_messages(self) -> None:
        self._app_settings["notify_ggs_messages"] = bool(self.sw_notify_messages.get())
        self._persist_app_settings()

    def _on_setting_notify_tg(self) -> None:
        on = bool(self.sw_notify_tg.get())
        self._app_settings["notify_telegram"] = on
        self._persist_app_settings()
        self._log("Уведомления Telegram-бота: "
                  + ("включены" if on else "выключены"))

    def _on_setting_proxy(self) -> None:
        on = bool(self.sw_proxy.get())
        try:
            import menu as _m_proxy
            ok = _m_proxy._set_proxy_enabled(on)
        except Exception as exc:
            ok = False
            self._log(f"⚠ Не удалось сохранить proxy.enabled: {exc}")
        if not ok:
            messagebox.showerror(
                "Ошибка",
                "Не удалось записать proxy.enabled в config.yaml.",
                parent=self,
            )
            # откат тумблера к фактическому значению в файле
            try:
                import menu as _m_proxy
                if _m_proxy._proxy_enabled():
                    self.sw_proxy.select()
                else:
                    self.sw_proxy.deselect()
            except Exception:
                pass
            return
        self._log(
            "Прокси для Flipkart: "
            + ("включён (proxy-first)" if on else "выключен")
        )
        self._log_network_mode()
        if on:
            # Прогрев пула живых прокси в фоне — первый вход не ждёт подбор
            import threading as _thr
            import menu as _m_pf
            _thr.Thread(
                target=_m_pf.prefetch_free_proxies, daemon=True,
                name="proxy-prefetch-toggle",
            ).start()

    def _log_network_mode(self) -> None:
        """Показывает итоговый режим сети по тумблеру прокси."""
        try:
            import menu as _m
            p = _m._proxy_enabled()
        except Exception:
            return
        mode = (
            "прокси → личный VPN на ПК" if p
            else "личный VPN на ПК (напрямую)"
        )
        self._log(f"Сеть Flipkart: {mode}")

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def _grizzly_status_text(self, r: dict) -> str:
        if r.get("error") == "no_api_key":
            return "API-ключ GrizzlySMS не настроен"
        if r.get("error") == "timeout":
            return "Таймаут API Grizzly — повтор позже"
        if r.get("error"):
            return f"Ошибка: {r['error']}"
        total = int(r.get("total") or 0)
        cancelled = int(r.get("cancelled") or 0)
        failed = int(r.get("failed") or 0)
        bal = r.get("balance")
        bal_s = f" · баланс ${bal:.4f}" if bal is not None else ""
        if cancelled or failed:
            if total == 0 and cancelled == 0 and failed == 0:
                return f"Сейчас активных номеров нет{bal_s}"
            if cancelled == total and total:
                return f"Отменено {cancelled} номер(ов){bal_s}"
            if cancelled:
                return f"Отменено {cancelled}/{total}, не удалось: {failed}{bal_s}"
            return f"Не удалось отменить {failed}/{total} (лимит Grizzly 1:30?){bal_s}"
        if total == 0:
            return f"Сейчас активных номеров нет{bal_s}"
        return f"Активных номеров: {total}{bal_s}"

    def _grizzly_cancel_summary(self, r: dict) -> str:
        return self._grizzly_status_text(r)

    def _set_grizzly_cancel_status(self, msg: str, active_total: int | None = None) -> None:
        if not hasattr(self, "grizzly_cancel_status"):
            return
        if active_total is not None:
            self._grizzly_active_total = max(0, int(active_total))
        elif msg.startswith("Активных номеров:"):
            try:
                self._grizzly_active_total = int(msg.split(":", 1)[1].split()[0])
            except Exception:
                pass
        elif (
            msg.startswith("Сейчас активных номеров нет")
            or msg.startswith("Отменено")
            or "активных номеров нет" in msg.lower()
        ):
            self._grizzly_active_total = 0
        if self._grizzly_active_total == 0 and "активных номеров нет" in msg.lower():
            bal_s = ""
            idx = msg.lower().find(" · баланс")
            if idx >= 0:
                bal_s = msg[idx:]
            elif " · баланс" in msg:
                bal_s = msg[msg.index(" · баланс"):]
            msg = f"Сейчас активных номеров нет{bal_s}"
        elif msg.strip() == "Активных номеров нет":
            msg = "Сейчас активных номеров нет"
        if msg.startswith("Сейчас активных номеров нет") or "активных номеров нет" in msg.lower():
            color = TEXT_DIM
        elif msg.startswith("Активных номеров:"):
            color = WARNING
        elif msg.startswith("Ошибка") or msg.startswith("Таймаут") or "Не удалось" in msg:
            color = ERROR
        else:
            color = TEXT_DIM
        self.grizzly_cancel_status.configure(text=msg, text_color=color)
        show_btn = (
            self._grizzly_active_total > 0
            and msg.startswith("Активных номеров:")
        )
        self._update_grizzly_cancel_btn(show_btn)

    def _ensure_grizzly_cancel_btn(self) -> None:
        parent = getattr(self, "_grizzly_cancel_parent", None)
        if parent is None or self.grizzly_cancel_btn is not None:
            return
        self.grizzly_btn_slot = ctk.CTkFrame(parent, fg_color="transparent")
        self.grizzly_cancel_btn = ctk.CTkButton(
            self.grizzly_btn_slot, text="Отменить номера", height=BTN_H,
            corner_radius=RADIUS_BTN, fg_color=ERROR, hover_color="#da3633",
            command=self._cancel_grizzly_numbers,
        )

    def _destroy_grizzly_cancel_btn(self) -> None:
        for w in (getattr(self, "grizzly_cancel_btn", None), getattr(self, "grizzly_btn_slot", None)):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self.grizzly_cancel_btn = None
        self.grizzly_btn_slot = None
        self._grizzly_btn_shown = False

    def _update_grizzly_cancel_btn(self, visible: bool) -> None:
        show = bool(visible and self._grizzly_active_total > 0)
        if not show:
            self._destroy_grizzly_cancel_btn()
            return
        self._ensure_grizzly_cancel_btn()
        if self.grizzly_cancel_btn is None:
            return
        self.grizzly_cancel_btn.configure(
            state="normal",
            fg_color=ERROR,
            hover_color="#da3633",
            text_color=TEXT_PRIMARY,
        )
        if not self._grizzly_btn_shown and self.grizzly_btn_slot is not None:
            self.grizzly_cancel_btn.pack(fill="x")
            self.grizzly_btn_slot.pack(fill="x", pady=(2, 0))
            self._grizzly_btn_shown = True

    def _apply_grizzly_result(self, r: dict) -> None:
        err = r.get("error")
        if err in ("timeout",) and getattr(self, "_grizzly_active_total", 0) > 0:
            self._set_grizzly_cancel_status(
                f"Не удалось обновить ({err}) · активных: {self._grizzly_active_total}",
                active_total=self._grizzly_active_total,
            )
            self._update_grizzly_cancel_btn(True)
            return
        total = int(r.get("total") or 0)
        cancelled = int(r.get("cancelled") or 0)
        if cancelled:
            active_left = max(0, total - cancelled)
        else:
            active_left = total
        self._set_grizzly_cancel_status(
            self._grizzly_status_text(r),
            active_total=active_left,
        )

    def _refresh_grizzly_status(self) -> None:
        if getattr(self, "_grizzly_status_busy", False):
            return
        self._grizzly_status_busy = True

        def _worker() -> None:
            import grizzly as gz
            try:
                r = gz.fetch_active_rentals_status_blocking()
            except Exception as exc:
                r = {"error": str(exc), "total": 0}
            try:
                self._run_on_main( lambda: self._apply_grizzly_result(r))
            finally:
                self._run_on_main( lambda: setattr(self, "_grizzly_status_busy", False))

        threading.Thread(target=_worker, daemon=True, name="grizzly-status").start()

    def _cancel_grizzly_numbers(self) -> None:
        if getattr(self, "_grizzly_active_total", 0) <= 0:
            return
        if self.grizzly_cancel_btn is None:
            return
        self._cancel_grizzly_numbers_ui(confirm=False)

    def _cancel_grizzly_numbers_ui(self, confirm: bool = True) -> None:
        """Отмена активных номеров GrizzlySMS (Настройки / YouTube)."""
        if confirm:
            if not messagebox.askyesno("GrizzlySMS", "Отменить все активные номера?"):
                return
        self._run_bg(
            self._cancel_grizzly_numbers_worker,
            "Отмена активных номеров GrizzlySMS…",
        )

    def _cancel_grizzly_numbers_worker(self) -> None:
        import grizzly as gz
        r = gz.cancel_all_active_rentals_blocking("вручную")
        msg = self._grizzly_cancel_summary(r)
        self._run_on_main( lambda: self._log(f"Grizzly: {msg}"))
        self._run_on_main( lambda: self._apply_grizzly_result(r))

    def _purge_temp_profiles_ui(self) -> None:
        """Удалить временные профили — успешные/доступные не трогает."""
        if not messagebox.askyesno(
            "Временные профили",
            "Удалить временные профили?\nУспешные и доступные останутся.",
        ):
            return

        def _w() -> None:
            import menu as m
            r = m.purge_temp_profiles()
            n = int(r.get("removed") or 0)
            err = r.get("errors") or []
            kept = int(r.get("skipped") or 0)
            msg = f"Временные профили: удалено {n}, оставлено {kept}"
            if err:
                msg += f", ошибок: {len(err)}"
            self._run_on_main(lambda: self._log(msg))
            self._run_on_main(self._refresh_purge_tmp_btn)
            if n and hasattr(self, "_refresh_youtube_hub"):
                self._run_on_main(self._refresh_youtube_hub)
            if n and hasattr(self, "_refresh_profiles"):
                self._run_on_main(self._refresh_profiles)

        self._run_bg(_w, "Удаление временных профилей…")

    def _refresh_purge_tmp_btn(self) -> None:
        """Кнопка «Удалить временные профили»: скрыта, когда удалять нечего."""
        btn = getattr(self, "btn_purge_tmp", None)
        if btn is None or not btn.winfo_exists():
            return

        def _count() -> None:
            try:
                import menu as m
                n = m.count_temp_profiles()
            except Exception:
                n = 0

            def _apply() -> None:
                if not btn.winfo_exists():
                    return
                if n > 0:
                    btn.configure(text=f"Удалить временные профили ({n})")
                    if not btn.winfo_ismapped():
                        btn.pack(fill="x", pady=(4, 0))
                else:
                    btn.pack_forget()

            self._run_on_main(_apply)

        import threading as _thr
        _thr.Thread(target=_count, daemon=True, name="tmp-profiles-count").start()

    def _apply_grizzly_startup(self, r: dict) -> None:
        summary = self._grizzly_cancel_summary(r)
        if int(r.get("total") or 0) > 0 or r.get("error"):
            self._log(f"Grizzly: {summary}")
        if hasattr(self, "grizzly_cancel_status"):
            self._apply_grizzly_result(r)
        self.after(1500, self._refresh_grizzly_status)

    def _bootstrap_backend(self) -> None:
        import menu as m
        import grizzly as gz
        import threading as _thr
        try:
            m._start_log_tee()
            log_path = _HERE / "automation.log"
            if log_path.exists():
                self._log_file_pos = log_path.stat().st_size
        except Exception:
            pass
        try:
            if not _sync_windows_startup_from_settings(self._app_settings):
                self._log("⚠ Автозагрузка Windows: не удалось применить настройку")
            elif self._app_settings.get("run_at_startup"):
                self._log("✓ Автозагрузка Windows включена")

            def _startup_cancel() -> None:
                try:
                    r = gz.startup_cleanup_active_rentals("старт приложения")
                except Exception as exc:
                    r = {"error": str(exc), "total": 0}
                self._run_on_main(self._apply_grizzly_startup, r)

            _thr.Thread(target=_startup_cancel, daemon=True, name="grizzly-startup-cancel").start()

            # Прогрев пула бесплатных прокси, чтобы первый вход не ждал подбор
            if m._proxy_enabled():
                _thr.Thread(
                    target=m.prefetch_free_proxies, daemon=True, name="proxy-prefetch",
                ).start()
            gz.start_global_monitor()
            self._sync_ggs_monitor()
            import bot as bot_mod
            r = bot_mod.ensure_tg_bot("app")
            if r == "started":
                self._log("✓ Telegram-бот запущен (приложение)")
            elif r == "active":
                self._log("✓ Telegram-бот активен")
            elif r == "no_token":
                self._log("⚠ Telegram: токен не настроен")
            self._log("✓ Фоновые сервисы запущены")
            self.after(800, self._ensure_desktop_shortcut)
        except Exception as e:
            self._log(f"⚠ Ошибка инициализации: {e}")

    def _schedule_refresh(self, key: str, fn: Callable[[], None], delay: int = 280) -> None:
        """Откладывает тяжёлое обновление UI — несколько вызовов сливаются в один."""
        job = self._refresh_jobs.get(key)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._refresh_jobs[key] = self.after(delay, lambda k=key, f=fn: self._run_scheduled_refresh(k, f))

    def _run_scheduled_refresh(self, key: str, fn: Callable[[], None]) -> None:
        self._refresh_jobs[key] = None
        try:
            fn()
        except Exception as e:
            self._log(f"⚠ Обновление UI ({key}): {e}")

    def _profiles_list_signature(self, rows: list) -> tuple:
        return tuple(
            (
                str(p.get("username", "")),
                int(p.get("issued_ts") or 0),
                int(p.get("link_received_ts") or 0),
                bool(p.get("black_activation_link") or p.get("black_short_link")),
                int(p.get("subscription_months") or 0),
            )
            for p in rows
        )

    def _log(self, msg: str) -> None:
        self._log_sink.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def _run_on_main(self, fn: Callable, /, *args, **kwargs) -> None:
        """Планирует обновление UI в главном потоке Tk (безопасно из фоновых потоков)."""
        self._main_queue.put((fn, args, kwargs))

    def _poll_main_queue(self) -> None:
        if self._quitting:
            return
        n = 0
        try:
            while n < 32:
                fn, args, kwargs = self._main_queue.get_nowait()
                n += 1
                try:
                    fn(*args, **kwargs)
                except Exception as exc:
                    self._log(f"UI: {exc}")
        except queue.Empty:
            pass
        delay = _MAIN_QUEUE_BUSY_MS if n else _MAIN_QUEUE_IDLE_MS
        with contextlib.suppress(RuntimeError, tk.TclError):
            self.after(delay, self._poll_main_queue)

    def _run_bg(self, fn: Callable, label: str = "") -> None:
        if label:
            self._log(label)

        def _wrap():
            try:
                fn()
            except Exception as e:
                self._log(f"Ошибка: {e}")
            finally:
                self._run_on_main( self._ensure_window_visible)

        threading.Thread(target=_wrap, daemon=True).start()

    def _append_log_widget(self, widget: ctk.CTkTextbox | None, text: str, max_lines: int = 0) -> None:
        if widget is None or not text:
            return
        try:
            widget.configure(state="normal")
            widget.insert("end", text)
            if max_lines > 0:
                # Без полного get() всего текста — дешевле на больших логах
                end_line = int(float(str(widget.index("end-1c")).split(".")[0]))
                if end_line > max_lines:
                    widget.delete("1.0", f"{end_line - max_lines + 1}.0")
            widget.see("end")
            widget.configure(state="disabled")
        except Exception:
            pass

    def _append_log_file(self, text: str) -> bool:
        """GUI-логи — в automation.log: единый файл журнала приложения."""
        try:
            if text and not text.endswith("\n"):
                text += "\n"
            with open(_HERE / "automation.log", "a", encoding="utf-8", errors="replace") as f:
                f.write(text)
            return True
        except Exception:
            return False

    def _tick_logs(self) -> None:
        if self._quitting:
            return
        chunks = self._log_sink.drain()
        if chunks:
            text = "".join(chunks)
            # Пишем в automation.log — на экран строки попадут из файла ниже.
            # Если файл недоступен (диск, права) — показываем напрямую.
            if not self._append_log_file(text):
                self._append_log_widget(self.log_text, text, _MAIN_LOG_LINES)
                self._append_log_widget(
                    getattr(self, "sidebar_log", None), text, _SIDEBAR_LOG_LINES,
                )
                if getattr(self, "_run_log_active", False):
                    self._append_run_log(text)
        # На экране «Логи» / активном run-log читаем файл; иначе только пишем
        show_logs = (
            self._current_page == "logs"
            or getattr(self, "_run_log_active", False)
            or getattr(self, "sidebar_log", None) is not None
        )
        if show_logs:
            log_path = _HERE / "automation.log"
            try:
                if log_path.exists():
                    size = log_path.stat().st_size
                    if size < self._log_file_pos:
                        self._log_file_pos = 0
                    if size > self._log_file_pos:
                        # Лимит за тик — не залипать на гигабайтном хвосте
                        chunk_lim = 128_000
                        with open(log_path, encoding="utf-8", errors="replace") as f:
                            f.seek(self._log_file_pos)
                            new = f.read(chunk_lim)
                            self._log_file_pos = f.tell()
                            if new:
                                self._append_log_widget(self.log_text, new, _MAIN_LOG_LINES)
                                self._append_log_widget(
                                    getattr(self, "sidebar_log", None), new, _SIDEBAR_LOG_LINES,
                                )
                                if getattr(self, "_run_log_active", False):
                                    self._append_run_log(new)
            except Exception:
                pass
        with contextlib.suppress(RuntimeError, tk.TclError):
            self.after(_LOG_TICK_MS, self._tick_logs)

    def _tick_status(self) -> None:
        if self._quitting:
            return
        # Скан профилей на диске — в фоне, чтобы UI-поток не подтормаживал
        if not getattr(self, "_status_tick_busy", False):
            self._status_tick_busy = True
            threading.Thread(
                target=self._collect_status_bg, daemon=True, name="status-tick",
            ).start()
        with contextlib.suppress(RuntimeError, tk.TclError):
            self.after(_STATUS_TICK_MS, self._tick_status)

    def _collect_status_bg(self) -> None:
        data = None
        try:
            import menu as m
            import bot as bot_mod
            vpn = m.sync_vpn_extension_status()
            # Один скан в фоне — UI не трогает диск повторно
            scan = m.scan_profiles_extension_status()
            data = {
                "profiles": len(m._load_done_profiles()),
                "tg": getattr(bot_mod, "_tg_status", "?"),
                "host": m.active_host() or "—",
                "vpn": vpn,
                "scan": scan,
            }
        except Exception:
            data = None
        try:
            self._run_on_main(self._apply_status_tick, data)
        except Exception:
            self._status_tick_busy = False

    def _sidebar_vpn_text(
        self, state: str, msg: str, scan: dict | None = None,
    ) -> tuple[str, str]:
        """Короткий статус VPN для боковой панели (без дискового скана в UI-потоке)."""
        scan = scan or {}
        total = int(scan.get("total") or 0)
        with_ext = int(scan.get("with_ext") or 0)

        if state in ("installing", "warming"):
            if any(x in msg for x in ("VPN", "Flipkart", "Проверка")):
                short = msg[:34] + ("…" if len(msg) > 34 else "")
                return short, WARNING
            if total:
                return f"Расширения {with_ext}/{total}…", WARNING
            return "Расширения в профили…", WARNING
        if state == "ready":
            if msg:
                return (msg[:40] + ("…" if len(msg) > 40 else "")), SUCCESS
            if total:
                return f"Расширение {with_ext}/{total}", SUCCESS
            return "VPN готов", SUCCESS
        if state == "error":
            short = (msg or "ошибка")[:36]
            return short, ERROR
        if state == "disabled":
            try:
                import menu as _m
                if _m._proxy_enabled():
                    return "Сеть: прокси → личный VPN", TEXT_DIM
            except Exception:
                pass
            return "Сеть: личный VPN на ПК", TEXT_DIM
        if state == "no_ext":
            return "Сеть: личный VPN на ПК", TEXT_DIM
        if total and with_ext == total:
            return f"Расширение {with_ext}/{total}", SUCCESS
        return "VPN: ожидание", TEXT_DIM

    def _tk_callback_exception(self, exc, val, tb) -> None:
        """Tk callback errors → лог, mainloop продолжает жить."""
        import traceback
        detail = "".join(traceback.format_exception(exc, val, tb))
        with contextlib.suppress(Exception):
            self._log(f"UI callback: {val}")
        with contextlib.suppress(Exception):
            crash = _HERE / "data" / "ui_callback_error.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(detail[-8000:], encoding="utf-8")

    def _apply_status_tick(self, data: dict | None) -> None:
        self._status_tick_busy = False
        if self._quitting or not data:
            return
        try:
            st = str(data["tg"])
            if st == "not_configured":
                tg = "—"
            elif st.startswith("ok:"):
                tg = "✓"
            elif st.startswith("error:"):
                tg = "✗"
            else:
                tg = "…"
            if hasattr(self, "dash_tg"):
                self.dash_tg.configure(text=tg)
            if not self._startup_done:
                running = "Старт…"
            elif (self._proc and self._proc.poll() is None) or self._external_auto:
                running = "Прогон"
            else:
                running = "Простой"
            host_ru = {"app": "приложение", "console": "консоль"}.get(
                str(data["host"]), "—")
            self.status_lbl.configure(
                text=f"{running} · Профили: {data['profiles']} · {host_ru}")

            vs = data["vpn"]
            state = vs.get("state", "idle")
            msg = vs.get("message", "")
            scan = data.get("scan") if isinstance(data.get("scan"), dict) else {}
            text, color = self._sidebar_vpn_text(state, msg, scan)
            if self._vpn_status_lbl:
                self._vpn_status_lbl.configure(text=text, text_color=color)
            if hasattr(self, "dash_vpn_chip"):
                self.dash_vpn_chip.configure(text=text, text_color=color)
            if self._current_page == "vpn" and hasattr(self, "vpn_page_status"):
                self._refresh_vpn_page()
            self._status_tick_count += 1
            # Раз в ~30 с (6 × 5 с): Grizzly + badge; sync всегда
            if self._status_tick_count % 6 == 0:
                self._refresh_grizzly_status()
            if self._status_tick_count % 6 == 0 or self._current_page == "settings":
                self._refresh_update_badge()
            self._sync_from_runtime()
        except Exception as exc:
            with contextlib.suppress(Exception):
                self._log(f"status tick: {exc}")

    def _sync_from_runtime(self) -> None:
        import menu as m
        st = m._read_runtime_state()
        ev_ts = float(st.get("last_event_ts") or 0)
        if ev_ts > self._last_sync_ts:
            self._last_sync_ts = ev_ts
            ev = st.get("last_event", "")
            if ev in ("gift_cards", "cards", "pay_method"):
                if self._current_page == "cards":
                    self._refresh_cards()
                else:
                    self._refresh_youtube_hub()
            elif ev in ("automation_started", "automation_finished"):
                self._schedule_refresh("run_status", self._sync_run_page_status)
                if ev == "automation_finished":
                    self._schedule_refresh("profiles", self._refresh_profiles)
                    self._schedule_refresh("youtube_hub", self._refresh_youtube_hub)
                    self._schedule_refresh("run_status", self._sync_run_page_status)

        ext, ast = m.shared_automation_running()
        local = self._proc and self._proc.poll() is None
        self._external_auto = ext and not local
        if self._external_auto:
            owner = ast.get("automation_owner") or "tg"
            mode = ast.get("automation_mode") or ""
            self.run_start_btn.configure(state="disabled")
            self.run_stop_btn.configure(state="normal")
            self._set_run_status_ui(f"Telegram: {mode or 'автоматизация'}", WARNING)
            self._set_run_form_enabled(False)
        elif not local and hasattr(self, "run_start_btn"):
            if str(self.run_status.cget("text")).startswith("Telegram:"):
                self.run_start_btn.configure(state="normal")
                self.run_stop_btn.configure(state="disabled")
                self._set_run_status_ui("Готов к запуску", TEXT_DIM)
                self._set_run_form_enabled(True)

    def _get_update_state(self) -> tuple[int, list[str], bool, float]:
        try:
            import bot as bot_mod
            commits = list(getattr(bot_mod, "_update_commits", []) or [])
            checked = bool(getattr(bot_mod, "_update_checked", False))
            checked_at = float(getattr(bot_mod, "_update_checked_at", 0) or 0)
            return len(commits), commits, checked, checked_at
        except Exception:
            return 0, [], False, 0.0

    def _format_update_when(self, checked_at: float) -> str:
        if checked_at <= 0:
            return "только что"
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(checked_at))

    def _update_commits_text(self, commits: list[str], limit: int = 8) -> str:
        if not commits:
            return ""
        lines = [f"• {c}" for c in commits[:limit]]
        if len(commits) > limit:
            lines.append(f"… и ещё {len(commits) - limit}")
        return "\n".join(lines)

    def _set_readonly_text(self, widget: ctk.CTkTextbox, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if text:
            widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _notify_updates_if_changed(self, n: int, commits: list[str], checked_at: float) -> None:
        if n <= 0:
            self._last_update_count = 0
            return
        if n == self._last_update_count:
            return
        when = self._format_update_when(checked_at)
        self._log(f"⚡ Доступно {n} обновлений (обнаружено {when})")
        for c in commits[:4]:
            self._log(f"   • {c}")
        if len(commits) > 4:
            self._log(f"   … и ещё {len(commits) - 4}")
        self._last_update_count = n
        self._toast(
            f"Обновления: {n}",
            (commits[0] if commits else "Доступны новые коммиты")[:120],
            WARNING,
        )

    def _needs_restart_for_update(self) -> bool:
        """Файлы на диске новее запущенной версии — нужен перезапуск."""
        if not self._app_start_sha:
            return False
        current = _local_repo_sha()
        if not current:
            return False
        return current[:7] != self._app_start_sha[:7]

    def _show_update_action_btn(
        self, text: str, command: Callable, color: str, state: str = "normal",
    ) -> None:
        btn = getattr(self, "settings_upd_btn", None)
        if btn is None:
            return
        short = text
        for prefix in ("⬆  ", "♻  "):
            if short.startswith(prefix):
                short = short[len(prefix):]
        if len(short) > 28:
            short = short[:27] + "…"
        btn.configure(
            text=short, command=command,
            fg_color=color if color in (SUCCESS, BTN_SUCCESS, ERROR, WARNING, ACCENT) else "transparent",
            border_width=0 if color in (SUCCESS, BTN_SUCCESS, ERROR, WARNING, ACCENT) else 1,
            border_color=BORDER_SUBTLE,
            hover_color=self._btn_hover(color),
            text_color=TEXT_ON_ACCENT if color in (ACCENT, BTN_SUCCESS, SUCCESS, WARNING, ERROR) else TEXT_PRIMARY,
            state=state,
        )
        if color == ACCENT:
            btn.configure(fg_color=ACCENT, text_color=TEXT_ON_ACCENT, hover_color=ACCENT_HOVER, border_width=0)
        with contextlib.suppress(Exception):
            btn.grid()

    def _hide_update_action_btn(self) -> None:
        btn = getattr(self, "settings_upd_btn", None)
        if btn is not None:
            with contextlib.suppress(Exception):
                btn.grid_remove()

    def _refresh_update_badge(self) -> None:
        try:
            if self._update_in_progress:
                return
            n, commits, checked, checked_at = self._get_update_state()
            when = self._format_update_when(checked_at)
            if checked:
                self._notify_updates_if_changed(n, commits, checked_at)
            needs_restart = self._needs_restart_for_update()
            if n:
                title = f"Доступно: {n}"
                sub = when
                title_color = WARNING
                list_text = self._update_commits_text(commits)
            elif needs_restart:
                title = "Нужен перезапуск"
                sub = "Обновление скачано"
                title_color = WARNING
                list_text = ""
            elif checked:
                title = "Актуальна"
                sub = when
                title_color = SUCCESS
                list_text = ""
            else:
                title = "Проверка…"
                sub = ""
                title_color = TEXT_DIM
                list_text = ""
            if hasattr(self, "settings_upd_info"):
                self.settings_upd_info.configure(text=title, text_color=title_color)
            if hasattr(self, "settings_upd_sub"):
                self.settings_upd_sub.configure(text=sub)
            if hasattr(self, "settings_upd_list"):
                lst = self.settings_upd_list
                anchor = getattr(self, "_settings_upd_actions", None)
                if list_text.strip():
                    # компактно: максимум 2 строки
                    lines = [ln for ln in list_text.strip().splitlines() if ln.strip()][:2]
                    lst.configure(text="\n".join(lines))
                    if not lst.winfo_ismapped():
                        if anchor is not None:
                            lst.pack(fill="x", pady=(0, 2), before=anchor)
                        else:
                            lst.pack(fill="x", pady=(0, 2))
                elif lst.winfo_ismapped():
                    lst.pack_forget()
                    lst.configure(text="")
            if n:
                self._show_update_action_btn(
                    f"Скачать {n}",
                    self._update_and_restart, BTN_SUCCESS,
                )
            elif needs_restart:
                self._show_update_action_btn(
                    "Перезапуск",
                    self._restart_for_update, WARNING,
                )
            else:
                self._hide_update_action_btn()
        except Exception:
            pass

    # ── Data refresh ──────────────────────────────────────────────────────────

    def _refresh_youtube_hub(self) -> None:
        import menu as m
        try:
            self.dash_profiles.configure(text=str(len(m._load_done_profiles())))
            self.dash_cards.configure(text=str(len(m._load_cards())))
            self.dash_gift.configure(text=f"₹{m._gift_balance()}")
            import bot as bot_mod
            st = getattr(bot_mod, "_tg_status", "?")
            self.dash_tg.configure(text="✓" if str(st).startswith("ok:") else "…")
            if hasattr(self, "dash_pay_chip"):
                pm = m._load_pay_method()
                if pm == "gift":
                    self.dash_pay_chip.configure(
                        text="Оплата · гифт", text_color=ACCENT,
                        fg_color=ACCENT_SOFT, border_color=ACCENT,
                    )
                else:
                    self.dash_pay_chip.configure(
                        text="Оплата · карта", text_color=TEXT_DIM,
                        fg_color=BG_SURFACE, border_color=BORDER_SUBTLE,
                    )
        except Exception as e:
            self._log(f"Ошибка: {e}")
        threading.Thread(target=self._fetch_balance, daemon=True).start()

    @staticmethod
    def _ggs_home_stats() -> dict:
        """Локальная статистика заказов GGSell (из data/ggsel_done.json)."""
        out = {"done": 0, "today": 0, "refunded": 0}
        try:
            raw = json.loads(
                (_HERE / "data" / "ggsel_done.json").read_text(encoding="utf-8"))
            done = raw.get("done") or {}
            out["done"] = len(done)
            out["refunded"] = len(raw.get("refunded") or {})
            today = time.strftime("%Y-%m-%d")
            out["today"] = sum(
                1 for v in done.values() if str(v).startswith(today))
        except Exception:
            pass
        return out

    def _ggs_monitor_wanted(self) -> bool:
        if not self._app_settings.get("background_mode", True):
            return False
        import menu as m
        gs = m._read_secrets().get("ggsel") or {}
        key = str(gs.get("api_key") or "")
        return bool(gs.get("seller_id") and key and "YOUR_" not in key)

    def _sync_ggs_monitor(self) -> None:
        from ggsell.monitor import is_monitor_running, start_monitor, stop_monitor
        import menu as m
        want = self._ggs_monitor_wanted()
        running = is_monitor_running()
        if want and not running:
            gs = m._read_secrets().get("ggsel") or {}
            try:
                start_monitor(gs["api_key"], int(gs["seller_id"]))
            except Exception as e:
                self._log(f"⚠ GGSell monitor: {e}")
        elif not want and running:
            stop_monitor()

    def _ggs_monitor_active(self) -> bool:
        from ggsell.monitor import is_monitor_running
        return self._ggs_monitor_wanted() and is_monitor_running()

    def _set_monitor_stat(self, lbl: ctk.CTkLabel, monitor_on: bool) -> None:
        bg_off = not self._app_settings.get("background_mode", True)
        if monitor_on:
            text, color = "Активен", SUCCESS
            sub = "следит за заказами"
        elif bg_off:
            text, color = "Выключен", TEXT_DIM
            sub = "фоновый режим выкл."
        else:
            text, color = "Выключен", TEXT_DIM
            sub = "следит за заказами"
        lbl.configure(text=text, text_color=color)
        if hasattr(lbl, "sub_label"):
            lbl.sub_label.configure(text=sub)

    def _refresh_home_ggsell(self) -> None:
        if not hasattr(self, "home_ggs_orders"):
            return
        import menu as m
        gs = m._read_secrets().get("ggsel") or {}
        api_ok = bool(gs.get("api_key") and gs.get("seller_id") and "YOUR_" not in str(gs.get("api_key", "")))
        monitor_on = self._ggs_monitor_active()
        self._set_monitor_stat(self.home_ggs_monitor, monitor_on)
        st = self._ggs_home_stats()
        self.home_ggs_orders.configure(text=str(st["done"]))
        try:
            self.home_ggs_orders.sub_label.configure(text=f"сегодня: {st['today']}")
        except Exception:
            pass
        self.home_ggs_refunds.configure(text=str(st["refunded"]))
        if api_ok:
            threading.Thread(target=self._fetch_ggsell_balance, daemon=True).start()
        else:
            self.home_ggs_balance.configure(text="—")
            try:
                self.home_ggs_balance.sub_label.configure(
                    text="настройте API-ключи")
            except Exception:
                pass

    def _set_ggsell_filter(self, flt: str) -> None:
        self._ggs_filter = flt
        for key, btn in getattr(self, "_ggs_filter_btns", {}).items():
            self._sync_chip_btn(btn, key == flt)
        self._render_ggsell_orders()

    def _refresh_ggsell(self) -> None:
        import menu as m
        sec = m._read_secrets()
        gs = sec.get("ggsel") or {}
        api_ok = bool(gs.get("api_key") and gs.get("seller_id") and "YOUR_" not in str(gs.get("api_key", "")))
        self.ggs_stat_api.configure(text="✓" if api_ok else "✗")

        monitor_on = self._ggs_monitor_active()
        self._set_monitor_stat(self.ggs_stat_monitor, monitor_on)

        from ggsell.gui_orders import load_local_state
        self._ggs_state = load_local_state()
        done_count = len(self._ggs_state.get("done", {}))
        self.ggs_stat_orders.configure(text=str(done_count))
        if hasattr(self, "home_ggs_orders"):
            self.home_ggs_orders.configure(text=str(done_count))
        if hasattr(self, "home_ggs_monitor"):
            self._set_monitor_stat(self.home_ggs_monitor, monitor_on)

        if not api_ok:
            self.ggs_stat_balance.configure(text="—")
            if hasattr(self, "ggs_orders_status"):
                self.ggs_orders_status.configure(
                    text="Настройте GGSell API в secrets.yaml",
                )
            self._render_ggsell_orders()
            return

        if self._ggs_loading:
            self._ggs_refresh_pending = True
            return
        self._ggs_loading = True
        if hasattr(self, "ggs_orders_status"):
            self.ggs_orders_status.configure(text="Загрузка заказов…")

        def _w():
            err: str | None = None
            try:
                asyncio.run(self._load_ggsell_orders())
            except Exception as e:
                err = str(e)
                self._run_on_main(lambda: self._log(f"GGSell заказы: {e}"))
            finally:
                self._run_on_main(lambda: self._ggsell_orders_loaded(err))

        threading.Thread(target=_w, daemon=True, name="ggs-orders").start()
        threading.Thread(target=self._fetch_ggsell_balance, daemon=True).start()
        if getattr(self, "_ggs_section", "") == "monitor":
            self._refresh_ggs_monitor_panel()

    async def _load_ggsell_orders(self) -> None:
        import menu as m
        from ggsell.client import GGSellClient
        from ggsell.gui_orders import fetch_youtube_orders, load_local_state

        gs = m._read_secrets().get("ggsel") or {}
        client = GGSellClient(gs["api_key"], int(gs["seller_id"]), http_timeout=20)
        try:
            orders, chat_map = await fetch_youtube_orders(client)
            self._ggs_orders = orders
            self._ggs_chat_map = chat_map
            self._ggs_state = load_local_state()
        finally:
            await client.close()

    def _ggsell_orders_loaded(self, err: str | None = None) -> None:
        self._ggs_loading = False
        self._render_ggsell_orders()
        n = len(self._ggs_orders)
        if err:
            if hasattr(self, "ggs_orders_status"):
                self.ggs_orders_status.configure(text=f"✗ {err[:80]}")
        else:
            self._log(f"✓ GGSell: загружено {n} заказов")
        pending = self._ggs_pending_select
        if pending:
            self._ggs_pending_select = None
            self._ggs_selected_id = pending
            self._render_ggsell_orders()
            self._ggsell_load_chat(pending)
        if self._ggs_refresh_pending:
            self._ggs_refresh_pending = False
            self.after(400, self._refresh_ggsell)

    def _render_ggsell_orders(self) -> None:
        from ggsell.gui_orders import (
            STATUS_LABEL, filter_orders, invoice_id, load_local_state,
            order_email, row_label, status_key,
        )

        if not hasattr(self, "ggs_orders_list"):
            return
        if not self._ggs_state:
            self._ggs_state = load_local_state()

        for w in self.ggs_orders_list.winfo_children():
            w.destroy()

        orders = filter_orders(self._ggs_orders, self._ggs_state, self._ggs_filter)
        counts = {k: len(filter_orders(self._ggs_orders, self._ggs_state, k))
                  for k in ("new", "issued", "used", "all")}
        if hasattr(self, "ggs_orders_status"):
            self.ggs_orders_status.configure(
                text=(
                    f"Всего: {len(self._ggs_orders)}  ·  "
                    f"новые: {counts['new']}  ·  выдано: {counts['issued']}  ·  "
                    f"архив: {counts['used']}  ·  показано: {len(orders)}"
                ),
            )

        if not self._ggs_orders:
            ctk.CTkLabel(
                self.ggs_orders_list, text="Нет заказов или API не настроен",
                text_color=TEXT_DIM,
            ).pack(pady=10)
            self._show_ggsell_order_detail(None)
            return

        if not orders:
            ctk.CTkLabel(
                self.ggs_orders_list, text="В этой категории заказов нет",
                text_color=TEXT_DIM,
            ).pack(pady=10)
            self._show_ggsell_order_detail(None)
            return

        sel = self._ggs_selected_id
        if sel not in {invoice_id(o) for o in orders}:
            sel = invoice_id(orders[0])
            self._ggs_selected_id = sel

        for o in orders[:40]:
            inv = invoice_id(o)
            sk = status_key(inv, self._ggs_state)
            active = inv == sel
            row = ctk.CTkFrame(
                self.ggs_orders_list,
                fg_color=BG_CARD_HOVER if active else BG_CARD,
                corner_radius=RADIUS_CHIP,
                border_width=2 if active else 1,
                border_color=ACCENT if active else BORDER_SUBTLE,
            )
            row.pack(fill="x", pady=3, padx=2)
            if not active:
                self._bind_row_hover(row)
            lbl = row_label(o, self._ggs_state, self._ggs_chat_map)
            ctk.CTkLabel(
                row, text=lbl, anchor="w", font=_ui_font(FONT_BODY),
                text_color=TEXT_PRIMARY if active else TEXT_DIM,
            ).pack(fill="x", padx=12, pady=10)
            tip = STATUS_LABEL.get(sk, "")
            if tip:
                ctk.CTkLabel(
                    row, text=tip, anchor="e", font=_ui_font(FONT_SMALL),
                    text_color=TEXT_MUTED,
                ).pack(anchor="e", padx=8, pady=(0, 4))
            for w in (row,):
                w.bind("<Button-1>", lambda e, i=inv: self._select_ggsell_order(i))
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, i=inv: self._select_ggsell_order(i))

        self._show_ggsell_order_detail(sel)

    def _select_ggsell_order(self, inv_id: int) -> None:
        self._ggsell_close_templates_modal()
        self._ggs_selected_id = inv_id
        self._render_ggsell_orders()

    def _ggsell_order_by_id(self, inv_id: int) -> dict | None:
        from ggsell.gui_orders import invoice_id
        for o in self._ggs_orders:
            if invoice_id(o) == inv_id:
                return o
        return None

    def _ggsell_chat_return(self, event=None):
        if event and (event.state & 0x1):
            return
        self._ggsell_send_chat()
        return "break"

    def _ggsell_get_chat_text(self) -> str:
        w = getattr(self, "ggs_chat_input", None)
        if w is None:
            return ""
        return w.get("1.0", "end-1c").strip()

    def _ggsell_set_chat_text(self, text: str) -> None:
        w = getattr(self, "ggs_chat_input", None)
        if w is None:
            return
        w.configure(state="normal")
        w.delete("1.0", "end")
        if text:
            w.insert("1.0", text)
        if self._ggs_selected_id:
            w.configure(state="normal")
        else:
            w.configure(state="disabled")

    def _ggsell_set_chat_enabled(self, enabled: bool) -> None:
        st = "normal" if enabled else "disabled"
        for w in (
            getattr(self, "ggs_btn_chat_refresh", None),
            getattr(self, "ggs_btn_seller", None),
            getattr(self, "ggs_btn_templates", None),
            getattr(self, "ggs_btn_chat_send", None),
        ):
            if w is not None:
                w.configure(state=st)
        inp = getattr(self, "ggs_chat_input", None)
        if inp is not None:
            inp.configure(state=st)

    def _ggsell_clear_chat_ui(self) -> None:
        if not hasattr(self, "ggs_chat_scroll"):
            return
        for w in self.ggs_chat_scroll.winfo_children():
            w.destroy()

    def _ggsell_render_chat(self, messages: list[dict]) -> None:
        self._ggsell_clear_chat_ui()
        if not messages:
            lbl = ctk.CTkLabel(
                self.ggs_chat_scroll, text="Сообщений пока нет",
                text_color=TEXT_MUTED, font=_ui_font(FONT_BODY),
            )
            lbl.pack(pady=10)
            return
        for msg in messages:
            seller = msg.get("seller", False)
            bubble_bg = ACCENT_SOFT if seller else BG_MAIN
            border = ACCENT if seller else BORDER_SUBTLE
            row = ctk.CTkFrame(self.ggs_chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            bubble = ctk.CTkFrame(
                row, fg_color=bubble_bg,
                corner_radius=RADIUS_CHIP,
                border_width=1, border_color=border,
            )
            pad = (36, 4) if seller else (4, 36)
            bubble.pack(
                side="right" if seller else "left",
                padx=pad, anchor="e" if seller else "w",
            )
            who = "Вы" if seller else "Покупатель"
            head = f"{who}" + (f"  ·  {msg['time']}" if msg.get("time") else "")
            ctk.CTkLabel(
                bubble, text=head,
                font=_ui_font(FONT_SMALL, "bold"),
                text_color=ACCENT if seller else TEXT_DIM,
                anchor="w", justify="left",
            ).pack(anchor="w", padx=10, pady=(6, 0))
            body = (msg.get("text") or "(пусто)").strip()
            ctk.CTkLabel(
                bubble, text=body, anchor="w", justify="left",
                font=_ui_font(FONT_BODY),
                text_color=TEXT_PRIMARY if seller else TEXT_DIM,
                wraplength=320,
            ).pack(anchor="w", padx=10, pady=(2, 8))
        try:
            self.ggs_chat_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _parse_ggsell_messages(self, messages: list) -> list[dict]:
        out: list[dict] = []
        for msg in (messages or [])[-30:]:
            is_seller = bool(
                msg.get("is_seller") or msg.get("is_seller_msg")
                or msg.get("sender") == "seller" or msg.get("type") == "seller"
                or int(msg.get("type_message") or msg.get("type_msg") or -1) == 1
            )
            raw_date = (
                msg.get("date") or msg.get("created_at")
                or msg.get("timestamp") or msg.get("date_add") or ""
            )
            t = str(raw_date)[:16].replace("T", " ") if raw_date else ""
            text_m = (msg.get("text") or msg.get("message") or msg.get("body") or "")
            out.append({"seller": is_seller, "time": t, "text": text_m})
        return out

    def _show_ggsell_order_detail(self, inv_id: int | None) -> None:
        from ggsell.gui_orders import (
            STATUS_LABEL, order_email, parse_order, status_key,
        )

        if not hasattr(self, "ggs_detail_title"):
            return
        if not inv_id:
            self.ggs_detail_title.configure(text="Выберите заказ")
            self.ggs_detail_meta.configure(text="—")
            self.ggs_chat_status.configure(text="")
            self._ggsell_set_chat_enabled(False)
            self._ggsell_clear_chat_ui()
            ph = ctk.CTkLabel(
                self.ggs_chat_scroll, text="Выберите заказ слева",
                text_color=TEXT_MUTED, font=_ui_font(FONT_BODY),
            )
            ph.pack(pady=10)
            return

        order = self._ggsell_order_by_id(inv_id)
        if not order:
            self.ggs_detail_title.configure(text=f"Заказ #{inv_id}")
            self.ggs_detail_meta.configure(text="Данные не найдены")
            self._ggsell_set_chat_enabled(False)
            return

        p = parse_order(order)
        sk = status_key(inv_id, self._ggs_state)
        email = order_email(order, self._ggs_state, self._ggs_chat_map)
        link = self._ggs_state.get("links", {}).get(inv_id, "")
        issued = self._ggs_state.get("done", {}).get(inv_id, "")

        meta_parts = [
            STATUS_LABEL.get(sk, sk),
            p["name_short"],
        ]
        if email:
            meta_parts.append(email)
        if p["sum_buy"]:
            meta_parts.append(f"{p['sum_buy']}₽")
        if issued:
            meta_parts.append(f"выдан {issued[:10]}")
        if link:
            meta_parts.append("ссылка ✓")

        self.ggs_detail_title.configure(text=f"#{inv_id}")
        self.ggs_detail_meta.configure(text="  ·  ".join(meta_parts))
        self._ggsell_set_chat_enabled(True)
        self._ggsell_load_chat(inv_id)

    def _ggsell_open_seller(self) -> None:
        inv = self._ggs_selected_id
        if not inv:
            return
        import webbrowser
        webbrowser.open(f"https://seller.ggsel.com/order/{inv}")

    def _ggsell_refresh_chat(self) -> None:
        inv = self._ggs_selected_id
        if inv:
            self._ggsell_load_chat(inv)

    def _ggsell_load_chat(self, inv_id: int) -> None:
        if self._ggs_chat_loading:
            return
        self._ggs_chat_loading = True
        self.ggs_chat_status.configure(text="Загрузка чата…")
        self._ggsell_set_chat_enabled(False)

        def _w():
            try:
                parsed = asyncio.run(self._fetch_ggsell_messages_parsed(inv_id))
                self._run_on_main( lambda: self._ggsell_chat_loaded(inv_id, parsed, None))
            except Exception as e:
                self._run_on_main( lambda: self._ggsell_chat_loaded(inv_id, None, str(e)))

        threading.Thread(target=_w, daemon=True, name="ggs-chat").start()

    def _ggsell_chat_loaded(
        self, inv_id: int, messages: list[dict] | None, err: str | None,
    ) -> None:
        self._ggs_chat_loading = False
        if self._ggs_selected_id != inv_id:
            return
        if err:
            self.ggs_chat_status.configure(text=f"Ошибка: {err[:80]}")
            self._ggsell_clear_chat_ui()
            ctk.CTkLabel(
                self.ggs_chat_scroll, text="Не удалось загрузить чат",
                text_color=ERROR, font=_ui_font(FONT_BODY),
            ).pack(pady=10)
        else:
            self.ggs_chat_status.configure(text=f"{len(messages or [])} сообщ.")
            self._ggsell_render_chat(messages or [])
        self._ggsell_set_chat_enabled(True)
        if not err:
            try:
                self.ggs_chat_input.focus()
            except Exception:
                pass

    async def _fetch_ggsell_messages_parsed(self, inv_id: int) -> list[dict]:
        import menu as m
        from ggsell.client import GGSellClient

        gs = m._read_secrets().get("ggsel") or {}
        client = GGSellClient(gs["api_key"], int(gs["seller_id"]), http_timeout=20)
        try:
            messages = await client.get_messages(inv_id)
        finally:
            await client.close()
        return self._parse_ggsell_messages(messages)

    def _ggsell_send_chat(self, text: str | None = None) -> None:
        inv = self._ggs_selected_id
        if not inv or not hasattr(self, "ggs_chat_input"):
            return
        body = (text or self._ggsell_get_chat_text()).strip()
        if not body:
            return
        if text is None:
            self._ggsell_set_chat_text("")
        self.ggs_chat_status.configure(text="Отправка…")
        self._ggsell_set_chat_enabled(False)

        def _w():
            err = None
            try:
                asyncio.run(self._send_ggsell_message(inv, body))
            except Exception as e:
                err = str(e)
            self._run_on_main( lambda: self._ggsell_after_send(inv, err))

        threading.Thread(target=_w, daemon=True, name="ggs-send").start()

    async def _send_ggsell_message(self, inv_id: int, text: str) -> None:
        import menu as m
        from ggsell.client import GGSellClient

        gs = m._read_secrets().get("ggsel") or {}
        client = GGSellClient(gs["api_key"], int(gs["seller_id"]), http_timeout=20)
        try:
            ok = await client.send_message(inv_id, text)
            if not ok:
                raise RuntimeError("GGSell не принял сообщение")
        finally:
            await client.close()

    def _ggsell_after_send(self, inv_id: int, err: str | None) -> None:
        if self._ggs_selected_id != inv_id:
            return
        if err:
            self.ggs_chat_status.configure(text=f"Ошибка: {err[:80]}")
            self._ggsell_set_chat_enabled(True)
            messagebox.showerror("Чат", err)
            return
        self._ggsell_load_chat(inv_id)

    def _ggsell_resolve_template(self, name: str, inv_id: int) -> str:
        from ggsell.monitor import get_template
        import menu as m

        text = (get_template(name) or "").strip()
        if not text:
            raise ValueError("Шаблон пуст")
        link = (self._ggs_state.get("links", {}) or {}).get(inv_id, "")
        promo = str((m._read_secrets().get("ggsel") or {}).get("promo_code") or "")
        if "{link}" in text:
            if not link:
                raise ValueError("Нет ссылки для этого заказа — шаблон требует {link}")
            text = text.format(link=link)
        if "{promo_code}" in text:
            if not promo:
                raise ValueError("Нет promo_code в secrets.yaml — шаблон требует {promo_code}")
            text = text.format(promo_code=promo)
        return text

    def _ggsell_close_templates_modal(self) -> None:
        win = getattr(self, "_ggs_templates_win", None)
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self._ggs_templates_win = None

    def _ggsell_open_templates_modal(self) -> None:
        inv = self._ggs_selected_id
        if not inv:
            return
        self._ggsell_close_templates_modal()

        win = ctk.CTkToplevel(self)
        win.title("Шаблоны")
        win.geometry("340x420")
        win.transient(self)
        win.resizable(False, False)
        win.configure(fg_color=BG_MAIN)
        self._ggs_templates_win = win
        try:
            x = self.winfo_rootx() + self.winfo_width() - 380
            y = self.ggs_order_detail.winfo_rooty()
            win.geometry(f"340x420+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        hdr = ctk.CTkFrame(win, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(
            hdr, text="Шаблоны", font=_ui_font(FONT_SECTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")
        ctk.CTkButton(
            hdr, text="×", width=BTN_ICON, height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color="transparent", hover_color=BG_CARD_HOVER,
            command=self._ggsell_close_templates_modal,
        ).pack(side="right")

        list_frame = AutoHideScrollFrame(
            win, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE, height=120,
        )
        list_frame.pack(fill="x", padx=10, pady=(0, 6))

        preview = ctk.CTkTextbox(
            win, height=130, corner_radius=RADIUS_CARD,
            fg_color=BG_MAIN, border_width=1, border_color=BORDER_SUBTLE,
            font=_ui_font(FONT_BODY), wrap="word",
        )
        preview.pack(fill="x", padx=10, pady=(0, 6))
        preview.configure(state="disabled")

        state: dict = {"name": ""}

        def _pick(name: str) -> None:
            state["name"] = name
            try:
                resolved = self._ggsell_resolve_template(name, inv)
            except Exception as e:
                resolved = f"Ошибка: {e}"
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", resolved)
            preview.configure(state="disabled")

        for key, (label, hint) in _GGS_TEMPLATE_META.items():
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            self._ghost_btn(
                row, label, lambda k=key: _pick(k),
            ).pack(fill="x")
            ctk.CTkLabel(
                row, text=hint, font=_ui_font(FONT_SMALL),
                text_color=TEXT_MUTED, anchor="w",
            ).pack(fill="x", padx=4, pady=(0, 4))

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(0, 8))
        btns.grid_columnconfigure((0, 1), weight=1)

        def _insert() -> None:
            name = state.get("name")
            if not name:
                messagebox.showinfo("Шаблоны", "Выберите шаблон из списка")
                return
            try:
                text = self._ggsell_resolve_template(name, inv)
            except Exception as e:
                messagebox.showerror("Шаблоны", str(e))
                return
            self._ggsell_set_chat_text(text)
            self._ggsell_close_templates_modal()
            try:
                self.ggs_chat_input.focus()
            except Exception:
                pass

        def _send_tpl() -> None:
            name = state.get("name")
            if not name:
                messagebox.showinfo("Шаблоны", "Выберите шаблон из списка")
                return
            try:
                text = self._ggsell_resolve_template(name, inv)
            except Exception as e:
                messagebox.showerror("Шаблоны", str(e))
                return
            self._ggsell_close_templates_modal()
            self._ggsell_send_chat(text)

        self._action_btn(btns, "Вставить", _insert, BTN_SECONDARY).grid(
            row=0, column=0, sticky="ew", padx=(0, 4),
        )
        self._action_btn(btns, "Отправить", _send_tpl, ACCENT).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self._toolbar_btn(
            win, "Редактировать шаблоны", self._open_ggsell_templates,
        ).pack(fill="x", padx=10, pady=(0, 8))

        first = next(iter(_GGS_TEMPLATE_META), None)
        if first:
            _pick(first)

        win.protocol("WM_DELETE_WINDOW", self._ggsell_close_templates_modal)

    def _fetch_ggsell_balance(self) -> None:
        async def _run():
            try:
                import menu as m
                from ggsell.client import GGSellClient
                gs = m._read_secrets().get("ggsel") or {}
                c = GGSellClient(gs["api_key"], int(gs["seller_id"]), http_timeout=15)
                bal = await c.get_balance()
                await c.close()
                self._run_on_main( lambda b=bal: self._set_ggsell_balance(f"${b:.2f}"))
            except Exception as e:
                self._run_on_main( lambda: self._set_ggsell_balance("ошибка"))
                self._log(f"GGSell баланс: {e}")
        asyncio.run(_run())

    def _set_ggsell_balance(self, text: str) -> None:
        if hasattr(self, "ggs_stat_balance"):
            self.ggs_stat_balance.configure(text=text)
        if hasattr(self, "home_ggs_balance"):
            self.home_ggs_balance.configure(text=text)

    def _open_ggsell_templates(self) -> None:
        p = _HERE / "data" / "ggsel_templates.json"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        os.startfile(str(p))

    def _fetch_balance(self) -> None:
        async def _run():
            try:
                import menu as m
                from grizzly_sms import GrizzlySMSClient
                key = (m._read_secrets().get("grizzlysms") or {}).get("api_key", "")
                if not key:
                    return
                c = GrizzlySMSClient(key, http_timeout=10)
                bal = await c.get_balance()
                await c.close()
                self._run_on_main( lambda: self.dash_balance.configure(text=f"GrizzlySMS: ${bal:.4f}"))
            except Exception as e:
                self._run_on_main( lambda: self.dash_balance.configure(text=f"Баланс: {e}"))
        asyncio.run(_run())

    def _refresh_settings_keys(self) -> None:
        import menu as m
        sec = m._read_secrets()
        rows = getattr(self, "_settings_key_labels", None)
        if not rows:
            return
        for name, section, key in [
            ("GrizzlySMS", "grizzlysms", "api_key"),
            ("Telegram", "telegram", "token"),
            ("GGSell", "ggsel", "api_key"),
        ]:
            val = (sec.get(section) or {}).get(key, "")
            ok = "OK" if val and "YOUR_" not in str(val) else "—"
            lbl = rows.get(f"{section}.{key}")
            if lbl is not None:
                lbl.configure(text=ok, text_color=SUCCESS if ok == "OK" else ERROR)

    def _set_profile_filter(self, flt: str) -> None:
        self._profile_filter = flt
        for key, btn in getattr(self, "_profile_filter_btns", {}).items():
            self._sync_chip_btn(btn, key == flt)
        self._refresh_profiles()

    def _profile_category(self, meta: dict) -> str:
        vt = meta.get("black_valid_till") or ""
        st = meta.get("status") or ""
        is_issued = bool(meta.get("issued_ts"))
        has_link = bool(
            meta.get("black_activation_link") or meta.get("black_short_link")
            or meta.get("issued_link") or meta.get("activation_url")
        )
        is_subact = st in ("activated", "explore_now", "activate_now") or bool(vt)
        if is_issued:
            return "active"
        if (has_link or is_subact) and not is_issued:
            return "paid"
        if meta.get("prepared_ts") or meta.get("buyer_email") or st == "email_completed":
            return "hasaddr"
        return "noaddr"

    def _profile_row_meta(self, prof: dict) -> tuple[str, str, str]:
        import menu as m
        phone = m._disp_phone(prof.get("username", "?"))
        cat = self._profile_category(prof)
        icons = {"noaddr": "", "hasaddr": "", "paid": "", "active": ""}
        icon = icons.get(cat, "")
        vt = prof.get("black_valid_till") or prof.get("subscription_expires_str") or ""
        st = prof.get("status") or ""
        link = (
            prof.get("issued_link") or prof.get("black_short_link")
            or prof.get("black_activation_link") or ""
        )
        inv = prof.get("issued_invoice_id") or ""
        email = prof.get("buyer_email") or ""
        issued = prof.get("issued_str") or ""
        parts: list[str] = []
        if cat == "active":
            parts.append("выдан")
        elif cat == "paid":
            parts.append(st or "оплачен")
        elif cat == "hasaddr":
            parts.append("с данными")
        else:
            parts.append(st or "доступен")
        if vt:
            parts.append(f"до {vt}")
        if issued:
            parts.append(f"выдан {issued[:10]}")
        if inv:
            parts.append(f"#{inv}")
        if email:
            parts.append(email[:28])
        if link:
            short = link.replace("https://", "")[:32]
            parts.append(short + ("…" if len(link) > 36 else ""))
        return icon, phone, " · ".join(parts)

    def _refresh_profiles(self, *, force: bool = False) -> None:
        import menu as m
        prev_phone = str((self._selected_profile or {}).get("username", ""))
        self._profile_rows = m._load_done_profiles()
        flt = self._profile_filter
        sig = self._profiles_list_signature(self._profile_rows)

        if (
            not force
            and sig == self._profiles_sig
            and flt == self._profiles_filter_sig
            and self._selected_profile
        ):
            updated = next(
                (p for p in self._profile_rows if str(p.get("username", "")) == prev_phone),
                None,
            )
            if updated:
                self._selected_profile = updated
                self._render_profile_detail(updated)
            return

        self._profiles_sig = sig
        self._profiles_filter_sig = flt
        for w in self.profile_list.winfo_children():
            w.destroy()
        if not self._profile_rows:
            self._selected_profile = None
            self._render_profile_detail(None)
            ctk.CTkLabel(self.profile_list, text="Нет профилей", text_color=TEXT_DIM).pack(pady=10)
            return
        shown = [
            p for p in self._profile_rows
            if flt == "all" or self._profile_category(p) == flt
        ]
        if not shown:
            self._selected_profile = None
            self._render_profile_detail(None)
            ctk.CTkLabel(
                self.profile_list, text="В этой категории профилей нет", text_color=TEXT_DIM,
            ).pack(pady=10)
            return

        selected_row = None
        selected_prof = None
        for prof in shown:
            icon, phone, sub = self._profile_row_meta(prof)
            is_sel = prev_phone and str(prof.get("username", "")) == prev_phone
            row = ctk.CTkFrame(
                self.profile_list,
                fg_color=BG_CARD_HOVER if is_sel else BG_CARD,
                corner_radius=RADIUS_CHIP,
                border_width=2 if is_sel else 1,
                border_color=ACCENT if is_sel else BORDER_SUBTLE,
            )
            row.pack(fill="x", pady=3, padx=2)
            self._bind_row_hover(row)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)
            ctk.CTkLabel(
                inner, text=phone,
                font=_ui_font(FONT_BODY, "bold"),
                anchor="w", text_color=TEXT_PRIMARY,
            ).pack(fill="x")
            if sub:
                ctk.CTkLabel(
                    inner, text=sub, font=_ui_font(FONT_SMALL),
                    anchor="w", text_color=TEXT_DIM, wraplength=420, justify="left",
                ).pack(fill="x", pady=(2, 0))

            def _sel(e=None, p=prof, r=row):
                self._select_profile(p, r)

            for w in (row, inner):
                w.bind("<Button-1>", _sel)
                w.bind("<Double-Button-1>", _sel)
            for child in inner.winfo_children():
                child.bind("<Button-1>", _sel)
                child.bind("<Double-Button-1>", _sel)

            if is_sel:
                selected_row = row
                selected_prof = prof

        if selected_prof is None and shown:
            selected_prof = shown[0]
            first_row = self.profile_list.winfo_children()[0]
            if isinstance(first_row, ctk.CTkFrame):
                selected_row = first_row
                first_row.configure(fg_color=BG_CARD_HOVER, border_width=1, border_color=ACCENT)

        if selected_prof:
            self._select_profile(selected_prof, selected_row, refresh_list=False)

    def _select_profile(
        self, prof: dict | None, row: ctk.CTkFrame | None = None, *, refresh_list: bool = True,
    ) -> None:
        self._selected_profile = prof
        if row is not None:
            for c in self.profile_list.winfo_children():
                if isinstance(c, ctk.CTkFrame):
                    c.configure(fg_color=BG_CARD, border_width=0)
            row.configure(fg_color=BG_CARD_HOVER, border_width=1, border_color=ACCENT)
        elif refresh_list:
            self._refresh_profiles()
            return
        self._render_profile_detail(prof)

    def _bind_readonly_copyable(self, tb) -> None:
        """Текст можно выделить и Ctrl+C — но не править (disabled блокирует копирование)."""
        if getattr(tb, "_readonly_copy_bound", False):
            return

        def _on_key(e):
            # Ctrl+C / Ctrl+A / Ctrl+Insert — разрешаем
            if (e.state & 0x4) and e.keysym.lower() in ("c", "a", "insert", "с", "ф"):
                return None
            if e.keysym in (
                "Left", "Right", "Up", "Down", "Home", "End",
                "Prior", "Next", "Shift_L", "Shift_R",
                "Control_L", "Control_R", "Alt_L", "Alt_R",
                "Escape", "Tab",
            ):
                return None
            return "break"

        tb.bind("<Key>", _on_key)
        tb.bind("<<Paste>>", lambda _e: "break")
        tb.bind("<<Cut>>", lambda _e: "break")
        tb._readonly_copy_bound = True

    def _fill_readonly_text(self, tb, text: str) -> None:
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", (text or "").strip() or "—")
        self._bind_readonly_copyable(tb)

    def _copy_to_clipboard(self, text: str, *, toast_title: str = "Скопировано") -> None:
        text = (text or "").strip()
        if not text:
            self._toast("Пусто", "Нечего копировать", WARNING)
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
            preview = text if len(text) <= 72 else text[:69] + "…"
            self._toast(toast_title, preview, SUCCESS)
        except Exception as e:
            self._log(f"Буфер обмена: {e}")

    def _profile_primary_link(self, prof: dict) -> str:
        return (
            (prof.get("issued_link") or prof.get("black_short_link")
             or prof.get("black_activation_link") or "")
        ).strip()

    def _render_profile_detail(self, prof: dict | None) -> None:
        for w in self.profile_detail_actions.winfo_children():
            w.destroy()
        if not prof:
            self._fill_readonly_text(
                self.profile_detail_body,
                "Выберите профиль слева\n\nОдин клик — выбор и действия здесь.\n"
                "Текст можно выделить и скопировать (Ctrl+C).",
            )
            if hasattr(self, "profile_busy_frame"):
                self.profile_busy_frame.grid_remove()
            return

        import menu as m
        phone = str(prof.get("username", ""))
        path = prof.get("path")
        cat = self._profile_category(prof)
        link = self._profile_primary_link(prof)
        has_link = bool(link)
        is_issued = bool(prof.get("issued_ts"))
        busy = phone in self._prof_busy

        self._fill_readonly_text(self.profile_detail_body, self._profile_menu_info(prof))

        # Отдельный блок этапа + стоп (не в textbox)
        if hasattr(self, "profile_busy_frame"):
            if busy:
                stage = (
                    m.get_profile_op_stage(phone)
                    or self._prof_labels.get(phone)
                    or "Операция выполняется…"
                )
                self.profile_busy_stage.configure(text=stage)
                self.profile_busy_stop.configure(
                    command=lambda p=path, ph=phone: self._prof_stop(ph, p),
                )
                self.profile_busy_frame.grid()
            else:
                self.profile_busy_frame.grid_remove()

        if busy:
            return  # только блок этапа / стоп — без бледных disabled-кнопок

        def _btn(txt: str, cmd: Callable, color: str = BTN_SECONDARY) -> None:
            self._action_btn(
                self.profile_detail_actions, txt, cmd, color,
            ).pack(fill="x", pady=3)

        if has_link:
            _btn(
                "Копировать ссылку",
                lambda l=link: self._copy_to_clipboard(l, toast_title="Ссылка"),
                SUCCESS,
            )
        _btn(
            "Копировать данные",
            lambda p=prof: self._copy_to_clipboard(
                self._profile_menu_info(p), toast_title="Данные профиля",
            ),
            BTN_SECONDARY,
        )
        _btn("Открыть Chrome", lambda: self._prof_chrome(phone, path), BTN_SECONDARY)
        _btn("Проверить активацию", lambda: self._prof_activate(phone, path), ACCENT)

        if cat in ("noaddr", "hasaddr"):
            _btn(
                "Перейти на товар (3 мес)",
                lambda: self._prof_open_product(phone, path),
                BTN_SECONDARY,
            )
            _btn("Купить 3 мес · ₹343", lambda: self._profile_buy_for(prof, 3), ACCENT)
            _btn("Купить 12 мес · ₹1499", lambda: self._profile_buy_for(prof, 12), ACCENT)
            _btn("Заполнить адрес", lambda: self._profile_fill_address_for(prof), BTN_SECONDARY)
            _btn("Заполнить данные (до оплаты)", lambda: self._prof_fill_data(phone, path), WARNING)
            if not is_issued:
                _btn("Удалить профиль", lambda: self._prof_delete(phone, path), ERROR)

        if cat == "paid":
            _btn("Поставить статус «выдан»", lambda: self._prof_set_issued(phone, path), BTN_SECONDARY)
            if has_link:
                _btn("Заменить ссылку", lambda: self._prof_activate(phone, path), WARNING)

        if cat == "active":
            if prof.get("issued_invoice_id"):
                inv = int(prof["issued_invoice_id"])

                def _go_order() -> None:
                    self._enter_service("ggsell")
                    self._ggs_selected_id = inv
                    self.show_page("ggsell")
                    if hasattr(self, "_set_ggs_section"):
                        self._set_ggs_section("orders")
                    self.after(400, self._refresh_ggsell)

                _btn(f"Заказ GGSell #{inv}", _go_order, ACCENT)
            _btn("Перенести в архив", lambda: self._prof_archive(phone, path), BTN_SECONDARY)

    def _refresh_archive(self) -> None:
        import menu as m
        from pathlib import Path
        prev_phone = str((self._selected_archive or {}).get("username", ""))
        prev_ts = int((self._selected_archive or {}).get("used_ts") or 0)
        for w in self.archive_list.winfo_children():
            w.destroy()
        records = m._load_archive_records()
        done_phones = {str(p.get("username", "")) for p in m._load_done_profiles()}
        cookies_dir = Path("cookies_backup")
        with_cookies = sum(
            1 for r in records
            if (cookies_dir / f"cookies_{r.get('username', '')}.json").exists()
        )
        alive = sum(1 for r in records if str(r.get("username", "")) in done_phones)
        self.archive_stat_total.configure(text=f"{len(records)}")
        self.archive_stat_cookies.configure(text=f"куки {with_cookies}")
        self.archive_stat_restored.configure(text=f"живые {alive}")

        if not records:
            self._selected_archive = None
            self._render_archive_detail(None)
            ctk.CTkLabel(self.archive_list, text="Архив пуст", text_color=TEXT_DIM).pack(pady=10)
            return

        selected_row = None
        selected_rec = None
        for r in records:
            phone = r.get("username", "?")
            used = r.get("used_str", "—")
            months = r.get("subscription_months")
            sub = f" · {months} мес." if months else ""
            has_ck = (cookies_dir / f"cookies_{phone}.json").exists()
            is_alive = str(phone) in done_phones
            is_sel = (
                prev_phone and str(phone) == prev_phone
                and (not prev_ts or int(r.get("used_ts") or 0) == prev_ts)
            )
            row = ctk.CTkFrame(
                self.archive_list,
                fg_color=BG_CARD_HOVER if is_sel else BG_CARD,
                corner_radius=RADIUS_CHIP,
                border_width=2 if is_sel else 1,
                border_color=ACCENT if is_sel else BORDER_SUBTLE,
            )
            row.pack(fill="x", pady=3, padx=2)
            self._bind_row_hover(row)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)
            badges = []
            if has_ck:
                badges.append("cookies")
            if is_alive:
                badges.append("активен")
            badge_txt = (" · ".join(badges) + " · ") if badges else ""
            top = ctk.CTkFrame(inner, fg_color="transparent")
            top.pack(fill="x")
            ctk.CTkLabel(
                top, text=f"{badge_txt}+91 {phone}{sub}",
                font=_ui_font(FONT_BODY, "bold"), anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                top, text=used, text_color=TEXT_DIM, font=_ui_font(FONT_SMALL),
            ).pack(side="right")
            email = r.get("buyer_email") or r.get("email") or ""
            if email:
                ctk.CTkLabel(
                    inner, text=email[:40], text_color=TEXT_DIM,
                    font=_ui_font(FONT_SMALL), anchor="w",
                ).pack(fill="x", pady=(2, 0))

            def _sel(e=None, rec=r, rw=row):
                self._select_archive(rec, rw)

            for w in (row, inner, top):
                w.bind("<Button-1>", _sel)
                w.bind("<Double-Button-1>", _sel)
            for child in inner.winfo_children():
                child.bind("<Button-1>", _sel)
                child.bind("<Double-Button-1>", _sel)
                if hasattr(child, "winfo_children"):
                    for sub_w in child.winfo_children():
                        sub_w.bind("<Button-1>", _sel)
                        sub_w.bind("<Double-Button-1>", _sel)

            if is_sel:
                selected_row = row
                selected_rec = r

        if selected_rec is None:
            selected_rec = records[0]
            first_row = self.archive_list.winfo_children()[0]
            if isinstance(first_row, ctk.CTkFrame):
                selected_row = first_row
                first_row.configure(fg_color=BG_CARD_HOVER, border_width=2, border_color=ACCENT)

        if selected_rec:
            self._select_archive(selected_rec, selected_row, refresh_list=False)

    def _select_archive(
        self, rec: dict | None, row: ctk.CTkFrame | None = None, *, refresh_list: bool = True,
    ) -> None:
        self._selected_archive = rec
        if row is not None:
            for c in self.archive_list.winfo_children():
                if isinstance(c, ctk.CTkFrame):
                    c.configure(fg_color=BG_CARD, border_width=0)
            row.configure(fg_color=BG_CARD_HOVER, border_width=2, border_color=ACCENT)
        elif refresh_list:
            self._refresh_archive()
            return
        self._render_archive_detail(rec)

    def _archive_record_info(self, rec: dict) -> str:
        import menu as m
        from pathlib import Path
        phone = m._disp_phone(rec.get("username", "?"))
        lines = [phone, ""]
        if rec.get("login_str"):
            lines.append(f"Создан: {rec['login_str']}")
        if rec.get("issued_str"):
            lines.append(f"Выдан: {rec['issued_str']}")
        if rec.get("used_str"):
            lines.append(f"В архиве: {rec['used_str']}")
        inv = rec.get("issued_invoice_id")
        if inv:
            em = rec.get("buyer_email") or rec.get("email") or ""
            lines.append(f"Заказ #{inv}" + (f" · {em}" if em else ""))
        months = rec.get("subscription_months")
        if months:
            lines.append(f"Подписка: {months} мес.")
        vt = rec.get("black_valid_till") or rec.get("subscription_expires_str")
        if vt:
            lines.append(f"До: {vt}")
        link = rec.get("issued_link") or rec.get("black_short_link") or ""
        if link:
            lines.append(link)
        ck = Path("cookies_backup") / f"cookies_{rec.get('username', '')}.json"
        lines.append(f"Куки: {'есть' if ck.exists() else 'нет'}")
        note = rec.get("note") or ""
        if note:
            lines.append(note)
        return "\n".join(lines)

    def _render_archive_detail(self, rec: dict | None) -> None:
        for w in self.archive_detail_actions.winfo_children():
            w.destroy()
        self.archive_detail_body.configure(state="normal")
        self.archive_detail_body.delete("1.0", "end")
        if not rec:
            self.archive_detail_body.insert(
                "1.0",
                "Выберите запись слева\n\nВосстановление профиля, куки и удаление — здесь.",
            )
            self.archive_detail_body.configure(state="disabled")
            return

        import menu as m
        from pathlib import Path
        phone = str(rec.get("username", ""))
        busy = phone in self._prof_busy
        ck_file = Path("cookies_backup") / f"cookies_{phone}.json"
        has_ck = ck_file.exists()
        done_exists = (Path("chrome_profiles_done") / f"profile_{phone}").exists()

        self.archive_detail_body.insert("1.0", self._archive_record_info(rec))
        if done_exists:
            self.archive_detail_body.insert("end", "\n\n✓ Профиль уже в chrome_profiles_done")
        self.archive_detail_body.configure(state="disabled")

        if busy:
            stage = (
                m.get_profile_op_stage(phone)
                or self._prof_labels.get(phone)
                or "Операция…"
            )
            card = ctk.CTkFrame(
                self.archive_detail_actions, fg_color=ACCENT_SOFT,
                corner_radius=RADIUS_SM, border_width=1, border_color=WARNING,
            )
            card.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(
                card, text="В процессе", font=_ui_font(FONT_SMALL, "bold"),
                text_color=WARNING, anchor="w",
            ).pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(
                card, text=stage, font=_ui_font(FONT_CAPTION),
                text_color=TEXT_PRIMARY, anchor="w", wraplength=220,
            ).pack(fill="x", padx=10, pady=(2, 6))
            self._action_btn(
                card, "Остановить",
                lambda: self._prof_stop(phone, None), ERROR,
            ).pack(fill="x", padx=10, pady=(0, 10))
            return

        def _btn(txt: str, cmd: Callable, color: str = BTN_SECONDARY, enabled: bool = True) -> None:
            state = "normal" if enabled else "disabled"
            self._action_btn(
                self.archive_detail_actions, txt, cmd, color, state=state,
            ).pack(fill="x", pady=3)

        _btn(
            "Восстановить профиль",
            lambda: self._archive_restore(rec),
            ACCENT,
            enabled=not done_exists,
        )
        if has_ck:
            _btn(
                "Восстановить из куков",
                lambda: self._archive_restore_cookies(rec, ck_file),
                ACCENT,
            )
            _btn(
                "Открыть файл куков",
                lambda: self._open_path(ck_file),
                BTN_SECONDARY,
            )
        _btn(
            "Папка cookies_backup",
            lambda: self._open_folder("cookies_backup"),
            BTN_SECONDARY,
        )
        _btn("Удалить запись", lambda: self._archive_delete(rec), ERROR)

    def _archive_restore(self, rec: dict) -> None:
        phone = str(rec.get("username", ""))
        if not messagebox.askyesno("Восстановление", f"Восстановить профиль {phone} в chrome_profiles_done?"):
            return

        def _w():
            import menu as m
            ok, msg = m.restore_archive_record(rec)
            self._log(f"{'✓' if ok else '✗'} {msg}")
        self._prof_run(phone, f"Восстановление {phone}…", _w)

    def _archive_restore_cookies(self, rec: dict, ck_file) -> None:
        phone = str(rec.get("username", ""))
        if not messagebox.askyesno(
            "Куки",
            f"Восстановить сессию {phone} из cookies_backup?\n(создаст/обновит профиль)",
        ):
            return

        def _w():
            import menu as m
            ok, msg = asyncio.run(m._restore_profile_from_cookies(ck_file, phone))
            self._log(f"{'✓' if ok else '✗'} {msg}")
        self._prof_run(phone, f"Восстановление из куков {phone}…", _w)

    def _archive_delete(self, rec: dict) -> None:
        phone = str(rec.get("username", "?"))
        if not messagebox.askyesno("Удаление", f"Удалить запись архива {phone}?"):
            return

        def _w():
            import menu as m
            ok, msg = m.delete_archive_record(rec)
            self._log(f"{'✓' if ok else '✗'} {msg}")
            self._selected_archive = None
            self._run_on_main( self._refresh_archive)
        self._prof_run(phone, f"Удаление записи {phone}…", _w)

    def _open_path(self, path) -> None:
        import os
        import subprocess
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            self._log(f"Файл не найден: {p}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(p))
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            self._log(f"Не удалось открыть: {e}")

    def _ordered_bank_indices(self, n: int, order: list) -> list[int]:
        if order:
            idx = [i for i in order if 0 <= i < n]
            idx += [i for i in range(n) if i not in idx]
            return idx
        return list(range(n))

    def _set_cards_tab(self, tab: str) -> None:
        self._cards_tab = tab
        for key, btn in self._cards_tab_btns.items():
            self._sync_chip_btn(btn, key == tab)
        if tab != "gift" and self._gift_add_visible:
            self.gift_add_panel.grid_forget()
            self._gift_add_visible = False
        if tab == "history":
            self.cards_detail.grid_remove()
            self.cards_list_wrap.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=0)
        else:
            self.cards_detail.grid()
            self.cards_list_wrap.grid(row=0, column=0, columnspan=1, sticky="nsew", padx=(0, 6))
        self._refresh_cards()

    def _rebuild_cards_toolbar(self) -> None:
        for w in self.cards_toolbar.winfo_children():
            w.destroy()
        tab = self._cards_tab
        if tab == "bank":
            specs = [
                ("Добавить", self._show_add_card_dialog, SUCCESS),
                ("Изменить", self._edit_selected_bank_card, BTN_SECONDARY),
                ("Удалить", self._delete_card, ERROR),
                ("Вверх", lambda: self._move_bank_card(-1), BTN_SECONDARY),
                ("Вниз", lambda: self._move_bank_card(1), BTN_SECONDARY),
                ("Порядок", self._reset_bank_order, BTN_SECONDARY),
            ]
        elif tab == "gift":
            specs = [
                ("Добавить", self._toggle_gift_add_panel, SUCCESS),
                ("Файл", self._upload_gift_file, BTN_SECONDARY),
                ("Изменить", self._edit_selected_gift_card, BTN_SECONDARY),
                ("Удалить", self._delete_gift_card, ERROR),
                ("Способ оплаты", self._toggle_pay_method, WARNING),
            ]
        else:
            specs = [("Обновить", self._refresh_cards, BTN_SECONDARY)]
        for txt, cmd, color in specs:
            self._action_btn(
                self.cards_toolbar, txt, cmd, color,
            ).pack(side="left", padx=(0, 4))

    def _cards_row(
        self, parent, pos: int, title: str, subtitle: str, badge: str,
        selected: bool, on_select: Callable,
    ) -> ctk.CTkFrame:
        bg = BG_CARD_HOVER if selected else BG_CARD
        row = ctk.CTkFrame(
            parent, fg_color=bg, corner_radius=RADIUS_CHIP,
            border_width=2 if selected else 1,
            border_color=ACCENT if selected else BORDER_SUBTLE,
        )
        row.pack(fill="x", pady=3, padx=4)
        if not selected:
            self._bind_row_hover(row)

        def _bind(widget) -> None:
            widget.bind("<Button-1>", lambda _e: on_select())

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkLabel(
            left, text=f"  {pos}. {title}", font=_ui_font(FONT_BODY, "bold"),
            anchor="w", text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                left, text=subtitle, font=_ui_font(FONT_SMALL),
                anchor="w", text_color=TEXT_DIM,
            ).pack(anchor="w", pady=(2, 0))
        if badge:
            ctk.CTkLabel(
                row, text=badge, font=_ui_font(FONT_CAPTION, "bold"),
                fg_color=BG_ELEVATED, corner_radius=RADIUS_CHIP,
                text_color=TEXT_PRIMARY, padx=8, pady=4,
            ).pack(side="right", padx=10, pady=8)
        for w in (row, left):
            _bind(w)
            for ch in w.winfo_children():
                _bind(ch)
        return row

    def _fill_gift_order_panel(self, gc: list, bal: int, pm_txt: str) -> None:
        """Видимый блок: всего карт, по номиналам, порядок очереди, действия."""
        for w in self.gift_order_panel.winfo_children():
            w.destroy()

        by_denom: dict[int, int] = {}
        for c in gc:
            d = int(c.get("denom") or 0)
            if d > 0:
                by_denom[d] = by_denom.get(d, 0) + 1

        hdr = ctk.CTkFrame(self.gift_order_panel, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            hdr, text="Очередь и сводка",
            font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY,
        ).pack(side="left")
        ctk.CTkLabel(
            hdr, text=f"₹{bal}",
            font=_ui_font(FONT_SECTION, "bold"), text_color=SUCCESS,
        ).pack(side="right")

        # Метрики: всего + способ оплаты
        metrics = ctk.CTkFrame(self.gift_order_panel, fg_color="transparent")
        metrics.pack(fill="x", padx=14, pady=(0, 8))
        for label, value, color in (
            ("Всего карт", str(len(gc)), TEXT_PRIMARY),
            ("Оплата", pm_txt, ACCENT),
        ):
            chip = ctk.CTkFrame(
                metrics, fg_color=BG_ELEVATED, corner_radius=RADIUS_SM,
                border_width=1, border_color=BORDER_SUBTLE,
            )
            chip.pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                chip, text=label.upper(), font=_ui_font(FONT_CAPTION),
                text_color=TEXT_MUTED,
            ).pack(anchor="w", padx=10, pady=(6, 0))
            ctk.CTkLabel(
                chip, text=value, font=_ui_font(FONT_BODY, "bold"),
                text_color=color,
            ).pack(anchor="w", padx=10, pady=(0, 6))

        # Номиналы — отдельная полоса
        ctk.CTkLabel(
            self.gift_order_panel, text="По номиналам",
            font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 4))
        den_row = ctk.CTkFrame(self.gift_order_panel, fg_color="transparent")
        den_row.pack(fill="x", padx=14, pady=(0, 8))
        if not by_denom:
            ctk.CTkLabel(
                den_row, text="карт нет", text_color=TEXT_DIM,
                font=_ui_font(FONT_CAPTION),
            ).pack(side="left")
        else:
            for d in sorted(by_denom, reverse=True):
                cnt = by_denom[d]
                cell = ctk.CTkFrame(
                    den_row, fg_color=ACCENT_SOFT, corner_radius=RADIUS_CHIP,
                    border_width=1, border_color=BORDER_SUBTLE,
                )
                cell.pack(side="left", padx=(0, 6), pady=2)
                ctk.CTkLabel(
                    cell, text=f"₹{d}",
                    font=_ui_font(FONT_BODY, "bold"), text_color=ACCENT,
                ).pack(side="left", padx=(10, 4), pady=6)
                ctk.CTkLabel(
                    cell, text=f"×{cnt}",
                    font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_PRIMARY,
                    fg_color=BG_ELEVATED, corner_radius=RADIUS_SM, padx=6, pady=2,
                ).pack(side="left", padx=(0, 8), pady=6)

        # Порядок очереди (видимая лента)
        ctk.CTkLabel(
            self.gift_order_panel, text="Порядок применения  ·  слева первая",
            font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 4))
        q_wrap = ctk.CTkFrame(
            self.gift_order_panel, fg_color=BG_SURFACE, corner_radius=RADIUS_SM,
            border_width=1, border_color=BORDER_SUBTLE,
        )
        q_wrap.pack(fill="x", padx=14, pady=(0, 8))
        q_row = ctk.CTkFrame(q_wrap, fg_color="transparent")
        q_row.pack(fill="x", padx=8, pady=8)
        if not gc:
            ctk.CTkLabel(
                q_row, text="Добавьте карты — порядок появится здесь",
                text_color=TEXT_DIM, font=_ui_font(FONT_CAPTION),
            ).pack(side="left")
        else:
            show_n = min(len(gc), 10)
            for i in range(show_n):
                d = int(gc[i].get("denom") or 0)
                is_first = i == 0
                is_sel = i == self._sel_gift_idx
                bg = SUCCESS if is_first else (ACCENT_SOFT if is_sel else BG_ELEVATED)
                fg = TEXT_ON_ACCENT if is_first else (ACCENT if is_sel else TEXT_PRIMARY)
                border = SUCCESS if is_first else (ACCENT if is_sel else BORDER_SUBTLE)
                pill = ctk.CTkFrame(
                    q_row, fg_color=bg, corner_radius=RADIUS_CHIP,
                    border_width=1, border_color=border,
                )
                pill.pack(side="left", padx=(0, 4))
                ctk.CTkLabel(
                    pill, text=f"{i + 1}",
                    font=_ui_font(FONT_CAPTION, "bold"), text_color=fg,
                    width=18,
                ).pack(side="left", padx=(8, 2), pady=5)
                ctk.CTkLabel(
                    pill, text=f"₹{d}",
                    font=_ui_font(FONT_CAPTION, "bold"), text_color=fg,
                ).pack(side="left", padx=(0, 8), pady=5)
                if i < show_n - 1:
                    ctk.CTkLabel(
                        q_row, text="→", font=_ui_font(FONT_CAPTION),
                        text_color=TEXT_MUTED,
                    ).pack(side="left", padx=(0, 4))
            if len(gc) > show_n:
                ctk.CTkLabel(
                    q_row, text=f"+{len(gc) - show_n}",
                    font=_ui_font(FONT_CAPTION, "bold"), text_color=TEXT_DIM,
                ).pack(side="left", padx=(4, 0))

        # Действия порядка — вместо потерянной кнопки в тулбаре
        acts = ctk.CTkFrame(self.gift_order_panel, fg_color="transparent")
        acts.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            acts, text="Выбранную карту:",
            font=_ui_font(FONT_CAPTION), text_color=TEXT_DIM,
        ).pack(side="left", padx=(0, 8))
        has_sel = self._sel_gift_idx is not None and 0 <= self._sel_gift_idx < len(gc)
        self._action_btn(
            acts, "В начало очереди", self._gift_to_front, SUCCESS,
        ).pack(side="left", padx=(0, 6))
        self._action_btn(
            acts, "В конец", self._gift_to_end, BTN_SECONDARY,
        ).pack(side="left", padx=(0, 6))
        if not has_sel:
            ctk.CTkLabel(
                acts, text="сначала выберите карту в списке",
                font=_ui_font(FONT_CAPTION), text_color=TEXT_MUTED,
            ).pack(side="left", padx=(8, 0))

    def _gift_queue_row(
        self, parent, idx: int, total: int, denom: int,
        series: str, pin: str, selected: bool, on_select: Callable,
    ) -> ctk.CTkFrame:
        """Строка гифт-карты: № в очереди, номинал, ↑↓ для перестановки."""
        is_next = idx == 0
        bg = BG_CARD_HOVER if selected else BG_CARD
        border = SUCCESS if is_next and not selected else (ACCENT if selected else BORDER_SUBTLE)
        row = ctk.CTkFrame(
            parent, fg_color=bg, corner_radius=RADIUS_CHIP,
            border_width=2 if (selected or is_next) else 1,
            border_color=border,
        )
        row.pack(fill="x", pady=3, padx=4)
        if not selected:
            self._bind_row_hover(row)

        def _bind(widget) -> None:
            widget.bind("<Button-1>", lambda _e: on_select())

        # № очереди
        pos_bg = SUCCESS if is_next else BG_ELEVATED
        pos_fg = "#0B1020" if is_next else TEXT_PRIMARY
        pos = ctk.CTkLabel(
            row, text=str(idx + 1), width=36, height=36,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=pos_bg, corner_radius=RADIUS_CHIP,
            text_color=pos_fg,
        )
        pos.pack(side="left", padx=(10, 8), pady=8)

        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.pack(side="left", fill="x", expand=True, pady=6)
        title = f"₹{denom}"
        if is_next:
            title += "  ·  следующая"
        ctk.CTkLabel(
            mid, text=title, font=_ui_font(FONT_BODY, "bold"),
            anchor="w", text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ser = f"…{str(series)[-6:]}" if series else "—"
        pin_s = f"…{str(pin)[-4:]}" if pin else "—"
        ctk.CTkLabel(
            mid, text=f"серия {ser}  ·  PIN {pin_s}",
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            anchor="w", text_color=TEXT_DIM,
        ).pack(anchor="w", pady=(2, 0))

        # ↑↓ прямо в строке — меняют местами с соседней
        moves = ctk.CTkFrame(row, fg_color="transparent")
        moves.pack(side="right", padx=(4, 8), pady=6)

        def _up() -> None:
            self._sel_gift_idx = idx
            self._move_gift_card(-1)

        def _down() -> None:
            self._sel_gift_idx = idx
            self._move_gift_card(1)

        up = ctk.CTkButton(
            moves, text="↑", width=32, height=28,
            fg_color=BG_ELEVATED, hover_color=BG_NAV_ACTIVE,
            text_color=TEXT_PRIMARY, font=_ui_font(FONT_BODY, "bold"),
            command=_up, state="normal" if idx > 0 else "disabled",
        )
        up.pack(side="top", pady=(0, 2))
        down = ctk.CTkButton(
            moves, text="↓", width=32, height=28,
            fg_color=BG_ELEVATED, hover_color=BG_NAV_ACTIVE,
            text_color=TEXT_PRIMARY, font=_ui_font(FONT_BODY, "bold"),
            command=_down, state="normal" if idx < total - 1 else "disabled",
        )
        down.pack(side="top")

        for w in (row, mid, pos):
            _bind(w)
            if hasattr(w, "winfo_children"):
                for ch in w.winfo_children():
                    if ch not in (up, down) and not isinstance(ch, ctk.CTkButton):
                        _bind(ch)
        return row

    def _set_cards_detail(self, text: str) -> None:
        self.cards_detail_body.configure(state="normal")
        self.cards_detail_body.delete("1.0", "end")
        self.cards_detail_body.insert("1.0", text.strip() or "Выберите элемент в списке слева")
        self.cards_detail_body.configure(state="disabled")

    def _refresh_cards(self) -> None:
        import menu as m
        self._rebuild_cards_toolbar()

        cards = m._load_cards()
        order = m._load_card_order()
        ordered = self._ordered_bank_indices(len(cards), order)
        gc = m._load_gift_cards()
        bal = m._gift_balance(gc)
        pm = m._load_pay_method()
        pm_txt = "гифт-карты" if pm == "gift" else "банковская"

        self.cards_stat_bank.configure(text=f"банк {len(cards)}")
        self.cards_stat_gift.configure(text=f"гифт {len(gc)}")
        self.cards_stat_balance.configure(text=f"₹{bal}")
        self.cards_stat_pay.configure(text=pm_txt)

        if self._gift_add_visible and self._cards_tab == "gift":
            self.gift_add_panel.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        else:
            self.gift_add_panel.grid_forget()

        if self._cards_tab == "gift":
            self._fill_gift_order_panel(gc, bal, pm_txt)
            self.gift_order_panel.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        else:
            self.gift_order_panel.grid_forget()

        self.cards_list.grid(row=2, column=0, sticky="nsew")

        for w in self.cards_list.winfo_children():
            w.destroy()

        tab = self._cards_tab
        if tab == "bank":
            if self._sel_bank_idx is not None and not (0 <= self._sel_bank_idx < len(cards)):
                self._sel_bank_idx = None
            if not cards:
                ctk.CTkLabel(
                    self.cards_list, text="Банковских карт нет\nНажмите «Добавить»",
                    text_color=TEXT_DIM, justify="center",
                ).pack(pady=40)
                self._set_cards_detail("")
            else:
                for pos, idx in enumerate(ordered, 1):
                    c = cards[idx]
                    nick = c.get("nickname") or c.get("name") or f"Карта {idx + 1}"
                    num = m._mask_card(c.get("number", ""))
                    exp = c.get("expiry", "—")
                    sel = idx == self._sel_bank_idx

                    def _pick(i=idx):
                        self._sel_bank_idx = i
                        self._refresh_cards()

                    self._cards_row(
                        self.cards_list, pos, nick,
                        f"{num}  ·  {exp}",
                        "★" if pos == 1 else "",
                        sel, _pick,
                    )
                if self._sel_bank_idx is not None:
                    c = cards[self._sel_bank_idx]
                    detail = (
                        f"Название: {c.get('nickname') or '—'}\n"
                        f"Номер: {c.get('number', '—')}\n"
                        f"Срок: {c.get('expiry', '—')}\n"
                        f"CVV: {c.get('cvv', '—')}\n"
                        f"Имя: {c.get('name', '—')}\n\n"
                        f"Страна: {c.get('country', '—')}\n"
                        f"Индекс: {c.get('zipcode', '—')}\n"
                        f"Штат: {c.get('state', '—')}\n"
                        f"Город: {c.get('city', '—')}\n"
                        f"Адрес: {c.get('address', '—')}\n\n"
                        f"Порядок оплаты: "
                        f"{ordered.index(self._sel_bank_idx) + 1} из {len(cards)}"
                    )
                    self._set_cards_detail(detail)
                else:
                    seq = " → ".join(str(i + 1) for i in ordered)
                    self._set_cards_detail(
                        f"Всего карт: {len(cards)}\n"
                        f"Порядок попытки оплаты: {seq}\n\n"
                        "Выберите карту слева для просмотра и редактирования.\n"
                        "Кнопки ↑ ↓ меняют порядок для всех покупок."
                    )

        elif tab == "gift":
            self._selected_gift = None
            if self._sel_gift_idx is not None and not (0 <= self._sel_gift_idx < len(gc)):
                self._sel_gift_idx = None
            if not gc:
                ctk.CTkLabel(
                    self.cards_list, text="Гифт-карт нет\nДобавьте вручную или из файла",
                    text_color=TEXT_DIM, justify="center",
                ).pack(pady=40)
                self._set_cards_detail("")
            else:
                for idx, c in enumerate(gc):
                    denom = int(c.get("denom") or 0)
                    series = str(c.get("number", "") or "")
                    pin = str(c.get("pin", "") or "")
                    sel = idx == self._sel_gift_idx

                    def _pick(p=idx, card=c):
                        self._sel_gift_idx = p
                        self._selected_gift = card
                        self._refresh_cards()

                    self._gift_queue_row(
                        self.cards_list, idx, len(gc), denom,
                        series, pin, sel, _pick,
                    )
                if self._sel_gift_idx is not None:
                    c = gc[self._sel_gift_idx]
                    pos = self._sel_gift_idx + 1
                    role = "следующая к применению" if self._sel_gift_idx == 0 else f"позиция {pos}"
                    self._set_cards_detail(
                        f"Номинал: ₹{int(c.get('denom') or 0)}\n"
                        f"Серия: {c.get('number', '—')}\n"
                        f"PIN: {c.get('pin', '—')}\n"
                        f"Добавлена: {m._fmt_msk(c['added_ts']) if c.get('added_ts') else '—'}\n\n"
                        f"В очереди: {pos} из {len(gc)} ({role})\n"
                        f"Способ оплаты: {pm_txt}\n\n"
                        "Порядок и сводка — в блоке над списком.\n"
                        "↑↓ в строке — сдвинуть на одну позицию."
                    )
                else:
                    self._set_cards_detail(
                        f"Всего: {len(gc)} шт.  ·  Баланс ₹{bal}\n"
                        f"Способ оплаты: {pm_txt}\n\n"
                        "Сводка и порядок — в блоке над списком.\n"
                        "Выберите карту или сдвиньте ↑↓ в строке."
                    )

        else:
            used = m._load_gift_used()
            if not used:
                ctk.CTkLabel(
                    self.cards_list, text="История пуста",
                    text_color=TEXT_DIM,
                ).pack(pady=40)
            else:
                for i, u in enumerate(reversed(used), 1):
                    st = "↩ другой акк." if u.get("status") == "used_elsewhere" else "✔ применена"
                    when = u.get("used_str") or "—"
                    prof = u.get("profile") or "—"
                    denom = int(u.get("denom") or 0)
                    series = u.get("number", "")
                    pin = u.get("pin", "")
                    row = ctk.CTkFrame(self.cards_list, fg_color=BG_CARD, corner_radius=RADIUS_CHIP)
                    row.pack(fill="x", pady=3, padx=4)
                    txt = (
                        f"  {i}. {st}  ₹{denom}\n"
                        f"      серия …{str(series)[-6:]}  PIN …{str(pin)[-4:]}\n"
                        f"      {when}  ·  {prof}"
                    )
                    ctk.CTkLabel(
                        row, text=txt, justify="left", anchor="w",
                        font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
                        text_color=TEXT_PRIMARY,
                    ).pack(anchor="w", padx=10, pady=8)
            self._set_cards_detail(
                f"Записей в истории: {len(used)}\n"
                "Показаны использованные гифт-карты (новые сверху)."
            )

        self._refresh_youtube_hub()
        if hasattr(self, "ds_card_box"):
            self._refresh_deepseek()

    _VPN_BOOTSTRAP_TERMINAL = frozenset(("ready", "error", "no_ext"))

    def _poll_vpn_bootstrap(self, attempt: int = 0) -> None:
        """Обновляет вкладку VPN, пока фоновая установка расширений не завершится."""
        import menu as m
        if hasattr(self, "vpn_page_status"):
            self._refresh_vpn_page()
        state = m.sync_vpn_extension_status().get("state", "idle")
        if state in self._VPN_BOOTSTRAP_TERMINAL or attempt >= 120:
            return
        self.after(2000, lambda: self._poll_vpn_bootstrap(attempt + 1))

    def _refresh_vpn_page(self) -> None:
        """Страница VPN удалена (расширения убраны из проекта) — no-op."""
        return

    def _sync_run_page_status(self) -> None:
        import menu as m
        ext, ast = m.shared_automation_running()
        local = self._proc and self._proc.poll() is None
        if ext and not local:
            mode = ast.get("automation_mode") or "автоматизация"
            self.run_start_btn.configure(state="disabled")
            self.run_stop_btn.configure(state="normal")
            self._set_run_status_ui(f"Telegram: {mode}", WARNING)
            self._set_run_form_enabled(False)
        elif local:
            self.run_start_btn.configure(state="disabled")
            self.run_stop_btn.configure(state="normal")
            if not str(self.run_status.cget("text")).startswith("Telegram:"):
                self._set_run_status_ui("Выполняется…", WARNING)
            self._set_run_form_enabled(False)
        elif not str(self.run_status.cget("text")).startswith("Telegram:"):
            self.run_start_btn.configure(state="normal")
            self.run_stop_btn.configure(state="disabled")
            if self.run_status.cget("text") in ("Выполняется…", "Проверка VPN…"):
                self._set_run_status_ui("Готов к запуску", TEXT_DIM)
            self._set_run_form_enabled(True)

    def _apply_vpn_check_result(self, ok: bool, msg: str) -> None:
        color = SUCCESS if ok else ERROR
        self._vpn_last_check = msg
        if hasattr(self, "run_status") and getattr(self, "_vpn_check_for_run", False):
            self._set_run_status_ui(msg, color)
        elif hasattr(self, "run_status"):
            self._sync_run_page_status()
        self._refresh_vpn_page()
        self._vpn_check_for_run = False

    def _append_run_log(self, text: str, max_lines: int = 200) -> None:
        if not hasattr(self, "run_log_text"):
            return
        try:
            self.run_log_text.configure(state="normal")
            self.run_log_text.insert("end", text)
            if max_lines > 0:
                lines = self.run_log_text.get("1.0", "end").splitlines()
                if len(lines) > max_lines:
                    self.run_log_text.delete("1.0", f"{len(lines) - max_lines + 1}.0")
            self.run_log_text.see("end")
            self.run_log_text.configure(state="disabled")
        except Exception:
            pass

    def _clear_run_log(self) -> None:
        if not hasattr(self, "run_log_text"):
            return
        try:
            self.run_log_text.configure(state="normal")
            self.run_log_text.delete("1.0", "end")
            self.run_log_text.configure(state="disabled")
        except Exception:
            pass

    def _set_run_status_ui(self, text: str, color: str = TEXT_DIM) -> None:
        if hasattr(self, "run_status"):
            self.run_status.configure(text=text, text_color=color)
        if hasattr(self, "run_status_dot"):
            self.run_status_dot.configure(fg_color=color)
        if hasattr(self, "run_stat_state"):
            short = text.replace("Telegram: ", "").strip()[:22].upper()
            border = color if color not in (TEXT_DIM, TEXT_MUTED) else BORDER_SUBTLE
            self.run_stat_state.configure(
                text=short or "—", text_color=color, border_color=border,
                fg_color=_lerp_hex(color, BG_SURFACE, 0.88),
            )

    def _set_run_form_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        ro = "readonly" if enabled else "disabled"
        for w in (getattr(self, "run_mode", None), getattr(self, "run_tariff", None)):
            if w:
                try:
                    w.configure(state=ro)
                except Exception:
                    pass
        if getattr(self, "run_accounts", None):
            try:
                self.run_accounts.configure(state=state)
            except Exception:
                pass
        if getattr(self, "run_headless", None):
            try:
                self.run_headless.configure(state=state)
            except Exception:
                pass
        for btn in getattr(self, "_run_preset_btns", {}).values():
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _run_mode_key(self) -> str:
        mapping = {
            "Полный цикл (вход + покупка)": "full",
            "До оплаты (существующий профиль)": "payment",
            "Только вход на ПК": "login_pc",
            "Вход + Telegram (перехват)": "tg_intercept",
            "Вход с данными (до email)": "email",
        }
        return mapping.get(self.run_mode.get(), "full")

    def _select_run_preset(self, key: str) -> None:
        self._run_preset_key = key
        self._preset_run(key)
        for k, btn in getattr(self, "_run_preset_btns", {}).items():
            self._sync_chip_btn(btn, k == key)
        self._on_run_param_change()

    def _on_run_param_change(self, *_args) -> None:
        key = self._run_mode_key()
        self._run_preset_key = key
        for k, btn in getattr(self, "_run_preset_btns", {}).items():
            self._sync_chip_btn(btn, k == key)
        self._update_run_cmd_preview()

    def _update_run_cmd_preview(self) -> None:
        if not hasattr(self, "run_cmd_preview"):
            return
        try:
            cmd = self._build_run_cmd()
            rel = cmd[2:] if len(cmd) > 2 else cmd
            self.run_cmd_preview.configure(text=" ".join(rel))
        except Exception:
            pass

    def _refresh_run_page(self) -> None:
        import menu as m
        try:
            n = len(m._load_done_profiles())
            # dash_profiles — число; отдельный run_stat_profiles — если жив
            if getattr(self, "run_stat_profiles", None) is not getattr(self, "dash_profiles", None):
                if hasattr(self, "run_stat_profiles"):
                    self.run_stat_profiles.configure(text=f"Профили {n}")
            vs = m.get_vpn_bg_status()
            state = vs.get("state", "idle")
            vpn_labels = {
                "ready": ("OK", SUCCESS),
                "warming": ("…", WARNING),
                "installing": ("…", WARNING),
                "error": ("ошибка", ERROR),
                "no_ext": ("нет", TEXT_DIM),
                "idle": ("—", TEXT_DIM),
            }
            vpn_txt, vpn_col = vpn_labels.get(state, ("—", TEXT_DIM))
            if hasattr(self, "run_stat_vpn"):
                with contextlib.suppress(Exception):
                    self.run_stat_vpn.configure(text=f"VPN {vpn_txt}", text_color=vpn_col)
            if hasattr(self, "run_pay_chip"):
                pm = m._load_pay_method()
                if pm == "gift":
                    self.run_pay_chip.configure(
                        text="Оплата · гифт", text_color=ACCENT,
                        fg_color=ACCENT_SOFT, border_color=ACCENT,
                    )
                else:
                    self.run_pay_chip.configure(
                        text="Оплата · карта", text_color=TEXT_DIM,
                        fg_color=BG_SURFACE, border_color=BORDER_SUBTLE,
                    )
        except Exception:
            pass
        self._update_run_cmd_preview()
        threading.Thread(target=self._fetch_run_balance, daemon=True, name="run-bal").start()

    def _fetch_run_balance(self) -> None:
        try:
            import grizzly as gz
            bal = gz.get_balance()
            self._run_on_main(lambda: self.run_stat_balance.configure(text=f"SMS ${bal:.2f}"))
        except Exception:
            self._run_on_main(lambda: self.run_stat_balance.configure(text="SMS —"))

    # ── Run ───────────────────────────────────────────────────────────────────

    def _preset_run(self, mode: str) -> None:
        mapping = {
            "full": "Полный цикл (вход + покупка)",
            "payment": "До оплаты (существующий профиль)",
            "login_pc": "Только вход на ПК",
            "tg_intercept": "Вход + Telegram (перехват)",
            "email": "Вход с данными (до email)",
        }
        self.run_mode.set(mapping.get(mode, mapping["full"]))

    def _build_run_cmd(self) -> list[str]:
        mode = self.run_mode.get()
        months = 12 if "12" in self.run_tariff.get() else 3
        accounts = self.run_accounts.get().strip()
        headless = self.run_headless.get()
        if mode == "Полный цикл (вход + покупка)":
            cmd = [sys.executable, str(_HERE / "menu.py"), "--full-cycle", "--tariffs", str(months)]
        elif mode == "До оплаты (существующий профиль)":
            cmd = [sys.executable, str(_HERE / "menu.py"), "--fill-to-payment"]
        elif mode == "Вход с данными (до email)":
            cmd = [sys.executable, str(_HERE / "menu.py"), "--full-cycle",
                   "--stop-at-email", "--tariffs", str(months)]
        elif mode == "Вход + Telegram (перехват)":
            cmd = [sys.executable, str(_HERE / "main.py"), "--tg-intercept"]
        else:
            cmd = [sys.executable, str(_HERE / "main.py"), "--tg-login"]
        if accounts.isdigit() and int(accounts) > 0:
            cmd += ["--accounts", accounts]
        if headless:
            cmd.append("--headless")
        return cmd

    def _start_run(self) -> None:
        import menu as m
        ext, _ = m.shared_automation_running()
        if ext:
            self._log("Уже выполняется (Telegram или другое окно)")
            return
        if self._proc and self._proc.poll() is None:
            self._log("Уже выполняется")
            return
        cmd = self._build_run_cmd()
        self._log(f"▶ {' '.join(cmd)}")
        self._run_log_active = True
        self._clear_run_log()
        self._append_run_log(f"▶ {' '.join(cmd)}\n\n")
        self._set_run_status_ui("Выполняется…", WARNING)
        self._set_run_form_enabled(False)
        if hasattr(self, "run_progress"):
            with contextlib.suppress(Exception):
                self.run_progress.pack(fill="x", pady=(10, 0))
            with contextlib.suppress(Exception):
                self.run_progress.start()
        self.run_start_btn.configure(state="disabled")
        self.run_stop_btn.configure(state="normal")
        flags = 0
        if os.name == "nt":
            flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

        def _worker():
            try:
                self._proc = subprocess.Popen(
                    cmd, cwd=str(_HERE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", creationflags=flags,
                )
                m.set_automation_proc(self._proc.pid, " ".join(cmd[-3:]), "app")
                assert self._proc.stdout
                for line in self._proc.stdout:
                    self._log_sink.write(line)
                    with contextlib.suppress(Exception):
                        sys.stdout.write(line)
                code = self._proc.wait() if self._proc else -1
                m.clear_automation_proc()
                self._run_on_main( lambda: self._run_finished(code))
            except Exception as e:
                m.clear_automation_proc()
                self._run_on_main( lambda: self._run_finished(-1, str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _run_finished(self, code: int, err: str = "") -> None:
        self._run_log_active = False
        if hasattr(self, "run_progress"):
            try:
                self.run_progress.stop()
            except Exception:
                pass
            self.run_progress.pack_forget()
        self.run_start_btn.configure(state="normal")
        self.run_stop_btn.configure(state="disabled")
        self._set_run_form_enabled(True)
        if err:
            self._set_run_status_ui(f"Ошибка: {err}", ERROR)
            self._append_run_log(f"\n■ Ошибка: {err}\n")
        elif code == 0:
            self._set_run_status_ui("✓ Завершено", SUCCESS)
            self._append_run_log(f"\n■ Завершено успешно (код 0)\n")
        else:
            self._set_run_status_ui(f"Код выхода {code}", WARNING)
            self._append_run_log(f"\n■ Завершено с кодом {code}\n")
        self._log(f"■ Завершено (код {code})")
        try:
            import menu as m
            m.disconnect_vpn_on_shutdown()
        except Exception:
            pass
        self._refresh_run_page()
        self._refresh_youtube_hub()
        self._ensure_window_visible()

    def _stop_run(self) -> None:
        import menu as m
        m.disconnect_vpn_on_shutdown()
        if self._proc and self._proc.poll() is None:
            self._log("■ Остановка…")
            pid = int(self._proc.pid or 0)
            try:
                if os.name == "nt":
                    # CREATE_NO_WINDOW: CTRL_BREAK часто игнорируется — сразу дерево процессов
                    with contextlib.suppress(Exception):
                        import signal
                        self._proc.send_signal(signal.CTRL_BREAK_EVENT)
                    time.sleep(0.35)
                    if self._proc.poll() is None and pid > 0:
                        _kill_pids([pid])
                    with contextlib.suppress(Exception):
                        self._proc.wait(timeout=5)
                else:
                    self._proc.terminate()
                    with contextlib.suppress(Exception):
                        self._proc.wait(timeout=5)
                    if self._proc.poll() is None:
                        self._proc.kill()
            except Exception:
                with contextlib.suppress(Exception):
                    if pid > 0 and os.name == "nt":
                        _kill_pids([pid])
                    else:
                        self._proc.kill()
            m.clear_automation_proc()
            return
        ext, st = m.shared_automation_running()
        if ext:
            pid = int(st.get("automation_pid") or 0)
            self._log(f"■ Остановка процесса Telegram (PID {pid})…")
            try:
                if os.name == "nt" and pid > 0:
                    _kill_pids([pid])
                elif pid > 0:
                    os.kill(pid, 15)
            except Exception:
                pass
            m.clear_automation_proc()
            self._external_auto = False
            self.run_start_btn.configure(state="normal")
            self.run_stop_btn.configure(state="disabled")
            self._set_run_status_ui("Остановлено", WARNING)
            self._set_run_form_enabled(True)
            self._run_log_active = False
            if hasattr(self, "run_progress"):
                try:
                    self.run_progress.stop()
                except Exception:
                    pass
                self.run_progress.pack_forget()

    def _check_vpn(self) -> None:
        if getattr(self, "_vpn_check_busy", False):
            return
        self._vpn_check_busy = True
        self._vpn_check_for_run = self._current_page == "run"
        self._log("Проверка VPN (без пинга Flipkart)…")
        if self._vpn_check_for_run:
            self._set_run_status_ui("Проверка VPN…", WARNING)
        import menu as m
        m._set_vpn_bg_status("warming", "Проверка VPN…")
        self._refresh_vpn_page()
        if hasattr(self, "vpn_check_btn"):
            self.vpn_check_btn.configure(state="disabled")

        def _worker():
            import menu as m
            ok = False
            msg = "✗ Проверка не завершилась"
            try:
                # Только наличие расширения / install — без открытия Flipkart
                ext = m._vpn_extension_dir()
                if not ext:
                    ok = False
                    msg = "✗ Папка VPN-расширения не найдена"
                else:
                    scan = m.scan_profiles_extension_status()
                    ok = int(scan.get("with_ext", 0)) > 0 or int(scan.get("missing", 1)) == 0
                    total = int(scan.get("total", 0))
                    with_ext = int(scan.get("with_ext", 0))
                    msg = (
                        f"✓ Расширение {with_ext}/{total} "
                        f"(VPN включится при сценарии)"
                    )
                    if not ok:
                        n = m.install_extensions_filesystem_all()
                        scan = m.scan_profiles_extension_status()
                        with_ext = int(scan.get("with_ext", 0))
                        total = int(scan.get("total", 0))
                        ok = with_ext > 0
                        msg = f"✓ Установлено: {n}; сейчас {with_ext}/{total}"
                self._log(msg)
                if ok:
                    m._set_vpn_bg_status("ready", "Расширение готово")
                else:
                    m._set_vpn_bg_status("error", "Расширение не установлено")
            except Exception as e:
                msg = f"✗ {e}"
                self._log(f"Ошибка: {e}")
                m._set_vpn_bg_status("error", str(e)[:80])
            finally:
                def _done():
                    self._vpn_check_busy = False
                    if hasattr(self, "vpn_check_btn"):
                        self.vpn_check_btn.configure(state="normal")
                    self._apply_vpn_check_result(ok, msg)
                    self._ensure_window_visible()
                self._run_on_main( _done)

        threading.Thread(target=_worker, daemon=True).start()

    def _install_extensions_bg(self) -> None:
        import menu as m
        self._log("Установка расширений в профили (фон)…")
        m._set_vpn_bg_status("warming", "Расширения…")

        def _w():
            n = m.install_extensions_filesystem_all()
            scan = m.scan_profiles_extension_status()
            if scan["missing"]:
                self._log(
                    f"⚠ Без расширения: {scan['missing']} проф. "
                    f"({', '.join(scan['missing_names'][:3])}…) — "
                    "установится при запуске автоматизации"
                )
            m._set_vpn_bg_status(
                "ready",
                f"Расширение {scan['with_ext']}/{scan['total']}"
                + (f" · установлено {n}" if n else " · на месте"),
            )
            self._log(f"✓ Готово ({n} профилей обновлено)")
            import menu as m
            m.sync_vpn_extension_status()
            self._run_on_main(self._refresh_vpn_page)
            self._run_on_main(lambda: self.after(2000, self._poll_vpn_bootstrap))

        threading.Thread(target=_w, daemon=True).start()

    # ── Profiles ──────────────────────────────────────────────────────────────

    def _profile_menu_info(self, prof: dict) -> str:
        import menu as m
        phone = m._disp_phone(prof.get("username", "?"))
        lines = [phone, ""]
        if prof.get("login_str"):
            lines.append(f"Создан: {prof['login_str']}")
        # Адрес / email — сразу под «Создан», из .profile_meta.json
        addr_sum = (prof.get("address_summary") or "").strip()
        if not addr_sum:
            name = (prof.get("address_name") or "").strip()
            pin = (prof.get("address_pincode") or "").strip()
            city = (prof.get("address_city") or "").strip()
            bits = [x for x in (name, f"{pin} {city}".strip()) if x]
            addr_sum = " | ".join(bits)
        addr_line = (prof.get("address_line") or "").strip()
        if not addr_line:
            house = (prof.get("address_house") or "").strip()
            road = (prof.get("address_road") or "").strip()
            addr_line = ", ".join(x for x in (house, road) if x)
        if addr_sum:
            lines.append(f"Адрес: {addr_sum}")
        if addr_line:
            lines.append(addr_line)
        st = (prof.get("address_state") or "").strip()
        if st and st not in addr_sum:
            lines.append(f"Штат: {st}")
        em = (prof.get("buyer_email") or "").strip()
        if em:
            lines.append(f"Email: {em}")
        if prof.get("issued_str"):
            lines.append(f"Выдан: {prof['issued_str']}")
        inv = prof.get("issued_invoice_id")
        if inv:
            lines.append(f"Заказ #{inv}" + (f" · {em}" if em else ""))
        vt = prof.get("black_valid_till") or prof.get("subscription_expires_str")
        if vt:
            lines.append(f"До: {vt}")
        link = (
            prof.get("issued_link") or prof.get("black_short_link")
            or prof.get("black_activation_link") or ""
        )
        if link:
            lines.append(link)
        note = prof.get("note") or ""
        if note:
            lines.append(note)
        return "\n".join(lines)

    def _open_profile_menu(self, prof: dict) -> None:
        """Совместимость: открытие действий в панели справа, без отдельного окна."""
        self._select_profile(prof)

    def _prof_run(self, phone: str, label: str, fn: Callable[[], None]) -> None:
        if phone in self._prof_busy:
            self._log(f"⚠ {phone}: уже выполняется")
            return
        self._prof_busy.add(phone)
        self._prof_labels[phone] = label
        self._log(label)
        with contextlib.suppress(Exception):
            import menu as m
            m.set_profile_op_stage(phone, label)
            m._purchase_cancel.clear()
        if self._selected_profile and str(self._selected_profile.get("username", "")) == phone:
            self._render_profile_detail(self._selected_profile)
        if self._selected_archive and str(self._selected_archive.get("username", "")) == phone:
            self._render_archive_detail(self._selected_archive)
        self._ensure_prof_stage_tick()

        def _w():
            try:
                fn()
            except Exception as e:
                self._log(f"Ошибка: {e}")
            finally:
                def _ui():
                    self._prof_busy.discard(phone)
                    self._prof_labels.pop(phone, None)
                    with contextlib.suppress(Exception):
                        import menu as m
                        m.set_profile_op_stage(phone, "")
                    if self._selected_profile and str(self._selected_profile.get("username", "")) == phone:
                        self._render_profile_detail(self._selected_profile)
                    if self._selected_archive and str(self._selected_archive.get("username", "")) == phone:
                        self._render_archive_detail(self._selected_archive)
                    if self._current_page in ("profiles", "youtube_hub", "run"):
                        self._schedule_refresh("profiles", lambda: self._refresh_profiles(force=True))
                    if self._current_page == "archive":
                        self._schedule_refresh("archive", self._refresh_archive)

                self._run_on_main(_ui)

        threading.Thread(target=_w, daemon=True, name=f"prof-{phone}").start()

    def _ensure_prof_stage_tick(self) -> None:
        """Обновляет текст этапа в блоке «В процессе», пока есть busy-профили."""
        if self._prof_stage_tick is not None:
            return

        def _tick() -> None:
            self._prof_stage_tick = None
            if not self._prof_busy:
                return
            sel = self._selected_profile
            if sel and str(sel.get("username", "")) in self._prof_busy:
                phone = str(sel.get("username", ""))
                with contextlib.suppress(Exception):
                    import menu as m
                    stage = m.get_profile_op_stage(phone) or self._prof_labels.get(phone, "")
                    if stage and hasattr(self, "profile_busy_stage"):
                        cur = self.profile_busy_stage.cget("text")
                        if stage != cur:
                            self.profile_busy_stage.configure(text=stage)
            self._prof_stage_tick = self.after(500, _tick)

        self._prof_stage_tick = self.after(500, _tick)

    def _prof_stop(self, phone: str, path) -> None:
        """Остановить сценарий выбранного профиля (флаг + kill Chrome)."""
        self._log(f"■ Стоп профиля +91 {phone}…")
        if hasattr(self, "profile_busy_stage"):
            with contextlib.suppress(Exception):
                self.profile_busy_stage.configure(text="Остановка…")

        def _w() -> None:
            try:
                import menu as m
                if path is not None:
                    killed = m.stop_profile_op(path)
                else:
                    m._purchase_cancel.set()
                    killed = m._stop_active_purchases()
                    # иначе следующий сценарий сразу CANCELLED
                    m._purchase_cancel.clear()
                    m.set_profile_op_stage(phone, "Остановка…")
                self._log(
                    f"■ Остановлено +91 {phone}"
                    + (f" (Chrome: {killed})" if killed else "")
                )
            except Exception as e:
                self._log(f"✗ Стоп: {e}")

        threading.Thread(target=_w, daemon=True, name=f"stop-{phone}").start()

    def _prof_chrome(self, phone: str, path) -> None:
        def _w():
            import menu as m
            ok = m.open_chrome(path)
            self._log(
                f"{'✓' if ok else '✗'} Chrome +91 {phone}"
                + (" — запускается (Flipkart; VPN только при автоматизации)" if ok else "")
            )
        self._prof_run(phone, f"Открытие Chrome +91 {phone}…", _w)

    def _prof_open_product(self, phone: str, path) -> None:
        """Открыть Chrome сразу на странице YouTube/Black 3 мес."""
        def _w():
            import menu as m
            url = m._BLACK_URLS.get(3) or m._BLACK_URLS[3]
            ok = m.open_chrome(path, url=url)
            self._log(
                f"{'✓' if ok else '✗'} Товар 3 мес · +91 {phone}"
                + (" — открываю страницу товара" if ok else "")
            )
        self._prof_run(phone, f"Товар 3 мес · +91 {phone}…", _w)

    def _prof_activate(self, phone: str, path) -> None:
        def _w():
            import menu as m
            result = asyncio.run(m._check_black_store_activation(path, username=phone, headless=True))
            st = result.get("status", "?") if isinstance(result, dict) else "?"
            vt = (result.get("valid_till") or "") if isinstance(result, dict) else ""
            link = (result.get("short_link") or result.get("activation_url") or "") if isinstance(result, dict) else ""
            self._log(f"{'✓' if st == 'activated' else '•'} {phone}: {st}" + (f" до {vt}" if vt else ""))
            if link:
                self._log(f"   🔗 {link}")
        self._prof_run(phone, f"Проверка активации {phone}…", _w)

    @staticmethod
    def _ru_result(msg) -> str:
        """Человекочитаемый результат сценария («CANCELLED» → «Отменено»)."""
        return {"CANCELLED": "Отменено"}.get(str(msg), str(msg))

    def _prof_fill_data(self, phone: str, path) -> None:
        def _w():
            import menu as m
            addr = m._gen_indian_address()
            ok, msg = asyncio.run(m._do_fill_address(path, addr, stop_at_payment=True))
            self._log(f"{'✓' if ok else '✗'} {phone}: {self._ru_result(msg)}")
        self._prof_run(phone, f"До оплаты · +91 {phone}…", _w)

    def _prof_set_issued(self, phone: str, path) -> None:
        def _w():
            import menu as m
            import time as _t
            if m._save_meta_field(path, issued_ts=_t.time()):
                self._log(f"✓ +91 {phone}: статус «выдан» установлен")
            else:
                self._log(f"✗ +91 {phone}: не удалось обновить статус")
        self._prof_run(phone, f"Статус «выдан» +91 {phone}…", _w)

    def _prof_archive(self, phone: str, path) -> None:
        if not messagebox.askyesno("Архив", f"Перенести профиль {phone} в архив?"):
            return

        def _w():
            import menu as m
            ok = m._archive_profile(path)
            self._log(f"{'✓' if ok else '✗'} Архив: {phone}")
        self._prof_run(phone, f"Архивация {phone}…", _w)

    def _prof_delete(self, phone: str, path) -> None:
        if not messagebox.askyesno("Удаление", f"Удалить профиль {phone} безвозвратно?"):
            return

        def _w():
            import shutil
            try:
                shutil.rmtree(path, ignore_errors=True)
                self._log(f"✓ Профиль {phone} удалён")
            except Exception as e:
                self._log(f"✗ Удаление: {e}")
        self._prof_run(phone, f"Удаление {phone}…", _w)

    def _profile_buy_for(self, prof: dict, months: int) -> None:
        self._selected_profile = prof
        self._profile_buy(months)

    def _profile_fill_address_for(self, prof: dict) -> None:
        self._selected_profile = prof
        self._profile_fill_address()

    def _profile_open_chrome(self) -> None:
        if not self._selected_profile:
            self._log("Выберите профиль")
            return
        phone = str(self._selected_profile.get("username", ""))
        self._prof_chrome(phone, self._selected_profile["path"])

    def _profile_buy(self, months: int) -> None:
        if not self._selected_profile:
            self._log("Выберите профиль")
            return
        path = self._selected_profile["path"]
        phone = str(self._selected_profile.get("username", ""))

        def _w():
            import menu as m
            ok, msg = asyncio.run(m._do_buy_membership(path, months, card=None))
            self._log(f"{'✓' if ok else '✗'} {self._ru_result(msg)}")
        self._prof_run(phone, f"Покупка {months} мес. · +91 {phone}…", _w)

    def _profile_fill_address(self) -> None:
        if not self._selected_profile:
            self._log("Выберите профиль")
            return
        self._show_address_dialog(self._selected_profile["path"])

    # ── Cards ─────────────────────────────────────────────────────────────────

    def _show_add_card_dialog(self, edit_idx: int | None = None) -> None:
        import menu as m
        cards = m._load_cards()
        existing = cards[edit_idx] if edit_idx is not None and 0 <= edit_idx < len(cards) else {}

        dlg = ctk.CTkToplevel(self)
        dlg.title("Изменить карту" if edit_idx is not None else "Добавить карту")
        dlg.geometry("480x520")
        dlg.transient(self)
        dlg.grab_set()

        fields: dict[str, ctk.CTkEntry] = {}
        labels = [
            ("nickname", "Название"), ("number", "Номер карты"), ("expiry", "MM/YY"),
            ("cvv", "CVV"), ("name", "Имя на карте"), ("country", "Страна"),
            ("zipcode", "Индекс"), ("state", "Штат"), ("city", "Город"), ("address", "Адрес"),
        ]
        scroll = AutoHideScrollFrame(dlg)
        scroll.pack(fill="both", expand=True, padx=16, pady=16)
        for key, label in labels:
            ctk.CTkLabel(scroll, text=label, text_color=TEXT_DIM).pack(anchor="w")
            e = ctk.CTkEntry(scroll)
            e.pack(fill="x", pady=(2, 8))
            if existing.get(key):
                e.insert(0, str(existing[key]))
            fields[key] = e
        if not fields["country"].get():
            fields["country"].insert(0, "USA")

        def _save():
            data = {k: v.get().strip() for k, v in fields.items()}
            if len(data.get("number", "").replace(" ", "")) < 13:
                messagebox.showerror("Ошибка", "Неверный номер карты")
                return
            data["name"] = data["name"].upper()
            cards_local = m._load_cards()
            if edit_idx is not None and 0 <= edit_idx < len(cards_local):
                cards_local[edit_idx] = data
                msg = f"Карта «{data['nickname']}» обновлена"
            else:
                cards_local.append(data)
                msg = f"Карта «{data['nickname']}» добавлена"
            m._save_cards(cards_local)
            self._log(msg)
            self._refresh_cards()
            dlg.destroy()

        ctk.CTkButton(dlg, text="Сохранить", fg_color=SUCCESS, command=_save).pack(pady=12)

    def _edit_selected_bank_card(self) -> None:
        if self._sel_bank_idx is None:
            messagebox.showinfo("Карты", "Выберите банковскую карту в списке")
            return
        self._show_add_card_dialog(self._sel_bank_idx)

    def _delete_card(self) -> None:
        import menu as m
        cards = m._load_cards()
        if not cards:
            return
        if self._sel_bank_idx is not None and 0 <= self._sel_bank_idx < len(cards):
            idx = self._sel_bank_idx
        else:
            dlg = ctk.CTkInputDialog(text=f"Номер карты [1-{len(cards)}]:", title="Удалить карту")
            val = dlg.get_input()
            try:
                idx = int(val) - 1
            except (TypeError, ValueError):
                return
        if 0 <= idx < len(cards):
            nick = cards[idx].get("nickname", "?")
            if not messagebox.askyesno("Удалить", f"Удалить карту «{nick}»?"):
                return
            cards.pop(idx)
            m._save_cards(cards)
            order = [i for i in m._load_card_order() if i != idx]
            order = [i - 1 if i > idx else i for i in order]
            m._save_card_order(order)
            self._sel_bank_idx = None
            self._log(f"Удалена: {nick}")
            self._refresh_cards()

    def _move_bank_card(self, delta: int) -> None:
        import menu as m
        cards = m._load_cards()
        if len(cards) < 2:
            return
        order = self._ordered_bank_indices(len(cards), m._load_card_order())
        if self._sel_bank_idx is None:
            messagebox.showinfo("Карты", "Выберите карту для перемещения")
            return
        try:
            pos = order.index(self._sel_bank_idx)
        except ValueError:
            return
        new_pos = pos + delta
        if not (0 <= new_pos < len(order)):
            return
        order[pos], order[new_pos] = order[new_pos], order[pos]
        m._save_card_order(order)
        self._refresh_cards()

    def _reset_bank_order(self) -> None:
        import menu as m
        if not m._load_cards():
            return
        if messagebox.askyesno("Порядок", "Сбросить порядок карт к списку по умолчанию?"):
            m._save_card_order([])
            self._refresh_cards()

    def _edit_selected_gift_card(self) -> None:
        import menu as m
        gc = m._load_gift_cards()
        if self._sel_gift_idx is None or not (0 <= self._sel_gift_idx < len(gc)):
            messagebox.showinfo("Гифт-карты", "Выберите гифт-карту в списке")
            return
        c = gc[self._sel_gift_idx]
        dlg = ctk.CTkToplevel(self)
        dlg.title("Изменить гифт-карту")
        dlg.geometry("420x280")
        dlg.transient(self)
        dlg.grab_set()
        fields: dict[str, ctk.CTkEntry] = {}
        for key, label in (("denom", "Номинал ₹"), ("number", "Серия"), ("pin", "PIN")):
            ctk.CTkLabel(dlg, text=label, text_color=TEXT_DIM).pack(anchor="w", padx=16, pady=(8, 0))
            e = ctk.CTkEntry(dlg, width=360)
            e.pack(padx=16)
            e.insert(0, str(c.get(key, "")))
            fields[key] = e

        def _save():
            try:
                denom = int(fields["denom"].get().strip())
            except ValueError:
                messagebox.showerror("Ошибка", "Номинал должен быть числом")
                return
            number = fields["number"].get().strip()
            pin = fields["pin"].get().strip()
            if len(number) < 14 or len(pin) < 4:
                messagebox.showerror("Ошибка", "Проверьте серию и PIN")
                return
            cards_local = m._load_gift_cards()
            if 0 <= self._sel_gift_idx < len(cards_local):
                cards_local[self._sel_gift_idx].update({
                    "denom": denom, "number": number, "pin": pin,
                })
                m._save_gift_cards(cards_local)
                self._log(f"Гифт-карта ₹{denom} обновлена")
                self._refresh_cards()
            dlg.destroy()

        ctk.CTkButton(dlg, text="Сохранить", fg_color=SUCCESS, command=_save).pack(pady=16)

    def _move_gift_card(self, delta: int) -> None:
        import menu as m
        gc = m._load_gift_cards()
        if len(gc) < 2 or self._sel_gift_idx is None:
            return
        pos = self._sel_gift_idx
        new_pos = pos + delta
        if not (0 <= new_pos < len(gc)):
            return
        gc[pos], gc[new_pos] = gc[new_pos], gc[pos]
        m._save_gift_cards(gc)
        self._sel_gift_idx = new_pos
        self._refresh_cards()

    def _gift_to_front(self) -> None:
        """Поставить выбранную гифт-карту первой в очереди."""
        import menu as m
        gc = m._load_gift_cards()
        if not gc or self._sel_gift_idx is None or not (0 <= self._sel_gift_idx < len(gc)):
            messagebox.showinfo("Гифт-карты", "Выберите карту в списке")
            return
        i = self._sel_gift_idx
        if i == 0:
            return
        card = gc.pop(i)
        gc.insert(0, card)
        m._save_gift_cards(gc)
        self._sel_gift_idx = 0
        self._refresh_cards()

    def _gift_to_end(self) -> None:
        """Поставить выбранную гифт-карту в конец очереди."""
        import menu as m
        gc = m._load_gift_cards()
        if not gc or self._sel_gift_idx is None or not (0 <= self._sel_gift_idx < len(gc)):
            messagebox.showinfo("Гифт-карты", "Выберите карту в списке")
            return
        i = self._sel_gift_idx
        if i >= len(gc) - 1:
            return
        card = gc.pop(i)
        gc.append(card)
        m._save_gift_cards(gc)
        self._sel_gift_idx = len(gc) - 1
        self._refresh_cards()

    def _toggle_gift_add_panel(self) -> None:
        if self._cards_tab != "gift":
            self._set_cards_tab("gift")
        self._gift_add_visible = not self._gift_add_visible
        self._refresh_cards()

    def _gift_add_show_result(self, res: dict) -> None:
        parts = []
        if res.get("added"):
            parts.append(f"✓ Добавлено: {res['added']}")
        if res.get("dup"):
            parts.append(f"дублей: {res['dup']}")
        if res.get("errs"):
            parts.append(f"ошибок: {len(res['errs'])}")
        parts.append(f"баланс ₹{res.get('balance', 0)}")
        msg = "  ·  ".join(parts)
        self.gift_add_result.configure(text=msg, text_color=SUCCESS if res.get("added") else TEXT_DIM)
        self._log(msg)
        for e in (res.get("errs") or [])[:5]:
            self._log(f"  ⚠ {e}")
        self._refresh_cards()

    def _add_gift_cards_manual(self) -> None:
        import menu as m
        text = self.gift_input.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Пусто", "Введите данные гифт-карт или загрузите файл.")
            return
        try:
            denom = int(self.gift_denom.get())
        except ValueError:
            denom = None
        res = m._add_gift_cards_from_text(text, denom)
        if not res["added"] and not res["errs"]:
            messagebox.showinfo("Результат", "Не найдено карт для добавления.")
        self._gift_add_show_result(res)
        if res["added"]:
            self.gift_input.delete("1.0", "end")

    def _upload_gift_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Выберите файл с гифт-картами",
            filetypes=[
                ("Все поддерживаемые", "*.csv;*.txt;*.xlsx;*.xls;*.html;*.htm"),
                ("Excel", "*.xlsx;*.xls"),
                ("CSV / текст", "*.csv;*.txt"),
                ("HTML", "*.html;*.htm"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path:
            return
        import menu as m
        try:
            raw = Path(path).read_bytes()
            text, err = m._gift_bytes_to_text(Path(path).name, raw)
            if err:
                messagebox.showerror("Ошибка файла", err)
                return
            try:
                denom = int(self.gift_denom.get())
            except ValueError:
                denom = None
            res = m._add_gift_cards_from_text(text, denom)
            self._gift_add_show_result(res)
            if not self._gift_add_visible:
                self._toggle_gift_add_panel()
            self.gift_input.delete("1.0", "end")
            self.gift_input.insert("1.0", f"# из файла: {Path(path).name}\n{text[:2000]}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def _delete_gift_card(self) -> None:
        import menu as m
        gc = m._load_gift_cards()
        if not gc:
            return
        if self._sel_gift_idx is not None and 0 <= self._sel_gift_idx < len(gc):
            removed = gc.pop(self._sel_gift_idx)
            m._save_gift_cards(gc)
            self._sel_gift_idx = None
            self._selected_gift = None
            self._log(f"Удалена гифт-карта …{str(removed.get('number', ''))[-4:]}")
            self._refresh_cards()
            return
        if self._selected_gift:
            num = str(self._selected_gift.get("number", ""))
            cards = [c for c in gc if str(c.get("number")) != num]
            m._save_gift_cards(cards)
            self._selected_gift = None
            self._sel_gift_idx = None
            self._log(f"Удалена гифт-карта …{num[-4:]}")
            self._refresh_cards()
            return
        dlg = ctk.CTkInputDialog(text=f"Номер [1-{len(gc)}] или 0=все:", title="Удалить гифт-карту")
        val = dlg.get_input()
        try:
            idx = int(val)
            if idx == 0:
                if messagebox.askyesno("Подтверждение", f"Удалить все {len(gc)} гифт-карт?"):
                    m._save_gift_cards([])
                    self._sel_gift_idx = None
                    self._log("Все гифт-карты удалены")
                    self._refresh_cards()
            elif 1 <= idx <= len(gc):
                removed = gc.pop(idx - 1)
                m._save_gift_cards(gc)
                self._log(f"Удалена гифт-карта …{str(removed.get('number', ''))[-4:]}")
                self._refresh_cards()
        except (TypeError, ValueError):
            pass

    def _toggle_pay_method(self) -> None:
        import menu as m
        cur = m._load_pay_method()
        new = "card" if cur == "gift" else "gift"
        m._save_pay_method(new)
        lbl = "гифт-карты" if new == "gift" else "банковская карта"
        self._log(f"Способ оплаты: {lbl}")
        self._refresh_cards()
        if hasattr(self, "_refresh_youtube_hub"):
            self._refresh_youtube_hub()
        self._toast("Оплата", lbl, ACCENT)

    def _show_address_dialog(self, profile_path: Path) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("Заполнить адрес")
        dlg.geometry("420x400")
        dlg.transient(self)
        dlg.grab_set()
        fields: dict[str, ctk.CTkEntry] = {}
        for key, label in [("name", "Имя"), ("phone", "Телефон"), ("pincode", "PIN"),
                           ("city", "Город"), ("state", "Штат"), ("line1", "Адрес")]:
            ctk.CTkLabel(dlg, text=label).pack(anchor="w", padx=16, pady=(8, 0))
            e = ctk.CTkEntry(dlg, width=360)
            e.pack(padx=16)
            fields[key] = e

        def _go():
            addr = {k: v.get().strip() for k, v in fields.items()}
            dlg.destroy()
            self._log("Заполнение адреса…")

            def _w():
                import menu as m
                try:
                    ok, msg = asyncio.run(m._do_fill_address(profile_path, addr))
                    self._log(f"{'✓' if ok else '✗'} {self._ru_result(msg)}")
                except Exception as e:
                    self._log(f"Ошибка: {e}")

            threading.Thread(target=_w, daemon=True).start()

        ctk.CTkButton(dlg, text="Заполнить", command=_go).pack(pady=16)

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _tool_check_activation(self) -> None:
        self._log("Проверка активации (может занять время)…")

        def _w():
            import menu as m
            profiles = [p for p in m._load_done_profiles() if p.get("issued_ts")]
            self._log(f"Выданных профилей: {len(profiles)}")
            for i, p in enumerate(profiles, 1):
                phone = p.get("username", "?")
                self._log(f"[{i}/{len(profiles)}] {m._disp_phone(phone)}")
                try:
                    chk = asyncio.run(m._check_black_store_activation(
                        p["path"], username=phone, headless=True))
                    st = chk.get("status", "?")
                    self._log(f"  → {st}")
                except Exception as e:
                    self._log(f"  ошибка: {e}")

        threading.Thread(target=_w, daemon=True).start()

    def _tool_restore_cookies(self) -> None:
        folder = _HERE / "cookies_backup"
        candidates: list[Path] = []
        if folder.exists():
            candidates = sorted(folder.glob("*.json"))
        if not candidates:
            self._log("Нет JSON в cookies_backup/")
            return
        names = "\n".join(f"{i+1}. {f.name}" for i, f in enumerate(candidates[:15]))
        dlg = ctk.CTkInputDialog(text=f"Файл:\n{names}", title="Восстановить из cookies")
        val = dlg.get_input()
        try:
            chosen = candidates[int(val) - 1]
        except (TypeError, ValueError, IndexError):
            return
        phone_dlg = ctk.CTkInputDialog(text="Номер (10 цифр, без +91):", title="Телефон")
        phone = "".join(filter(str.isdigit, phone_dlg.get_input() or ""))
        if len(phone) < 10:
            self._log("Неверный номер")
            return
        self._log(f"Восстановление из {chosen.name}…")

        def _w():
            import menu as m
            try:
                ok, msg = asyncio.run(m._restore_profile_from_cookies(chosen, phone))
                self._log(f"{'✓' if ok else '✗'} {msg}")
            except Exception as e:
                self._log(f"Ошибка: {e}")
            self._run_on_main( self._refresh_profiles)

        threading.Thread(target=_w, daemon=True).start()

    def _tool_check_updates(self) -> None:
        self._tool_check_updates_now()

    def _tool_check_updates_now(self) -> None:
        self._log("Проверка обновлений…")

        def _w():
            import menu as m
            try:
                m._check_updates_bg()
                n, commits, checked, checked_at = self._get_update_state()
                when = self._format_update_when(checked_at)
                if commits:
                    self._log(f"⚡ Доступно обновлений: {len(commits)} (обнаружено {when})")
                    for c in commits[:8]:
                        self._log(f"  • {c}")
                    if len(commits) > 8:
                        self._log(f"  … и ещё {len(commits) - 8}")
                else:
                    self._log(f"✓ Версия актуальна (проверено {when})")
                self._run_on_main( self._refresh_update_badge)
            except Exception as e:
                self._log(f"Ошибка: {e}")

        threading.Thread(target=_w, daemon=True).start()

    def _tool_purge(self) -> None:
        if not messagebox.askyesno(
            "Подтверждение",
            "Удалить ВСЕ папки из chrome_profiles_used и chrome_profiles_backup?\n"
            "(JSON-записи архива и бэкапы профилей)",
        ):
            return

        def _w():
            import shutil
            import menu as m
            removed = 0
            for d in [m.USED_PROFILES_DIR, m.BACKUP_PROFILES_DIR]:
                if d.exists():
                    for p in list(d.iterdir()):
                        if p.is_dir():
                            try:
                                shutil.rmtree(p)
                                removed += 1
                            except Exception:
                                pass
            self._log(f"Удалено: {removed}")

        threading.Thread(target=_w, daemon=True).start()

    # ── Update & restart ──────────────────────────────────────────────────────

    def _automation_is_busy(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        try:
            import menu as m
            ext, _ = m.shared_automation_running()
            return bool(ext)
        except Exception:
            return False

    def _stop_automation_then_restart(self) -> None:
        """Остановить run (локальный / Telegram) и сразу перезапустить GUI."""
        self._log("■ Остановка → перезапуск…")
        with contextlib.suppress(Exception):
            self._stop_run()
        deadline = time.time() + 8.0
        while time.time() < deadline and self._automation_is_busy():
            with contextlib.suppress(Exception):
                self.update_idletasks()
            time.sleep(0.12)
        self.after(200, self._restart_app)

    def _ask_stop_and_restart(self) -> bool:
        """Диалог «Занято»: Да = остановить и перезапустить. False = отмена."""
        if not self._automation_is_busy():
            return False
        return bool(messagebox.askyesno(
            "Занято",
            "Идёт автоматизация.\n\n"
            "Остановить и сразу перезапустить?",
            icon="warning",
            parent=self,
        ))

    def _update_and_restart(self) -> None:
        if self._automation_is_busy():
            if not messagebox.askyesno(
                "Занято",
                "Идёт автоматизация.\n\n"
                "Остановить и обновить с перезапуском?",
                icon="warning",
                parent=self,
            ):
                return
            self._log("■ Остановка перед обновлением…")
            with contextlib.suppress(Exception):
                self._stop_run()
            deadline = time.time() + 8.0
            while time.time() < deadline and self._automation_is_busy():
                with contextlib.suppress(Exception):
                    self.update_idletasks()
                time.sleep(0.12)
        try:
            import bot as bot_mod
            n = len(getattr(bot_mod, "_update_commits", []) or [])
        except Exception:
            n = 0
        if n == 0 and not self._needs_restart_for_update():
            return

        self._update_in_progress = True
        self._log("⬆ Проверка и скачивание обновлений…")
        if hasattr(self, "settings_upd_btn"):
            self._show_update_action_btn(
                "Обновление…", self._update_and_restart, BTN_SECONDARY, state="disabled",
            )

        def _w():
            import menu as m
            try:
                m._check_updates_bg()
                ok, msg = m._do_git_update()
                self._log(f"{'✓' if ok else '✗'} {msg}")
                if not ok:
                    self._run_on_main( lambda: messagebox.showerror("Ошибка обновления", msg))
                    self._run_on_main( self._enable_upd_btn)
                    return
                self._log("📦 Установка зависимостей…")
                m.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], hidden=True)
                m.run([sys.executable, "-m", "playwright", "install", "chromium"], hidden=True)
                self._log("♻ Перезапуск через 2 сек…")
                self._run_on_main(lambda: self.after(2000, self._restart_app))
            except Exception as e:
                self._log(f"Ошибка: {e}")
                self._run_on_main( lambda: messagebox.showerror("Ошибка", str(e)))
                self._run_on_main( self._enable_upd_btn)

        threading.Thread(target=_w, daemon=True).start()

    def _enable_upd_btn(self) -> None:
        self._update_in_progress = False
        self._refresh_update_badge()

    def _restart_for_update(self) -> None:
        if self._automation_is_busy():
            if self._ask_stop_and_restart():
                self._stop_automation_then_restart()
            return
        if messagebox.askyesno(
            "Применение обновления",
            "Файлы уже обновлены на диске.\nПерезапустить SubHub для применения?",
            parent=self,
        ):
            self._restart_app()

    def _restart_only(self) -> None:
        if self._automation_is_busy():
            if self._ask_stop_and_restart():
                self._stop_automation_then_restart()
            return
        if messagebox.askyesno("Перезапуск", "Перезапустить приложение?", parent=self):
            self._restart_app()

    def _desktop_shortcut_path(self) -> Path:
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_DESKTOP = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf) == 0:
                return Path(buf.value) / f"{APP_NAME}.lnk"
        except Exception:
            pass
        return Path.home() / "Desktop" / f"{APP_NAME}.lnk"

    def _start_menu_shortcut_path(self) -> Path:
        """Ярлык в меню Пуск — иначе Windows Search не даёт «Закрепить»."""
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return (
            Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / f"{APP_NAME}.lnk"
        )

    def _create_app_shortcut(self, lnk: Path, *, force: bool = False) -> bool:
        """Создаёт .lnk на SubHub.exe (рабочий стол / меню Пуск)."""
        if sys.platform != "win32":
            return False
        _regenerate_app_ico()
        launcher = _launcher_path().resolve()
        if not launcher.exists():
            return False
        lnk = Path(lnk)
        lnk.parent.mkdir(parents=True, exist_ok=True)
        if lnk.exists() and not force:
            return True
        work = str(_HERE.resolve())
        ico = _app_icon_path()
        icon = str((ico or launcher).resolve())

        def _q(s: str) -> str:
            return s.replace("'", "''")

        ps = (
            f"$w=New-Object -ComObject WScript.Shell;"
            f"$s=$w.CreateShortcut('{_q(str(lnk.resolve()))}');"
            f"$s.TargetPath='{_q(str(launcher))}';"
            f"$s.WorkingDirectory='{_q(work)}';"
            f"$s.Description='{APP_NAME} {APP_VERSION}';"
            f"$s.IconLocation='{_q(icon)},0';"
            f"$s.Save()"
        )
        import winproc
        r = winproc.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-Command", ps,
            ],
            capture_output=True, text=True, cwd=work, timeout=20,
        )
        return r.returncode == 0 and lnk.exists()

    def _register_windows_uninstall(self) -> bool:
        """Запись в HKCU Uninstall — иначе portable SubHub не виден в «Удаление программ»."""
        if sys.platform != "win32":
            return False
        try:
            import winreg
            uninstall_ps1 = (_HERE / "scripts" / "uninstall_subhub.ps1").resolve()
            launcher = _launcher_path().resolve()
            root = str(_HERE.resolve())
            cmd = (
                f'powershell.exe -NoProfile -ExecutionPolicy Bypass '
                f'-WindowStyle Hidden -File "{uninstall_ps1}"'
            )
            icon = f"{launcher},0" if launcher.exists() else f"{_HERE / 'assets' / 'app.ico'},0"
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CrownfallSubHub"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
                winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
                winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_VENDOR)
                winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, root)
                winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, icon)
                winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, cmd)
                winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, cmd)
                winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
            return True
        except Exception as e:
            self._log(f"⚠ Регистрация uninstall: {e}")
            return False

    def _create_desktop_shortcut(self) -> bool:
        """Ярлык на рабочий стол + в меню Пуск (для закрепления в Windows)."""
        ok_desk = self._create_app_shortcut(self._desktop_shortcut_path())
        ok_start = self._create_app_shortcut(self._start_menu_shortcut_path(), force=True)
        self._register_windows_uninstall()
        return ok_desk and ok_start

    def _ensure_desktop_shortcut(self) -> None:
        """Ярлыки на столе и в Пуске, если их ещё нет."""
        need_desk = not self._desktop_shortcut_path().exists()
        need_start = not self._start_menu_shortcut_path().exists()
        # Даже если ярлыки есть — убедиться что есть запись uninstall
        need_reg = True
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CrownfallSubHub",
                ):
                    need_reg = False
            except OSError:
                need_reg = True
        if not need_desk and not need_start and not need_reg:
            return

        def _w() -> None:
            ok = False
            with contextlib.suppress(Exception):
                if need_desk:
                    ok = self._create_app_shortcut(self._desktop_shortcut_path()) or ok
                if need_start:
                    ok = self._create_app_shortcut(
                        self._start_menu_shortcut_path(), force=True,
                    ) or ok
                if self._register_windows_uninstall():
                    ok = True
            if ok:
                self._run_on_main(
                    self._log, f"✓ Ярлыки / uninstall «{APP_NAME}» в Windows",
                )
        threading.Thread(target=_w, daemon=True, name="desktop-shortcut").start()

    def _create_desktop_shortcut_ui(self) -> None:
        desk = self._create_app_shortcut(self._desktop_shortcut_path(), force=True)
        start = self._create_app_shortcut(self._start_menu_shortcut_path(), force=True)
        reg = self._register_windows_uninstall()
        if desk or start or reg:
            self._log(f"✓ Ярлык: {self._desktop_shortcut_path()}")
            self._log(f"✓ Меню Пуск: {self._start_menu_shortcut_path()}")
            if reg:
                self._log("✓ Запись в «Приложения» Windows (можно удалить оттуда)")
            self._toast(
                "Ярлыки готовы",
                "Параметры → Приложения → SubHub · или Пуск → закрепить",
                SUCCESS,
            )
            messagebox.showinfo(
                "Готово",
                "Ярлыки созданы на рабочем столе и в меню «Пуск».\n"
                "SubHub зарегистрирован в списке программ Windows.\n\n"
                "Удалить: Параметры → Приложения → SubHub → Удалить\n"
                "или кнопка «Удалить из Windows» в настройках.\n\n"
                "Закрепить: Пуск → Все приложения → SubHub → ПКМ.",
            )
        else:
            messagebox.showerror("Ошибка", "Не удалось создать ярлык.")

    def _uninstall_from_windows_ui(self) -> None:
        if not messagebox.askyesno(
            "Удалить SubHub из Windows?",
            "Удалить ярлыки и запись из «Приложения / Удаление программ»?\n\n"
            "Папка проекта на диске останется — её можно удалить вручную.",
            parent=self,
        ):
            return
        ps1 = _HERE / "scripts" / "uninstall_subhub.ps1"
        if not ps1.exists():
            # Fallback: только снять registry + ярлыки из Python
            self._unregister_windows_uninstall()
            with contextlib.suppress(Exception):
                self._desktop_shortcut_path().unlink(missing_ok=True)
                self._start_menu_shortcut_path().unlink(missing_ok=True)
            self._toast("Удалено", "Запись Windows и ярлыки сняты", SUCCESS)
            return

        def _w() -> None:
            import winproc
            r = winproc.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                    "-File", str(ps1), "-Quiet",
                ],
                capture_output=True, text=True, cwd=str(_HERE), timeout=60,
            )
            self._run_on_main(
                lambda: self._after_windows_uninstall(r.returncode == 0, (r.stdout or "")[-300:]),
            )

        threading.Thread(target=_w, daemon=True, name="win-uninstall").start()

    def _after_windows_uninstall(self, ok: bool, out: str) -> None:
        if ok:
            self._log("✓ SubHub удалён из списка программ Windows")
            self._toast("Удалено", "Ярлыки и запись uninstall сняты", SUCCESS)
            messagebox.showinfo(
                "Готово",
                "SubHub убран из Пуска, рабочего стола и списка программ.\n"
                f"Папка проекта сохранена:\n{_HERE}",
                parent=self,
            )
        else:
            self._log(f"⚠ Uninstall: {out or 'ошибка'}")
            self._toast("Ошибка удаления", (out or "см. лог")[:120], ERROR)

    def _unregister_windows_uninstall(self) -> None:
        if sys.platform != "win32":
            return
        with contextlib.suppress(Exception):
            import winreg
            winreg.DeleteKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CrownfallSubHub",
            )

    def _setup_exe_candidates(self) -> list[Path]:
        dist = _HERE / "dist"
        return [
            _HERE / "setup.exe",
            dist / "SubHub-Setup.exe",
            dist / f"SubHub-Setup-{APP_VERSION}.exe",
            _HERE / f"SubHub-Setup-{APP_VERSION}.exe",
            _HERE / "SubHub-Setup.exe",
        ]

    def _find_setup_exe(self) -> Path | None:
        for p in self._setup_exe_candidates():
            if p.is_file() and p.stat().st_size > 100_000:
                return p
        return None

    def _downloads_dir(self) -> Path:
        home = Path.home()
        for cand in (
            home / "Downloads",
            Path(os.environ.get("USERPROFILE", "")) / "Downloads",
            home / "Desktop",
        ):
            if cand.is_dir():
                return cand
        return home

    def _download_setup_exe_ui(self) -> None:
        """Собрать Setup.exe (если нет) и положить в Загрузки — как обычный установщик."""
        existing = self._find_setup_exe()
        if existing:
            self._deliver_setup_to_downloads(existing)
            return
        self._toast("Setup", "Собираю SubHub-Setup.exe…", ACCENT)
        self._log("📦 Сборка Setup.exe…")

        def _w() -> None:
            import winproc
            bat = _HERE / "scripts" / "build_installer.bat"
            r = winproc.run(
                ["cmd", "/c", str(bat)],
                capture_output=True, text=True, cwd=str(_HERE), timeout=600,
            )
            setup = self._find_setup_exe()
            out = ((r.stdout or "") + (r.stderr or ""))[-500:]
            self._run_on_main(
                lambda: self._after_setup_build(r.returncode == 0 and setup is not None, setup, out),
            )

        threading.Thread(target=_w, daemon=True, name="build-setup").start()

    def _after_setup_build(self, ok: bool, setup: Path | None, out: str) -> None:
        if ok and setup:
            self._log(f"✓ Setup собран: {setup}")
            self._deliver_setup_to_downloads(setup)
        else:
            self._log(f"⚠ Сборка Setup: {out or 'ошибка'}")
            self._toast("Setup", "Не удалось собрать — см. лог", ERROR)
            messagebox.showerror(
                "Setup",
                "Не удалось собрать SubHub-Setup.exe.\n"
                "Запустите вручную: scripts\\build_installer.bat",
                parent=self,
            )

    def _deliver_setup_to_downloads(self, setup: Path) -> None:
        dest = self._downloads_dir() / f"SubHub-Setup-{APP_VERSION}.exe"
        try:
            import shutil
            shutil.copy2(setup, dest)
        except Exception as e:
            dest = setup
            self._log(f"⚠ Копирование в Загрузки: {e}")
        self._toast("Setup.exe", f"Сохранено: {dest.name}", SUCCESS)
        with contextlib.suppress(Exception):
            os.startfile(str(dest.parent))
        if messagebox.askyesno(
            "SubHub Setup",
            f"Установщик готов:\n{dest}\n\nЗапустить установку сейчас?\n"
            "(ярлык на рабочем столе, процессы только пока приложение открыто)",
            parent=self,
        ):
            self._launch_setup_exe(dest)

    def _run_setup_exe_ui(self) -> None:
        setup = self._find_setup_exe()
        if not setup:
            if messagebox.askyesno(
                "Setup",
                "SubHub-Setup.exe ещё нет.\nСобрать сейчас?",
                parent=self,
            ):
                self._download_setup_exe_ui()
            return
        self._launch_setup_exe(setup)

    def _launch_setup_exe(self, setup: Path) -> None:
        try:
            # GUI setup — нужен видимый процесс (не CREATE_NO_WINDOW)
            subprocess.Popen([str(setup)], cwd=str(setup.parent))
            self._log(f"▶ Установщик: {setup}")
            self._toast("Установка", "Запущен Setup.exe", ACCENT)
        except Exception as e:
            messagebox.showerror("Setup", str(e), parent=self)

    def _run_portable_install(self) -> None:
        """Ярлыки Пуск/Desktop через scripts/install_subhub.ps1 (без админа)."""
        ps1 = _HERE / "scripts" / "install_subhub.ps1"

        def _w() -> None:
            import winproc
            if ps1.exists():
                r = winproc.run(
                    [
                        "powershell", "-NoProfile", "-NonInteractive",
                        "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                        "-File", str(ps1),
                    ],
                    capture_output=True, text=True, cwd=str(_HERE), timeout=90,
                )
                ok = r.returncode == 0
                out = ((r.stdout or "") + (r.stderr or "")).strip()[-400:]
            else:
                ok = self._create_desktop_shortcut()
                out = ""
            self._run_on_main(lambda: self._after_portable_install(ok, out))

        threading.Thread(target=_w, daemon=True, name="portable-install").start()
        self._toast("Установка", "Создаю ярлыки…", ACCENT)

    def _after_portable_install(self, ok: bool, out: str) -> None:
        if ok:
            self._log("✓ Ярлыки установлены (меню Пуск / рабочий стол)")
            if out:
                self._log(out.splitlines()[-1] if out else "")
            self._toast(
                "Готово",
                "Пуск + Параметры → Приложения → SubHub (можно удалить)",
                SUCCESS,
            )
        else:
            self._log(f"⚠ Установка ярлыков: {out or 'ошибка'}")
            self._toast("Ошибка", "Не удалось установить ярлыки", ERROR)

    def _backup_data_ui(self) -> None:
        def _w() -> None:
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "backup_data", _HERE / "scripts" / "backup_data.py",
                )
                mod = importlib.util.module_from_spec(spec)
                assert spec and spec.loader
                spec.loader.exec_module(mod)
                path = mod.build_backup()
                self._run_on_main(lambda: self._after_backup(True, str(path)))
            except Exception as e:
                self._run_on_main(lambda: self._after_backup(False, str(e)))

        threading.Thread(target=_w, daemon=True, name="backup-zip").start()
        self._toast("Бэкап", "Собираю zip…", ACCENT)

    def _after_backup(self, ok: bool, detail: str) -> None:
        if ok:
            self._log(f"✓ Бэкап: {detail}")
            self._toast("Бэкап готов", Path(detail).name, SUCCESS)
            with contextlib.suppress(Exception):
                os.startfile(str(Path(detail).parent))
        else:
            self._log(f"⚠ Бэкап: {detail}")
            self._toast("Бэкап не удался", detail[:120], ERROR)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _install_deps(self) -> None:
        self._log("Установка в фоне…")

        def _w():
            import menu as m
            ok, msg = m.ensure_dependencies(
                log_fn=lambda s: self._run_on_main(self._log, str(s)),
            )
            self._log(f"{'✓' if ok else '⚠'} {msg}")
            if ok:
                self._run_on_main(self._refresh_vpn_page)

        threading.Thread(target=_w, daemon=True).start()

    def _open_secrets(self) -> None:
        p = _HERE / "secrets.yaml"
        if not p.exists():
            p = _HERE / "secrets.yaml.example"
        if p.exists():
            os.startfile(str(p))

    def _open_config(self) -> None:
        p = _HERE / "config.yaml"
        if p.exists():
            os.startfile(str(p))

    def _open_vpn_folder(self) -> None:
        d = _HERE / "veepn_extension"
        d.mkdir(exist_ok=True)
        os.startfile(str(d))

    def _open_folder(self, name: str) -> None:
        d = _HERE / name
        d.mkdir(exist_ok=True)
        os.startfile(str(d))

    def _clear_logs(self) -> None:
        self.log_text.delete("1.0", "end")
        if hasattr(self, "sidebar_log"):
            self.sidebar_log.configure(state="normal")
            self.sidebar_log.delete("1.0", "end")
            self.sidebar_log.configure(state="disabled")

    def _open_log_file(self) -> None:
        p = _HERE / "automation.log"
        if p.exists():
            os.startfile(str(p))

    def _hide_to_background(self) -> bool:
        """Скрыть окно, оставив сервисы в фоне. Возвращает False, если скрыть нельзя."""
        if not self._app_settings.get("background_mode", True):
            return False
        if not self._ensure_tray():
            messagebox.showwarning(
                "SubHub",
                "Не удалось создать иконку в трее.\n"
                "Установите: pip install pystray Pillow\n\n"
                "Без трея окно нельзя безопасно скрыть.",
            )
            return False
        if self._hidden_to_tray:
            return True
        self._hidden_to_tray = True
        self.withdraw()
        self._log("Окно скрыто — приложение работает в фоне (иконка в трее)")
        return True

    def _ask_close_action(self) -> str | None:
        """Спросить при закрытии окна: quit | tray | None (нет)."""
        result: dict[str, str | None] = {"v": None}
        dlg = ctk.CTkToplevel(self)
        dlg.title("SubHub")
        dlg.geometry("440x240")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(fg_color=BG_MAIN)
        try:
            dlg.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() - 440) // 2
            y = self.winfo_y() + (self.winfo_height() - 240) // 2
            dlg.geometry(f"440x240+{x}+{y}")
        except Exception:
            pass

        ctk.CTkLabel(
            dlg, text="Закрыть SubHub?",
            font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY,
        ).pack(pady=(22, 8), padx=24, anchor="w")
        ctk.CTkLabel(
            dlg,
            text=(
                "Telegram-бот и автоматизация могут продолжать работу в фоне.\n"
                "Выберите действие:"
            ),
            justify="left", anchor="w", wraplength=392,
            text_color=TEXT_DIM, font=_ui_font(FONT_SMALL),
        ).pack(fill="x", padx=24, pady=(0, 16))

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=(0, 22))

        def _pick(v: str | None) -> None:
            result["v"] = v
            with contextlib.suppress(Exception):
                dlg.grab_release()
            dlg.destroy()

        ctk.CTkButton(
            btns, text="Да, закрыть полностью", height=BTN_H,
            corner_radius=RADIUS_BTN, font=_ui_font(FONT_BODY, "bold"),
            fg_color=ERROR, hover_color="#da3633",
            command=lambda: _pick("quit"),
        ).pack(fill="x", pady=(0, 6))
        ctk.CTkButton(
            btns, text="В трей", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=lambda: _pick("tray"),
        ).pack(fill="x", pady=(0, 6))
        ctk.CTkButton(
            btns, text="Нет", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BG_SURFACE, hover_color=BG_NAV_ACTIVE,
            command=lambda: _pick(None),
        ).pack(fill="x")

        dlg.protocol("WM_DELETE_WINDOW", lambda: _pick(None))
        dlg.wait_window()
        return result["v"]

    def _on_close(self) -> None:
        if self._quitting:
            return
        if not self._app_settings.get("background_mode", True):
            if messagebox.askyesno("Выход", "Закрыть SubHub?"):
                self._quit_app()
            return

        action = self._ask_close_action()
        if action == "quit":
            self._quit_app()
        elif action == "tray":
            if not self._hide_to_background():
                if messagebox.askyesno("Выход", "Скрыть не удалось. Закрыть SubHub полностью?"):
                    self._quit_app()

    def _shutdown_services(self, *, fast: bool = False) -> None:
        # Всегда останавливаем автоматизацию: и из UI (self._proc), и из Telegram
        self._stop_run()
        try:
            import menu as m
            m.disconnect_vpn_on_shutdown()
        except Exception:
            pass
        try:
            from ggsell.monitor import stop_monitor
            stop_monitor()
        except Exception:
            pass
        if fast:
            return
        try:
            import grizzly as gz
            gz.cleanup_all_rentals_on_exit()
        except Exception:
            pass
        try:
            import menu as m
            m._patch_runtime_state(tg_bot_pid=0, tg_bot_owner="")
        except Exception:
            pass
        try:
            import bot as bot_mod
            bot_mod.stop_tg_bot()
        except Exception:
            pass

    def _quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True

        with contextlib.suppress(Exception):
            self.withdraw()
            self.update_idletasks()

        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.stop()
            self._tray_icon = None

        def _shutdown_bg() -> None:
            try:
                self._shutdown_services()
            except Exception:
                pass
            self._run_on_main(_exit_process)

        def _exit_process() -> None:
            with contextlib.suppress(Exception):
                self.destroy()
            _release_gui_mutex()
            os._exit(0)

        threading.Thread(target=_shutdown_bg, daemon=True, name="subhub-exit").start()

    def _restart_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._restart_requested = True

        with contextlib.suppress(Exception):
            self._shutdown_services(fast=True)
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.stop()
            self._tray_icon = None

        # Полный spawn нового процесса — иначе класс SubHubApp остаётся из
        # уже импортированного модуля и правки app.py не подхватываются.
        if "--console" in sys.argv:
            with contextlib.suppress(Exception):
                self.destroy()
            _release_gui_mutex()
            os._exit(42)

        with contextlib.suppress(Exception):
            self.destroy()
        _release_gui_mutex()
        with contextlib.suppress(Exception):
            launcher = _launcher_path().resolve()
            cwd = str(_HERE.resolve())
            if launcher.suffix.lower() == ".exe":
                subprocess.Popen([str(launcher)], cwd=cwd, close_fds=True)
            elif launcher.suffix.lower() == ".vbs":
                subprocess.Popen(
                    ["wscript.exe", "//nologo", str(launcher)],
                    cwd=cwd, close_fds=True,
                )
            else:
                argv = [sys.executable, *sys.argv] if sys.argv else [
                    sys.executable, str(_HERE / "scripts" / "_gui_boot.py"),
                ]
                subprocess.Popen(argv, cwd=cwd, close_fds=True)
        os._exit(0)

    def _tray_image(self):
        from PIL import Image, ImageDraw
        for src in (_app_icon_png_path(), _app_icon_path()):
            if src:
                try:
                    img = Image.open(src)
                    return img.resize((64, 64), Image.Resampling.LANCZOS)
                except Exception:
                    pass
        img = Image.new("RGB", (64, 64), color=(13, 17, 23))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([8, 8, 56, 56], radius=12, fill=(40, 116, 240))
        draw.rounded_rectangle([22, 18, 42, 46], radius=4, fill=(255, 107, 107))
        draw.rectangle([28, 28, 36, 36], fill=(13, 17, 23))
        return img

    def _ensure_tray(self) -> bool:
        if self._tray_ready and self._tray_icon is not None:
            return True
        try:
            import pystray
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        if self._tray_icon is not None:
            return True
        try:
            tray_img = self._tray_image()
        except Exception:
            return False

        def _show(_icon=None, _item=None) -> None:
            self._run_on_main(self._show_from_tray)

        def _restart(_icon=None, _item=None) -> None:
            self._run_on_main(self._restart_app)

        def _quit(_icon=None, _item=None) -> None:
            self._run_on_main(self._quit_app)

        menu = pystray.Menu(
            pystray.MenuItem("Открыть", _show, default=True),
            pystray.MenuItem("Перезапустить", _restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", _quit),
        )
        self._tray_icon = pystray.Icon(
            "subhub", tray_img, APP_NAME, menu,
        )

        def _run() -> None:
            try:
                self._tray_icon.run()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name="systray").start()
        self._tray_ready = True
        return True

    def _show_from_tray(self) -> None:
        self._hidden_to_tray = False
        self._ensure_window_visible(force=True)
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _minimize_to_tray(self) -> bool:
        return self._hide_to_background()


def main() -> None:
    with contextlib.suppress(Exception):
        import winproc as _wp
        os.environ.setdefault("SUBHUB_LAUNCHED_BY", "app")
        _wp.patch_subprocess_hidden()
    _hide_console_window()
    _set_windows_app_id()
    if not _try_acquire_gui_mutex():
        _notify_or_activate_existing()
        return
    try:
        # Окно сначала — orphan cleanup в фоне (не блокирует старт на 3–4 с)
        while True:
            app = SubHubApp()
            if not (_HERE / "secrets.yaml").exists():
                app._log("⚠ Заполните secrets.yaml (Настройки)")
            app.mainloop()
            if not getattr(app, "_restart_requested", False):
                break
            time.sleep(1.5)
    except Exception:
        import traceback
        crash = _HERE / "data" / "app_crash.log"
        with contextlib.suppress(Exception):
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        _release_gui_mutex()
    os._exit(0)


if __name__ == "__main__":
    main()
