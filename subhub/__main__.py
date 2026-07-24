"""python -m subhub  →  консоль menu.py"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_ROOT = _PKG.parent

# Плоские импорты: import menu / grizzly / ggsell из subhub/, не из корня.
# ROOT нужен для редких импортов scripts/; PKG всегда первым.
_root_s, _pkg_s = str(_ROOT), str(_PKG)
if _root_s in sys.path:
    sys.path.remove(_root_s)
if _pkg_s in sys.path:
    sys.path.remove(_pkg_s)
sys.path.insert(0, _root_s)
sys.path.insert(0, _pkg_s)

# config.yaml, data/, chrome_profiles* — в корне репо
os.chdir(_ROOT)

runpy.run_module("menu", run_name="__main__")
