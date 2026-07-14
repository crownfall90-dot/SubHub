"""Quick check for Win32 cmdline enumeration + activate helpers."""
from __future__ import annotations

import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app


def main() -> None:
    t0 = time.perf_counter()
    pids = app._collect_subhub_gui_pids()
    dt = time.perf_counter() - t0
    print(f"collect_pids={pids} in {dt:.3f}s")
    if dt > 1.5:
        print("FAIL: PID scan too slow")
        sys.exit(1)
    assert app._launcher_path().name.lower() in {"subhub.exe", "app_launch.vbs", "app.bat"}
    boot = ROOT / "scripts" / "_gui_boot.py"
    assert boot.exists(), "missing scripts/_gui_boot.py"
    exe = ROOT / "SubHub.exe"
    assert exe.exists(), "missing SubHub.exe — run scripts/build_subhub_exe.bat"
    print("OK launch helpers")


if __name__ == "__main__":
    main()
