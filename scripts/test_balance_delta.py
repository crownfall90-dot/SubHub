"""Self-check: итог по балансу не показывает «Потрачено: -$2».

Возвраты за отменённые номера приходят асинхронно, поэтому баланс после
прогона бывает больше, чем до. Два снимка баланса измеряют изменение, а не
траты — подпись должна соответствовать знаку.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu as m

    # Обычный прогон: потратили больше, чем вернули
    label, value, _ = m._balance_delta_line(0.4705)
    assert "Потрачено" in label, label
    assert abs(value - 0.4705) < 1e-9, value

    # Ровно тот случай из живого лога: баланс вырос на 2.0 за счёт возвратов
    label, value, _ = m._balance_delta_line(-2.0)
    assert "Потрачено" not in label, f"минус не должен подписываться как траты: {label}"
    assert "Возврат" in label, label
    assert value == 2.0, "показываем модуль, а не отрицательное число"

    # Ноль — это траты, а не возврат
    label, value, _ = m._balance_delta_line(0.0)
    assert "Потрачено" in label and value == 0.0

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    # Ни консоль, ни Telegram не должны печатать спорную цифру напрямую
    assert 'Потрачено          : {R}${spent' not in src
    assert 'Потрачено: <b>${spent' not in src
    assert src.count("_balance_delta_line(") >= 3, "helper должен использоваться в обоих отчётах"

    print("PASS balance_delta")


if __name__ == "__main__":
    main()
