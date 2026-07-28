"""Self-check: fallback-заливка OTP не падает, когда elementFromPoint отдал
обёртку поля (это роняло весь вход с уже полученным кодом: Illegal invocation)."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

PAGE = """
<div id="wrap" style="position:absolute;left:0;top:0;width:300px;height:60px">
  <input id="otp" type="text" style="width:100%;height:100%">
</div>
<div id="overlay" style="position:absolute;left:0;top:0;width:300px;height:60px"></div>
"""


def _extract_js() -> str:
    """Берём ровно тот JS, что в main.py, чтобы тест проверял продакшн-код."""
    src = (ROOT / "subhub" / "main.py").read_text(encoding="utf-8", errors="replace")
    m = re.search(r'_fill_res = await page\.evaluate\("""\s*(.*?)\s*"""', src, re.S)
    assert m, "не нашёл JS fallback-заливки OTP в main.py"
    return m.group(1)


async def main_async() -> None:
    from playwright.async_api import async_playwright

    js = _extract_js()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()

        # 1. Точка попадает в оверлей-обёртку, а не в сам <input>
        await page.set_content(PAGE)
        res = await page.evaluate(js, [150, 30, "123456"])
        assert res == "ok", f"обёртка: ожидал ok, получил {res}"
        assert await page.input_value("#otp") == "123456"

        # 2. Точка прямо по input — как раньше
        await page.set_content('<input id="otp" type="text" '
                               'style="position:absolute;left:0;top:0;width:300px;height:60px">')
        assert await page.evaluate(js, [150, 30, "654321"]) == "ok"
        assert await page.input_value("#otp") == "654321"

        # 3. Под точкой нет поля вовсе — возвращаем причину, а не исключение
        await page.set_content('<div style="position:absolute;left:0;top:0;'
                               'width:300px;height:60px">нет поля</div>')
        assert await page.evaluate(js, [150, 30, "111111"]) == "no_input"

        # 4. Точка вне документа
        await page.set_content("<div>x</div>")
        assert await page.evaluate(js, [5000, 5000, "1"]) == "no_element"

        await browser.close()


def main() -> None:
    asyncio.run(main_async())
    src = (ROOT / "subhub" / "main.py").read_text(encoding="utf-8", errors="replace")
    i = src.find("OTP не совпал с ожидаемым")
    chunk = src[i: i + 3500]
    assert "except Exception as _fill_exc" in chunk, \
        "fallback обязан не ронять фазу входа с уже полученным OTP"
    print("PASS otp_fill_fallback")


if __name__ == "__main__":
    main()
