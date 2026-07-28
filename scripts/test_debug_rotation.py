"""Self-check: ротация debug/ — свежая диагностика остаётся, старая уходит.

Путь передаётся явным аргументом `root`. Раньше тест подменял `menu._HERE`;
после выноса функции в housekeeping.py подмена стала незаметным no-op, и
ротация ушла чистить настоящий debug/. Явный аргумент делает это невозможным.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu as m

    tmp = Path(tempfile.mkdtemp(prefix="dbgrot_"))
    (tmp / "debug").mkdir()
    (tmp / "debug" / "deepseek").mkdir()          # вложенные каталоги тоже чистим
    now = time.time()

    fresh = []
    for i in range(60):
        f = tmp / "debug" / f"shot_{i:03}.png"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (now - i, now - i))           # i=0 самый новый
        fresh.append(f)
    old = []
    for i in range(5):
        f = tmp / "debug" / "deepseek" / f"old_{i}.png"
        f.write_bytes(b"y" * 2000)
        os.utime(f, (now - 10 * 86400, now - 10 * 86400))
        old.append(f)

    freed = m._rotate_debug_dir(keep=50, max_age_days=7.0, root=tmp)
    assert freed == 10 * 1000 + 5 * 2000, freed   # 10 лишних свежих + 5 старых
    for f in fresh[:50]:
        assert f.exists(), f"удалён свежий файл из первых 50: {f.name}"
    for f in fresh[50:]:
        assert not f.exists(), f"лишний свежий файл остался: {f.name}"
    for f in old:
        assert not f.exists(), f"старый файл остался: {f.name}"

    # Повторный прогон уже ничего не освобождает
    assert m._rotate_debug_dir(keep=50, max_age_days=7.0, root=tmp) == 0

    # Старое удаляется, даже если файлов меньше keep
    f_old = tmp / "debug" / "ancient.png"
    f_old.write_bytes(b"z" * 500)
    os.utime(f_old, (now - 30 * 86400, now - 30 * 86400))
    assert m._rotate_debug_dir(keep=50, max_age_days=7.0, root=tmp) == 500
    assert not f_old.exists()

    # Нет каталога — не падаем
    assert m._rotate_debug_dir(root=Path(tempfile.mkdtemp(prefix="nodbg_"))) == 0

    # Тест обязан уметь задавать корень, иначе он чистит настоящий debug/
    import inspect
    assert "root" in inspect.signature(m._rotate_debug_dir).parameters

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    i = src.find("def _startup_cleanup")
    assert "_rotate_debug_dir()" in src[i: i + 1500], "ротация не вызывается на старте"

    print("PASS debug_rotation")


if __name__ == "__main__":
    main()
