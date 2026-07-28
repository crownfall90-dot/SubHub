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
import threading
import time
from datetime import datetime
from pathlib import Path
from paths import ROOT
from typing import Awaitable, Callable, Optional, Set

from loguru import logger

from .client import GGSellClient, GGSellError

_DATA = ROOT / "data"
_ORDERS_FILE      = _DATA / "ggsel_orders.json"
_SEEN_MSGS_FILE   = _DATA / "ggsel_seen_msgs.json"
_TEMPLATES_FILE   = _DATA / "ggsel_templates.json"
_SEEN_REVIEWS_FILE = _DATA / "ggsel_seen_reviews.json"
_UNREAD_FILE       = _DATA / "ggsel_unread.json"  # invoice_id(str) -> непрочитанные сообщения покупателя

POLL_INTERVAL        = 15.0  # секунды между проверкой заказов
MSG_POLL_INTERVAL    =  8.0  # секунды между проверкой сообщений (компромисс отклик/нагрузка API)
REVIEW_POLL_INTERVAL = 120.0 # секунды между проверкой отзывов

# Обрабатываем только заказы YouTube Premium
YOUTUBE_PREMIUM_PRODUCT_ID = 102276416

# Очередь уведомлений для TG-бота (thread-safe)
# Элементы: {"type": "new_order", "invoice_id": int, "order": dict}
notify_queue: _queue.SimpleQueue = _queue.SimpleQueue()
_STATE_LOCK = threading.RLock()


def _atomic_json_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def emit_ggs_notify(item: dict) -> None:
    """Положить событие GGSell в очередь TG-бота."""
    notify_queue.put(item)

# Сообщения, отправленные НАМИ (продавцом/ботом) — чтобы монитор (и консоль) не принял
# их за сообщения покупателя. Хранит (invoice_id, нормализованный_текст, время).
# Персистентно на диске — иначе после перезапуска консоли/бота (память деки теряется)
# все ранее отправленные нами сообщения (например автоприветствие при заказе,
# просмотренное спустя часы) снова показывались бы как «от покупателя».
import collections as _collections
_SENT_MSGS_FILE = _DATA / "ggsel_sent_msgs.json"
_recent_sent: "_collections.deque" = _collections.deque(maxlen=400)


def _norm_msg(text: str) -> str:
    return " ".join(str(text or "").split())[:200].lower()


def _load_recent_sent() -> None:
    try:
        raw = json.loads(_SENT_MSGS_FILE.read_text(encoding="utf-8"))
        for item in raw.get("sent", []):
            _recent_sent.append((int(item[0]), str(item[1]), float(item[2])))
    except Exception:
        pass


def _save_recent_sent() -> None:
    with _STATE_LOCK:
        try:
            _atomic_json_write(_SENT_MSGS_FILE, {"sent": list(_recent_sent)})
        except Exception:
            pass


_load_recent_sent()


def record_sent_message(invoice_id, text: str) -> None:
    """Запомнить отправленное продавцом сообщение (фильтр своих сообщений)."""
    try:
        _recent_sent.append((int(invoice_id), _norm_msg(text), time.time()))
        _save_recent_sent()
    except Exception:
        pass


# max_age практически бессрочный (deque и так ограничена 400 записями) — раньше
# 1800с (30 мин) приводили к тому, что уже спустя полчаса собственное сообщение
# (например автоприветствие) переставало распознаваться и подписывалось «покупатель».
def is_own_sent(invoice_id, text: str, max_age: float = 60 * 60 * 24 * 90) -> bool:
    """True, если сообщение совпадает с недавно отправленным нами по тому же заказу."""
    try:
        inv = int(invoice_id)
        norm = _norm_msg(text)
        if not norm:
            return False
        now = time.time()
        for _inv, _norm, _ts in list(_recent_sent):
            if _inv == inv and (now - _ts) <= max_age and _norm == norm:
                return True
    except Exception:
        pass
    return False

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

# Промокод за 5-звёздочный отзыв
REVIEW_PROMO_CODE = "ZAPROMO5"

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

# Приветствие при новом заказе — отправляется покупателю сразу
MSG_GREETING = (
    "👋 Привет! Продавец уже получил сообщение о новом заказе.\n\n"
    "⚠️ Обязательно перед выполнением заказа:\n\n"
    "❗️Ознакомься с названием и описанием товара, прочитай внимательно!\n"
    "❗️Убедись, что твоя почта gmail, у тебя нет активной подписки и ты готов "
    "активировать подписку в течение 2-3 часов после выдачи ссылки.\n\n"
    "⏰ Онлайн: 9:00–02:00\n"
    "🙏 Продавец может выполнить заказ от 15 минут до 120 минут."
)

