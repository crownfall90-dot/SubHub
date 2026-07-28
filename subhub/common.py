"""Мелкие общие помощники без зависимостей от логики проекта.

Отсюда берут атомарную запись, размер каталога и формат времени МСК все
вынесенные из menu.py модули. Модуль намеренно ничего не импортирует из
menu.py — иначе вынос экранов упирается в циклический импорт.
"""
from __future__ import annotations

import contextlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))


def atomic_write_text(path, text: str) -> None:
    """Атомарная запись текста: пишем во временный файл рядом, fsync и заменяем
    целевой через os.replace. Защищает от обрезанного/битого файла, если процесс
    убьют во время записи (например os._exit(42) при перезапуске консоли)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as _f:
        _f.write(text)
        _f.flush()
        os.fsync(_f.fileno())
    os.replace(tmp, path)


def dir_size(path) -> int:
    """Суммарный размер каталога в байтах (0 — если нет или недоступен)."""
    total = 0
    with contextlib.suppress(OSError):
        for p in Path(path).rglob("*"):
            with contextlib.suppress(OSError):
                if p.is_file():
                    total += p.stat().st_size
    return total


def fmt_msk(ts: float) -> str:
    """Unix timestamp → строка даты-времени по московскому времени (UTC+3)."""
    return datetime.fromtimestamp(ts, tz=MSK).strftime("%d.%m.%Y  %H:%M  МСК")
