"""Self-check: направление сообщений GGSell (наше / покупателя).

Фикстуры — реальные payload'ы из /debates/v2 (заказы 35060202 и 29155679,
снято 2026-07-28). Раньше ручные ответы продавца с сайта GGSell подписывались
«Новое сообщение от покупателя».
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

# (message, buyer, seller, date_seen, ожидаем_наше)
REAL = [
    # покупатель: buyer=1, seller=1, date_seen заполнен
    ("Здравствуйте.",                              1, 1, "2026-07-28T15:03:07+03:00", False),
    ("Премиум который я купил у вас закончился",    1, 1, "2026-07-28T15:03:07+03:00", False),
    ("Да, надеюсь в дороге будет связь)",           1, 1, "2026-07-28T15:03:07+03:00", False),
    ("Фигово",                                     1, 1, "2026-07-28T15:03:07+03:00", False),
    ("Отлично, что от меня нужно?",                 1, 1, "2026-07-28T15:03:07+03:00", False),
    # наши ручные ответы: buyer=0, seller=0, date_seen пустой
    ("Да, у меня тоже отвалилась и у половины покупателей.", 0, 0, None, True),
    ("Поэтому сорян, тут я никак не повлияю",       0, 0, None, True),
    ("Сегодня вечером могу выдать",                 0, 0, None, True),
    ("могу выдать замену",                          0, 0, None, True),
    ("Тоже в дороге) состыкуемся, я напишу",        0, 0, None, True),
    # наши шаблонные автоответы — тоже 0/0
    ("Ссылка на активацию подписки отправлена ✅",   0, 0, None, True),
]


def main() -> None:
    from ggsell.monitor import classify_is_seller, direction_from_flags

    for text, b, s, seen, want_ours in REAL:
        msg = {"message": text, "buyer": b, "seller": s, "date_seen": seen}
        got = classify_is_seller(9999999, msg)
        assert got is want_ours, (
            f"{'наше' if want_ours else 'покупателя'} → определилось как "
            f"{'наше' if got else 'покупателя'}: {text[:40]!r}")

    # Смешанные/непонятные комбинации: не гадаем, остаётся прежнее поведение
    # («покупатель») — глушить живой вопрос клиента нельзя.
    for b, s, seen in ((1, 0, None), (0, 1, None), (0, 0, "2026-07-28T15:00:00+03:00")):
        msg = {"message": "непонятное", "buyer": b, "seller": s, "date_seen": seen}
        assert direction_from_flags(msg) is None, (b, s, seen)
        assert classify_is_seller(9999999, msg) is False, (b, s, seen)

    # Флагов нет вовсе (старый формат / вебхук) — прежние эвристики
    assert direction_from_flags({"message": "x"}) is None
    assert classify_is_seller(9999999, {"message": "x"}) is False
    assert classify_is_seller(9999999, {"message": "x", "is_seller": True}) is True
    # Локальная запись «мы это отправляли» сильнее флагов покупателя
    assert classify_is_seller(
        9999999, {"message": "x", "is_current_user": True,
                  "buyer": 1, "seller": 1, "date_seen": "now"}) is True

    print("PASS ggsell_msg_direction")


if __name__ == "__main__":
    main()
