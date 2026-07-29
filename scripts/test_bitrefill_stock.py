"""Self-check: разбор наличия Bitrefill и текст уведомления.

Фикстуры — реальный ответ `/api/product/flipkart-india`. Без сети и браузера.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

# Так сайт отвечает сейчас: распродано, остался номинал 1000 INR
OUT_OF_STOCK = {
    "name": "Flipkart India", "currency": "INR", "countryCode": "IN",
    "outOfStock": True,
    "packages": [{"value": "1000", "amount": 1000, "usdPrice": 12.9712, "eurPrice": 11.38}],
}
IN_STOCK = {
    "name": "Flipkart India", "currency": "INR", "countryCode": "IN",
    "outOfStock": False,
    "packages": [
        {"value": "1000", "amount": 1000, "usdPrice": 12.97},
        {"value": "100", "amount": 100, "usdPrice": 1.29},
        {"value": "500", "amount": 500, "usdPrice": 6.48},
    ],
}


def main() -> None:
    import bitrefill as b

    # ── нет в наличии ────────────────────────────────────────────────────
    st = b.stock_from_product(OUT_OF_STOCK)
    assert st["in_stock"] is False, st
    assert st["currency"] == "INR" and st["name"] == "Flipkart India"
    assert st["denoms"] == [{"value": 1000, "usd": 12.97}], st["denoms"]
    msg = b.stock_message(st)
    assert "нет в наличии" in msg and "Сообщу" in msg, msg
    assert "В НАЛИЧИИ" not in msg

    # ── появилось ────────────────────────────────────────────────────────
    st2 = b.stock_from_product(IN_STOCK)
    assert st2["in_stock"] is True
    # номиналы отсортированы по возрастанию — так их читать удобнее
    assert [d["value"] for d in st2["denoms"]] == [100, 500, 1000], st2["denoms"]
    msg2 = b.stock_message(st2)
    assert "В НАЛИЧИИ" in msg2, msg2
    for want in ("100 INR", "500 INR", "1000 INR", "$1.29", "$12.97"):
        assert want in msg2, (want, msg2)

    # ── кривой ответ не роняет разбор ────────────────────────────────────
    assert b.stock_from_product({})["in_stock"] is False
    bad = b.stock_from_product({"outOfStock": False, "packages": [
        {"value": "нет"}, None, {"amount": 250, "usdPrice": "—"}]})
    assert bad["denoms"] == [{"value": 250, "usd": None}], bad["denoms"]
    assert "250 INR" in b.stock_message(bad)

    # ── контракт с монитором в menu.py ───────────────────────────────────
    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    assert "_bitrefill_stock_watch" in src, "нет фоновой проверки наличия"
    assert "_bitrefill_check_once" in src
    # уведомляем только на переходе «не было → появилось», не на каждой проверке
    i = src.find("async def _bitrefill_check_once")
    chunk = src[i: i + 1800]
    assert "appeared" in chunk and "prev.get(\"in_stock\")" in chunk, chunk[:400]
    assert "BITREFILL_STOCK_FILE" in chunk, "состояние должно сохраняться"

    print("PASS bitrefill_stock")


if __name__ == "__main__":
    main()
