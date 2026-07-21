"""Playwright worker for background Flipkart logins."""

import asyncio
import io
import json
import random
import shutil
import sys
import threading
import time
from pathlib import Path

import httpx
from grizzly_sms import GrizzlySMSClient
from playwright.async_api import async_playwright


_ACTIVE_IDS: set[str] = set()
_ACTIVE_IDS_LOCK = threading.Lock()


async def _send_cookies_to_tg(ctx, phone_10: str, grizzly) -> None:
    """Export only the required Flipkart cookies as a Telegram document."""
    token = grizzly._get_telegram_token_standalone()
    chats = grizzly._get_tg_subscribers_standalone()
    if not token or not chats:
        return
    allowed = {"T", "ULSN", "at", "rt", "vd", "ud", "S", "SN"}
    cookies = [
        cookie for cookie in await ctx.cookies()
        if "flipkart.com" in str(cookie.get("domain") or "").lower()
        and cookie.get("name") in allowed
    ]
    if not cookies:
        return
    payload = json.dumps(cookies, ensure_ascii=False, indent=2).encode("utf-8")
    backup = Path(__file__).parent / "cookies_backup" / f"cookies_{phone_10}.json"
    backup.parent.mkdir(exist_ok=True)
    tmp = backup.with_suffix(".json.tmp")
    tmp.write_bytes(payload)
    tmp.replace(backup)
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        for chat_id in chats:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": str(chat_id), "caption": f"🍪 Cookies +91{phone_10} (фон)"},
                    files={"document": (backup.name, io.BytesIO(payload), "application/json")},
                )
            except httpx.RequestError:
                continue


def submit_bg_login(api_key: str, activation_id: str, otp_code: str,
                    login_url: str, months: int, phone_10: str, loop):
    """Schedule at most one background login per activation ID."""
    aid = str(activation_id)
    with _ACTIVE_IDS_LOCK:
        if aid in _ACTIVE_IDS:
            return None
        _ACTIVE_IDS.add(aid)
    try:
        return asyncio.run_coroutine_threadsafe(
            _run_and_release(api_key, aid, otp_code, login_url, months, phone_10),
            loop,
        )
    except BaseException:
        with _ACTIVE_IDS_LOCK:
            _ACTIVE_IDS.discard(aid)
        raise


async def _run_and_release(api_key: str, activation_id: str, otp_code: str,
                           login_url: str, months: int, phone_10: str) -> None:
    try:
        await _bg_login_with_otp(
            api_key, activation_id, otp_code, login_url, months, phone_10
        )
    finally:
        with _ACTIVE_IDS_LOCK:
            _ACTIVE_IDS.discard(str(activation_id))


