"""Проверка: после SubHub.exe нет видимых powershell/cmd/cscript от дерева pythonw."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import winproc  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _cim_processes() -> list[dict]:
    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", ps],
        capture_output=True, text=True, timeout=30,
        creationflags=NO_WINDOW,
    )
    if not r.stdout.strip():
        return []
    import json
    raw = json.loads(r.stdout)
    rows = raw if isinstance(raw, list) else [raw]
    return [x for x in rows if x]


def _kill_subhub() -> None:
    for row in _cim_processes():
        name = (row.get("Name") or "").lower()
        cl = (row.get("CommandLine") or "")
        if name == "subhub.exe" or (
            name in ("pythonw.exe", "python.exe")
            and ("app.py" in cl or "_gui_boot" in cl or "flipkart-automation-master 3" in cl)
        ):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(row["ProcessId"]), "/T", "/F"],
                    capture_output=True, timeout=8, creationflags=NO_WINDOW,
                )
            except Exception:
                pass


def main() -> int:
    os.chdir(ROOT)
    _kill_subhub()
    time.sleep(1.0)

    exe = ROOT / "SubHub.exe"
    if not exe.exists():
        print("FAIL: no SubHub.exe")
        return 1

    subprocess.Popen(
        [str(exe)], cwd=str(ROOT),
        creationflags=NO_WINDOW,
    )
    time.sleep(2.5)

    root_pid = None
    for row in _cim_processes():
        cl = row.get("CommandLine") or ""
        if (row.get("Name") or "").lower() == "pythonw.exe" and (
            "_gui_boot" in cl or "app.py" in cl
        ):
            root_pid = int(row["ProcessId"])
            break
    if not root_pid:
        print("FAIL: pythonw not started")
        crash = ROOT / "data" / "launch_error.log"
        if crash.exists():
            print(crash.read_text(encoding="utf-8", errors="replace")[:500])
        return 1
    print(f"pythonw={root_pid}")

    by_id = {int(r["ProcessId"]): r for r in _cim_processes() if r.get("ProcessId")}

    def is_desc(pid: int) -> bool:
        guard = 0
        while pid and guard < 16:
            if pid == root_pid:
                return True
            row = by_id.get(pid)
            if not row:
                return False
            pid = int(row.get("ParentProcessId") or 0)
            guard += 1
        return False

    bad_names = {"cmd.exe", "powershell.exe", "pwsh.exe", "cscript.exe", "wscript.exe", "git.exe"}
    flashes: list[str] = []
    deadline = time.time() + 12.0
    seen: set[int] = set()
    while time.time() < deadline:
        by_id = {int(r["ProcessId"]): r for r in _cim_processes() if r.get("ProcessId")}
        for pid, row in by_id.items():
            name = (row.get("Name") or "").lower()
            if name not in bad_names:
                continue
            if pid in seen:
                continue
            if not is_desc(pid):
                continue
            seen.add(pid)
            cl = (row.get("CommandLine") or "")[:200]
            flashes.append(f"{pid} {name} :: {cl}")
        time.sleep(0.25)

    if flashes:
        print("FAIL flashes:")
        for f in flashes:
            print(" ", f)
        return 1
    print("PASS: no cmd/powershell/cscript/git children under SubHub pythonw")
    print("GIT prefer", __import__("menu")._GIT)
    print("gui_host", winproc.is_gui_host())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
