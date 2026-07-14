"""Self-check: gift card selection + pay_method + payment path wiring."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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

    rep, bal2, need, short = m._gift_shortage_report(343)
    assert "Нужно:" in rep and need % 50 == 0 and short >= 0 and bal2 >= 0

    pm = m._load_pay_method()
    assert pm in ("card", "gift"), pm

    # Source contracts: gift path must honor pay_method in fill + buy
    src = (ROOT / "menu.py").read_text(encoding="utf-8", errors="replace")
    assert "gift=(_pm == \"gift\")" in src or 'gift=(_pm == "gift")' in src
    assert "_do_gift_card_payment" in src
    assert "_navigate_flipkart_resilient" in src
    # Buy membership opens Flipkart via resilient (not only raw _open_flipkart_page)
    buy_idx = src.find("async def _do_buy_membership")
    assert buy_idx > 0
    buy_chunk = src[buy_idx: buy_idx + 4500]
    assert "_navigate_flipkart_resilient" in buy_chunk, "buy must use resilient navigate"

    # Sticky cancel must not survive shutdown / is cleared at purchase entry
    m._purchase_cancel.set()
    m.disconnect_vpn_on_shutdown()
    assert not m._purchase_cancel.is_set(), "cancel sticky after disconnect_vpn_on_shutdown"
    m._purchase_cancel.set()
    # simulate entry points
    assert " _purchase_cancel.clear()" in src or "_purchase_cancel.clear()" in src

    print("PASS gift_payment_helpers")


if __name__ == "__main__":
    main()
