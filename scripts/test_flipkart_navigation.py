"""Характеризующий тест: контракт захода на Flipkart в рабочем (direct) режиме.

Написан ДО вычистки VPN-веток из _navigate_flipkart_resilient и фиксирует то,
что работает сейчас: сколько попыток делается и что возвращается. Если упрощение
функции изменит поведение живого пути — этот тест упадёт.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


class FakePage:
    url = "https://www.flipkart.com/"

    async def bring_to_front(self): ...
    async def goto(self, *a, **k): ...


class FakeContext:
    pages: list = []


def run_case(diagnoses: list) -> tuple:
    """Прогоняет навигацию, отдавая диагнозы по списку. Возвращает (результат,
    сколько раз реально ходили на страницу)."""
    import menu as m

    page = FakePage()
    calls = {"nav": 0}
    seq = list(diagnoses)

    async def fake_main_work_page(_ctx):
        return page

    async def fake_force_navigate(_page, _url, *, label="", fast=False):
        calls["nav"] += 1
        return True, ""

    async def fake_diagnose(_ctx, _page):
        return seq.pop(0) if seq else {"ok": False, "kind": "unknown",
                                       "hint": "нет ответа", "proxy": None, "country": ""}

    orig = (m._main_work_page, m._force_navigate_flipkart, m._diagnose_flipkart_state)
    m._main_work_page = fake_main_work_page
    m._force_navigate_flipkart = fake_force_navigate
    m._diagnose_flipkart_state = fake_diagnose
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = asyncio.run(m._navigate_flipkart_resilient(
                FakeContext(), page, "https://www.flipkart.com/", label="test"))
    finally:
        (m._main_work_page, m._force_navigate_flipkart, m._diagnose_flipkart_state) = orig
    return res, calls["nav"]


OK = {"ok": True, "kind": "ok", "hint": "", "proxy": None, "country": ""}
BAD = {"ok": False, "kind": "access_denied", "hint": "Access Denied",
       "proxy": None, "country": ""}


def main() -> None:
    import menu as m

    # Живой режим — direct: VPN-тумблер выключен, расширения нет
    assert m._context_skip_vpn(FakeContext()) is True, \
        "живой путь должен быть direct — иначе тест фиксирует не то поведение"
    assert m._vpn_extension_dir() is None

    # 1. Открылось с первой попытки — ходим ровно один раз
    (ok, page, err), navs = run_case([OK])
    assert ok is True and err == "", (ok, err)
    assert navs == 1, navs

    # 2. Со второй/третьей попытки — успех, лишних заходов нет
    (ok, _, err), navs = run_case([BAD, OK])
    assert ok is True and err == "" and navs == 2, (ok, err, navs)
    (ok, _, err), navs = run_case([BAD, BAD, OK])
    assert ok is True and err == "" and navs == 3, (ok, err, navs)

    # 3. Не открылось совсем — ровно 3 попытки и осмысленная ошибка
    (ok, _, err), navs = run_case([BAD, BAD, BAD, BAD, BAD])
    assert ok is False, ok
    assert navs == 3, f"попыток должно быть 3, а не {navs}"
    assert "Access Denied" in err or "Flipkart" in err, err

    print("PASS flipkart_navigation")


if __name__ == "__main__":
    main()
