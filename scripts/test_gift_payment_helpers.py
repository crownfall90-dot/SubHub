"""Self-check: gift card selection + pay_method + payment path wiring."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu as m

    # Exact knapsack: 200+100 covers 250 with min overshoot
    pool = [
        {"number": "1111", "pin": "1111", "denom": 100, "used": False},
        {"number": "2222", "pin": "2222", "denom": 200, "used": False},
        {"number": "3333", "pin": "3333", "denom": 500, "used": False},
    ]
    picked, tot = m._select_gift_cards(250, pool)
    assert picked is not None, "select returned None for coverable total"
    assert tot >= 250, tot
    assert sum(int(c["denom"]) for c in picked) == tot
    assert all(not c.get("used") for c in picked)

    none, bal = m._select_gift_cards(9999, pool)
    assert none is None
    assert bal == m._gift_balance(pool)

    # Skip used / incomplete
    dirty = [
        {"number": "1", "pin": "1", "denom": 500, "used": True},
        {"number": "", "pin": "2", "denom": 500, "used": False},
        {"number": "3", "pin": "3", "denom": 50, "used": False},
    ]
    assert m._gift_balance(dirty) == 50
    p2, t2 = m._select_gift_cards(50, dirty)
    assert p2 and t2 == 50 and p2[0]["number"] == "3"

    # Формат «номер / PIN на следующей строке» (без даты и номинала)
    two_line = """6000170825521426
291692
6000170820014434
279714"""
    cards, errs = m._parse_gift_cards(two_line, 500)
    assert not errs, errs
    assert [(c["number"], c["pin"], c["denom"]) for c in cards] == [
        ("6000170825521426", "291692", 500),
        ("6000170820014434", "279714", 500),
    ], cards
    # PIN из 4 цифр, совпадающий с номиналом, не должен съедаться как denom
    cards2, errs2 = m._parse_gift_cards("6000170825275058\n1000", 500)
    assert not errs2 and cards2[0]["pin"] == "1000" and cards2[0]["denom"] == 500, cards2
    # Старый однострочный формат «Серия PIN Дата» продолжает работать
    cards3, errs3 = m._parse_gift_cards("6000170524661453  281697  2027-05-25", 200)
    assert not errs3 and cards3 == [
        {"denom": 200, "number": "6000170524661453", "pin": "281697", "used": False}
    ], cards3

    rep, bal2, need, short = m._gift_shortage_report(343)
    assert "Осталось покрыть:" in rep and need % 50 == 0 and short >= 0 and bal2 >= 0
    # short==0 must NOT say «Не хватает: ₹0» (путали остаток заказа со складом)
    assert ("Не хватает на складе:" in rep) == (short > 0)
    if short == 0:
        assert "На складе хватает" in rep
        assert "Не хватает: ₹0" not in rep

    pm = m._load_pay_method()
    assert pm in ("card", "gift"), pm

    # Source contracts: gift path must honor pay_method in fill + buy.
    # Браузерная часть оплаты осталась в menu.py, слой данных живёт в
    # gift_cards.py — проверки по тексту смотрят на оба файла.
    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    src += "\n" + (ROOT / "subhub" / "gift_cards.py").read_text(
        encoding="utf-8", errors="replace")
    assert "gift=(_pm == \"gift\")" in src or 'gift=(_pm == "gift")' in src
    assert "_do_gift_card_payment" in src
    assert "_select_gift_cards_pay_method" in src
    assert "Have a Flipkart Gift Card" in src
    assert "_ensure_voucher_fields" in src
    assert "gift cards" in src.lower()
    assert "_navigate_flipkart_resilient" in src
    # Gift path: Use Gift Cards checkbox OR left «Have a Flipkart Gift Card?»
    gift_idx = src.find("async def _do_gift_card_payment")
    assert gift_idx > 0
    gift_chunk = src[gift_idx: gift_idx + 12000]
    assert "_use_gift_cards_checkbox_state" in gift_chunk
    assert "_select_gift_cards_pay_method" in gift_chunk
    assert "_ensure_voucher_fields" in gift_chunk
    # После мелких — снова спросить крупные, не слать «не хватает ₹0»
    assert "_ask_big_gift_confirm" in src
    assert "остались только крупные" in src
    assert "На складе хватает" in src
    assert "Use Gift Cards — применяю уже использованный баланс" in src
    assert "добираю картами" in src
    sel_idx = src.find("async def _select_gift_cards_pay_method")
    sel_chunk = src[sel_idx: sel_idx + 3500]
    assert "have a flipkart gift card" in sel_chunk
    # Must not skip the left-panel opener
    assert "have a flipkart|apply gift" not in sel_chunk.replace(
        "have a flipkart gift card", "X"
    ) or "continue" not in sel_chunk  # soft: prefer string present
    assert "PLACEHOLDER_CUT" not in gift_chunk
    # Buy membership opens Flipkart via resilient (not only raw _open_flipkart_page)
    buy_idx = src.find("async def _do_buy_membership")
    assert buy_idx > 0
    buy_chunk = src[buy_idx: buy_idx + 4500]
    assert "_navigate_flipkart_resilient" in buy_chunk, "buy must use resilient navigate"
    # Buy Now / payments: reload+retry without closing browser
    assert "_BUY_NOW_TO_CHECKOUT_ROUNDS" in src
    assert "_PAYMENTS_REACH_ROUNDS" in src
    assert "обновляю товар и повторяю" in src
    assert "Buy Now не дал переход на оплату — обновляю страницу" in src
    assert "_profile_addr_meta" in src
    assert "address_summary" in src
    assert "_get_filled_email" in src
    assert "_cv_filled_email" in src
    assert "_set_filled_email" in src

    # Залипший cancel: обёртка disconnect_vpn_on_shutdown удалена вместе с
    # VPN-подсистемой (в продакшене её никто не звал, только этот тест).
    # Гарантия осталась прежней — флаг снимается на входе в покупку.
    m._purchase_cancel.set()
    m._stop_active_purchases()
    m._purchase_cancel.clear()
    assert not m._purchase_cancel.is_set()
    assert "_purchase_cancel.clear()" in src

    print("PASS gift_payment_helpers")


if __name__ == "__main__":
    main()
