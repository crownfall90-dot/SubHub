"""VPN → Flipkart homepage (без покупки)."""
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

    phone = sys.argv[1] if len(sys.argv) > 1 else "919794820750"
    target = None
    for p in m._load_done_profiles(force=True):
        key = str(p.get("username") or "") + str(p["path"])
        if phone in key:
            target = p
            break
    if not target:
        print("NO_PROFILE")
        return 2

    path = Path(target["path"])
    print(f"PROFILE {phone} {path}", flush=True)
    m._kill_chrome_for_profile(path)
    m._clear_stale_profile_locks(path)
    m.set_automation_proc(os.getpid(), mode="veepn-fk", owner="agent")
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

        ok_vpn = await m._vpn_fresh_connect_usa(ctx, path, quick=True)
        eid = await m._vpn_ext_id(ctx)
        proxy = bool(eid and await m._vpn_is_proxy_active(ctx, eid))
        print(f"VPN ok={ok_vpn} proxy={proxy}", flush=True)
        if not (ok_vpn and proxy):
            print("RESULT FLIPKART=skip VPN_FAIL")
            return 1

        ok, page, err = await m._navigate_flipkart_resilient(
            ctx, page, "https://www.flipkart.com",
            label=phone, profile_path=path,
        )
        url = (page.url if page else "")[:120]
        blocked = bool(page and await m._flipkart_page_blocked(page))
        print(f"RESULT FLIPKART ok={ok} blocked={blocked} err={err!r} url={url}", flush=True)
        await asyncio.sleep(8)
        return 0 if ok and not blocked else 1
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
