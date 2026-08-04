"""Self-check: уведомление о появлении гифт-карт срабатывает ровно когда надо.

Без сети: `check_stock` подменяется. Проверяется то, из-за чего такие
напоминания обычно бесят или молчат — повторные сообщения на каждой проверке
и пропущенный переход «не было → появилось».
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

OUT = {"in_stock": False, "denoms": [{"value": 1000, "usd": 12.97}],
       "currency": "INR", "name": "Flipkart India"}
IN_100 = {"in_stock": True, "denoms": [{"value": 100, "usd": 1.29}],
          "currency": "INR", "name": "Flipkart India"}
IN_100_500 = {"in_stock": True,
              "denoms": [{"value": 100, "usd": 1.29}, {"value": 500, "usd": 6.48}],
              "currency": "INR", "name": "Flipkart India"}


def main() -> None:
    import bitrefill as b
    import menu as m

    sent: list = []
    tmp = Path(tempfile.mkdtemp(prefix="brnotify_")) / "stock.json"

    orig_file, orig_notify, orig_check = (
        m.BITREFILL_STOCK_FILE, m._bitrefill_notify, b.check_stock)
    m.BITREFILL_STOCK_FILE = tmp
    m._bitrefill_notify = lambda text: sent.append(text)

    def feed(state):
        async def _fake(*_a, **_k):
            return state, ""
        b.check_stock = _fake

    try:
        # 1. Нет в наличии — молчим
        feed(OUT)
        asyncio.run(m._bitrefill_check_once())
        assert sent == [], "уведомление о том, что товара нет, не нужно"

        # 2. Появился — сообщаем, с номиналом
        feed(IN_100)
        asyncio.run(m._bitrefill_check_once())
        assert len(sent) == 1, sent
        assert "В НАЛИЧИИ" in sent[0] and "100 INR" in sent[0], sent[0]
        assert "$1.29" in sent[0], sent[0]

        # 3. Всё ещё в наличии — второй раз не долбим
        asyncio.run(m._bitrefill_check_once())
        assert len(sent) == 1, f"повторное уведомление на той же позиции: {sent}"

        # 4. Появился новый номинал — сообщаем ещё раз, уже про оба
        feed(IN_100_500)
        asyncio.run(m._bitrefill_check_once())
        assert len(sent) == 2, sent
        assert "500 INR" in sent[1] and "100 INR" in sent[1], sent[1]

        # 5. Пропал — молчим, но состояние обновилось
        feed(OUT)
        asyncio.run(m._bitrefill_check_once())
        assert len(sent) == 2, "об исчезновении товара сообщать не просили"
        saved = json.loads(tmp.read_text(encoding="utf-8"))
        assert saved["in_stock"] is False, saved

        # 6. Появился снова — снова сообщаем
        feed(IN_100)
        asyncio.run(m._bitrefill_check_once())
        assert len(sent) == 3, sent

        # 7. notify=False (кнопка «проверить сейчас») ничего не шлёт
        feed(IN_100_500)
        asyncio.run(m._bitrefill_check_once(False))
        assert len(sent) == 3, "ручная проверка не должна слать уведомление"
    finally:
        m.BITREFILL_STOCK_FILE, m._bitrefill_notify, b.check_stock = (
            orig_file, orig_notify, orig_check)

    # фоновая нить и её вызов на старте консоли
    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    assert "_bitrefill_stock_watch()" in src, "монитор не запускается при старте"
    i = src.find("def _bitrefill_stock_watch")
    assert "_BITREFILL_CHECK_EVERY" in src[i: i + 900], "нет интервала опроса"

    print("PASS bitrefill_notify")


if __name__ == "__main__":
    main()
