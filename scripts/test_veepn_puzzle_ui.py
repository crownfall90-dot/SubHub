"""Прогон: maximize → пазл → VeePN → USA → круг → Подключено."""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

for s in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        s.reconfigure(encoding="utf-8", errors="replace")


async def main() -> int:
    import menu as m

    phone = sys.argv[1] if len(sys.argv) > 1 else ""
    target = None
    for p in m._load_done_profiles(force=True):
        key = str(p.get("username") or "") + str(p["path"])
        if phone and phone in key:
            target = p
            break
        if not phone and p.get("login_ts") and not p.get("issued_ts"):
            target = p
            break
    if not target:
        print("NO_PROFILE")
        return 2

    path = Path(target["path"])
    phone = target.get("username") or m._phone_from_path(path)
    print(f"PROFILE {phone} {path}", flush=True)
    m._kill_chrome_for_profile(path)
    m._clear_stale_profile_locks(path)
    m.set_automation_proc(os.getpid(), mode="veepn-puzzle", owner="agent")
    m._register_purchase_profile(path)

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    ctx = None
    try:
        m._pre_inject_chrome_prefs(path)
        ctx = await pw.chromium.launch_persistent_context(
            str(path.resolve()),
            **m._browser_launch_kw(phone=m._phone_from_path(path), profile_path=path),
        )
        await m._close_extension_startup_tabs(ctx)
        page = await m._main_work_page(ctx)
        await m._maximize_window(ctx, page)
        with contextlib.suppress(Exception):
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=10_000)
        await page.wait_for_timeout(800)

        print("TEST: fresh USA via UI (пазл→страна→круг)…", flush=True)
        ok = await m._vpn_fresh_connect_usa(ctx, path, quick=True)
        eid = await m._vpn_ext_id(ctx)
        proxy = bool(eid and await m._vpn_is_proxy_active(ctx, eid))
        print(f"RESULT ok={ok} proxy={proxy}", flush=True)
        if not (ok and proxy):
            print("FAIL: need ok=True AND proxy=True", flush=True)
            return 1
        # Держим ~15с — видно, что подключено
        await asyncio.sleep(15)
        return 0
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}", flush=True)
        return 1
    finally:
        m.clear_automation_proc()
        if ctx:
            with contextlib.suppress(Exception):
                await m._close_browser_session(ctx, pw, path, disconnect_vpn=True)
        else:
            m._unregister_purchase_profile(path)
            with contextlib.suppress(Exception):
                await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
