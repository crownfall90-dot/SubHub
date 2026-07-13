"""
GGSell → DeepSeek: авто-обработка заказов на пополнение API-баланса.

Логика:
  1. Монитор ловит заказ товара DeepSeek (по названию товара).
  2. Сумма пополнения берётся из опций заказа (или названия товара) — «5$», «$10» и т.п.
  3. Email/пароль аккаунта DeepSeek: из параметров заказа; если их нет —
     бот пишет покупателю в чат и ждёт «email пароль» одной строкой через пробел
     или двумя отдельными сообщениями (сначала email, потом пароль).
  4. Когда всё есть — запускается deepseek.topup() с первой картой по приоритету.
  5. Успех/ошибка: сообщение покупателю + уведомление в TG-бот и GUI.

Состояние заказов хранится в data/ggsel_deepseek.json и переживает перезапуск.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

_DATA = Path(__file__).resolve().parent.parent / "data"
_STATE_FILE = _DATA / "ggsel_deepseek.json"
_CARDS_FILE = _DATA / "cards.json"
_CARD_ORDER_FILE = _DATA / "card_order.json"

# Распознавание товара DeepSeek по названию
_PRODUCT_RE = re.compile(r"deep\s*[-_]?\s*seek|дипсик", re.I)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Сумма: «5$», «$5», «5 usd», «5 долларов»
_AMOUNT_RE = re.compile(
    r"(?:\$\s*(\d+(?:[.,]\d+)?))|(?:(\d+(?:[.,]\d+)?)\s*(?:\$|usd|долл))", re.I)

# Ограничение на разовое авто-пополнение (защита от кривой распарсенной суммы)
MAX_AUTO_AMOUNT = 500.0
# «Зависший» запуск: если оплата шла дольше — считаем сбоем (после рестарта)
STALE_RUN_SECONDS = 1800

# Одновременно только одна оплата (одна карта, меньше сюрпризов)
_pay_lock = asyncio.Lock()


# ── Состояние ────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def has_pending(invoice_id: int) -> bool:
    """Ждёт ли заказ данных из чата (для хука монитора на сообщения)."""
    st = _load_state().get(str(invoice_id)) or {}
    return st.get("status") in ("waiting_creds", "waiting_password")


def sweep_stale() -> None:
    """После рестарта: оплаты, оборванные на середине, помечаем сбойными."""
    state = _load_state()
    changed = False
    for inv, st in state.items():
        if st.get("status") == "running" and \
                time.time() - float(st.get("run_ts") or 0) > STALE_RUN_SECONDS:
            st["status"] = "failed"
            st["result"] = "Оплата оборвана перезапуском — проверьте вручную"
            changed = True
            _notify(int(inv), f"⚠️ DeepSeek #{inv}: оплата была оборвана "
                              f"перезапуском — проверьте вручную")
    if changed:
        _save_state(state)


# ── Распознавание заказа и данных ────────────────────────────────────────────

def _product_name(order: dict) -> str:
    product = order.get("product") or {}
    return str(product.get("name") or product.get("product_name")
               or order.get("product_name") or order.get("name")
               or order.get("offer_title") or order.get("title") or "")


def is_deepseek_order(order: dict) -> bool:
    return bool(_PRODUCT_RE.search(_product_name(order)))


def _iter_options(order: dict, info_content: dict, info_v2: dict):
    """Все опции заказа как пары (name, value) из всех источников API."""
    for opt in (order.get("options") or []) + (info_content.get("options") or []):
        name = str(opt.get("name") or opt.get("title") or opt.get("label") or "")
        val = str(opt.get("user_data") or opt.get("value") or opt.get("selected") or "")
        if name or val:
            yield name, val
    sel = (order.get("selected_options") or []) \
        + (info_content.get("selected_options") or []) \
        + (info_v2.get("selected_options") or [])
    for s in sel:
        s = str(s)
        if ": " in s:
            name, val = s.split(": ", 1)
        else:
            name, val = "", s
        yield name.strip(), val.strip()


def _parse_amount_str(text: str) -> Optional[float]:
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    try:
        return float((m.group(1) or m.group(2)).replace(",", "."))
    except Exception:
        return None


def _extract_amount(order: dict, info_content: dict, info_v2: dict) -> Optional[float]:
    # 1) Опции заказа (выбранная сумма)
    for name, val in _iter_options(order, info_content, info_v2):
        amt = _parse_amount_str(f"{name} {val}")
        if amt:
            return amt
    # 2) Название товара (отдельный оффер на конкретную сумму)
    amt = _parse_amount_str(_product_name(order))
    if amt:
        cnt = 1
        for key in ("cnt_goods", "cnt", "quantity", "count"):
            try:
                v = int(order.get(key) or info_content.get(key)
                        or info_v2.get(key) or 0)
                if v > 1:
                    cnt = v
                    break
            except Exception:
                pass
        return amt * cnt
    return None


def _extract_creds(order: dict, info_content: dict, info_v2: dict) -> tuple[str, str]:
    """(email, password) из параметров заказа; пустые строки если не заданы."""
    email, password = "", ""
    for name, val in _iter_options(order, info_content, info_v2):
        nl, val = name.lower(), val.strip()
        if not val:
            continue
        if any(k in nl for k in ("парол", "password", "pass")):
            password = val
        elif _EMAIL_RE.fullmatch(val):
            email = val
        elif any(k in nl for k in ("почт", "email", "mail", "логин", "login", "аккаунт")):
            m = _EMAIL_RE.search(val)
            if m:
                email = m.group(0)
                rest = val.replace(m.group(0), "", 1).strip(" :;,/|")
                if rest and not password:
                    password = rest
    return email, password


def _parse_creds_from_message(text: str) -> tuple[str, str]:
    """(email, password) из сообщения покупателя. Пустые строки если нет."""
    text = str(text or "").strip()
    m = _EMAIL_RE.search(text)
    if not m:
        return "", ""
    email = m.group(0)
    rest = text.replace(email, "", 1)
    # убираем подписи вида «почта», «пароль:», «pass -» в любом месте строки
    rest = re.sub(
        r"(?i)\b(почт[аыу]?|e-?mail|мейл|логин|login|парол[ья]?|password|pass)\b\s*[:\-—=]?\s*",
        " ", rest)
    rest = rest.strip(" :;,\n\t")
    password = ""
    tokens = rest.split()
    if len(tokens) == 1 and "\n" not in rest and len(tokens[0]) >= 4:
        password = tokens[0]
    return email, password


def _looks_like_password(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and " " not in t and "\n" not in t and 4 <= len(t) <= 64


# ── Карта для оплаты ─────────────────────────────────────────────────────────

def _pick_card() -> Optional[dict]:
    """Первая карта по сохранённому порядку (data/card_order.json)."""
    try:
        cards = json.loads(_CARDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        cards = []
    if not isinstance(cards, list) or not cards:
        return None
    try:
        order = json.loads(_CARD_ORDER_FILE.read_text(encoding="utf-8"))
        for i in order:
            if isinstance(i, int) and 0 <= i < len(cards):
                return cards[i]
    except Exception:
        pass
    return cards[0]


# ── Уведомления (TG-бот + GUI) ───────────────────────────────────────────────

def _notify(invoice_id: int, text: str) -> None:
    try:
        from .monitor import emit_ggs_notify
        emit_ggs_notify({"type": "ds_status", "invoice_id": invoice_id, "text": text})
    except Exception:
        pass


def _tpl(name: str) -> str:
    from .monitor import get_template
    return get_template(name)


def _fmt_amount(amount: float) -> str:
    return f"{amount:g}"


# ── Основной флоу ────────────────────────────────────────────────────────────

async def handle_new_order(client, invoice_id: int, order: dict) -> None:
    """Новый заказ DeepSeek: определить сумму и данные, при нехватке — спросить в чате."""
    info_content, info_v2 = {}, {}
    try:
        info = await client.get_order_info(invoice_id)
        info_content = (info.get("content") if isinstance(info, dict) else {}) or {}
    except Exception as exc:
        logger.debug(f"DeepSeek #{invoice_id}: order_info: {exc}")
    try:
        info_v2 = await client.get_order_info_v2(invoice_id) or {}
    except Exception as exc:
        logger.debug(f"DeepSeek #{invoice_id}: order_info_v2: {exc}")

    amount = _extract_amount(order, info_content, info_v2)
    email, password = _extract_creds(order, info_content, info_v2)

    state = _load_state()
    st = {
        "status": "new",
        "amount": amount,
        "email": email,
        "password": password,
        "created_ts": time.time(),
        "product": _product_name(order)[:80],
    }
    state[str(invoice_id)] = st

    if amount is None or amount <= 0 or amount > MAX_AUTO_AMOUNT:
        st["status"] = "failed"
        st["result"] = f"Не удалось определить сумму пополнения (получено: {amount})"
        _save_state(state)
        logger.warning(f"DeepSeek #{invoice_id}: сумма не распознана — ручная обработка")
        _notify(invoice_id,
                f"⚠️ DeepSeek #{invoice_id}: не смог определить сумму пополнения "
                f"из заказа — выполните вручную через приложение (страница DeepSeek)")
        try:
            await client.send_message(invoice_id, _tpl("ds_delay"))
        except Exception:
            pass
        return

    logger.info(f"DeepSeek #{invoice_id}: сумма ${amount:g}, "
                f"email={'есть' if email else 'нет'}, пароль={'есть' if password else 'нет'}")

    if email and password:
        _save_state(state)
        await _start_topup(client, invoice_id)
        return

    # Данных нет — спрашиваем в чате
    st["status"] = "waiting_creds" if not email else "waiting_password"
    _save_state(state)
    try:
        if not email:
            await client.send_message(invoice_id, _tpl("ds_ask_creds"))
        else:
            await client.send_message(invoice_id, _tpl("ds_ask_password"))
    except Exception as exc:
        logger.error(f"DeepSeek #{invoice_id}: не смог отправить запрос данных: {exc}")
    _notify(invoice_id,
            f"🧠 DeepSeek #{invoice_id}: заказ на ${amount:g}, жду данные аккаунта "
            f"от покупателя в чате GGSell")


async def on_buyer_message(client, invoice_id: int, text: str) -> None:
    """Сообщение покупателя по заказу, ожидающему данные."""
    state = _load_state()
    st = state.get(str(invoice_id))
    if not st or st.get("status") not in ("waiting_creds", "waiting_password"):
        return

    email, password = _parse_creds_from_message(text)
    if email:
        st["email"] = email
        if password:
            st["password"] = password
    elif st.get("email") and _looks_like_password(text):
        st["password"] = str(text).strip()

    if st.get("email") and st.get("password"):
        _save_state(state)
        await _start_topup(client, invoice_id)
        return

    if st.get("email") and not st.get("password"):
        if st["status"] != "waiting_password":
            st["status"] = "waiting_password"
            _save_state(state)
            try:
                await client.send_message(invoice_id, _tpl("ds_ask_password"))
            except Exception:
                pass
        else:
            _save_state(state)
    # ничего полезного в сообщении — молчим, покупатель может писать что угодно


async def _start_topup(client, invoice_id: int) -> None:
    state = _load_state()
    st = state.get(str(invoice_id)) or {}
    amount = float(st.get("amount") or 0)
    email = str(st.get("email") or "")
    password = str(st.get("password") or "")

    card = _pick_card()
    if card is None:
        ...
        st["status"] = "failed"
        st["result"] = "Нет сохранённых карт"
        state[str(invoice_id)] = st
        _save_state(state)
        _notify(invoice_id, f"❌ DeepSeek #{invoice_id}: нет сохранённых карт — "
                            f"добавьте карту и выполните вручную")
        return

    st["status"] = "running"
    st["run_ts"] = time.time()
    state[str(invoice_id)] = st
    _save_state(state)

    try:
        await client.send_message(
            invoice_id, _tpl("ds_processing").format(amount=_fmt_amount(amount)))
    except Exception:
        pass
    _notify(invoice_id, f"🧠 DeepSeek #{invoice_id}: начинаю пополнение ${amount:g} "
                        f"для `{email}` (при отказе — следующая карта)")

    import deepseek as ds
    async with _pay_lock:
        try:
            ok, msg = await ds.topup(
                email, password, amount, card,
                headless=False, keep_open_on_fail=False,
                retry_cards=True,
                log=lambda s: logger.info(f"DeepSeek #{invoice_id}: {s}"),
            )
        except Exception as exc:
            ok, msg = False, f"Ошибка: {exc}"

    state = _load_state()
    st = state.get(str(invoice_id)) or st

    if ok:
        st["status"] = "done"
        st["result"] = msg
        state[str(invoice_id)] = st
        _save_state(state)
        balance = ""
        m = re.search(r"баланс DeepSeek: \$([\d.]+)", msg)
        if m:
            balance = m.group(1)
        try:
            await client.send_message(
                invoice_id,
                _tpl("ds_done").format(amount=_fmt_amount(amount),
                                       balance=balance or "—"))
        except Exception:
            pass
        _notify(invoice_id, f"✅ DeepSeek #{invoice_id}: пополнено ${amount:g} "
                            f"для `{email}`")
        return

    # Неудача: если не смогли войти — запрашиваем данные заново
    login_failed = "войти" in msg.lower()
    st["result"] = msg
    if login_failed:
        st["status"] = "waiting_creds"
        st["password"] = ""
        state[str(invoice_id)] = st
        _save_state(state)
        try:
            await client.send_message(invoice_id, _tpl("ds_fail_creds"))
        except Exception:
            pass
        _notify(invoice_id, f"⚠️ DeepSeek #{invoice_id}: вход не удался "
                            f"(`{email}`) — запросил данные у покупателя заново")
    else:
        st["status"] = "failed"
        state[str(invoice_id)] = st
        _save_state(state)
        try:
            await client.send_message(invoice_id, _tpl("ds_delay"))
        except Exception:
            pass
        _notify(invoice_id,
                f"❌ DeepSeek #{invoice_id}: пополнение не удалось — {msg}\n"
                f"Данные для ручного запуска: `{email}` / `{password}` / ${amount:g}")
