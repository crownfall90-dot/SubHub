"""Прогон сценария: VPN → Flipkart → Buy Now → адрес → страница оплаты."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    phone = ""
    args = sys.argv[1:]
    if "--phone" in args:
        i = args.index("--phone")
        if i + 1 < len(args):
            phone = args[i + 1]

    cmd = [sys.executable, str(ROOT / "subhub" / "menu.py"), "--fill-to-payment"]
    if phone:
        cmd.extend(["--phone", phone])

    print("=== Run to payment ===")
    print(" ".join(cmd))
    print()
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
