"""Self-check: PVAPins delayed reject must survive its caller's event loop closing.

_delayed_reject() used to be scheduled with asyncio.create_task(), which binds
it to whichever loop is running at call time -- the per-profile automation
loop. Once that profile's asyncio.run() returns, the loop is closed and the
task (still sleeping through the cooldown) is destroyed before it ever cancels
the number. _schedule_delayed_reject() must instead hand the coroutine to
grizzly's persistent bg loop, which keeps running after the caller's loop dies.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    from pvapins_sms import PVAPinsSMSClient

    client = PVAPinsSMSClient(api_key="TEST_KEY", min_reject_seconds=0.05)
    cancelled = []

    # Stand-in for the real _delayed_reject (which sleeps min_reject_seconds+5
    # before cancelling) -- what matters here is whether the coroutine survives
    # its caller's loop closing, not the cooldown timing itself.
    async def fake_delayed_reject(aid: str) -> None:
        await asyncio.sleep(0.2)
        cancelled.append(aid)

    client._delayed_reject = fake_delayed_reject

    async def schedule_then_die() -> None:
        # Mimics one profile's automation: schedule the reject, then the
        # coroutine (and its loop, via asyncio.run()) ends immediately.
        client._schedule_delayed_reject("aid-1")

    asyncio.run(schedule_then_die())  # loop closes right after this returns

    deadline = time.monotonic() + 2.0
    while not cancelled and time.monotonic() < deadline:
        time.sleep(0.05)

    if cancelled != ["aid-1"]:
        print(f"FAIL pvapins_bg_reject: cancelled={cancelled!r}, want ['aid-1']")
        sys.exit(1)
    print("PASS pvapins_bg_reject")


if __name__ == "__main__":
    main()
