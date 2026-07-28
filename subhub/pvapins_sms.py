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
from pathlib import Path
from typing import Optional, Tuple

import httpx
from loguru import logger

from paths import ROOT as _ROOT
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

class RateLimitedError(GrizzlySMSError):
    """HTTP 429 от PVAPins — слишком частые запросы, нужен бэкофф."""


class CancelTooEarlyError(GrizzlySMSError):
    """Отмена номера ещё недоступна (кулдаун 2 мин) — повторить позже."""


_AID_PREFIX = "pva:"
# Код после ключевого слова ("...using OTP 544741", "code is 123456") — берётся
# первым. Иначе любое 4-8-значное число впереди кода уводит парсер не туда:
# "2024: Your OTP is 544741" -> 2024, "support 1800 208..." -> 1800.
_OTP_KEYED_RE = re.compile(
    r"(?:otp|code|pin|password)\D{0,20}?(\d{4,8})\b", re.I)
# Вариант "#544741" — Flipkart дублирует код в конце SMS для автозаполнения.
_OTP_HASH_RE = re.compile(r"#\s*(\d{4,8})\b")
_OTP_RE = re.compile(r"\b(\d{4,8})\b")

# Своя статистика успешности по операторам (app): PVAPins не отдаёт success%
# через документированный API (это только на сайте) — считаем сами по фактам
# "код дошёл через get_sms" / "номер отменён без кода".
_STATS_FILE = _ROOT / "data" / "pvapins_operator_stats.json"
_STATS_LOCK = asyncio.Lock()


def _load_operator_stats() -> dict:
    try:
        return json.loads(_STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_operator_stats(stats: dict) -> None:
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_STATS_FILE)
    except Exception as exc:
        logger.debug(f"PVAPins: не удалось сохранить статистику операторов: {exc}")
