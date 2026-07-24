"""
GrizzlySMS API client — покупка виртуальных номеров и получение OTP.
Документация: https://grizzlysms.com/docs
"""

import asyncio
import json as _json
from typing import Optional, Tuple

import httpx
from loguru import logger


class GrizzlySMSError(Exception):
    pass

class NumberUnavailableError(GrizzlySMSError):
    pass

class InsufficientBalanceError(GrizzlySMSError):
    pass


class GrizzlySMSClient:
    BASE_URL = "https://api.grizzlysms.com/stubs/handler_api.php"

    # Статусы для setStatus
    STATUS_READY    = 1   # сообщаем: SMS отправлена на номер
    STATUS_RETRY    = 3   # ждать следующий код
    STATUS_COMPLETE = 6   # активация завершена успешно
    STATUS_CANCEL   = -1  # отмена

    def __init__(self, api_key: str, http_timeout: int = 30) -> None:
        self.api_key = api_key.strip()
        if not self.api_key or self.api_key.upper().startswith(("YOUR_", "ВАШ_")):
            raise ValueError("Не задан API-ключ GrizzlySMS")
        # Один persistent client на весь сеанс — повторное использование TCP-соединений
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=min(10.0, http_timeout),
                read=float(http_timeout),
                write=min(10.0, http_timeout),
                pool=5.0,
            ),
            headers={"Accept": "text/plain"},
            follow_redirects=True,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=64,       # 5 слотов × 5 аккаунтов = 25 макс, 64 с запасом
                max_keepalive_connections=20,
                keepalive_expiry=20,      # закрываем соединение раньше, чем сервер
            ),
        )

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "GrizzlySMSClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ── HTTP helper ──────────────────────────────────────────────────────────

    async def _get(self, params: dict) -> str:
        full_params = {"api_key": self.api_key, **params}
        resp = await self._client.get(self.BASE_URL, params=full_params)
        resp.raise_for_status()
        return resp.text.strip()

    # ── Balance ──────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Возвращает текущий баланс в рублях."""
        raw = await self._get({"action": "getBalance"})
        if raw.startswith("ACCESS_BALANCE:"):
            balance = float(raw.split(":")[1])
            logger.debug(f"GrizzlySMS getBalance: {balance}")
            return balance
        if raw == "BAD_KEY":
            raise GrizzlySMSError("Неверный API-ключ GrizzlySMS")
        raise GrizzlySMSError(f"Ошибка баланса: {raw}")

    # ── Get number ───────────────────────────────────────────────────────────

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
        """
        Параллельный поиск номера с поддержкой ценовых уровней.

        price_tiers — список {"max_price": float, "duration": int}.
        cycle=True: после последнего тира снова с первого (бесконечная ротация).
        Если price_tiers не задан — используем max_price весь timeout.
        """
        if not price_tiers:
            price_tiers = [{"max_price": max_price, "duration": 0}]

        # Жёсткий потолок: ни один тир не выше max_price (если задан).
        if max_price is not None:
            cap = float(max_price)
            clamped = []
            for t in price_tiers:
                tp = t.get("max_price")
                if tp is None:
                    clamped.append(dict(t, max_price=cap))
                else:
                    clamped.append(dict(t, max_price=min(float(tp), cap)))
            price_tiers = clamped

        # timeout <= 0 → искать пока не купит (без дедлайна).
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + timeout) if timeout and timeout > 0 else None
        tier_seq  = 0   # абсолютный счётчик тиров (не сбрасывается при цикле)
        n_tiers   = len(price_tiers)

        while True:
            remaining = (deadline - loop.time()) if deadline is not None else float("inf")
            if deadline is not None and remaining <= 0:
                break

            tier_idx      = tier_seq % n_tiers
            tier          = price_tiers[tier_idx]
            tier_price    = tier.get("max_price")
            tier_duration = float(tier.get("duration") or 0)

            is_last_pass  = (tier_idx == n_tiers - 1) and not cycle
            # При cycle duration<=0 = шаг 20с (иначе застряли бы на одном тире навсегда).
            if tier_duration <= 0 and cycle and not is_last_pass:
                tier_duration = 20.0
            tier_limit    = remaining if (tier_duration <= 0 or is_last_pass) \
                            else min(tier_duration, remaining)

            cycle_round   = tier_seq // n_tiers + 1
            price_str     = f"${tier_price:.2f}" if tier_price is not None else "любая"
            label         = (f"круг {cycle_round}, шаг {tier_idx + 1}/{n_tiers}"
                             if cycle else f"уровень {tier_idx + 1}/{n_tiers}")
            logger.info(
                f"  ╔ Цена {price_str} [{label}] | {parallel_slots} слотов | "
                f"{'∞' if tier_limit == float('inf') else f'{tier_limit:.0f}s'}"
            )

            result = await self._parallel_acquire(
                service, country, tier_price, parallel_slots, poll_delay, tier_limit
            )

            if result is not None:
                return result

            tier_seq += 1
            next_idx = tier_seq % n_tiers
            if not cycle and tier_seq >= n_tiers:
                break
            next_price = price_tiers[next_idx].get("max_price")
            logger.info(
                f"  ╚ Нет номера → следующая цена ${next_price:.2f}"
                + (" (повтор цикла)" if cycle and next_idx == 0 else "")
            )

        raise NumberUnavailableError(
            f"NO_NUMBERS при всех ценовых уровнях"
            + (f" (таймаут {timeout:.0f}s)" if timeout and timeout > 0 else "")
        )

    async def _parallel_acquire(
        self,
        service: str,
        country: str | int,
        max_price: Optional[float],
        parallel_slots: int,
        poll_delay: float,
        tier_timeout: float,
    ) -> Optional[Tuple[str, str, float]]:
        """
        Запускает parallel_slots конкурентных слотов на один ценовой уровень.
        Возвращает (activation_id, phone, cost) при успехе или None при истечении tier_timeout.
        """
        import time as _time
        import sys as _sys

        _no_count  = [0]   # суммарно NO_NUMBERS по всем слотам
        _t_start   = _time.monotonic()
        _found_evt = asyncio.Event()

        def _print_status(extra: str = "") -> None:
            elapsed = _time.monotonic() - _t_start
            price_s = f"${max_price:.2f}" if max_price is not None else "любая"
            line = f"  ║ NO_NUMBERS ×{_no_count[0]}  цена={price_s}  {elapsed:.0f}s{extra}"
            _sys.stdout.write("\r" + line + "   ")
            _sys.stdout.flush()

        async def _slot(slot_idx: int) -> Tuple[str, str, float]:
            # Равномерный сдвиг старта чтобы запросы не шли все одновременно
            if slot_idx > 0:
                await asyncio.sleep(slot_idx * (poll_delay / max(parallel_slots, 1)))
            while True:
                params: dict = {
                    "action": "getNumberV2",
                    "service": str(service),
                    "country": str(country),
                }
                if max_price is not None:
                    params["maxPrice"] = str(max_price)

                try:
                    raw = await self._get(params)
                except httpx.PoolTimeout:
                    await asyncio.sleep(poll_delay)
                    continue
                except httpx.RequestError:
                    await asyncio.sleep(min(1.0, poll_delay))
                    continue

                try:
                    data   = _json.loads(raw)
                    act_id = str(data["activationId"])
                    phone  = str(data["phoneNumber"])
                    cost   = float(data.get("activationCost", 0))
                    _found_evt.set()
                    return act_id, phone, cost
                except (_json.JSONDecodeError, KeyError):
                    pass

                if raw == "NO_NUMBERS":
                    _no_count[0] += 1
                    _print_status()
                    await asyncio.sleep(poll_delay)
                    continue
                if raw == "NO_BALANCE":
                    raise InsufficientBalanceError("Недостаточно средств")
                if raw == "BAD_KEY":
                    raise GrizzlySMSError("Неверный API-ключ")

                # Неожиданный ответ — логируем и ждём
                logger.warning(f"\r  ║ Неожиданный ответ: {raw[:80]}")
                await asyncio.sleep(poll_delay)

        tasks = [asyncio.create_task(_slot(i)) for i in range(parallel_slots)]

        winner_id:    Optional[str] = None
        winner_phone: Optional[str] = None
        winner_cost:  float = 0.0
        last_fatal:   Optional[Exception] = None

        try:
            wait_timeout = None if tier_timeout == float("inf") else tier_timeout
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=wait_timeout,
            )

            for t in pending:
                t.cancel()

            if not done:
                # Таймаут уровня — очищаем строку и переходим к следующей цене
                _sys.stdout.write("\r" + " " * 70 + "\r")
                _sys.stdout.flush()
                return None

            for t in done:
                try:
                    act_id, phone, cost = t.result()
                    if winner_id is None:
                        winner_id, winner_phone, winner_cost = act_id, phone, cost
                    else:
                        # Два слота успели одновременно — лишний регистрируем для отложенной отмены
                        try:
                            import grizzly
                            ph_10 = phone.lstrip("+")
                            if ph_10.startswith("91") and len(ph_10) > 10:
                                ph_10 = ph_10[2:]
                            ph_10 = ph_10[-10:]
                            grizzly.register_rental(act_id, ph_10, _time.monotonic())
                            grizzly.mark_failed(act_id)
                        except Exception:
                            # fallback: пробуем немедленно отменить
                            asyncio.create_task(self.cancel(act_id))
                except (InsufficientBalanceError, GrizzlySMSError) as exc:
                    last_fatal = exc
                except Exception:
                    pass

            if winner_id is None:
                if last_fatal:
                    raise last_fatal
                return None

            # Очищаем строку прогресса перед финальным сообщением
            _sys.stdout.write("\r" + " " * 70 + "\r")
            _sys.stdout.flush()
            return winner_id, winner_phone, winner_cost  # type: ignore[return-value]

        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def get_number(
        self,
        service: str,
        country: str | int,
        max_price: Optional[float] = None,
        retries: int = 5,
        retry_delay: float = 15.0,
    ) -> Tuple[str, str, float]:
        """
        Арендует виртуальный номер.
        Возвращает (activation_id, phone_number, cost).

        service  — код сервиса, например "xt" для Flipkart
        country  — код страны, например 22 (Индия) или "any"
        retries  — сколько раз повторить при NO_NUMBERS
        """
        params: dict = {
            "action": "getNumberV2",
            "service": str(service),
            "country": str(country),
        }
        if max_price is not None:
            params["maxPrice"] = str(max_price)

        for attempt in range(1, retries + 1):
            raw = await self._get(params)
            logger.debug(f"getNumber ответ: {raw.split(':', 1)[0][:40]}")

            # Успех — JSON ответ
            try:
                data = _json.loads(raw)
                activation_id = str(data["activationId"])
                phone = str(data["phoneNumber"])
                cost = float(data.get("activationCost", 0))
                logger.info(f"Номер куплен: +{phone} | id={activation_id} | цена=${cost}")
                return activation_id, phone, cost
            except (_json.JSONDecodeError, KeyError):
                pass

            # Известные ошибки
            if raw == "NO_NUMBERS":
                if attempt < retries:
                    logger.warning(f"NO_NUMBERS, повтор через {retry_delay}s (попытка {attempt}/{retries})")
                    await asyncio.sleep(retry_delay)
                    continue
                raise NumberUnavailableError("Нет доступных номеров после всех попыток")

            if raw == "NO_BALANCE":
                raise InsufficientBalanceError("Недостаточно средств на балансе GrizzlySMS")

            if raw == "BAD_KEY":
                raise GrizzlySMSError("Неверный API-ключ GrizzlySMS")

            raise GrizzlySMSError(f"Неожиданный ответ getNumber: {raw}")

        raise NumberUnavailableError("Нет доступных номеров")

    # ── Activation status ────────────────────────────────────────────────────

    async def get_status(self, activation_id: str) -> dict:
        """
        Возвращает словарь:
          {"type": "WAIT"}
          {"type": "OK",         "code": "123456"}
          {"type": "WAIT_RETRY", "code": "123456"}   ← неверный код, ждём следующий
          {"type": "CANCEL"}
        """
        raw = await self._get({"action": "getStatus", "id": str(activation_id)})

        if raw.startswith("STATUS_OK:"):
            return {"type": "OK", "code": raw.split(":", 1)[1]}

        if raw == "STATUS_WAIT_CODE":
            return {"type": "WAIT", "code": None}

        if raw.startswith("STATUS_WAIT_RETRY:"):
            return {"type": "WAIT_RETRY", "code": raw.split(":", 1)[1]}

        if raw == "STATUS_WAIT_RESEND":
            return {"type": "WAIT_RESEND", "code": None}

        if raw == "STATUS_CANCEL":
            return {"type": "CANCEL", "code": None}

        logger.warning(f"Неизвестный статус активации: {raw}")
        return {"type": "UNKNOWN", "code": None}

    async def set_status(self, activation_id: str, status: int) -> str:
        raw = await self._get({
            "action": "setStatus",
            "id": str(activation_id),
            "status": str(status),
        })
        logger.trace(f"setStatus({activation_id}, {status}) → {raw}")
        return raw

    async def complete(self, activation_id: str) -> None:
        """Завершить активацию успешно."""
        raw = await self.set_status(activation_id, self.STATUS_COMPLETE)
        if raw not in ("ACCESS_ACTIVATION", "ACCESS_CANCEL"):
            logger.warning(f"complete({activation_id}) неожиданный ответ: {raw}")

    async def cancel(self, activation_id: str) -> None:
        """Отменить активацию (вернуть деньги, если ещё не получена SMS)."""
        raw = await self.set_status(activation_id, self.STATUS_CANCEL)
        if raw != "ACCESS_CANCEL":
            raise GrizzlySMSError(f"Отмена {activation_id} не удалась: {raw}")

    # ── Wait for SMS ─────────────────────────────────────────────────────────

    async def wait_for_code(
        self,
        activation_id: str,
        timeout: int = 300,
        poll_interval: int = 5,
    ) -> Optional[str]:
        """
        Ждёт SMS-код, опрашивая API каждые poll_interval секунд.
        Возвращает строку с кодом или None при истечении таймаута / отмене.
        """
        elapsed = 0
        logger.info(f"Ожидание SMS (id={activation_id}, таймаут={timeout}s)...")

        while elapsed < timeout:
            status = await self.get_status(activation_id)

            if status["type"] == "OK":
                logger.success("SMS получена")
                return status["code"]

            if status["type"] == "CANCEL":
                logger.warning(f"Активация {activation_id} отменена провайдером")
                return None

            logger.debug(f"Статус: {status['type']} | ожидание {elapsed}s/{timeout}s")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.error(f"Таймаут ожидания SMS для id={activation_id}")
        return None

    # ── Prices (справочно) ───────────────────────────────────────────────────

    async def get_prices(self, service: str, country: str | int) -> dict:
        raw = await self._get({
            "action": "getPricesV3",
            "service": str(service),
            "country": str(country),
        })
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            return {"raw": raw}

    async def get_active_activations(self) -> list:
        raw = await self._get({"action": "getActiveActivations"})
        try:
            result = _json.loads(raw)
            return result if isinstance(result, list) else []
        except _json.JSONDecodeError:
            return []
