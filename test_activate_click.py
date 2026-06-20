"""
Тест: найти и нажать кнопку 'Activate Now' на flipkart-black-store.
Запуск: python test_activate_click.py [номер_телефона]
Если номер не указан — перебирает все профили в chrome_profiles_done.
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DONE_DIR = Path("./chrome_profiles_done")

_ACTIVATE_JS = """() => {
    // 1. aria-label
    for (const el of document.querySelectorAll('[aria-label]')) {
        const al = (el.getAttribute('aria-label') || '').trim();
        if (al === 'Activate Now' || al === 'Activate now') {
            el.scrollIntoView({behavior:'instant', block:'center'});
            el.click(); return 'aria:' + al;
        }
    }
    // 2. Promos-картинка 1200x213 (Activate Now кнопка)
    for (const img of document.querySelectorAll('img[width="1200"]')) {
        if (img.getAttribute('height') !== '213') continue;
        if (!(img.src||'').includes('/promos/')) continue;
        let el = img.parentElement;
        for (let i = 0; i < 6 && el; i++) {
            if (el.style && el.style.cursor === 'pointer') {
                el.scrollIntoView({behavior:'instant', block:'center'});
                el.click(); return 'img-1200x213';
            }
            el = el.parentElement;
        }
        img.scrollIntoView({behavior:'instant', block:'center'});
        img.click(); return 'img-direct';
    }
    // 3. cursor:pointer div с img (fallback)
    const cands = [...document.querySelectorAll('div[style*="cursor"]')]
        .filter(el => {
            if (el.style.cursor !== 'pointer') return false;
            const r = el.getBoundingClientRect();
            return r.width > 200 && r.height > 50 && r.top > 200 && el.querySelector('img');
        })
        .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    if (cands.length > 0) {
        const r = cands[0].getBoundingClientRect();
        cands[0].scrollIntoView({behavior:'instant', block:'center'});
        cands[0].click();
        return 'cursor:' + Math.round(r.width) + 'x' + Math.round(r.height) + '@y=' + Math.round(r.top);
    }
    return null;
}"""

_DEBUG_JS = """() => {
    const out = {};
    // Все aria-label на странице
    const ariaEls = [...document.querySelectorAll('[aria-label]')];
    out.aria_labels = ariaEls.map(el => ({
        tag: el.tagName,
        label: el.getAttribute('aria-label'),
        cls: el.className.slice(0, 50)
    })).slice(0, 20);
    // cursor:pointer divs
    const ptrs = [...document.querySelectorAll('div[style*="cursor"]')]
        .filter(el => el.style.cursor === 'pointer');
    out.cursor_divs = ptrs.map(el => {
        const r = el.getBoundingClientRect();
        const hasImg = !!el.querySelector('img');
        return {w: Math.round(r.width), h: Math.round(r.height), top: Math.round(r.top), img: hasImg};
    }).slice(0, 10);
    // img[width=1200] элементы (кнопка Activate Now)
    out.imgs_1200 = [...document.querySelectorAll('img[width="1200"]')].map(img => ({
        h: img.getAttribute('height'),
        src: (img.src||'').slice(-60),
        cursorParent: (() => {
            let el = img.parentElement;
            for (let i = 0; i < 6 && el; i++) {
                if (el.style && el.style.cursor === 'pointer') return i;
                el = el.parentElement;
            }
            return null;
        })()
    })).slice(0, 5);
    // Наличие flipkart-black-store ссылки
    out.black_store_link = document.documentElement.innerHTML.includes('flipkart-black-store');
    return out;
}"""


async def test_profile(profile_path: Path, phone: str):
    print(f"\n{'='*60}")
    print(f"Профиль: {profile_path.name}")
    print(f"{'='*60}")

    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path.resolve()),
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print("  Открываю flipkart-black-store...")
        try:
            await page.goto(
                "https://www.flipkart.com/flipkart-black-store",
                wait_until="domcontentloaded", timeout=20_000
            )
        except Exception as e:
            print(f"  Ошибка goto: {e}")

        await page.wait_for_timeout(3_000)

        # Скролл для загрузки lazy-элементов
        for pos in [0.3, 0.6, 1.0, 0.0]:
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pos})")
            await page.wait_for_timeout(500)

        # Дебаг — что есть на странице
        print("\n  === ДИАГНОСТИКА ===")
        try:
            dbg = await page.evaluate(_DEBUG_JS)
            print(f"  aria-label элементы ({len(dbg.get('aria_labels', []))} шт):")
            for it in dbg.get("aria_labels", []):
                print(f"    [{it['tag']}] aria-label='{it['label']}' cls={it['cls']}")
            print(f"  cursor:pointer div-ы ({len(dbg.get('cursor_divs', []))} шт):")
            for it in dbg.get("cursor_divs", []):
                print(f"    {it['w']}x{it['h']} top={it['top']} img={it['img']}")
            print(f"  img[width=1200] элементы ({len(dbg.get('imgs_1200', []))} шт):")
            for it in dbg.get("imgs_1200", []):
                print(f"    height={it['h']} src=...{it['src']} cursor_parent_level={it['cursorParent']}")
            print(f"  flipkart-black-store в HTML: {dbg.get('black_store_link')}")
        except Exception as e:
            print(f"  Диагностика: {e}")

        # Проверяем статус кнопки через Playwright локатор
        _BTN_SEL = (
            "[aria-label='Activate Now'], [aria-label='Activate now'], "
            "button:has-text('Activate now'), a:has-text('Activate now'), "
            "[role='button']:has-text('Activate now')"
        )
        try:
            btn = page.locator(_BTN_SEL).first
            cnt = await btn.count()
            vis = await btn.is_visible() if cnt > 0 else False
            print(f"\n  Playwright локатор: count={cnt}, visible={vis}")
        except Exception as e:
            print(f"  Локатор: {e}")

        print("\n  === КЛИК ===")
        try:
            new_page_fut = asyncio.ensure_future(
                ctx.wait_for_event("page", timeout=12_000)
            )
            method = await page.evaluate(_ACTIVATE_JS)
            if method:
                print(f"  JS-клик: {method}")
                try:
                    new_tab = await asyncio.wait_for(
                        asyncio.shield(new_page_fut), timeout=10
                    )
                    await new_tab.wait_for_load_state("domcontentloaded", timeout=12_000)
                    print(f"  Новая вкладка: {new_tab.url}")
                except asyncio.TimeoutError:
                    await page.wait_for_timeout(3_000)
                    if "flipkart-black-store" not in page.url:
                        print(f"  Та же страница перешла: {page.url}")
                    else:
                        print(f"  Навигации не произошло. Текущий URL: {page.url}")
            else:
                new_page_fut.cancel()
                print("  JS-клик: элемент не найден")
        except Exception as e:
            print(f"  Клик: {e}")

        print("\n  Браузер открыт 10 сек, потом закроется...")
        await page.wait_for_timeout(10_000)
        await ctx.close()
    finally:
        await pw.stop()


async def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        profile_path = DONE_DIR / f"profile_{arg}"
        if not profile_path.exists():
            # Попробуем как есть
            profile_path = Path(arg)
        await test_profile(profile_path, arg)
    else:
        profiles = sorted(DONE_DIR.iterdir()) if DONE_DIR.exists() else []
        if not profiles:
            print("Профили не найдены в chrome_profiles_done/")
            return
        print(f"Найдено профилей: {len(profiles)}")
        phone = input("Введи номер телефона профиля (или Enter для первого): ").strip()
        if phone:
            profile_path = DONE_DIR / f"profile_{phone}"
        else:
            profile_path = profiles[0]
        await test_profile(profile_path, profile_path.name)


if __name__ == "__main__":
    asyncio.run(main())
