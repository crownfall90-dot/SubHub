"""Lightweight check that critical Flipkart UI selector strings still exist in menu.py."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU = (ROOT / "menu.py").read_text(encoding="utf-8", errors="replace")

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
    "_VPN_DEFAULT_COUNTRY",
    "_vpn_toggle_same_country",
    "_flipkart_reload_and_check",
    "_VPN_FLIPKART_COUNTRY_ORDER",
    "stop_at_payment",
    "errors.edgesuite.net",
]



def main() -> None:
    missing = [s for s in REQUIRED if s not in MENU]
    if missing:
        print("FAIL missing selectors/contracts:", ", ".join(missing))
        sys.exit(1)
    if '_VPN_DEFAULT_COUNTRY = "us"' not in MENU and "_VPN_DEFAULT_COUNTRY = 'us'" not in MENU:
        print("FAIL _VPN_DEFAULT_COUNTRY is not us")
        sys.exit(1)
    idx = MENU.find("_VPN_FLIPKART_COUNTRY_ORDER")
    chunk = MENU[idx: idx + 200]
    if '"ca"' not in chunk and "'ca'" not in chunk:
        print("FAIL Canada missing from FLIPKART country order")
        sys.exit(1)
    if "_vpn_free_country_codes_static" not in MENU or "_flipkart_vpn_country_queue" not in MENU:
        print("FAIL free-country queue helpers missing")
        sys.exit(1)
    print("PASS selector_health")


if __name__ == "__main__":
    main()
