"""Windows: subprocess без мелькающих окон консоли (PowerShell, git, pip, wmic)."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_patched = False


def is_gui_host() -> bool:
    """True если нас запустили без консоли (pythonw / SubHub.exe)."""
    if sys.platform != "win32":
        return False
    exe = (sys.executable or "").lower().replace("/", "\\")
    if exe.endswith("\\pythonw.exe") or "pythonw.exe" in exe:
        return True
    if (os.environ.get("SUBHUB_LAUNCHED_BY") or "").strip():
        return True
    try:
        if sys.stdout is None or not sys.stdout.isatty():
            return True
    except Exception:
        return True
    return False


def hidden_kwargs(**extra: Any) -> dict[str, Any]:
    """creationflags + STARTUPINFO(SW_HIDE) для Windows. На других ОС — extra as-is."""
    kw = dict(extra)
    if sys.platform != "win32" or not _NO_WINDOW:
        return kw
    flags = int(kw.pop("creationflags", 0) or 0) | _NO_WINDOW
    # Не смешивать с CREATE_NEW_CONSOLE — иначе окно снова вспыхнет
    create_new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
    flags &= ~create_new_console
    kw["creationflags"] = flags
    if "startupinfo" not in kw:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kw["startupinfo"] = si
    return kw


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run со скрытым окном на Windows."""
    return subprocess.run(cmd, **hidden_kwargs(**kwargs))


def popen(cmd, **kwargs) -> subprocess.Popen:
    """subprocess.Popen со скрытым окном на Windows."""
    return subprocess.Popen(cmd, **hidden_kwargs(**kwargs))


def patch_subprocess_hidden() -> None:
    """Глобально: любой subprocess.run/Popen в GUI без окна (powershell/git/cmd)."""
    global _patched
    if _patched or sys.platform != "win32" or not is_gui_host():
        return
    _patched = True
    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen

    def _run(*args, **kwargs):
        return _orig_run(*args, **hidden_kwargs(**kwargs))

    class _Popen(_orig_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **hidden_kwargs(**kwargs))

    subprocess.run = _run  # type: ignore[assignment]
    subprocess.Popen = _Popen  # type: ignore[misc,assignment]
