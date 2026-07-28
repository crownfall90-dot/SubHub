"""Self-check: активацию, которую нельзя отменить (SMS уже пришла), закрываем
завершением, а не бросаем висеть до истечения."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def _client(responses: dict):
    """Клиент с подменённым _get: {status: ответ}. Пишет вызовы в client.calls."""
    from grizzly_sms import GrizzlySMSClient
    c = GrizzlySMSClient("test_key_not_placeholder")
    c.calls = []

    async def fake_get(params: dict) -> str:
        st = str(params.get("status"))
        c.calls.append(st)
        return responses.get(st, "NO_ACTIVATION")

    c._get = fake_get  # type: ignore[method-assign]
    return c


def main() -> None:
    from grizzly_sms import GrizzlySMSClient, GrizzlySMSError

    # Обычная отмена — как раньше, завершение не дёргаем
    c = _client({"-1": "ACCESS_CANCEL"})
    asyncio.run(c.cancel("1"))
    assert c.calls == ["-1"], c.calls

    # BAD_ACTION → закрываем через setStatus=6, исключения нет
    c = _client({"-1": "BAD_ACTION", "6": "ACCESS_ACTIVATION"})
    asyncio.run(c.cancel("548083197"))
    assert c.calls == ["-1", "6"], c.calls
    assert str(GrizzlySMSClient.STATUS_COMPLETE) == "6"

    # Активации и правда нет: 6 тоже не проходит → штатная ошибка вызывающему,
    # он снимет номер с учёта (см. обработку BAD_ACTION в grizzly.py)
    c = _client({"-1": "BAD_ACTION", "6": "NO_ACTIVATION"})
    try:
        asyncio.run(c.cancel("2"))
        raise AssertionError("ожидалась ошибка, когда и завершение не прошло")
    except GrizzlySMSError as exc:
        assert "BAD_ACTION" in str(exc), exc

    # Ранняя отмена по-прежнему ошибка: монитор повторит после кулдауна
    c = _client({"-1": "EARLY_CANCEL_DENIED"})
    try:
        asyncio.run(c.cancel("3"))
        raise AssertionError("EARLY_CANCEL_DENIED должен оставаться ошибкой")
    except GrizzlySMSError as exc:
        assert "EARLY_CANCEL_DENIED" in str(exc), exc
    assert c.calls == ["-1"], "раннюю отмену нельзя закрывать завершением"

    print("PASS grizzly_bad_action")


if __name__ == "__main__":
    main()
