"""Бенчмарк: цена флагов _PROFILE_SLIM_ARGS — время загрузки и размер профиля.

Гоняет один и тот же сценарий (страница логина + страница товара Black
Membership) в чистом профиле, с флагами и без них, по N прогонов на вариант.
Запуск: python scripts/bench_slim_args.py [прогонов]
"""
from __future__ import annotations

import asyncio
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

URLS = [
    "https://www.flipkart.com/account/login?ret=/",
    ("https://www.flipkart.com/flipkart-black-3-months-membership/p/itmaacb5a37224f3"
     "?pid=XVZHES63WKZK7FUM"),
]


async def one_run(slim: bool) -> tuple[float, int]:
    """Возвращает (секунды на загрузку всех URL, размер профиля в байтах)."""
    import menu as m
    from playwright.async_api import async_playwright

    prof = Path(tempfile.mkdtemp(prefix="bench_")) / "profile_0001_9000000000"
    prof.mkdir(parents=True)
    kw = m._browser_launch_kw(headless=True, use_vpn=False, profile_path=prof)
    if not slim:
        kw["args"] = [a for a in kw["args"] if a not in m._PROFILE_SLIM_ARGS]
    pw = await async_playwright().start()
    try:
        t0 = time.monotonic()
        ctx = await pw.chromium.launch_persistent_context(str(prof), **kw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        for url in URLS:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
        elapsed = time.monotonic() - t0
        await ctx.close()
    finally:
        await pw.stop()
    size = m._dir_size(prof)
    shutil.rmtree(prof.parent, ignore_errors=True)
    return elapsed, size


async def main(runs: int) -> None:
    import menu as m
    print(f"флаги: {' '.join(m._PROFILE_SLIM_ARGS)}\n")
    res: dict = {}
    for slim in (False, True):
        times, sizes = [], []
        for i in range(runs):
            t, s = await one_run(slim)
            times.append(t)
            sizes.append(s)
            print(f"  {'со флагами' if slim else 'без флагов '} "
                  f"прогон {i + 1}/{runs}: {t:5.1f}s  профиль {s / 1048576:5.1f} МБ")
        res[slim] = (statistics.median(times), statistics.median(sizes))
    (t_old, s_old), (t_new, s_new) = res[False], res[True]
    print("\n── медианы ──")
    print(f"  без флагов : {t_old:5.1f}s   {s_old / 1048576:6.1f} МБ")
    print(f"  с флагами  : {t_new:5.1f}s   {s_new / 1048576:6.1f} МБ")
    _dt = (t_new - t_old) / t_old * 100 if t_old else 0
    _ds = (s_new - s_old) / s_old * 100 if s_old else 0
    print(f"  разница    : время {_dt:+.1f}%   место {_ds:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
