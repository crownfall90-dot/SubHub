"""Lightweight check that critical Flipkart UI selector strings still exist in menu.py."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")

REQUIRED = [
    "Add Gift Card",
    "Have a Flipkart Gift Card",
    "Use Gift Cards",
    "_use_gift_cards_checkbox_state",
    "_select_gift_cards_pay_method",
    "_ensure_voucher_fields",
    "voucher number",
    "Place Order",
    "Buy Now",
    "_do_gift_card_payment",
    "_navigate_flipkart_resilient",
    "_diagnose_flipkart_state",
    "_vpn_fresh_connect_usa",
    "_vpn_connect_country",
    "_flipkart_reload_and_check",
    "stop_at_payment",
    "errors.edgesuite.net",
]



def main() -> None:
    missing = [s for s in REQUIRED if s not in MENU]
    if missing:
        print("FAIL missing selectors/contracts:", ", ".join(missing))
        sys.exit(1)
    # VPN-расширения удалены: страну держит личный VPN пользователя на ПК,
    # проверять порядок стран и хелперы очереди больше нечего.
    print("PASS selector_health")


if __name__ == "__main__":
    main()
