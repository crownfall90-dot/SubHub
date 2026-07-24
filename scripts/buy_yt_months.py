"""Купить YouTube Premium / Flipkart Black на N месяцев через готовый профиль."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

# Windows cp125x падает на emoji в print() — без этого покупка рвётся как «ошибка»
for _stream in (sys.stdout, sys.stderr):
    with __import__("contextlib").suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    months = 3
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        months = int(sys.argv[1])

    import menu as m

    m._start_log_tee()
    m.set_automation_proc(os.getpid(), mode=f"buy-{months}m", owner="agent")
    try:
        target = None
        for p in m._load_done_profiles(force=True):
            if p.get("issued_ts") or p.get("prepared_ts"):
                continue
            if p.get("status") in ("activated", "explore_now", "activate_now"):
                continue
            if p.get("login_ts"):
                target = p
                break
        if not target:
            print("NO_CANDIDATE — нет свободного профиля с логином", flush=True)
            return 2

        path = Path(target["path"])
        print(f"TARGET {target.get('username')} {path}", flush=True)
        print(
            f"PAY {m._load_pay_method()} GIFT {m._gift_balance()} MONTHS {months}",
            flush=True,
        )
        m._kill_chrome_for_profile(path)
        m._clear_stale_profile_locks(path)

        async def _run():
            return await m._do_buy_membership(path, months, card=None)

        ok, msg = asyncio.run(_run())
        print(f"RESULT ok={ok} msg={msg}", flush=True)
        return 0 if ok else 1
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}", flush=True)
        return 1
    finally:
        m.clear_automation_proc()


if __name__ == "__main__":
    raise SystemExit(main())
