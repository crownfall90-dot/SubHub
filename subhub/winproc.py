"""Windows: subprocess без мелькающих окон консоли (PowerShell, git, pip, wmic)."""
from __future__ import annotations

import subprocess
import sys
from typing import Any


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


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
