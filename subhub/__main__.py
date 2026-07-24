"""python -m subhub  →  консоль menu.py"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_ROOT = _PKG.parent

# Плоские импорты: import menu / grizzly / ggsell
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# config.yaml, data/, chrome_profiles* — в корне репо
os.chdir(_ROOT)

runpy.run_module("menu", run_name="__main__")
