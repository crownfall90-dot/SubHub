"""
Failover SMS: GrizzlySMS (primary) → PVAPins (fallback).
Один интерфейс как у GrizzlySMSClient для menu.py / main.py / grizzly.py.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

from grizzly_sms import (
    GrizzlySMSClient,
    GrizzlySMSError,
    InsufficientBalanceError,
    NumberUnavailableError,
)
from pvapins_sms import PVAPinsSMSClient


class FailoverSMSClient:
    STATUS_READY = GrizzlySMSClient.STATUS_READY
    STATUS_RETRY = GrizzlySMSClient.STATUS_RETRY
    STATUS_COMPLETE = GrizzlySMSClient.STATUS_COMPLETE
    STATUS_CANCEL = GrizzlySMSClient.STATUS_CANCEL

    def __init__(
        self,
        primary: Optional[GrizzlySMSClient] = None,
        fallback: Optional[PVAPinsSMSClient] = None,
    ) -> None:
        if primary is None and fallback is None:
            raise ValueError("Нужен хотя бы один SMS-провайдер (Grizzly или PVAPins)")
        self.primary = primary
        self.fallback = fallback

    def _client_for(self, activation_id: str):
        if PVAPinsSMSClient.is_aid(activation_id):
            if self.fallback is None:
                raise GrizzlySMSError("PVAPins id, но клиент не настроен")
            return self.fallback
        if self.primary is None:
            raise GrizzlySMSError("Grizzly id, но клиент не настроен")
        return self.primary

    async def close(self) -> None:
        for c in (self.primary, self.fallback):
            if c is not None:
                try:
                    await c.close()
                except Exception:
                    pass

    async def __aenter__(self) -> "FailoverSMSClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def get_balance(self) -> float:
        # Для UI/логов — баланс primary, иначе fallback
        if self.primary is not None:
            try:
                return await self.primary.get_balance()
            except Exception:
                if self.fallback is None:
                    raise
        assert self.fallback is not None
        return await self.fallback.get_balance()

    async def get_balances(self) -> dict:
        out: dict = {}
        if self.primary is not None:
            try:
                out["grizzly"] = await self.primary.get_balance()
            except Exception as exc:
                out["grizzly_error"] = str(exc)
        if self.fallback is not None:
            try:
                out["pvapins"] = await self.fallback.get_balance()
            except Exception as exc:
                out["pvapins_error"] = str(exc)
        return out

    async def get_number(
        self,
        service: str,
        country: str | int,
        max_price: Optional[float] = None,
        retries: int = 5,
        retry_delay: float = 15.0,
    ) -> Tuple[str, str, float]:
        last: Exception | None = None
        if self.primary is not None:
            try:
                return await self.primary.get_number(
                    service=service, country=country, max_price=max_price,
                    retries=retries, retry_delay=retry_delay,
                )
            except (NumberUnavailableError, InsufficientBalanceError, GrizzlySMSError) as exc:
                last = exc
                if self.fallback is None:
                    raise
                logger.warning(f"SMS failover Grizzly → PVAPins: {exc}")
        if self.fallback is not None:
            return await self.fallback.get_number(
                service=service, country=country, max_price=max_price,
                retries=retries, retry_delay=retry_delay,
            )
        raise last or NumberUnavailableError("Нет SMS-провайдера")

    async def get_number_parallel(
        self,
        service: str,
        country: str | int,
        max_price: Optional[float] = None,
        parallel_slots: int = 3,
        poll_delay: float = 5.0,
        timeout: float = 90.0,
        price_tiers: Optional[list] = None,
        cycle: bool = False,
    ) -> Tuple[str, str, float]:
        last: Exception | None = None
        if self.primary is not None:
            try:
                return await self.primary.get_number_parallel(
                    service=service,
                    country=country,
                    max_price=max_price,
                    parallel_slots=parallel_slots,
                    poll_delay=poll_delay,
                    timeout=timeout,
                    price_tiers=price_tiers,
                    cycle=cycle,
                )
            except (NumberUnavailableError, InsufficientBalanceError) as exc:
                last = exc
                if self.fallback is None:
                    raise
                logger.warning(f"SMS failover Grizzly → PVAPins: {exc}")
            except GrizzlySMSError as exc:
                last = exc
                if self.fallback is None:
                    raise
                logger.warning(f"SMS failover Grizzly → PVAPins: {exc}")

        if self.fallback is not None:
            return await self.fallback.get_number_parallel(
                service=service,
                country=country,
                max_price=max_price,
                parallel_slots=1,
                poll_delay=poll_delay,
                timeout=timeout,
                price_tiers=price_tiers,
                cycle=cycle,
            )
        raise last or NumberUnavailableError("Нет SMS-провайдера")

    async def get_status(self, activation_id: str) -> dict:
        return await self._client_for(activation_id).get_status(activation_id)

    async def set_status(self, activation_id: str, status: int) -> str:
        return await self._client_for(activation_id).set_status(activation_id, status)

    async def complete(self, activation_id: str) -> None:
        await self._client_for(activation_id).complete(activation_id)

    async def cancel(self, activation_id: str) -> None:
        await self._client_for(activation_id).cancel(activation_id)

    async def wait_for_code(
        self,
        activation_id: str,
        timeout: int = 300,
        poll_interval: int = 5,
    ) -> Optional[str]:
        return await self._client_for(activation_id).wait_for_code(
            activation_id, timeout=timeout, poll_interval=poll_interval,
        )

    async def get_prices(self, service: str, country: str | int) -> dict:
        if self.primary is not None:
            return await self.primary.get_prices(service, country)
        assert self.fallback is not None
        return await self.fallback.get_prices(service, country)

    async def get_active_activations(self) -> list:
        if self.primary is not None:
            return await self.primary.get_active_activations()
        return []


def _real_key(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    return bool(s) and not s.upper().startswith(("YOUR_", "ВАШ_"))


def build_sms_client(
    secrets: dict,
    cfg: dict,
) -> FailoverSMSClient | GrizzlySMSClient | PVAPinsSMSClient:
    """Собирает клиент из secrets.yaml + config.yaml."""
    gs = (secrets.get("grizzlysms") or {})
    ps = (secrets.get("pvapins") or {})
    g_key = str(gs.get("api_key") or "").strip()
    p_key = str(ps.get("api_key") or "").strip()
    g_cfg = cfg.get("grizzlysms") or {}
    p_cfg = cfg.get("pvapins") or {}
    http_timeout = int(g_cfg.get("http_timeout") or p_cfg.get("http_timeout") or 30)

    primary = None
    fallback = None
    if _real_key(g_key):
        primary = GrizzlySMSClient(g_key, http_timeout=http_timeout)
    if _real_key(p_key):
        apps = p_cfg.get("apps")
        fallback = PVAPinsSMSClient(
            p_key,
            http_timeout=http_timeout,
            country=str(p_cfg.get("country") or "india"),
            apps=list(apps) if apps else None,
            max_price=p_cfg.get("max_price"),
            buy_interval_seconds=float(p_cfg.get("buy_interval_seconds") or 10),
            min_reject_seconds=float(p_cfg.get("min_reject_seconds") or 180),
        )

    if primary and fallback:
        return FailoverSMSClient(primary, fallback)
    if primary:
        return primary
    if fallback:
        return fallback
    raise ValueError("Нет api_key ни у grizzlysms, ни у pvapins в secrets.yaml")
