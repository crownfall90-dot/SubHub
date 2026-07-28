"""
Failover SMS: GrizzlySMS (primary) → PVAPins (fallback).
Один интерфейс как у GrizzlySMSClient для menu.py / main.py / grizzly.py.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

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
        mode_getter: Optional[Callable[[], str]] = None,
    ) -> None:
        if primary is None and fallback is None:
            raise ValueError("Нужен хотя бы один SMS-провайдер (Grizzly или PVAPins)")
        self.primary = primary
        self.fallback = fallback
        # Переключатель «М» в консоли пишет sms.provider в config.yaml на лету —
        # mode_getter перечитывает его на каждый вызов get_number*, чтобы уже
        # запущенная автоматизация подхватывала переключение без перезапуска.
        self._mode_getter = mode_getter or (lambda: "auto")

    def _active(self) -> tuple[bool, bool]:
        """(использовать primary?, использовать fallback?) по текущему режиму."""
        try:
            mode = self._mode_getter()
        except Exception:
            mode = "auto"
        use_primary = self.primary is not None and mode in ("grizzly", "auto")
        use_fallback = self.fallback is not None and mode in ("pvapins", "auto")
        if not use_primary and not use_fallback:
            # Режим указывает на провайдера без клиента (ключ не задан) — не
            # блокируем работу молча, используем что есть.
            use_primary = self.primary is not None
            use_fallback = not use_primary and self.fallback is not None
        return use_primary, use_fallback

    def client_for(self, activation_id: str):
        """Публичный доступ к клиенту конкретного номера — нужен, чтобы
        показать баланс именно того сервиса, где номер куплен/отменён."""
        return self._client_for(activation_id)

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
        # Баланс активного по ТЕКУЩЕМУ режиму провайдера — раньше всегда
        # показывал primary (Grizzly), даже в режиме «только PVAPins».
        use_primary, use_fallback = self._active()
        if use_primary:
            try:
                return await self.primary.get_balance()
            except Exception:
                if not use_fallback:
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
        use_primary, use_fallback = self._active()
        if use_primary:
            try:
                return await self.primary.get_number(
                    service=service, country=country, max_price=max_price,
                    retries=retries, retry_delay=retry_delay,
                )
            except (NumberUnavailableError, InsufficientBalanceError, GrizzlySMSError) as exc:
                last = exc
                if not use_fallback:
                    raise
                logger.warning(f"SMS failover Grizzly → PVAPins: {exc}")
        if use_fallback:
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
        use_primary, use_fallback = self._active()
        if use_primary:
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
                if not use_fallback:
                    raise
                logger.warning(f"SMS failover Grizzly → PVAPins: {exc}")
            except GrizzlySMSError as exc:
                last = exc
                if not use_fallback:
                    raise
                logger.warning(f"SMS failover Grizzly → PVAPins: {exc}")

        if use_fallback:
            # Раньше здесь стояла жёсткая 1 — расширяющийся круг рос по одному
            # оператору за раунд, хотя _buy_gate (pvapins_sms.py) и так разносит
            # реальные запросы во времени; ширина волны это не ускоряет, только
            # мешает первому же кругу опросить сразу нескольких операторов.
            return await self.fallback.get_number_parallel(
                service=service,
                country=country,
                max_price=max_price,
                parallel_slots=self.fallback.parallel_slots,
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
        use_primary, use_fallback = self._active()
        if use_primary:
            return await self.primary.get_prices(service, country)
        assert self.fallback is not None
        return await self.fallback.get_prices(service, country)

    async def get_active_activations(self) -> list:
        """Активные номера ОБОИХ провайдеров — чтобы «отменить всё» и стартовая
        очистка «хвостов» видели и Grizzly, и PVAPins независимо от режима
        (номера могли остаться от предыдущего запуска на другом провайдере)."""
        out: list = []
        for client in (self.primary, self.fallback):
            if client is None:
                continue
            try:
                out.extend(await client.get_active_activations() or [])
            except Exception as exc:
                logger.debug(f"get_active_activations({type(client).__name__}): {exc}")
        return out


def _real_key(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    return bool(s) and not s.upper().startswith(("YOUR_", "ВАШ_"))


def _sms_provider_mode(cfg: dict | None = None) -> str:
    """grizzly | pvapins | auto — из config.yaml → sms.provider."""
    if cfg is None:
        cfg = {}
    mode = str(((cfg.get("sms") or {}).get("provider") or "auto")).strip().lower()
    if mode in ("grizzly", "grizzlysms", "g"):
        return "grizzly"
    if mode in ("pvapins", "pva", "p"):
        return "pvapins"
    return "auto"


_LIVE_MODE_TTL = 3.0  # сек — не парсим весь YAML на каждый вызов get_number*
_live_mode_cache: dict = {"ts": 0.0, "mode": "auto", "path": None}


def _read_live_provider_mode(config_path: "Path | str") -> str:
    """Быстро (без полного yaml.safe_load) перечитывает sms.provider из
    config.yaml — так переключатель «М» в консоли применяется к уже
    запущенной автоматизации без перезапуска процесса."""
    now = time.monotonic()
    key = str(config_path)
    if _live_mode_cache["path"] == key and now - _live_mode_cache["ts"] < _LIVE_MODE_TTL:
        return _live_mode_cache["mode"]
    mode = "auto"
    try:
        text = Path(config_path).read_text(encoding="utf-8")
        m = re.search(r"(?m)^sms:\s*\n(?:[ \t]+\S.*\n)*?[ \t]*provider:\s*(\S+)", text)
        if m:
            mode = _sms_provider_mode({"sms": {"provider": m.group(1)}})
    except Exception:
        pass
    _live_mode_cache.update(ts=now, mode=mode, path=key)
    return mode


def build_sms_client(
    secrets: dict,
    cfg: dict,
    config_path: "Path | str | None" = None,
) -> FailoverSMSClient | GrizzlySMSClient | PVAPinsSMSClient:
    """Собирает клиент из secrets.yaml + config.yaml.

    sms.provider:
      grizzly — только GrizzlySMS
      pvapins — только PVAPins
      auto    — Grizzly → PVAPins failover (по умолчанию)

    Если заданы ОБА api_key, собираются оба клиента независимо от текущего
    режима, а выбор между ними идёт на каждый вызов через `config_path`
    (если передан) — переключение режима в уже запущенной автоматизации
    подхватывается без пересоздания клиента/перезапуска процесса."""
    gs = (secrets.get("grizzlysms") or {})
    ps = (secrets.get("pvapins") or {})
    g_key = str(gs.get("api_key") or "").strip()
    p_key = str(ps.get("api_key") or "").strip()
    g_cfg = cfg.get("grizzlysms") or {}
    p_cfg = cfg.get("pvapins") or {}
    http_timeout = int(g_cfg.get("http_timeout") or p_cfg.get("http_timeout") or 30)
    mode = _sms_provider_mode(cfg)

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
            buy_interval_seconds=float(p_cfg.get("buy_interval_seconds") or 3),
            min_reject_seconds=float(p_cfg.get("min_reject_seconds") or 120),
            poll_interval_seconds=float(p_cfg.get("poll_interval_seconds") or 3),
            parallel_slots=int(p_cfg.get("parallel_slots") or 4),
        )

    # Режим «только X», но ключа нет — не молча падаем на другой
    if mode == "grizzly" and primary is None:
        raise ValueError("sms.provider=grizzly, но нет grizzlysms.api_key в secrets.yaml")
    if mode == "pvapins" and fallback is None:
        raise ValueError("sms.provider=pvapins, но нет pvapins.api_key в secrets.yaml")

    if primary and fallback:
        mode_getter = (
            (lambda: _read_live_provider_mode(config_path)) if config_path
            else (lambda: mode)
        )
        return FailoverSMSClient(primary, fallback, mode_getter=mode_getter)
    if primary:
        return primary
    if fallback:
        return fallback
    raise ValueError("Нет api_key ни у grizzlysms, ни у pvapins в secrets.yaml")
