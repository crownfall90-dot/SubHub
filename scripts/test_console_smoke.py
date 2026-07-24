"""Console startup smoke test — verifies the console boots after the GUI removal.

Run: python scripts/test_console_smoke.py   (exits non-zero on first failure)

Checks that the core modules import, the dependency/Chromium check passes with a
normal Google Chrome install, the Telegram bot can be started under the console
owner (no leftover 'blocked_by_app' from the old desktop app), and that the key
console entry points still exist. Does NOT open the interactive menu.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(name)


def main() -> int:
    print("Console startup smoke test\n")

    # 1. No GUI leftovers
    check("app.py removed", not (ROOT / "app.py").exists())
    check("single launcher menu.bat", (ROOT / "menu.bat").exists())

    # 2. Core modules import
    try:
        import menu  # noqa: F401
        import bot   # noqa: F401
        import main  # noqa: F401
        check("import menu/bot/main", True)
    except Exception as exc:  # pragma: no cover
        check("import menu/bot/main", False, repr(exc))
        return _finish()  # can't continue without imports

    # 3. Dependency / browser check (Google Chrome is enough — no bundled Chromium)
    check("Google Chrome found", bool(menu._find_chrome()), str(menu._find_chrome()))
    check("_chromium_ok() true", menu._chromium_ok())
    ok, msg = menu.ensure_dependencies()
    check("ensure_dependencies() ok", ok, msg)

    # 4. Host coordination is console-only (GUI 'app' host gone)
    check("_host_kind() == console", menu._host_kind() == "console")
    check("restart_target_label() == консоль", menu.restart_target_label() == "консоль")
    check("dead register_host_restart removed",
          not hasattr(menu, "register_host_restart"))

    # 5. Telegram bot starts under console owner (never 'blocked_by_app')
    status = bot.ensure_tg_bot("console")
    check("ensure_tg_bot not blocked_by_app", status != "blocked_by_app", status)
    check("ensure_tg_bot valid status",
          status in ("started", "active", "no_token"), status)

    # 6. Key console entry points still exist
    for fn in ("screen_main", "screen_restore_from_cookies", "screen_logs",
               "screen_used", "_do_git_update", "_http_check_updates",
               "restore_archive_record"):
        check(f"menu.{fn} exists", hasattr(menu, fn))

    return _finish()


def _finish() -> int:
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): {', '.join(_failures)}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
