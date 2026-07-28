"""Self-check: Currently out of stock = брак аккаунта → профиль удаляется сам,
покупка идёт дальше на следующем профиле (без вопросов оператору)."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu as m

    # Реально удаляет папку и не падает на повторном вызове
    prof = Path(tempfile.mkdtemp(prefix="oos_")) / "profile_0001_9998887766"
    (prof / "Default").mkdir(parents=True)
    (prof / "Default" / "Cookies").write_bytes(b"x")
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert m._delete_oos_profile(prof, "9998887766") is True
    assert not prof.exists(), "профиль не удалён"
    assert "out of stock" in out.getvalue().lower()
    with contextlib.redirect_stdout(io.StringIO()):
        assert m._delete_oos_profile(prof, "9998887766") is True  # идемпотентно

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")

    # Пути покупки больше не спрашивают подтверждение на OOS
    for fn in ("def screen_auto_buy", "def screen_buy_membership"):
        i = src.find(fn)
        assert i > 0, fn
        chunk = src[i: i + 6000]
        assert "_delete_oos_profile" in chunk, f"{fn}: нет авто-удаления OOS"
        assert "Удалить профиль" not in chunk, f"{fn}: остался вопрос об удалении"

    # Фолбэк «Всё в одном» перебирает годные профили и чистит брак на ходу
    i = src.find("def screen_all_in_one")
    chunk = src[i: i + 9000]
    assert "_buy_candidates()" in chunk, "фолбэк всё ещё берёт все профили подряд"
    assert "_delete_oos_profile" in chunk, "фолбэк не удаляет OOS-брак"
    assert 'glob("profile_*")' not in chunk

    # OOS не должен расходовать лимит попыток в автопокупке
    i = src.find("def screen_auto_buy")
    chunk = src[i: i + 6000]
    assert "_tries >= _AUTO_BUY_MAX_TRIES" in chunk
    assert chunk.find("_oos += 1") < chunk.find("_tries += 1"), \
        "OOS должен обрабатываться до счётчика попыток"

    # Удаление обязано сначала убить Chrome, иначе папка остаётся жить
    i = src.find("def _delete_oos_profile")
    chunk = src[i: i + 1800]
    assert chunk.find("_kill_chrome_for_profile(") < chunk.find("_sh_oos.rmtree("), \
        "Chrome надо закрыть до rmtree"
    assert "if _p.exists():" in chunk, "нет проверки, что папка реально удалена"

    # Все OOS-выходы _do_buy_membership должны закрывать браузер (_keep_open=False)
    i = src.find("async def _do_buy_membership")
    body = src[i: src.find("\ndef screen_buy_membership")]
    for pos in [m for m in range(len(body)) if body.startswith('return False, "OUT_OF_STOCK', m)]:
        before = body[max(0, pos - 400): pos]
        keep_true = before.rfind("_keep_open = True")
        keep_false = before.rfind("_keep_open = False")
        assert keep_true <= keep_false, \
            f"OOS-выход оставляет браузер открытым: ...{before[-120:]!r}"

    print("PASS oos_auto_delete")


if __name__ == "__main__":
    main()
