"""Self-check: пришедший OTP не выбрасывается — входим и сохраняем профиль
по любому активному номеру, кроме режима перехвата."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import grizzly as g

    # Обычный номер нашей автоматизации
    assert g._should_login_with_otp({"phone_10": "9000000001"}) is True
    # «Лишний» номер параллельной волны, подобранный api-сканом: раньше код
    # выбрасывался вместе с номером — теперь входим и сохраняем аккаунт
    assert g._should_login_with_otp({"phone_10": "9000000002", "external": True}) is True
    # Номер, уже помеченный на отмену
    assert g._should_login_with_otp({"phone_10": "9000000003", "status": "failed"}) is True
    # Перехват — код намеренно уходит человеку в Telegram, сами не входим
    assert g._should_login_with_otp({"phone_10": "9000000004",
                                     "intercept_mode": True}) is False
    assert g._should_login_with_otp({"phone_10": "9000000005", "intercept_mode": True,
                                     "external": True}) is False

    src = (ROOT / "subhub" / "grizzly.py").read_text(encoding="utf-8", errors="replace")
    # Оба места (монитор и «последний момент перед отменой») ходят через правило
    assert src.count("_should_login_with_otp(r)") == 2, \
        "правило должно применяться и в мониторе, и перед отменой"
    # Старое условие, из-за которого external-номера теряли готовый код
    assert 'r.get("intercept_mode") or r.get("external")' not in src, \
        "external снова глушит вход по пришедшему коду"
    # Номер с полученным кодом не должен попадать под отмену по возрасту
    i = src.find("# 2. Истекают номера без OTP")
    assert 'r.get("otp_received")' in src[i: i + 400], \
        "номер с кодом может попасть под отмену по таймауту"

    print("PASS otp_always_login")


if __name__ == "__main__":
    main()
