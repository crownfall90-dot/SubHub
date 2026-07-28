"""Self-check: _cookie_backup_state — оценка бэкапа куков по JWT без браузера."""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def _jwt(exp_ts: float) -> str:
    pl = base64.urlsafe_b64encode(json.dumps({"exp": int(exp_ts)}).encode()).decode().rstrip("=")
    return f"hdr.{pl}.sig"


def _write(cookies: dict) -> Path:
    p = Path(tempfile.mkdtemp()) / "cookies_0000000000.json"
    p.write_text(json.dumps([{"name": k, "value": v} for k, v in cookies.items()]),
                 encoding="utf-8")
    return p


def main() -> None:
    import menu as m

    now = time.time()
    # Живой бэкап: at ещё не истёк
    st, d = m._cookie_backup_state(_write({"at": _jwt(now + 900), "rt": _jwt(now + 86400 * 90)}))
    assert st == "ok", (st, d)
    # at живёт ~30 мин — просроченный at это норма, поднимаем через rt
    st, d = m._cookie_backup_state(_write({"at": _jwt(now - 3600), "rt": _jwt(now + 86400 * 90)}))
    assert st == "at_expired", (st, d)
    assert "rt действителен" in d, d
    # rt истёк → мёртво, браузер запускать не надо
    st, d = m._cookie_backup_state(_write({"at": _jwt(now - 3600), "rt": _jwt(now - 60)}))
    assert st == "dead", (st, d)
    # нет rt вовсе → мёртво
    st, d = m._cookie_backup_state(_write({"at": _jwt(now + 900)}))
    assert st == "dead" and "нет rt" in d, (st, d)
    # пустой / битый JSON
    empty = Path(tempfile.mkdtemp()) / "c.json"
    empty.write_text("[]", encoding="utf-8")
    assert m._cookie_backup_state(empty)[0] == "dead"
    assert m._cookie_backup_state(Path(tempfile.mkdtemp()) / "нет.json")[0] == "unknown"

    # Мёртвый бэкап отсекается до запуска Chrome
    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    r_idx = src.find("async def _restore_profile_from_cookies")
    assert 0 < src.find("_cookie_backup_state", r_idx) < src.find("launch_persistent_context", r_idx)

    print("PASS cookie_backup_state")


if __name__ == "__main__":
    main()