# ── Шаблоны для заказов DeepSeek (пополнение API-баланса) ────────────────────

DS_MSG_ASK_CREDS = (
    "👋 Здравствуйте! Для пополнения баланса DeepSeek API мне нужны данные "
    "вашего аккаунта platform.deepseek.com.\n\n"
    "Отправьте, пожалуйста, email и пароль ОДНОЙ строкой через пробел:\n"
    "email@example.com вашпароль\n\n"
    "Или двумя отдельными сообщениями: сначала email, затем пароль."
)

DS_MSG_ASK_PASSWORD = (
    "Email получил ✅ Теперь отправьте пароль отдельным сообщением."
)

DS_MSG_PROCESSING = (
    "Данные получил ✅ Выполняю пополнение на {amount}$ — обычно занимает "
    "5–10 минут. Пожалуйста, не меняйте пароль до завершения."
)

DS_MSG_DONE = (
    "Готово! ✅ Баланс DeepSeek API пополнен на {amount}$.\n"
    "Текущий баланс: {balance}$ — проверить можно на "
    "https://platform.deepseek.com/usage\n\n"
    "Буду очень благодарен за ваш отзыв о сервисе 🙌"
)

DS_MSG_FAIL_CREDS = (
    "Не получилось войти в аккаунт с этими данными ❌\n"
    "Проверьте email и пароль и отправьте их ещё раз одной строкой через пробел."
)

DS_MSG_DELAY = (
    "Спасибо! Продавцу потребуется немного больше времени на выполнение заказа — "
    "напишу, как только всё будет готово 🙏"
)

# Фиксированные куски наших шаблонов — по ним узнаём свои сообщения даже если
# GGSell API не проставляет ни одного из полей is_seller/sender/type/role/... и
# is_own_sent не сработал (сообщение отправлено давно или через сайт GGSell,
# а не через SubHub). Берём первые ~30 символов — этого достаточно как отпечатка
# и не ломается на {link}/{amount}-подстановках.
_OWN_TEMPLATE_SNIPPETS = tuple(
    _norm_msg(t)[:30] for t in (
        MSG_TEMPLATE, MSG_REVIEW_PROMO, MSG_WAIT, MSG_GREETING,
        DS_MSG_ASK_CREDS, DS_MSG_ASK_PASSWORD, DS_MSG_PROCESSING,
        DS_MSG_DONE, DS_MSG_FAIL_CREDS, DS_MSG_DELAY,
    ) if t.strip()
)


def direction_from_flags(msg: dict):
    """Направление сообщения по служебным полям GGSell: True — наше,
    False — покупателя, None — не берёмся судить.

    API не отдаёт ни одного из полей is_seller/sender/role, зато отдаёт
    `buyer`/`seller`/`date_seen`. Замер на 37 сообщениях двух чатов
    (2026-07-28): НАШИ сообщения — `buyer=0, seller=0, date_seen=null`,
    сообщения покупателя — `buyer=1, seller=1` с заполненным `date_seen`.
    Совпало на всех, включая наши шаблонные автоответы. Смешанных комбинаций
    (1,0)/(0,1) в выборке не было — на них не гадаем, пусть решают прежние
    эвристики: ошибка «наше» вместо «покупателя» глушит живой вопрос клиента,
    это дороже лишнего пинга.
    """
    b, s = msg.get("buyer"), msg.get("seller")
    if b is None or s is None:
        return None
    try:
        b, s = int(b), int(s)
    except (TypeError, ValueError):
        return None
    if b == 1 and s == 1:
        return False
    if b == 0 and s == 0 and not msg.get("date_seen"):
        return True
    return None


def classify_is_seller(invoice_id, msg: dict) -> bool:
    """Определить, что сообщение чата отправлено НАМИ (продавцом), а не покупателем.

    Единая проверка для консоли (menu.py), монитора и Telegram-бота — раньше
    у каждого была своя урезанная версия, из-за чего в консоли старые собственные
    сообщения (например автоприветствие) подписывались «Покупатель».
    """
    text = str(msg.get("text") or msg.get("message") or msg.get("body") or "")
    norm = _norm_msg(text)
    known_ours = bool(
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
        or (text and is_own_sent(invoice_id, text))
        or (norm and any(norm.startswith(s) for s in _OWN_TEMPLATE_SNIPPETS if s))
    )
    if known_ours:
        return True
    # Наши ручные ответы с сайта GGSell не попадают ни в is_own_sent, ни в
    # шаблоны — они и подписывались «от покупателя». Решают служебные флаги.
    by_flags = direction_from_flags(msg)
    return by_flags if by_flags is not None else False


# ── Хранение и загрузка шаблонов сообщений ───────────────────────────────────

