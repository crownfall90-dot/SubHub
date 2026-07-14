"""Если Flipkart не открывается — включить VPN (любая free) и открыть сайт."""
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
        if phone in str(p.get("username") or "") or phone in str(p["path"]):
            target = p
            break
    if not target:
        for p in m._load_done_profiles(force=True):
            if p.get("login_ts") and not p.get("issued_ts"):
                target = p
                break
    if not target:
        print("NO_PROFILE")
        return 2

    path = Path(target["path"])
    print(f"PROFILE {target.get('username')} {path}", flush=True)
    m._kill_chrome_for_profile(path)
    m._clear_stale_profile_locks(path)
    m.set_automation_proc(os.getpid(), mode="vpn-fk", owner="agent")
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

        print("Шаг1: если ВЫКЛ — жмём кнопку → ждём ВКЛ…", flush=True)
        ok_vpn = await m._ensure_vpn_connected(ctx, quick=False, flipkart=True)
        print(f"VPN ok={ok_vpn}", flush=True)
        if not ok_vpn:
            print("STOP: VPN не ВКЛ — на Flipkart не заходим", flush=True)
            await asyncio.sleep(120)
            return 1
        await m._dismiss_all_veepn_welcome(ctx)
        await m._close_vpn_extension_tabs(ctx, await m._vpn_ext_id(ctx))

        print("Шаг2: Соединение ВКЛ — открываю Flipkart…", flush=True)
        page = await m._main_work_page(ctx)
        ok, page, err = await m._navigate_flipkart_resilient(
            ctx, page, "https://www.flipkart.com",
            label=m._phone_from_path(path), profile_path=path,
        )
        print(f"FLIPKART ok={ok} err={err} url={(page.url if page else '')[:80]}", flush=True)
        if ok:
            page = await m._keep_only_flipkart_tabs(ctx, prefer_page=page)
        # держим браузер ~3 мин чтобы увидеть результат
        await asyncio.sleep(180)
        return 0 if ok else 1
    finally:
        m._unregister_purchase_profile(path)
        m.clear_automation_proc()
        # не закрываем браузер сразу — пользователь смотрит
        with contextlib.suppress(Exception):
            await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
