"""Bitrefill: импорт купленных карт в хранилище и наличие товара.

`import_all_cards` заходит в аккаунт (в отдельном Chrome-профиле SubHub) и
забирает ВСЕ карты из «Мои продукты» — новые купленные подхватываются сами,
ссылку на заказ вводить не нужно. Вход требуется один раз: дальше сессия
живёт в профиле.

Почему через браузер: сайт под Cloudflare, обычный GET получает страницу
проверки, а заказы отдаются только владельцу сессии. В официальном API товар
закрыт для нашего аккаунта (`/products/flipkart-india` → 403), поэтому и
наличие (`check_stock`) читается с сайта.

Разбор карт (`cards_from_text`) — чистая функция, её гоняет тест без сети.
"""
from __future__ import annotations

import re

# Метки на карточке заказа: сайт отдаёт их на языке интерфейса аккаунта
_CODE_LABELS = ("код подарочного сертификата", "gift certificate code",
                "gift card code", "код сертификата", "voucher code")
_PIN_LABELS = ("pin-код", "pin код", "pin code", "pin")

_NUM_RE = re.compile(r"\b(\d{14,19})\b")
_PIN_RE = re.compile(r"\b(\d{4,8})\b")
# ₹100.00 / ₹1,000 / 100,00 ₹
_AMOUNT_RE = re.compile(r"[₹]\s*([\d\s.,]{2,12})|([\d\s.,]{2,12})\s*[₹]")




def _amount_to_denom(raw: str) -> int:
    """«100.00» / «1,000» / «100,00» → 100 / 1000 / 100."""
    s = re.sub(r"\s", "", str(raw or ""))
    if not s:
        return 0
    # Отбрасываем дробную часть: у гифт-карт номинал целый
    s = re.sub(r"[.,]\d{1,2}$", "", s)
    s = s.replace(",", "").replace(".", "")
    try:
        return int(s)
    except ValueError:
        return 0


def cards_from_text(text: str, default_denom: int | None = None) -> list[dict]:
    """Достаёт карты из текста страницы заказа.

    Ожидаемая раскладка (именно так рендерит Bitrefill):
        Flipkart India
        ₹100.00
        Код подарочного сертификата
        6000170823257591
        PIN-код
        243092

    Номинал берётся из ближайшей суммы ПЕРЕД кодом; если её нет —
    default_denom. Карты без номера или PIN пропускаются.
    """
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    low = [ln.lower() for ln in lines]
    out: list[dict] = []
    seen: set[str] = set()

    for i, ln in enumerate(low):
        if not any(lbl in ln for lbl in _CODE_LABELS):
            continue
        # номер — в этой же строке или в ближайших следующих
        number = ""
        for j in range(i, min(i + 4, len(lines))):
            m = _NUM_RE.search(lines[j])
            if m:
                number = m.group(1)
                num_at = j
                break
        if not number or number in seen:
            continue

        # PIN — после метки PIN, ниже номера
        pin = ""
        for j in range(num_at + 1, min(num_at + 6, len(lines))):
            if any(lbl in low[j] for lbl in _PIN_LABELS):
                for k in range(j, min(j + 3, len(lines))):
                    m = _PIN_RE.search(lines[k])
                    if m and m.group(1) != number:
                        pin = m.group(1)
                        break
                break
        if not pin:
            continue

        # номинал — ближайшая сумма выше кода
        denom = 0
        for j in range(i - 1, max(-1, i - 8), -1):
            m = _AMOUNT_RE.search(lines[j])
            if m:
                denom = _amount_to_denom(m.group(1) or m.group(2))
                if denom:
                    break
        if not denom:
            denom = int(default_denom or 0)
        if not denom:
            continue

        seen.add(number)
        out.append({"denom": denom, "number": number, "pin": pin, "used": False})
    return out




# ── Браузерная часть ─────────────────────────────────────────────────────────

# Профиль под Bitrefill: сессия аккаунта живёт здесь, чтобы вход был разовым
PROFILE_NAME = "bitrefill_profile"
_LOGIN_MARKERS = ("invoice not found", "log in", "войти", "sign in", "not found")




# ── Наличие товара ───────────────────────────────────────────────────────────

PRODUCT_ID = "flipkart-india"
_STOCK_API = "/api/product/"


