"""Self-check: бот не держит своих копий общих помощников.

Копии расходятся с оригиналом: у бота была НЕатомарная запись порядка карт,
из-за чего при обрыве процесса файл мог остаться обрезанным.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu  # noqa: F401  (bot._m() резолвит имена через модуль menu)
    import bot

    # escape_html — одна на модуль, а не по копии на каждый обработчик
    src = (ROOT / "subhub" / "bot.py").read_text(encoding="utf-8", errors="replace")
    assert src.count("def escape_html") == 1, "копии escape_html вернулись"
    assert bot.escape_html("<b> & </b>") == "&lt;b&gt; &amp; &lt;/b&gt;"
    assert bot.escape_html(123) == "123", "должен принимать не только строки"

    # Порядок карт и формат номера берутся из menu.py
    assert bot._m("_load_card_order") is menu._load_card_order
    assert bot._m("_save_card_order") is menu._save_card_order
    assert bot._m("_disp_phone") is menu._disp_phone
    assert isinstance(menu._load_card_order(), list)

    # Запись порядка карт обязана быть атомарной (иначе обрыв рвёт файл)
    msrc = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    i = msrc.index("def _save_card_order")
    assert "_atomic_write_text" in msrc[i: i + 400], "порядок карт пишется не атомарно"
    assert not re.search(r"_CARD_ORDER_FILE\.write_text", src), \
        "в боте вернулась своя неатомарная запись порядка карт"

    print("PASS bot_shared_helpers")


if __name__ == "__main__":
    main()
