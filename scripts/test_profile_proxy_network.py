"""Self-check: сеть сценариев — прокси (тумблер) или direct (личный VPN на ПК).

VPN-расширения удалены из проекта: use_vpn всегда False, расширение
не ставится и не включается ни в одном сценарии.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

import menu as m  # noqa: E402


def _run() -> None:
    fake_proxy = {"server": "http://1.2.3.4:8080"}

    async def _proxy_on() -> None:
        """Прокси ВКЛ и живой найден → сценарий идёт через прокси."""
        with (
            patch.object(m, "_proxy_enabled", return_value=True),
            patch.object(
                m, "_select_proxy_for_launch_async",
                new=AsyncMock(return_value=fake_proxy),
            ),
        ):
            use_vpn, proxy, err = await m._resolve_profile_scenario_network(
                Path("chrome_profiles_done/profile_x"),
            )
        assert err is None
        assert use_vpn is False
        assert proxy == fake_proxy

    async def _proxy_off_direct() -> None:
        """Прокси ВЫКЛ → direct (личный VPN на ПК), без расширения."""
        with patch.object(m, "_proxy_enabled", return_value=False):
            use_vpn, proxy, err = await m._resolve_profile_scenario_network(
                Path("chrome_profiles_done/profile_x"),
            )
        assert err is None
        assert use_vpn is False
        assert proxy is None

    async def _proxy_on_dead() -> None:
        """Прокси ВКЛ, живого нет → direct (личный VPN), расширение не трогаем."""
        with (
            patch.object(m, "_proxy_enabled", return_value=True),
            patch.object(
                m, "_select_proxy_for_launch_async",
                new=AsyncMock(return_value=None),
            ),
        ):
            use_vpn, proxy = await m._resolve_flipkart_launch_network(
                allow_vpn_extension=True,
            )
        assert use_vpn is False
        assert proxy is None

    def _extension_removed() -> None:
        """Расширение недоступно нигде: dir=None, vpn_enabled=False."""
        assert m._vpn_extension_dir() is None
        assert m._vpn_extension_dir(ignore_toggle=True) is None
        assert m._vpn_enabled() is False
        assert m._needs_load_extension(None) is False

    asyncio.run(_proxy_on())
    asyncio.run(_proxy_off_direct())
    asyncio.run(_proxy_on_dead())
    _extension_removed()
    print("ok: profile proxy network (no vpn extension)")


if __name__ == "__main__":
    _run()
