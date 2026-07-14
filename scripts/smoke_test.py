"""Smoke + интеграционные проверки GUI/backend."""
from __future__ import annotations

import contextlib
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
    if "pickCountryId" not in js or "wantIso" not in js:
        fail("vpn_country_js", "нет pickCountryId / USA")
    if not hasattr(m, "_VPN_FLIPKART_COUNTRY_ORDER"):
        fail("vpn_helpers", "нет _VPN_FLIPKART_COUNTRY_ORDER")
    if m._VPN_FLIPKART_COUNTRY_ORDER[0] != "us":
        fail("vpn_helpers", "USA не первый в порядке стран")
    if not hasattr(m, "_vpn_connect_for_profile"):
        fail("vpn_helpers", "нет _vpn_connect_for_profile")
    if not hasattr(m, "_navigate_flipkart_resilient"):
        fail("vpn_helpers", "нет _navigate_flipkart_resilient")
    if not hasattr(m, "disconnect_vpn_on_shutdown"):
        fail("vpn_helpers", "нет disconnect_vpn_on_shutdown")
    if not hasattr(m, "_vpn_toggle_reconnect_flipkart"):
        fail("vpn_helpers", "нет _vpn_toggle_reconnect_flipkart")
    # sticky cancel после shutdown не должен ломать следующий fill/buy
    m._purchase_cancel.set()
    m.disconnect_vpn_on_shutdown()
    if m._purchase_cancel.is_set():
        fail("vpn_helpers", "_purchase_cancel залипает после disconnect_vpn_on_shutdown")
    # VeepN только для успешных done (+ .profile_meta.json); вход/tmp — нет
    from pathlib import Path as _P
    sample = _P("chrome_profiles_done") / "profile_smokevpn"
    sample.mkdir(parents=True, exist_ok=True)
    meta = sample / ".profile_meta.json"
    try:
        if m._profile_allows_vpn(sample):
            fail("vpn_helpers", "VPN allow без meta (ожидали False)")
        meta.write_text('{"phone":"0000000000"}', encoding="utf-8")
        if not m._profile_allows_vpn(sample):
            fail("vpn_helpers", "VPN allow: успешный done+meta должен быть True")
        tmp = _P("chrome_profiles_done") / "profile_0000000000_tmp_1"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            (tmp / ".profile_meta.json").write_text("{}", encoding="utf-8")
            if m._profile_allows_vpn(tmp):
                fail("vpn_helpers", "VPN allow для _tmp_ (ожидали False)")
        finally:
            with contextlib.suppress(Exception):
                (tmp / ".profile_meta.json").unlink(missing_ok=True)
                tmp.rmdir()
    finally:
        with contextlib.suppress(Exception):
            meta.unlink(missing_ok=True)
            sample.rmdir()
    ok("vpn_helpers", "USA + resilient navigate + VPN only for done+meta")


