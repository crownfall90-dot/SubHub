"""Импорт гифт-карт Flipkart из заказа Bitrefill по ссылке.

Ссылка вида
`https://www.bitrefill.com/checkout/<id>#<accessToken>` открывается в отдельном
Chrome-профиле SubHub. Первый раз нужно один раз войти в аккаунт Bitrefill —
дальше сессия живёт в профиле и импорт идёт без участия человека.

Почему через браузер, а не обычным HTTP-запросом:
  • сайт под Cloudflare — простой GET получает страницу проверки;
  • заказ отдаётся только владельцу: `/api/accounts/invoice/<id>?accessToken=…`
    без сессии аккаунта отвечает `404 Invoice not found`, одного токена мало,
    поэтому коды читаются со страницы, а не из API.

Разбор карт (`cards_from_text`) вынесен отдельной чистой функцией — её гоняет
тест без сети и без браузера.
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


def parse_order_url(url: str) -> tuple[str, str]:
    """Ссылка на заказ → (invoice_id, access_token). Пустые строки, если не разобрал."""
    u = str(url or "").strip()
    m = re.search(r"/(?:checkout|invoice|order)/([0-9a-fA-F-]{16,64})", u)
    if not m:
        return "", ""
    token = ""
    if "#" in u:
        token = u.split("#", 1)[1].split("?")[0].split("&")[0].strip()
    return m.group(1), token


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


async def fetch_order_cards(url: str, *, default_denom: int | None = None,
                            log=print, login_wait: float = 180.0) -> tuple[list, str]:
    """Открывает заказ Bitrefill и возвращает (карты, сообщение).

    Первый запуск: если сессии нет, окно остаётся открытым и ждёт до login_wait
    секунд, пока человек войдёт в аккаунт. Дальше вход уже не нужен.
    """
    import asyncio
    import contextlib

    import menu as _menu
    from playwright.async_api import async_playwright

    inv, token = parse_order_url(url)
    if not inv:
        return [], "Не похоже на ссылку заказа Bitrefill"

    profile = _menu._HERE / "data" / PROFILE_NAME
    profile.mkdir(parents=True, exist_ok=True)
    page_url = f"https://www.bitrefill.com/checkout/{inv}" + (f"#{token}" if token else "")

    pw = ctx = None
    try:
        # Окно видимое: Cloudflare заворачивает headless на проверку бота
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
            await page.goto(page_url, wait_until="domcontentloaded", timeout=60_000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2500)

            text = ""
            with contextlib.suppress(Exception):
                text = await page.evaluate("() => document.body?.innerText || ''")

            cards = cards_from_text(text, default_denom)
            if cards:
                return cards, f"Найдено карт: {len(cards)}"

            low = text.lower()
            if any(mk in low for mk in _LOGIN_MARKERS):
                if asyncio.get_running_loop().time() > deadline:
                    return [], ("Заказ не открылся: нужен вход в аккаунт Bitrefill. "
                                "Войдите в открывшемся окне и повторите.")
                if not asked:
                    asked = True
                    log("  Войдите в аккаунт Bitrefill в открывшемся окне — "
                        "жду и заберу карты сам…")
                await page.wait_for_timeout(5000)
                continue
            return [], "Карты на странице не найдены (заказ пустой или другой формат)"
    except Exception as exc:
        return [], f"Ошибка: {exc}"
    finally:
        if ctx is not None:
            with contextlib.suppress(Exception):
                await _menu._close_browser_session(ctx, pw, profile)
        elif pw is not None:
            with contextlib.suppress(Exception):
                await pw.stop()


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


async def check_stock(product_id: str = PRODUCT_ID) -> tuple[dict, str]:
    """Смотрит наличие на сайте. Возвращает (состояние, ошибка).

    Через headless-браузер: сайт под Cloudflare, обычный запрос получает 403.
    С боевыми аргументами запуска проекта проверка проходит незаметно.
    """
    import contextlib
    import json as _json
    import tempfile

    import menu as _menu
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
