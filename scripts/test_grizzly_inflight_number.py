"""Self-check: номер, купленный «в полёте» при истечении ценового тира,
сдаётся провайдеру, а не теряется вместе со списанными деньгами."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


async def scenario(slow_seconds: float) -> tuple[object, list]:
    """Слот 0 отдаёт NO_NUMBERS, слот 1 «покупает» номер через slow_seconds.
    Тир истекает раньше. Возвращает (результат, список отменённых id)."""
    from grizzly_sms import GrizzlySMSClient

    client = GrizzlySMSClient("test_key_not_a_placeholder")
    cancelled: list = []
    calls = {"n": 0}

    async def fake_get(params: dict) -> str:
        action = params.get("action")
        if action == "setStatus":
            if str(params.get("status")) == "-1":
                cancelled.append(str(params.get("id")))
            return "ACCESS_CANCEL"
        if action == "getNumberV2":
            calls["n"] += 1
            if calls["n"] == 2:          # второй слот — «медленная» покупка
                await asyncio.sleep(slow_seconds)
                return json.dumps({"activationId": "999", "phoneNumber": "+918349110530",
                                   "activationCost": 0.0941})
            await asyncio.sleep(0.01)
            return "NO_NUMBERS"
        return "NO_NUMBERS"

    client._get = fake_get  # type: ignore[method-assign]
    # Прогресс печатается рамками (║) — гасим stdout, чтобы тест не зависел
    # от кодовой страницы консоли.
    with contextlib.redirect_stdout(io.StringIO()):
        res = await client._parallel_acquire("xt", 22, 0.10, 2, 0.05, 0.3)
        await client.close()    # close() дожидается фоновых отмен
    return res, cancelled


def main() -> None:
    # 1. Номер приходит после истечения тира → тир проигран, но номер СДАН
    res, cancelled = asyncio.run(scenario(1.0))
    assert res is None, f"тир истёк — победителя быть не должно, а вернулось {res}"
    assert cancelled == ["999"], f"опоздавший номер не сдан провайдеру: {cancelled}"

    # 2. Номер успевает до истечения тира → он победитель, отмены нет
    res2, cancelled2 = asyncio.run(scenario(0.02))
    assert res2 is not None and res2[0] == "999", res2
    assert res2[2] == 0.0941, res2
    assert cancelled2 == [], f"победителя отменять нельзя: {cancelled2}"

    src = (ROOT / "subhub" / "grizzly_sms.py").read_text(encoding="utf-8", errors="replace")
    assert "_INFLIGHT_DRAIN" in src and "_fire_cancel" in src
    assert "_found_evt" not in src, "мёртвый _found_evt остался"
    # Фоновые отмены должны держаться за ссылку, иначе их соберёт GC
    assert "self._bg_tasks.add(t)" in src

    print("PASS grizzly_inflight_number")


if __name__ == "__main__":
    main()
