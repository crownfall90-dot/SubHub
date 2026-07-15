"""Self-check: profile scenarios pick proxy when enabled, skip when disabled."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import menu as m  # noqa: E402


def _run() -> None:
    fake_proxy = {"server": "http://1.2.3.4:8080"}

    async def _on() -> None:
        with (
            patch.object(m, "_proxy_enabled", return_value=True),
            patch.object(
                m, "_select_proxy_for_launch_async",
                new=AsyncMock(return_value=fake_proxy),
            ),
            patch.object(m, "_ensure_extension_in_profile", new=AsyncMock()),
        ):
            use_vpn, proxy, err = await m._resolve_profile_scenario_network(
                Path("chrome_profiles_done/profile_x"),
            )
        assert err is None
        assert use_vpn is False
        assert proxy == fake_proxy

    async def _off() -> None:
        with (
            patch.object(m, "_proxy_enabled", return_value=False),
            patch.object(m, "_flipkart_direct_accessible", new=AsyncMock(return_value=True)),
            patch.object(m, "_ensure_extension_in_profile", new=AsyncMock()),
        ):
            use_vpn, proxy, err = await m._resolve_profile_scenario_network(
                Path("chrome_profiles_done/profile_x"),
            )
        assert err is None
        assert use_vpn is False
        assert proxy is None

    async def _off_fallback_vpn() -> None:
        with (
            patch.object(m, "_proxy_enabled", return_value=False),
            patch.object(m, "_flipkart_direct_accessible", new=AsyncMock(return_value=False)),
            patch.object(m, "_vpn_extension_dir", return_value=Path("vpn_extension")),
            patch.object(
                m, "_ensure_extension_in_profile", new=AsyncMock(return_value=True),
            ),
            patch.object(m, "_vpn_chrome_cooldown", new=AsyncMock()),
        ):
            use_vpn, proxy, err = await m._resolve_profile_scenario_network(
                Path("chrome_profiles_done/profile_x"),
            )
        assert err is None
        assert use_vpn is True
        assert proxy is None

    async def _off_no_vpn_fallback_when_proxy_on() -> None:
        """proxy.enabled + нет живого прокси → не VeepN."""
        with (
            patch.object(m, "_proxy_enabled", return_value=True),
            patch.object(
                m, "_select_proxy_for_launch_async",
                new=AsyncMock(return_value=None),
            ),
            patch.object(m, "_vpn_extension_dir", return_value=Path("vpn_extension")),
        ):
            use_vpn, proxy = await m._resolve_flipkart_launch_network(
                allow_vpn_extension=True,
            )
        assert use_vpn is False
        assert proxy is None

    asyncio.run(_on())
    asyncio.run(_off())
    asyncio.run(_off_fallback_vpn())
    asyncio.run(_off_no_vpn_fallback_when_proxy_on())
    print("ok: profile proxy network")


if __name__ == "__main__":
    _run()
