"""Self-check: ротация debug/ — свежая диагностика остаётся, старая уходит."""
from __future__ import annotations

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
    real_here = m._HERE
    m._HERE = tmp                                  # не трогаем настоящий debug/
    try:
        now = time.time()
        # 60 свежих файлов + 5 старых (10 дней) во вложенной папке
        fresh = []
        for i in range(60):
            f = tmp / "debug" / f"shot_{i:03}.png"
            f.write_bytes(b"x" * 1000)
            import os
            os.utime(f, (now - i, now - i))        # i=0 самый новый
            fresh.append(f)
        old = []
        for i in range(5):
            f = tmp / "debug" / "deepseek" / f"old_{i}.png"
            f.write_bytes(b"y" * 2000)
            import os
            os.utime(f, (now - 10 * 86400, now - 10 * 86400))
            old.append(f)

        freed = m._rotate_debug_dir(keep=50, max_age_days=7.0)
        # 10 лишних свежих (по 1000) + 5 старых (по 2000)
        assert freed == 10 * 1000 + 5 * 2000, freed
        for f in fresh[:50]:
            assert f.exists(), f"удалён свежий файл из первых 50: {f.name}"
        for f in fresh[50:]:
            assert not f.exists(), f"лишний свежий файл остался: {f.name}"
        for f in old:
            assert not f.exists(), f"старый файл остался: {f.name}"

        # Повторный прогон уже ничего не освобождает
        assert m._rotate_debug_dir(keep=50, max_age_days=7.0) == 0
        # Старое удаляется, даже если файлов меньше keep
        f_old = tmp / "debug" / "ancient.png"
        f_old.write_bytes(b"z" * 500)
        import os
        os.utime(f_old, (now - 30 * 86400, now - 30 * 86400))
        assert m._rotate_debug_dir(keep=50, max_age_days=7.0) == 500
        assert not f_old.exists()

        # Нет каталога — не падаем
        m._HERE = Path(tempfile.mkdtemp(prefix="nodbg_"))
        assert m._rotate_debug_dir() == 0
    finally:
        m._HERE = real_here

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    i = src.find("def _startup_cleanup")
    assert "_rotate_debug_dir()" in src[i: i + 1500], "ротация не вызывается на старте"

    print("PASS debug_rotation")


if __name__ == "__main__":
    main()