_ERROR_NO_NUMBERS = (
    "no number found",
    "new numbers registration in progress",
    "error 102",
    "out of stock",  # текст из офиц. доков get_number.php
)
_ERROR_BALANCE = (
    "your balance is expired",
    "insufficient",
    "low balance",
    "not enough balance",
)
_ERROR_CONFIG = (
    "app not found",
    "country not found",
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
        min_reject_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
        parallel_slots: int = 4,
    ) -> None:
        self.api_key = api_key.strip()
        if not self.api_key or self.api_key.upper().startswith(("YOUR_", "ВАШ_")):
            raise ValueError("Не задан API-ключ PVAPins")
        self.country = (country or "india").strip().lower()
        self.apps = [str(a).strip() for a in (apps or [
            "Flipkart22", "Flipkart1", "Flipkart", "Flipkart33", "Flipkart2",
        ]) if str(a).strip()]
        self.max_price = max_price
        # Дефолт ширины волны для get_number_parallel() при вызове через
        # FailoverSMSClient (см. sms_failover.py) — сколько операторов
        # проверять с самого первого круга, а не расширяться по одному.
        self.parallel_slots = max(1, int(parallel_slots or 1))
        # Интервал между бронями. Жёсткий пол был 10с (6 номеров/мин) и полностью
        # съедал выигрыш от параллельного поиска; берём значение из config.yaml,
        # не опускаясь ниже 2с — при 429 всё равно включается общий бэкофф.
        self.buy_interval_seconds = max(2.0, float(buy_interval_seconds))
        # Доки get_reject_number.php: номер можно отменить ЧЕРЕЗ 2 минуты после
        # покупки (и до прихода кода). Раньше по умолчанию стояло 180с —
        # отмена ждала лишнюю минуту, а «Остановить всё» упиралось в таймаут.
        self.min_reject_seconds = max(120.0, float(min_reject_seconds))
        self._bought_at: dict[str, float] = {}
        self._costs: dict[str, float] = {}
        self._last_buy_mono = 0.0
        self._rate_cache: dict[str, float] | None = None
        self._rates_fetched_at = 0.0
        self._stat_recorded: set[str] = set()  # aid's с уже учтённым финальным исходом
        self._buy_gate = asyncio.Lock()        # сериализует резервирование слота покупки
        self._poll_gate = asyncio.Lock()       # то же для опроса кодов (get_sms)
        self._last_poll_mono = 0.0
        # Доки: опрашивать get_sms раз в 3-5с. Значение общее на клиент, т.к.
        # лимит у PVAPins на аккаунт, а не на номер.
        self.poll_interval_seconds = max(3.0, float(poll_interval_seconds or 3.0))
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
            if code == 429:
                raise RateLimitedError(f"PVAPins HTTP 429 on {path}") from None
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
        if any(x in low for x in _ERROR_CONFIG):
            # "App Not Found." / "Country Not Found." — конфигурация (apps/country
            # в config.yaml), а не временное отсутствие номеров; отдельная ошибка,
            # чтобы не тонула в общем NumberUnavailableError retry-цикле молча.
            raise GrizzlySMSError(f"PVAPins конфигурация: {raw.strip()}")
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
        """RAW-баланс из API.

        Внимание (прямо в доках PVAPins): здесь НЕ вычтены средства, замороженные
        под уже забронированные номера, поэтому значение выше реально доступного.
        Деньги замораживаются при покупке номера, возвращаются при его отмене и
        списываются окончательно, когда приходит код.
        Для «сколько реально можно потратить» — get_available_balance()."""
        raw = await self._get("get_balance.php")
        self._raise_if_api_error(raw)
        try:
            data = json.loads(raw)
            bal = float(data.get("balance", 0))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise GrizzlySMSError(f"PVAPins balance: {raw}") from exc
        logger.debug(f"PVAPins getBalance: {bal}")
        return bal

    async def get_frozen(self) -> float:
        """Сумма, замороженная под активные (ещё не отменённые и без кода) номера.

        Считается по своим активациям: API отдельного поля не отдаёт."""
        total = 0.0
        for aid in list(self._bought_at):
            try:
                app = self.parse_aid(aid)[0]
            except Exception:
                continue
            rate = self._costs.get(aid) or 0.0
            if not rate and self._rate_cache:
                rate = self._rate_cache.get(app) or 0.0
            total += float(rate)
        return round(total, 4)

    async def get_available_balance(self) -> float:
        """Баланс за вычетом заморозки — сколько реально можно потратить."""
        return round(max(0.0, await self.get_balance() - await self.get_frozen()), 4)

    _MIN_STAT_ATTEMPTS = 2  # сколько исходов нужно, чтобы доверять success-rate оператора
    _RATE_LIMIT_BACKOFF = 20.0  # сек паузы на весь клиент после HTTP 429
    _NOT_FOUND_GRACE = 60.0     # сек, пока "Number Not Found." после покупки = WAIT

    async def _record_result(self, app: str, success: bool) -> None:
        """Учитывает финальный исход (код дошёл / номер отменён без кода) в
        data/pvapins_operator_stats.json — свой success-rate, раз PVAPins не
        отдаёт его через документированный API (только на сайте)."""
        async with _STATS_LOCK:
            try:
                stats = _load_operator_stats()
                entry = stats.setdefault(app, {"attempts": 0, "successes": 0})
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
                if success:
                    entry["successes"] = int(entry.get("successes", 0)) + 1
                _save_operator_stats(stats)
            except Exception as exc:
                logger.debug(f"PVAPins: не удалось учесть исход {app}: {exc}")

    def _operator_rank(self, app: str, stats: dict) -> tuple[int, float]:
        """0 — подтверждённые 100%, 1 — без данных (пробуем осторожно), 2 — смешанный
        результат, 3 — подтверждённо плохой (0% на >= _MIN_STAT_ATTEMPTS). Вторым
        элементом — цена (для сортировки внутри группы), если известна."""
        price = self._rate_cache.get(app) if self._rate_cache else None
        price_key = price if price is not None else 999.0
        entry = stats.get(app)
        if not entry or int(entry.get("attempts", 0)) == 0:
            return (1, price_key)
        attempts = int(entry["attempts"])
        successes = int(entry.get("successes", 0))
        rate = successes / attempts
        if rate >= 1.0:
            return (0, price_key)
        if rate <= 0.0 and attempts >= self._MIN_STAT_ATTEMPTS:
            return (3, price_key)
        return (2, price_key)

    _RATE_CACHE_TTL = 3600.0  # сек — get_rates.php отдаёт статичный по факту прайс-лист

    _BRAND_RE = re.compile(r"^[A-Za-z]+")

    async def _refresh_rate_cache(self) -> bool:
        """Тянет get_rates.php (весь прайс-лист по стране) с кэшем на _RATE_CACHE_TTL.
        Возвращает False, если не удалось (сеть/парсинг) — вызывающий код тогда
        должен работать по self.apps без фильтра/сортировки по цене."""
        now = time.monotonic()
        if self._rate_cache is not None and now - self._rates_fetched_at <= self._RATE_CACHE_TTL:
            return True
        try:
            raw = await self._get("get_rates.php", {"country": self.country})
            # Доки: неизвестная страна возвращает bare-text "Country Not Found.",
            # НЕ JSON — проверяем перед парсингом, а не полагаемся на try/except.
            if not raw.lstrip().startswith("["):
                raise GrizzlySMSError(f"PVAPins get_rates: {raw.strip()[:120]}")
            items = json.loads(raw)
            cache: dict[str, float] = {}
            for it in items:
                name = str(it.get("app") or "").strip()
                try:
                    rate = float(it.get("rate"))
                except (TypeError, ValueError):
                    continue
                if name:
                    cache[name] = rate
            self._rate_cache = cache
            self._rates_fetched_at = now
            return True
        except Exception as exc:
            logger.warning(f"PVAPins get_rates.php недоступен ({exc}) — фильтр цены пропущен")
            return False

    async def _apps_within_budget(self, cap: Optional[float]) -> list[str]:
        """Все операторы того же бренда (не только перечисленные в config.yaml),
        отфильтрованные по реальной цене из get_rates.php и отсортированные
        дешёвые-первыми.

        get_number.php НЕ возвращает цену при бронировании (списание происходит
        только когда придёт SMS — см. доки PVAPins), поэтому проверка cost > cap
        ПОСЛЕ покупки номера никогда не срабатывает: cost всегда 0.0. Раньше это
        приводило к покупке номеров дороже max_price (например Flipkart1 = $0.36
        при cap=$0.20), когда дешёвые app'ы (Flipkart22=$0.12) были не в наличии
        и код просто перебирал следующий по списку без проверки цены."""
        if cap is None or not await self._refresh_rate_cache():
            return list(self.apps)

        # Автообнаружение всех операторов того же бренда (Flipkart, Flipkart22,
        # Flipkart-R3, ...) из полного прайс-листа — не только вручную
        # перечисленных в config.yaml → pvapins.apps.
        brands = {m.group(0).lower() for a in self.apps if (m := self._BRAND_RE.match(a))}
        discovered = [name for name in self._rate_cache
                      if any(name.lower().startswith(b) for b in brands)]
        extra = sorted(set(discovered) - set(self.apps))
        if extra:
            logger.debug(f"PVAPins: доп. операторы того же бренда из прайса — {', '.join(extra)}")
        pool = list(dict.fromkeys(self.apps + extra))  # без дублей, порядок стабилен

        known = [(a, self._rate_cache[a]) for a in pool if a in self._rate_cache]
        unknown = [a for a in pool if a not in self._rate_cache]
        candidates = [a for a, r in known if r <= float(cap) + 1e-9]
        over = [a for a, r in known if r > float(cap) + 1e-9]
        if over:
            logger.debug(
                f"PVAPins: app'ы дороже ${cap} пропущены — "
                + ", ".join(f"{a}=${self._rate_cache[a]}" for a in over)
            )
        # Ранжируем по своей статистике успешности (100% — первыми, подтверждённо
        # плохие — в конец), внутри группы — дешевле сначала. unknown-по-цене
        # (не нашлись в прайсе) идут совсем в хвост — цена не подтверждена.
        op_stats = _load_operator_stats()
        within = sorted(candidates, key=lambda a: self._operator_rank(a, op_stats))
        return within + unknown

    async def _throttle_buy(self) -> None:
        """Разносит запросы get_number.php во времени.

        Под замком и с резервированием слота (_last_buy_mono ставится ДО сна):
        без этого 5 параллельных пайплайнов читали одно и то же значение,
        просыпались одновременно и упирались в HTTP 429 пачками."""
        async with self._buy_gate:
            now = time.monotonic()
            slot = max(now, self._last_buy_mono + self.buy_interval_seconds)
            self._last_buy_mono = slot   # резервируем слот сразу
            wait = slot - now
        if wait > 0:
            await asyncio.sleep(wait)

    async def _cooldown_429(self) -> None:
        """Сдвигает общий слот покупок после 429 — иначе все параллельные
        задачи продолжают долбить API в том же ритме и лимит не снимается."""
        async with self._buy_gate:
            self._last_buy_mono = max(
                self._last_buy_mono, time.monotonic()) + self._RATE_LIMIT_BACKOFF

    async def _throttle_poll(self, activation_id: str) -> None:
        """Разносит опросы get_sms.php по всем номерам этого клиента.

        Доки PVAPins: опрашивать раз в 3-5с. При 10 активных номерах каждый
        со своим циклом это ~10 rps на аккаунт → HTTP 429 и коды не читаются.
        Слот общий на клиент и резервируется до сна (как у покупок)."""
        async with self._poll_gate:
            now = time.monotonic()
            slot = max(now, self._last_poll_mono + self.poll_interval_seconds)
            self._last_poll_mono = slot
            wait = slot - now
        if wait > 0:
            await asyncio.sleep(wait)

    async def _cooldown_poll(self) -> None:
        """Пауза на все опросы после 429 (лимит общий на аккаунт)."""
        async with self._poll_gate:
            self._last_poll_mono = max(
                self._last_poll_mono, time.monotonic()) + self._RATE_LIMIT_BACKOFF

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
        apps = await self._apps_within_budget(cap)
        if not apps:
            raise NumberUnavailableError(
                f"PVAPins: все app'ы дороже ${cap} — нет доступных в бюджете"
            )
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            for app in apps:
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
                        self._schedule_delayed_reject(aid)
                        continue
                    aid = self.make_aid(app, self.country, number)
                    self._bought_at[aid] = time.monotonic()
                    self._costs[aid] = cost
                    # aid хранит номер в формате API (для get_sms/reject), а наружу
                    # отдаём с кодом страны — menu.py/main.py сами срезают 91 перед
                    # вводом на Flipkart (там нужны ровно 10 цифр).
                    phone = number if number.startswith("91") else f"91{number}"
                    logger.info(f"PVAPins номер: +{phone} | app={app} | ${cost}")
                    return aid, phone, cost
                except RateLimitedError as exc:
                    # 429 — общий лимит аккаунта, а не проблема этого app:
                    # тормозим весь клиент и прекращаем обход остальных app'ов,
                    # иначе каждый из них добавляет свой запрос в тот же лимит.
                    last_err = exc
                    await self._cooldown_429()
                    logger.debug(f"PVAPins 429 — пауза {self._RATE_LIMIT_BACKOFF}s")
                    break
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
        # Возвращаем номер РОВНО как отдал API (обычно с кодом страны: 919434944186).
        # Обрезка до 10 цифр здесь ломала get_sms.php/get_reject_number.php —
        # PVAPins ведёт номер в своём формате и на "9434944186" отвечает
        # "Number Not Found.", из-за чего пришедшие коды терялись.
        # Формат для показа/ввода на Flipkart делается отдельно — см. get_number().
        number = re.sub(r"\D", "", number)
        if cost <= 0:
            # fallback из списка apps deduct неизвестен
            cost = 0.0
        _ = app
        return number, cost

    def _schedule_delayed_reject(self, activation_id: str) -> None:
        """Планирует _delayed_reject на persistent bg loop (grizzly._get_bg_loop),
        а не на asyncio.create_task: тот вешает задачу на loop текущего
        asyncio.run() автоматизации профиля — как только тот заканчивается,
        loop закрывается и неотправленный reject молча исчезает, номер
        остаётся висеть в заморозке до автоосвобождения PVAPins (20 мин)."""
        from grizzly import _get_bg_loop
        asyncio.run_coroutine_threadsafe(
            self._delayed_reject(activation_id), _get_bg_loop()
        )

    async def _delayed_reject(self, activation_id: str) -> None:
        """Фоновая отмена после кулдауна (cancel() больше не спит сам).
        Несколько попыток: сервер может считать кулдаун чуть иначе."""
        try:
            await asyncio.sleep(self.min_reject_seconds + 5)
            for _ in range(6):
                try:
                    await self.cancel(activation_id)
                    return
                except CancelTooEarlyError:
                    await asyncio.sleep(20)
                except Exception:
                    return
        except asyncio.CancelledError:
            raise
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
        """Параллельный поиск по операторам — дешёвые первыми, затем всё
        расширяющийся круг вплоть до max_price (как ценовые тиры у Grizzly).

        Раньше был строго последовательный обход одним потоком: с общим
        троттлингом покупок (10с) полный круг по 8 операторам занимал больше
        минуты. Теперь несколько операторов проверяются одновременно, а первый
        выдавший номер выигрывает — остальные отменяются."""
        _ = cycle
        if price_tiers:
            prices = [t.get("max_price") for t in price_tiers if t.get("max_price") is not None]
            if prices:
                max_price = max(float(p) for p in prices) if max_price is None else max_price
        cap = max_price if max_price is not None else self.max_price

        # timeout <= 0 → искать пока не купит (без дедлайна) — как в grizzly_sms.py.
        # Раньше deadline=now+0=now делал цикл нулевым: "PVAPins timeout: None"
        # мгновенно, даже не сделав ни одной попытки (get_number_timeout: 0
        # в config.yaml — это «без лимита», а не «ноль секунд»).
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + timeout) if timeout and timeout > 0 else None

        apps = await self._apps_within_budget(cap)   # уже отсортированы: дёшево → дорого
        if not apps:
            raise NumberUnavailableError(
                f"PVAPins: все app'ы дороже ${cap} — нет доступных в бюджете")

        slots = max(1, int(parallel_slots or 1))
        last_err: Exception | None = None
        wave = 0
        while deadline is None or loop.time() < deadline:
            # Волна 0 — самые дешёвые slots операторов; каждая следующая волна
            # расширяет окно, пока не охватит весь список в пределах бюджета.
            width = min(len(apps), slots * (wave + 1))
            batch = apps[:width]
            price_hint = ""
            if self._rate_cache:
                known = [self._rate_cache[a] for a in batch if a in self._rate_cache]
                if known:
                    price_hint = f" | ${min(known):.2f}–${max(known):.2f}"
            logger.info(
                f"  ╔ PVAPins: {len(batch)} оператор(ов){price_hint} "
                f"[круг {wave + 1}] | лимит ${cap}"
            )
            try:
                return await self._race_apps(batch, cap, poll_delay, deadline)
            except InsufficientBalanceError:
                raise
            except (NumberUnavailableError, GrizzlySMSError) as exc:
                last_err = exc
            # Волна не дала номера — расширяем круг (или начинаем заново, если
            # уже охватили всех): номера появляются в наличии волнами.
            wave = wave + 1 if width < len(apps) else 0
        raise NumberUnavailableError(f"PVAPins timeout: {last_err}")

    async def _race_apps(
        self,
        apps: list[str],
        cap: Optional[float],
        poll_delay: float,
        deadline: Optional[float],
    ) -> Tuple[str, str, float]:
        """Запрашивает номер у нескольких операторов одновременно; побеждает
        первый ответивший. Проигравшие задачи отменяются — если кто-то из них
        всё же успел получить номер, он тут же отправляется в отмену, чтобы не
        висел забронированным."""
        loop = asyncio.get_running_loop()
        tasks = {
            asyncio.create_task(self._acquire_one(app, cap)): app
            for app in apps
        }
        winner: Optional[Tuple[str, str, float]] = None
        last_err: Exception | None = None
        try:
            pending = set(tasks)
            while pending:
                remaining = None
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                done, pending = await asyncio.wait(
                    pending, timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    break  # дедлайн
                for t in done:
                    try:
                        res = t.result()
                    except InsufficientBalanceError:
                        raise
                    except Exception as exc:
                        last_err = exc
                        continue
                    if res is not None and winner is None:
                        winner = res
                if winner is not None:
                    break
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Номера, доехавшие уже после победителя, — сразу в отмену.
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, tuple) and res is not winner:
                    aid = res[0]
                    logger.debug(f"PVAPins: лишний номер {aid} — отменяю")
                    self._schedule_delayed_reject(aid)
        if winner is not None:
            return winner
        if isinstance(last_err, InsufficientBalanceError):
            raise last_err
        raise NumberUnavailableError(f"PVAPins: нет номеров ({last_err})")

    async def _acquire_one(
        self, app: str, cap: Optional[float]
    ) -> Optional[Tuple[str, str, float]]:
        """Одна попытка брони у конкретного оператора (с общим троттлингом)."""
        await self._throttle_buy()
        raw = await self._get("get_number.php", {
            "app": app, "country": self.country,
        })
        self._raise_if_api_error(raw)
        number, cost = self._parse_number_response(raw, app)
        aid = self.make_aid(app, self.country, number)
        self._bought_at[aid] = time.monotonic()
        rate = cost or (self._rate_cache or {}).get(app) or 0.0
        self._costs[aid] = rate
        if cap is not None and rate > float(cap) + 1e-9:
            # Подстраховка: прайс разошёлся с фактической ценой — не берём.
            logger.warning(f"PVAPins {app} ${rate} > лимит ${cap} — отменяю")
            self._schedule_delayed_reject(aid)
            raise NumberUnavailableError(f"PVAPins {app}: дороже лимита")
        phone = number if number.startswith("91") else f"91{number}"
        logger.info(f"PVAPins номер: +{phone} | app={app} | ${rate}")
        return aid, phone, rate

    async def get_status(self, activation_id: str) -> dict:
        app, country, number = self.parse_aid(activation_id)
        # Общий троттлинг опроса кодов: доки требуют 3-5s между вызовами,
        # а при 10 параллельных номерах наивный цикл давал ~10 rps и упирался
        # в HTTP 429 — коды в это время не читались вовсе.
        await self._throttle_poll(activation_id)
        try:
            raw = await self._get("get_sms.php", {
                "number": number,
                "country": country,
                "app": app,
            })
        except RateLimitedError as exc:
            await self._cooldown_poll()
            logger.debug(f"PVAPins get_sms 429 — пауза {self._RATE_LIMIT_BACKOFF}s")
            return {"type": "WAIT", "code": None}
        except GrizzlySMSError as exc:
            logger.warning(f"PVAPins get_sms: {self._safe_exc(exc)}")
            return {"type": "WAIT", "code": None}
        low = raw.lower()
        if "you have not received any code yet" in low or "not received" in low:
            return {"type": "WAIT", "code": None}
        if "customer not found" in low:
            return {"type": "CANCEL", "code": None}
        if "number not found" in low:
            # Сразу после покупки номер может быть ещё не проиндексирован на
            # стороне PVAPins — не хороним активацию по первому же ответу,
            # иначе теряем номер (и пришедший на него код) через секунду.
            bought = self._bought_at.get(str(activation_id), 0.0)
            if bought and time.monotonic() - bought < self._NOT_FOUND_GRACE:
                return {"type": "WAIT", "code": None}
            return {"type": "CANCEL", "code": None}
        code = self._extract_otp(raw)
        if code:
            await self._finalize_result(activation_id, app, True)
            return {"type": "OK", "code": code}
        return {"type": "WAIT", "code": None}

    async def _finalize_result(self, activation_id: str, app: str, success: bool) -> None:
        """Учитывает финальный исход ровно один раз на activation_id (свой
        success-rate по операторам — см. _record_result)."""
        aid = str(activation_id)
        if aid in self._stat_recorded:
            return
        self._stat_recorded.add(aid)
        await self._record_result(app, success)

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
        # Порядок важен: ключевое слово → "#код" → любое число (запасной путь,
        # когда поставщик отдал голый код без текста SMS).
        for rx in (_OTP_KEYED_RE, _OTP_HASH_RE, _OTP_RE):
            m = rx.search(text)
            if m:
                return m.group(1)
        return None

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
        """Отменяет номер. Если кулдаун (2 мин по докам) ещё не прошёл —
        бросает CancelTooEarlyError СРАЗУ, не блокируя вызывающего.

        Раньше здесь был asyncio.sleep до конца кулдауна: мониторы, которые
        пробуют отменить номер каждые несколько секунд, залипали на всю паузу
        и в это время не проверяли пришедший на номер код."""
        app, country, number = self.parse_aid(activation_id)
        bought = self._bought_at.get(str(activation_id), 0.0)
        if bought:
            age = time.monotonic() - bought
            if age < self.min_reject_seconds:
                raise CancelTooEarlyError(
                    f"PVAPins: отмена доступна через "
                    f"{int(self.min_reject_seconds - age)}s"
                )
        raw = await self._get("get_reject_number.php", {
            "number": number,
            "country": country,
            "app": app,
        })
        low = raw.lower()
        if "number rejected" in low or "rejected" in low or raw.strip() == "":
            self._bought_at.pop(str(activation_id), None)
            await self._finalize_result(activation_id, app, False)
            return
        if "limit: 3 minutes" in low or "3 minutes" in low:
            # Сервер считает, что кулдаун ещё идёт — пусть вызывающий повторит
            # позже, а не спит внутри отмены.
            raise CancelTooEarlyError(f"PVAPins reject: {raw.strip()[:80]}")
        if "not able to reject" in low or "number not found" in low:
            # уже нельзя / уже нет — считаем отменённым. "Not able to reject" может
            # также означать, что код уже пришёл (см. доки) — не учитываем как fail,
            # если get_status уже отметил успех (_finalize_result идемпотентен).
            self._bought_at.pop(str(activation_id), None)
            await self._finalize_result(activation_id, app, False)
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

    # Номер сам освобождается через 20 мин (доки get_reject_number.php) —
    # старше этого в истории уже неактивен, отменять нечего.
    _AUTO_RELEASE_MINUTES = 20

    async def get_active_activations(self) -> list:
        """Активные номера из get_history.php — аналог Grizzly-списка активаций.

        Активным считаем номер, по которому ещё НЕ пришёл код (message пустой),
        который не отменён (is_reserved == 0 — по докам 1 означает
        cancelled/released) и который моложе автоосвобождения (20 мин).
        Формат ответа — как у Grizzly (activationId/phoneNumber), чтобы
        вызывающий код работал с обоими провайдерами одинаково."""
        try:
            raw = await self._get("get_history.php")
            self._raise_if_api_error(raw)
            if not raw.lstrip().startswith("["):
                return []
            rows = json.loads(raw)
        except Exception as exc:
            logger.debug(f"PVAPins get_history: {self._safe_exc(exc)}")
            return []

        out: list = []
        for r in rows if isinstance(rows, list) else []:
            try:
                number = re.sub(r"\D", "", str(r.get("number") or ""))
                if not number:
                    continue
                if str(r.get("message") or "").strip():
                    continue  # код уже пришёл — номер отработал, отменять нельзя
                if int(r.get("is_reserved") or 0) == 1:
                    continue  # уже отменён/освобождён
                if int(r.get("minutes_passed") or 0) >= self._AUTO_RELEASE_MINUTES:
                    continue  # сам освободился по таймауту
                app = str(r.get("app_name") or r.get("app_link") or "").strip()
                out.append({
                    "activationId": self.make_aid(app, self.country, number[-10:]),
                    "phoneNumber": number,
                    "app": app,
                    "minutes_passed": int(r.get("minutes_passed") or 0),
                })
            except Exception:
                continue
        return out
