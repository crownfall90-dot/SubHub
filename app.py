"""
SubHub — десктопное приложение (GUI).
YouTube · GGSELL · DeepSeek · Kling AI
"""

from __future__ import annotations

import asyncio
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
from tkinter import filedialog, messagebox

_HERE = Path(__file__).parent
os.chdir(_HERE)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Design system ─────────────────────────────────────────────────────────────
BG_MAIN = "#0d1117"
BG_SIDEBAR = "#161b22"
BG_CARD = "#1c2333"
BG_CARD_HOVER = "#242d3f"
BG_ELEVATED = "#21283b"
TEXT_DIM = "#8b949e"
TEXT_MUTED = "#6e7681"
ACCENT = "#2874F0"
SUCCESS = "#3fb950"
WARNING = "#d29922"
ERROR = "#f85149"
BTN_SECONDARY = "#2d333b"
BTN_SUCCESS = "#238636"
RADIUS_CARD = 16
RADIUS_BTN = 10

SVC_YOUTUBE = "#ff0033"
SVC_GGSELL = "#ff8c00"
SVC_DEEPSEEK = "#5b7fff"
SVC_KLING = "#00c9a7"

SERVICE_META: dict[str, dict[str, Any]] = {
    "youtube": {
        "title": "YouTube Premium",
        "subtitle": "Flipkart · вход · покупка · VPN",
        "accent": SVC_YOUTUBE,
        "icon": "▶",
        "ready": True,
    },
    "ggsell": {
        "title": "GGSELL",
        "subtitle": "Заказы · мониторинг · доставка",
        "accent": SVC_GGSELL,
        "icon": "🛒",
        "ready": True,
    },
    "deepseek": {
        "title": "DeepSeek",
        "subtitle": "Оплата подписки — скоро",
        "accent": SVC_DEEPSEEK,
        "icon": "🧠",
        "ready": False,
    },
    "kling": {
        "title": "Kling AI",
        "subtitle": "Оплата подписки — скоро",
        "accent": SVC_KLING,
        "icon": "🎬",
        "ready": False,
    },
}

