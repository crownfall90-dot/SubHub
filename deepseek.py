"""
DeepSeek Platform — автоматизация пополнения API-баланса банковской картой.

Флоу (platform.deepseek.com):
  1. Логин по email+паролю (сессия хранится в отдельном профиле на аккаунт).
  2. /usage — запоминаем «Topped-up balance» до оплаты.
  3. /top_up — валюта USD, сумма (пресет $2/$5/… или Custom), метод Visa/Mastercard.
  4. Реквизиты карты → Pay (3DS у этих карт нет).
  5. Успех = «Topped-up balance» на /usage вырос на сумму пополнения.

Модуль самостоятельный (не тянет menu.py) — чтобы позже его могла вызывать
и авто-обработка заказов GGSell, и GUI.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

_HERE = Path(__file__).parent
BASE_URL = "https://platform.deepseek.com"
PROFILES_DIR = _HERE / "chrome_profiles_deepseek"
DEBUG_DIR = _HERE / "debug" / "deepseek"

# Пресеты сумм на странице /top_up — остальное вводится через Custom
PRESET_AMOUNTS = (2, 5, 10, 20, 50, 100, 500)

LOGIN_MANUAL_WAIT = 180      # сек ожидания ручного входа (капча и т.п.), если авто-логин не прошёл
PAY_RESULT_WAIT = 120        # сек ожидания реакции формы после Pay
BALANCE_WAIT = 180           # сек ожидания роста баланса на /usage
KEEP_OPEN_ON_FAIL = 600      # сек держать браузер открытым при ошибке (ручное завершение)


def _profile_dir(email: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", email.strip().lower())
    return PROFILES_DIR / safe


def _safe_log(cb):
    """Оборачивает колбэк лога: его ошибки не должны ронять оплату."""
    def _log(msg: str) -> None:
        try:
            cb(msg)
        except Exception:
            pass
    return _log


async def _shot(page, tag: str, log) -> None:
    """Скриншот для разбора проблем → debug/deepseek/."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        f = DEBUG_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
        await page.screenshot(path=str(f), full_page=True)
        log(f"📸 Скриншот: {f.name}")
    except Exception:
        pass


async def _page_text(page) -> str:
    try:
        return await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""


async def _dismiss_notices(page) -> None:
    """Закрывает плашки: cookie-баннер, «Got it» и т.п."""
    for label in ("Necessary cookies only", "Got it", "OK", "我知道了"):
        for loc in (page.get_by_role("button", name=label, exact=True).first,
                    page.get_by_text(label, exact=True).first):
            try:
                if await loc.is_visible(timeout=500):
                    await loc.click()
                    await page.wait_for_timeout(300)
                    break
            except Exception:
                pass


