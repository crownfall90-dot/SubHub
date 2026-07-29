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

    # ── импорт из аккаунта: одна кнопка, без ссылок и ввода ─────────────
    import menu as _menu
    assert callable(_menu._import_gift_cards_from_bitrefill)
    import inspect
    assert not [p for p in inspect.signature(
        _menu._import_gift_cards_from_bitrefill).parameters.values()
        if p.default is inspect.Parameter.empty], 'кнопка не должна требовать аргументов'
    assert callable(b.import_all_cards)
    assert not hasattr(b, 'parse_order_url'), 'импорт по ссылке должен был уйти'

    # вставленная страница заказа разбирается общим парсером хранилища
    parsed, errs = _menu._parse_gift_cards(PAGE)
    assert not errs and len(parsed) == 3, (parsed, errs)

    # ── импортированное должно проходить общий парсер хранилища ───────────
    import menu as m
    text = "\n".join(f"{c['number']}\n{c['pin']}" for c in cards)
    parsed, errs = m._parse_gift_cards(text, 100)
    assert not errs and len(parsed) == 3, (parsed, errs)

    print("PASS bitrefill_import")


if __name__ == "__main__":
    main()
