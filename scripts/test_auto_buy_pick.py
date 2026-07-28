"""Self-check: автоподбор профиля для кнопки «Купить» (_buy_candidates)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu as m

    pool = [
        {"username": "no_meta",   "login_ts": None},                      # нет данных
        {"username": "issued",    "login_ts": 1, "issued_ts": 2},         # выдан
        {"username": "paid",      "login_ts": 1, "black_valid_till": "x"},
        {"username": "paid2",     "login_ts": 1, "status": "activated"},
        {"username": "paid3",     "login_ts": 1, "paid_ready": True},
        {"username": "with_data", "login_ts": 1, "prepared_ts": 5},
        {"username": "with_mail", "login_ts": 1, "buyer_email": "a@b.c"},
        {"username": "plain",     "login_ts": 1},
    ]
    with_data, plain = m._buy_candidates(pool)
    assert [p["username"] for p in with_data] == ["with_data", "with_mail"], with_data
    assert [p["username"] for p in plain] == ["plain"], plain
    # «С данными» идут первыми — их и берёт screen_auto_buy
    assert (with_data + plain)[0]["username"] == "with_data"

    # Нет профилей с данными → берём любой доступный
    only_plain = [p for p in pool if p["username"] in ("plain", "paid", "issued")]
    wd2, pl2 = m._buy_candidates(only_plain)
    assert not wd2 and [p["username"] for p in pl2] == ["plain"]

    # Совсем нечего покупать → пусто (экран должен показать ошибку)
    nothing = [p for p in pool if p["username"] in ("no_meta", "issued", "paid")]
    assert m._buy_candidates(nothing) == ([], [])

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    assert "def screen_auto_buy" in src
    assert 'elif choice == "Б":' in src, "кнопка «Б» не подключена в screen_main"
    assert "Нет подходящих профилей для покупки" in src
    assert "_do_buy_membership(p[\"path\"], months, card=None)" in src

    print("PASS auto_buy_pick")


if __name__ == "__main__":
    main()
