"""
PVAPins Legacy API — покупка +91 и OTP для Flipkart.
Docs: https://pvapins.com/api_integrate
Base: https://api.pvapins.com/user/api/
Auth: ?customer=API_KEY  (не REST sk_live_)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional, Tuple

import httpx
from loguru import logger

from grizzly_sms import (
    GrizzlySMSError,
    InsufficientBalanceError,
    NumberUnavailableError,
)

# Совместимость с GrizzlySMSClient.set_status
STATUS_READY = 1
STATUS_RETRY = 3
STATUS_COMPLETE = 6
STATUS_CANCEL = -1

_AID_PREFIX = "pva:"
_OTP_RE = re.compile(r"\b(\d{4,8})\b")
_ERROR_NO_NUMBERS = (
    "no number found",
    "new numbers registration in progress",
    "error 102",
)
_ERROR_BALANCE = (
    "your balance is expired",
    "insufficient",
    "low balance",
    "not enough balance",
)


class PVAPinsSMSClient:
    BASE = "https://api.pvapins.com/user/api"
    STATUS_READY = STATUS_READY
    STATUS_RETRY = STATUS_RETRY
    STATUS_COMPLETE = STATUS_COMPLETE
    STATUS_CANCEL = STATUS_CANCEL

    def __init__(
        self,
        api_key: str,
        http_timeout: int = 30,
        country: str = "india",
        apps: Optional[list] = None,
        max_price: Optional[float] = None,
        buy_interval_seconds: float = 10.0,
        min_reject_seconds: float = 180.0,
    ) -> None:
        self.api_key = api_key.strip()
        if not self.api_key or self.api_key.upper().startswith(("YOUR_", "ВАШ_")):
            raise ValueError("Не задан API-ключ PVAPins")
        self.country = (country or "india").strip().lower()
        self.apps = [str(a).strip() for a in (apps or [
            "Flipkart22", "Flipkart1", "Flipkart", "Flipkart33", "Flipkart2",
        ]) if str(a).strip()]
        self.max_price = max_price
        self.buy_interval_seconds = max(10.0, float(buy_interval_seconds))  # Standard: 6/min
        self.min_reject_seconds = float(min_reject_seconds)
        self._bought_at: dict[str, float] = {}
        self._costs: dict[str, float] = {}
        self._last_buy_mono = 0.0
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=min(10.0, http_timeout),
                read=float(http_timeout),
                write=min(10.0, http_timeout),
                pool=5.0,
            ),
            headers={"Accept": "application/json, text/plain", "User-Agent": "SubHub/1.0"},
            follow_redirects=True,
            trust_env=False,
        )

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "PVAPinsSMSClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    @staticmethod
    def is_aid(activation_id: str) -> bool:
        return str(activation_id).startswith(_AID_PREFIX)

    @staticmethod
    def make_aid(app: str, country: str, number: str) -> str:
        return f"{_AID_PREFIX}{app}:{country}:{number}"

    @staticmethod
    def parse_aid(activation_id: str) -> Tuple[str, str, str]:
        raw = str(activation_id)
        if not raw.startswith(_AID_PREFIX):
            raise GrizzlySMSError(f"Не PVAPins activation_id: {raw}")
        parts = raw[len(_AID_PREFIX):].split(":", 2)
        if len(parts) != 3:
            raise GrizzlySMSError(f"Битый PVAPins id: {raw}")
        return parts[0], parts[1], parts[2]

    async def _get(self, path: str, params: Optional[dict] = None) -> str:
        q = {"customer": self.api_key, **(params or {})}
        url = f"{self.BASE}/{path.lstrip('/')}"
        try:
            resp = await self._client.get(url, params=q)
            resp.raise_for_status()
            return resp.text.strip()
        except httpx.HTTPError as exc:
            # Не логируем URL — в query есть customer=api_key
            code = getattr(getattr(exc, "response", None), "status_code", "?")
            raise GrizzlySMSError(f"PVAPins HTTP {code} on {path}") from None

    @staticmethod
    def _safe_exc(exc: Exception) -> str:
        text = str(exc)
        # redact customer=... in accidental log strings
        return re.sub(r"(customer=)[^&\s]+", r"\1***", text, flags=re.I)

    def _raise_if_api_error(self, raw: str) -> None:
        low = raw.lower()
        if "customer not found" in low:
            raise GrizzlySMSError("Неверный API-ключ PVAPins")
        if any(x in low for x in _ERROR_BALANCE):
            raise InsufficientBalanceError("Недостаточно средств на балансе PVAPins")
        if any(x in low for x in _ERROR_NO_NUMBERS):
            raise NumberUnavailableError(raw or "No Number Found")
        if low.startswith("{") and '"error"' in low:
            try:
                err = json.loads(raw).get("error") or raw
            except Exception:
                err = raw
            raise GrizzlySMSError(str(err))

    async def get_balance(self) -> float:
        raw = await self._get("get_balance.php")
        self._raise_if_api_error(raw)
        try:
            data = json.loads(raw)
            bal = float(data.get("balance", 0))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise GrizzlySMSError(f"PVAPins balance: {raw}") from exc
        logger.debug(f"PVAPins getBalance: {bal}")
        return bal

    async def _throttle_buy(self) -> None:
        elapsed = time.monotonic() - self._last_buy_mono
        wait = self.buy_interval_seconds - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    async def get_number(
        self,
        service: str = "xt",
        country: str | int = 22,
        max_price: Optional[float] = None,
        retries: int = 5,
        retry_delay: float = 15.0,
    ) -> Tuple[str, str, float]:
        """Игнорирует service/country Grizzly-коды — берёт self.country / self.apps."""
        _ = service, country  # совместимость сигнатуры
        cap = max_price if max_price is not None else self.max_price
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            for app in self.apps:
                try:
                    await self._throttle_buy()
                    raw = await self._get("get_number.php", {
                        "app": app,
                        "country": self.country,
                    })
                    self._last_buy_mono = time.monotonic()
                    self._raise_if_api_error(raw)
                    number, cost = self._parse_number_response(raw, app)
                    if cap is not None and cost > float(cap) + 1e-9:
                        # дороже лимита — reject после min_reject и пробуем другой app
                        aid = self.make_aid(app, self.country, number)
                        self._bought_at[aid] = time.monotonic()
                        self._costs[aid] = cost
                        logger.warning(f"PVAPins {app} ${cost} > max_price ${cap} — reject later")
                        asyncio.create_task(self._delayed_reject(aid))
                        continue
                    aid = self.make_aid(app, self.country, number)
                    self._bought_at[aid] = time.monotonic()
                    self._costs[aid] = cost
                    phone = number if number.startswith("91") else f"91{number}"
                    logger.info(f"PVAPins номер: +{phone} | app={app} | ${cost}")
                    return aid, phone, cost
                except (NumberUnavailableError, InsufficientBalanceError) as exc:
                    last_err = exc
                    if isinstance(exc, InsufficientBalanceError):
                        raise
                except GrizzlySMSError as exc:
                    last_err = exc
                    logger.warning(f"PVAPins {app}: {exc}")
            if attempt < retries:
                await asyncio.sleep(retry_delay)
        if isinstance(last_err, InsufficientBalanceError):
            raise last_err
        raise NumberUnavailableError(
            f"PVAPins: нет номеров Flipkart/{self.country} ({last_err})"
        )

    def _parse_number_response(self, raw: str, app: str) -> Tuple[str, float]:
        number = ""
        cost = 0.0
        if raw.startswith("{") or raw.startswith("["):
            try:
                data = json.loads(raw)
                if isinstance(data, list) and data:
                    data = data[0]
                if isinstance(data, dict):
                    number = str(
                        data.get("number")
                        or data.get("phone")
                        or data.get("Phone")
                        or ""
                    )
                    for k in ("deduct", "rate", "price", "cost"):
                        if data.get(k) is not None:
                            try:
                                cost = float(data[k])
                                break
                            except (TypeError, ValueError):
                                pass
            except json.JSONDecodeError:
                pass
        if not number:
            # plain text number
            m = re.search(r"(\d{10,15})", raw)
            if m:
                number = m.group(1)
        if not number:
            low = raw.lower()
            if any(x in low for x in _ERROR_BALANCE):
                raise InsufficientBalanceError("Недостаточно средств на балансе PVAPins")
            raise NumberUnavailableError(f"PVAPins empty number: {raw[:120]}")
        number = re.sub(r"\D", "", number)
        if number.startswith("91") and len(number) > 10:
            number = number[2:]
        number = number[-10:]
        if cost <= 0:
            # fallback из списка apps deduct неизвестен
            cost = 0.0
        _ = app
        return number, cost

    async def _delayed_reject(self, activation_id: str) -> None:
        try:
            await asyncio.sleep(self.min_reject_seconds)
            await self.cancel(activation_id)
        except Exception:
            pass

    async def get_number_parallel(
        self,
        service: str,
        country: str | int,
        max_price: Optional[float] = None,
        parallel_slots: int = 1,
        poll_delay: float = 5.0,
        timeout: float = 90.0,
        price_tiers: Optional[list] = None,
        cycle: bool = False,
    ) -> Tuple[str, str, float]:
        """Один поток: Standard = max 6 новых номеров/мин."""
        _ = parallel_slots, cycle
        if price_tiers:
            prices = [t.get("max_price") for t in price_tiers if t.get("max_price") is not None]
            if prices:
                max_price = max(float(p) for p in prices) if max_price is None else max_price
        deadline = asyncio.get_running_loop().time() + timeout
        last_err: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                return await self.get_number(
                    service=service,
                    country=country,
                    max_price=max_price,
                    retries=1,
                    retry_delay=poll_delay,
                )
            except InsufficientBalanceError:
                raise
            except (NumberUnavailableError, GrizzlySMSError) as exc:
                last_err = exc
                await asyncio.sleep(max(poll_delay, self.buy_interval_seconds))
        raise NumberUnavailableError(f"PVAPins timeout: {last_err}")

    async def get_status(self, activation_id: str) -> dict:
        app, country, number = self.parse_aid(activation_id)
        try:
            raw = await self._get("get_sms.php", {
                "number": number,
                "country": country,
                "app": app,
            })
        except GrizzlySMSError as exc:
            logger.warning(f"PVAPins get_sms: {self._safe_exc(exc)}")
            return {"type": "WAIT", "code": None}
        low = raw.lower()
        if "you have not received any code yet" in low or "not received" in low:
            return {"type": "WAIT", "code": None}
        if "number not found" in low or "customer not found" in low:
            return {"type": "CANCEL", "code": None}
        code = self._extract_otp(raw)
        if code:
            return {"type": "OK", "code": code}
        return {"type": "WAIT", "code": None}

    @staticmethod
    def _extract_otp(raw: str) -> Optional[str]:
        text = raw
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    for k in ("sms", "code", "otp", "message", "msg"):
                        if data.get(k):
                            text = str(data[k])
                            break
            except json.JSONDecodeError:
                pass
        m = _OTP_RE.search(text)
        return m.group(1) if m else None

    async def set_status(self, activation_id: str, status: int) -> str:
        if status == self.STATUS_CANCEL:
            await self.cancel(activation_id)
            return "ACCESS_CANCEL"
        if status == self.STATUS_COMPLETE:
            await self.complete(activation_id)
            return "ACCESS_ACTIVATION"
        return "OK"

    async def complete(self, activation_id: str) -> None:
        # У PVAPins нет complete — номер просто истекает
        self._bought_at.pop(str(activation_id), None)
        logger.trace(f"PVAPins complete({activation_id}) no-op")

    async def cancel(self, activation_id: str) -> None:
        app, country, number = self.parse_aid(activation_id)
        bought = self._bought_at.get(str(activation_id), 0.0)
        age = time.monotonic() - bought if bought else self.min_reject_seconds
        if age < self.min_reject_seconds:
            await asyncio.sleep(self.min_reject_seconds - age)
        raw = await self._get("get_reject_number.php", {
            "number": number,
            "country": country,
            "app": app,
        })
        low = raw.lower()
        if "number rejected" in low or "rejected" in low or raw.strip() == "":
            self._bought_at.pop(str(activation_id), None)
            return
        if "limit: 3 minutes" in low or "3 minutes" in low:
            await asyncio.sleep(self.min_reject_seconds)
            raw2 = await self._get("get_reject_number.php", {
                "number": number,
                "country": country,
                "app": app,
            })
            low2 = raw2.lower()
            if "rejected" in low2 or "number rejected" in low2:
                self._bought_at.pop(str(activation_id), None)
                return
            raise GrizzlySMSError(f"PVAPins reject: {raw2}")
        if "not able to reject" in low or "number not found" in low:
            # уже нельзя / уже нет — считаем отменённым
            self._bought_at.pop(str(activation_id), None)
            return
        raise GrizzlySMSError(f"PVAPins reject: {raw}")

    async def wait_for_code(
        self,
        activation_id: str,
        timeout: int = 300,
        poll_interval: int = 5,
    ) -> Optional[str]:
        elapsed = 0
        while elapsed < timeout:
            status = await self.get_status(activation_id)
            if status["type"] == "OK":
                return status["code"]
            if status["type"] == "CANCEL":
                return None
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return None

    async def get_prices(self, service: str, country: str | int) -> dict:
        _ = service, country
        raw = await self._get("get_rates.php", {"country": self.country})
        try:
            return {"raw": json.loads(raw)}
        except json.JSONDecodeError:
            return {"raw": raw}

    async def get_active_activations(self) -> list:
        # Legacy API не отдаёт единый список активаций как Grizzly
        return []
