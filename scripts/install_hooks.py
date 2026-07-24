"""Устанавливает git-хуки из scripts/git-hooks/ в .git/hooks/.

Запуск: python scripts/install_hooks.py
"""
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "git-hooks"
DST = ROOT / ".git" / "hooks"


def main() -> int:
    if not DST.parent.exists():
        print("Не найден .git — запустите из корня репозитория SubHub.")
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    installed = []
    for hook in SRC.iterdir():
        if not hook.is_file():
            continue
        target = DST / hook.name
        shutil.copyfile(hook, target)
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(hook.name)
    if installed:
        print(f"Установлены хуки: {', '.join(installed)}")
    else:
        print("Нет хуков для установки в scripts/git-hooks/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
