"""Self-check: GGSell console panel logic — seller/buyer classification,
persisted own-sent messages, unread-message tracking (no live API needed)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    from ggsell import monitor as mon

    inv = 999999001  # тестовый invoice_id, не пересекается с реальными заказами

    # ── classify_is_seller: явные флаги API ──────────────────────────────────
    assert mon.classify_is_seller(inv, {"is_seller": True, "text": "x"}) is True
    assert mon.classify_is_seller(inv, {"sender": "seller", "text": "x"}) is True
    assert mon.classify_is_seller(inv, {"type_message": 1, "text": "x"}) is True
    assert mon.classify_is_seller(inv, {"text": "Просто вопрос от покупателя"}) is False

    # ── classify_is_seller: отпечаток наших шаблонов (без is_own_sent) ───────
    assert mon.classify_is_seller(inv, {"text": mon.MSG_WAIT}) is True
    assert mon.classify_is_seller(inv, {"text": mon.MSG_GREETING}) is True

    # ── is_own_sent: персистентность (сообщение остаётся «нашим» и без TTL 30 мин) ──
    unique_text = f"Тестовое сообщение продавца {time.time()}"
    assert mon.is_own_sent(inv, unique_text) is False  # ещё не отправляли
    mon.record_sent_message(inv, unique_text)
    assert mon.is_own_sent(inv, unique_text) is True
    assert mon.classify_is_seller(inv, {"text": unique_text}) is True
    # запись должна попасть на диск (переживает перезапуск процесса)
    assert mon._SENT_MSGS_FILE.exists()
    raw = mon._SENT_MSGS_FILE.read_text(encoding="utf-8")
    assert mon._norm_msg(unique_text) in raw

    # ── unread-счётчик: сохранение / чтение / сброс ──────────────────────────
    unread_before = mon._load_unread()
    try:
        unread = dict(unread_before)
        unread[str(inv)] = 3
        mon._save_unread(unread)
        counts = mon.get_unread_counts()
        assert counts.get(inv) == 3, counts

        mon.mark_order_read(inv)
        counts_after = mon.get_unread_counts()
        assert counts_after.get(inv, 0) == 0, counts_after
    finally:
        # не оставляем тестовый invoice_id в состоянии на диске
        cleanup = mon._load_unread()
        cleanup.pop(str(inv), None)
        mon._save_unread(cleanup)

    print("OK: ggsell console classification / persistence / unread tracking")


if __name__ == "__main__":
    main()
