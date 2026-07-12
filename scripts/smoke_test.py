"""Smoke + интеграционные проверки GUI/backend."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOG = ROOT / "automation.log"
MARK = f"SMOKE_{int(time.time())}"


def ok(name: str, detail: str = "") -> None:
    print(f"  [OK] {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str = "") -> None:
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
    sys.exit(1)


def _log_since_marker(marker: str) -> str:
    if not LOG.exists():
        return ""
    text = LOG.read_text(encoding="utf-8", errors="replace")
    idx = text.rfind(marker)
    return text[idx:] if idx >= 0 else text[-4000:]


def test_subprocess_log_stream() -> None:
    """Как _start_run в app.py: stdout → LogSink/tee."""
    import menu as m

    marker = f"STREAMTEST_{int(time.time())}"
    m._start_log_tee()
    print(marker)
    cmd = [
        sys.executable, "-c",
        "import time\nfor i in range(8):\n print(f'STREAM_LINE_{i}', flush=True); time.sleep(0.15)\n",
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.stdout
    for line in proc.stdout:
        sys.stdout.write(line)
    code = proc.wait()
    if code != 0:
        fail("subprocess_stream", f"code {code}")
    chunk = _log_since_marker(marker)
    if "STREAM_LINE_0" not in chunk or "STREAM_LINE_7" not in chunk:
        fail("subprocess_stream", "строки не попали в automation.log")
    ok("subprocess_stream", "8 строк в логе")


def test_vpn_helpers() -> None:
    import menu as m

    js = m._veepn_connect_js(loops=2, sleep_ms=1000)
    if "pickIndiaId" not in js or "india" not in js.lower():
        fail("vpn_india_js", "нет pickIndiaId")
    if not hasattr(m, "_vpn_connect_for_profile"):
        fail("vpn_helpers", "нет _vpn_connect_for_profile")
    if not hasattr(m, "_navigate_flipkart_resilient"):
        fail("vpn_helpers", "нет _navigate_flipkart_resilient")
    ok("vpn_helpers", "India + resilient navigate")


def test_fill_to_payment_cli() -> None:
    """CLI --fill-to-payment и run_to_payment.py на месте."""
    run_script = ROOT / "scripts" / "run_to_payment.py"
    if not run_script.exists():
        fail("fill_to_payment_cli", "нет scripts/run_to_payment.py")
    menu_src = (ROOT / "menu.py").read_text(encoding="utf-8", errors="replace")
    if "--fill-to-payment" not in menu_src:
        fail("fill_to_payment_cli", "нет --fill-to-payment в menu.py")
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ROOT / "menu.py")],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail("fill_to_payment_cli", proc.stderr or "py_compile failed")
    ok("fill_to_payment_cli", "CLI + run_to_payment.py")


def test_open_chrome_flipkart() -> None:
    import menu as m

    marker = f"CHROMETEST_{int(time.time())}"
    m._start_log_tee()
    print(marker)
    profiles = m._load_done_profiles(force=True)
    if not profiles:
        ok("open_chrome_flipkart", "skip — нет профилей")
        return
    path = profiles[0]["path"]
    if not m.open_chrome(path):
        fail("open_chrome", "не стартовал")

    deadline = time.time() + 150
    success = vpn_ok = fail_msg = False
    while time.time() < deadline:
        time.sleep(2)
        chunk = _log_since_marker(marker)
        if "Flipkart открыт" in chunk:
            success = True
        if "flipkart.com/flipkart-black" in chunk.lower() or "flipkart-black-store" in chunk.lower():
            success = True
        if "VeepN подключён" in chunk or "VeepN уже подключён" in chunk:
            vpn_ok = True
        if "Flipkart не открылся" in chunk:
            fail_msg = True
        if success and (vpn_ok or "VPN не подключился" in chunk or "VPN: таймаут" in chunk or "VPN: не" in chunk):
            break
        if fail_msg:
            break

    chunk = _log_since_marker(marker)
    print("--- open_chrome log ---")
    print(chunk[-2500:])

    if success and vpn_ok:
        ok("open_chrome_flipkart", "✔ VPN + Flipkart")
    elif success:
        ok("open_chrome_flipkart", "✔ Flipkart (VPN вручную)")
    elif fail_msg:
        fail("open_chrome_flipkart", "навигация не удалась")
    else:
        fail("open_chrome_flipkart", "таймаут 150s без результата")


def test_full_cycle_boot() -> None:
    """Старт menu.py --full-cycle: первые строки в логе без ожидания покупки."""
    pos = LOG.stat().st_size if LOG.exists() else 0
    cmd = [
        sys.executable, str(ROOT / "menu.py"),
        "--full-cycle", "--tariffs", "3", "--accounts", "1", "--headless",
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    lines: list[str] = []

    def _reader():
        assert proc.stdout
        for line in proc.stdout:
            lines.append(line)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    time.sleep(12)
    proc.kill()
    t.join(timeout=3)
    text = "".join(lines)
    print("--- full-cycle boot ---")
    print(text[-1500:] if text else "(empty)")
    if not text.strip():
        fail("full_cycle_boot", "нет вывода за 12s")
    ok("full_cycle_boot", f"{len(lines)} строк stdout")


def main() -> None:
    print(f"\n=== Integration test {MARK} ===\n")
    import menu as m  # noqa: F401
    import app  # noqa: F401
    ok("imports")

    test_subprocess_log_stream()
    test_vpn_helpers()
    test_fill_to_payment_cli()
    test_open_chrome_flipkart()
    test_full_cycle_boot()
    print(f"\n=== ALL OK ({MARK}) ===\n")


if __name__ == "__main__":
    main()