async def _wait_usage_or_signin(page, timeout: float = 25.0) -> str:
    """После goto /usage ждём, куда нас пустили: 'signin' | 'usage' | 'unknown'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "sign_in" in page.url:
            return "signin"
        txt = await _page_text(page)
        if "Topped-up balance" in txt:
            return "usage"
        try:
            if await page.locator("input[type='password']").first.is_visible():
                return "signin"
        except Exception:
            pass
        await page.wait_for_timeout(1000)
    return "unknown"


async def _read_topped_up_balance(page) -> float | None:
    """Парсит «Topped-up balance $X.XX» из текста /usage."""
    txt = await _page_text(page)
    m = re.search(r"Topped-up balance[^$]{0,80}\$\s*([\d,]+(?:\.\d+)?)", txt)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


async def _type_into(page, locator, value: str, clear: bool = True) -> bool:
    """Кликает в поле и печатает значение с человеческой задержкой."""
    try:
        await locator.click(timeout=5000)
        if clear:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
        await page.keyboard.type(value, delay=40)
        return True
    except Exception:
        return False


async def _fill_by_placeholder(page, key: str, value: str, clear: bool = True) -> bool:
    loc = page.locator(f"input[placeholder*='{key}' i]").first
    return await _type_into(page, loc, value, clear=clear)


# ── Шаги флоу ────────────────────────────────────────────────────────────────

async def _login(page, email: str, password: str, log, headless: bool = False) -> bool:
    await _dismiss_notices(page)
    log("Ввожу логин и пароль…")
    # Поле логина: placeholder «Phone number / email address»
    login_inp = page.locator("input[placeholder*='email' i]").first
    try:
        await login_inp.wait_for(state="visible", timeout=10000)
    except Exception:
        login_inp = page.locator("input[type='text']").first

    if not await _type_into(page, login_inp, email):
        log("❌ Не нашёл поле для email")
        await _shot(page, "login_no_email_field", log)
        return False

    pwd_inp = page.locator("input[type='password']").first
    if not await _type_into(page, pwd_inp, password):
        log("❌ Не нашёл поле пароля")
        await _shot(page, "login_no_password_field", log)
        return False

    clicked = False
    candidates = (
        page.get_by_role("button", name="Log in", exact=True).first,
        page.get_by_text("Log in", exact=True).first,
        page.locator("button:has-text('Log in')").first,
    )
    for btn in candidates:
        try:
            # не перепутать с «Log in with Google»
            if "google" in (((await btn.inner_text()) or "").lower()):
                continue
            await btn.click(timeout=4000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        log("❌ Не нашёл кнопку Log in")
        await _shot(page, "login_no_button", log)
        return False

    # Ждём уход со страницы входа
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        await page.wait_for_timeout(1000)
        if "sign_in" not in page.url:
            log("✅ Вход выполнен")
            return True

    # Авто-вход не прошёл (капча/ошибка) — даём шанс завершить вручную
    txt = await _page_text(page)
    m = re.search(r"(incorrect|invalid|wrong)[^\n]{0,60}", txt, re.I)
    if m:
        log(f"❌ Ошибка входа: {m.group(0).strip()}")
        await _shot(page, "login_error", log)
        return False

    if headless:
        log("❌ Авто-вход не подтвердился (headless — ручной вход невозможен)")
        await _shot(page, "login_timeout", log)
        return False

    log(f"⚠ Авто-вход не подтвердился (возможно, капча). Завершите вход вручную — жду до {LOGIN_MANUAL_WAIT // 60} мин…")
    deadline = time.monotonic() + LOGIN_MANUAL_WAIT
    while time.monotonic() < deadline:
        await page.wait_for_timeout(2000)
        if "sign_in" not in page.url:
            log("✅ Вход выполнен (вручную)")
            return True
    await _shot(page, "login_timeout", log)
    return False


async def _google_autofill(gpage, email: str, password: str, log) -> None:
    """Best-effort автозаполнение окна Google OAuth (email → пароль).
    Google может показать капчу/2FA/«браузер небезопасен» — тогда доводим вручную."""
    try:
        await gpage.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    # Аккаунт уже знаком браузеру — выбор из списка
    try:
        acc = gpage.locator(f"[data-identifier='{email}']").first
        if await acc.is_visible(timeout=4000):
            await acc.click()
            log("Google: выбрал аккаунт из списка")
            return
    except Exception:
        pass

    try:
        em = gpage.locator(
            "input[type='email'], #identifierId, input[name='identifier']"
        ).locator("visible=true").first
        await em.wait_for(state="visible", timeout=15000)
        await em.click()
        await gpage.keyboard.type(email, delay=40)
        await gpage.keyboard.press("Enter")
        log("Google: ввёл email")
    except Exception:
        log("Google: поле email не появилось — продолжите вход вручную в окне Google")
        return

    if not password:
        log("Google: пароль не задан — введите его вручную в окне Google")
        return
    try:
        pw_inp = gpage.locator(
            "input[type='password'], input[name='Passwd']"
        ).locator("visible=true").first
        await pw_inp.wait_for(state="visible", timeout=20000)
        await gpage.wait_for_timeout(800)
        await pw_inp.click()
        await gpage.keyboard.type(password, delay=40)
        await gpage.keyboard.press("Enter")
        log("Google: ввёл пароль")
    except Exception:
        log("Google: поле пароля не появилось (капча/2FA?) — завершите вход вручную")


async def _login_google(page, email: str, password: str, log, headless: bool = False) -> bool:
    """Вход через «Log in with Google». Пароль можно не указывать —
    тогда окно Google заполняется вручную, скрипт ждёт завершения."""
    await _dismiss_notices(page)
    log("Вход через Google…")

    # На странице есть скрытый span-«измеритель» с тем же текстом — берём видимый
    async def _find_link():
        for loc in (page.get_by_text("Log in with Google", exact=True),
                    page.get_by_text(re.compile(r"log ?in with google", re.I))):
            try:
                cand = loc.locator("visible=true").first
                await cand.wait_for(state="visible", timeout=8000)
                return cand
            except Exception:
                continue
        return None

    link = await _find_link()
    if link is None:
        log("❌ Не нашёл кнопку «Log in with Google»")
        await _shot(page, "google_no_button", log)
        return False

    gpage = None
    for attempt in range(3):
        try:
            async with page.expect_popup(timeout=8000) as pinfo:
                await link.click()
            gpage = await pinfo.value
            break
        except Exception:
            pass
        # OAuth мог открыться в этой же вкладке
        await page.wait_for_timeout(3000)
        u = page.url.lower()
        if "accounts.google" in u:
            gpage = page
            break
        # Google отбросил обратно на sign_in с ошибкой (NEED_RETRY) — повторяем
        if "sign_in" in u and "error" in u:
            log(f"Google: сервис вернул ошибку — пробую ещё раз ({attempt + 1}/3)…")
            await page.wait_for_timeout(2000)
            link = await _find_link()
            if link is None:
                break
            continue
        break

    if gpage is not None:
        try:
            await _google_autofill(gpage, email, password, log)
        except Exception:
            pass  # окно могло закрыться само (уже залогинен)

    def _logged_in() -> bool:
        u = page.url
        return u.startswith(BASE_URL) and "sign_in" not in u

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _logged_in():
            log("✅ Вход через Google выполнен")
            return True
        await asyncio.sleep(2)

    if headless:
        log("❌ Google-вход не завершился (headless — ручной вход невозможен)")
        await _shot(page, "google_login_timeout", log)
        return False

    log(f"⚠ Google-вход не завершился автоматически (капча/2FA?). "
        f"Завершите вход в окне Google — жду до {LOGIN_MANUAL_WAIT // 60} мин…")
    deadline = time.monotonic() + LOGIN_MANUAL_WAIT
    while time.monotonic() < deadline:
        if _logged_in():
            log("✅ Вход через Google выполнен (вручную)")
            return True
        await asyncio.sleep(2)
    await _shot(page, "google_login_timeout", log)
    return False


async def _select_amount(page, amount: float, log) -> bool:
    """Выбирает сумму: пресет или Custom + ввод. Проверяет по «Total excluding tax»."""
    # USD (на случай если выбран CNY)
    try:
        await page.get_by_text("USD", exact=True).first.click(timeout=3000)
        await page.wait_for_timeout(400)
    except Exception:
        pass

    is_preset = float(amount).is_integer() and int(amount) in PRESET_AMOUNTS
    if is_preset:
        try:
            await page.get_by_text(f"${int(amount)}", exact=True).first.click(timeout=5000)
        except Exception:
            log(f"⚠ Пресет ${int(amount)} не нашёлся — пробую через Custom")
            is_preset = False

    if not is_preset:
        try:
            await page.get_by_text("Custom", exact=True).first.click(timeout=5000)
        except Exception:
            log("❌ Не нашёл кнопку Custom")
            await _shot(page, "no_custom_button", log)
            return False
        await page.wait_for_timeout(500)
        # После Custom появляется поле ввода суммы — на этом этапе других
        # видимых текстовых полей на странице нет
        target = None
        inputs = page.locator("input")
        for i in range(await inputs.count()):
            el = inputs.nth(i)
            try:
                if not await el.is_visible():
                    continue
                t = ((await el.get_attribute("type")) or "text").lower()
                if t in ("checkbox", "radio", "password", "hidden"):
                    continue
                target = el
                break
            except Exception:
                continue
        amount_str = f"{amount:g}"
        if target is None or not await _type_into(page, target, amount_str):
            # возможно, фокус уже в поле после клика по Custom
            try:
                await page.keyboard.type(amount_str, delay=40)
            except Exception:
                log("❌ Не нашёл поле для ввода суммы Custom")
                await _shot(page, "no_custom_input", log)
                return False

    await page.wait_for_timeout(800)
    # Контроль: «Total excluding tax $X.XX»
    txt = await _page_text(page)
    m = re.search(r"Total excluding tax\s*\$\s*([\d,]+(?:\.\d+)?)", txt)
    if m:
        applied = float(m.group(1).replace(",", ""))
        if abs(applied - amount) > 0.011:
            log(f"❌ Сумма не применилась: на странице ${applied:g}, нужно ${amount:g}")
            await _shot(page, "amount_mismatch", log)
            return False
        log(f"Сумма выбрана: ${applied:g} (+VAT)")
    else:
        log("⚠ Не смог проверить сумму по «Total excluding tax» — продолжаю")
    return True


async def _select_card_method(page, log) -> bool:
    """Кликает метод оплаты Visa/Mastercard (второй блок) и ждёт форму карты."""

    async def _card_form_visible() -> bool:
        try:
            await page.locator("input[placeholder*='Card number' i]").first.wait_for(
                state="visible", timeout=4000)
            return True
        except Exception:
            return False

    if await _card_form_visible():
        return True

    strategies = [
        ("логотип mastercard", "img[alt*='master' i], img[src*='master' i]"),
        ("логотип visa", "img[alt*='visa' i], img[src*='visa' i]"),
    ]
    for name, sel in strategies:
        try:
            await page.locator(sel).first.click(timeout=3000)
            if await _card_form_visible():
                log(f"Метод оплаты выбран ({name})")
                return True
        except Exception:
            continue

    # Текстовый вариант (если логотипы — не <img>)
    try:
        await page.get_by_text(re.compile("mastercard", re.I)).last.click(timeout=3000)
        if await _card_form_visible():
            log("Метод оплаты выбран (текст mastercard)")
            return True
    except Exception:
        pass

    # Фолбэк: блок после «PayPal / Debit or Credit Card»
    try:
        ok = await page.evaluate(
            """() => {
                const leaves = [...document.querySelectorAll('*')].filter(
                    e => e.childElementCount === 0 &&
                         /Debit or Credit Card/i.test(e.textContent || ''));
                let el = leaves[0];
                while (el && el.parentElement) {
                    const sib = el.nextElementSibling;
                    if (sib && sib.offsetHeight > 30) { sib.click(); return true; }
                    el = el.parentElement;
                }
                return false;
            }"""
        )
        if ok and await _card_form_visible():
            log("Метод оплаты выбран (по соседнему блоку)")
            return True
    except Exception:
        pass

    log("❌ Не смог открыть форму карты (Visa/Mastercard)")
    await _shot(page, "no_card_method", log)
    return False


async def _fill_card_form(page, card: dict, log) -> bool:
    number = (card.get("number") or "").replace(" ", "").replace("-", "")
    cvv = (card.get("cvv") or "").strip()
    expiry = (card.get("expiry") or "").strip()          # «06/31»
    holder = (card.get("name") or "").strip()

    if not number or not cvv or not expiry:
        log("❌ У карты не хватает данных (номер/CVV/срок)")
        return False

    if holder and holder not in ("-", "NA", "N/A"):
        await _fill_by_placeholder(page, "Cardholder", holder)

    if not await _fill_by_placeholder(page, "Card number", number):
        log("❌ Не смог заполнить номер карты")
        await _shot(page, "card_number_fail", log)
        return False
    if not await _fill_by_placeholder(page, "CVV", cvv):
        log("❌ Не смог заполнить CVV")
        await _shot(page, "cvv_fail", log)
        return False

    # Срок: печатаем цифры (маска сама ставит «/»); если маски нет — вводим с «/»
    digits = expiry.replace("/", "").replace(" ", "")
    exp_loc = page.locator("input[placeholder*='MM' i]").first
    if not await _type_into(page, exp_loc, digits):
        log("❌ Не смог заполнить срок действия")
        await _shot(page, "expiry_fail", log)
        return False
    try:
        val = (await exp_loc.input_value()) or ""
        if "/" not in val and len(digits) == 4:
            await _type_into(page, exp_loc, f"{digits[:2]}/{digits[2:]}")
    except Exception:
        pass

    log(f"Реквизиты введены: **** {number[-4:]}, exp {expiry}")
    return True


async def _click_pay(page, log) -> bool:
    for sel in ("button:text-is('Pay')", "button:has-text('Pay')"):
        try:
            btn = page.locator(sel).first
            txt = ((await btn.inner_text()) or "").strip()
            if txt.lower().startswith("paypal"):
                continue
            await btn.click(timeout=5000)
            log("Нажал Pay — жду результат оплаты…")
            return True
        except Exception:
            continue
    log("❌ Не нашёл кнопку Pay")
    await _shot(page, "no_pay_button", log)
    return False


_ERROR_RE = re.compile(
    r"(payment failed|transaction failed|declined|insufficient funds|"
    r"invalid card|card number is invalid|expired card|try again later)", re.I)
_SUCCESS_RE = re.compile(r"(payment success|top[- ]?up success|successful)", re.I)


async def _wait_payment_result(page, log) -> str:
    """Ждёт реакцию формы после Pay: 'error' | 'maybe_ok'."""
    deadline = time.monotonic() + PAY_RESULT_WAIT
    while time.monotonic() < deadline:
        await page.wait_for_timeout(3000)
        txt = await _page_text(page)
        m = _ERROR_RE.search(txt)
        if m:
            log(f"❌ Ошибка оплаты: {m.group(0)}")
            await _shot(page, "pay_error", log)
            return "error"
        if _SUCCESS_RE.search(txt):
            log("Похоже на успех — проверяю баланс…")
            return "maybe_ok"
        try:
            form_visible = await page.locator(
                "input[placeholder*='Card number' i]").first.is_visible()
        except Exception:
            form_visible = False
        if not form_visible:
            log("Форма оплаты закрылась — проверяю баланс…")
            return "maybe_ok"
    log("⚠ Форма не отреагировала за отведённое время — проверяю баланс…")
    return "maybe_ok"


async def _wait_balance_growth(ctx, balance_before: float | None,
                               amount: float, log) -> tuple[bool, float | None]:
    """Открывает /usage в отдельной вкладке и ждёт роста Topped-up balance."""
    page2 = await ctx.new_page()
    try:
        deadline = time.monotonic() + BALANCE_WAIT
        last = None
        while time.monotonic() < deadline:
            try:
                await page2.goto(f"{BASE_URL}/usage",
                                 wait_until="domcontentloaded", timeout=30000)
                await page2.wait_for_timeout(3000)
                await _dismiss_notices(page2)
                last = await _read_topped_up_balance(page2)
            except Exception:
                last = None
            if last is not None:
                if balance_before is None:
                    # стартовый баланс неизвестен — считаем успехом сам факт ненулевого баланса
                    if last >= amount - 0.011:
                        return True, last
                elif last >= balance_before + amount - 0.011:
                    return True, last
            await asyncio.sleep(8)
        return False, last
    finally:
        try:
            await page2.close()
        except Exception:
            pass


# ── Публичный вход ───────────────────────────────────────────────────────────

async def topup(email: str, password: str, amount: float, card: dict,
                headless: bool = False, log=None,
                keep_open_on_fail: bool = True,
                login_method: str = "password") -> tuple[bool, str]:
    """Пополняет API-баланс DeepSeek на `amount` USD картой `card`.

    card — dict формата data/cards.json (number/expiry/cvv/name/…).
    login_method: "password" (email+пароль DeepSeek) или "google"
    (кнопка «Log in with Google»; password — пароль Google, можно пустой,
    тогда окно Google заполняется вручную).
    Возвращает (успех, сообщение для пользователя).
    """
    log = _safe_log(log or print)
    email = (email or "").strip()
    amount = round(float(amount), 2)
    if not email or (not password and login_method != "google"):
        return False, "Не указаны email или пароль DeepSeek"
    if amount <= 0:
        return False, "Сумма должна быть больше нуля"

    from playwright.async_api import async_playwright

    prof = _profile_dir(email)
    prof.mkdir(parents=True, exist_ok=True)

    log(f"Запускаю браузер (профиль {prof.name})…")
    pw = await async_playwright().start()
    ctx = None
    failed_keep_open = False
    try:
        kw: dict = {
            "headless": headless,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
                "--disable-notifications",
            ],
            "locale": "en-US",
            "ignore_https_errors": True,
        }
        if headless:
            kw["viewport"] = {"width": 1440, "height": 900}
            # CloudFront отдаёт 403 на UA «HeadlessChrome» — маскируемся под обычный
            kw["user_agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/131.0.0.0 Safari/537.36")
        else:
            kw["args"].append("--start-maximized")
            kw["no_viewport"] = True
        ctx = await pw.chromium.launch_persistent_context(str(prof), **kw)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 1. Логин (или уже залогинен — тогда /usage откроется сразу)
        await page.goto(f"{BASE_URL}/usage", wait_until="domcontentloaded", timeout=60000)
        state = await _wait_usage_or_signin(page)
        if state == "signin":
            if login_method == "google":
                logged = await _login_google(page, email, password, log, headless=headless)
            else:
                logged = await _login(page, email, password, log, headless=headless)
            if not logged:
                failed_keep_open = True
                return False, "Не удалось войти в аккаунт DeepSeek"
            await page.goto(f"{BASE_URL}/usage", wait_until="domcontentloaded", timeout=60000)
            state = await _wait_usage_or_signin(page)
        if state == "unknown":
            log("⚠ Страница /usage не распозналась — продолжаю на свой страх")
            await _shot(page, "usage_unknown", log)

        await _dismiss_notices(page)
        balance_before = await _read_topped_up_balance(page)
        if balance_before is None:
            log("⚠ Не смог прочитать текущий баланс — продолжаю без него")
        else:
            log(f"Баланс до пополнения: ${balance_before:.2f}")

        # 2. Страница пополнения
        log(f"Открываю Top up, сумма ${amount:g}…")
        await page.goto(f"{BASE_URL}/top_up", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        await _dismiss_notices(page)

        if not await _select_amount(page, amount, log):
            failed_keep_open = True
            return False, "Не удалось выбрать сумму пополнения"
        if not await _select_card_method(page, log):
            failed_keep_open = True
            return False, "Не удалось открыть форму оплаты картой"
        if not await _fill_card_form(page, card, log):
            failed_keep_open = True
            return False, "Не удалось заполнить реквизиты карты"
        if not await _click_pay(page, log):
            failed_keep_open = True
            return False, "Не удалось нажать Pay"

        # 3. Результат
        res = await _wait_payment_result(page, log)
        if res == "error":
            failed_keep_open = True
            return False, "Оплата отклонена — см. скриншот в debug/deepseek"

        ok, bal = await _wait_balance_growth(ctx, balance_before, amount, log)
        if ok:
            msg = f"✅ Пополнено ${amount:g}, баланс DeepSeek: ${bal:.2f}"
            log(msg)
            return True, msg

        failed_keep_open = True
        cur = f"${bal:.2f}" if bal is not None else "неизвестен"
        msg = (f"Баланс не вырос за {BALANCE_WAIT // 60} мин (сейчас {cur}) — "
               f"проверьте оплату вручную")
        log(f"⚠ {msg}")
        await _shot(page, "balance_not_grown", log)
        return False, msg

    except Exception as e:
        log(f"❌ Ошибка: {e}")
        try:
            if ctx and ctx.pages:
                await _shot(ctx.pages[0], "exception", log)
        except Exception:
            pass
        failed_keep_open = True
        return False, f"Ошибка: {e}"
    finally:
        # При неудаче в видимом режиме не закрываем браузер сразу —
        # можно завершить оплату вручную; закроется вместе с окном.
        if ctx and failed_keep_open and not headless and keep_open_on_fail:
            log(f"Браузер оставлен открытым для ручного завершения "
                f"(до {KEEP_OPEN_ON_FAIL // 60} мин — закройте окно, когда закончите)")
            deadline = time.monotonic() + KEEP_OPEN_ON_FAIL
            try:
                while time.monotonic() < deadline and ctx.pages:
                    await asyncio.sleep(5)
            except Exception:
                pass
        try:
            if ctx:
                await ctx.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass
