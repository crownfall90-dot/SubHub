"""USA VPN → Manage Addresses → заполнить все поля (телефон профиля)."""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
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
    import random

    phone = sys.argv[1] if len(sys.argv) > 1 else "919794820750"
    phone10 = "".join(ch for ch in phone if ch.isdigit())[-10:]
    target = None
    for p in m._load_done_profiles(force=True):
        if phone in str(p.get("username") or "") or phone in str(p["path"]):
            target = p
            break
    if not target:
        print("NO_PROFILE")
        return 2

    path = Path(target["path"])
    print(f"PROFILE {target.get('username')} fill phone={phone10}", flush=True)
    m._kill_chrome_for_profile(path)
    m._clear_stale_profile_locks(path)
    m.set_automation_proc(os.getpid(), mode="fill-addr", owner="agent")
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
        await ctx.grant_permissions(["geolocation"], origin="https://www.flipkart.com")

        print("VPN…", flush=True)
        ok_vpn = await m._ensure_vpn_connected(ctx, quick=False, flipkart=True)
        print(f"VPN ok={ok_vpn}", flush=True)
        await m._dismiss_all_veepn_welcome(ctx)
        await m._close_vpn_extension_tabs(ctx, await m._vpn_ext_id(ctx))

        page = await m._main_work_page(ctx)
        ok, page, err = await m._navigate_flipkart_resilient(
            ctx, page, "https://www.flipkart.com/account/addresses",
            label=phone10, profile_path=path,
        )
        print(f"addresses nav ok={ok} err={err!r} url={(page.url if page else '')[:90]}", flush=True)
        if not ok or not page:
            return 1

        await page.wait_for_timeout(1200)
        # Открыть форму ADD A NEW ADDRESS
        with contextlib.suppress(Exception):
            await page.wait_for_selector("text=/ADD\\s+A\\s+NEW\\s+ADDRESS/i", timeout=12_000)
        ready = await m._membership_oos_form_ready(page)
        if not ready:
            loc = page.get_by_text(re.compile(r"ADD\s+A\s+NEW\s+ADDRESS", re.I)).first
            if await loc.count() > 0:
                await loc.scroll_into_view_if_needed()
                box = await loc.bounding_box()
                if box:
                    await page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                else:
                    await loc.click(force=True)
                await page.wait_for_timeout(1500)
            ready = await m._membership_oos_form_ready(page)
        print(f"form ready={ready}", flush=True)

        a = m._gen_indian_address()
        a["phone"] = phone10
        a["locality"] = random.choice(m._IND_AREAS)
        a["address_line"] = f"{a['house']}, {a['road']}"
        for pin, city, state in m._IND_PINCODES:
            if str(city).lower() == "lucknow" or str(pin).startswith("226"):
                a["pincode"], a["city"], a["state"] = pin, city, state
                break
        print(f"fill {a['name']} | {a['pincode']} {a['city']} | phone={phone10}", flush=True)
        ok_fill = await m._fill_address_form(page, a)
        print(f"SAVE ok={ok_fill} url={page.url[:100]}", flush=True)
        await page.wait_for_timeout(2500)
        return 0 if ok_fill else 1
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
