"""Точка входа для SubHub.exe — ловит краши до и внутри app.main()."""
from __future__ import annotations

import os
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SUBHUB_LAUNCHED_BY", "SubHub.exe")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_CRASH = ROOT / "data" / "pythonw_crash.log"


def _write_crash(text: str) -> None:
    try:
        _CRASH.parent.mkdir(parents=True, exist_ok=True)
        _CRASH.write_text(text, encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    try:
        import winproc
        winproc.patch_subprocess_hidden()
    except Exception:
        pass
    try:
        import app
        app.main()
    except SystemExit:
        raise
    except BaseException:
        _write_crash(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