def test_purge_temp_profiles() -> None:
    """Классификация + быстрый purge в изолированных dirs (без реального chrome_profiles)."""
    import tempfile
    import time
    import menu as m

    if not hasattr(m, "purge_temp_profiles"):
        fail("purge_temp", "нет purge_temp_profiles")

    with tempfile.TemporaryDirectory(prefix="purge_smoke_") as td:
        root = Path(td)
        work = root / "chrome_profiles"
        done = root / "chrome_profiles_done"
        work.mkdir()
        done.mkdir()
        keep = done / "profile_smoke_keep"
        tmp = done / "profile_smoke_tmp_1"
        bare = done / "profile_smoke_bare"
        work_p = work / "profile_smoke_work"
        bulk = [work / f"profile_smoke_bulk_{i}" for i in range(24)]
        for d in (keep, tmp, bare, work_p, *bulk):
            d.mkdir()
        (keep / ".profile_meta.json").write_text('{"phone":"1"}', encoding="utf-8")

        old_w, old_d = m.PROFILES_DIR, m.DONE_PROFILES_DIR
        kill_n = {"n": 0}
        real_kill = m._kill_chrome_for_profiles

        def _count_kill(paths):
            kill_n["n"] += 1
            return 0

        m.PROFILES_DIR = work
        m.DONE_PROFILES_DIR = done
        m._kill_chrome_for_profiles = _count_kill
        try:
            if not m._is_temp_profile_dir(tmp) or not m._is_temp_profile_dir(bare):
                fail("purge_temp", "tmp/bare должны быть temp")
            if not m._is_temp_profile_dir(work_p):
                fail("purge_temp", "chrome_profiles/ без meta = temp")
            if m._is_temp_profile_dir(keep):
                fail("purge_temp", "done+meta нельзя считать temp")
            t0 = time.perf_counter()
            r = m.purge_temp_profiles()
            dt = time.perf_counter() - t0
            if not keep.exists():
                fail("purge_temp", "удалили done+meta")
            if tmp.exists() or bare.exists() or work_p.exists():
                fail("purge_temp", "temp не удалились")
            if any(d.exists() for d in bulk):
                fail("purge_temp", "bulk temp не удалились")
            if int(r.get("removed") or 0) < 26:
                fail("purge_temp", f"removed={r.get('removed')}")
            # пустые папки — без kill; иначе регрессия «kill на каждый профиль»
            if kill_n["n"] != 0:
                fail("purge_temp", f"лишний kill x{kill_n['n']}")
            if dt > 3.0:
                fail("purge_temp", f"слишком медленно {dt:.2f}s")
        finally:
            m.PROFILES_DIR = old_w
            m.DONE_PROFILES_DIR = old_d
            m._kill_chrome_for_profiles = real_kill
    ok("purge_temp", "classify + fast purge, keep meta")


def test_launch_helpers() -> None:
    """SubHub.exe / быстрый PID-скан / boot-обёртка."""
    import time
    import app as a

    boot = ROOT / "scripts" / "_gui_boot.py"
    if not boot.exists():
        fail("launch_helpers", "нет scripts/_gui_boot.py")
    exe = ROOT / "SubHub.exe"
    if not exe.exists():
        fail("launch_helpers", "нет SubHub.exe — scripts/build_subhub_exe.bat")
    launcher = a._launcher_path()
    if launcher.resolve() != exe.resolve():
        fail("launch_helpers", f"launcher={launcher}, expected SubHub.exe")
    t0 = time.perf_counter()
    a._collect_subhub_gui_pids()
    dt = time.perf_counter() - t0
    if dt > 1.5:
        fail("launch_helpers", f"PID scan {dt:.2f}s — слишком медленно")
    if not a._cmdline_is_subhub_gui(
        f'pythonw.exe "{ROOT / "scripts" / "_gui_boot.py"}"'
    ):
        fail("launch_helpers", "_gui_boot.py не распознаётся как GUI")
    import menu as m
    git = (m._GIT or "").lower().replace("/", "\\")
    if git.endswith("git.cmd"):
        fail("launch_helpers", f"git.cmd вызывает вспышки cmd: {m._GIT}")
    win = ROOT / "winproc.py"
    if not win.exists():
        fail("launch_helpers", "нет winproc.py")
    ok("launch_helpers", f"exe + boot + silent git + PID scan {dt:.2f}s")



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


def test_gift_payment_helpers() -> None:
    """Gift knapsack + pay_method wiring (scripts/test_gift_payment_helpers.py)."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_gift_payment_helpers.py")],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail("gift_payment_helpers", (proc.stdout or "") + (proc.stderr or ""))
    ok("gift_payment_helpers", "select + balance + buy resilient")


def test_selector_health() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_selector_health.py")],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail("selector_health", (proc.stdout or "") + (proc.stderr or ""))
    ok("selector_health")


def test_backup_zip() -> None:
    out = ROOT / "data" / "backups" / "_smoke_backup.zip"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "backup_data.py"), "-o", str(out)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail("backup_zip", (proc.stdout or "") + (proc.stderr or ""))
    if not out.is_file() or out.stat().st_size < 20:
        fail("backup_zip", "zip missing/empty")
    out.unlink(missing_ok=True)
    ok("backup_zip")


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
    test_purge_temp_profiles()
    test_launch_helpers()
    test_fill_to_payment_cli()
    test_gift_payment_helpers()
    test_selector_health()
    test_backup_zip()
    test_open_chrome_flipkart()
    test_full_cycle_boot()
    print(f"\n=== ALL OK ({MARK}) ===\n")


if __name__ == "__main__":
    main()
