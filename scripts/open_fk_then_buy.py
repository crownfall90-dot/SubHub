"""USA VPN → Flipkart OK → покупка. При OUT_OF_STOCK — следующий доступный профиль."""
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


def _available_profiles(m, prefer_phone: str = "") -> list[dict]:
    """Сначала prefer_phone, потом остальные доступные (login, не выдан)."""
    all_p = m._load_done_profiles(force=True)
    preferred, rest = [], []
    for p in all_p:
        if p.get("issued_ts") or p.get("prepared_ts"):
            continue
        if p.get("status") in ("activated", "explore_now", "activate_now"):
            continue
        if not p.get("login_ts"):
            continue
        key = str(p.get("username") or "") + str(p["path"])
        if prefer_phone and prefer_phone in key:
            preferred.append(p)
        else:
            rest.append(p)
    return preferred + rest


async def _open_flipkart_with_vpn(m, ctx, path: Path, *, max_rounds: int = 6) -> tuple[bool, object]:
    """VPN → Flipkart. Сначала проверенный путь: USA UI (карточка→список→питание)."""
    phone = m._phone_from_path(path)

    print("VPN: USA UI first…", flush=True)
    ok_vpn = await m._ensure_vpn_connected(ctx, quick=False, flipkart=True)
    print(f"VPN ok={ok_vpn}", flush=True)
    await m._dismiss_all_veepn_welcome(ctx)
    await m._close_vpn_extension_tabs(ctx, await m._vpn_ext_id(ctx))

    for round_n in range(1, max_rounds + 1):
        eid = await m._vpn_ext_id(ctx)
        proxy = bool(eid and await m._vpn_is_proxy_active(ctx, eid))
        print(f"[{round_n}/{max_rounds}] proxy={proxy} → Flipkart", flush=True)

        if not proxy:
            print("  proxy off → USA UI (карточка → список → питание)", flush=True)
            await m._vpn_fresh_connect_usa(ctx, path, quick=True)
            await m._dismiss_all_veepn_welcome(ctx)
            await m._close_vpn_extension_tabs(ctx, await m._vpn_ext_id(ctx))

        page = await m._main_work_page(ctx)
        ok, page, err = await m._navigate_flipkart_resilient(
            ctx, page, "https://www.flipkart.com",
            label=phone, profile_path=path,
        )
        url = (page.url if page else "")[:100]
        print(f"  flipkart ok={ok} err={err!r} url={url}", flush=True)
        if ok and page and not await m._flipkart_page_blocked(page):
            page = await m._keep_only_flipkart_tabs(ctx, prefer_page=page)
            print("FLIPKART_READY", flush=True)
            return True, page

        print("  fail → fresh USA UI", flush=True)
        await m._vpn_fresh_connect_usa(ctx, path, quick=True)
        await m._dismiss_all_veepn_welcome(ctx)
        await m._close_vpn_extension_tabs(ctx, await m._vpn_ext_id(ctx))

    return False, None


async def _buy_one(m, target: dict, months: int) -> tuple[bool, str]:
    path = Path(target["path"])
    phone = target.get("username") or m._phone_from_path(path)
    print(f"\n══ PROFILE {phone} {path} months={months} ══", flush=True)
    m._kill_chrome_for_profile(path)
    m._clear_stale_profile_locks(path)
    m.set_automation_proc(os.getpid(), mode=f"buy-{months}m", owner="agent")
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

        ok, page = await _open_flipkart_with_vpn(m, ctx, path)
        if not ok:
            print("STOP: Flipkart не открылся", flush=True)
            return False, "FLIPKART_FAIL"

        if await m._page_logged_out(page):
            print("не залогинен → сразу cookies restore", flush=True)
            if not await m._auto_restore_flipkart_session(ctx, page, path):
                return False, m._NOT_LOGGED_IN_MSG
            page = await m._main_work_page(ctx)

        print(f"BUY start months={months}", flush=True)
        buy_ok, msg = await m._do_buy_membership(
            path, months, card=None,
            _skip_ping=True,
            _existing_ctx=ctx,
            _existing_page=page,
        )
        print(f"BUY ok={buy_ok} msg={msg}", flush=True)
        # Если не залогинен после buy — restore + ещё раз
        if (not buy_ok and msg == m._NOT_LOGGED_IN_MSG
                and await m._auto_restore_flipkart_session(ctx, page, path)):
            page = await m._main_work_page(ctx)
            print("BUY retry after cookie restore", flush=True)
            buy_ok, msg = await m._do_buy_membership(
                path, months, card=None,
                _skip_ping=True,
                _existing_ctx=ctx,
                _existing_page=page,
            )
            print(f"BUY retry ok={buy_ok} msg={msg}", flush=True)
        return buy_ok, str(msg or "")
    except Exception as e:
        print(f"FATAL {type(e).__name__}: {e}", flush=True)
        return False, f"FATAL:{e}"
    finally:
        m.clear_automation_proc()
        if ctx:
            with contextlib.suppress(Exception):
                await m._close_browser_session(ctx, pw, path, disconnect_vpn=True)
        else:
            m._unregister_purchase_profile(path)
            with contextlib.suppress(Exception):
                await pw.stop()


async def main() -> int:
    import menu as m

    months = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3
    prefer = sys.argv[1] if len(sys.argv) > 1 else ""

    profiles = _available_profiles(m, prefer_phone=prefer)
    if not profiles:
        print("NO_PROFILE")
        return 2

    print(f"queue={len(profiles)} months={months}", flush=True)
    for i, target in enumerate(profiles, 1):
        print(f"\n>>> [{i}/{len(profiles)}] try {target.get('username')}", flush=True)
        ok, msg = await _buy_one(m, target, months)
        if ok:
            print(f"SUCCESS {msg}", flush=True)
            return 0
        if str(msg).startswith("OUT_OF_STOCK"):
            print(
                f"OOS — профиль не подходит, удалите его и берём следующий "
                f"({target.get('username')})",
                flush=True,
            )
            continue
        # другая ошибка — тоже пробуем следующий доступный
        print(f"fail → next profile ({msg[:80]})", flush=True)

    print("ALL_PROFILES_FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
