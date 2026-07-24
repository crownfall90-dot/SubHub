"""Project and package roots for SubHub.

ROOT  — репозиторий (config.yaml, data/, chrome_profiles*, secrets)
PKG   — пакет subhub/ (menu.py, main.py, …)
"""
from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
