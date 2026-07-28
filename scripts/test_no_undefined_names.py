"""Self-check: в проекте нет обращений к несуществующим именам.

Ловит класс ошибок, который обычные тесты пропускают: функция уехала в другой
модуль, а вызов остался (так сломались _mask_gift и runtime_touch при выносе).
Плюс динамические обращения — bot.py достаёт функции menu.py через _m("имя"),
такой вызов не виден ни линтеру, ни импорту.
"""
from __future__ import annotations

import ast
import builtins
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

PKG = ROOT / "subhub"


def _undefined(py: Path) -> dict:
    tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
    known = set(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                  "__spec__", "__package__"}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            known.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            known.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                known.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.arg):
            known.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            known.add(n.name)
        elif isinstance(n, ast.Global):
            known.update(n.names)
    return dict(collections.Counter(
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in known))


def main() -> None:
    files = sorted(list(PKG.glob("*.py")) + list((PKG / "ggsell").glob("*.py")))
    assert files, "модули не найдены"
    problems = {str(f.relative_to(ROOT)): u for f in files if (u := _undefined(f))}
    assert not problems, f"обращения к несуществующим именам: {problems}"

    # Динамика: bot.py достаёт функции menu.py по строковому имени
    import menu
    bot_src = (PKG / "bot.py").read_text(encoding="utf-8", errors="replace")
    names = sorted(set(re.findall(r'_m\(\s*["\']([A-Za-z_]\w*)["\']', bot_src)))
    assert names, "не нашёл ни одного _m(\"...\") — проверка потеряла смысл"
    missing = [n for n in names if not hasattr(menu, n)]
    assert not missing, f"bot.py просит у menu.py то, чего нет: {missing}"

    # main.py и ggsell обращаются к menu через _menu.X
    for f in files:
        src = f.read_text(encoding="utf-8", errors="replace")
        gone = [a for a in set(re.findall(r"\b_menu\.([A-Za-z_]\w*)", src))
                if not hasattr(menu, a)]
        assert not gone, f"{f.name}: _menu.{gone} больше нет в menu.py"

    print(f"PASS no_undefined_names ({len(files)} модулей, {len(names)} динамических имён)")


if __name__ == "__main__":
    main()