def get_template(name: str) -> str:
    """Загрузить шаблон из файла; если нет — вернуть встроенный по умолчанию."""
    defaults = {"msg_template": MSG_TEMPLATE, "msg_wait": MSG_WAIT,
                "msg_review_promo": MSG_REVIEW_PROMO, "msg_greeting": MSG_GREETING,
                "ds_ask_creds": DS_MSG_ASK_CREDS, "ds_ask_password": DS_MSG_ASK_PASSWORD,
                "ds_processing": DS_MSG_PROCESSING, "ds_done": DS_MSG_DONE,
                "ds_fail_creds": DS_MSG_FAIL_CREDS, "ds_delay": DS_MSG_DELAY}
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
    with _STATE_LOCK:
        try:
            try:
                raw = json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            raw[name] = text
            _atomic_json_write(_TEMPLATES_FILE, raw)
        except Exception as exc:
            logger.warning(f"GGSell: не удалось сохранить шаблон {name}: {exc}")


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
    with _STATE_LOCK:
        _atomic_json_write(_SEEN_MSGS_FILE, seen)


def _save_processed(ids: Set[int]) -> None:
    with _STATE_LOCK:
        ids = set(ids) | _load_processed()
        _atomic_json_write(_ORDERS_FILE, {"processed": sorted(ids)})


def _load_seen_reviews() -> Set[str]:
    """Загрузить множество виденных review-ключей (invoice_id:review_id)."""
    try:
        raw = json.loads(_SEEN_REVIEWS_FILE.read_text(encoding="utf-8"))
        return set(raw.get("seen", []))
    except Exception:
        return set()


def _save_seen_reviews(seen: Set[str]) -> None:
    with _STATE_LOCK:
        _atomic_json_write(_SEEN_REVIEWS_FILE, {"seen": sorted(seen)})


# ── Непрочитанные сообщения покупателей (для консоли: подсветка + сортировка) ─

