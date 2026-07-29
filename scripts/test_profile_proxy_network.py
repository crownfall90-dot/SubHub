"""Self-check: сеть сценариев — всегда прямое соединение.

Прокси и VPN-расширения удалены: VPN держит пользователь на ПК, браузер ходит
напрямую. Тест закрепляет этот контракт — если в план сети снова просочится
прокси или расширение, он упадёт.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

import menu as m  # noqa: E402


def main() -> None:
    use_vpn, proxy = asyncio.run(m._resolve_flipkart_launch_network())
    assert use_vpn is False, use_vpn
    assert proxy is None, proxy

    # Аргументы совместимости не должны воскрешать прокси
    use_vpn, proxy = asyncio.run(
        m._resolve_flipkart_launch_network(allow_vpn_extension=True))
    assert use_vpn is False and proxy is None

    # Запуск браузера — без proxy и без расширения
    kw = m._browser_launch_kw(headless=True)
    assert "proxy" not in kw, kw.get("proxy")
    assert not any("load-extension" in a for a in kw["args"]), kw["args"]

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    assert "_select_proxy_for_launch_async" in src, "заглушка нужна для совместимости"
    for gone in ("_get_free_proxies", "_proxy6_api_key", "_fetch_free_proxy_candidates"):
        assert gone not in src, f"остатки прокси: {gone}"

    print("PASS profile_proxy_network")


if __name__ == "__main__":
    main()
