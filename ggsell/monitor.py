"""
GGSell order monitor — следит за новыми заказами и доставляет ссылки покупателям.

Логика:
  1. Каждые POLL_INTERVAL секунд запрашиваем список продаж.
  2. Новые заказы (invoice_id не в processed-файле) передаём в on_new_order.
  3. on_new_order возвращает ссылку (str) или None.
  4. Если ссылка получена — отправляем в чат GGSell.
  5. invoice_id записывается в data/ggsel_orders.json, чтобы не обрабатывать повторно.
"""

import asyncio
import json
import queue as _queue
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional, Set

from loguru import logger

from .client import GGSellClient, GGSellError

_DATA = Path(__file__).resolve().parent.parent / "data"
_ORDERS_FILE      = _DATA / "ggsel_orders.json"
_SEEN_MSGS_FILE   = _DATA / "ggsel_seen_msgs.json"
_TEMPLATES_FILE   = _DATA / "ggsel_templates.json"
_SEEN_REVIEWS_FILE = _DATA / "ggsel_seen_reviews.json"

POLL_INTERVAL        = 60.0  # секунды между проверкой заказов
MSG_POLL_INTERVAL    = 15.0  # секунды между проверкой сообщений
REVIEW_POLL_INTERVAL = 120.0 # секунды между проверкой отзывов

# Обрабатываем только заказы YouTube Premium
YOUTUBE_PREMIUM_PRODUCT_ID = 102276416

# Очередь уведомлений для TG-бота (thread-safe)
# Элементы: {"type": "new_order", "invoice_id": int, "order": dict}
notify_queue: _queue.SimpleQueue = _queue.SimpleQueue()

# Сообщение покупателю при получении ссылки
MSG_TEMPLATE = (
    "Ссылка на активацию подписки отправлена ✅\n\n"
    "{link}\n\n"
    "Пожалуйста, активируйте её в течение 1–2 часов на тот аккаунт (почту), "
    "который вы указали в чате.\n\n"
    "Инструкция по активации:\n\n"
    "1. Перейдите по ссылке\n"
    "2. Выберите нужную почту\n"
    "3. Подтвердите активацию\n\n"
    "Важно! Для вашей безопасности и на случай технических вопросов — пожалуйста, "
    "запишите процесс активации на видео (запись экрана). Это поможет мне оперативно "
    "решить любые проблемы и, при необходимости, сделать замену.\n\n"
    "После успешной активации буду очень благодарен, если вы оставите свой драгоценный "
    "отзыв о сервисе — это очень поможет развитию и качеству работы 🙌\n\n"
    "🎁 Бонус: После хорошего отзыва я выдам вам промокод на скидку 5% на следующую покупку.\n\n"
    "Спасибо за доверие и сотрудничество! Буду на связи."
)

# Сообщение с промокодом после 5-звёздочного отзыва
MSG_REVIEW_PROMO = (
    "🎉 Огромное спасибо за ваш отзыв!\n\n"
    "Как и обещал — дарю вам промокод на скидку 5% на следующую покупку:\n\n"
    "🎁 *{promo_code}*\n\n"
    "Введите его при оформлении заказа в поле «Промокод».\n\n"
    "Буду рад видеть вас снова! 🙌"
)

# Сообщение если ссылка ещё готовится
MSG_WAIT = (
    "Ваш заказ принят! Ссылка на активацию будет отправлена в течение нескольких минут. "
    "Пожалуйста, ожидайте."
)


# ── Хранение и загрузка шаблонов сообщений ───────────────────────────────────

def get_template(name: str) -> str:
    """Загрузить шаблон из файла; если нет — вернуть встроенный по умолчанию."""
    defaults = {"msg_template": MSG_TEMPLATE, "msg_wait": MSG_WAIT,
                "msg_review_promo": MSG_REVIEW_PROMO}
    try:
        raw = json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
        val = raw.get(name, "").strip()
        if val:
            return val
    except Exception:
        pass
    return defaults.get(name, "")


