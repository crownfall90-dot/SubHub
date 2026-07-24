"""Minimal self-check: PVAPins id parse + failover routing (no live buy)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

from pvapins_sms import PVAPinsSMSClient
from sms_failover import FailoverSMSClient, _sms_provider_mode

aid = PVAPinsSMSClient.make_aid("Flipkart22", "india", "9876543210")
assert PVAPinsSMSClient.is_aid(aid)
app, country, number = PVAPinsSMSClient.parse_aid(aid)
assert (app, country, number) == ("Flipkart22", "india", "9876543210")

assert _sms_provider_mode({"sms": {"provider": "grizzly"}}) == "grizzly"
assert _sms_provider_mode({"sms": {"provider": "pvapins"}}) == "pvapins"
assert _sms_provider_mode({"sms": {"provider": "auto"}}) == "auto"
assert _sms_provider_mode({}) == "auto"

# Failover routes by id prefix without network
class _Stub:
    STATUS_READY = 1
    STATUS_RETRY = 3
    STATUS_COMPLETE = 6
    STATUS_CANCEL = -1

    async def get_status(self, activation_id):
        return {"type": "WAIT", "code": None, "who": "primary"}

    async def close(self):
        pass

class _StubP(_Stub):
    async def get_status(self, activation_id):
        return {"type": "WAIT", "code": None, "who": "pva"}

async def _main():
    fo = FailoverSMSClient(primary=_Stub(), fallback=_StubP())
    st1 = await fo.get_status("12345")
    st2 = await fo.get_status(aid)
    assert st1["who"] == "primary"
    assert st2["who"] == "pva"
    await fo.close()
    print("ok")

if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