def stock_from_product(data: dict) -> dict:
    """JSON карточки товара → {in_stock, denoms, currency}.

    Поле `outOfStock` отвечает за наличие, `packages` — номиналы с ценой в USD.
    """
    packages = data.get("packages") or []
    denoms = []
    for p in packages:
        if not isinstance(p, dict):
            continue
        val = p.get("amount") or p.get("value")
        try:
            val = int(float(str(val)))
        except (TypeError, ValueError):
            continue
        usd = p.get("usdPrice")
        try:
            usd = round(float(usd), 2)
        except (TypeError, ValueError):
            usd = None
        denoms.append({"value": val, "usd": usd})
    denoms.sort(key=lambda d: d["value"])
    return {
        "in_stock": not bool(data.get("outOfStock", True)),
        "denoms": denoms,
        "currency": data.get("currency") or "INR",
        "name": data.get("name") or PRODUCT_ID,
    }


def stock_message(state: dict) -> str:
    """Текст уведомления в Telegram (HTML)."""
    name = state.get("name") or "Flipkart India"
    if not state.get("in_stock"):
        return (f"🚫 <b>{name}</b> — нет в наличии\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Сообщу, как появится.")
    cur = state.get("currency") or "INR"
    lines = [f"🎁 <b>{name} — В НАЛИЧИИ!</b>", "━━━━━━━━━━━━━━━━━━━", ""]
    for d in state.get("denoms") or []:
        price = f"  ·  ${d['usd']:.2f}" if d.get("usd") is not None else ""
        lines.append(f"▸ <b>{d['value']} {cur}</b>{price}")
    if not state.get("denoms"):
        lines.append("<i>номиналы не распознаны</i>")
    lines.append("")
    lines.append("<i>Покупка — на сайте Bitrefill, потом импорт карт в SubHub.</i>")
    return "\n".join(lines)


def stock_gone_message(gone: list, state: dict) -> str:
    """Текст о том, что номинал закончился: что ушло и что осталось (HTML)."""
    name = state.get("name") or "Flipkart India"
    cur = state.get("currency") or "INR"
    gone_ints = {int(g) for g in gone}
    left = [d for d in (state.get("denoms") or [])
            if int(d.get("value") or 0) not in gone_ints]
    gone_s = ", ".join(f"<b>{g} {cur}</b>" for g in sorted(gone_ints))
    lines = [f"\u274c <b>{name}</b> \u2014 закончился {gone_s}",
             "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
             "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501", ""]
    if state.get("in_stock") and left:
        lines.append("Остались в наличии:")
        for d in left:
            price = f"  \u00b7  ${d['usd']:.2f}" if d.get("usd") is not None else ""
            lines.append(f"\u25b8 <b>{d['value']} {cur}</b>{price}")
    else:
        lines.append("\U0001f6ab <b>Нет в наличии</b> \u2014 не осталось ничего.")
        lines.append("<i>Сообщу, когда завезут.</i>")
    return "\n".join(lines)










# Cloudflare пропускает по TLS-отпечатку клиента, а не по кукам: обычный
# requests/httpx получает 403 даже с куками из браузера (проверено). curl_cffi
# умеет представляться Chrome на уровне TLS — и запрос проходит за 0.3с вместо
# ~10с на запуск headless-браузера.
_IMPERSONATE = "chrome124"


def _http_stock(product_id: str) -> dict | None:
    """Наличие обычным HTTP с подделкой TLS-отпечатка. None — не получилось."""
    import contextlib
    try:
        from curl_cffi import requests as _cr
    except ImportError:
        return None
    with contextlib.suppress(Exception):
        r = _cr.get(f"https://www.bitrefill.com{_STOCK_API}{product_id}",
                    impersonate=_IMPERSONATE, timeout=25)
        if r.status_code == 200:
            return stock_from_product(r.json())
    return None


async def check_stock(product_id: str = PRODUCT_ID) -> tuple[dict, str]:
    """Смотрит наличие на сайте. Возвращает (состояние, ошибка).

    Сначала обычный HTTP с TLS-отпечатком Chrome (0.3с). Если curl_cffi не
    установлен или Cloudflare поменял правила — поднимаем headless-браузер
    (~10с), он проходит всегда.
    """
    import contextlib
    import json as _json

    import menu as _menu

    fast = _http_stock(product_id)
    if fast is not None:
        return fast, ""

    from playwright.async_api import async_playwright

    prof = _menu._HERE / "data" / "bitrefill_stock_profile"
    prof.mkdir(parents=True, exist_ok=True)
    pw = ctx = None
    try:
        kw = _menu._browser_launch_kw(headless=True, profile_path=prof)
        pw = await async_playwright().start()
        ctx = await pw.chromium.launch_persistent_context(str(prof.resolve()), **kw)
        with contextlib.suppress(Exception):
            st = _menu._build_stealth_js_m()
            if st:
                await ctx.add_init_script(st)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.bitrefill.com/", wait_until="domcontentloaded",
                        timeout=60_000)
        await page.wait_for_timeout(4000)
        body = await page.evaluate(
            """async (p) => {
                const r = await fetch(p, {credentials: 'include'});
                return r.status === 200 ? await r.text() : '';
            }""", _STOCK_API + product_id)
        if not body:
            return {}, "Сайт не отдал карточку товара"
        return stock_from_product(_json.loads(body)), ""
    except Exception as exc:
        return {}, f"Ошибка проверки: {exc}"
    finally:
        if ctx is not None:
            with contextlib.suppress(Exception):
                await _menu._close_browser_session(ctx, pw, prof)
        elif pw is not None:
            with contextlib.suppress(Exception):
                await pw.stop()