def save_template(name: str, text: str) -> None:
    """Сохранить шаблон в файл."""
    _DATA.mkdir(parents=True, exist_ok=True)
    try:
        try:
            raw = json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        raw[name] = text
        _TEMPLATES_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Хранение обработанных заказов ────────────────────────────────────────────

def _load_processed() -> Set[int]:
    try:
        raw = json.loads(_ORDERS_FILE.read_text(encoding="utf-8"))
        return set(int(x) for x in raw.get("processed", []))
    except Exception:
        return set()


def _load_seen_msgs() -> dict:
    try:
        return json.loads(_SEEN_MSGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_seen_msgs(seen: dict) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _SEEN_MSGS_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _save_processed(ids: Set[int]) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _ORDERS_FILE.write_text(
        json.dumps({"processed": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_seen_reviews() -> Set[str]:
    """Загрузить множество виденных review-ключей (invoice_id:review_id)."""
    try:
        raw = json.loads(_SEEN_REVIEWS_FILE.read_text(encoding="utf-8"))
        return set(raw.get("seen", []))
    except Exception:
        return set()


def _save_seen_reviews(seen: Set[str]) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _SEEN_REVIEWS_FILE.write_text(
        json.dumps({"seen": sorted(seen)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Пул ссылок (если накоплены заранее) ──────────────────────────────────────

_LINKS_FILE = _DATA / "ggsel_links.json"


def _pool_entry_url(entry) -> str:
    return entry["url"] if isinstance(entry, dict) else entry


def _pop_link() -> Optional[str]:
    """Взять одну ссылку из пула и удалить её оттуда."""
    try:
        raw = json.loads(_LINKS_FILE.read_text(encoding="utf-8"))
        links: list = raw.get("links", [])
        if not links:
            return None
        entry = links.pop(0)
        _LINKS_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _pool_entry_url(entry)
    except Exception:
        return None


def add_link_to_pool(link: str, profile_path: str = "") -> None:
    """Добавить ссылку в пул. profile_path — путь к Chrome-профилю для авто-пометки «выдан»."""
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(_LINKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            raw = {"links": []}
        entry: dict = {"url": link, "added_at": datetime.now().isoformat(timespec="seconds")}
        if profile_path:
            entry["profile_path"] = str(profile_path)
        raw.setdefault("links", []).append(entry)
        if profile_path:
            raw.setdefault("profile_map", {})[link] = str(profile_path)
        _LINKS_FILE.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"GGSell: ссылка добавлена в пул ({len(raw['links'])} всего)")
    except Exception as exc:
        logger.error(f"GGSell: не удалось сохранить ссылку в пул: {exc}")


# ── Монитор ───────────────────────────────────────────────────────────────────

class GGSellMonitor:
    """
    Асинхронный монитор заказов GGSell.

    Параметры:
      client      — экземпляр GGSellClient
      on_new_order — async-колбэк (order_dict) -> Optional[str]
                     должен вернуть ссылку или None (если нужно время)
      poll_interval — интервал опроса в секундах (default 60)

    Использование:
      monitor = GGSellMonitor(client, my_callback)
      await monitor.run()          # блокирующий бесконечный цикл
      # или:
      asyncio.create_task(monitor.run())
    """

    def __init__(
        self,
        client: GGSellClient,
        on_new_order: Optional[Callable[[dict], Awaitable[Optional[str]]]] = None,
        poll_interval: float = POLL_INTERVAL,
        manual_confirm: bool = True,
    ) -> None:
        self.client = client
        self.on_new_order = on_new_order
        self.poll_interval = poll_interval
        self.manual_confirm = manual_confirm
        self._running = False
        self._seen_msgs: dict    = {}
        self._seen_reviews: set  = set()

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        processed = _load_processed()
        self._seen_msgs    = _load_seen_msgs()
        self._seen_reviews = _load_seen_reviews()
        _msgs_initialized    = False
        _reviews_initialized = False
        _last_order_check  = 0.0
        _last_review_check = 0.0

        logger.info(
            f"GGSell монитор запущен "
            f"(заказы={self.poll_interval:.0f}с, сообщения={MSG_POLL_INTERVAL:.0f}с, "
            f"отзывы={REVIEW_POLL_INTERVAL:.0f}с, "
            f"обработано={len(processed)} заказов)"
        )

        while self._running:
            now = time.monotonic()
            try:
                if now - _last_order_check >= self.poll_interval:
                    await self._tick(processed)
                    _last_order_check = time.monotonic()

                try:
                    await self._check_new_messages(_msgs_initialized)
                finally:
                    _msgs_initialized = True

                if now - _last_review_check >= REVIEW_POLL_INTERVAL:
                    try:
                        await self._check_new_reviews(_reviews_initialized)
                    finally:
                        _reviews_initialized = True
                    _last_review_check = time.monotonic()

            except GGSellError as exc:
                logger.warning(f"GGSell API: {exc}")
            except asyncio.CancelledError:
                break
            except RuntimeError as exc:
                if "shutdown" in str(exc).lower() or "closed" in str(exc).lower():
                    break
                logger.error(f"GGSell монитор: {exc}")
            except Exception as exc:
                logger.error(f"GGSell монитор: {exc}")

            try:
                await asyncio.sleep(MSG_POLL_INTERVAL)
            except (asyncio.CancelledError, RuntimeError):
                break

        logger.info("GGSell монитор остановлен")

    async def _check_new_messages(self, initialized: bool) -> None:
        """Проверить новые входящие сообщения от покупателей."""
        try:
            # На первом запуске все чаты (для инициализации last_id),
            # потом только с новой активностью через filter_new.
            # cnt_new не используем — GGSell сбрасывает его при get_chats(),
            # что делает его ненадёжным для отслеживания новых сообщений.
            chats = await self.client.get_chats(filter_new=initialized)
        except Exception as exc:
            logger.debug(f"GGSell chats: {exc}")
            return

        if chats and not initialized:
            logger.trace(f"GGSell chat[0] keys: {list(chats[0].keys())}")

        seen = self._seen_msgs
        changed = False

        for chat in chats:
            id_i = int(chat.get("id_i") or chat.get("invoice_id") or chat.get("id") or 0)
            if not id_i:
                continue

            seen_key = str(id_i)
            last_id  = int(seen.get(seen_key) or 0)

            try:
                messages = await self.client.get_messages(id_i, id_from=last_id)
            except Exception:
                continue

            if not messages:
                if seen_key not in seen:
                    seen[seen_key] = 0
                    changed = True
                continue

            msg_ids = [int(m.get("id") or m.get("message_id") or 0) for m in messages]
            max_id  = max(msg_ids) if msg_ids else 0

            if not initialized:
                # Первый запуск — запоминаем, уведомления не шлём
                seen[seen_key] = max(max_id, last_id)
                changed = True
                continue

            # Находим новые сообщения от покупателя
            for msg in messages:
                msg_id = int(msg.get("id") or msg.get("message_id") or 0)
                if msg_id <= last_id:
                    continue
                # Системные сообщения (поддержка GGSell, order_id=null) — пропускаем
                if msg.get("system") or msg.get("order_id") is None:
                    continue
                # Уже прочитанные продавцом — не уведомляем
                if msg.get("read"):
                    continue
                logger.debug(f"GGSell msg #{msg_id} в заказе #{id_i}: {msg}")
                # Сообщение от продавца (нашего бота) — не уведомляем
                is_seller = bool(
                    msg.get("is_current_user")
                    or msg.get("is_seller")
                    or msg.get("is_seller_msg")
                    or msg.get("sender") == "seller"
                    or msg.get("type") == "seller"
                    or msg.get("from_seller")
                    or msg.get("role") == "seller"
                    or msg.get("who") == "seller"
                    or msg.get("author_type") == "seller"
                    or msg.get("user_type") == "seller"
                    or msg.get("is_mine")
                    or int(msg.get("type_message") or msg.get("type_msg") or -1) == 1
                )
                if not is_seller:
                    buyer_email = (msg.get("author") or {}).get("email") or ""
                    notify_queue.put({
                        "type": "new_message",
                        "invoice_id": id_i,
                        "message": msg,
                        "chat": chat,
                        "buyer_email": buyer_email,
                    })
                    logger.info(f"GGSell: новое сообщение от покупателя {buyer_email!r} в заказе #{id_i}")

            if max_id > last_id:
                seen[seen_key] = max_id
                changed = True

        if changed:
            _save_seen_msgs(seen)

    # YOUTUBE_PREMIUM_PRODUCT_ID из константы бота (дублируем)
    _YT_GGSEL_ID = 102276416

    async def _check_new_reviews(self, initialized: bool) -> None:
        """Проверить новые отзывы покупателей: через orders v1 (надёжнее) + reviews API."""
        changed = False

        # ── orders v1: ищем заказы с новым review_score (только YouTube Premium) ──
        try:
            orders_v1 = await self.client.get_orders_v1(limit=30)
            for o in orders_v1:
                rv = o.get("review_score")
                if rv is None:
                    continue
                if int(o.get("offer_ggsel_id") or 0) != self._YT_GGSEL_ID:
                    continue
                invoice_id = int(o.get("id") or o.get("invoice_id") or 0)
                key = f"ord:{invoice_id}:{rv}"
                if not invoice_id or key in self._seen_reviews:
                    continue
                self._seen_reviews.add(key)
                if not initialized:
                    continue  # первый запуск — только запоминаем
                changed = True
                logger.info(f"GGSell: отзыв {rv}★ на заказ #{invoice_id} (orders v1)")
                notify_queue.put({
                    "type":       "new_review",
                    "invoice_id": invoice_id,
                    "review":     {
                        "rating":     int(rv),
                        "invoice_id": invoice_id,
                        "email":      o.get("buyer_email") or "",
                        "text":       "",
                    },
                })
        except Exception as exc:
            logger.debug(f"GGSell reviews via orders v1: {exc}")

        # ── reviews API: запасной источник ────────────────────────────────────────
        try:
            reviews = await self.client.get_reviews(limit=50)
        except Exception as exc:
            logger.debug(f"GGSell reviews poll: {exc}")
            if changed:
                _save_seen_reviews(self._seen_reviews)
            return

        if reviews and not initialized:
            logger.trace(f"GGSell review[0] keys: {list(reviews[0].keys())}")
            logger.trace(f"GGSell review[0] sample: {reviews[0]}")

        for r in (reviews or []):
            key = self._review_key(r)
            if not key or key in self._seen_reviews:
                continue
            self._seen_reviews.add(key)
            if not initialized:
                continue
            changed = True
            invoice_id = int(r.get("invoice_id") or r.get("id_i") or r.get("order_id") or 0)
            logger.info(f"GGSell: новый отзыв #{invoice_id if invoice_id else '?'} (reviews API)")
            notify_queue.put({
                "type":       "new_review",
                "invoice_id": invoice_id,
                "review":     r,
            })

        if changed:
            _save_seen_reviews(self._seen_reviews)

    @staticmethod
    def _review_key(r: dict) -> str:
        """Уникальный ключ отзыва для дедупликации."""
        rid = (r.get("id") or r.get("review_id") or r.get("feedback_id") or "")
        iid = (r.get("invoice_id") or r.get("id_i") or r.get("order_id") or "")
        if rid:
            return f"{iid}:{rid}"
        # Нет ID — используем хэш текста + дата
        text = str(r.get("text") or r.get("comment") or r.get("review") or "")
        date = str(r.get("date") or r.get("created_at") or r.get("date_add") or "")
        return f"{iid}:{text[:40]}:{date}" if (text or date) else ""

    async def _tick(self, processed: Set[int]) -> None:
        orders = await self.client.get_last_orders()
        for order in orders:
            invoice_id = int(order.get("invoice_id") or order.get("id") or 0)
            if not invoice_id or invoice_id in processed:
                continue

            # Проверяем product_id — обрабатываем только YouTube Premium
            product = order.get("product") or {}
            product_id = int(product.get("id") or 0)
            if product_id and product_id != YOUTUBE_PREMIUM_PRODUCT_ID:
                logger.debug(
                    f"GGSell: заказ #{invoice_id} пропущен (product_id={product_id}, не YouTube Premium)"
                )
                processed.add(invoice_id)
                _save_processed(processed)
                continue

            logger.info(
                f"GGSell: новый заказ YouTube Premium #{invoice_id} "
                f"(продукт: {product.get('name', '?')})"
            )

            # Уведомляем TG-бот через очередь (всегда)
            notify_queue.put({"type": "new_order", "invoice_id": invoice_id, "order": order})

            # В режиме manual_confirm бот сам управляет отправкой
            if not self.manual_confirm:
                await self._handle_order(invoice_id, order)

            processed.add(invoice_id)
            _save_processed(processed)

    async def _handle_order(self, invoice_id: int, order: dict) -> None:
        # Получаем email покупателя для YouTube из деталей заказа
        buyer_email: Optional[str] = None
        try:
            buyer_email = await self.client.get_buyer_email(invoice_id)
        except Exception as exc:
            logger.warning(f"GGSell #{invoice_id}: не удалось получить email покупателя: {exc}")

        logger.info(f"GGSell #{invoice_id}: email покупателя = {buyer_email!r}")

        # 1. Проверяем пул накопленных ссылок
        link = _pop_link()

        if link:
            msg = get_template("msg_template").format(link=link)
            await self.client.send_message(invoice_id, msg)
            logger.success(f"GGSell #{invoice_id}: ссылка из пула отправлена → {link}")
            return

        # 2. Нет ссылки в пуле — сообщаем покупателю что готовим
        await self.client.send_message(invoice_id, get_template("msg_wait"))
        logger.info(f"GGSell #{invoice_id}: пул пуст, сообщение об ожидании отправлено")

        # 3. Вызываем колбэк для генерации ссылки (если задан)
        if self.on_new_order:
            # Передаём обогащённый dict с email покупателя
            order_info = dict(order)
            order_info["invoice_id"] = invoice_id
            order_info["buyer_email"] = buyer_email
            try:
                link = await self.on_new_order(order_info)
            except Exception as exc:
                logger.error(f"GGSell #{invoice_id}: on_new_order ошибка: {exc}")
                link = None

            if link:
                msg = get_template("msg_template").format(link=link)
                await self.client.send_message(invoice_id, msg)
                logger.success(f"GGSell #{invoice_id}: ссылка от колбэка отправлена → {link}")
            else:
                logger.warning(
                    f"GGSell #{invoice_id}: колбэк не вернул ссылку — потребуется ручная доставка"
                )


# ── Запуск в фоновом daemon-потоке ───────────────────────────────────────────

_monitor_instance: Optional[GGSellMonitor] = None


def start_monitor(
    api_key: str,
    seller_id: int,
    on_new_order: Optional[Callable[[dict], Awaitable[Optional[str]]]] = None,
    poll_interval: float = POLL_INTERVAL,
    manual_confirm: bool = True,
) -> None:
    """Запустить GGSell-монитор в фоновом daemon-потоке.
    manual_confirm=True (по умолчанию): только эмитирует в notify_queue,
    отправкой управляет TG-бот.
    manual_confirm=False: авто-отправка из пула без подтверждения."""
    global _monitor_instance

    if not api_key or not seller_id:
        logger.debug("GGSell: api_key или seller_id не заданы — монитор не запущен")
        return

    import threading

    from .client import GGSellClient

    client = GGSellClient(api_key=api_key, seller_id=seller_id)
    _monitor_instance = GGSellMonitor(
        client=client,
        on_new_order=on_new_order,
        poll_interval=poll_interval,
        manual_confirm=manual_confirm,
    )

    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_monitor_instance.run())
        except Exception:
            pass
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()

    t = threading.Thread(target=_thread_main, daemon=True, name="ggsel-monitor")
    t.start()
    logger.info(f"GGSell монитор запущен в фоне (seller_id={seller_id})")
