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
from pathlib import Path
from typing import Awaitable, Callable, Optional, Set

from loguru import logger

from .client import GGSellClient, GGSellError

_DATA = Path(__file__).resolve().parent.parent / "data"
_ORDERS_FILE = _DATA / "ggsel_orders.json"

POLL_INTERVAL = 60.0  # секунды между опросами

# Обрабатываем только заказы YouTube Premium
YOUTUBE_PREMIUM_PRODUCT_ID = 102276416

# Очередь уведомлений для TG-бота (thread-safe)
# Элементы: {"type": "new_order", "invoice_id": int, "order": dict}
notify_queue: _queue.SimpleQueue = _queue.SimpleQueue()

# Сообщение покупателю при получении ссылки
MSG_TEMPLATE = (
    "Спасибо за покупку! Ваша ссылка:\n\n"
    "{link}\n\n"
    "Перейдите по ссылке и примите приглашение в Family-план YouTube Premium."
)

# Сообщение если ссылка ещё готовится
MSG_WAIT = (
    "Ваш заказ принят! Ссылка будет отправлена в течение нескольких минут. "
    "Пожалуйста, ожидайте."
)


# ── Хранение обработанных заказов ────────────────────────────────────────────

def _load_processed() -> Set[int]:
    try:
        raw = json.loads(_ORDERS_FILE.read_text(encoding="utf-8"))
        return set(int(x) for x in raw.get("processed", []))
    except Exception:
        return set()


def _save_processed(ids: Set[int]) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    _ORDERS_FILE.write_text(
        json.dumps({"processed": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Пул ссылок (если накоплены заранее) ──────────────────────────────────────

_LINKS_FILE = _DATA / "ggsel_links.json"


def _pop_link() -> Optional[str]:
    """Взять одну ссылку из пула и удалить её оттуда."""
    try:
        raw = json.loads(_LINKS_FILE.read_text(encoding="utf-8"))
        links: list = raw.get("links", [])
        if not links:
            return None
        link = links.pop(0)
        _LINKS_FILE.write_text(
            json.dumps({"links": links}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return link
    except Exception:
        return None


def add_link_to_pool(link: str) -> None:
    """Добавить ссылку в пул (вызывается из автоматизации после успешного создания аккаунта)."""
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(_LINKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            raw = {"links": []}
        raw.setdefault("links", []).append(link)
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

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        processed = _load_processed()
        logger.info(
            f"GGSell монитор запущен "
            f"(интервал={self.poll_interval:.0f}с, обработано={len(processed)} заказов)"
        )

        while self._running:
            try:
                await self._tick(processed)
            except GGSellError as exc:
                logger.warning(f"GGSell API: {exc}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"GGSell монитор: {exc}")

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

        logger.info("GGSell монитор остановлен")

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
            msg = MSG_TEMPLATE.format(link=link)
            await self.client.send_message(invoice_id, msg)
            logger.success(f"GGSell #{invoice_id}: ссылка из пула отправлена → {link}")
            return

        # 2. Нет ссылки в пуле — сообщаем покупателю что готовим
        await self.client.send_message(invoice_id, MSG_WAIT)
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
                msg = MSG_TEMPLATE.format(link=link)
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
        finally:
            loop.close()

    t = threading.Thread(target=_thread_main, daemon=True, name="ggsel-monitor")
    t.start()
    logger.info(f"GGSell монитор запущен в фоне (seller_id={seller_id})")
