"""Self-check: OTA раздаёт всё, без чего обновлённый код не запустится.

Обновление скачивает фиксированный список файлов (_UPDATE_FILES). Когда из
menu.py выносили модули, список не пополнили — клиент получил бы новый
menu.py без console_ui/common/gift_cards и упал бы на импорте при старте.
Тест ловит это для любого будущего выноса.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

PKG = ROOT / "subhub"


def _local_imports(py: Path) -> set[str]:
    """Имена модулей из subhub/, которые импортирует файл (плоские импорты)."""
    tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            names.add(n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add(a.name.split(".")[0])
    return {n for n in names if (PKG / f"{n}.py").exists()}


def main() -> None:
    import menu as m

    listed = set(m._UPDATE_FILES)

    # 1. Всё перечисленное существует — иначе обновление скачает 404
    missing = [f for f in listed if not (ROOT / f).exists()]
    assert not missing, f"в списке OTA файлы, которых нет в репозитории: {missing}"

    # 2. Транзитивно: всё, что импортируют раздаваемые модули, тоже раздаётся
    seen: set[str] = set()
    queue = [f for f in listed if f.endswith(".py")]
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for mod in _local_imports(ROOT / rel):
            target = f"subhub/{mod}.py"
            assert target in listed, (
                f"{rel} импортирует {mod}, но {target} не раздаётся по OTA — "
                f"после обновления клиент упадёт на импорте")
            if target not in seen:
                queue.append(target)

    # 3. Ключевые модули на месте
    for must in ("subhub/menu.py", "subhub/console_ui.py", "subhub/common.py",
                 "subhub/gift_cards.py", "subhub/housekeeping.py",
                 "subhub/cookie_restore.py", "VERSION"):
        assert must in listed, must

    print("PASS ota_file_list")


if __name__ == "__main__":
    main()
