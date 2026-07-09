"""
proxy.py — утилиты профилей (прокси удалены из проекта).
"""

from pathlib import Path


def _phone_from_path(profile_path: Path) -> str:
    """Извлекает 10-значный номер из папки профиля (profile_9876543210 → 9876543210)."""
    name = profile_path.name
    parts = name.rsplit("_", 1)
    if len(parts) > 1:
        candidate = parts[-1]
        if candidate.isdigit():
            return candidate
    return ""
