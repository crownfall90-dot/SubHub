"""Self-check: разбор заказа Bitrefill (ссылка и карты со страницы).

Текст страницы взят с реального заказа — 10 карт Flipkart India по ₹100.
Сети и браузера тест не требует.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

PAGE = """Заказ выполнен
Спасибо за покупку

Flipkart India
₹100.00
Код подарочного сертификата
6000170823257591
PIN-код
243092
Отметить как использованный
Копировать ссылку
Чтобы погасить код, перейдите https://www.flipkart.com/

Flipkart India
₹100.00
Код подарочного сертификата
6000170820296925
PIN-код
214881
Отметить как использованный

Flipkart India
₹100.00
Код подарочного сертификата
6000170820682998
PIN-код
128146
"""

PAGE_EN = """Flipkart India
₹500.00
Gift certificate code
6000170825521426
PIN code
291692
"""


def main() -> None:
    import bitrefill as b

    # ── ссылка ────────────────────────────────────────────────────────────
    inv, tok = b.parse_order_url(
        "https://www.bitrefill.com/checkout/098522b5-1b5a-4ff7-8ef3-e8c94a9ddbef"
        "#4BHODBSYyF0WgR1qLolg")
    assert inv == "098522b5-1b5a-4ff7-8ef3-e8c94a9ddbef", inv
    assert tok == "4BHODBSYyF0WgR1qLolg", tok
    # без токена — тоже валидная ссылка
    assert b.parse_order_url("https://www.bitrefill.com/checkout/" + "a" * 20)[1] == ""
    # мусор не проходит
    assert b.parse_order_url("https://example.com/") == ("", "")
    assert b.parse_order_url("") == ("", "")

    # ── карты со страницы ─────────────────────────────────────────────────
    cards = b.cards_from_text(PAGE)
    assert len(cards) == 3, cards
    assert cards[0] == {"denom": 100, "number": "6000170823257591",
                        "pin": "243092", "used": False}, cards[0]
    assert [c["number"] for c in cards] == [
        "6000170823257591", "6000170820296925", "6000170820682998"]
    assert [c["pin"] for c in cards] == ["243092", "214881", "128146"]
    assert all(c["denom"] == 100 for c in cards)

    # английский интерфейс и другой номинал
    en = b.cards_from_text(PAGE_EN)
    assert en == [{"denom": 500, "number": "6000170825521426",
                   "pin": "291692", "used": False}], en

    # повтор той же карты в тексте не даёт дубля
    assert len(b.cards_from_text(PAGE + PAGE)) == 3

    # без суммы на странице берётся номинал по умолчанию
    no_amount = "Код подарочного сертификата\n6000170823257591\nPIN-код\n243092"
    assert b.cards_from_text(no_amount, 250)[0]["denom"] == 250
    assert b.cards_from_text(no_amount) == []      # и нечего угадывать — пропускаем

    # ── номиналы в разных форматах ────────────────────────────────────────
    assert b._amount_to_denom("100.00") == 100
    assert b._amount_to_denom("1,000") == 1000
    assert b._amount_to_denom("100,00") == 100
    assert b._amount_to_denom("") == 0

    # ── карты из JSON, если сайт отдаст их структурой ─────────────────────
    data = {"items": [{"cardNumber": "6000170823257591", "pin": "243092",
                       "value": 100}]}
    got = b.cards_from_invoice_json(data)
    assert got == [{"denom": 100, "number": "6000170823257591",
                    "pin": "243092", "used": False}], got

    # ── импортированное должно проходить общий парсер хранилища ───────────
    import menu as m
    text = "\n".join(f"{c['number']}\n{c['pin']}" for c in cards)
    parsed, errs = m._parse_gift_cards(text, 100)
    assert not errs and len(parsed) == 3, (parsed, errs)

    print("PASS bitrefill_import")


if __name__ == "__main__":
    main()