def _load_unread() -> dict:
    try:
        return json.loads(_UNREAD_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_unread(unread: dict) -> None:
    with _STATE_LOCK:
        _atomic_json_write(_UNREAD_FILE, unread)


def get_unread_counts() -> dict:
    """{invoice_id: количество непрочитанных сообщений покупателя} для экрана консоли."""
    raw = _load_unread()
    out = {}
    for k, v in raw.items():
        try:
            n = int(v)
            if n > 0:
                out[int(k)] = n
        except Exception:
            pass
    return out


def mark_order_read(invoice_id) -> None:
    """Сбросить счётчик непрочитанных для заказа — вызывается при открытии заказа в консоли."""
    try:
        inv_key = str(int(invoice_id))
    except Exception:
        return
    unread = _load_unread()
    if unread.get(inv_key):
        unread[inv_key] = 0
        _save_unread(unread)
    if _monitor_instance is not None:
        _monitor_instance._unread[inv_key] = 0


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
        self._unread: dict       = {}

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        processed = _load_processed()
        self._seen_msgs    = _load_seen_msgs()
        self._seen_reviews = _load_seen_reviews()
        self._unread        = _load_unread()
        try:
            from . import deepseek_orders as _ds
            _ds.sweep_stale()
        except Exception:
            pass
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
                _emsg = str(exc)
                if any(c in _emsg for c in ("502", "503", "504", "429")):
                    logger.debug(f"GGSell API: {_emsg} (временная ошибка сервера)")
                else:
                    logger.warning(f"GGSell API: {_emsg}")
            except asyncio.CancelledError:
                break
            except RuntimeError as exc:
                if "shutdown" in str(exc).lower() or "closed" in str(exc).lower():
                    break
                logger.error(f"GGSell монитор: {type(exc).__name__}: {exc}")
            except Exception as exc:
                # str(exc) часто пуст для asyncio.TimeoutError/httpx-обрывов —
                # без имени типа в логе оставалось "GGSell монитор:" без деталей.
                logger.error(f"GGSell монитор: {type(exc).__name__}: {exc}")

            try:
                await asyncio.sleep(MSG_POLL_INTERVAL)
            except (asyncio.CancelledError, RuntimeError):
                break

        logger.info("GGSell монитор остановлен")

    async def _check_new_messages(self, initialized: bool) -> None:
        """Проверить новые входящие сообщения от покупателей."""
        try:
            # ВСЕГДА берём все чаты. filter_new ненадёжен: GGSell может не флагать
            # чат как «новый» при сообщении покупателя — и сообщение терялось.
            # Новизну отслеживаем сами по last_id каждого чата.
            chats = await self.client.get_chats(filter_new=False)
        except Exception as exc:
            logger.debug(f"GGSell chats: {exc}")
            return

        if chats and not initialized:
            logger.trace(f"GGSell chat[0] keys: {list(chats[0].keys())}")

        seen = self._seen_msgs
        # Ключи, которые уже были в сохранённом состоянии ДО этого прохода.
        # Нужны, чтобы после перезапуска бота не «проглатывать» новые сообщения
        # в уже известных чатах (раньше первый проход терял их без уведомления).
        _orig_keys = set(seen.keys())
        changed = False
        _unread_changed = False

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

            if not initialized and seen_key not in _orig_keys:
                # Первый проход + чат БЕЗ сохранённого состояния (бэклог при самом
                # первом запуске) — инициализируем без уведомления. Уже известные
                # чаты обрабатываем нормально даже на первом проходе после рестарта.
                seen[seen_key] = max(max_id, last_id)
                changed = True
                continue

            # Находим новые сообщения от покупателя
            for msg in messages:
                msg_id = int(msg.get("id") or msg.get("message_id") or 0)
                if msg_id <= last_id:
                    continue
                logger.debug(
                    f"GGSell msg #{msg_id} в заказе #{id_i}: "
                    f"поля={list(msg.keys())}"
                )
                # Системные сообщения поддержки GGSell — пропускаем.
                # (По order_id НЕ фильтруем: у обычных сообщений его может не быть.)
                if msg.get("system"):
                    continue
                _mtext = str(msg.get("text") or msg.get("message") or msg.get("body") or "")
                # Определяем, чьё сообщение: наше (продавца/бота) или покупателя.
                # Фильтр read убран: повторы исключаются по last_id.
                is_seller = classify_is_seller(id_i, msg)
                # Уведомляем о ВСЕХ сообщениях (и своих, и покупателя), но с флагом —
                # бот покажет «от вас» или «от покупателя».
                buyer_email = ((msg.get("author") or {}).get("email")
                               or chat.get("email") or "")
                emit_ggs_notify({
                    "type": "new_message",
                    "invoice_id": id_i,
                    "message": msg,
                    "chat": chat,
                    "buyer_email": buyer_email,
                    "is_seller": is_seller,
                })
                logger.info(
                    f"GGSell: новое сообщение ({'продавец' if is_seller else 'покупатель'}) "
                    f"в заказе #{id_i}")
                # Сообщение от покупателя — считаем непрочитанным до открытия
                # заказа в консоли (см. get_unread_counts / mark_order_read).
                if not is_seller:
                    self._unread[seen_key] = int(self._unread.get(seen_key) or 0) + 1
                    _unread_changed = True
                # Заказ DeepSeek ждёт данные аккаунта — передаём сообщение покупателя
                if not is_seller:
                    try:
                        from . import deepseek_orders as _ds
                        if _ds.has_pending(id_i):
                            asyncio.create_task(
                                _ds.on_buyer_message(self.client, id_i, _mtext))
                    except Exception as exc:
                        logger.debug(f"DeepSeek msg-hook #{id_i}: {exc}")

            if max_id > last_id:
                seen[seen_key] = max_id
                changed = True

        if changed:
            _save_seen_msgs(seen)
        if _unread_changed:
            _save_unread(self._unread)

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
                emit_ggs_notify({
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
            emit_ggs_notify({
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

            # Заказы DeepSeek (пополнение API) — отдельный авто-флоу
            try:
                from . import deepseek_orders as _ds
            except Exception:
                _ds = None
            if _ds is not None and _ds.is_deepseek_order(order):
                logger.info(
                    f"GGSell: новый заказ DeepSeek #{invoice_id} "
                    f"(продукт: {(order.get('product') or {}).get('name', '?')})"
                )
                emit_ggs_notify({"type": "new_order", "invoice_id": invoice_id, "order": order})
                asyncio.create_task(_ds.handle_new_order(self.client, invoice_id, order))
                processed.add(invoice_id)
                _save_processed(processed)
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
            emit_ggs_notify({"type": "new_order", "invoice_id": invoice_id, "order": order})

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

        # 1. Сообщаем покупателю что готовим ссылку
        await self.client.send_message(invoice_id, get_template("msg_wait"))
        logger.info(f"GGSell #{invoice_id}: сообщение об ожидании отправлено")

        link = None

        # 2. Вызываем колбэк для генерации ссылки (если задан)
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


def stop_monitor() -> None:
    """Останавливает фоновый GGSell-монитор."""
    global _monitor_instance
    if _monitor_instance is not None:
        _monitor_instance.stop()
        _monitor_instance = None


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
        except Exception as exc:
            logger.error(f"GGSell монитор остановлен с ошибкой: {exc}")
        finally:
            try:
                loop.run_until_complete(client.close())
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


def is_monitor_running() -> bool:
    """True, если поток ggsel-monitor жив."""
    import threading
    return any(t.name == "ggsel-monitor" and t.is_alive() for t in threading.enumerate())