_APP_SETTINGS_PATH = _HERE / "data" / "app_settings.json"
_APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "background_mode": True,
    "minimize_to_tray": True,
    "run_at_startup": False,
    "start_minimized": False,
}
_WIN_STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_STARTUP_NAME = "SubHub"
APP_NAME = "SubHub"
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
                if launcher.suffix.lower() == ".vbs":
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
        self.title(APP_NAME)
        self.geometry("1280x820")
        self.minsize(1024, 680)
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

        self.withdraw()
        _cleanup_legacy_branding()
        self._build_layout()
        self._apply_window_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_page("home")
        self.status_lbl.configure(text="⏳ Инициализация…")
        self._log("⏳ Запуск SubHub…")
        self.after(1500, self._maybe_show_loading)
        threading.Thread(target=self._startup_preflight, daemon=True, name="preflight").start()

    def _maybe_show_loading(self) -> None:
        if self._startup_done:
            return
        if not self.winfo_viewable():
            self.deiconify()
            self._log("⏳ Инициализация, подождите…")

    def _startup_preflight(self) -> None:
        """Перед открытием: зависимости и обновления (каждый запуск)."""
        import menu as m

        def log(msg: str) -> None:
            self.after(0, lambda m=msg: self._log(m))

        try:
            m._init_secrets()
            m._migrate_config()
            m._startup_cleanup()
        except Exception as e:
            self.after(0, lambda: self._log(f"⚠ Инициализация: {e}"))

        self.after(0, lambda: self._log("📦 Проверка зависимостей…"))
        try:
            ok, dep_msg = m.ensure_dependencies(log_fn=log)
            self.after(0, lambda: self._log(f"{'✓' if ok else '⚠'} {dep_msg}"))
        except Exception as e:
            self.after(0, lambda: self._log(f"⚠ Зависимости: {e}"))

        self.after(0, lambda: self._log("🔄 Проверка обновлений…"))
        try:
            m._check_updates_bg()
            n, commits, _, _ = self._get_update_state()
            if n:
                self.after(0, lambda: self._log(f"⚡ Доступно обновлений: {n}"))
                for c in commits[:3]:
                    self.after(0, lambda line=c: self._log(f"   • {line}"))
            else:
                self.after(0, lambda: self._log("✓ Версия актуальна"))
        except Exception as e:
            self.after(0, lambda: self._log(f"⚠ Обновления: {e}"))

        self.after(0, self._finish_startup)

    def _finish_startup(self) -> None:
        self._startup_done = True
        self._bootstrap_backend()
        self._refresh_update_badge()
        self._tick_logs()
        self._tick_status()
        self.after(200, self._startup_tray)
        start_hidden = (
            self._app_settings.get("start_minimized")
            and self._app_settings.get("background_mode")
        )
        if not start_hidden:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.update_idletasks()
            self._apply_window_icon()
            self.after(250, self._apply_window_icon)
        self._log("✓ SubHub готов")

    def _startup_tray(self) -> None:
        """Иконка SubHub в системном трее — сразу при запуске."""
        if not self._ensure_tray():
            self._log("⚠ Трей: pip install pystray Pillow")
            return
        if (self._app_settings.get("start_minimized")
                and self._app_settings.get("background_mode")):
            self._hidden_to_tray = True
            self.withdraw()
            self._log("Запущено в трее — двойной клик откроет окно")

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

        self.sidebar = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=BG_SIDEBAR)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(20, 4))
        ctk.CTkLabel(
            brand, text="SubHub", font=ctk.CTkFont(size=26, weight="bold"),
            text_color="#f0f6fc",
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand, text="Подписки · маркетплейс", font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        ).pack(anchor="w", pady=(2, 0))

        self._vpn_status_lbl = ctk.CTkLabel(
            self.sidebar, text="🔒 VPN…", font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM, wraplength=200,
            fg_color=BG_CARD, corner_radius=8, padx=10, pady=6,
        )
        self._vpn_status_lbl.pack(fill="x", padx=14, pady=(12, 8))

        self.nav_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_container.pack(fill="both", expand=True, padx=6, pady=4)

        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", pady=(0, 12), padx=12)

        foot_nav = ctk.CTkFrame(bottom, fg_color="transparent")
        foot_nav.pack(fill="x", pady=(0, 8))
        foot_nav.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            foot_nav, text="📋 Логи", height=32, corner_radius=8,
            font=ctk.CTkFont(size=12), fg_color=BTN_SECONDARY,
            command=lambda: self.show_page("logs"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            foot_nav, text="⚙️ Настройки", height=32, corner_radius=8,
            font=ctk.CTkFont(size=12), fg_color=BTN_SECONDARY,
            command=lambda: self.show_page("settings"),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.status_lbl = ctk.CTkLabel(
            bottom, text="", font=ctk.CTkFont(size=10), text_color=TEXT_MUTED, wraplength=200,
        )
        self.status_lbl.pack(anchor="w")
        self._bg_status_lbl = None

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=20, pady=18)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._build_home()
        self._build_youtube_hub()
        self._build_ggsell()
        self._build_deepseek()
        self._build_kling()
        self._build_run()
        self._build_profiles()
        self._build_archive()
        self._build_cards()
        self._build_vpn()
        self._build_tools()
        self._build_logs()
        self._build_settings()
        self._render_sidebar(None)

    def _render_sidebar(self, service: str | None) -> None:
        for w in self.nav_container.winfo_children():
            w.destroy()
        self._nav_btns.clear()
        accent = SERVICE_META.get(service or "", {}).get("accent", ACCENT)

        if service is None:
            items = [("home", "🏠  Главная")]
        elif service == "youtube":
            items = [
                ("__home__", "←  Главная"),
                ("youtube_hub", "📊  Обзор"),
                ("run", "🚀  Запуск"),
                ("profiles", "👤  Профили"),
                ("archive", "📦  Архив"),
                ("cards", "💳  Карты"),
                ("vpn", "🔒  VPN"),
            ]
        elif service == "ggsell":
            items = [
                ("__home__", "←  Главная"),
                ("ggsell", "🛒  GGSELL"),
            ]
        else:
            meta = SERVICE_META.get(service, {})
            items = [
                ("__home__", "←  Главная"),
                (service, f"{meta.get('icon', '●')}  {meta.get('title', service)}"),
            ]

        for key, label in items:
            is_active = key == self._current_page or (
                key == "youtube_hub"
                and self._current_page in ("run", "profiles", "archive", "cards", "vpn", "tools")
            )
            btn = ctk.CTkButton(
                self.nav_container, text=label, anchor="w", height=36,
                corner_radius=8,
                fg_color=accent if is_active else "transparent",
                hover_color=BG_CARD_HOVER,
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self._nav_click(k),
            )
            btn.pack(fill="x", padx=6, pady=2)
            self._nav_btns[key] = btn

    def _nav_click(self, key: str) -> None:
        if key == "__home__":
            self._go_home()
            return
        if key in ("run", "profiles", "archive", "cards", "vpn", "tools"):
            self._current_service = "youtube"
        self.show_page(key)

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
        hdr.pack(fill="x", pady=(0, 16))
        stripe = ctk.CTkFrame(hdr, width=4, height=48, fg_color=accent, corner_radius=2)
        stripe.pack(side="left", padx=(0, 14))
        txt = ctk.CTkFrame(hdr, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(txt, text=title, font=ctk.CTkFont(size=28, weight="bold"),
                     text_color="#f0f6fc").pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(txt, text=subtitle, font=ctk.CTkFont(size=13),
                         text_color=TEXT_DIM).pack(anchor="w", pady=(2, 0))

    def _service_tile(self, parent, col: int, service: str) -> ctk.CTkFrame:
        meta = SERVICE_META[service]
        accent = meta["accent"]
        ready = meta.get("ready", True)
        card = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=20,
            border_width=2, border_color=accent if ready else "#30363d",
        )
        card.grid(row=0, column=col, sticky="nsew", padx=8, pady=8)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=22, pady=22)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        icon = ctk.CTkFrame(top, width=56, height=56, corner_radius=28, fg_color=accent)
        icon.pack(side="left")
        icon.pack_propagate(False)
        ctk.CTkLabel(icon, text=meta["icon"], font=ctk.CTkFont(size=26)).place(relx=0.5, rely=0.5, anchor="center")
        if not ready:
            ctk.CTkLabel(
                top, text="СКОРО", font=ctk.CTkFont(size=10, weight="bold"),
                fg_color="#30363d", corner_radius=6, text_color=TEXT_DIM, padx=8, pady=2,
            ).pack(side="right")

        ctk.CTkLabel(
            inner, text=meta["title"], font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#f0f6fc", anchor="w",
        ).pack(fill="x", pady=(16, 4))
        ctk.CTkLabel(
            inner, text=meta["subtitle"], font=ctk.CTkFont(size=12),
            text_color=TEXT_DIM, anchor="w", justify="left", wraplength=220,
        ).pack(fill="x", pady=(0, 16))

        btn_txt = "Открыть →" if ready else "Скоро"
        btn = ctk.CTkButton(
            inner, text=btn_txt, height=40, corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=accent if ready else "#30363d",
            hover_color=accent if ready else "#30363d",
            state="normal" if ready else "disabled",
            command=lambda s=service: self._enter_service(s),
        )
        btn.pack(fill="x")

        if ready:
            def _hover_in(_e=None):
                card.configure(fg_color=BG_CARD_HOVER, border_color=accent)
            def _hover_out(_e=None):
                card.configure(fg_color=BG_CARD, border_color=accent)
            for w in (card, inner, icon):
                w.bind("<Enter>", _hover_in)
                w.bind("<Leave>", _hover_out)
                w.bind("<Button-1>", lambda e, s=service: self._enter_service(s))
        return card

    def _ggsell_home_banner(self, parent) -> ctk.CTkFrame:
        meta = SERVICE_META["ggsell"]
        accent = meta["accent"]
        card = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=20,
            border_width=2, border_color=accent,
        )
        card.pack(fill="x", pady=(0, 20))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=20)
        inner.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 20))
        icon = ctk.CTkFrame(left, width=64, height=64, corner_radius=32, fg_color=accent)
        icon.pack(anchor="w")
        icon.pack_propagate(False)
        ctk.CTkLabel(icon, text=meta["icon"], font=ctk.CTkFont(size=30)).place(
            relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(
            left, text="Маркетплейс", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=accent,
        ).pack(anchor="w", pady=(10, 0))
        ctk.CTkLabel(
            left, text=meta["title"], font=ctk.CTkFont(size=26, weight="bold"),
            text_color="#f0f6fc",
        ).pack(anchor="w")
        ctk.CTkLabel(
            left, text=meta["subtitle"], font=ctk.CTkFont(size=12),
            text_color=TEXT_DIM, wraplength=280, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        mid = ctk.CTkFrame(inner, fg_color="transparent")
        mid.grid(row=0, column=1, sticky="ew")
        for i in range(3):
            mid.grid_columnconfigure(i, weight=1)
        self.home_ggs_orders = self._stat_box(mid, 0, "Заказов", "—", accent)
        self.home_ggs_monitor = self._stat_box(mid, 1, "Монитор", "—", accent)
        self.home_ggs_balance = self._stat_box(mid, 2, "Баланс", "—", accent)

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.grid(row=0, column=2, sticky="ne", padx=(20, 0))
        ctk.CTkButton(
            right, text="Открыть GGSELL →", width=180, height=48, corner_radius=12,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=accent, hover_color="#e67e00",
            command=lambda: self._enter_service("ggsell"),
        ).pack(pady=(12, 0))

        def _hover_in(_e=None):
            card.configure(fg_color=BG_CARD_HOVER)
        def _hover_out(_e=None):
            card.configure(fg_color=BG_CARD)
        for w in (card, inner, left, mid):
            w.bind("<Enter>", _hover_in)
            w.bind("<Leave>", _hover_out)
            w.bind("<Button-1>", lambda e: self._enter_service("ggsell"))
        return card

    def _page(self, name: str) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self._pages[name] = frame
        return frame

    def show_page(self, name: str) -> None:
        youtube_pages = {"run", "profiles", "archive", "cards", "vpn", "tools", "youtube_hub"}
        if name in youtube_pages and self._current_service != "youtube":
            self._current_service = "youtube"
            self._render_sidebar("youtube")
        elif name == "ggsell" and self._current_service != "ggsell":
            self._current_service = "ggsell"
            self._render_sidebar("ggsell")
        elif name in ("deepseek", "kling"):
            self._current_service = name
            self._render_sidebar(name)
        elif name == "home":
            self._current_service = None
            self._render_sidebar(None)

        for f in self._pages.values():
            f.grid_forget()
        self._pages[name].grid(row=0, column=0, sticky="nsew")
        self._current_page = name

        accent = SERVICE_META.get(self._current_service or "", {}).get("accent", ACCENT)
        for k, btn in self._nav_btns.items():
            active = k == name or (
                k == "youtube_hub"
                and name in ("run", "profiles", "archive", "cards", "vpn", "tools", "youtube_hub")
            )
            btn.configure(fg_color=accent if active else "transparent")

        refresh = {
            "home": lambda: (self._refresh_home_ggsell(), self._refresh_update_badge()),
            "youtube_hub": lambda: (self._refresh_youtube_hub(), self._refresh_update_badge()),
            "ggsell": self._refresh_ggsell,
            "profiles": self._refresh_profiles,
            "cards": self._refresh_cards,
            "archive": self._refresh_archive,
            "vpn": self._refresh_vpn_page,
            "settings": lambda: (
                self._refresh_settings_keys(), self._refresh_update_badge(), self._sync_settings_switches(),
            ),
        }
        fn = refresh.get(name)
        if fn:
            fn()

    def _card(self, parent, title: str, accent: str | None = None) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=RADIUS_CARD)
        f.pack(fill="x", pady=(0, 14))
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(14, 8))
        if accent:
            ctk.CTkFrame(hdr, width=3, height=18, fg_color=accent, corner_radius=2).pack(
                side="left", padx=(0, 10))
        ctk.CTkLabel(
            hdr, text=title, font=ctk.CTkFont(size=15, weight="bold"), text_color="#f0f6fc",
        ).pack(side="left")
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(0, 14))
        return inner

    def _hint(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text, justify="left", anchor="w",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=12), wraplength=720,
        ).pack(fill="x")

    def _toolbar(self, parent) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 12))
        return bar

    def _action_grid(self, parent, actions: list[tuple], cols: int = 2) -> ctk.CTkFrame:
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x")
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1)
        for i, item in enumerate(actions):
            txt, cmd, color = item[0], item[1], item[2]
            r, c = divmod(i, cols)
            ctk.CTkButton(
                grid, text=txt, height=46, corner_radius=RADIUS_BTN,
                font=ctk.CTkFont(size=13, weight="bold"),
                fg_color=color, hover_color=color, command=cmd,
            ).grid(row=r, column=c, sticky="ew", padx=4, pady=4)
        return grid

    def _list_panel(self, parent, height: int = 420) -> ctk.CTkScrollableFrame:
        return ctk.CTkScrollableFrame(
            parent, height=height, fg_color=BG_ELEVATED, corner_radius=12,
        )

    def _action_btn(self, parent, text: str, cmd: Callable, color: str = ACCENT, **kw) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=44, corner_radius=10,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=color, hover_color=color, command=cmd, **kw,
        )

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _build_home(self) -> None:
        p = self._page("home")
        self._page_header(p, "Главная", "Маркетплейс и автоматизация подписок", ACCENT)
        self._ggsell_home_banner(p)

        sec = ctk.CTkFrame(p, fg_color="transparent")
        sec.pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(
            sec, text="Подписки", font=ctk.CTkFont(size=17, weight="bold"), text_color="#f0f6fc",
        ).pack(anchor="w")
        self._hint(sec, "YouTube Premium, DeepSeek и Kling AI — в одном месте")

        tiles = ctk.CTkFrame(p, fg_color="transparent")
        tiles.pack(fill="x")
        for i in range(3):
            tiles.grid_columnconfigure(i, weight=1)
        for i, svc in enumerate(("youtube", "deepseek", "kling")):
            self._service_tile(tiles, i, svc)

    def _build_youtube_hub(self) -> None:
        p = self._page("youtube_hub")
        self._page_header(
            p, "YouTube Premium",
            "Flipkart · вход · покупка · VPN · карты",
            SVC_YOUTUBE,
        )

        stats = ctk.CTkFrame(p, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 14))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.dash_profiles = self._stat_box(stats, 0, "Профили", "—", SVC_YOUTUBE)
        self.dash_cards = self._stat_box(stats, 1, "Карты", "—", SVC_YOUTUBE)
        self.dash_gift = self._stat_box(stats, 2, "Гифт-карты", "—", SVC_YOUTUBE)
        self.dash_tg = self._stat_box(stats, 3, "Telegram", "—", SVC_YOUTUBE)

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, 14))
        self.dash_vpn_chip = ctk.CTkLabel(
            row, text="VPN…", font=ctk.CTkFont(size=12),
            fg_color=BG_CARD, corner_radius=8, padx=12, pady=8, text_color=TEXT_DIM,
        )
        self.dash_vpn_chip.pack(side="left")
        self.dash_balance = ctk.CTkLabel(
            row, text="", font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
        )
        self.dash_balance.pack(side="left", padx=(12, 0))

        quick = self._card(p, "Быстрый доступ", accent=SVC_YOUTUBE)
        self._action_grid(quick, [
            ("🚀  Запуск", lambda: self.show_page("run"), SVC_YOUTUBE),
            ("👤  Профили", lambda: self.show_page("profiles"), BTN_SECONDARY),
            ("💳  Карты", lambda: self.show_page("cards"), BTN_SECONDARY),
            ("🔒  VPN", lambda: self.show_page("vpn"), BTN_SECONDARY),
        ])

        tools_card = self._card(p, "Сервис", accent=SVC_YOUTUBE)
        self._action_grid(tools_card, [
            ("▶  Полный цикл", lambda: (self.show_page("run"), self._preset_run("full")), BTN_SUCCESS),
            ("🛠  Инструменты", lambda: self.show_page("tools"), BTN_SECONDARY),
        ])

    def _build_ggsell(self) -> None:
        p = self._page("ggsell")
        self._page_header(
            p, "GGSELL",
            "Заказы · мониторинг · доставка ссылок",
            SVC_GGSELL,
        )

        stats = ctk.CTkFrame(p, fg_color="transparent")
        stats.pack(fill="x", pady=(0, 14))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.ggs_stat_orders = self._stat_box(stats, 0, "Заказов", "—", SVC_GGSELL)
        self.ggs_stat_balance = self._stat_box(stats, 1, "Баланс", "—", SVC_GGSELL)
        self.ggs_stat_monitor = self._stat_box(stats, 2, "Монитор", "—", SVC_GGSELL)
        self.ggs_stat_api = self._stat_box(stats, 3, "API", "—", SVC_GGSELL)

        actions = self._card(p, "Управление", accent=SVC_GGSELL)
        self._action_grid(actions, [
            ("🔄  Обновить", self._refresh_ggsell, SVC_GGSELL),
            ("🔑  API-ключи", self._open_secrets, BTN_SECONDARY),
            ("📁  data/", lambda: self._open_folder("data"), BTN_SECONDARY),
            ("📋  Шаблоны", self._open_ggsell_templates, BTN_SECONDARY),
        ])

        orders_card = self._card(p, "Последние заказы", accent=SVC_GGSELL)
        self.ggs_orders_list = self._list_panel(orders_card, height=300)
        self.ggs_orders_list.pack(fill="both", expand=True)

        info = self._card(p, "Справка")
        self._hint(
            info,
            "Полное управление заказами — в Telegram-боте.\n"
            "Монитор проверяет новые заказы каждые ~15 сек.",
        )

    def _build_deepseek(self) -> None:
        p = self._page("deepseek")
        self._build_coming_soon(p, "deepseek")

    def _build_kling(self) -> None:
        p = self._page("kling")
        self._build_coming_soon(p, "kling")

    def _build_coming_soon(self, p: ctk.CTkScrollableFrame, service: str) -> None:
        meta = SERVICE_META[service]
        self._page_header(p, meta["title"], meta["subtitle"], meta["accent"])
        box = ctk.CTkFrame(
            p, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=2, border_color=meta["accent"],
        )
        box.pack(fill="x", pady=16, ipady=32)
        ctk.CTkLabel(box, text=meta["icon"], font=ctk.CTkFont(size=56)).pack(pady=(24, 8))
        ctk.CTkLabel(
            box, text="Скоро", font=ctk.CTkFont(size=28, weight="bold"), text_color=meta["accent"],
        ).pack()
        self._hint(box, "Раздел в разработке — автоматизация оплаты подписки.")
        ctk.CTkButton(
            p, text="←  Главная", height=40, fg_color=BTN_SECONDARY, command=self._go_home,
        ).pack(pady=8)

    def _stat_box(self, parent, col: int, title: str, value: str, accent: str = ACCENT) -> ctk.CTkLabel:
        box = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color="#30363d",
        )
        box.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkFrame(box, height=3, fg_color=accent, corner_radius=2).pack(fill="x", padx=12, pady=(12, 0))
        ctk.CTkLabel(box, text=title, text_color=TEXT_DIM, font=ctk.CTkFont(size=11)).pack(pady=(10, 0))
        lbl = ctk.CTkLabel(box, text=value, font=ctk.CTkFont(size=22, weight="bold"), text_color="#f0f6fc")
        lbl.pack(pady=(4, 14))
        return lbl

    def _build_run(self) -> None:
        p = self._page("run")
        self._page_header(p, "Запуск", "Автоматизация YouTube Premium", SVC_YOUTUBE)

        inner = self._card(p, "Параметры", accent=SVC_YOUTUBE)
        ctk.CTkLabel(inner, text="Режим", text_color=TEXT_DIM).grid(row=0, column=0, sticky="w", pady=6)
        self.run_mode = ctk.CTkComboBox(inner, width=420, values=[
            "Полный цикл (вход + покупка)",
            "Только вход на ПК",
            "Вход + Telegram (перехват)",
            "Вход с данными (до email)",
        ])
        self.run_mode.grid(row=0, column=1, sticky="ew", pady=6, padx=(12, 0))
        self.run_mode.set("Полный цикл (вход + покупка)")

        ctk.CTkLabel(inner, text="Аккаунтов", text_color=TEXT_DIM).grid(row=1, column=0, sticky="w", pady=6)
        self.run_accounts = ctk.CTkEntry(inner, width=120, placeholder_text="из config")
        self.run_accounts.grid(row=1, column=1, sticky="w", pady=6, padx=(12, 0))

        ctk.CTkLabel(inner, text="Тариф", text_color=TEXT_DIM).grid(row=2, column=0, sticky="w", pady=6)
        self.run_tariff = ctk.CTkComboBox(inner, width=220, values=["3 месяца (₹343)", "12 месяцев (₹1,499)"])
        self.run_tariff.grid(row=2, column=1, sticky="w", pady=6, padx=(12, 0))
        self.run_tariff.set("3 месяца (₹343)")

        self.run_headless = ctk.CTkCheckBox(inner, text="Фоновый браузер")
        self.run_headless.grid(row=3, column=1, sticky="w", pady=8, padx=(12, 0))
        inner.grid_columnconfigure(1, weight=1)

        btns = self._toolbar(p)
        self.run_start_btn = self._action_btn(
            btns, "▶  Старт", self._start_run, color=SUCCESS, width=140,
        )
        self.run_start_btn.pack(side="left", padx=(0, 8))
        self.run_stop_btn = ctk.CTkButton(
            btns, text="■  Стоп", width=100, height=44, corner_radius=RADIUS_BTN,
            fg_color=ERROR, state="disabled", command=self._stop_run,
        )
        self.run_stop_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="🔒 VPN", width=90, height=44, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, command=self._check_vpn,
        ).pack(side="left")

        self.run_status = ctk.CTkLabel(p, text="Готов к запуску", text_color=TEXT_DIM)
        self.run_status.pack(anchor="w")

    def _build_profiles(self) -> None:
        p = self._page("profiles")
        self._page_header(p, "Профили", "Активные Chrome-сессии Flipkart", SVC_YOUTUBE)

        toolbar = self._toolbar(p)
        for txt, w, cmd, color in [
            ("🔄", 44, self._refresh_profiles, BTN_SECONDARY),
            ("🌐 Chrome", 100, self._profile_open_chrome, BTN_SECONDARY),
            ("🛒 3 мес", 90, lambda: self._profile_buy(3), SVC_YOUTUBE),
            ("🛒 12 мес", 96, lambda: self._profile_buy(12), SVC_YOUTUBE),
            ("📍 Адрес", 90, self._profile_fill_address, BTN_SECONDARY),
        ]:
            ctk.CTkButton(
                toolbar, text=txt, width=w, height=36, corner_radius=8,
                fg_color=color, command=cmd,
            ).pack(side="left", padx=3)

        self.profile_list = self._list_panel(p, height=440)
        self.profile_list.pack(fill="both", expand=True)

    def _build_archive(self) -> None:
        p = self._page("archive")
        self._page_header(p, "Архив", "Использованные профили", SVC_YOUTUBE)
        toolbar = self._toolbar(p)
        ctk.CTkButton(
            toolbar, text="🔄 Обновить", width=110, height=36, corner_radius=8,
            fg_color=BTN_SECONDARY, command=self._refresh_archive,
        ).pack(side="left")
        self.archive_list = self._list_panel(p, height=500)
        self.archive_list.pack(fill="both", expand=True)

    def _build_cards(self) -> None:
        p = self._page("cards")
        self._page_header(p, "Карты", "Банковские и подарочные карты", SVC_YOUTUBE)

        bc_tool = self._toolbar(p)
        ctk.CTkButton(
            bc_tool, text="➕ Карта", height=36, corner_radius=8, fg_color=SUCCESS,
            command=self._show_add_card_dialog,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bc_tool, text="🗑", width=44, height=36, corner_radius=8, fg_color=ERROR,
            command=self._delete_card,
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            bc_tool, text="🔄", width=44, height=36, corner_radius=8, fg_color=BTN_SECONDARY,
            command=self._refresh_cards,
        ).pack(side="left", padx=3)

        inner = self._card(p, "Банковские карты", accent=SVC_YOUTUBE)
        self.cards_info = ctk.CTkTextbox(
            inner, height=120, font=ctk.CTkFont(family="Consolas", size=12), fg_color=BG_ELEVATED,
        )
        self.cards_info.pack(fill="x")
        self.cards_info.configure(state="disabled")

        gc_tool = self._toolbar(p)
        ctk.CTkButton(
            gc_tool, text="➕ Гифт-карты", height=36, corner_radius=8, fg_color=SUCCESS,
            command=self._toggle_gift_add_panel,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            gc_tool, text="📁 Файл", height=36, corner_radius=8, fg_color=BTN_SECONDARY,
            command=self._upload_gift_file,
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            gc_tool, text="🗑", width=44, height=36, corner_radius=8, fg_color=ERROR,
            command=self._delete_gift_card,
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            gc_tool, text="💳/🎁", height=36, corner_radius=8, fg_color=BTN_SECONDARY,
            command=self._toggle_pay_method,
        ).pack(side="left", padx=3)

        self.gift_summary = ctk.CTkLabel(
            p, text="", justify="left", anchor="w", font=ctk.CTkFont(size=13),
        )
        self.gift_summary.pack(fill="x", pady=(0, 6))

        self.gift_add_panel = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=RADIUS_CARD)
        self._gift_add_visible = False

        add_hdr = ctk.CTkFrame(self.gift_add_panel, fg_color="transparent")
        add_hdr.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            add_hdr, text="Добавление гифт-карт", font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            add_hdr, text="✕", width=30, fg_color="transparent", command=self._toggle_gift_add_panel,
        ).pack(side="right")

        add_inner = ctk.CTkFrame(self.gift_add_panel, fg_color="transparent")
        add_inner.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(add_inner, text="Номинал (₹)", text_color=TEXT_DIM).grid(row=0, column=0, sticky="w")
        import menu as _m
        self.gift_denom = ctk.CTkComboBox(add_inner, width=120, values=[str(d) for d in _m.GIFT_DENOMS])
        self.gift_denom.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=4)
        self.gift_denom.set("500")
        ctk.CTkLabel(
            add_inner,
            text="Формат: серия(14–19 цифр)  PIN(4–8)  — одна карта на строку",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11), anchor="w", justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 2))
        self.gift_input = ctk.CTkTextbox(
            add_inner, height=100, font=ctk.CTkFont(family="Consolas", size=12), fg_color=BG_ELEVATED,
        )
        self.gift_input.grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        add_inner.grid_columnconfigure(1, weight=1)
        add_btns = ctk.CTkFrame(add_inner, fg_color="transparent")
        add_btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ctk.CTkButton(
            add_btns, text="✓ Добавить", fg_color=SUCCESS, command=self._add_gift_cards_manual,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            add_btns, text="📁 Файл", fg_color=BTN_SECONDARY, command=self._upload_gift_file,
        ).pack(side="left")
        self.gift_add_result = ctk.CTkLabel(add_inner, text="", text_color=TEXT_DIM, anchor="w")
        self.gift_add_result.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        inner2 = self._card(p, "Список гифт-карт", accent=SVC_YOUTUBE)
        self.gift_list = self._list_panel(inner2, height=240)
        self.gift_list.pack(fill="both", expand=True)
        self._selected_gift: dict | None = None

        used_card = self._card(p, "История")
        self.gift_used_info = ctk.CTkTextbox(
            used_card, height=90, font=ctk.CTkFont(family="Consolas", size=11), fg_color=BG_ELEVATED,
        )
        self.gift_used_info.pack(fill="x")
        self.gift_used_info.configure(state="disabled")

    def _build_vpn(self) -> None:
        p = self._page("vpn")
        self._page_header(p, "VPN", "Расширение VPNLY для Flipkart", SVC_YOUTUBE)

        inner = self._card(p, "Статус", accent=SVC_YOUTUBE)
        self.vpn_page_status = ctk.CTkLabel(
            inner, text="Загрузка…", justify="left", anchor="w", font=ctk.CTkFont(size=14),
        )
        self.vpn_page_status.pack(fill="x")
        self.vpn_page_ext = ctk.CTkLabel(
            inner, text="", justify="left", anchor="w", text_color=TEXT_DIM, font=ctk.CTkFont(size=12),
        )
        self.vpn_page_ext.pack(fill="x", pady=(8, 0))

        btns = self._toolbar(p)
        ctk.CTkButton(
            btns, text="🔒 Проверить VPN", height=42, corner_radius=RADIUS_BTN,
            fg_color=SVC_YOUTUBE, command=self._check_vpn,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="📦 Установить расширения", height=42, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, command=self._install_extensions_bg,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="📁 vpn_extension/", height=42, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, command=self._open_vpn_folder,
        ).pack(side="left")

        info = self._card(p, "Справка")
        self._hint(
            info,
            "Расширение ставится в профили автоматически.\n"
            "VPN включается в фоне перед каждым открытием Flipkart.",
        )

    def _build_tools(self) -> None:
        p = self._page("tools")
        self._page_header(p, "Инструменты", "Дополнительные действия", SVC_YOUTUBE)
        grid_wrap = self._card(p, "Действия", accent=SVC_YOUTUBE)
        self._action_grid(grid_wrap, [
            ("✅ Проверить активацию", self._tool_check_activation, BTN_SECONDARY),
            ("🍪 Восстановить cookies", self._tool_restore_cookies, BTN_SECONDARY),
            ("🗑 Очистить профили", self._tool_purge, ERROR),
            ("📂 cookies_backup/", lambda: self._open_folder("cookies_backup"), BTN_SECONDARY),
            ("📂 chrome_profiles/", lambda: self._open_folder("chrome_profiles"), BTN_SECONDARY),
        ], cols=2)

    def _build_logs(self) -> None:
        p = self._page("logs")
        self._page_header(p, "Логи", "Журнал событий", ACCENT)
        toolbar = self._toolbar(p)
        ctk.CTkButton(
            toolbar, text="Очистить", width=90, height=36, corner_radius=8,
            fg_color=BTN_SECONDARY, command=self._clear_logs,
        ).pack(side="left")
        ctk.CTkButton(
            toolbar, text="Открыть файл", width=110, height=36, corner_radius=8,
            fg_color=BTN_SECONDARY, command=self._open_log_file,
        ).pack(side="left", padx=8)
        self.log_text = ctk.CTkTextbox(
            p, font=ctk.CTkFont(family="Consolas", size=12), fg_color=BG_ELEVATED, corner_radius=12,
        )
        self.log_text.pack(fill="both", expand=True)
        self._log_file_pos = 0

    def _build_settings(self) -> None:
        p = self._page("settings")
        self._page_header(p, "Настройки", "Параметры SubHub", ACCENT)

        inner = self._card(p, "Система")
        for txt, cmd in [
            ("📦 Зависимости + Chromium", self._install_deps),
            ("📁 Папка проекта", lambda: os.startfile(str(_HERE))),
            ("🔑 secrets.yaml", self._open_secrets),
            ("⚙️ config.yaml", self._open_config),
        ]:
            ctk.CTkButton(
                inner, text=txt, height=38, corner_radius=8, fg_color=BTN_SECONDARY, command=cmd,
            ).pack(fill="x", pady=3)

        inner_upd = self._card(p, "Обновления", accent=BTN_SUCCESS)
        self.settings_upd_info = ctk.CTkLabel(
            inner_upd, text="Проверка…", justify="left", anchor="w", text_color=TEXT_DIM,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.settings_upd_info.pack(fill="x", pady=(0, 4))
        self.settings_upd_sub = ctk.CTkLabel(
            inner_upd, text="", justify="left", anchor="w", text_color=TEXT_DIM,
        )
        self.settings_upd_sub.pack(fill="x", pady=(0, 6))
        self.settings_upd_list = ctk.CTkTextbox(
            inner_upd, height=100, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=BG_ELEVATED, corner_radius=8,
        )
        self.settings_upd_list.pack(fill="x", pady=(0, 8))
        self.settings_upd_list.configure(state="disabled")
        self.settings_upd_check_btn = ctk.CTkButton(
            inner_upd, text="🔄  Проверить", height=34, corner_radius=8,
            fg_color=BTN_SECONDARY, command=self._tool_check_updates_now,
        )
        self.settings_upd_check_btn.pack(fill="x", pady=(0, 6))
        self.settings_upd_btn = ctk.CTkButton(
            inner_upd, text="⬆  Скачать и перезапустить", height=42, corner_radius=RADIUS_BTN,
            font=ctk.CTkFont(size=14, weight="bold"), fg_color=BTN_SUCCESS,
            command=self._update_and_restart,
        )
        ctk.CTkButton(
            inner_upd, text="♻  Перезапустить", height=36, corner_radius=8,
            fg_color=BTN_SECONDARY, command=self._restart_only,
        ).pack(fill="x", pady=4)
        ctk.CTkButton(
            inner_upd, text="🖥  Ярлык на рабочем столе", height=36, corner_radius=8,
            fg_color=BTN_SECONDARY, command=self._create_desktop_shortcut_ui,
        ).pack(fill="x", pady=4)

        inner2 = self._card(p, "API-ключи")
        self.settings_keys = ctk.CTkLabel(inner2, text="", justify="left", anchor="w")
        self.settings_keys.pack(fill="x")

        inner_bg = self._card(p, "Фоновый режим")
        self._hint(
            inner_bg,
            "Окно можно закрыть — бот и мониторинг продолжат работать в трее.",
        )

        self.sw_background = ctk.CTkSwitch(
            inner_bg, text="Постоянная работа в фоне",
            command=self._on_setting_background,
        )
        self.sw_background.pack(anchor="w", pady=4)
        if self._app_settings.get("background_mode", True):
            self.sw_background.select()

        self.sw_tray = ctk.CTkSwitch(
            inner_bg, text="Сворачивать в трей при закрытии окна",
            command=self._on_setting_tray,
        )
        self.sw_tray.pack(anchor="w", pady=4)
        if self._app_settings.get("minimize_to_tray", True):
            self.sw_tray.select()

        self.sw_startup = ctk.CTkSwitch(
            inner_bg, text="Запускать при входе в Windows",
            command=self._on_setting_startup,
        )
        self.sw_startup.pack(anchor="w", pady=4)
        if self._app_settings.get("run_at_startup"):
            self.sw_startup.select()

        self.sw_start_min = ctk.CTkSwitch(
            inner_bg, text="Запускать свёрнутым в трей",
            command=self._on_setting_start_min,
        )
        self.sw_start_min.pack(anchor="w", pady=4)
        if self._app_settings.get("start_minimized"):
            self.sw_start_min.select()

        ctk.CTkButton(
            inner_bg, text="📌 Свернуть в трей сейчас", height=36,
            fg_color="#37474f", command=self._minimize_to_tray,
        ).pack(fill="x", pady=(10, 0))

    def _sync_settings_switches(self) -> None:
        s = self._app_settings
        for sw, key in (
            (getattr(self, "sw_background", None), "background_mode"),
            (getattr(self, "sw_tray", None), "minimize_to_tray"),
            (getattr(self, "sw_startup", None), "run_at_startup"),
            (getattr(self, "sw_start_min", None), "start_minimized"),
        ):
            if sw is None:
                continue
            if s.get(key):
                sw.select()
            else:
                sw.deselect()

    def _persist_app_settings(self) -> None:
        _save_app_settings(self._app_settings)

    def _on_setting_background(self) -> None:
        self._app_settings["background_mode"] = bool(self.sw_background.get())
        if not self._app_settings["background_mode"]:
            self._app_settings["start_minimized"] = False
            if hasattr(self, "sw_start_min"):
                self.sw_start_min.deselect()
        self._persist_app_settings()

    def _on_setting_tray(self) -> None:
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

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def _bootstrap_backend(self) -> None:
        import menu as m
        try:
            scan = m.scan_profiles_extension_status()
            if m._vpn_extension_dir() and scan["total"]:
                m._set_vpn_bg_status(
                    "warming",
                    f"Фон: расширение {scan['with_ext']}/{scan['total']} проф.…",
                )
            m.start_background_bootstrap()
            import grizzly as gz
            gz.start_global_monitor()
            try:
                from ggsell.monitor import start_monitor
                gs = (m._read_secrets().get("ggsel") or {})
                if gs.get("api_key") and gs.get("seller_id"):
                    start_monitor(gs["api_key"], int(gs["seller_id"]))
            except Exception:
                pass
            import bot as bot_mod
            r = bot_mod.ensure_tg_bot("app")
            if r == "started":
                self._log("✓ Telegram-бот запущен (приложение)")
            elif r == "active":
                self._log("✓ Telegram-бот активен")
            elif r == "no_token":
                self._log("⚠ Telegram: токен не настроен")
            self._log("✓ Фоновые сервисы запущены")
            self._log("⏳ Проверка расширений в профилях — в фоне (без Chrome)…")
            self.after(800, self._ensure_desktop_shortcut)
        except Exception as e:
            self._log(f"⚠ Ошибка инициализации: {e}")

    def _log(self, msg: str) -> None:
        self._log_sink.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def _run_bg(self, fn: Callable, label: str = "") -> None:
        if label:
            self._log(label)

        def _wrap():
            try:
                fn()
            except Exception as e:
                self._log(f"Ошибка: {e}")

        threading.Thread(target=_wrap, daemon=True).start()

    def _tick_logs(self) -> None:
        for chunk in self._log_sink.drain():
            self.log_text.insert("end", chunk)
            self.log_text.see("end")
        log_path = _HERE / "automation.log"
        try:
            if log_path.exists():
                size = log_path.stat().st_size
                if size > self._log_file_pos:
                    with open(log_path, encoding="utf-8", errors="replace") as f:
                        f.seek(self._log_file_pos)
                        new = f.read()
                        self._log_file_pos = size
                        if new:
                            self.log_text.insert("end", new)
                            self.log_text.see("end")
        except Exception:
            pass
        self.after(400, self._tick_logs)

    def _tick_status(self) -> None:
        try:
            import menu as m
            import bot as bot_mod
            profiles = len(m._load_done_profiles())
            st = getattr(bot_mod, "_tg_status", "?")
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
            host = m.active_host() or "—"
            running = "🟢 Запуск" if (self._proc and self._proc.poll() is None) or self._external_auto else "⚪ Ожидание"
            self.status_lbl.configure(text=f"{running}\n{profiles} профилей · {host}")

            vs = m.get_vpn_bg_status()
            state = vs.get("state", "idle")
            msg = vs.get("message", "")
            colors = {
                "ready": SUCCESS, "warming": WARNING, "installing": WARNING,
                "error": ERROR, "no_ext": TEXT_DIM, "idle": TEXT_DIM,
            }
            labels = {
                "ready": f"🔒 VPN: {msg or 'готов'}",
                "warming": "📦 Расширения в профили…",
                "installing": "📦 Установка…",
                "error": f"🔒 VPN: {msg[:40]}",
                "no_ext": "🔒 VPN: нет расширения",
                "idle": "🔒 VPN: ожидание",
            }
            color = colors.get(state, TEXT_DIM)
            text = labels.get(state, "🔒 VPN: …")
            if self._vpn_status_lbl:
                self._vpn_status_lbl.configure(text=text, text_color=color)
            if hasattr(self, "dash_vpn_chip"):
                self.dash_vpn_chip.configure(text=text.replace("🔒 ", ""), text_color=color)
            self._refresh_update_badge()
            self._sync_from_runtime()
        except Exception:
            pass
        self.after(2500, self._tick_status)

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
                pass

        ext, ast = m.shared_automation_running()
        local = self._proc and self._proc.poll() is None
        self._external_auto = ext and not local
        if self._external_auto:
            owner = ast.get("automation_owner") or "tg"
            mode = ast.get("automation_mode") or ""
            self.run_start_btn.configure(state="disabled")
            self.run_stop_btn.configure(state="normal")
            self.run_status.configure(
                text=f"▶ Telegram: {mode or 'автоматизация'}", text_color=WARNING)
        elif not local and hasattr(self, "run_start_btn"):
            if str(self.run_status.cget("text")).startswith("▶ Telegram"):
                self.run_start_btn.configure(state="normal")
                self.run_stop_btn.configure(state="disabled")
                self.run_status.configure(text="Готов к запуску", text_color=TEXT_DIM)

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
        btn.configure(text=text, command=command, fg_color=color, state=state)
        if not btn.winfo_ismapped():
            btn.pack(fill="x", pady=4, after=self.settings_upd_check_btn)

    def _hide_update_action_btn(self) -> None:
        btn = getattr(self, "settings_upd_btn", None)
        if btn is not None and btn.winfo_ismapped():
            btn.pack_forget()

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
                title = f"⚡ Доступно обновлений: {n}"
                sub = f"Обнаружено: {when}"
                title_color = WARNING
                list_text = self._update_commits_text(commits)
            elif needs_restart:
                title = "♻ Требуется перезапуск"
                sub = "Обновление скачано — перезапустите для применения"
                title_color = WARNING
                list_text = ""
            elif checked:
                title = "✓ Версия актуальна"
                sub = f"Последняя проверка: {when}"
                title_color = SUCCESS
                list_text = ""
            else:
                title = "Проверка обновлений…"
                sub = "Ожидание GitHub"
                title_color = TEXT_DIM
                list_text = ""
            if hasattr(self, "settings_upd_info"):
                self.settings_upd_info.configure(text=title, text_color=title_color)
            if hasattr(self, "settings_upd_sub"):
                self.settings_upd_sub.configure(text=sub)
            if hasattr(self, "settings_upd_list"):
                self._set_readonly_text(self.settings_upd_list, list_text)
            if n:
                self._show_update_action_btn(
                    f"⬆  Скачать {n} обновлений и перезапустить",
                    self._update_and_restart, BTN_SUCCESS,
                )
            elif needs_restart:
                self._show_update_action_btn(
                    "♻  Перезапустить для применения",
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
        except Exception as e:
            self._log(f"Ошибка: {e}")
        threading.Thread(target=self._fetch_balance, daemon=True).start()

    def _refresh_home_ggsell(self) -> None:
        if not hasattr(self, "home_ggs_orders"):
            return
        import menu as m
        gs = m._read_secrets().get("ggsel") or {}
        api_ok = bool(gs.get("api_key") and gs.get("seller_id") and "YOUR_" not in str(gs.get("api_key", "")))
        monitor_on = any(t.name == "ggsel-monitor" and t.is_alive() for t in threading.enumerate())
        self.home_ggs_monitor.configure(text="🟢" if monitor_on else "⚪")
        done_count = 0
        try:
            done_path = _HERE / "data" / "ggsel_done.json"
            if done_path.exists():
                done_count = len(json.loads(done_path.read_text(encoding="utf-8")).get("done", {}) or {})
        except Exception:
            pass
        self.home_ggs_orders.configure(text=str(done_count))
        if api_ok:
            threading.Thread(target=self._fetch_ggsell_balance, daemon=True).start()
        else:
            self.home_ggs_balance.configure(text="—")

    def _refresh_ggsell(self) -> None:
        import menu as m
        sec = m._read_secrets()
        gs = sec.get("ggsel") or {}
        api_ok = bool(gs.get("api_key") and gs.get("seller_id") and "YOUR_" not in str(gs.get("api_key", "")))
        self.ggs_stat_api.configure(text="✓" if api_ok else "✗")

        monitor_on = any(t.name == "ggsel-monitor" and t.is_alive() for t in threading.enumerate())
        self.ggs_stat_monitor.configure(text="🟢" if monitor_on else "⚪")

        done_count = 0
        done_path = _HERE / "data" / "ggsel_done.json"
        done_data: dict = {}
        try:
            if done_path.exists():
                raw = json.loads(done_path.read_text(encoding="utf-8"))
                done_data = raw.get("done", {}) or {}
                done_count = len(done_data)
        except Exception:
            pass
        self.ggs_stat_orders.configure(text=str(done_count))
        if hasattr(self, "home_ggs_orders"):
            self.home_ggs_orders.configure(text=str(done_count))
        if hasattr(self, "home_ggs_monitor"):
            mon = "🟢" if monitor_on else "⚪"
            self.home_ggs_monitor.configure(text=mon)

        for w in self.ggs_orders_list.winfo_children():
            w.destroy()
        if not done_data:
            ctk.CTkLabel(
                self.ggs_orders_list, text="Заказов пока нет", text_color=TEXT_DIM,
            ).pack(pady=24)
        else:
            items = sorted(done_data.items(), key=lambda x: x[1], reverse=True)[:25]
            links = {}
            try:
                if done_path.exists():
                    links = json.loads(done_path.read_text(encoding="utf-8")).get("links", {}) or {}
            except Exception:
                pass
            for inv_id, dt in items:
                row = ctk.CTkFrame(self.ggs_orders_list, fg_color=BG_CARD, corner_radius=8)
                row.pack(fill="x", pady=3, padx=2)
                has_link = "✓" if str(inv_id) in links or int(inv_id) in links else "·"
                ctk.CTkLabel(
                    row, text=f"  #{inv_id}", font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
                ).pack(side="left", padx=8, pady=10)
                ctk.CTkLabel(row, text=f"{has_link}  {dt}", text_color=TEXT_DIM,
                             font=ctk.CTkFont(size=11)).pack(side="right", padx=12)

        if api_ok:
            threading.Thread(target=self._fetch_ggsell_balance, daemon=True).start()
        else:
            self.ggs_stat_balance.configure(text="—")

    def _fetch_ggsell_balance(self) -> None:
        async def _run():
            try:
                import menu as m
                from ggsell.client import GGSellClient
                gs = m._read_secrets().get("ggsel") or {}
                c = GGSellClient(gs["api_key"], int(gs["seller_id"]), http_timeout=15)
                bal = await c.get_balance()
                await c.close()
                self.after(0, lambda b=bal: self._set_ggsell_balance(f"${b:.2f}"))
            except Exception as e:
                self.after(0, lambda: self._set_ggsell_balance("ошибка"))
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
                self.after(0, lambda: self.dash_balance.configure(text=f"GrizzlySMS: ${bal:.4f}"))
            except Exception as e:
                self.after(0, lambda: self.dash_balance.configure(text=f"Баланс: {e}"))
        asyncio.run(_run())

    def _refresh_settings_keys(self) -> None:
        import menu as m
        sec = m._read_secrets()
        lines = []
        for name, section, key in [
            ("GrizzlySMS", "grizzlysms", "api_key"),
            ("Telegram", "telegram", "token"),
            ("GGSell", "ggsel", "api_key"),
        ]:
            val = (sec.get(section) or {}).get(key, "")
            ok = "✅" if val and "YOUR_" not in str(val) else "❌"
            lines.append(f"{ok}  {name}")
        self.settings_keys.configure(text="\n".join(lines))

    def _refresh_profiles(self) -> None:
        import menu as m
        for w in self.profile_list.winfo_children():
            w.destroy()
        self._profile_rows = m._load_done_profiles()
        self._selected_profile = None
        if not self._profile_rows:
            ctk.CTkLabel(self.profile_list, text="Нет профилей", text_color=TEXT_DIM).pack(pady=24)
            return
        for p in self._profile_rows:
            phone = m._disp_phone(p.get("username", "?"))
            status = p.get("status") or "готов"
            row = ctk.CTkFrame(self.profile_list, fg_color=BG_CARD, corner_radius=10)
            row.pack(fill="x", pady=3, padx=4)

            def _sel(prof=p, r=row):
                self._selected_profile = prof
                for c in self.profile_list.winfo_children():
                    if isinstance(c, ctk.CTkFrame):
                        c.configure(fg_color=BG_CARD)
                r.configure(fg_color=ACCENT)

            row.bind("<Button-1>", lambda e, fn=_sel: fn())
            ctk.CTkLabel(row, text=f"  {phone}", font=ctk.CTkFont(size=14, weight="bold"),
                         anchor="w").pack(side="left", padx=8, pady=10)
            ctk.CTkLabel(row, text=status, text_color=TEXT_DIM).pack(side="right", padx=12)
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, fn=_sel: fn())

    def _refresh_archive(self) -> None:
        import menu as m
        for w in self.archive_list.winfo_children():
            w.destroy()
        records = m._load_archive_records()
        if not records:
            ctk.CTkLabel(self.archive_list, text="Архив пуст", text_color=TEXT_DIM).pack(pady=24)
            return
        for r in records[:80]:
            phone = r.get("username", "?")
            used = r.get("used_str", "—")
            months = r.get("subscription_months")
            sub = f" · {months} мес." if months else ""
            row = ctk.CTkFrame(self.archive_list, fg_color=BG_CARD, corner_radius=10)
            row.pack(fill="x", pady=3, padx=4)
            ctk.CTkLabel(row, text=f"  +91 {phone}{sub}", font=ctk.CTkFont(size=13, weight="bold"),
                         anchor="w").pack(side="left", padx=8, pady=8)
            ctk.CTkLabel(row, text=used, text_color=TEXT_DIM, font=ctk.CTkFont(size=11)
                         ).pack(side="right", padx=10)

    def _refresh_cards(self) -> None:
        import menu as m
        cards = m._load_cards()
        bank_lines = []
        for i, c in enumerate(cards, 1):
            nick = c.get("nickname") or c.get("name") or f"Карта {i}"
            num = c.get("number", "")
            exp = c.get("expiry", "—")
            name = c.get("name", "—")
            addr = c.get("address", "")
            city = c.get("city", "")
            state = c.get("state", "")
            zipc = c.get("zipcode", "")
            country = c.get("country", "")
            bank_lines.append(
                f"[{i}] {nick}\n"
                f"    Номер: {num}\n"
                f"    Срок: {exp}   Имя: {name}\n"
                f"    Адрес: {addr}, {city}, {state} {zipc}, {country}\n"
            )
        self.cards_info.configure(state="normal")
        self.cards_info.delete("1.0", "end")
        self.cards_info.insert("1.0", "".join(bank_lines) if bank_lines else "  Банковских карт нет")
        self.cards_info.configure(state="disabled")

        gc = m._load_gift_cards()
        bal = m._gift_balance(gc)
        pm = m._load_pay_method()
        pm_txt = "🎁 гифт-карты" if pm == "gift" else "💳 банковская карта"
        by_denom: dict[int, int] = {}
        for c in gc:
            if c.get("number") and c.get("pin"):
                d = int(c.get("denom") or 0)
                by_denom[d] = by_denom.get(d, 0) + 1
        breakdown = "  ·  ".join(f"₹{d}×{by_denom[d]}" for d in sorted(by_denom, reverse=True)) or "—"
        self.gift_summary.configure(
            text=f"Баланс: ₹{bal}  ({len(gc)} шт.)  ·  Оплата: {pm_txt}\n{breakdown}")

        for w in self.gift_list.winfo_children():
            w.destroy()
        self._selected_gift = None
        if not gc:
            ctk.CTkLabel(self.gift_list, text="Нет гифт-карт — добавьте выше",
                         text_color=TEXT_DIM).pack(pady=20)
        else:
            for i, c in enumerate(sorted(gc, key=lambda x: -int(x.get("denom") or 0)), 1):
                denom = int(c.get("denom") or 0)
                series = c.get("number", "")
                pin = c.get("pin", "")
                added = m._fmt_msk(c["added_ts"]) if c.get("added_ts") else "—"
                row = ctk.CTkFrame(self.gift_list, fg_color="#253350", corner_radius=8)
                row.pack(fill="x", pady=3, padx=4)

                def _sel(card=c, r=row):
                    self._selected_gift = card
                    for ch in self.gift_list.winfo_children():
                        if isinstance(ch, ctk.CTkFrame):
                            ch.configure(fg_color="#253350")
                    r.configure(fg_color=ACCENT)

                row.bind("<Button-1>", lambda e, fn=_sel: fn())
                txt = (
                    f"  [{i}]  ₹{denom}\n"
                    f"      Серия:  {series}\n"
                    f"      PIN:    {pin}\n"
                    f"      Добавлена: {added}"
                )
                lbl = ctk.CTkLabel(row, text=txt, justify="left", anchor="w",
                                   font=ctk.CTkFont(family="Consolas", size=12))
                lbl.pack(anchor="w", padx=10, pady=8)
                lbl.bind("<Button-1>", lambda e, fn=_sel: fn())

        used = m._load_gift_used()
        used_lines = []
        for u in list(reversed(used))[:30]:
            st = "↩ другой акк." if u.get("status") == "used_elsewhere" else "✔ применена"
            when = u.get("used_str") or "—"
            prof = u.get("profile") or "—"
            used_lines.append(
                f"{st}  ₹{int(u.get('denom') or 0)}  серия {u.get('number','')}  "
                f"PIN {u.get('pin','')}  ·  {when}  ·  {prof}\n"
            )
        self.gift_used_info.configure(state="normal")
        self.gift_used_info.delete("1.0", "end")
        self.gift_used_info.insert("1.0", "".join(used_lines) if used_lines else "  История пуста")
        self.gift_used_info.configure(state="disabled")
        self._refresh_youtube_hub()

    def _refresh_vpn_page(self) -> None:
        import menu as m
        vs = m.get_vpn_bg_status()
        ext = m._vpn_extension_dir()
        scan = m.scan_profiles_extension_status()
        state = vs.get("state", "idle")
        msg = vs.get("message", "")
        icons = {"ready": "✅", "error": "❌", "warming": "⏳", "installing": "📦", "no_ext": "⚠️"}
        prof_line = (
            f"Профили: {scan['with_ext']}/{scan['total']} с расширением"
            + (f" · без: {scan['missing']}" if scan["missing"] else "")
        )
        self.vpn_page_status.configure(
            text=f"{icons.get(state, '•')}  {state.upper()}\n{msg}\n{prof_line}")
        self.vpn_page_ext.configure(
            text=f"Расширение: {ext or 'не найдено (положите в vpn_extension/)'}")

    # ── Run ───────────────────────────────────────────────────────────────────

    def _preset_run(self, mode: str) -> None:
        mapping = {
            "full": "Полный цикл (вход + покупка)",
            "login_pc": "Только вход на ПК",
            "tg_intercept": "Вход + Telegram (перехват)",
        }
        self.run_mode.set(mapping.get(mode, mapping["full"]))

    def _build_run_cmd(self) -> list[str]:
        mode = self.run_mode.get()
        months = 12 if "12" in self.run_tariff.get() else 3
        accounts = self.run_accounts.get().strip()
        headless = self.run_headless.get()
        if mode == "Полный цикл (вход + покупка)":
            cmd = [sys.executable, str(_HERE / "menu.py"), "--full-cycle", "--tariffs", str(months)]
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
        self.run_status.configure(text="Выполняется…", text_color=WARNING)
        self.run_start_btn.configure(state="disabled")
        self.run_stop_btn.configure(state="normal")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

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
                code = self._proc.wait() if self._proc else -1
                m.clear_automation_proc()
                self.after(0, lambda: self._run_finished(code))
            except Exception as e:
                m.clear_automation_proc()
                self.after(0, lambda: self._run_finished(-1, str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _run_finished(self, code: int, err: str = "") -> None:
        self.run_start_btn.configure(state="normal")
        self.run_stop_btn.configure(state="disabled")
        if err:
            self.run_status.configure(text=f"Ошибка: {err}", text_color=ERROR)
        elif code == 0:
            self.run_status.configure(text="✓ Завершено", text_color=SUCCESS)
        else:
            self.run_status.configure(text=f"Код {code}", text_color=WARNING)
        self._log(f"■ Завершено (код {code})")
        self._refresh_youtube_hub()

    def _stop_run(self) -> None:
        import menu as m
        if self._proc and self._proc.poll() is None:
            self._log("■ Остановка…")
            try:
                if os.name == "nt":
                    import signal
                    self._proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self._proc.terminate()
            except Exception:
                self._proc.kill()
            m.clear_automation_proc()
            return
        ext, st = m.shared_automation_running()
        if ext:
            pid = int(st.get("automation_pid") or 0)
            self._log(f"■ Остановка процесса Telegram (PID {pid})…")
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, cwd=str(_HERE),
                    )
                else:
                    os.kill(pid, 15)
            except Exception:
                pass
            m.clear_automation_proc()
            self._external_auto = False
            self.run_start_btn.configure(state="normal")
            self.run_stop_btn.configure(state="disabled")
            self.run_status.configure(text="Остановлено", text_color=WARNING)

    def _check_vpn(self) -> None:
        self._log("Проверка VPN…")
        self.run_status.configure(text="Проверка VPN…", text_color=WARNING)

        def _worker():
            import menu as m
            try:
                ok = asyncio.run(m._check_flipkart_accessible())
                msg = "✓ VPN OK · Flipkart доступен" if ok else "✗ Flipkart недоступен"
                self._log(msg)
                color = SUCCESS if ok else ERROR
                self.after(0, lambda: self.run_status.configure(text=msg, text_color=color))
                if ok:
                    m._set_vpn_bg_status("ready", "Flipkart доступен")
            except Exception as e:
                self._log(f"Ошибка: {e}")

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

        threading.Thread(target=_w, daemon=True).start()

    # ── Profiles ──────────────────────────────────────────────────────────────

    def _profile_open_chrome(self) -> None:
        if not self._selected_profile:
            self._log("Выберите профиль")
            return
        import menu as m
        ok = m.open_chrome(self._selected_profile["path"])
        self._log("Chrome: VPN в фоне → Flipkart" if ok else "Ошибка открытия Chrome")

    def _profile_buy(self, months: int) -> None:
        if not self._selected_profile:
            self._log("Выберите профиль")
            return
        path = self._selected_profile["path"]
        self._log(f"Покупка {months} мес.")

        def _w():
            import menu as m
            try:
                ok, msg = asyncio.run(m._do_buy_membership(path, months, card=None))
                self._log(f"{'✓' if ok else '✗'} {msg}")
            except Exception as e:
                self._log(f"Ошибка: {e}")
            self.after(0, self._refresh_profiles)

        threading.Thread(target=_w, daemon=True).start()

    def _profile_fill_address(self) -> None:
        if not self._selected_profile:
            self._log("Выберите профиль")
            return
        self._show_address_dialog(self._selected_profile["path"])

    # ── Cards ─────────────────────────────────────────────────────────────────

    def _show_add_card_dialog(self) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("Добавить карту")
        dlg.geometry("480x520")
        dlg.transient(self)
        dlg.grab_set()

        fields: dict[str, ctk.CTkEntry] = {}
        labels = [
            ("nickname", "Название"), ("number", "Номер карты"), ("expiry", "MM/YY"),
            ("cvv", "CVV"), ("name", "Имя на карте"), ("country", "Страна"),
            ("zipcode", "Индекс"), ("state", "Штат"), ("city", "Город"), ("address", "Адрес"),
        ]
        scroll = ctk.CTkScrollableFrame(dlg)
        scroll.pack(fill="both", expand=True, padx=16, pady=16)
        for key, label in labels:
            ctk.CTkLabel(scroll, text=label, text_color=TEXT_DIM).pack(anchor="w")
            e = ctk.CTkEntry(scroll)
            e.pack(fill="x", pady=(2, 8))
            fields[key] = e
        fields["country"].insert(0, "USA")

        def _save():
            import menu as m
            data = {k: v.get().strip() for k, v in fields.items()}
            if len(data.get("number", "").replace(" ", "")) < 13:
                messagebox.showerror("Ошибка", "Неверный номер карты")
                return
            data["name"] = data["name"].upper()
            cards = m._load_cards()
            cards.append(data)
            m._save_cards(cards)
            self._log(f"Карта «{data['nickname']}» добавлена")
            self._refresh_cards()
            dlg.destroy()

        ctk.CTkButton(dlg, text="Сохранить", fg_color=SUCCESS, command=_save).pack(pady=12)

    def _delete_card(self) -> None:
        import menu as m
        cards = m._load_cards()
        if not cards:
            return
        dlg = ctk.CTkInputDialog(text=f"Номер карты [1-{len(cards)}]:", title="Удалить карту")
        val = dlg.get_input()
        try:
            idx = int(val) - 1
            if 0 <= idx < len(cards):
                removed = cards.pop(idx)
                m._save_cards(cards)
                self._log(f"Удалена: {removed.get('nickname')}")
                self._refresh_cards()
        except (TypeError, ValueError):
            pass

    def _toggle_gift_add_panel(self) -> None:
        if self._gift_add_visible:
            self.gift_add_panel.pack_forget()
            self._gift_add_visible = False
        else:
            self.gift_add_panel.pack(fill="x", pady=(0, 10), after=self.gift_summary)
            self._gift_add_visible = True

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
        if self._selected_gift:
            num = str(self._selected_gift.get("number", ""))
            cards = [c for c in m._load_gift_cards() if str(c.get("number")) != num]
            m._save_gift_cards(cards)
            self._log(f"Удалена гифт-карта …{num[-4:]}")
            self._refresh_cards()
            return
        gc = m._load_gift_cards()
        if not gc:
            return
        dlg = ctk.CTkInputDialog(text=f"Номер карты [1-{len(gc)}] или 0=все:", title="Удалить гифт-карту")
        val = dlg.get_input()
        try:
            idx = int(val)
            if idx == 0:
                if messagebox.askyesno("Подтверждение", f"Удалить все {len(gc)} гифт-карт?"):
                    m._save_gift_cards([])
                    self._log("Все гифт-карты удалены")
                    self._refresh_cards()
            elif 1 <= idx <= len(gc):
                sorted_gc = sorted(gc, key=lambda x: -int(x.get("denom") or 0))
                removed = sorted_gc[idx - 1]
                cards = [c for c in gc if str(c.get("number")) != str(removed.get("number"))]
                m._save_gift_cards(cards)
                self._log(f"Удалена гифт-карта …{str(removed.get('number',''))[-4:]}")
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
                    self._log(f"{'✓' if ok else '✗'} {msg}")
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
            self.after(0, self._refresh_profiles)

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
                self.after(0, self._refresh_update_badge)
            except Exception as e:
                self._log(f"Ошибка: {e}")

        threading.Thread(target=_w, daemon=True).start()

    def _tool_purge(self) -> None:
        if not messagebox.askyesno("Подтверждение", "Удалить устаревшие профили из архива?"):
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

    def _update_and_restart(self) -> None:
        if self._proc and self._proc.poll() is None:
            messagebox.showwarning("Занято", "Сначала остановите запущенную автоматизацию.")
            return
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
                "⏳ Обновление…", self._update_and_restart, BTN_SECONDARY, state="disabled",
            )

        def _w():
            import menu as m
            try:
                m._check_updates_bg()
                ok, msg = m._do_git_update()
                self._log(f"{'✓' if ok else '✗'} {msg}")
                if not ok:
                    self.after(0, lambda: messagebox.showerror("Ошибка обновления", msg))
                    self.after(0, self._enable_upd_btn)
                    return
                self._log("📦 Установка зависимостей…")
                m.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
                m.run([sys.executable, "-m", "playwright", "install", "chromium"])
                self._log("♻ Перезапуск через 2 сек…")
                self.after(2000, self._restart_app)
            except Exception as e:
                self._log(f"Ошибка: {e}")
                self.after(0, lambda: messagebox.showerror("Ошибка", str(e)))
                self.after(0, self._enable_upd_btn)

        threading.Thread(target=_w, daemon=True).start()

    def _enable_upd_btn(self) -> None:
        self._update_in_progress = False
        self._refresh_update_badge()

    def _restart_for_update(self) -> None:
        if self._proc and self._proc.poll() is None:
            messagebox.showwarning("Занято", "Сначала остановите автоматизацию.")
            return
        if messagebox.askyesno(
            "Применение обновления",
            "Файлы уже обновлены на диске.\nПерезапустить SubHub для применения?",
        ):
            self._restart_app()

    def _restart_only(self) -> None:
        if self._proc and self._proc.poll() is None:
            messagebox.showwarning("Занято", "Сначала остановите автоматизацию.")
            return
        if messagebox.askyesno("Перезапуск", "Перезапустить приложение?"):
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

    def _create_desktop_shortcut(self) -> bool:
        launcher = _launcher_path()
        work = str(_HERE.resolve())
        ico = _app_icon_path()
        icon_ps = ""
        if ico:
            icon_ps = f"$s.IconLocation = '{ico.resolve()},0'; "
        if launcher.suffix.lower() == ".vbs":
            ps = (
                "$d = [Environment]::GetFolderPath('Desktop'); "
                "$w = New-Object -ComObject WScript.Shell; "
                f"$s = $w.CreateShortcut((Join-Path $d '{APP_NAME}.lnk')); "
                "$s.TargetPath = 'wscript.exe'; "
                f"$s.Arguments = '//nologo \"{launcher.resolve()}\"'; "
                f"$s.WorkingDirectory = '{work}'; "
                f"$s.Description = '{APP_NAME}'; "
                f"{icon_ps}"
                "$s.Save()"
            )
        else:
            ps = (
                "$d = [Environment]::GetFolderPath('Desktop'); "
                "$w = New-Object -ComObject WScript.Shell; "
                f"$s = $w.CreateShortcut((Join-Path $d '{APP_NAME}.lnk')); "
                f"$s.TargetPath = '{launcher.resolve()}'; "
                f"$s.WorkingDirectory = '{work}'; "
                f"$s.Description = '{APP_NAME}'; "
                f"{icon_ps}"
                "$s.Save()"
            )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, cwd=str(_HERE),
        )
        return r.returncode == 0 and self._desktop_shortcut_path().exists()

    def _ensure_desktop_shortcut(self) -> None:
        """Создаёт или обновляет ярлык SubHub на рабочем столе (имя + иконка)."""
        if self._create_desktop_shortcut():
            self._log(f"✓ Ярлык «{APP_NAME}» на рабочем столе")

    def _create_desktop_shortcut_ui(self) -> None:
        if self._create_desktop_shortcut():
            self._log(f"✓ Ярлык: {self._desktop_shortcut_path()}")
            messagebox.showinfo("Готово", "Ярлык создан на рабочем столе.")
        else:
            messagebox.showerror("Ошибка", "Не удалось создать ярлык.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _install_deps(self) -> None:
        self._log("Установка в фоне…")

        def _w():
            import menu as m
            ok, msg = m.ensure_dependencies(log_fn=self._log)
            self._log(f"{'✓' if ok else '⚠'} {msg}")
            if ok:
                m.start_background_bootstrap(force=True)

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
        d = _HERE / "vpn_extension"
        d.mkdir(exist_ok=True)
        os.startfile(str(d))

    def _open_folder(self, name: str) -> None:
        d = _HERE / name
        d.mkdir(exist_ok=True)
        os.startfile(str(d))

    def _clear_logs(self) -> None:
        self.log_text.delete("1.0", "end")

    def _open_log_file(self) -> None:
        p = _HERE / "automation.log"
        if p.exists():
            os.startfile(str(p))

    def _on_close(self) -> None:
        if self._quitting:
            return
        bg = self._app_settings.get("background_mode", True)
        tray = self._app_settings.get("minimize_to_tray", True)
        if bg and tray:
            if self._minimize_to_tray():
                return
            if messagebox.askyesno(
                "Фоновый режим",
                "Не удалось свернуть в трей.\nВсё равно выйти из приложения?",
            ):
                self._quit_app()
            return
        if bg and not tray:
            if messagebox.askyesno(
                "Фоновый режим",
                "Приложение продолжит работать в фоне (без окна).\n"
                "Telegram-бот и мониторинг останутся активны.\n\nСвернуть окно?",
            ):
                self.withdraw()
                self._hidden_to_tray = True
                return
        if messagebox.askyesno("Выход", "Закрыть приложение?"):
            self._quit_app()

    def _shutdown_services(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._stop_run()
        try:
            from ggsell.monitor import stop_monitor
            stop_monitor()
        except Exception:
            pass
        try:
            import grizzly as gz
            gz.cleanup_all_rentals_on_exit()
        except Exception:
            pass
        try:
            import menu as m
            m.clear_automation_proc()
            m._patch_runtime_state(tg_bot_pid=0, tg_bot_owner="")
        except Exception:
            pass

    def _quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._shutdown_services()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self.destroy()

    def _restart_app(self) -> None:
        self._shutdown_services()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        if "--console" in sys.argv:
            self.destroy()
            os._exit(42)
        self._restart_requested = True
        self.destroy()

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
        except ImportError:
            return False
        if self._tray_icon is not None:
            return True

        def _show(_icon=None, _item=None) -> None:
            self.after(0, self._show_from_tray)

        def _restart(_icon=None, _item=None) -> None:
            self.after(0, self._restart_app)

        def _quit(_icon=None, _item=None) -> None:
            self.after(0, self._quit_app)

        menu = pystray.Menu(
            pystray.MenuItem("Открыть", _show, default=True),
            pystray.MenuItem("Перезапустить", _restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", _quit),
        )
        self._tray_icon = pystray.Icon(
            "subhub", self._tray_image(), APP_NAME, menu,
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
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _minimize_to_tray(self) -> bool:
        if not self._ensure_tray():
            self._log("⚠ Для трея: pip install pystray Pillow")
            return False
        if self._hidden_to_tray:
            return True
        self._hidden_to_tray = True
        self.withdraw()
        self._log("Свёрнуто в трей")
        return True


def main() -> None:
    _hide_console_window()
    _set_windows_app_id()
    while True:
        app = SubHubApp()
        if not (_HERE / "secrets.yaml").exists():
            app._log("⚠ Заполните secrets.yaml (Настройки)")
        app.mainloop()
        if not getattr(app, "_restart_requested", False):
            break
        time.sleep(1.5)
    os._exit(0)


if __name__ == "__main__":
    main()
