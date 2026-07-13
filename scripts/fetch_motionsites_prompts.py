"""Fetch MotionSites prompts into vendor/motionsites_prompts/ (Playwright + clipboard).

  python scripts/fetch_motionsites_prompts.py --slug bold-studio
  python scripts/fetch_motionsites_prompts.py --free
  python scripts/fetch_motionsites_prompts.py --title "Bold Studio" --slug bold-studio

Premium: --visible --wait-login 120 (войти на motionsites.ai вручную).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_OUT = _ROOT / "vendor" / "motionsites_prompts"
_CATALOG = _OUT / "catalog.json"
_SITE = "https://motionsites.ai/"


def _catalog() -> list[dict]:
    if not _CATALOG.exists():
        return []
    return json.loads(_CATALOG.read_text(encoding="utf-8")).get("prompts", [])


def _entry_by_slug(slug: str) -> dict | None:
    for p in _catalog():
        if p.get("slug") == slug:
            return p
    return None


async def _fetch_title(page, title: str) -> str:
    await page.goto(_SITE, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(2500)
    card = page.get_by_text(title, exact=True).first
    await card.scroll_into_view_if_needed()
    await card.click(timeout=15000)
    await page.wait_for_timeout(2000)
    for label in ("Copy full prompt", "Copy prompt", "Copy"):
        try:
            btn = page.get_by_role("button", name=label, exact=False).first
            if await btn.is_visible(timeout=2500):
                await btn.click()
                await page.wait_for_timeout(1200)
                break
        except Exception:
            continue
    try:
        return (await page.evaluate("async () => await navigator.clipboard.readText()")) or ""
    except Exception:
        return ""


def _save(slug: str, title: str, body: str, meta: dict | None) -> Path:
    _OUT.mkdir(parents=True, exist_ok=True)
    url = f"{_SITE}?prompt={slug}"
    tier = (meta or {}).get("tier", "unknown")
    category = (meta or {}).get("category", "")
    out = _OUT / f"{slug}.md"
    md = (
        f"---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        f"source: {url}\n"
        f"tier: {tier}\n"
        f"category: {category}\n"
        f"fetched: {time.strftime('%Y-%m-%d')}\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"Источник: [MotionSites]({url})\n\n"
        f"## Prompt\n\n"
        f"{body.strip()}\n"
    )
    out.write_text(md, encoding="utf-8")
    return out


async def _run_batch(
    items: list[tuple[str, str]],
    *,
    headless: bool,
    wait_login: int,
) -> tuple[int, int]:
    from playwright.async_api import async_playwright

    ok = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = await ctx.new_page()
        if wait_login > 0:
            await page.goto(_SITE, timeout=90000)
            print(f"Войдите на motionsites.ai при необходимости — жду {wait_login} сек…")
            await page.wait_for_timeout(wait_login * 1000)

        for slug, title in items:
            meta = _entry_by_slug(slug)
            t = (meta or {}).get("title") or title
            try:
                body = await _fetch_title(page, t)
            except Exception as exc:
                print("FAIL", slug, exc)
                continue
            if len(body.strip()) < 80:
                print("FAIL", slug, "clipboard empty or too short")
                continue
            path = _save(slug, t, body, meta)
            print("OK", slug, "->", path.relative_to(_ROOT))
            ok += 1
            await page.wait_for_timeout(800)

        await browser.close()
    return ok, len(items)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--title")
    ap.add_argument("--free", action="store_true")
    ap.add_argument("--missing", action="store_true", help="only slugs without .md file")
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--wait-login", type=int, default=0)
    args = ap.parse_args()

    items: list[tuple[str, str]] = []
    if args.slug:
        meta = _entry_by_slug(args.slug) or {}
        items.append((args.slug, args.title or meta.get("title") or args.slug))
    if args.free:
        for p in _catalog():
            if p.get("tier") == "free":
                items.append((p["slug"], p["title"]))

    if args.missing:
        items = [(s, t) for s, t in items if not (_OUT / f"{s}.md").exists()]

    if not items:
        ap.error("укажите --slug, --title+--slug или --free")

    import asyncio

    n_ok, total = asyncio.run(
        _run_batch(items, headless=not args.visible, wait_login=args.wait_login)
    )
    print(f"Готово: {n_ok}/{total}")
    try:
        import subprocess
        subprocess.run([__import__("sys").executable, str(_ROOT / "scripts" / "motionsites_index.py")], check=False)
    except Exception:
        pass


if __name__ == "__main__":
    main()
