"""Данные заказов GGSell для десктопного GUI SubHub."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

YOUTUBE_PREMIUM_PRODUCT_ID = 102276416
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DONE_FILE = _DATA_DIR / "ggsel_done.json"

STATUS_ICON = {
    "new": "🟢",
    "issued": "🔵",
    "used": "🟡",
    "pending": "⏳",
    "refunded": "🟠",
}
STATUS_LABEL = {
    "new": "Новый",
    "issued": "Выдан",
    "used": "В архиве",
    "pending": "Ожидает подтверждения",
    "refunded": "Возврат",
}


def load_local_state() -> dict[str, Any]:
    try:
        raw = json.loads(_DONE_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return {
        "done": {int(k): v for k, v in (raw.get("done") or {}).items()},
        "used": {int(k): v for k, v in (raw.get("used") or {}).items()},
        "refunded": {int(k): v for k, v in (raw.get("refunded") or {}).items()},
        "links": {int(k): v for k, v in (raw.get("links") or {}).items()},
        "emails": {int(k): v for k, v in (raw.get("buyer_emails") or {}).items()},
        "profile_paths": {int(k): v for k, v in (raw.get("profile_paths") or {}).items()},
    }


def parse_order(order: dict) -> dict:
    product = order.get("product") or {}
    name = (
        product.get("name") or product.get("product_name")
        or order.get("product_name") or order.get("name")
        or order.get("offer_title") or "YouTube Premium"
    )
    buyer = order.get("buyer") or order.get("buyer_info") or {}
    email = (
        buyer.get("email") or order.get("email")
        or order.get("buyer_email") or ""
    )
    sum_buy = (
        order.get("sum_t") or order.get("sum") or order.get("amount_t")
        or order.get("amount") or order.get("buyer_sum")
        or order.get("price_total") or order.get("total") or ""
    )
    sum_sell = (
        order.get("sum_seller") or order.get("seller_sum")
        or order.get("profit") or order.get("payout")
        or order.get("amount_seller") or ""
    )
    status = order.get("status") or order.get("state") or ""
    date = str(order.get("date") or order.get("created_at") or "").replace("T", " ")[:16]

    name_short = str(name)
    parts = [p.strip() for p in str(name).split("|")]
    if len(parts) >= 2:
        name_short = f"{parts[0]} | {parts[1]}"
    if len(name_short) > 60:
        name_short = name_short[:57] + "…"

    parsed_options = []
    for s in (order.get("selected_options") or []):
        s = str(s).strip()
        if ": " not in s:
            continue
        opt_name, rest = s.split(": ", 1)
        opt_name = opt_name.strip()
        rest = re.sub(r"\s*\(\+[\d.]+\s*RUB\)", "", rest).strip()
        if opt_name and rest:
            parsed_options.append({"name": opt_name, "value": rest})

    if not parsed_options:
        for opt in (order.get("options") or []):
            opt_name = (opt.get("name") or opt.get("title") or opt.get("label") or "").strip()
            opt_val = (opt.get("user_data") or opt.get("value") or opt.get("selected") or "").strip()
            if opt_name and opt_val:
                parsed_options.append({"name": opt_name, "value": opt_val})

    return {
        "name": str(name),
        "name_short": name_short,
        "email": str(email),
        "sum_buy": sum_buy,
        "sum_sell": sum_sell,
        "status": str(status),
        "date": date,
        "options": parsed_options,
    }


def invoice_id(order: dict) -> int:
    try:
        return int(order.get("invoice_id") or order.get("id") or 0)
    except Exception:
        return 0


def order_email(order: dict, state: dict, chat_map: dict | None = None) -> str:
    inv = invoice_id(order)
    p = parse_order(order)
    email = (
        (chat_map or {}).get(inv)
        or p["email"]
        or order.get("buyer_email") or order.get("email")
        or (order.get("buyer") or {}).get("email")
        or (order.get("buyer_info") or {}).get("email")
        or state["emails"].get(inv, "")
    )
    return str(email or "")


def status_key(inv: int, state: dict) -> str:
    if inv in state["refunded"]:
        return "refunded"
    if inv in state["used"]:
        return "used"
    if inv in state["done"]:
        return "issued"
    return "new"


def filter_orders(orders: list[dict], state: dict, flt: str) -> list[dict]:
    if flt == "all":
        return orders
    out = []
    for o in orders:
        sk = status_key(invoice_id(o), state)
        if flt == "new" and sk == "new":
            out.append(o)
        elif flt == "issued" and sk == "issued":
            out.append(o)
        elif flt == "used" and sk in ("used", "refunded"):
            out.append(o)
    return out


def _chat_email_map(chats_raw: list) -> dict[int, str]:
    out: dict[int, str] = {}
    for ch in chats_raw or []:
        try:
            inv_id = int(ch.get("id_i") or ch.get("invoice_id") or ch.get("id") or 0)
            if not inv_id:
                continue
            em = (
                ch.get("email") or ch.get("buyer_email") or ch.get("name")
                or (ch.get("buyer") or {}).get("email") or ""
            )
            if em and "@" in em:
                out[inv_id] = em
        except Exception:
            pass
    return out


async def fetch_youtube_orders(client) -> tuple[list[dict], dict[int, str]]:
    orders_v1_task = asyncio.ensure_future(client.get_orders_v1(limit=40))
    chats_task = asyncio.ensure_future(client.get_chats())
    orders_v1, chats_raw = await asyncio.gather(
        orders_v1_task, chats_task, return_exceptions=True,
    )
    if isinstance(orders_v1, Exception):
        orders_v1 = []
    if isinstance(chats_raw, Exception):
        chats_raw = []
    chat_map = _chat_email_map(chats_raw if isinstance(chats_raw, list) else [])

    yt_orders = [
        o for o in (orders_v1 or [])
        if int(o.get("offer_ggsel_id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID
    ]
    if not yt_orders:
        orders = await client.get_last_orders()
        yt_orders = [
            o for o in orders
            if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID
        ]

    yt_orders.sort(key=lambda o: invoice_id(o), reverse=True)

    missing = [
        invoice_id(o) for o in yt_orders[:20]
        if invoice_id(o) and not order_email(o, load_local_state(), chat_map)
    ]
    if missing:
        fetched = await asyncio.gather(
            *[client.get_buyer_email(i) for i in missing],
            return_exceptions=True,
        )
        for inv_i, res in zip(missing, fetched):
            if isinstance(res, str) and res:
                chat_map[inv_i] = res

    return yt_orders, chat_map


def row_label(order: dict, state: dict, chat_map: dict | None = None) -> str:
    inv = invoice_id(order)
    sk = status_key(inv, state)
    icon = STATUS_ICON.get(sk, "•")
    p = parse_order(order)
    email = order_email(order, state, chat_map)
    dt = p["date"]
    dt_show = dt[5:16] if len(dt) >= 16 else dt
    period = ""
    for opt in p["options"]:
        m_p = re.search(r"(\d+)\s*(?:мес|год|month|year)", opt.get("value", "").lower())
        if m_p:
            period = f"({m_p.group(1)}) "
            break
    if email:
        return f"{icon}  {period}{email[:38]}   {dt_show}"
    return f"{icon}  {period}#{inv}   {dt_show}"
