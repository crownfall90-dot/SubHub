"""Self-check: все пресеты Обзора → корректный argv (без запуска браузера)."""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# app imports CTk — only need _build_run_cmd logic; load via AST extract + mock.
import app as app_mod  # noqa: E402


class _Fake:
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v

    def configure(self, **_kw):
        pass


MODES = {
    "full": "Полный цикл (вход + покупка)",
    "payment": "До оплаты (существующий профиль)",
    "login_pc": "Только вход на ПК",
    "tg_intercept": "Вход + Telegram (перехват)",
    "email": "Вход с данными (до email)",
}

EXPECT = {
    "full": ("menu.py", ["--full-cycle", "--tariffs"]),
    "payment": ("menu.py", ["--fill-to-payment"]),
    "login_pc": ("main.py", ["--tg-login"]),
    "tg_intercept": ("main.py", ["--tg-intercept"]),
    "email": ("menu.py", ["--full-cycle", "--stop-at-email", "--tariffs"]),
}


def _host() -> SimpleNamespace:
    h = SimpleNamespace()
    h.run_mode = _Fake(MODES["full"])
    h.run_tariff = _Fake("3 месяца (₹343)")
    h.run_accounts = _Fake("авто")
    h.run_headless = _Fake(0)
    h._build_run_cmd = app_mod.SubHubApp._build_run_cmd.__get__(h, app_mod.SubHubApp)
    return h


def _assert_cmd(h, key: str, *, headless=False, accounts="", months12=False) -> list[str]:
    h.run_mode.set(MODES[key])
    h.run_tariff.set("12 месяцев (₹1,499)" if months12 else "3 месяца (₹343)")
    h.run_accounts.set(accounts)
    h.run_headless = _Fake(1 if headless else 0)
    cmd = h._build_run_cmd()
    script, flags = EXPECT[key]
    assert any(Path(p).name == script for p in cmd), (key, cmd)
    for f in flags:
        assert f in cmd, (key, f, cmd)
    if "--tariffs" in flags:
        assert cmd[cmd.index("--tariffs") + 1] == ("12" if months12 else "3"), cmd
    if accounts.isdigit() and int(accounts) > 0:
        assert "--accounts" in cmd and accounts in cmd, cmd
    else:
        assert "--accounts" not in cmd, cmd
    if headless:
        assert "--headless" in cmd, cmd
    else:
        assert "--headless" not in cmd, cmd
    return cmd


def _cli_flags_exist() -> None:
    menu = (ROOT / "menu.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    for flag in ("--full-cycle", "--fill-to-payment", "--stop-at-email", "--tariffs", "--accounts", "--headless"):
        assert flag in menu, flag
    for flag in ("--tg-login", "--tg-intercept", "--headless", "--accounts"):
        assert flag in main, flag


def _ui_strings() -> None:
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'text="Фоновый режим"' in src
    assert "Headless" not in src.split("def _build_youtube_hub")[1].split("def _build_ggsell")[0]
    # parse checkbox label survives syntax
    tree = ast.parse(src)
    assert tree is not None


def main() -> int:
    _cli_flags_exist()
    _ui_strings()
    h = _host()
    results = []
    for key in MODES:
        results.append((key, _assert_cmd(h, key)))
        results.append((f"{key}+hl", _assert_cmd(h, key, headless=True)))
        results.append((f"{key}+n2", _assert_cmd(h, key, accounts="2")))
    results.append(("full+12", _assert_cmd(h, "full", months12=True)))
    results.append(("email+12+hl", _assert_cmd(h, "email", months12=True, headless=True, accounts="1")))

    # preset ↔ mode sync
    for key, label in MODES.items():
        app_mod.SubHubApp._preset_run(h, key)
        assert h.run_mode.get() == label, (key, h.run_mode.get())

    print("OK run variants:")
    for name, cmd in results:
        print(f"  {name:16} {' '.join(Path(c).name if c.endswith('.py') else c for c in cmd[1:])}")
    print(f"SVC_YOUTUBE={app_mod.SVC_YOUTUBE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