# ── Импорт всех карт из аккаунта ─────────────────────────────────────────────

PROFILE_NAME = "bitrefill_profile"
PRODUCTS_URL = "https://www.bitrefill.com/account/products"
_LOGGED_OUT = ("log in", "войти", "sign in", "sign up", "зарегистр")


async def import_all_cards(*, log=print, login_wait: float = 240.0,
                           default_denom: int | None = None) -> tuple[list, str]:
    """Забирает все карты из «Мои продукты». Возвращает (карты, сообщение).

    Коды на карточках скрыты, пока не нажать «показать» — поэтому сначала
    кликаем по всем таким кнопкам, потом читаем текст страницы. У Bitrefill
    есть настройка «Автоматическое распечатывание»: с ней коды открыты сразу,
    и клики просто не находят целей.
    """
    import asyncio
    import contextlib

    import menu as _menu
    from playwright.async_api import async_playwright

    profile = _menu._HERE / "data" / PROFILE_NAME
    profile.mkdir(parents=True, exist_ok=True)

    pw = ctx = None
    try:
        # Окно видимое: при первом запуске в нём нужно войти в аккаунт
        kw = _menu._browser_launch_kw(headless=False, profile_path=profile)
        pw = await async_playwright().start()
        ctx = await pw.chromium.launch_persistent_context(str(profile.resolve()), **kw)
        with contextlib.suppress(Exception):
            _st = _menu._build_stealth_js_m()
            if _st:
                await ctx.add_init_script(_st)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        deadline = asyncio.get_running_loop().time() + max(10.0, login_wait)
        asked = False
        while True:
            await page.goto(PRODUCTS_URL, wait_until="domcontentloaded", timeout=60_000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2500)

            # Подгружаем весь список: карточек может быть много
            with contextlib.suppress(Exception):
                for _ in range(12):
                    _before = await page.evaluate("() => document.body.scrollHeight")
                    await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1200)
                    if await page.evaluate("() => document.body.scrollHeight") == _before:
                        break

            # Раскрываем скрытые коды
            with contextlib.suppress(Exception):
                revealed = await page.evaluate("""() => {
                    const want = ['показать', 'reveal', 'show code', 'показать код',
                                  'распечат', 'unseal'];
                    let n = 0;
                    for (const el of document.querySelectorAll(
                            'button,a,[role="button"]')) {
                        const t = (el.innerText || '').trim().toLowerCase();
                        if (t && want.some(w => t.includes(w))) { el.click(); n++; }
                    }
                    return n;
                }""")
                if revealed:
                    log(f"  Раскрываю коды: {revealed}")
                    await page.wait_for_timeout(3000)

            text = ""
            with contextlib.suppress(Exception):
                text = await page.evaluate("() => document.body?.innerText || ''")

            cards = cards_from_text(text, default_denom)
            if cards:
                return cards, f"Найдено карт в аккаунте: {len(cards)}"

            low = text.lower()
            if any(mk in low for mk in _LOGGED_OUT):
                if asyncio.get_running_loop().time() > deadline:
                    return [], ("Нужен вход в аккаунт Bitrefill. Войдите в "
                                "открывшемся окне и нажмите импорт снова.")
                if not asked:
                    asked = True
                    log("  Войдите в аккаунт Bitrefill в открывшемся окне — "
                        "дальше заберу карты сам…")
                await page.wait_for_timeout(5000)
                continue
            return [], ("Карты на странице не найдены. Если коды скрыты, включите "
                        "в настройках Bitrefill «Автоматическое распечатывание».")
    except Exception as exc:
        return [], f"Ошибка: {exc}"
    finally:
        if ctx is not None:
            with contextlib.suppress(Exception):
                await _menu._close_browser_session(ctx, pw, profile)
        elif pw is not None:
            with contextlib.suppress(Exception):
                await pw.stop()
