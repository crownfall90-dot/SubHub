"""
GGSell Seller API client.
Документация: https://seller.ggsel.com/docs

Аутентификация:
  1. POST /api_sellers/api/apilogin
     Body: {seller_id, timestamp, sign=SHA256(api_key+timestamp)}
  2. Ответ: {retval, desc, token, seller_id, valid_thru}
  3. Все остальные запросы: ?token=TOKEN (query-параметр)
"""

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger


class GGSellError(Exception):
    pass


class GGSellAuthError(GGSellError):
    pass


class GGSellClient:
    BASE_URL = "https://seller.ggsel.com/api_sellers/api"
    V2_BASE_URL = "https://seller.ggsel.com/api_sellers/v2"

    def __init__(self, api_key: str, seller_id: int, http_timeout: int = 30) -> None:
        self.api_key = api_key
        self.seller_id = seller_id
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._client = httpx.AsyncClient(
            timeout=http_timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "GGSellClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _sign(self, timestamp: str) -> str:
        return hashlib.sha256((self.api_key + timestamp).encode()).hexdigest()

    async def get_token(self, force: bool = False) -> str:
        """Получить или обновить токен сессии."""
        now = time.monotonic()
        if self._token and not force and now < self._token_expires_at:
            return self._token

        ts = str(int(time.time()))
        resp = await self._client.post(
            f"{self.BASE_URL}/apilogin",
            json={
                "seller_id": self.seller_id,
                "timestamp": ts,
                "sign": self._sign(ts),
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("retval", -1) != 0:
            raise GGSellAuthError(
                f"GGSell auth failed: retval={data.get('retval')} desc={data.get('desc')}"
            )

        token = data.get("token", "")
        if not token:
            raise GGSellAuthError(f"GGSell: пустой токен в ответе: {data}")

        self._token = token
        # valid_thru — ISO datetime, считаем срок ~50 минут от сейчас для надёжности
        self._token_expires_at = now + 3000
        try:
            valid_thru = data.get("valid_thru", "")
            if valid_thru:
                dt = datetime.fromisoformat(valid_thru.rstrip("Z")).replace(tzinfo=timezone.utc)
                self._token_expires_at = time.monotonic() + max(
                    0, (dt - datetime.now(timezone.utc)).total_seconds() - 60
                )
        except Exception:
            pass

        logger.trace(f"GGSell: токен получен (seller_id={data.get('seller_id')})")
        return self._token

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[Dict] = None, retry: bool = True) -> Any:
        token = await self.get_token()
        full_params = {"token": token, **(params or {})}
        try:
            resp = await self._client.get(f"{self.BASE_URL}{path}", params=full_params)
            if resp.status_code == 401 and retry:
                # токен истёк — обновляем и повторяем один раз
                token = await self.get_token(force=True)
                full_params["token"] = token
                resp = await self._client.get(f"{self.BASE_URL}{path}", params=full_params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise GGSellError(f"HTTP {exc.response.status_code} for {path}") from exc

    async def _post(
        self,
        path: str,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
        retry: bool = True,
    ) -> Any:
        token = await self.get_token()
        full_params = {"token": token, **(params or {})}
        try:
            resp = await self._client.post(
                f"{self.BASE_URL}{path}", params=full_params, json=json_body
            )
            if resp.status_code == 401 and retry:
                token = await self.get_token(force=True)
                full_params["token"] = token
                resp = await self._client.post(
                    f"{self.BASE_URL}{path}", params=full_params, json=json_body
                )
            resp.raise_for_status()
            if not resp.content:
                return {}
            try:
                return resp.json()
            except Exception:
                return {}
        except httpx.HTTPStatusError as exc:
            raise GGSellError(f"HTTP {exc.response.status_code} for {path}") from exc

    @property
    def _v2_headers(self) -> Dict[str, str]:
        return {"Authorization": self.api_key, "Accept": "application/json",
                "Content-Type": "application/json"}

    async def _v2_get(self, path: str, params: Optional[Dict] = None) -> Any:
        try:
            resp = await self._client.get(
                f"{self.V2_BASE_URL}{path}",
                headers=self._v2_headers,
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise GGSellError(f"HTTP {exc.response.status_code} for v2{path}") from exc

    async def _v2_post(self, path: str, json_body: Optional[Dict] = None) -> Any:
        try:
            resp = await self._client.post(
                f"{self.V2_BASE_URL}{path}",
                headers=self._v2_headers,
                json=json_body or {},
            )
            resp.raise_for_status()
            if not resp.content:
                return {}
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise GGSellError(f"HTTP {exc.response.status_code} for v2{path}") from exc

    # ── Account ──────────────────────────────────────────────────────────────

    async def get_balance_info(self) -> Dict[str, float]:
        """Вернуть информацию о балансе: free, lock, plus."""
        raw = await self._get("/sellers/account/balance/info", {"locale": "ru"})
        c = raw if isinstance(raw, dict) else {}
        data_node = c.get("data") or c.get("content") or c
        if not isinstance(data_node, dict):
            data_node = c

        # Структура: data.balance.amount или data.amount или content.amount_t_free
        bal_node = data_node.get("balance") if isinstance(data_node.get("balance"), dict) else None
        free = float(
            (bal_node.get("amount") if bal_node else None)
            or data_node.get("amount")
            or data_node.get("amount_t_free")
            or 0.0
        )
        lock = float(
            data_node.get("amount_in_hold")
            or data_node.get("amount_t_lock")
            or (bal_node.get("amount_in_hold") if bal_node else None)
            or 0.0
        )
        plus = float(data_node.get("amount_plus") or data_node.get("amount_t_plus") or 0.0)

        # Холд — пробуем отдельные эндпоинты
        if not lock:
            for path in ("/account/payments", "/account/balance", "/sellers/account/payments"):
                try:
                    pr = await self._v2_get(path)
                    pd = (pr.get("data") or pr) if isinstance(pr, dict) else {}
                    lock = float(pd.get("amount_in_hold") or pd.get("hold") or pd.get("lock") or 0.0)
                    if lock:
                        break
                except Exception:
                    pass

        return {"free": free, "lock": lock, "plus": plus}

    async def get_balance(self) -> float:
        """Вернуть доступный баланс продавца."""
        info = await self.get_balance_info()
        return info["free"]

    async def get_stats(self) -> Dict[str, Any]:
        """Статистика продаж с дашборда."""
        return await self._get("/seller-last-sales/stat", {"locale": "ru"})

    async def get_payment_schedule(self) -> Dict[str, Any]:
        """Расписание ближайших поступлений на баланс. Возвращает сырые данные или {}."""
        for path in (
            "/sellers/account/transactions",
            "/sellers/account/balance/transactions",
            "/sellers/finance",
            "/sellers/account/finance",
        ):
            try:
                data = await self._get(path, {"locale": "ru"})
                if isinstance(data, dict) and data.get("retval", -1) == 0:
                    return data
            except GGSellError:
                pass
        return {}

    async def get_buyer_email(self, invoice_id: int) -> Optional[str]:
        """Извлечь email покупателя для YouTube из деталей заказа."""
        info = await self.get_order_info(invoice_id)
        content = info.get("content", {}) if isinstance(info, dict) else {}
        # Структурированные options (Seller API v1)
        for opt in content.get("options", []):
            name = (opt.get("name") or "").lower()
            if "youtube" in name or "почт" in name or "email" in name.lower():
                return (opt.get("user_data") or "").strip() or None
        # selected_options как строки (API v1/v2)
        for s in content.get("selected_options", []):
            sl = str(s).lower()
            if "youtube" in sl or "почт" in sl or "email" in sl:
                if ": " in str(s):
                    return str(s).split(": ", 1)[1].strip() or None
        # buyer_info.email или buyer_email в корне
        buyer = content.get("buyer_info", {}) or {}
        return buyer.get("email") or info.get("buyer_email") or None

    # ── Orders ───────────────────────────────────────────────────────────────

    async def get_last_orders(self) -> List[Dict[str, Any]]:
        """Вернуть список последних продаж."""
        data = await self._get(
            "/seller-last-sales",
            {"locale": "ru", "seller_id": self.seller_id},
        )
        # Логируем первый заказ чтобы видеть реальные поля API
        if isinstance(data, list) and data:
            logger.debug(f"GGSell last-sales[0] keys: {list(data[0].keys())}")
            logger.debug(f"GGSell last-sales[0] sample: {data[0]}")
        # ответ может быть списком или {items: [...], data: [...]}
        if isinstance(data, list):
            return data
        for field in ("items", "data", "sales", "orders"):
            if field in data and isinstance(data[field], list):
                return data[field]
        logger.debug(f"GGSell last-orders raw: {data}")
        return []

    async def get_order_info(self, invoice_id: int) -> Dict[str, Any]:
        """Подробная информация о заказе (Seller API v1)."""
        data = await self._get(f"/purchase/info/{invoice_id}", {"locale": "ru"})
        content = data.get("content") if isinstance(data, dict) else None
        if isinstance(content, dict):
            logger.debug(f"GGSell order_info #{invoice_id} content keys: {list(content.keys())}")
            logger.debug(f"GGSell order_info #{invoice_id} content: {content}")
        return data

    async def get_order_info_v2(self, invoice_id: int) -> Dict[str, Any]:
        """Детали заказа через публичный API V1 (/api/v1/orders/{id}).

        Возвращает dict с полями: selected_options, buyer_email,
        seller_reward_amount, amount, unique_code, status, ...
        """
        try:
            resp = await self._client.get(
                f"https://seller.ggsel.com/api/v1/orders/{invoice_id}",
                headers={"Authorization": self.api_key, "Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("data") or {}
                logger.debug(f"GGSell order_v2 #{invoice_id} keys: {list(result.keys())}")
                return result
        except Exception as exc:
            logger.debug(f"GGSell order_v2 #{invoice_id} failed: {exc}")
        return {}

    # ── Chats ────────────────────────────────────────────────────────────────

    async def get_chats(self, filter_new: bool = False) -> List[Dict[str, Any]]:
        """Список чатов с покупателями."""
        params: Dict[str, Any] = {}
        if filter_new:
            params["filter_new"] = 1
        data = await self._get("/debates/v2/chats", params)
        if isinstance(data, list):
            return data
        for field in ("items", "data", "chats"):
            if field in data and isinstance(data[field], list):
                return data[field]
        return []

    async def get_messages(self, order_id: int, id_from: int = 0) -> List[Dict[str, Any]]:
        """Сообщения чата по ID заказа."""
        data = await self._get("/debates/v2", {"id_i": order_id, "id_from": id_from})
        if isinstance(data, list):
            return data
        for field in ("items", "data", "messages"):
            if field in data and isinstance(data[field], list):
                return data[field]
        return []

    async def send_message(self, order_id: int, message: str) -> bool:
        """Отправить сообщение покупателю по ID заказа."""
        try:
            data = await self._post(
                "/debates/v2",
                params={"id_i": order_id},
                json_body={"message": message},
            )
            logger.info(f"GGSell: сообщение отправлено → заказ #{order_id}")
            logger.debug(f"GGSell send_message response: {data}")
            return True
        except GGSellError as exc:
            logger.error(f"GGSell: ошибка отправки сообщения в #{order_id}: {exc}")
            return False

    # ── Reviews ──────────────────────────────────────────────────────────────

    async def get_reviews(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Список отзывов покупателей. Эндпоинт: GET /api_sellers/api/reviews"""
        data = await self._get("/reviews", {"locale": "ru", "limit": limit})
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # API возвращает {retval, retdesc, totalPages, totalItems, totalGood, totalBad, reviews}
            v = data.get("reviews")
            if isinstance(v, list):
                return v
            for field in ("content", "items", "data", "feedbacks"):
                v = data.get(field)
                if isinstance(v, list):
                    return v
        return []

    async def get_offer_detail(self, offer_id: int) -> Dict[str, Any]:
        """Детали оффера (включая status) через v2 API."""
        try:
            data = await self._v2_get(f"/offers/{offer_id}")
            if isinstance(data, dict) and "id" in data:
                return data
        except Exception as exc:
            logger.debug(f"GGSell get_offer_detail {offer_id}: {exc}")
        return {}

    async def get_orders_v1(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Заказы из v1 API (rich format: review_score, offer_id, buyer_email, usdt)."""
        try:
            resp = await self._client.get(
                "https://seller.ggsel.com/api/v1/orders/",
                headers={"Authorization": self.api_key, "Accept": "application/json"},
                params={"limit": limit, "page": 1},
            )
            if resp.status_code == 200:
                data = resp.json()
                v = data.get("data") or data.get("items")
                if isinstance(v, list):
                    return v
                if isinstance(data, list):
                    return data
        except Exception as exc:
            logger.debug(f"GGSell get_orders_v1: {exc}")
        return []

    async def get_offers(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Список офферов через v2 API (включает поле status в каждом элементе).

        status: 'active' | 'paused' | 'draft' | None (все)
        Ответ: {"data": [...list_offer_object...], "pagination": {...}}
        """
        try:
            params: Dict[str, Any] = {"limit": 100}
            if status:
                params["status"] = status
            data = await self._v2_get("/offers", params)
            v = data.get("data")
            if isinstance(v, list):
                return v
        except Exception as exc:
            logger.debug(f"GGSell get_offers v2: {exc}")
        return []

    async def set_offer_status(self, offer_id: int, status: str) -> bool:
        """Изменить статус оффера через v2 batch API: 'active' или 'paused'."""
        try:
            if status == "active":
                path = "/offers/batch_activate"
            else:
                path = "/offers/batch_pause"
            data = await self._v2_post(path, {"offer_ids": [offer_id]})
            return bool(data.get("success", True))
        except Exception as exc:
            logger.debug(f"GGSell set_offer_status v2: {exc}")
        return False

    async def get_promo_codes(self) -> List[Dict[str, Any]]:
        """Список промокодов продавца. Эндпоинт: GET /promo-codes"""
        try:
            data = await self._get("/promo-codes", {"locale": "ru", "limit": 50})
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                v = data.get("data") or data.get("items") or data.get("promo_codes")
                if isinstance(v, list):
                    return v
        except Exception as exc:
            logger.debug(f"GGSell get_promo_codes: {exc}")
        return []

    async def get_order_review(self, invoice_id: int) -> Optional[Dict[str, Any]]:
        """Отзыв на конкретный заказ; None если отзыва нет.
        Ищет в общем списке отзывов, фильтруя по invoice_id."""
        try:
            reviews = await self.get_reviews(limit=200)
            for r in reviews:
                rid = int(r.get("invoice_id") or r.get("id_i") or r.get("order_id") or 0)
                if rid == invoice_id:
                    return r
        except Exception as exc:
            logger.debug(f"GGSell get_order_review #{invoice_id}: {exc}")
        return None
