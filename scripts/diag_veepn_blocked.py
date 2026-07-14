"""Диагностика: ERR_BLOCKED на VeepN popup + load-extension."""
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

    ext = m._vpn_extension_dir()
    eid = m._vpn_ext_id_for_install()
    print(f"ext={ext}")
    print(f"eid={eid}")
    print(f"paths={m._vpn_popup_rel_paths()}")
    bad = f"chrome-extension://{eid}/popup.html"
    good = f"chrome-extension://{eid}/src/popup/popup.html"
    print(f"bad_url_exists_file={(Path(ext)/'popup.html').exists() if ext else None}")
    print(f"good_url_exists_file={(Path(ext)/'src/popup/popup.html').exists() if ext else None}")

    phone = sys.argv[1] if len(sys.argv) > 1 else "919794820750"
    target = None
    for p in m._load_done_profiles(force=True):
        if phone in str(p.get("username") or "") + str(p["path"]):
            target = p
            break
    if not target:
        print("NO_PROFILE")
        return 2
    path = Path(target["path"])
    m._kill_chrome_for_profile(path)
    m._clear_stale_profile_locks(path)
    m._install_extension_filesystem(path)
    m._pre_inject_chrome_prefs(path)
    m.set_automation_proc(os.getpid(), mode="diag-ext", owner="agent")
    m._register_purchase_profile(path)

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    ctx = None
    try:
        ctx = await pw.chromium.launch_persistent_context(
            str(path.resolve()),
            **m._browser_launch_kw(phone=m._phone_from_path(path), profile_path=path),
        )
        await m._close_extension_startup_tabs(ctx)
        live_eid = await m._vpn_ext_id(ctx)
        print(f"live_eid={live_eid}")

        # 1) Bad path must not be treated as popup
        page = await ctx.new_page()
        with contextlib.suppress(Exception):
            await page.goto(bad, wait_until="domcontentloaded", timeout=10_000)
        await page.wait_for_timeout(800)
        title = ""
        body = ""
        with contextlib.suppress(Exception):
            title = await page.title()
            body = await page.evaluate(
                "() => (document.body && document.body.innerText || '').slice(0, 400)"
            )
        blocked = m._page_shows_client_block(page.url or "", title, body)
        is_pop = m._is_extension_popup_url(page.url or "", eid or "")
        print(f"BAD url={(page.url or '')[:80]!r} blocked={blocked} is_popup={is_pop}")
        print(f"BAD title={title[:60]!r} body={body[:80]!r}")
        with contextlib.suppress(Exception):
            await page.close()

        # 2) Good path / open helper
        pop = await m._open_extension_popup_page(ctx, live_eid or eid, m._veepn_popup_rel_paths())
        ready = bool(pop and await m._veepn_popup_ui_ready(pop))
        print(f"GOOD open ready={ready} url={(pop.url if pop else '')[:90]!r}")

        # 3) Service worker / proxy status
        sw = await m._wait_vpn_service_worker(ctx, live_eid or eid or "", timeout=8.0)
        print(f"service_worker={bool(sw)}")
        proxy = bool(live_eid and await m._vpn_is_proxy_active(ctx, live_eid))
        print(f"proxy_active={proxy}")

        ok = (not is_pop) and (blocked or "popup.html" in bad) and ready and bool(sw)
        print(f"RESULT ok={ok}")
        await asyncio.sleep(3)
        return 0 if ok else 1
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}")
        return 1
    finally:
        m.clear_automation_proc()
        if ctx:
            with contextlib.suppress(Exception):
                await m._close_browser_session(ctx, pw, path, disconnect_vpn=False)
        else:
            m._unregister_purchase_profile(path)
            with contextlib.suppress(Exception):
                await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