async def _bg_login_with_otp(api_key: str, activation_id: str, otp_code: str,
                             login_url: str, months: int,
                             phone_10: str = "") -> None:
    """Log in with an OTP using the already-loaded menu module."""
    menu = sys.modules.get("menu")
    grizzly = sys.modules.get("grizzly")
    if menu is None or grizzly is None:
        raise RuntimeError("menu and grizzly must be loaded before background login")

    done_profiles_dir = menu.DONE_PROFILES_DIR
    pre_inject_chrome_prefs = menu._pre_inject_chrome_prefs
    browser_launch_kw = menu._browser_launch_kw
    flipkart_phase1 = menu._flipkart_phase1
    otp_sel = menu._OTP_SEL

    client = None
    pw = None
    ctx2 = None
    profile_path = None
    bg_del_profile = False
    auto_submitted = False

    try:
        client = GrizzlySMSClient(api_key, http_timeout=15)
        pw = await async_playwright().start()

        try:
            if not phone_10:
                try:
                    acts = await client.get_active_activations()
                    for activation in acts:
                        aid = str(
                            activation.get("activationId")
                            or activation.get("id")
                            or ""
                        )
                        if aid == str(activation_id):
                            raw_phone = str(
                                activation.get("phoneNumber")
                                or activation.get("phone")
                                or ""
                            ).lstrip("+")
                            if raw_phone.startswith("91") and len(raw_phone) > 10:
                                raw_phone = raw_phone[2:]
                            phone_10 = raw_phone[-10:]
                            break
                except Exception:
                    pass

            if not phone_10:
                print(f"  [BG] Не удалось определить номер для id={activation_id}")
                return

            profile_path = done_profiles_dir / f"profile_{phone_10}"
            if profile_path.exists():
                print(f"  [BG] Профиль +91 {phone_10} уже существует, пропускаю")
                try:
                    await client.complete(activation_id)
                except Exception:
                    pass
                grizzly.mark_completed(activation_id)
                return

            try:
                phone_variants = (phone_10, "91" + phone_10)
                if any(
                    any(variant in path.name for variant in phone_variants)
                    for path in done_profiles_dir.iterdir()
                    if path.is_dir()
                ):
                    print(
                        f"  [BG] Профиль +91 {phone_10} уже существует "
                        "(другое имя), пропускаю"
                    )
                    try:
                        await client.complete(activation_id)
                    except Exception:
                        pass
                    grizzly.mark_completed(activation_id)
                    return
            except Exception:
                pass

            profile_path.mkdir(parents=True, exist_ok=True)
            pre_inject_chrome_prefs(profile_path)

            ctx2 = await pw.chromium.launch_persistent_context(
                str(profile_path.resolve()),
                **browser_launch_kw(
                    headless=True, phone=phone_10, profile_path=profile_path
                ),
            )
            page2 = ctx2.pages[0] if ctx2.pages else await ctx2.new_page()
            phase1_result = await flipkart_phase1(page2, login_url, phone_10)
            if phase1_result != "ok":
                print(f"  [BG] Фаза1 не прошла: {phase1_result}")
                await grizzly._tg_login_fail_notify(
                    phone_10,
                    otp_code,
                    f"Фаза 1 не прошла (ввод номера): {phase1_result}",
                )
                bg_del_profile = True
                return

            print(f"  [BG] +91 {phone_10}: OTP получен — ввожу")
            otp_el = page2.locator(otp_sel).first
            try:
                await otp_el.wait_for(state="visible", timeout=15_000)
                box = await otp_el.bounding_box()
                if box:
                    await page2.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                else:
                    await otp_el.click()
                await page2.wait_for_timeout(150)
                await page2.keyboard.press("Control+a")
                await page2.keyboard.press("Delete")
                for char in otp_code:
                    await page2.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.05, 0.10))
                    try:
                        if "login" not in page2.url.lower():
                            auto_submitted = True
                            break
                    except Exception:
                        auto_submitted = True
                        break
                await page2.wait_for_timeout(400)
            except Exception as exc:
                print(f"  [BG] Ошибка ввода OTP: {exc}. Fallback keyboard...")
                try:
                    await page2.keyboard.type(otp_code, delay=80)
                except Exception:
                    pass

            try:
                login_success = auto_submitted and "login" not in page2.url.lower()
            except Exception:
                login_success = auto_submitted
            deadline = time.time() + 30.0
            button_clicked = False

            try:
                await page2.wait_for_timeout(300)
            except Exception:
                pass

            while time.time() < deadline:
                try:
                    current_url = page2.url.lower()
                except Exception:
                    if auto_submitted:
                        login_success = True
                    break
                if "login" not in current_url:
                    login_success = True
                    break

                if not button_clicked:
                    try:
                        otp_value = await page2.eval_on_selector(
                            otp_sel, "el => el.value"
                        )
                        if not otp_value:
                            print(
                                f"  [BG] Поле OTP пустое, ввожу заново "
                                f"для +91 {phone_10}..."
                            )
                            otp_el = page2.locator(otp_sel).first
                            await otp_el.click()
                            await page2.keyboard.type(otp_code, delay=80)
                            await page2.wait_for_timeout(200)
                    except Exception:
                        pass

                if not button_clicked:
                    verify_selectors = [
                        "button:has-text('VERIFY')",
                        "button:has-text('Verify')",
                        "button:has-text('LOGIN')",
                        "button:has-text('Login')",
                        "button:has-text('CONTINUE')",
                        "button:has-text('Continue')",
                        "button:has-text('Signup')",
                        "button:has-text('SIGNUP')",
                    ]
                    clicked = False
                    for selector in verify_selectors:
                        try:
                            button = page2.locator(selector).first
                            if await button.is_visible():
                                box = await button.bounding_box()
                                if box:
                                    await page2.mouse.click(
                                        box["x"] + box["width"] / 2,
                                        box["y"] + box["height"] / 2,
                                    )
                                else:
                                    await button.click()
                                clicked = True
                                button_clicked = True
                                break
                        except Exception:
                            pass
                    if not clicked:
                        try:
                            otp_locator = page2.locator(otp_sel).first
                            if await otp_locator.count() > 0:
                                await otp_locator.press("Enter")
                            else:
                                await page2.keyboard.press("Enter")
                        except Exception:
                            try:
                                await page2.keyboard.press("Enter")
                            except Exception:
                                pass

                await page2.wait_for_timeout(1000)

            if login_success:
                try:
                    (profile_path / ".profile_meta.json").write_text(
                        json.dumps(
                            {
                                "username": phone_10,
                                "login_ts": time.time(),
                                "source": "bg_loser",
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    grizzly._STATS["profiles_saved"] += 1
                except Exception:
                    pass
                try:
                    await client.complete(activation_id)
                except Exception:
                    pass
                print(
                    f"  [BG✓] Профиль +91 {phone_10} сохранён "
                    "(фоновый вход)"
                )
                try:
                    await grizzly._tg_login_ok_notify(phone_10)
                except Exception:
                    pass
                try:
                    await _send_cookies_to_tg(ctx2, phone_10, grizzly)
                except Exception as exc:
                    print(f"  [BG] Ошибка отправки кук в TG: {exc}")
            else:
                print(
                    f"  [BG] Фоновый вход +91 {phone_10} не прошёл "
                    "в течение 30 секунд"
                )
                await grizzly._tg_login_fail_notify(
                    phone_10,
                    otp_code,
                    "Таймаут входа (30 секунд истекло, сайт не перенаправил)",
                )
                bg_del_profile = True
        except BaseException as exc:
            error_text = str(exc).lower()
            connection_closed = any(
                marker in error_text
                for marker in (
                    "connection",
                    "closed",
                    "driver",
                    "disconnected",
                    "target closed",
                )
            )
            if connection_closed and auto_submitted:
                print(
                    f"  [BG] Соединение с браузером потеряно для +91 "
                    f"{phone_10}, но страница уже перешла с логина — "
                    "профиль сохраняем"
                )
                try:
                    meta_path = profile_path / ".profile_meta.json"
                    if profile_path and not meta_path.exists():
                        meta_path.write_text(
                            json.dumps(
                                {
                                    "username": phone_10,
                                    "login_ts": time.time(),
                                    "source": "bg_loser",
                                },
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                        grizzly._STATS["profiles_saved"] += 1
                except Exception:
                    pass
                try:
                    await client.complete(activation_id)
                except Exception:
                    pass
                try:
                    await grizzly._tg_login_ok_notify(phone_10)
                except Exception:
                    pass
                bg_del_profile = False
            else:
                if not isinstance(exc, Exception):
                    print(
                        f"  [BG] Прервано ({type(exc).__name__}) для "
                        f"+91 {phone_10} — профиль удаляется"
                    )
                else:
                    print(
                        f"  [BG] Ошибка при фоновом входе +91 "
                        f"{phone_10}: {exc}"
                    )
                try:
                    await grizzly._tg_login_fail_notify(
                        phone_10, otp_code, f"{type(exc).__name__}: {exc}"
                    )
                except Exception:
                    pass
                bg_del_profile = True
    finally:
        if ctx2 is not None:
            try:
                await ctx2.close()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        if bg_del_profile and profile_path and profile_path.exists():
            try:
                shutil.rmtree(profile_path, ignore_errors=True)
                print(
                    f"  [BG] Профиль +91 {phone_10} удалён "
                    "(неуспешный вход)"
                )
            except Exception:
                pass
