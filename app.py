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
from tkinter import filedialog, messagebox

_HERE = Path(__file__).parent
os.chdir(_HERE)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)

# ── Design system (Pinterest-inspired dark) ───────────────────────────────────
FONT_UI = "Segoe UI"
FONT_MONO = 13
BG_MAIN = "#191919"
BG_SIDEBAR = "#191919"
BG_CARD = "#2a2a2a"
BG_CARD_HOVER = "#353535"
BG_ELEVATED = "#333333"
BG_SURFACE = "#242424"
BG_NAV_ACTIVE = "#3a3a3a"
TEXT_DIM = "#b3b3b3"
TEXT_MUTED = "#767676"
TEXT_PRIMARY = "#ffffff"
ACCENT = "#E60023"
ACCENT_HOVER = "#ad081b"
SUCCESS = "#0d7a3f"
SUCCESS_FG = "#1dad65"
WARNING = "#c48800"
ERROR = "#e60023"
BTN_SECONDARY = "#3a3a3a"
BTN_SECONDARY_HOVER = "#4a4a4a"
BTN_SUCCESS = "#0d7a3f"
BORDER_SUBTLE = "#3d3d3d"
RADIUS_CARD = 22
RADIUS_PIN = 22
RADIUS_BTN = 26
RADIUS_SM = 14
RADIUS_CHIP = 18
_PAD_PAGE = 24
_PAD_CARD = 18
_GAP_CARD = 14
BTN_H = 44
BTN_H_MD = 48
BTN_ICON = 48
SIDEBAR_W = 268
FONT_TITLE = 28
FONT_SECTION = 18
FONT_BODY = 15
FONT_CAPTION = 13
FONT_SMALL = 12
_SIDEBAR_LOG_LINES = 48
_MAIN_LOG_LINES = 800

SVC_YOUTUBE = "#ff0033"
SVC_GGSELL = "#ff6a00"
SVC_DEEPSEEK = "#6b8cff"
SVC_KLING = "#00c9a7"


def _ui_font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_UI, size=size, weight=weight)

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
        "subtitle": "Пополнение API-баланса",
        "accent": SVC_DEEPSEEK,
        "icon": "🧠",
        "ready": True,
    },
    "kling": {
        "title": "Kling AI",
        "subtitle": "Скоро",
        "accent": SVC_KLING,
        "icon": "🎬",
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
        self.geometry("1380x860")
        self.minsize(1120, 720)
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
        self._ggs_chat_loading = False
        self._ggs_templates_win = None
        self._profile_filter = "all"
        self._prof_busy: set[str] = set()
        self._profiles_sig: tuple | None = None
        self._profiles_filter_sig: str | None = None
        self._refresh_jobs: dict[str, str | None] = {}
        self._run_preset_key = "full"
        self._run_log_active = False
        self._vpn_last_check = ""
        self._notif_cards: list[ctk.CTkFrame] = []
        self._notif_unread = 0

        _cleanup_legacy_branding()
        self._build_layout()
        self._build_notification_layer()
        self._apply_window_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show_page("home")
        self.status_lbl.configure(text="⏳ Инициализация…")
        self._log("⏳ Запуск SubHub…")
        if (
            self._app_settings.get("start_minimized")
            and self._app_settings.get("background_mode")
        ):
            self.withdraw()
        else:
            self._ensure_window_visible()
        self.after(1500, self._maybe_show_loading)
        # Логи и статус — сразу, не дожидаясь preflight (иначе при зависшей
        # проверке зависимостей/обновлений интерфейс вечно «Инициализация…»)
        self._start_ticks()
        threading.Thread(target=self._startup_preflight, daemon=True, name="preflight").start()
        # Страховка: если preflight завис (сеть, pip, git) — запускаем
        # бэкенд (TG-бот, GGSell-монитор) принудительно через 45 секунд
        self.after(45000, self._finish_startup)

    def _maybe_show_loading(self) -> None:
        if self._startup_done:
            return
        if self._hidden_to_tray:
            return
        if not self.winfo_viewable():
            self._ensure_window_visible()
            self._log("⏳ Инициализация, подождите…")

    def _ensure_window_visible(self) -> None:
        """Показать главное окно (не трогать режим «в трее»)."""
        if self._hidden_to_tray:
            return
        if self._app_settings.get("start_minimized") and not self._startup_done:
            return
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

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

    def _start_ticks(self) -> None:
        if getattr(self, "_ticks_started", False):
            return
        self._ticks_started = True
        self._tick_logs()
        self._tick_status()

    def _finish_startup(self) -> None:
        if self._startup_done:
            return
        self._startup_done = True
        try:
            import menu as m
            m.register_host_restart(lambda: self.after(0, self._restart_app))
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
        self.after(600, self._poll_ggs_notify)
        self.after(2000, self._guard_window_visible)

    def _guard_window_visible(self) -> None:
        """Если окно пропало без явного «в трей» — вернуть на экран."""
        if self._quitting or self._hidden_to_tray:
            pass
        elif not self._app_settings.get("start_minimized") or self._startup_done:
            try:
                if not self.winfo_viewable():
                    self.deiconify()
                    self.lift()
            except Exception:
                pass
        self.after(3000, self._guard_window_visible)

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

        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_W, corner_radius=0, fg_color=BG_SIDEBAR)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", pady=(0, 12), padx=12)

        foot_nav = ctk.CTkFrame(bottom, fg_color="transparent")
        foot_nav.pack(fill="x", pady=(0, 6))
        foot_nav.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            foot_nav, text="Логи", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"), fg_color=BG_SURFACE,
            hover_color=BG_NAV_ACTIVE,
            command=lambda: self.show_page("logs"),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            foot_nav, text="Настройки", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"), fg_color=BG_SURFACE,
            hover_color=BG_NAV_ACTIVE,
            command=lambda: self.show_page("settings"),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        self.status_lbl = ctk.CTkLabel(
            bottom, text="", font=_ui_font(FONT_BODY), text_color=TEXT_MUTED, wraplength=220,
        )
        self.status_lbl.pack(anchor="w")
        self._bg_status_lbl = None

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=14, pady=(16, 10))
        brand_row = ctk.CTkFrame(brand, fg_color="transparent")
        brand_row.pack(fill="x")
        logo = ctk.CTkFrame(brand_row, width=44, height=44, corner_radius=22, fg_color=ACCENT)
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="S", font=_ui_font(22, "bold"), text_color=TEXT_PRIMARY).place(
            relx=0.5, rely=0.5, anchor="center",
        )
        brand_txt = ctk.CTkFrame(brand_row, fg_color="transparent")
        brand_txt.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            brand_txt, text="SubHub", font=_ui_font(22, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand_txt, text="Подписки · маркетплейс", font=_ui_font(FONT_CAPTION),
            text_color=TEXT_MUTED,
        ).pack(anchor="w")

        self._vpn_status_lbl = ctk.CTkLabel(
            self.sidebar, text="🔒 VPN…", font=_ui_font(FONT_CAPTION),
            text_color=TEXT_DIM, wraplength=220,
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, padx=12, pady=6,
        )
        self._vpn_status_lbl.pack(fill="x", padx=12, pady=(0, 8))

        self.nav_container = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_container.pack(fill="x", padx=8, pady=4)

        self.sidebar_log_wrap = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_log_wrap.pack(fill="both", expand=True, padx=12, pady=(4, 6))

        self.sidebar_log = ctk.CTkTextbox(
            self.sidebar_log_wrap,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_SURFACE, corner_radius=RADIUS_SM,
            border_width=0,
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
            items = [
                ("home", "🏠  Главная"),
                ("cards", "💳  Карты"),
            ]
        elif service == "youtube":
            items = [
                ("__home__", "←  Главная"),
                ("youtube_hub", "📊  Обзор"),
                ("run", "🚀  Запуск"),
                ("profiles", "👤  Профили"),
                ("archive", "📦  Архив"),
                ("vpn", "🔒  VPN"),
                ("tools", "🛠  Инструменты"),
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
                and self._current_page in ("run", "profiles", "archive", "vpn", "tools")
            )
            btn = ctk.CTkButton(
                self.nav_container, text=label, anchor="w", height=48,
                corner_radius=RADIUS_CHIP,
                fg_color=BG_NAV_ACTIVE if is_active else "transparent",
                hover_color=BG_CARD_HOVER,
                text_color=TEXT_PRIMARY if is_active else TEXT_DIM,
                font=_ui_font(FONT_BODY, "bold" if is_active else "normal"),
                command=lambda k=key: self._nav_click(k),
            )
            btn.pack(fill="x", padx=4, pady=2)
            self._nav_btns[key] = btn

    def _nav_click(self, key: str) -> None:
        if key == "__home__":
            self._go_home()
            return
        if key in ("run", "profiles", "archive", "vpn", "tools"):
            self._current_service = "youtube"
        elif key == "cards":
            self._current_service = None
            self._render_sidebar(None)
        self.show_page(key)

    def _open_cards_page(self) -> None:
        self._current_service = None
        self._render_sidebar(None)
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
        ctk.CTkLabel(
            hdr, text=title, font=_ui_font(FONT_TITLE, "bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                hdr, text=subtitle, font=_ui_font(FONT_BODY), text_color=TEXT_MUTED,
            ).pack(anchor="w", pady=(2, 0))

    def _section_title(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text, font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w", pady=(4, 8))

    def _service_tile(self, parent, col: int, service: str) -> ctk.CTkFrame:
        meta = SERVICE_META[service]
        accent = meta["accent"]
        ready = meta.get("ready", True)
        card = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_PIN,
            border_width=0,
        )
        card.grid(row=0, column=col, sticky="nsew", padx=6, pady=6)

        cover = ctk.CTkFrame(card, fg_color=accent, height=108, corner_radius=RADIUS_SM)
        cover.pack(fill="x", padx=8, pady=(8, 0))
        cover.pack_propagate(False)
        ctk.CTkLabel(
            cover, text=meta["icon"], font=_ui_font(44),
        ).pack(expand=True)

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=14)

        title_row = ctk.CTkFrame(body, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(
            title_row, text=meta["title"], font=_ui_font(17, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left")
        if not ready:
            ctk.CTkLabel(
                title_row, text="Скоро", font=_ui_font(10, "bold"),
                fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, text_color=TEXT_MUTED,
                padx=10, pady=3,
            ).pack(side="right")

        ctk.CTkLabel(
            body, text=meta["subtitle"], font=_ui_font(FONT_CAPTION),
            text_color=TEXT_MUTED, anchor="w", justify="left", wraplength=240,
        ).pack(fill="x", pady=(4, 12))

        btn_txt = "Открыть" if ready else "Скоро"
        btn = ctk.CTkButton(
            body, text=btn_txt, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=accent if ready else BG_SURFACE,
            hover_color=self._btn_hover(accent) if ready else BG_SURFACE,
            text_color=TEXT_PRIMARY,
            state="normal" if ready else "disabled",
            command=lambda s=service: self._enter_service(s),
        )
        btn.pack(fill="x")

        if ready:
            def _hover_in(_e=None):
                card.configure(fg_color=BG_CARD_HOVER)
            def _hover_out(_e=None):
                card.configure(fg_color=BG_CARD)
            for w in (card, cover, body, title_row):
                w.bind("<Enter>", _hover_in)
                w.bind("<Leave>", _hover_out)
                w.bind("<Button-1>", lambda e, s=service: self._enter_service(s))
        return card

    def _ggsell_home_banner(self, parent) -> ctk.CTkFrame:
        meta = SERVICE_META["ggsell"]
        accent = meta["accent"]
        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=RADIUS_PIN, border_width=0)
        card.pack(fill="x", pady=(0, _GAP_CARD))

        hero = ctk.CTkFrame(card, fg_color=accent, height=128, corner_radius=RADIUS_SM)
        hero.pack(fill="x", padx=8, pady=(8, 0))
        hero.pack_propagate(False)
        hero_inner = ctk.CTkFrame(hero, fg_color="transparent")
        hero_inner.pack(fill="both", expand=True, padx=16, pady=12)
        hero_inner.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hero_inner, text=meta["icon"], font=_ui_font(48)).grid(row=0, column=0, sticky="w")
        hero_txt = ctk.CTkFrame(hero_inner, fg_color="transparent")
        hero_txt.grid(row=0, column=1, sticky="w", padx=(12, 0))
        ctk.CTkLabel(
            hero_txt, text="Маркетплейс", font=_ui_font(10, "bold"), text_color="#ffe8d6",
        ).pack(anchor="w")
        ctk.CTkLabel(
            hero_txt, text=meta["title"], font=_ui_font(26, "bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            hero_txt, text=meta["subtitle"], font=_ui_font(FONT_CAPTION), text_color="#ffe8d6",
        ).pack(anchor="w")
        ctk.CTkButton(
            hero_inner, text="Открыть", width=130, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=TEXT_PRIMARY, hover_color="#f0f0f0", text_color=accent,
            command=lambda: self._enter_service("ggsell"),
        ).grid(row=0, column=2, sticky="e")

        stats_row = ctk.CTkFrame(card, fg_color="transparent")
        stats_row.pack(fill="x", padx=8, pady=10)
        for i in range(4):
            stats_row.grid_columnconfigure(i, weight=1)
        self.home_ggs_orders = self._stat_box(
            stats_row, 0, "Выдано заказов", "—", accent, sub="сегодня: —")
        self.home_ggs_monitor = self._stat_box(
            stats_row, 1, "Монитор", "—", accent, sub="следит за заказами")
        self.home_ggs_balance = self._stat_box(
            stats_row, 2, "Баланс GGSell", "—", accent, sub="на счёте продавца")
        self.home_ggs_refunds = self._stat_box(
            stats_row, 3, "Возвраты", "—", accent, sub="за всё время")

        def _hover_in(_e=None):
            card.configure(fg_color=BG_CARD_HOVER)
        def _hover_out(_e=None):
            card.configure(fg_color=BG_CARD)
        for w in (card, hero, stats_row):
            w.bind("<Enter>", _hover_in)
            w.bind("<Leave>", _hover_out)
            w.bind("<Button-1>", lambda e: self._enter_service("ggsell"))
        return card

    def _page(self, name: str) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        self._pages[name] = frame
        return frame

    def show_page(self, name: str) -> None:
        if name not in self._pages:
            self._log(f"Неизвестная страница: {name}")
            return
        youtube_pages = {"run", "profiles", "archive", "vpn", "tools", "youtube_hub"}
        home_pages = {"home", "cards"}
        if name in home_pages:
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

        for f in self._pages.values():
            f.grid_forget()
        self._pages[name].grid(row=0, column=0, sticky="nsew")
        self._current_page = name

        for k, btn in self._nav_btns.items():
            active = k == name or (
                k == "youtube_hub"
                and name in ("run", "profiles", "archive", "vpn", "tools", "youtube_hub")
            )
            btn.configure(
                fg_color=BG_NAV_ACTIVE if active else "transparent",
                text_color=TEXT_PRIMARY if active else TEXT_DIM,
                font=_ui_font(FONT_BODY, "bold" if active else "normal"),
            )

        refresh = {
            "home": lambda: (self._refresh_home_ggsell(), self._refresh_update_badge()),
            "youtube_hub": lambda: (self._refresh_youtube_hub(), self._refresh_update_badge()),
            "ggsell": self._refresh_ggsell,
            "run": lambda: (self._refresh_run_page(), self._sync_run_page_status),
            "profiles": self._refresh_profiles,
            "cards": self._refresh_cards,
            "archive": self._refresh_archive,
            "vpn": self._refresh_vpn_page,
            "tools": lambda: None,
            "logs": lambda: None,
            "deepseek": self._refresh_deepseek,
            "settings": lambda: (
                self._refresh_settings_keys(), self._refresh_update_badge(), self._sync_settings_switches(),
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
            fg_color=ACCENT, corner_radius=RADIUS_CHIP, text_color=TEXT_PRIMARY,
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
        self.after(1200, self._poll_ggs_notify)

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
            if self._current_page != "ggsell":
                self.after(400, self._refresh_ggsell)
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

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=10)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=title, font=_ui_font(FONT_BODY, "bold"),
            text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            top, text="✕", width=28, height=28, corner_radius=14,
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
        self._ggs_filter = "all"
        for key, btn in getattr(self, "_ggs_filter_btns", {}).items():
            active = key == "all"
            btn.configure(
                fg_color=SVC_GGSELL if active else BG_SURFACE,
                hover_color=self._btn_hover(SVC_GGSELL) if active else BG_NAV_ACTIVE,
            )
        self._enter_service("ggsell")
        self.show_page("ggsell")
        self._notif_unread = 0
        self._update_notif_badge()

    def _card(self, parent, title: str, accent: str | None = None) -> ctk.CTkFrame:
        f = ctk.CTkFrame(
            parent, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=0,
        )
        f.pack(fill="x", pady=(0, _GAP_CARD))
        hdr = ctk.CTkFrame(f, fg_color="transparent")
        hdr.pack(fill="x", padx=_PAD_CARD, pady=(12, 4))
        if accent:
            dot = ctk.CTkFrame(hdr, width=8, height=8, corner_radius=4, fg_color=accent)
            dot.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            hdr, text=title, font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY,
        ).pack(side="left")
        inner = ctk.CTkFrame(f, fg_color="transparent")
        inner.pack(fill="x", padx=_PAD_CARD, pady=(0, 12))
        return inner

    def _chip_btn(self, parent, text: str, active: bool, accent: str, command) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=BTN_H, corner_radius=RADIUS_CHIP,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=accent if active else BG_SURFACE,
            hover_color=self._btn_hover(accent) if active else BG_NAV_ACTIVE,
            text_color=TEXT_PRIMARY,
            command=command,
        )

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
            SVC_YOUTUBE: "#cc0029",
            SVC_GGSELL: "#e65c00",
            SVC_DEEPSEEK: "#5578e8",
            SVC_KLING: "#00a88a",
            SUCCESS: SUCCESS_FG,
            BTN_SUCCESS: SUCCESS_FG,
            ERROR: ACCENT_HOVER,
            BTN_SECONDARY: BTN_SECONDARY_HOVER,
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
            ctk.CTkButton(
                grid, text=txt, height=BTN_H, corner_radius=RADIUS_BTN,
                font=_ui_font(FONT_BODY, "bold"),
                fg_color=color, hover_color=self._btn_hover(color), command=cmd,
            ).grid(row=r, column=c, sticky="ew", padx=2, pady=2)
        return grid

    def _list_panel(self, parent, height: int | None = 420) -> ctk.CTkScrollableFrame:
        kw: dict[str, Any] = {
            "fg_color": BG_SURFACE, "corner_radius": RADIUS_CARD,
            "border_width": 0,
        }
        if height is not None:
            kw["height"] = height
        return ctk.CTkScrollableFrame(parent, **kw)

    def _page_fill(self, name: str) -> ctk.CTkFrame:
        """Страница с растягиванием списка до низа окна."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        self._pages[name] = frame
        return frame

    def _action_btn(self, parent, text: str, cmd: Callable, color: str = ACCENT, **kw) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=color, hover_color=self._btn_hover(color), command=cmd, **kw,
        )

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _build_home(self) -> None:
        p = self._page("home")
        self._page_header(p, "Главная", "Маркетплейс и автоматизация подписок", ACCENT)
        self._ggsell_home_banner(p)
        self._section_title(p, "Подписки")

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
        stats.pack(fill="x", pady=(0, _GAP_CARD))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.dash_profiles = self._stat_box(stats, 0, "Профили", "—", SVC_YOUTUBE)
        self.dash_cards = self._stat_box(stats, 1, "Карты", "—", SVC_YOUTUBE)
        self.dash_gift = self._stat_box(stats, 2, "Гифт-карты", "—", SVC_YOUTUBE)
        self.dash_tg = self._stat_box(stats, 3, "Telegram", "—", SVC_YOUTUBE)

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, _GAP_CARD))
        self.dash_vpn_chip = ctk.CTkLabel(
            row, text="VPN…", font=_ui_font(FONT_CAPTION, "bold"),
            fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP, padx=12, pady=6,
            text_color=TEXT_DIM,
        )
        self.dash_vpn_chip.pack(side="left")
        self.dash_balance = ctk.CTkLabel(
            row, text="", font=_ui_font(FONT_BODY), text_color=TEXT_DIM,
        )
        self.dash_balance.pack(side="left", padx=(12, 0))

        quick = self._card(p, "Быстрый доступ", accent=SVC_YOUTUBE)
        self._action_grid(quick, [
            ("🚀  Запуск", lambda: self.show_page("run"), SVC_YOUTUBE),
            ("👤  Профили", lambda: self.show_page("profiles"), BTN_SECONDARY),
            ("💳  Карты", self._open_cards_page, BTN_SECONDARY),
            ("🔒  VPN", lambda: self.show_page("vpn"), BTN_SECONDARY),
        ])

        tools_card = self._card(p, "Сервис", accent=SVC_YOUTUBE)
        self._action_grid(tools_card, [
            ("▶  Полный цикл", lambda: (self.show_page("run"), self._preset_run("full")), BTN_SUCCESS),
            ("🛠  Инструменты", lambda: self.show_page("tools"), BTN_SECONDARY),
        ])

    def _build_ggsell(self) -> None:
        p = self._page_fill("ggsell")
        p.grid_rowconfigure(3, weight=1)

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(
            hdr, "GGSELL",
            "Заказы · мониторинг · доставка ссылок",
            SVC_GGSELL,
        )

        stats = ctk.CTkFrame(p, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, _GAP_CARD))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.ggs_stat_orders = self._stat_box(
            stats, 0, "Выдано заказов", "—", SVC_GGSELL, sub="за всё время")
        self.ggs_stat_balance = self._stat_box(
            stats, 1, "Баланс", "—", SVC_GGSELL, sub="на счёте GGSell")
        self.ggs_stat_monitor = self._stat_box(
            stats, 2, "Монитор", "—", SVC_GGSELL, sub="следит за заказами")
        self.ggs_stat_api = self._stat_box(
            stats, 3, "API", "—", SVC_GGSELL, sub="ключи продавца")

        actions_container = ctk.CTkFrame(p, fg_color="transparent")
        actions_container.grid(row=2, column=0, sticky="ew", pady=(0, _GAP_CARD))
        actions = self._card(actions_container, "Управление", accent=SVC_GGSELL)
        actions.pack(fill="x")
        self._action_grid(actions, [
            ("🔄  Обновить", self._refresh_ggsell, SVC_GGSELL),
            ("🔑  API-ключи", self._open_secrets, BTN_SECONDARY),
            ("📁  data/", lambda: self._open_folder("data"), BTN_SECONDARY),
            ("📋  Шаблоны", self._open_ggsell_templates, BTN_SECONDARY),
        ])

        orders_wrap = ctk.CTkFrame(p, fg_color="transparent")
        orders_wrap.grid(row=3, column=0, sticky="nsew")
        orders_wrap.grid_rowconfigure(0, weight=1)
        orders_wrap.grid_columnconfigure(0, weight=1)

        orders_outer = ctk.CTkFrame(
            orders_wrap, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
        orders_outer.grid(row=0, column=0, sticky="nsew")
        orders_outer.grid_rowconfigure(3, weight=1)
        orders_outer.grid_columnconfigure(0, weight=1)

        orders_hdr = ctk.CTkFrame(orders_outer, fg_color="transparent")
        orders_hdr.grid(row=0, column=0, sticky="ew", padx=_PAD_CARD, pady=(12, 4))
        dot = ctk.CTkFrame(orders_hdr, width=8, height=8, corner_radius=4, fg_color=SVC_GGSELL)
        dot.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            orders_hdr, text="Заказы", font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY,
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
                filt_row, label, key == "new", SVC_GGSELL,
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

        self.ggs_orders_list = ctk.CTkScrollableFrame(
            body, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
        )
        self.ggs_orders_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.ggs_order_detail = ctk.CTkFrame(
            body, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=0,
        )
        self.ggs_order_detail.grid(row=0, column=1, sticky="nsew")
        self.ggs_order_detail.grid_columnconfigure(0, weight=1)
        self.ggs_order_detail.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(self.ggs_order_detail, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        hdr.grid_columnconfigure(0, weight=1)
        self.ggs_detail_title = ctk.CTkLabel(
            hdr, text="Выберите заказ",
            font=_ui_font(FONT_SECTION, "bold"), text_color=TEXT_PRIMARY, anchor="w",
        )
        self.ggs_detail_title.grid(row=0, column=0, sticky="w")
        self.ggs_btn_chat_refresh = ctk.CTkButton(
            hdr, text="🔄  Чат", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            state="disabled", command=self._ggsell_refresh_chat,
        )
        self.ggs_btn_chat_refresh.grid(row=0, column=1, padx=(8, 0))
        self.ggs_btn_seller = ctk.CTkButton(
            hdr, text="🌐  GGSell", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            state="disabled", command=self._ggsell_open_seller,
        )
        self.ggs_btn_seller.grid(row=0, column=2, padx=(6, 0))

        self.ggs_detail_meta = ctk.CTkLabel(
            self.ggs_order_detail, text="—",
            justify="left", anchor="nw", text_color=TEXT_DIM,
            font=_ui_font(FONT_BODY), wraplength=440,
        )
        self.ggs_detail_meta.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        self.ggs_chat_scroll = ctk.CTkScrollableFrame(
            self.ggs_order_detail, fg_color=BG_SURFACE, corner_radius=RADIUS_SM,
            border_width=0,
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
        self.ggs_btn_templates = ctk.CTkButton(
            chat_input, text="📝  Шаблоны", height=BTN_H_MD, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            state="disabled", command=self._ggsell_open_templates_modal,
        )
        self.ggs_btn_templates.grid(row=0, column=0, padx=(0, 6))
        self.ggs_chat_input = ctk.CTkTextbox(
            chat_input, height=BTN_H_MD, corner_radius=RADIUS_BTN,
            fg_color=BG_CARD, border_width=1, border_color=BORDER_SUBTLE,
            font=_ui_font(FONT_BODY), wrap="word",
        )
        self.ggs_chat_input.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.ggs_chat_input.configure(state="disabled")
        self.ggs_chat_input.bind("<Return>", self._ggsell_chat_return)
        self.ggs_chat_input.bind("<Shift-Return>", lambda _e: None)
        self.ggs_btn_chat_send = ctk.CTkButton(
            chat_input, text="➤  Отправить", height=BTN_H_MD, corner_radius=RADIUS_BTN,
            fg_color=SVC_GGSELL, hover_color="#e67e00",
            state="disabled", command=self._ggsell_send_chat,
        )
        self.ggs_btn_chat_send.grid(row=0, column=2)
        self._ggs_templates_win = None

    def _build_deepseek(self) -> None:
        p = self._page("deepseek")
        self._page_header(
            p, "DeepSeek", "Пополнение API-баланса банковской картой", SVC_DEEPSEEK,
        )

        form = self._card(p, "Пополнение", accent=SVC_DEEPSEEK)
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x")
        inner.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(inner, text="Вход", text_color=TEXT_DIM, font=_ui_font(FONT_BODY)).grid(row=0, column=0, sticky="w", pady=6)
        self.ds_login_method = ctk.CTkSegmentedButton(
            inner, values=["Почта и пароль", "Google"], height=BTN_H,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=BG_SURFACE,
            selected_color=SVC_DEEPSEEK, selected_hover_color="#5578e8",
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

        self.ds_btn = self._action_btn(form, "🧠  Пополнить", self._ds_topup_clicked, SVC_DEEPSEEK)
        self.ds_btn.pack(fill="x", pady=(10, 0))

        log_card = self._card(p, "Ход выполнения", accent=SVC_DEEPSEEK)
        self.ds_log = ctk.CTkTextbox(
            log_card, height=240,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_ELEVATED, corner_radius=RADIUS_SM, wrap="word",
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
            + (" (вход через Google)" if method == "google" else ""))

        def _w():
            try:
                import deepseek as ds
                ok, msg = asyncio.run(ds.topup(
                    email, password, amount, card,
                    login_method=method,
                    log=lambda s: self.after(0, self._ds_log_line, str(s)),
                ))
            except Exception as e:
                ok, msg = False, f"Ошибка: {e}"
            self.after(0, self._ds_topup_done, ok, msg)

        threading.Thread(target=_w, daemon=True, name="ds-topup").start()

    def _ds_topup_done(self, ok: bool, msg: str) -> None:
        self._ds_running = False
        self.ds_btn.configure(state="normal", text="🧠  Пополнить")
        self._ds_log_line(msg)
        self._log(f"DeepSeek: {msg}")
        if ok:
            messagebox.showinfo("DeepSeek", msg)
        else:
            messagebox.showerror("DeepSeek", msg)

    def _build_kling(self) -> None:
        p = self._page("kling")
        self._build_coming_soon(p, "kling")

    def _build_coming_soon(self, p: ctk.CTkScrollableFrame, service: str) -> None:
        meta = SERVICE_META[service]
        self._page_header(p, meta["title"], meta["subtitle"], meta["accent"])
        box = ctk.CTkFrame(
            p, fg_color=BG_CARD, corner_radius=RADIUS_PIN,
            border_width=0,
        )
        box.pack(fill="x", pady=8)
        cover = ctk.CTkFrame(box, fg_color=meta["accent"], height=148, corner_radius=RADIUS_SM)
        cover.pack(fill="x", padx=8, pady=(8, 0))
        cover.pack_propagate(False)
        ctk.CTkLabel(cover, text=meta["icon"], font=_ui_font(56)).pack(expand=True)
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=14)
        ctk.CTkLabel(
            inner, text="Скоро", font=_ui_font(26, "bold"), text_color=meta["accent"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            inner, text=f"{meta['title']} появится в следующих обновлениях",
            font=_ui_font(FONT_BODY), text_color=TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 0))
        ctk.CTkButton(
            p, text="На главную", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=BG_SURFACE, hover_color=BG_NAV_ACTIVE, command=self._go_home,
        ).pack(pady=8)

    def _stat_box(self, parent, col: int, title: str, value: str,
                  accent: str = ACCENT, sub: str = "") -> ctk.CTkLabel:
        box = ctk.CTkFrame(
            parent, fg_color=BG_SURFACE, corner_radius=RADIUS_SM,
            border_width=0,
        )
        box.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkLabel(box, text=title, text_color=TEXT_MUTED, font=_ui_font(FONT_SMALL)).pack(pady=(12, 0))
        lbl = ctk.CTkLabel(
            box, text=value, font=_ui_font(22, "bold"), text_color=TEXT_PRIMARY,
        )
        lbl.pack(pady=(2, 2 if sub else 12))
        if sub:
            sub_lbl = ctk.CTkLabel(
                box, text=sub, text_color=TEXT_DIM, font=_ui_font(FONT_SMALL),
            )
            sub_lbl.pack(pady=(0, 10))
            lbl.sub_label = sub_lbl  # для обновления подписи из refresh
        return lbl

    def _build_run(self) -> None:
        p = self._page_fill("run")
        p.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(
            hdr, "Запуск",
            "Тот же сценарий, что menu.py в консоли — вход GrizzlySMS и покупка BLACK",
            SVC_YOUTUBE,
        )

        stats = ctk.CTkFrame(p, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, _GAP_CARD))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.run_stat_profiles = self._stat_box(stats, 0, "Профили", "—", SVC_YOUTUBE)
        self.run_stat_vpn = self._stat_box(stats, 1, "VPN", "—", SVC_YOUTUBE)
        self.run_stat_balance = self._stat_box(stats, 2, "GrizzlySMS", "—", SVC_YOUTUBE)
        self.run_stat_state = self._stat_box(
            stats, 3, "Статус", "Готов", SVC_YOUTUBE, sub="ожидание",
        )

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=0, minsize=400)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        presets_inner = self._card(left, "Быстрый режим", accent=SVC_YOUTUBE)
        preset_row = ctk.CTkFrame(presets_inner, fg_color="transparent")
        preset_row.pack(fill="x")
        self._run_preset_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("full", "Полный цикл"),
            ("payment", "До оплаты"),
            ("login_pc", "Вход ПК"),
            ("tg_intercept", "Telegram"),
            ("email", "До email"),
        ):
            btn = self._chip_btn(
                preset_row, label, key == "full", SVC_YOUTUBE,
                lambda k=key: self._select_run_preset(k),
            )
            btn.pack(side="left", padx=(0, 6), pady=(0, 4))
            self._run_preset_btns[key] = btn

        params_inner = self._card(left, "Параметры", accent=SVC_YOUTUBE)
        form = ctk.CTkFrame(params_inner, fg_color="transparent")
        form.pack(fill="x")
        form.grid_columnconfigure(0, minsize=96)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            form, text="Режим", text_color=TEXT_DIM, font=_ui_font(FONT_BODY),
        ).grid(row=0, column=0, sticky="w", pady=5)
        self.run_mode = ctk.CTkComboBox(
            form, height=BTN_H, font=_ui_font(FONT_BODY), state="readonly",
            values=[
                "Полный цикл (вход + покупка)",
                "До оплаты (существующий профиль)",
                "Только вход на ПК",
                "Вход + Telegram (перехват)",
                "Вход с данными (до email)",
            ],
            command=lambda _v: self._on_run_param_change(),
        )
        self.run_mode.grid(row=0, column=1, sticky="ew", pady=5, padx=(10, 0))
        self.run_mode.set("Полный цикл (вход + покупка)")

        ctk.CTkLabel(
            form, text="Аккаунтов", text_color=TEXT_DIM, font=_ui_font(FONT_BODY),
        ).grid(row=1, column=0, sticky="w", pady=5)
        self.run_accounts = ctk.CTkEntry(
            form, height=BTN_H, font=_ui_font(FONT_BODY),
            placeholder_text="из config.yaml (auto_accounts)",
        )
        self.run_accounts.grid(row=1, column=1, sticky="ew", pady=5, padx=(10, 0))
        self.run_accounts.bind("<KeyRelease>", lambda _e: self._on_run_param_change())

        ctk.CTkLabel(
            form, text="Тариф", text_color=TEXT_DIM, font=_ui_font(FONT_BODY),
        ).grid(row=2, column=0, sticky="w", pady=5)
        self.run_tariff = ctk.CTkComboBox(
            form, height=BTN_H, font=_ui_font(FONT_BODY), state="readonly",
            values=["3 месяца (₹343)", "12 месяцев (₹1,499)"],
            command=lambda _v: self._on_run_param_change(),
        )
        self.run_tariff.grid(row=2, column=1, sticky="ew", pady=5, padx=(10, 0))
        self.run_tariff.set("3 месяца (₹343)")

        self.run_headless = ctk.CTkCheckBox(
            form, text="Фоновый браузер (headless)", font=_ui_font(FONT_BODY),
            command=self._on_run_param_change,
        )
        self.run_headless.grid(row=3, column=1, sticky="w", pady=(8, 2), padx=(10, 0))

        cmd_box = ctk.CTkFrame(params_inner, fg_color=BG_ELEVATED, corner_radius=RADIUS_SM)
        cmd_box.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(
            cmd_box, text="Команда", text_color=TEXT_MUTED, font=_ui_font(FONT_SMALL),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 2))
        self.run_cmd_preview = ctk.CTkLabel(
            cmd_box, text="", justify="left", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO - 1),
            text_color=TEXT_DIM, wraplength=360,
        )
        self.run_cmd_preview.pack(fill="x", padx=10, pady=(0, 10))

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        console_hdr = ctk.CTkFrame(right, fg_color="transparent")
        console_hdr.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(
            console_hdr, text="Консоль запуска", font=_ui_font(FONT_SECTION, "bold"),
            text_color=TEXT_PRIMARY,
        ).pack(side="left")
        ctk.CTkButton(
            console_hdr, text="Очистить", width=90, height=32, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_SMALL), fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER, command=self._clear_run_log,
        ).pack(side="right")
        ctk.CTkButton(
            console_hdr, text="Все логи →", width=100, height=32, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_SMALL), fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER, command=lambda: self.show_page("logs"),
        ).pack(side="right", padx=(0, 6))

        console_card = ctk.CTkFrame(
            right, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
        console_card.grid(row=1, column=0, sticky="nsew")
        console_card.grid_rowconfigure(0, weight=1)
        console_card.grid_columnconfigure(0, weight=1)
        self.run_log_text = ctk.CTkTextbox(
            console_card,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_ELEVATED, corner_radius=RADIUS_SM,
            wrap="word", activate_scrollbars=True,
        )
        self.run_log_text.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.run_log_text.configure(state="disabled")
        self._append_run_log(
            "Готов к запуску.\n"
            "Нажмите «Запустить» — вывод menu.py / main.py появится здесь в реальном времени.\n",
        )

        foot = ctk.CTkFrame(p, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        foot.grid_columnconfigure(0, weight=1)

        status_row = ctk.CTkFrame(foot, fg_color="transparent")
        status_row.grid(row=0, column=0, sticky="ew")
        status_row.grid_columnconfigure(1, weight=1)

        self.run_status_dot = ctk.CTkFrame(
            status_row, width=10, height=10, corner_radius=5, fg_color=TEXT_DIM,
        )
        self.run_status_dot.grid(row=0, column=0, padx=(0, 8), pady=6)
        self.run_status = ctk.CTkLabel(
            status_row, text="Готов к запуску", text_color=TEXT_DIM,
            font=_ui_font(FONT_BODY, "bold"), anchor="w",
        )
        self.run_status.grid(row=0, column=1, sticky="w")

        self.run_progress = ctk.CTkProgressBar(
            status_row, height=6, corner_radius=3, mode="indeterminate",
            progress_color=SVC_YOUTUBE,
        )
        self.run_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.run_progress.grid_remove()

        btns = ctk.CTkFrame(foot, fg_color="transparent")
        btns.grid(row=1, column=0, sticky="ew")
        self.run_start_btn = self._action_btn(
            btns, "▶  Запустить", self._start_run, color=SUCCESS, width=160,
        )
        self.run_start_btn.pack(side="left", padx=(0, 8))
        self.run_stop_btn = ctk.CTkButton(
            btns, text="■  Остановить", width=150, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=ERROR, hover_color="#da3633", state="disabled", command=self._stop_run,
        )
        self.run_stop_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="🔒  VPN", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY), fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER, command=self._check_vpn,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="👤  Профили", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY), fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER, command=lambda: self.show_page("profiles"),
        ).pack(side="left")

        self._update_run_cmd_preview()

    def _build_profiles(self) -> None:
        p = self._page_fill("profiles")
        p.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(hdr, "Профили", "Активные Chrome-сессии Flipkart", SVC_YOUTUBE)

        toolbar = ctk.CTkFrame(p, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(
            toolbar, text="🔄  Обновить", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BG_SURFACE, hover_color=BG_NAV_ACTIVE,
            command=self._refresh_profiles,
        ).pack(side="left", padx=(0, 8))
        self._profile_filter_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("all", "Все"),
            ("noaddr", "Доступные"),
            ("hasaddr", "С данными"),
            ("paid", "Оплаченные"),
            ("active", "Выданные"),
        ):
            btn = self._chip_btn(
                toolbar, label, key == "all", SVC_YOUTUBE,
                lambda k=key: self._set_profile_filter(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._profile_filter_btns[key] = btn

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self.profile_list = ctk.CTkScrollableFrame(
            body, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
        )
        self.profile_list.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.profile_detail = ctk.CTkFrame(
            body, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
        self.profile_detail.grid(row=0, column=1, sticky="nsew")
        self.profile_detail.grid_rowconfigure(2, weight=1)
        self.profile_detail.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.profile_detail, text="Профиль",
            font=_ui_font(FONT_SECTION, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self.profile_detail_body = ctk.CTkTextbox(
            self.profile_detail,
            font=ctk.CTkFont(family="Consolas", size=FONT_MONO),
            fg_color=BG_ELEVATED, corner_radius=RADIUS_SM, height=140, wrap="word",
        )
        self.profile_detail_body.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.profile_detail_body.configure(state="disabled")

        self.profile_detail_actions = ctk.CTkScrollableFrame(
            self.profile_detail, fg_color="transparent",
        )
        self.profile_detail_actions.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 12))

    def _build_archive(self) -> None:
        p = self._page_fill("archive")
        p.grid_rowconfigure(3, weight=1)

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(hdr, "Архив", "Использованные профили · восстановление", SVC_YOUTUBE)

        stats = ctk.CTkFrame(p, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, _GAP_CARD))
        stats.grid_columnconfigure((0, 1, 2), weight=1)
        self.archive_stat_total = self._stat_box(stats, 0, "Записей", "—", SVC_YOUTUBE)
        self.archive_stat_cookies = self._stat_box(stats, 1, "С куками", "—", SUCCESS)
        self.archive_stat_restored = self._stat_box(stats, 2, "Уже живые", "—", WARNING)

        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkButton(
            bar, text="🔄  Обновить", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._refresh_archive,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            bar, text="📁 Папка архива", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=lambda: self._open_folder("chrome_profiles_used"),
        ).pack(side="right", padx=(4, 0))

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self.archive_list = ctk.CTkScrollableFrame(
            body, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
        )
        self.archive_list.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.archive_detail = ctk.CTkFrame(
            body, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
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
            fg_color=BG_ELEVATED, corner_radius=RADIUS_SM, height=160, wrap="word",
        )
        self.archive_detail_body.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.archive_detail_body.configure(state="disabled")

        self.archive_detail_actions = ctk.CTkScrollableFrame(
            self.archive_detail, fg_color="transparent",
        )
        self.archive_detail_actions.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 12))

    def _build_cards(self) -> None:
        p = self._page_fill("cards")
        p.grid_rowconfigure(3, weight=1)
        self._cards_tab = "bank"
        self._sel_bank_idx: int | None = None
        self._sel_gift_idx: int | None = None
        self._gift_add_visible = False

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(
            hdr, "Карты и оплата",
            "Банковские и подарочные карты · порядок · история",
            ACCENT,
        )

        stats = ctk.CTkFrame(p, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, _GAP_CARD))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)
        self.cards_stat_bank = self._stat_box(stats, 0, "Банковские", "—", ACCENT)
        self.cards_stat_gift = self._stat_box(stats, 1, "Гифт-карты", "—", SUCCESS)
        self.cards_stat_balance = self._stat_box(stats, 2, "Баланс гифт", "—", WARNING)
        self.cards_stat_pay = self._stat_box(stats, 3, "Оплата", "—", BTN_SECONDARY)

        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        tabs = ctk.CTkFrame(bar, fg_color="transparent")
        tabs.pack(side="left", fill="x", expand=True)
        self._cards_tab_btns: dict[str, ctk.CTkButton] = {}
        for key, label in (
            ("bank", "💳  Банковские"),
            ("gift", "🎁  Гифт-карты"),
            ("history", "📜  История"),
        ):
            btn = self._chip_btn(
                tabs, label, key == "bank", ACCENT,
                lambda k=key: self._set_cards_tab(k),
            )
            btn.pack(side="left", padx=(0, 6))
            self._cards_tab_btns[key] = btn

        self.cards_toolbar = ctk.CTkFrame(bar, fg_color="transparent")
        self.cards_toolbar.pack(side="right")

        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        self.cards_list_wrap = ctk.CTkFrame(body, fg_color="transparent")
        self.cards_list_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.cards_list_wrap.grid_rowconfigure(1, weight=1)
        self.cards_list_wrap.grid_columnconfigure(0, weight=1)

        self.gift_add_panel = ctk.CTkFrame(
            self.cards_list_wrap, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
        add_hdr = ctk.CTkFrame(self.gift_add_panel, fg_color="transparent")
        add_hdr.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            add_hdr, text="Добавить гифт-карты", font=_ui_font(FONT_SECTION, "bold"),
        ).pack(side="left")
        ctk.CTkButton(
            add_hdr, text="✕", width=36, height=28, fg_color="transparent",
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
            fg_color=BG_ELEVATED, corner_radius=RADIUS_SM,
        )
        self.gift_input.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        add_inner.grid_columnconfigure(1, weight=1)
        add_btns = ctk.CTkFrame(add_inner, fg_color="transparent")
        add_btns.grid(row=2, column=0, columnspan=2, sticky="ew")
        ctk.CTkButton(
            add_btns, text="✓ Добавить", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"), fg_color=SUCCESS,
            hover_color=SUCCESS_FG, command=self._add_gift_cards_manual,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            add_btns, text="📁 Из файла", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY), fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER, command=self._upload_gift_file,
        ).pack(side="left")
        self.gift_add_result = ctk.CTkLabel(add_inner, text="", text_color=TEXT_DIM, anchor="w")
        self.gift_add_result.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.cards_list = ctk.CTkScrollableFrame(
            self.cards_list_wrap, fg_color=BG_SURFACE, corner_radius=RADIUS_CARD,
        )
        self.cards_list.grid(row=1, column=0, sticky="nsew")

        self.cards_detail = ctk.CTkFrame(
            body, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
        )
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
            fg_color=BG_ELEVATED, corner_radius=RADIUS_SM, wrap="word",
        )
        self.cards_detail_body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.cards_detail_body.configure(state="disabled")

        self._selected_gift: dict | None = None

    def _build_vpn(self) -> None:
        p = self._page_fill("vpn")
        p.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(hdr, "VPN", "Расширение VeepN для Flipkart", SVC_YOUTUBE)

        inner_wrap = ctk.CTkFrame(p, fg_color="transparent")
        inner_wrap.grid(row=1, column=0, sticky="ew", pady=(0, _GAP_CARD))
        inner = self._card(inner_wrap, "Статус", accent=SVC_YOUTUBE)
        inner.pack(fill="x")
        self.vpn_page_status = ctk.CTkLabel(
            inner, text="Загрузка…", justify="left", anchor="w", font=_ui_font(FONT_SECTION),
        )
        self.vpn_page_status.pack(fill="x")
        self.vpn_page_ext = ctk.CTkLabel(
            inner, text="", justify="left", anchor="w", text_color=TEXT_DIM, font=_ui_font(FONT_BODY),
        )
        self.vpn_page_ext.pack(fill="x", pady=(4, 0))

        btns = ctk.CTkFrame(p, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew")
        ctk.CTkButton(
            btns, text="🔒  Проверить VPN", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"),
            fg_color=SVC_YOUTUBE, hover_color="#cc0029", command=self._check_vpn,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="📦  Установить расширения", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._install_extensions_bg,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="📁  Папка veepn_extension", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._open_vpn_folder,
        ).pack(side="left")

    def _build_tools(self) -> None:
        p = self._page_fill("tools")

        hdr = ctk.CTkFrame(p, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew")
        self._page_header(
            hdr, "Инструменты",
            "Проверка активации · cookies · обслуживание профилей",
            SVC_YOUTUBE,
        )
        grid_wrap = ctk.CTkFrame(p, fg_color="transparent")
        grid_wrap.grid(row=1, column=0, sticky="nsew")
        grid_card = self._card(grid_wrap, "Действия", accent=SVC_YOUTUBE)
        grid_card.pack(fill="both", expand=True)
        self._action_grid(grid_card, [
            ("✅  Проверить активацию", self._tool_check_activation, BTN_SECONDARY),
            ("🍪  Восстановить cookies", self._tool_restore_cookies, BTN_SECONDARY),
            ("🔄  Проверить обновления", self._tool_check_updates_now, BTN_SECONDARY),
            ("🗑  Очистить папки архива", self._tool_purge, ERROR),
            ("📂  cookies_backup/", lambda: self._open_folder("cookies_backup"), BTN_SECONDARY),
            ("📂  chrome_profiles/", lambda: self._open_folder("chrome_profiles"), BTN_SECONDARY),
        ], cols=2)

    def _build_logs(self) -> None:
        p = self._page_fill("logs")
        p.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        title_box = ctk.CTkFrame(bar, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title_box, text="Логи", font=_ui_font(FONT_TITLE, "bold"), text_color=TEXT_PRIMARY,
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text="Журнал событий", font=_ui_font(FONT_BODY), text_color=TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        toolbar = ctk.CTkFrame(bar, fg_color="transparent")
        toolbar.pack(side="right")
        ctk.CTkButton(
            toolbar, text="Очистить", width=90, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER, command=self._clear_logs,
        ).pack(side="left")
        ctk.CTkButton(
            toolbar, text="Открыть файл", width=110, height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY),
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER, command=self._open_log_file,
        ).pack(side="left", padx=6)

        self.log_text = ctk.CTkTextbox(
            p, font=ctk.CTkFont(family="Consolas", size=FONT_MONO), fg_color=BG_ELEVATED,
            corner_radius=RADIUS_CARD, border_width=1, border_color=BORDER_SUBTLE,
            wrap="none", activate_scrollbars=True,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self._log_file_pos = 0

    def _build_settings(self) -> None:
        p = self._page("settings")
        self._page_header(p, "Настройки", "Параметры SubHub", ACCENT)

        cols = ctk.CTkFrame(p, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        cols.grid_columnconfigure((0, 1), weight=1)
        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        inner = self._card(left, "Система")
        sys_grid = ctk.CTkFrame(inner, fg_color="transparent")
        sys_grid.pack(fill="x")
        sys_grid.grid_columnconfigure((0, 1), weight=1)
        for i, (txt, cmd) in enumerate([
            ("📦 Зависимости", self._install_deps),
            ("📁 Проект", lambda: os.startfile(str(_HERE))),
            ("🔑 secrets.yaml", self._open_secrets),
            ("⚙️ config.yaml", self._open_config),
        ]):
            ctk.CTkButton(
                sys_grid, text=txt, height=BTN_H, corner_radius=RADIUS_BTN,
                font=_ui_font(FONT_BODY),
                fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER, command=cmd,
            ).grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)

        inner_upd = self._card(left, "Обновления", accent=BTN_SUCCESS)
        self.settings_upd_info = ctk.CTkLabel(
            inner_upd, text="Проверка…", justify="left", anchor="w", text_color=TEXT_DIM,
            font=_ui_font(FONT_BODY, "bold"),
        )
        self.settings_upd_info.pack(fill="x", pady=(0, 2))
        self.settings_upd_sub = ctk.CTkLabel(
            inner_upd, text="", justify="left", anchor="w", text_color=TEXT_MUTED,
            font=_ui_font(FONT_SMALL),
        )
        self.settings_upd_sub.pack(fill="x", pady=(0, 4))
        self.settings_upd_list = ctk.CTkLabel(
            inner_upd, text="", justify="left", anchor="nw",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Consolas", size=FONT_SMALL),
            wraplength=340,
        )
        self.settings_upd_check_btn = ctk.CTkButton(
            inner_upd, text="🔄 Проверить", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._tool_check_updates_now,
        )
        self.settings_upd_check_btn.pack(fill="x", pady=(2, 2))
        self.settings_upd_btn = ctk.CTkButton(
            inner_upd, text="⬆ Скачать и перезапустить", height=BTN_H, corner_radius=RADIUS_BTN,
            font=_ui_font(FONT_BODY, "bold"), fg_color=BTN_SUCCESS,
            hover_color="#2ea043",
            command=self._update_and_restart,
        )
        upd_row = ctk.CTkFrame(inner_upd, fg_color="transparent")
        upd_row.pack(fill="x", pady=(2, 0))
        upd_row.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            upd_row, text="♻ Перезапуск", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._restart_only,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
        ctk.CTkButton(
            upd_row, text="🖥 Ярлык", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._create_desktop_shortcut_ui,
        ).grid(row=0, column=1, sticky="ew", padx=(2, 0))

        inner_gs = self._card(left, "GrizzlySMS", accent=WARNING)
        ctk.CTkLabel(
            inner_gs,
            text="При старте и перезапуске SubHub все активные номера\n"
                 "отменяются автоматически (возврат на баланс).",
            justify="left", anchor="w", text_color=TEXT_MUTED,
            font=_ui_font(FONT_SMALL),
        ).pack(fill="x", pady=(0, 6))
        self.grizzly_cancel_status = ctk.CTkLabel(
            inner_gs, text="", justify="left", anchor="w", text_color=TEXT_DIM,
            font=_ui_font(FONT_SMALL),
        )
        self.grizzly_cancel_status.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(
            inner_gs, text="🛑 Отменить все активные номера", height=BTN_H,
            corner_radius=RADIUS_BTN, fg_color=ERROR, hover_color="#da3633",
            command=self._cancel_grizzly_numbers,
        ).pack(fill="x")

        inner2 = self._card(right, "API-ключи")
        self._settings_key_labels: dict[str, ctk.CTkLabel] = {}
        for name, section, key in [
            ("GrizzlySMS", "grizzlysms", "api_key"),
            ("Telegram", "telegram", "token"),
            ("GGSell", "ggsel", "api_key"),
        ]:
            row = ctk.CTkFrame(inner2, fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP)
            row.pack(fill="x", pady=3)
            icon = ctk.CTkLabel(row, text="…", width=36, font=_ui_font(FONT_SECTION))
            icon.pack(side="left", padx=(8, 4), pady=5)
            ctk.CTkLabel(
                row, text=name, font=_ui_font(FONT_BODY),
                text_color=TEXT_PRIMARY, anchor="w",
            ).pack(side="left", pady=5)
            self._settings_key_labels[f"{section}.{key}"] = icon
        self.settings_keys = None  # legacy ref guard

        inner_bg = self._card(right, "Фоновый режим")
        for sw_attr, text, key, handler in (
            ("sw_background", "Постоянная работа в фоне", "background_mode", "_on_setting_background"),
            ("sw_tray", "При закрытии — в трей (с вопросом)", "minimize_to_tray", "_on_setting_tray"),
            ("sw_startup", "Автозапуск Windows", "run_at_startup", "_on_setting_startup"),
            ("sw_start_min", "Старт свёрнутым", "start_minimized", "_on_setting_start_min"),
        ):
            row = ctk.CTkFrame(inner_bg, fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP)
            row.pack(fill="x", pady=3)
            sw = ctk.CTkSwitch(
                row, text=text, font=_ui_font(FONT_BODY),
                command=getattr(self, handler),
            )
            sw.pack(anchor="w", padx=8, pady=4)
            setattr(self, sw_attr, sw)
            if self._app_settings.get(key):
                sw.select()
        ctk.CTkButton(
            inner_bg, text="📌 В трей", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=self._minimize_to_tray,
        ).pack(fill="x", pady=(4, 0))

        inner_notif = self._card(right, "Уведомления", accent=SVC_GGSELL)
        for sw_attr, text, key, handler in (
            ("sw_notify_orders", "Новые заказы GGSELL", "notify_ggs_orders", "_on_setting_notify_orders"),
            ("sw_notify_messages", "Новые сообщения в чате", "notify_ggs_messages", "_on_setting_notify_messages"),
        ):
            row = ctk.CTkFrame(inner_notif, fg_color=BG_SURFACE, corner_radius=RADIUS_CHIP)
            row.pack(fill="x", pady=3)
            sw = ctk.CTkSwitch(
                row, text=text, font=_ui_font(FONT_BODY),
                command=getattr(self, handler),
            )
            sw.pack(anchor="w", padx=8, pady=4)
            setattr(self, sw_attr, sw)
            if self._app_settings.get(key, True):
                sw.select()

    def _sync_settings_switches(self) -> None:
        s = self._app_settings
        for sw, key in (
            (getattr(self, "sw_background", None), "background_mode"),
            (getattr(self, "sw_tray", None), "minimize_to_tray"),
            (getattr(self, "sw_startup", None), "run_at_startup"),
            (getattr(self, "sw_start_min", None), "start_minimized"),
            (getattr(self, "sw_notify_orders", None), "notify_ggs_orders"),
            (getattr(self, "sw_notify_messages", None), "notify_ggs_messages"),
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

    def _on_setting_notify_orders(self) -> None:
        self._app_settings["notify_ggs_orders"] = bool(self.sw_notify_orders.get())
        self._persist_app_settings()

    def _on_setting_notify_messages(self) -> None:
        self._app_settings["notify_ggs_messages"] = bool(self.sw_notify_messages.get())
        self._persist_app_settings()

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def _grizzly_cancel_summary(self, r: dict) -> str:
        if r.get("error") == "no_api_key":
            return "API-ключ GrizzlySMS не настроен"
        if r.get("error"):
            return f"Ошибка: {r['error']}"
        total = int(r.get("total") or 0)
        cancelled = int(r.get("cancelled") or 0)
        failed = int(r.get("failed") or 0)
        bal = r.get("balance")
        bal_s = f" · баланс ${bal:.4f}" if bal is not None else ""
        if total == 0:
            return f"Активных номеров нет{bal_s}"
        if cancelled == total:
            return f"Отменено {cancelled} номер(ов){bal_s}"
        if cancelled:
            return f"Отменено {cancelled}/{total}, не удалось: {failed}{bal_s}"
        return f"Не удалось отменить {failed}/{total} (лимит Grizzly 1:30?){bal_s}"

    def _cancel_grizzly_numbers(self) -> None:
        self._run_bg(self._cancel_grizzly_numbers_worker, "Отмена активных номеров GrizzlySMS…")

    def _cancel_grizzly_numbers_worker(self) -> None:
        import grizzly as gz
        r = gz.cancel_all_active_rentals_blocking("вручную")
        msg = self._grizzly_cancel_summary(r)
        self.after(0, lambda: self._log(f"Grizzly: {msg}"))
        self.after(0, lambda: self.grizzly_cancel_status.configure(text=msg))

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
            cleanup_box: dict = {}

            def _startup_cancel() -> None:
                cleanup_box["r"] = gz.startup_cleanup_active_rentals("старт приложения")

            t = _thr.Thread(target=_startup_cancel, daemon=True, name="grizzly-startup-cancel")
            t.start()
            t.join(timeout=35)
            r = cleanup_box.get("r") or {}
            summary = self._grizzly_cancel_summary(r)
            if int(r.get("total") or 0) > 0 or r.get("error"):
                self._log(f"Grizzly: {summary}")
            if hasattr(self, "grizzly_cancel_status"):
                self.grizzly_cancel_status.configure(text=summary)

            scan = m.scan_profiles_extension_status()
            if m._vpn_extension_dir() and scan["total"]:
                m._set_vpn_bg_status(
                    "warming",
                    f"Фон: расширение {scan['with_ext']}/{scan['total']} проф.…",
                )
            m.start_background_bootstrap()
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

    def _run_bg(self, fn: Callable, label: str = "") -> None:
        if label:
            self._log(label)

        def _wrap():
            try:
                fn()
            except Exception as e:
                self._log(f"Ошибка: {e}")
            finally:
                self.after(0, self._ensure_window_visible)

        threading.Thread(target=_wrap, daemon=True).start()

    def _append_log_widget(self, widget: ctk.CTkTextbox | None, text: str, max_lines: int = 0) -> None:
        if widget is None:
            return
        try:
            widget.configure(state="normal")
            widget.insert("end", text)
            if max_lines > 0:
                lines = widget.get("1.0", "end").splitlines()
                if len(lines) > max_lines:
                    widget.delete("1.0", f"{len(lines) - max_lines + 1}.0")
            widget.see("end")
            widget.configure(state="disabled")
        except Exception:
            pass

    def _tick_logs(self) -> None:
        chunks = self._log_sink.drain()
        if chunks:
            text = "".join(chunks)
            self._append_log_widget(self.log_text, text, _MAIN_LOG_LINES)
            self._append_log_widget(
                getattr(self, "sidebar_log", None), text, _SIDEBAR_LOG_LINES,
            )
            if getattr(self, "_run_log_active", False):
                self._append_run_log(text)
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
                            self._append_log_widget(self.log_text, new, _MAIN_LOG_LINES)
                            self._append_log_widget(
                                getattr(self, "sidebar_log", None), new, _SIDEBAR_LOG_LINES,
                            )
                            if getattr(self, "_run_log_active", False):
                                self._append_run_log(new)
        except Exception:
            pass
        self.after(750, self._tick_logs)

    def _tick_status(self) -> None:
        # Скан профилей на диске — в фоне, чтобы UI-поток не подтормаживал
        if not getattr(self, "_status_tick_busy", False):
            self._status_tick_busy = True
            threading.Thread(
                target=self._collect_status_bg, daemon=True, name="status-tick",
            ).start()
        self.after(4000, self._tick_status)

    def _collect_status_bg(self) -> None:
        data = None
        try:
            import menu as m
            import bot as bot_mod
            data = {
                "profiles": len(m._load_done_profiles()),
                "tg": getattr(bot_mod, "_tg_status", "?"),
                "host": m.active_host() or "—",
                "vpn": m.get_vpn_bg_status(),
            }
        except Exception:
            data = None
        try:
            self.after(0, self._apply_status_tick, data)
        except Exception:
            self._status_tick_busy = False

    def _apply_status_tick(self, data: dict | None) -> None:
        self._status_tick_busy = False
        if not data:
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
                running = "⏳ Инициализация…"
            elif (self._proc and self._proc.poll() is None) or self._external_auto:
                running = "🟢 Запуск"
            else:
                running = "⚪ Ожидание"
            self.status_lbl.configure(text=f"{running}\n{data['profiles']} профилей · {data['host']}")

            vs = data["vpn"]
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
            self._status_tick_count += 1
            if self._status_tick_count % 6 == 0 or self._current_page == "settings":
                self._refresh_update_badge()
            self._sync_from_runtime()
        except Exception:
            pass

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
            self._set_run_status_ui(f"▶  Telegram: {mode or 'автоматизация'}", WARNING)
            self._set_run_form_enabled(False)
        elif not local and hasattr(self, "run_start_btn"):
            if str(self.run_status.cget("text")).startswith("▶  Telegram"):
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
            btn.pack(fill="x", pady=2, after=self.settings_upd_check_btn)

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
                lst = self.settings_upd_list
                if list_text.strip():
                    lst.configure(text=list_text.strip())
                    if not lst.winfo_ismapped():
                        lst.pack(fill="x", pady=(0, 4), before=self.settings_upd_check_btn)
                elif lst.winfo_ismapped():
                    lst.pack_forget()
                    lst.configure(text="")
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

    def _set_monitor_stat(self, lbl: ctk.CTkLabel, monitor_on: bool) -> None:
        lbl.configure(
            text="Активен" if monitor_on else "Выключен",
            text_color=SUCCESS if monitor_on else TEXT_DIM,
        )

    def _refresh_home_ggsell(self) -> None:
        if not hasattr(self, "home_ggs_orders"):
            return
        import menu as m
        gs = m._read_secrets().get("ggsel") or {}
        api_ok = bool(gs.get("api_key") and gs.get("seller_id") and "YOUR_" not in str(gs.get("api_key", "")))
        monitor_on = any(t.name == "ggsel-monitor" and t.is_alive() for t in threading.enumerate())
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
            active = key == flt
            btn.configure(
                fg_color=SVC_GGSELL if active else BG_SURFACE,
                hover_color=self._btn_hover(SVC_GGSELL) if active else BG_NAV_ACTIVE,
            )
        self._render_ggsell_orders()

    def _refresh_ggsell(self) -> None:
        import menu as m
        sec = m._read_secrets()
        gs = sec.get("ggsel") or {}
        api_ok = bool(gs.get("api_key") and gs.get("seller_id") and "YOUR_" not in str(gs.get("api_key", "")))
        self.ggs_stat_api.configure(text="✓" if api_ok else "✗")

        monitor_on = any(t.name == "ggsel-monitor" and t.is_alive() for t in threading.enumerate())
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
                    text="⚠ Настройте GGSell API в secrets.yaml",
                )
            self._render_ggsell_orders()
            return

        if self._ggs_loading:
            return
        self._ggs_loading = True
        if hasattr(self, "ggs_orders_status"):
            self.ggs_orders_status.configure(text="⏳ Загрузка заказов…")

        def _w():
            try:
                asyncio.run(self._load_ggsell_orders())
            except Exception as e:
                self.after(0, lambda: self._log(f"GGSell заказы: {e}"))
            finally:
                self.after(0, self._ggsell_orders_loaded)

        threading.Thread(target=_w, daemon=True, name="ggs-orders").start()
        threading.Thread(target=self._fetch_ggsell_balance, daemon=True).start()

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

    def _ggsell_orders_loaded(self) -> None:
        self._ggs_loading = False
        self._render_ggsell_orders()
        n = len(self._ggs_orders)
        self._log(f"✓ GGSell: загружено {n} заказов")

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
                    f"🟢 {counts['new']}  ·  🔵 {counts['issued']}  ·  "
                    f"🟡 {counts['used']}  ·  показано: {len(orders)}"
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
                fg_color=SVC_GGSELL if active else BG_CARD,
                corner_radius=RADIUS_CHIP,
                border_width=0,
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
            accent = SVC_GGSELL if seller else "#3d5a80"
            row = ctk.CTkFrame(self.ggs_chat_scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            bubble = ctk.CTkFrame(
                row, fg_color=accent if seller else BG_SURFACE,
                corner_radius=RADIUS_CHIP,
                border_width=0,
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
                text_color="#ffe8cc" if seller else TEXT_DIM,
                anchor="w", justify="left",
            ).pack(anchor="w", padx=10, pady=(6, 0))
            body = (msg.get("text") or "(пусто)").strip()
            ctk.CTkLabel(
                bubble, text=body, anchor="w", justify="left",
                font=_ui_font(FONT_BODY),
                text_color="#f0f6fc" if seller else "#e6edf3",
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
            STATUS_ICON, STATUS_LABEL, order_email, parse_order, status_key,
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
            f"{STATUS_ICON.get(sk, '•')} {STATUS_LABEL.get(sk, sk)}",
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
                self.after(0, lambda: self._ggsell_chat_loaded(inv_id, parsed, None))
            except Exception as e:
                self.after(0, lambda: self._ggsell_chat_loaded(inv_id, None, str(e)))

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
            self.after(0, lambda: self._ggsell_after_send(inv, err))

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
            hdr, text="📝 Шаблоны", font=_ui_font(FONT_SECTION, "bold"),
            text_color="#f0f6fc",
        ).pack(side="left")
        ctk.CTkButton(
            hdr, text="✕", width=BTN_ICON, height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color="transparent", hover_color=BTN_SECONDARY,
            command=self._ggsell_close_templates_modal,
        ).pack(side="right")

        list_frame = ctk.CTkScrollableFrame(
            win, fg_color=BG_CARD, corner_radius=RADIUS_CARD,
            border_width=1, border_color=BORDER_SUBTLE, height=120,
        )
        list_frame.pack(fill="x", padx=10, pady=(0, 6))

        preview = ctk.CTkTextbox(
            win, height=130, corner_radius=RADIUS_CARD,
            fg_color=BG_ELEVATED, border_width=1, border_color=BORDER_SUBTLE,
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
                resolved = f"⚠ {e}"
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", resolved)
            preview.configure(state="disabled")

        for key, (label, hint) in _GGS_TEMPLATE_META.items():
            row = ctk.CTkFrame(list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkButton(
                row, text=f"{label}", anchor="w", height=BTN_H,
                corner_radius=RADIUS_BTN, fg_color=BTN_SECONDARY,
                hover_color=BTN_SECONDARY_HOVER,
                command=lambda k=key: _pick(k),
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

        ctk.CTkButton(
            btns, text="Вставить", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=BTN_SECONDARY, hover_color=BTN_SECONDARY_HOVER,
            command=_insert,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            btns, text="Отправить", height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color=SVC_GGSELL, hover_color="#e67e00",
            command=_send_tpl,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ctk.CTkButton(
            win, text="⚙️ Редактировать шаблоны (файл)",
            height=BTN_H, corner_radius=RADIUS_BTN,
            fg_color="transparent", hover_color=BTN_SECONDARY,
            command=self._open_ggsell_templates,
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
        rows = getattr(self, "_settings_key_labels", None)
        if not rows:
            return
        for name, section, key in [
            ("GrizzlySMS", "grizzlysms", "api_key"),
            ("Telegram", "telegram", "token"),
            ("GGSell", "ggsel", "api_key"),
        ]:
            val = (sec.get(section) or {}).get(key, "")
            ok = "✅" if val and "YOUR_" not in str(val) else "❌"
            lbl = rows.get(f"{section}.{key}")
            if lbl is not None:
                lbl.configure(text=ok, text_color=SUCCESS if ok == "✅" else ERROR)

    def _set_profile_filter(self, flt: str) -> None:
        self._profile_filter = flt
        for key, btn in getattr(self, "_profile_filter_btns", {}).items():
            active = key == flt
            btn.configure(
                fg_color=SVC_YOUTUBE if active else BG_SURFACE,
                hover_color=self._btn_hover(SVC_YOUTUBE) if active else BG_NAV_ACTIVE,
            )
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
        icons = {"noaddr": "🟢", "hasaddr": "🟠", "paid": "🟣", "active": "🔵"}
        icon = icons.get(cat, "•")
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
            parts.append(f"🔗 {short}{'…' if len(link) > 36 else ''}")
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
                border_width=1 if is_sel else 0,
                border_color=ACCENT if is_sel else BG_CARD,
            )
            row.pack(fill="x", pady=3, padx=2)
            self._bind_row_hover(row)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)
            ctk.CTkLabel(
                inner, text=f"{icon}  {phone}", font=_ui_font(FONT_BODY, "bold"),
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

    def _render_profile_detail(self, prof: dict | None) -> None:
        for w in self.profile_detail_actions.winfo_children():
            w.destroy()
        self.profile_detail_body.configure(state="normal")
        self.profile_detail_body.delete("1.0", "end")
        if not prof:
            self.profile_detail_body.insert("1.0", "Выберите профиль слева\n\nОдин клик — выбор и действия здесь.")
            self.profile_detail_body.configure(state="disabled")
            return

        import menu as m
        phone = str(prof.get("username", ""))
        path = prof.get("path")
        cat = self._profile_category(prof)
        has_link = bool(
            prof.get("black_activation_link") or prof.get("black_short_link")
            or prof.get("issued_link")
        )
        is_issued = bool(prof.get("issued_ts"))
        busy = phone in self._prof_busy

        self.profile_detail_body.insert("1.0", self._profile_menu_info(prof))
        if busy:
            self.profile_detail_body.insert("end", "\n\n⏳ Операция выполняется…")
        self.profile_detail_body.configure(state="disabled")

        def _btn(txt: str, cmd: Callable, color: str = BTN_SECONDARY) -> None:
            state = "disabled" if busy else "normal"
            ctk.CTkButton(
                self.profile_detail_actions, text=txt, height=BTN_H, corner_radius=RADIUS_BTN,
                fg_color=color, hover_color=self._btn_hover(color), state=state,
                command=cmd,
            ).pack(fill="x", pady=3)

        _btn("🌐  Открыть Chrome", lambda: self._prof_chrome(phone, path), BTN_SECONDARY)
        _btn("🌑  Проверить активацию (фон)", lambda: self._prof_activate(phone, path), SUCCESS)

        if cat in ("noaddr", "hasaddr"):
            _btn("🥈  Купить 3 мес · ₹343", lambda: self._profile_buy_for(prof, 3), SVC_YOUTUBE)
            _btn("🥇  Купить 12 мес · ₹1499", lambda: self._profile_buy_for(prof, 12), SVC_YOUTUBE)
            _btn("📍  Заполнить адрес", lambda: self._profile_fill_address_for(prof), BTN_SECONDARY)
            _btn("⚡  Заполнить данные (до оплаты)", lambda: self._prof_fill_data(phone, path), WARNING)
            if not is_issued:
                _btn("🗑  Удалить профиль", lambda: self._prof_delete(phone, path), ERROR)

        if cat == "paid":
            _btn("🔵  Поставить статус «выдан»", lambda: self._prof_set_issued(phone, path), BTN_SECONDARY)
            if has_link:
                _btn("🔄  Заменить ссылку", lambda: self._prof_activate(phone, path), WARNING)

        if cat == "active":
            if prof.get("issued_invoice_id"):
                inv = int(prof["issued_invoice_id"])

                def _go_order() -> None:
                    self._enter_service("ggsell")
                    self._ggs_selected_id = inv
                    self.show_page("ggsell")
                    self.after(400, self._refresh_ggsell)

                _btn(f"📋  Заказ GGSell #{inv}", _go_order, SVC_GGSELL)
            _btn("📦  Перенести в архив", lambda: self._prof_archive(phone, path), BTN_SECONDARY)

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
        self.archive_stat_total.configure(text=str(len(records)))
        self.archive_stat_cookies.configure(text=str(with_cookies))
        self.archive_stat_restored.configure(text=str(alive))

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
                border_width=1 if is_sel else 0,
                border_color=SVC_YOUTUBE if is_sel else BG_CARD,
            )
            row.pack(fill="x", pady=3, padx=2)
            self._bind_row_hover(row)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=8)
            badges = []
            if has_ck:
                badges.append("🍪")
            if is_alive:
                badges.append("✓")
            badge_txt = (" ".join(badges) + "  ") if badges else ""
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
                first_row.configure(fg_color=BG_CARD_HOVER, border_width=1, border_color=SVC_YOUTUBE)

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
            row.configure(fg_color=BG_CARD_HOVER, border_width=1, border_color=SVC_YOUTUBE)
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
            lines.append(f"📆 Создан: {rec['login_str']}")
        if rec.get("issued_str"):
            lines.append(f"✅ Выдан: {rec['issued_str']}")
        if rec.get("used_str"):
            lines.append(f"📦 В архиве: {rec['used_str']}")
        inv = rec.get("issued_invoice_id")
        if inv:
            em = rec.get("buyer_email") or rec.get("email") or ""
            lines.append(f"📋 Заказ #{inv}" + (f" · {em}" if em else ""))
        months = rec.get("subscription_months")
        if months:
            lines.append(f"⏳ Подписка: {months} мес.")
        vt = rec.get("black_valid_till") or rec.get("subscription_expires_str")
        if vt:
            lines.append(f"До: {vt}")
        link = rec.get("issued_link") or rec.get("black_short_link") or ""
        if link:
            lines.append(f"🔗 {link}")
        ck = Path("cookies_backup") / f"cookies_{rec.get('username', '')}.json"
        lines.append(f"🍪 Куки: {'есть' if ck.exists() else 'нет'}")
        note = rec.get("note") or ""
        if note:
            lines.append(f"📝 {note}")
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
        if busy:
            self.archive_detail_body.insert("end", "\n\n⏳ Операция выполняется…")
        self.archive_detail_body.configure(state="disabled")

        def _btn(txt: str, cmd: Callable, color: str = BTN_SECONDARY, enabled: bool = True) -> None:
            state = "normal" if enabled and not busy else "disabled"
            ctk.CTkButton(
                self.archive_detail_actions, text=txt, height=BTN_H, corner_radius=RADIUS_BTN,
                fg_color=color, hover_color=self._btn_hover(color), state=state,
                command=cmd,
            ).pack(fill="x", pady=3)

        _btn(
            "♻  Восстановить профиль",
            lambda: self._archive_restore(rec),
            SUCCESS,
            enabled=not done_exists,
        )
        if has_ck:
            _btn(
                "🍪  Восстановить из куков",
                lambda: self._archive_restore_cookies(rec, ck_file),
                SVC_YOUTUBE,
            )
            _btn(
                "📄  Открыть файл куков",
                lambda: self._open_path(ck_file),
                BTN_SECONDARY,
            )
        _btn(
            "📁  Папка cookies_backup",
            lambda: self._open_folder("cookies_backup"),
            BTN_SECONDARY,
        )
        _btn("🗑  Удалить запись", lambda: self._archive_delete(rec), ERROR)

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
            self.after(0, self._refresh_archive)
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
            active = key == tab
            btn.configure(
                fg_color=ACCENT if active else BG_SURFACE,
                hover_color=self._btn_hover(ACCENT) if active else BG_NAV_ACTIVE,
            )
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
                ("➕ Добавить", self._show_add_card_dialog, SUCCESS),
                ("✎ Изменить", self._edit_selected_bank_card, BTN_SECONDARY),
                ("🗑 Удалить", self._delete_card, ERROR),
                ("↑  Вверх", lambda: self._move_bank_card(-1), BTN_SECONDARY),
                ("↓  Вниз", lambda: self._move_bank_card(1), BTN_SECONDARY),
                ("↺ Порядок", self._reset_bank_order, BTN_SECONDARY),
            ]
        elif tab == "gift":
            specs = [
                ("➕ Добавить", self._toggle_gift_add_panel, SUCCESS),
                ("📁 Файл", self._upload_gift_file, BTN_SECONDARY),
                ("✎ Изменить", self._edit_selected_gift_card, BTN_SECONDARY),
                ("🗑 Удалить", self._delete_gift_card, ERROR),
                ("↑  Вверх", lambda: self._move_gift_card(-1), BTN_SECONDARY),
                ("↓  Вниз", lambda: self._move_gift_card(1), BTN_SECONDARY),
                ("💳  Способ оплаты", self._toggle_pay_method, WARNING),
            ]
        else:
            specs = [("🔄 Обновить", self._refresh_cards, BTN_SECONDARY)]
        for i, (txt, cmd, color) in enumerate(specs):
            ctk.CTkButton(
                self.cards_toolbar, text=txt, height=BTN_H,
                corner_radius=RADIUS_BTN, font=_ui_font(FONT_BODY, "bold"),
                fg_color=color, hover_color=self._btn_hover(color), command=cmd,
            ).pack(side="left", padx=(0, 4))

    def _cards_row(
        self, parent, pos: int, title: str, subtitle: str, badge: str,
        selected: bool, on_select: Callable,
    ) -> ctk.CTkFrame:
        bg = ACCENT if selected else BG_CARD
        row = ctk.CTkFrame(parent, fg_color=bg, corner_radius=RADIUS_CHIP)
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
        pm_txt = "🎁 гифт-карты" if pm == "gift" else "💳 банковская"

        self.cards_stat_bank.configure(text=str(len(cards)))
        self.cards_stat_gift.configure(text=str(len(gc)))
        self.cards_stat_balance.configure(text=f"₹{bal}")
        self.cards_stat_pay.configure(text=pm_txt)

        if self._gift_add_visible and self._cards_tab == "gift":
            self.gift_add_panel.grid(row=0, column=0, sticky="ew", pady=(0, 6))
            self.cards_list.grid(row=1, column=0, sticky="nsew")
        else:
            self.gift_add_panel.grid_forget()
            self.cards_list.grid(row=1, column=0, sticky="nsew")

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
                by_denom: dict[int, int] = {}
                for c in gc:
                    d = int(c.get("denom") or 0)
                    by_denom[d] = by_denom.get(d, 0) + 1
                breakdown = "  ·  ".join(
                    f"₹{d}×{by_denom[d]}" for d in sorted(by_denom, reverse=True)
                )
                for pos, c in enumerate(gc, 1):
                    denom = int(c.get("denom") or 0)
                    series = c.get("number", "")
                    pin = c.get("pin", "")
                    added = m._fmt_msk(c["added_ts"]) if c.get("added_ts") else "—"
                    sel = (pos - 1) == self._sel_gift_idx

                    def _pick(p=pos - 1, card=c):
                        self._sel_gift_idx = p
                        self._selected_gift = card
                        self._refresh_cards()

                    self._cards_row(
                        self.cards_list, pos, f"₹{denom}",
                        f"серия …{series[-6:]}  ·  PIN …{pin[-4:]}",
                        added[:10] if added != "—" else "",
                        sel, _pick,
                    )
                if self._sel_gift_idx is not None:
                    c = gc[self._sel_gift_idx]
                    self._set_cards_detail(
                        f"Номинал: ₹{int(c.get('denom') or 0)}\n"
                        f"Серия: {c.get('number', '—')}\n"
                        f"PIN: {c.get('pin', '—')}\n"
                        f"Добавлена: {m._fmt_msk(c['added_ts']) if c.get('added_ts') else '—'}\n\n"
                        f"Позиция в очереди: {self._sel_gift_idx + 1} из {len(gc)}\n"
                        f"Сводка: {breakdown}\n"
                        f"Способ оплаты: {pm_txt}"
                    )
                else:
                    self._set_cards_detail(
                        f"Всего: {len(gc)} шт.  ·  Баланс ₹{bal}\n{breakdown}\n\n"
                        f"Способ оплаты: {pm_txt}\n"
                        "Выберите карту слева. ↑ ↓ меняют порядок применения."
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
            text=f"{icons.get(state, '•')}  {state.upper()}\n{msg}\n{prof_line}"
            + (f"\n\nПроверка: {self._vpn_last_check}" if self._vpn_last_check else ""),
        )
        self.vpn_page_ext.configure(
            text=f"Расширение: {ext or 'не найдено (положите в veepn_extension/)'}")

    def _sync_run_page_status(self) -> None:
        import menu as m
        ext, ast = m.shared_automation_running()
        local = self._proc and self._proc.poll() is None
        if ext and not local:
            mode = ast.get("automation_mode") or "автоматизация"
            self.run_start_btn.configure(state="disabled")
            self.run_stop_btn.configure(state="normal")
            self._set_run_status_ui(f"▶  Telegram: {mode}", WARNING)
            self._set_run_form_enabled(False)
        elif local:
            self.run_start_btn.configure(state="disabled")
            self.run_stop_btn.configure(state="normal")
            if not str(self.run_status.cget("text")).startswith("▶"):
                self._set_run_status_ui("Выполняется…", WARNING)
            self._set_run_form_enabled(False)
        elif not str(self.run_status.cget("text")).startswith("▶  Telegram"):
            self.run_start_btn.configure(state="normal")
            self.run_stop_btn.configure(state="disabled")
            if self.run_status.cget("text") in ("Выполняется…", "Проверка VPN…"):
                self._set_run_status_ui("Готов к запуску", TEXT_DIM)
            self._set_run_form_enabled(True)

    def _apply_vpn_check_result(self, ok: bool, msg: str) -> None:
        color = SUCCESS if ok else ERROR
        self._vpn_last_check = msg
        if hasattr(self, "run_status"):
            self._set_run_status_ui(msg, color)
        self._refresh_vpn_page()

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
            short = text.replace("▶  ", "").strip()[:24]
            self.run_stat_state.configure(text=short or "—")
            if hasattr(self.run_stat_state, "sub_label"):
                self.run_stat_state.sub_label.configure(
                    text="выполняется" if color == WARNING else (
                        "готово" if color == SUCCESS else "ожидание"
                    ),
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
            active = k == key
            btn.configure(
                fg_color=SVC_YOUTUBE if active else BG_SURFACE,
                hover_color=self._btn_hover(SVC_YOUTUBE) if active else BG_NAV_ACTIVE,
            )
        self._on_run_param_change()

    def _on_run_param_change(self, *_args) -> None:
        key = self._run_mode_key()
        self._run_preset_key = key
        for k, btn in getattr(self, "_run_preset_btns", {}).items():
            active = k == key
            btn.configure(
                fg_color=SVC_YOUTUBE if active else BG_SURFACE,
                hover_color=self._btn_hover(SVC_YOUTUBE) if active else BG_NAV_ACTIVE,
            )
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
            self.run_stat_profiles.configure(text=str(len(m._load_done_profiles())))
            vs = m.get_vpn_bg_status()
            state = vs.get("state", "idle")
            vpn_labels = {
                "ready": ("✓ OK", SUCCESS),
                "warming": ("…", WARNING),
                "installing": ("…", WARNING),
                "error": ("✗", ERROR),
                "no_ext": ("—", TEXT_DIM),
                "idle": ("—", TEXT_DIM),
            }
            vpn_txt, vpn_col = vpn_labels.get(state, ("—", TEXT_DIM))
            self.run_stat_vpn.configure(text=vpn_txt, text_color=vpn_col)
        except Exception:
            pass
        self._update_run_cmd_preview()
        threading.Thread(target=self._fetch_run_balance, daemon=True, name="run-bal").start()

    def _fetch_run_balance(self) -> None:
        try:
            import grizzly as gz
            bal = gz.get_balance()
            self.after(0, lambda: self.run_stat_balance.configure(text=f"${bal:.2f}"))
        except Exception:
            self.after(0, lambda: self.run_stat_balance.configure(text="—"))

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
            self.run_progress.grid()
            try:
                self.run_progress.start()
            except Exception:
                pass
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
                    with contextlib.suppress(Exception):
                        sys.stdout.write(line)
                code = self._proc.wait() if self._proc else -1
                m.clear_automation_proc()
                self.after(0, lambda: self._run_finished(code))
            except Exception as e:
                m.clear_automation_proc()
                self.after(0, lambda: self._run_finished(-1, str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _run_finished(self, code: int, err: str = "") -> None:
        self._run_log_active = False
        if hasattr(self, "run_progress"):
            try:
                self.run_progress.stop()
            except Exception:
                pass
            self.run_progress.grid_remove()
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
        self._refresh_run_page()
        self._refresh_youtube_hub()
        self._ensure_window_visible()

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
            self._set_run_status_ui("Остановлено", WARNING)
            self._set_run_form_enabled(True)
            self._run_log_active = False
            if hasattr(self, "run_progress"):
                try:
                    self.run_progress.stop()
                except Exception:
                    pass
                self.run_progress.grid_remove()

    def _check_vpn(self) -> None:
        self._log("Проверка VPN и доступности Flipkart…")
        self._set_run_status_ui("Проверка VPN…", WARNING)
        if hasattr(self, "vpn_page_status"):
            self.vpn_page_status.configure(text="⏳  Проверка VPN и Flipkart…")

        def _worker():
            import menu as m
            try:
                ok = asyncio.run(m._check_flipkart_accessible())
                msg = "✓ VPN OK · Flipkart доступен" if ok else "✗ Flipkart недоступен (проверьте VPN)"
                self._log(msg)
                if ok:
                    m._set_vpn_bg_status("ready", "Flipkart доступен")
                else:
                    m._set_vpn_bg_status("error", "Flipkart недоступен")
                self.after(0, lambda: self._apply_vpn_check_result(ok, msg))
            except Exception as e:
                self._log(f"Ошибка: {e}")
                self.after(0, lambda: self._apply_vpn_check_result(False, f"✗ {e}"))
            finally:
                self.after(0, self._ensure_window_visible)

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
            self.after(0, self._refresh_vpn_page)

        threading.Thread(target=_w, daemon=True).start()

    # ── Profiles ──────────────────────────────────────────────────────────────

    def _profile_menu_info(self, prof: dict) -> str:
        import menu as m
        phone = m._disp_phone(prof.get("username", "?"))
        lines = [phone, ""]
        if prof.get("login_str"):
            lines.append(f"📆 Создан: {prof['login_str']}")
        if prof.get("issued_str"):
            lines.append(f"✅ Выдан: {prof['issued_str']}")
        inv = prof.get("issued_invoice_id")
        if inv:
            em = prof.get("buyer_email") or ""
            lines.append(f"📦 Заказ #{inv}" + (f" · {em}" if em else ""))
        vt = prof.get("black_valid_till") or prof.get("subscription_expires_str")
        if vt:
            lines.append(f"⏳ До: {vt}")
        link = (
            prof.get("issued_link") or prof.get("black_short_link")
            or prof.get("black_activation_link") or ""
        )
        if link:
            lines.append(f"🔗 {link}")
        note = prof.get("note") or ""
        if note:
            lines.append(f"📝 {note}")
        return "\n".join(lines)

    def _open_profile_menu(self, prof: dict) -> None:
        """Совместимость: открытие действий в панели справа, без отдельного окна."""
        self._select_profile(prof)

    def _prof_run(self, phone: str, label: str, fn: Callable[[], None]) -> None:
        if phone in self._prof_busy:
            self._log(f"⚠ {phone}: уже выполняется")
            return
        self._prof_busy.add(phone)
        self._log(label)
        if self._selected_profile and str(self._selected_profile.get("username", "")) == phone:
            self._render_profile_detail(self._selected_profile)
        if self._selected_archive and str(self._selected_archive.get("username", "")) == phone:
            self._render_archive_detail(self._selected_archive)

        def _w():
            try:
                fn()
            except Exception as e:
                self._log(f"Ошибка: {e}")
            finally:
                self._prof_busy.discard(phone)

                def _ui():
                    if self._selected_profile and str(self._selected_profile.get("username", "")) == phone:
                        self._render_profile_detail(self._selected_profile)
                    if self._selected_archive and str(self._selected_archive.get("username", "")) == phone:
                        self._render_archive_detail(self._selected_archive)
                    if self._current_page in ("profiles", "youtube_hub", "run"):
                        self._schedule_refresh("profiles", lambda: self._refresh_profiles(force=True))
                    if self._current_page == "archive":
                        self._schedule_refresh("archive", self._refresh_archive)

                self.after(0, _ui)

        threading.Thread(target=_w, daemon=True, name=f"prof-{phone}").start()

    def _prof_chrome(self, phone: str, path) -> None:
        def _w():
            import menu as m
            ok = m.open_chrome(path)
            self._log(
                f"{'✓' if ok else '✗'} Chrome +91 {phone}"
                + (" — открываю VPN и Flipkart…" if ok else "")
            )
        self._prof_run(phone, f"Открытие Chrome +91 {phone}…", _w)

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

    def _prof_fill_data(self, phone: str, path) -> None:
        def _w():
            import menu as m
            addr = m._gen_indian_address()
            ok, msg = asyncio.run(m._do_fill_address(path, addr, stop_at_payment=True))
            self._log(f"{'✓' if ok else '✗'} {phone}: {msg}")
            if ok:
                self.after(0, self._schedule_refresh)
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
            self._log(f"{'✓' if ok else '✗'} {msg}")
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
        scroll = ctk.CTkScrollableFrame(dlg)
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
        _regenerate_app_ico()
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

    def _on_close(self) -> None:
        if self._quitting:
            return
        bg = self._app_settings.get("background_mode", True)
        if not bg:
            if messagebox.askyesno("Выход", "Закрыть приложение?"):
                self._quit_app()
            return

        tray = self._app_settings.get("minimize_to_tray", False)
        hint = (
            "• Да — свернуть в трей\n"
            "• Нет — оставить окно открытым\n"
            "• Отмена"
        ) if tray else (
            "• Да — скрыть окно (фон продолжит работу)\n"
            "• Нет — оставить окно открытым\n"
            "• Отмена"
        )
        r = messagebox.askyesnocancel(
            "SubHub",
            "Приложение может работать в фоне.\n"
            "Полный выход — через меню иконки в трее → «Выход».\n\n" + hint,
        )
        if r is True:
            if tray:
                if not self._minimize_to_tray():
                    if messagebox.askyesno("Выход", "Не удалось свернуть в трей. Выйти полностью?"):
                        self._quit_app()
            else:
                self._hidden_to_tray = True
                self.withdraw()
                self._log("Окно скрыто — приложение работает в фоне")
        elif r is False:
            self._ensure_window_visible()

    def _shutdown_services(self) -> None:
        # Всегда останавливаем автоматизацию: и из UI (self._proc), и из Telegram
        # (PID в runtime). Раньше при перезапуске TG-процесс menu.py оставался жить
        # и продолжал покупать номера.
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
        self._ensure_window_visible()
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
