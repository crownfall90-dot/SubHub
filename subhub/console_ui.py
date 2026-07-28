"""Консольный UI: цвета ANSI и примитивы вывода.

Вынесено из menu.py, чтобы экраны можно было выносить в отдельные модули, не
таща за собой весь monolith: раньше любой вынесенный экран пришлось бы
импортировать из menu.py ради `header`/`opt`/`RST`, а menu.py импортирует его
самого — циклический импорт.

`pause()` живёт здесь же, но состояние «идёт выключение» принадлежит menu.py:
он выставляет его через `set_shutting_down()` в обработчике остановки.
"""
from __future__ import annotations

# ── ANSI ──────────────────────────────────────────────────────────────────────
R   = "\033[91m"   # красный
G   = "\033[92m"   # зелёный
Y   = "\033[93m"   # жёлтый
C   = "\033[96m"   # голубой
M   = "\033[95m"   # фиолетовый
B   = "\033[94m"   # синий
W   = "\033[97m"   # белый
DIM = "\033[90m"   # серый
BLD = "\033[1m"    # жирный
RST = "\033[0m"    # сброс

# Во время выключения ввод не запрашиваем: консоль уже гасится, а input()
# в этот момент вешает процесс. Флаг ставит menu.py.
_state = {"shutting_down": False}


def set_shutting_down(value: bool = True) -> None:
    _state["shutting_down"] = bool(value)


def is_shutting_down() -> bool:
    return _state["shutting_down"]


def cls() -> None:
    import os
    os.system("cls" if os.name == "nt" else "clear")


def pause(msg: str = "  Нажмите Enter для продолжения...") -> None:
    if _state["shutting_down"]:
        return
    try:
        input(f"\n{DIM}{msg}{RST}")
    except (KeyboardInterrupt, EOFError):
        pass


def header(title: str = "LOGIN AUTOMATION  ──  PROFILE MANAGER", color: str = C) -> None:
    print()
    W_ = 54
    line = "═" * W_
    pad = (W_ - len(title)) // 2
    print(f"{color}{BLD}  ╔{line}╗{RST}")
    print(f"{color}{BLD}  ║{' ' * pad}{title}{' ' * (W_ - pad - len(title))}║{RST}")
    print(f"{color}{BLD}  ╚{line}╝{RST}")
    print()


def section(title: str, color: str = DIM) -> None:
    print(f"\n{color}  ┌─ {title} {'─' * max(0, 44 - len(title))}┐{RST}")


def opt(key: str, label: str, color: str = W) -> None:
    print(f"  {BLD}{Y}[{key}]{RST}  {color}{label}{RST}")
