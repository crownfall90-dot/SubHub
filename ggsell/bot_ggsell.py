"""GGSell Telegram bot handlers.

Весь код для работы GGSell в боте вынесен сюда из bot.py.
Используется через GGSellBotHandler — инициализируется в _poll() и получает
ссылки на общее состояние и вспомогательные функции.
"""

import asyncio
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

_DATA_DIR       = Path(__file__).resolve().parent.parent / "data"
_TEMPLATES_FILE = _DATA_DIR / "ggsel_templates.json"

YOUTUBE_PREMIUM_PRODUCT_ID = 102276416


class GGSellBotHandler:
    """Обработчик GGSell-команд в Telegram-боте."""

    def __init__(
        self,
        *,
        orders: dict,
        confirm: dict,
        done: dict,
        done_loaded: list,
        reply_mode: dict,
        pool_pick_pending: dict,
        done_links: dict,
        cli_holder: list,
        subs: set,
        edit_fn,
        send_fn,
        ack_fn,
        get_fn,
        set_fn,
        m_fn,
        http_client,
        tg_api_url: str,
        project_root: Path,
        webhook_url: str = "",
        record_sale_fn=None,
    ):
        self._record_sale_fn = record_sale_fn
        self.orders = orders
        self.confirm = confirm
        self._done = done
        self._done_loaded = done_loaded
        self.reply_mode = reply_mode
        self.pool_pick_pending = pool_pick_pending
        self._done_links        = done_links
        self._done_buyer_emails: dict = {}  # {invoice_id: buyer_email}
        self._confirm_profile: dict   = {}  # {invoice_id: profile_path} — какой профиль выдаётся
        self._cli = cli_holder
        self.subs = subs

        self._edit       = edit_fn
        self._send       = send_fn
        self._ack        = ack_fn
        self._get        = get_fn
        self._set        = set_fn
        self._m          = m_fn
        self._http       = http_client
        self._api        = tg_api_url
        self._root       = project_root
        self.webhook_url = webhook_url.rstrip("/")

        self.template_edit_mode: dict = {}  # cid → template_name
        self.card_order_mode:    dict = {}  # cid → True (ожидаем ввод порядка карт)
        self._auto_pending:      dict = {}  # invoice_id → order (ждём первого сообщения покупателя)
        self._hanging_prompted:  set  = set()  # invoice_id, по которым уже спросили выполнение
        self._fulfill_cancel:    set  = set()  # invoice_id, выполнение которых отменено
        self._greeted_sent:      dict = {}     # invoice_id → строка времени отправки приветствия
        self._buy_lock           = asyncio.Lock()  # одновременно только одна покупка

    # ── GGSell client ────────────────────────────────────────────────────────

    def get_client(self):
        if self._cli[0] is not None:
            return self._cli[0]
        try:
            from ggsell.client import GGSellClient
            sec = self._m("_read_secrets")().get("ggsel") or {}
            key = sec.get("api_key", "").strip()
            sid = int(sec.get("seller_id") or 0)
            if key and sid:
                self._cli[0] = GGSellClient(api_key=key, seller_id=sid)
        except Exception:
            pass
        return self._cli[0]

    # ── Выполненные заказы ───────────────────────────────────────────────────

    def get_done(self) -> dict:
        if not self._done_loaded[0]:
            try:
                f = _DATA_DIR / "ggsel_done.json"
                raw = json.loads(f.read_text(encoding="utf-8"))
                loaded = raw.get("done", {})
                self._done.update({int(k): v for k, v in loaded.items()})
                links_loaded = raw.get("links", {})
                self._done_links.update({int(k): v for k, v in links_loaded.items()})
                emails_loaded = raw.get("buyer_emails", {})
                self._done_buyer_emails.update({int(k): v for k, v in emails_loaded.items()})
            except Exception:
                pass
            self._done_loaded[0] = True
        return self._done

    def mark_done(self, invoice_id: int, link: str = "", profile_path: str = "") -> None:
        dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.get_done()[invoice_id] = dt_str
        if link:
            self._done_links[invoice_id] = link

        # Email покупателя из кэша заказов (или из ранее сохранённого)
        _cached = self.orders.get(invoice_id, {})
        buyer_email = ""
        if isinstance(_cached, dict):
            buyer_email = (
                _cached.get("buyer_email") or
                self.parse_order(_cached.get("order", {})).get("email", "")
            ) or ""
        if not buyer_email:
            buyer_email = self._done_buyer_emails.get(invoice_id, "")
        if buyer_email:
            self._done_buyer_emails[invoice_id] = buyer_email

        # Путь профиля: явно переданный, либо запомненный при подготовке к выдаче
        profile_path_str = str(profile_path or self._confirm_profile.get(invoice_id, "") or "")

        try:
            f = _DATA_DIR / "ggsel_done.json"
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                raw = {"done": {}}
            raw.setdefault("done", {})[str(invoice_id)] = dt_str
            if link:
                raw.setdefault("links", {})[str(invoice_id)] = link
            if not profile_path_str:
                # Если путь не передан — берём ранее сохранённый (не затираем пустым)
                profile_path_str = raw.get("profile_paths", {}).get(str(invoice_id), "")
            if profile_path_str:
                raw.setdefault("profile_paths", {})[str(invoice_id)] = profile_path_str
            if buyer_email:
                raw.setdefault("buyer_emails", {})[str(invoice_id)] = buyer_email
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        # Привязываем заказ к профилю в его .profile_meta.json — ссылка не потеряется
        if profile_path_str:
            self._bind_profile_to_order(profile_path_str, invoice_id, link, buyer_email)
        self._confirm_profile.pop(invoice_id, None)

        # Автоматически записываем продажу, если есть сумма выплаты
        if self._record_sale_fn and profile_path_str:
            try:
                _cached_ord = self.orders.get(invoice_id, {})
                _order_data = (_cached_ord.get("order", {}) if isinstance(_cached_ord, dict) else {})
                _parsed = self.parse_order(_order_data)
                _sell = _parsed.get("sum_sell")
                if _sell:
                    _months = self._order_months(_order_data) if _order_data else 3
                    _plan = "12m" if _months >= 12 else ("6m" if _months >= 6 else "3m")
                    _ph_m = re.search(r"\d{10}", Path(profile_path_str).name)
                    _phone = _ph_m.group() if _ph_m else ""
                    self._record_sale_fn(_phone, _plan, float(_sell))
            except Exception:
                pass

    def get_used(self) -> set:
        """Множество invoice_id помеченных как «использованные»."""
        try:
            raw = json.loads((_DATA_DIR / "ggsel_done.json").read_text(encoding="utf-8"))
            return set(int(k) for k in raw.get("used", {}).keys())
        except Exception:
            return set()

    def mark_used(self, invoice_id: int) -> str:
        """Помечает заказ использованным. Возвращает путь профиля для архивирования."""
        dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        profile_path_str = ""
        try:
            f = _DATA_DIR / "ggsel_done.json"
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            raw.setdefault("used", {})[str(invoice_id)] = dt_str
            profile_path_str = raw.get("profile_paths", {}).get(str(invoice_id), "")
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return profile_path_str

    def get_refunded(self) -> dict:
        """{invoice_id: 'YYYY-MM-DD HH:MM'} заказов, по которым сделан возврат на GGSell."""
        try:
            raw = json.loads((_DATA_DIR / "ggsel_done.json").read_text(encoding="utf-8"))
            return {int(k): v for k, v in raw.get("refunded", {}).items()}
        except Exception:
            return {}

    def mark_refunded(self, invoice_id: int, undo: bool = False) -> None:
        """Помечает заказ как возврат (или снимает пометку при undo=True).
        Возвратные заказы отсеиваются из списков невыданных, массовой и
        авто-выдачи."""
        try:
            f = _DATA_DIR / "ggsel_done.json"
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            if undo:
                raw.get("refunded", {}).pop(str(invoice_id), None)
            else:
                dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                raw.setdefault("refunded", {})[str(invoice_id)] = dt_str
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        # Возвратный заказ не должен ждать авто-выдачи
        if not undo:
            self._auto_pending.pop(invoice_id, None)

    async def bg_mark_used(self, cid: int, mid: int, invoice_id: int) -> None:
        """Помечает использованной и архивирует Chrome-профиль."""
        profile_path_str = self.mark_used(invoice_id)
        arch_ok = False
        profile_name = ""
        if profile_path_str:
            profile_path = Path(profile_path_str)
            profile_name = profile_path.name
            if profile_path.exists():
                try:
                    archive_fn = self._m("_archive_profile")
                    loop = asyncio.get_running_loop()
                    arch_ok = await loop.run_in_executor(
                        None, lambda: archive_fn(profile_path))
                except Exception as exc:
                    logger.debug(f"GGSell: archive profile: {exc}")

        msg = f"🟡 *Ссылка отмечена как использованная*\n\nЗаказ: `#{invoice_id}`"
        if profile_name:
            status = "✅ заархивирован" if arch_ok else "⚠️ не найден / уже удалён"
            msg += f"\n📁 Профиль `{profile_name}`: {status}"

        await self._edit(cid, mid, msg, {"inline_keyboard": [
            [{"text": "◀️ Заказ",  "callback_data": f"ggsell:order:{invoice_id}"},
             {"text": "◀️ Заказы", "callback_data": "ggsell:orders"}],
        ]})

    def _bind_profile_to_order(self, profile_path_str: str, invoice_id: int,
                                link: str = "", buyer_email: str = "") -> None:
        """Привязывает заказ GGSell к профилю: пишет issued_ts/issued_link/
        issued_invoice_id/buyer_email в .profile_meta.json. Профиль → статус «выдан»."""
        try:
            import time as _time
            if not profile_path_str:
                return
            meta_file = Path(profile_path_str) / ".profile_meta.json"
            if not meta_file.exists():
                return
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["issued_ts"]         = _time.time()
            meta["issued_invoice_id"] = invoice_id
            if link:
                # Ведём link_history и здесь (запись идёт мимо _save_meta_field)
                _hist = meta.get("link_history")
                if not isinstance(_hist, list):
                    _hist = []
                if not _hist:
                    _old = (meta.get("black_short_link") or meta.get("black_activation_link")
                            or meta.get("issued_link") or "")
                    if _old and _old != link:
                        _hist.append({"ts": meta.get("link_received_ts")
                                            or meta.get("issued_ts") or 0,
                                      "link": _old})
                if not _hist or _hist[-1].get("link") != link:
                    _hist.append({"ts": _time.time(), "link": link})
                meta["link_history"] = _hist
                meta["issued_link"] = link
            if buyer_email:
                meta["buyer_email"] = buyer_email
            meta_file.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(
                f"GGSell: профиль {Path(profile_path_str).name} привязан к заказу #{invoice_id}")
        except Exception as exc:
            logger.debug(f"GGSell: _bind_profile_to_order: {exc}")

    def get_sent_link(self, invoice_id: int) -> str:
        self.get_done()
        return self._done_links.get(invoice_id, "")

    def get_bound_profile(self, invoice_id: int) -> str:
        """Путь Chrome-профиля, привязанного к заказу (из ggsel_done.json)."""
        try:
            raw = json.loads((_DATA_DIR / "ggsel_done.json").read_text(encoding="utf-8"))
            return raw.get("profile_paths", {}).get(str(invoice_id), "")
        except Exception:
            return ""

    # ── Метка заказа для кнопок ─────────────────────────────────────────────

    def _order_label(self, order: dict, invoice_id: int) -> str:
        """Метка заказа для кнопки: email покупателя · дата и время создания
        (номер заказа не пишем)."""
        p = self.parse_order(order)
        email = p["email"]
        date  = p.get("date") or ""
        if not email or not date:
            cached = self.orders.get(invoice_id, {})
            _co = self.parse_order(cached.get("order", {})) if isinstance(cached, dict) else {}
            if not email:
                email = (cached.get("buyer_email") if isinstance(cached, dict) else "") or _co.get("email", "")
            if not date:
                date = _co.get("date", "")

        parts = []
        if email:
            parts.append(email[:34])
        if date:
            parts.append(date)
        return "  ·  ".join(parts) if parts else f"#{invoice_id}"

    # ── Парсинг заказа ───────────────────────────────────────────────────────

    def parse_order(self, order: dict) -> dict:
        product   = order.get("product") or {}
        name      = (product.get("name") or product.get("product_name")
                     or order.get("product_name") or order.get("name")
                     or order.get("offer_title") or "YouTube Premium")
        buyer     = order.get("buyer") or order.get("buyer_info") or {}
        email     = (buyer.get("email") or order.get("email")
                     or order.get("buyer_email") or "")
        sum_buy   = (order.get("sum_t") or order.get("sum") or order.get("amount_t")
                     or order.get("amount") or order.get("buyer_sum")
                     or order.get("price_total") or order.get("total") or "")
        sum_sell  = (order.get("sum_seller") or order.get("seller_sum")
                     or order.get("profit") or order.get("payout")
                     or order.get("amount_seller") or "")
        status    = order.get("status") or order.get("state") or ""
        date      = str(order.get("date") or order.get("created_at") or "").replace("T", " ")[:16]

        name_short = name
        parts = [p.strip() for p in str(name).split("|")]
        if len(parts) >= 2:
            name_short = f"{parts[0]} | {parts[1]}"
        if len(name_short) > 60:
            name_short = name_short[:57] + "…"

        parsed_options = []
        # Приоритет 1: selected_options — строки "Название: значение (+X.X RUB)"
        for s in (order.get("selected_options") or []):
            s = str(s).strip()
            if ": " not in s:
                continue
            opt_name, rest = s.split(": ", 1)
            opt_name = opt_name.strip()
            price_add = 0.0
            m = re.search(r'\(\+(\d+(?:\.\d+)?)\s*RUB\)', rest)
            if m:
                try:
                    price_add = float(m.group(1))
                except Exception:
                    pass
                rest = re.sub(r'\s*\(\+[\d.]+\s*RUB\)', '', rest).strip()
            if opt_name and rest:
                parsed_options.append({"name": opt_name, "value": rest, "price_add": price_add})

        # Приоритет 2: структурированные options[] (Seller API v1)
        if not parsed_options:
            for opt in (order.get("options") or []):
                opt_name  = (opt.get("name") or opt.get("title") or opt.get("label") or "").strip()
                opt_val   = (opt.get("user_data") or opt.get("value") or opt.get("selected") or "").strip()
                opt_price = float(opt.get("price_add") or opt.get("amount_add") or 0)
                if opt_name and opt_val:
                    parsed_options.append({"name": opt_name, "value": opt_val, "price_add": opt_price})

        return {
            "name": str(name),
            "name_short": name_short,
            "email": str(email),
            "sum_buy": sum_buy,
            "sum_sell": sum_sell,
            "status": str(status),
            "date": date,
            "options": parsed_options,
        }

    # ── Карточка заказа ──────────────────────────────────────────────────────

    def order_text(self, invoice_id: int) -> str:
        item        = self.orders.get(invoice_id, {})
        order       = item.get("order", {})
        p           = self.parse_order(order)
        email       = (item.get("buyer_email") or p["email"]
                       or self._done_buyer_emails.get(invoice_id, ""))
        confirm_lnk = self.confirm.get(invoice_id)
        done        = self.get_done()
        sent_link   = self.get_sent_link(invoice_id)
        used_ids    = self.get_used()

        lines = [
            f"📦 *Заказ* `#{invoice_id}`",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
        ]

        # Название товара
        if p["name_short"]:
            lines.append(f"🏷 *{p['name_short']}*")

        # Параметры (период подписки и т.д.)
        if p["options"]:
            for opt in p["options"]:
                v_s = opt["value"][:60] + "…" if len(opt["value"]) > 60 else opt["value"]
                lines.append(f"   ▸ {opt['name']}: `{v_s}`")

        lines.append("")

        # Покупатель
        if email:
            lines.append(f"👤 Покупатель: `{email}`")

        # Суммы
        if p["sum_buy"]:
            try:
                buy_s = f"{float(p['sum_buy']):.2f}".rstrip("0").rstrip(".")
            except Exception:
                buy_s = str(p["sum_buy"])
            lines.append(f"💰 Сумма покупки: *{buy_s}₽*")
        if p["sum_sell"]:
            try:
                sell_s = f"{float(p['sum_sell']):.2f}".rstrip("0").rstrip(".")
            except Exception:
                sell_s = str(p["sum_sell"])
            lines.append(f"💼 Твоя выплата: *{sell_s}₽*")

        # Дата создания заказа
        if p["date"]:
            lines.append(f"🕒 Создан: `{p['date']}`")

        # Статус
        lines.append("")
        issued_dt = done.get(invoice_id, "")
        _refunded = self.get_refunded()
        if invoice_id in _refunded:
            lines.append(f"↩️ *Статус: возврат*  ·  `{_refunded[invoice_id]}`")
        elif invoice_id in used_ids:
            lines.append("🟡 *Статус: в архиве*")
        elif invoice_id in done:
            lines.append("🔵 *Статус: выдано*")
        elif confirm_lnk:
            lines.append("⏳ *Статус: ждёт подтверждения*")
            lines.append(f"🔗 `{confirm_lnk}`")
        else:
            lines.append("🟢 *Статус: новый*")

        # Привязанный (оплаченный) профиль: номер, короткая ссылка, дата выдачи
        bound = self.get_bound_profile(invoice_id)
        if bound:
            meta = self._read_bound_meta(bound)
            ph_raw = Path(bound).name.replace("profile_", "")
            short_link = (meta.get("black_short_link") or sent_link
                          or meta.get("issued_link")
                          or meta.get("black_activation_link") or "")
            issued_str = issued_dt or meta.get("issued_str") or ""
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("📎 *Привязанный профиль*")
            lines.append(f"📱 `{self._disp_phone(ph_raw)}`")
            if short_link:
                lines.append(f"🔗 `{short_link}`")
            if issued_str:
                lines.append(f"📅 Выдан: `{issued_str}`")
        elif sent_link and (invoice_id in done or invoice_id in used_ids):
            # Профиль не привязан, но ссылка покупателю отправлялась
            lines.append(f"🔗 `{sent_link}`")
            if issued_dt:
                lines.append(f"📅 Выдан: `{issued_dt}`")

        return "\n".join(lines)

    @staticmethod
    def _read_bound_meta(profile_path: str) -> dict:
        """Метаданные привязанного профиля (.profile_meta.json)."""
        try:
            mf = Path(profile_path) / ".profile_meta.json"
            if mf.exists():
                return json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    @staticmethod
    def _disp_phone(username: str) -> str:
        u = str(username).strip()
        if len(u) == 12 and u.startswith("91") and u.isdigit():
            return f"+91 {u[2:]}"
        return f"+91 {u}"

    def order_kb(self, invoice_id: int, review_exists: bool = False) -> dict:
        done        = self.get_done()
        used_ids    = self.get_used()
        refunded    = self.get_refunded()
        confirm_lnk = self.confirm.get(invoice_id)
        rows = []
        if invoice_id in refunded:
            # Возврат: выдача скрыта, можно только снять пометку
            rows.append([{"text": "↩️ Возврат — снять пометку",
                          "callback_data": f"ggsell:unmark_refund:{invoice_id}"}])
        elif confirm_lnk:
            rows.append([
                {"text": "📤 Отправить покупателю", "callback_data": f"ggsell:send:{invoice_id}"},
            ])
            rows.append([{"text": "❌ Не отправлять", "callback_data": f"ggsell:nosend:{invoice_id}"}])
        elif invoice_id not in done:
            rows.append([{"text": "▶️ Выполнить",      "callback_data": f"ggsell:run:{invoice_id}"}])
            rows.append([
                {"text": "✅ Отметить выдано",  "callback_data": f"ggsell:mark_done:{invoice_id}"},
                {"text": "↩️ Отметить возврат", "callback_data": f"ggsell:mark_refund:{invoice_id}"},
            ])
        elif invoice_id not in used_ids:
            rows.append([{"text": "🟡 Использована", "callback_data": f"ggsell:mark_used:{invoice_id}"},
                         {"text": "↩️ Отметить возврат", "callback_data": f"ggsell:mark_refund:{invoice_id}"}])
        else:
            # В архиве — возврат всё ещё возможен (деньги вернули после выдачи)
            rows.append([{"text": "↩️ Отметить возврат",
                          "callback_data": f"ggsell:mark_refund:{invoice_id}"}])

        # Привязанный профиль: переход и замена ссылки (как в меню профиля)
        bound = self.get_bound_profile(invoice_id)
        if bound:
            bound_phone = Path(bound).name.replace("profile_", "")
            rows.append([
                {"text": "👤 Перейти к профилю",
                 "callback_data": f"profile:menu:{bound_phone}:active"},
                {"text": "🔄 Заменить ссылку",
                 "callback_data": f"profile:refresh_link:{bound_phone}"},
            ])
            rows.append([{"text": "📜 История ссылок",
                          "callback_data": f"ggsell:link_history:{invoice_id}"}])

        review_icon = "✅" if review_exists else "❌"
        chat_row = [
            {"text": "💬 Чат", "callback_data": f"ggsell:chat:{invoice_id}"},
            {"text": f"{review_icon} Отзыв", "callback_data": f"ggsell:review_order:{invoice_id}"},
        ]
        rows.append(chat_row)
        rows.append([{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}])
        return {"inline_keyboard": rows}

    def settings_page(self, cid) -> tuple:
        ord_on = self._get(cid, "ggsel_notify_orders")
        msg_on = self._get(cid, "ggsel_notify_messages")
        rev_on = self._get(cid, "ggsel_notify_reviews")
        lines = [
            "⚙️ *GGSell — Настройки*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            "*🔔 Уведомления:*",
            f"  {'✅' if ord_on else '❌'} Заказы: {'включены' if ord_on else 'выключены'}",
            f"  {'✅' if msg_on else '❌'} Чат: {'включены' if msg_on else 'выключены'}",
            f"  {'✅' if rev_on else '❌'} Отзывы: {'включены' if rev_on else 'выключены'}",
            "",
            "*📝 Шаблоны сообщений:*",
            "  Тексты, которые отправляются покупателю",
            "  при выдаче ссылки и при ожидании.",
        ]
        if self.webhook_url:
            wh_endpoint = f"{self.webhook_url}/ggsel/notify"
            lines += [
                "",
                "*🌐 Вебхук (уведомления от GGSell):*",
                f"  `{wh_endpoint}`",
                "  _Вставь этот URL в настройки GGSell → Уведомления_",
            ]
        kb = {"inline_keyboard": [
            [{"text": ("🔔 Заказы: Вкл"  if ord_on else "🔕 Заказы: Выкл"),
              "callback_data": "ggsell:toggle:orders"},
             {"text": ("🔔 Чат: Вкл"     if msg_on else "🔕 Чат: Выкл"),
              "callback_data": "ggsell:toggle:messages"},
             {"text": ("🔔 Отзывы: Вкл"  if rev_on else "🔕 Отзывы: Выкл"),
              "callback_data": "ggsell:toggle:reviews"}],
            [{"text": "📝 Шаблоны сообщений", "callback_data": "ggsell:templates"},
             {"text": "💳 Порядок карт",       "callback_data": "ggsell:card_order"}],
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]}
        return "\n".join(lines), kb

    # ── Шаблоны сообщений ────────────────────────────────────────────────────

    _TEMPLATE_NAMES = {
        "msg_greeting":    ("Приветствие",      "Отправляется покупателю сразу при получении ЛЮБОГО нового заказа."),
        "msg_template":    ("Ссылка готова",    "Отправляется покупателю вместе со ссылкой на активацию. Используй `{link}` для вставки ссылки."),
        "msg_wait":        ("Ожидание",         "Отправляется покупателю пока ссылка ещё готовится."),
        "msg_review_promo":("Промокод за отзыв","Отправляется покупателю автоматически при получении отзыва 5 звёзд. Используй `{promo_code}` для вставки кода."),
    }

    def load_templates(self) -> dict:
        try:
            return json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _tpl_default(self, name: str) -> str:
        from ggsell.monitor import MSG_TEMPLATE, MSG_WAIT, MSG_REVIEW_PROMO, MSG_GREETING
        return {"msg_greeting": MSG_GREETING, "msg_template": MSG_TEMPLATE,
                "msg_wait": MSG_WAIT, "msg_review_promo": MSG_REVIEW_PROMO}.get(name, "")

    def bg_templates_page_sync(self, cid: int) -> tuple:
        saved = self.load_templates()
        lines = [
            "📝 *GGSell — Шаблоны сообщений*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            "Нажми на шаблон чтобы просмотреть или изменить.", "",
        ]
        rows = []
        for key, (label, desc) in self._TEMPLATE_NAMES.items():
            status = "✏️ изменён" if key in saved and saved[key].strip() else "📄 по умолчанию"
            lines.append(f"*{label}* — _{status}_")
            lines.append(f"  _{desc}_")
            lines.append("")
            rows.append([{"text": f"📝 {label}", "callback_data": f"ggsell:template_view:{key}"}])
        rows.append([{"text": "◀️ Настройки", "callback_data": "ggsell:settings"}])
        return "\n".join(lines), {"inline_keyboard": rows}

    def bg_template_view_sync(self, name: str) -> tuple:
        label, desc = self._TEMPLATE_NAMES.get(name, (name, ""))
        saved = self.load_templates()
        text  = saved.get(name, "").strip() or self._tpl_default(name)
        is_custom = name in saved and saved[name].strip()
        preview   = text[:300] + "…" if len(text) > 300 else text
        lines = [
            f"📝 *Шаблон: {label}*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"_{desc}_", "",
            "*Текущий текст:*", "",
            preview,
        ]
        if is_custom:
            lines += ["", "✏️ _Изменён вами_"]
        else:
            lines += ["", "📄 _Используется текст по умолчанию_"]
        rows = [
            [{"text": "✏️ Изменить", "callback_data": f"ggsell:template_edit:{name}"}],
        ]
        if is_custom:
            rows.append([{"text": "🔄 Сбросить к умолчанию", "callback_data": f"ggsell:template_reset:{name}"}])
        rows.append([{"text": "◀️ Шаблоны", "callback_data": "ggsell:templates"}])
        return "\n".join(lines), {"inline_keyboard": rows}

    def check_template_edit_mode(self, cid: int, text: str) -> Optional[str]:
        """Если cid в режиме редактирования шаблона — вернуть имя шаблона и выйти из режима."""
        if cid in self.template_edit_mode and text and not text.startswith("/"):
            return self.template_edit_mode.pop(cid)
        return None

    async def bg_template_save(self, cid: int, name: str, text: str) -> None:
        from ggsell.monitor import save_template
        save_template(name, text)
        label, _ = self._TEMPLATE_NAMES.get(name, (name, ""))
        preview  = text[:200] + "…" if len(text) > 200 else text
        await self._send(cid,
            f"✅ *Шаблон «{label}» сохранён!*\n\n"
            f"{preview}",
            reply_markup={"inline_keyboard": [
                [{"text": "📝 Шаблоны", "callback_data": "ggsell:templates"}],
            ]}
        )

    # ── Порядок карт ─────────────────────────────────────────────────────────

    # Единый порядок карт для всех покупок (тот же, что в основных настройках/консоли)
    _CARD_ORDER_FILE = _DATA_DIR / "card_order.json"

    def _load_cards(self) -> list:
        """Прочитать cards.json. Возвращает список card-dict."""
        try:
            f = self._root / "data" / "cards.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8")) or []
        except Exception:
            pass
        return []

    def _load_card_order(self) -> list:
        """Загрузить сохранённый порядок карт (0-based индексы). Пустой список = не задан."""
        try:
            if self._CARD_ORDER_FILE.exists():
                v = json.loads(self._CARD_ORDER_FILE.read_text(encoding="utf-8"))
                if isinstance(v, list):
                    return v
        except Exception:
            pass
        return []

    def _save_card_order(self, order: list) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._CARD_ORDER_FILE.write_text(
            json.dumps(order, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _card_label(c: dict, n: int) -> str:
        """Короткое описание карты: [N] Название **** 1234"""
        name = (c.get("nickname") or c.get("name") or "Карта")[:20]
        num  = str(c.get("number", "")).replace(" ", "").replace("-", "")
        mask = f"*{num[-4:]}" if len(num) >= 4 else "****"
        exp  = c.get("expiry", "")
        exp_s = f"  {exp}" if exp else ""
        return f"[{n}] {name}  {mask}{exp_s}"

    def cards_order_page_sync(self) -> tuple:
        """Страница управления порядком карт. Возвращает (text, keyboard)."""
        cards = self._load_cards()
        order = self._load_card_order()

        lines = ["💳 *Порядок карт для авто-оплаты*", ""]
        if not cards:
            lines.append("_Нет карт. Добавьте карту через меню консоли._")
            kb = {"inline_keyboard": [[{"text": "◀️ Настройки", "callback_data": "ggsell:settings"}]]}
            return "\n".join(lines), kb

        # Показываем все карты с номерами
        lines.append("*Доступные карты:*")
        for i, c in enumerate(cards):
            lines.append(f"  `{self._card_label(c, i + 1)}`")
        lines.append("")

        # Показываем текущий порядок
        if order:
            order_labels = []
            for idx in order:
                if 0 <= idx < len(cards):
                    num = str(cards[idx].get("number", "")).replace(" ", "")[-4:]
                    order_labels.append(f"*{idx + 1}*  _{cards[idx].get('nickname') or 'Карта'}_ (*{num})")
            if order_labels:
                lines.append("*Текущий порядок попытки:*")
                for pos, lbl in enumerate(order_labels, 1):
                    lines.append(f"  {pos}. {lbl}")
                lines.append("")
        else:
            lines.append("_Порядок не задан — карты берутся по умолчанию_")
            lines.append("")

        lines.append("_Нажми кнопку ниже и отправь порядок числами через пробел._")
        lines.append(f"_Например:_ `1 3 2`  _(попробует 1-ю, затем 3-ю, затем 2-ю)_")

        kb_rows = []
        if order:
            kb_rows.append([{"text": "🔄 Сбросить к умолчанию", "callback_data": "ggsell:card_order_reset"}])
        kb_rows.append([{"text": "✏️ Изменить порядок", "callback_data": "ggsell:card_order_edit"}])
        kb_rows.append([{"text": "◀️ Настройки", "callback_data": "ggsell:settings"}])
        return "\n".join(lines), {"inline_keyboard": kb_rows}

    def check_card_order_mode(self, cid: int, text: str) -> bool:
        """True если cid ждёт ввода порядка карт и текст не является командой."""
        if cid not in self.card_order_mode:
            return False
        if not text or text.startswith("/"):
            self.card_order_mode.pop(cid, None)
            return False
        return True

    async def bg_card_order_save(self, cid: int, text: str) -> None:
        """Разобрать строку типа '1 3 2' и сохранить как порядок карт (0-based)."""
        self.card_order_mode.pop(cid, None)
        cards = self._load_cards()
        if not cards:
            await self._send(cid, "❌ Нет карт для настройки порядка.")
            return

        # Парсим числа: принимаем пробел, запятую, тире
        import re as _re
        tokens = _re.split(r"[\s,;]+", text.strip())
        order = []
        errors = []
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            try:
                n = int(t)
                idx = n - 1  # 1-based → 0-based
                if not (0 <= idx < len(cards)):
                    errors.append(f"`{t}` — нет такой карты")
                elif idx in order:
                    errors.append(f"`{t}` — повторяется")
                else:
                    order.append(idx)
            except ValueError:
                errors.append(f"`{t}` — не число")

        if errors:
            err_list = "\n".join(f"  • {e}" for e in errors)
            await self._send(cid,
                f"⚠️ Ошибки в порядке:\n{err_list}\n\n"
                f"_Попробуй ещё раз: отправь числа через пробел, например_ `1 3 2`",
                reply_markup={"inline_keyboard": [
                    [{"text": "💳 Порядок карт", "callback_data": "ggsell:card_order"}],
                ]})
            return

        if not order:
            await self._send(cid, "❌ Не удалось прочитать порядок. Отправь числа через пробел: `1 3 2`")
            return

        self._save_card_order(order)

        order_str = " → ".join(
            f"*{idx + 1}*  _{cards[idx].get('nickname') or 'Карта'}_"
            for idx in order if idx < len(cards)
        )
        await self._send(cid,
            f"✅ *Порядок карт сохранён!*\n\n{order_str}",
            reply_markup={"inline_keyboard": [
                [{"text": "💳 Порядок карт", "callback_data": "ggsell:card_order"}],
                [{"text": "◀️ Настройки",   "callback_data": "ggsell:settings"}],
            ]})

    # ── Баланс ───────────────────────────────────────────────────────────────

    async def _fetch_balance(self, cli):
        bal_s = lock_s = plus_s = payment_date_s = ""
        try:
            bi = await cli.get_balance_info()
            bal_s  = f"${bi['free']:.2f}"
            lock_s = f"${bi['lock']:.2f}" if bi.get("lock") else ""
            plus_s = f"${bi['plus']:.2f}" if bi.get("plus") else ""
        except Exception as exc:
            bal_s = f"❌ {exc}"
        try:
            sched = await cli.get_payment_schedule()
            if isinstance(sched, dict) and sched:
                c = sched.get("content") or sched
                items = c if isinstance(c, list) else (
                    c.get("items") or c.get("data") or c.get("transactions") or []
                )
                if isinstance(items, list) and items:
                    f0 = items[0]
                    amt = (f0.get("amount") or f0.get("sum") or f0.get("total") or "")
                    dt  = (f0.get("date") or f0.get("payment_date") or f0.get("release_date") or "")
                    if amt and not plus_s:
                        try:
                            plus_s = f"${float(amt):.2f}"
                        except Exception:
                            plus_s = str(amt)
                    if dt:
                        payment_date_s = str(dt)[:16].replace("T", " ")
                elif isinstance(c, dict):
                    amt = c.get("pending") or c.get("pending_amount") or ""
                    dt  = c.get("next_payment") or c.get("next_payment_date") or ""
                    if amt and not plus_s:
                        try:
                            plus_s = f"${float(amt):.2f}"
                        except Exception:
                            plus_s = str(amt)
                    if dt:
                        payment_date_s = str(dt)[:16].replace("T", " ")
        except Exception:
            pass
        return bal_s, lock_s, plus_s, payment_date_s

    # ── Фоновые задачи (страницы) ─────────────────────────────────────────────

    async def bg_info(self, cid, mid):
        cli = self.get_client()
        if cli is None:
            await self._edit(cid, mid,
                "🏪 *GGSell*\n\n❌ _Не настроен. Заполните_ `ggsel` _в_ `secrets.yaml`_._",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:other"}]]})
            return

        auto_on     = self._get(cid, "ggsel_auto_fulfill")
        done_cnt    = len(self.get_done())
        pending_cnt = len(self._auto_pending)

        bal_s, lock_s, plus_s, payment_date_s = await self._fetch_balance(cli)

        lines = ["🏪 *GGSell — Панель продавца*", "━━━━━━━━━━━━━━━━━━━━━━", ""]

        # Баланс | Холд
        bal_line = f"💵 Баланс:  *{bal_s}*"
        if lock_s:
            hold_exp = f"  _(до {payment_date_s[:10]})_" if payment_date_s else ""
            bal_line += f"   │   🔒 Холд:  *{lock_s}*{hold_exp}"
        lines.append(bal_line)
        if plus_s and plus_s != lock_s:
            dp = f" _(поступит {payment_date_s[:10]})_" if payment_date_s else ""
            lines.append(f"⏳ К поступлению:  *{plus_s}*{dp}")

        lines += ["", "─────────────────────"]

        # Краткая статистика
        stat_parts = [f"✅ Выдано: *{done_cnt}*"]
        if pending_cnt:
            stat_parts.append(f"⏳ В работе: *{pending_cnt}*")
        lines.append("   ·   ".join(stat_parts))

        auto_label = "🤖 Автоматизация:  ВКЛ ✅" if auto_on else "🤖 Автоматизация:  ВЫКЛ ❌"
        kb_rows = [
            [{"text": auto_label, "callback_data": "ggsell:toggle:auto_fulfill"}],
            [{"text": "📋 Заказы",    "callback_data": "ggsell:orders"},
             {"text": "📦 Офферы",    "callback_data": "ggsell:offers"}],
            [{"text": "⚙️ Настройки", "callback_data": "ggsell:settings"}],
            [{"text": "◀️ Назад",     "callback_data": "go:other"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    @staticmethod
    def _fmt_hold_date(ts: str) -> str:
        """Форматирует дату разморозки выплаты."""
        if not ts:
            return ""
        try:
            return ts[:10]  # YYYY-MM-DD
        except Exception:
            return str(ts)[:10]

    async def bg_orders_page(self, cid, mid, offset: int = 0):
        cli = self.get_client()
        if cli is None:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        try:
            orders_v1_task = asyncio.ensure_future(cli.get_orders_v1(limit=30))
            chats_task     = asyncio.ensure_future(cli.get_chats())
            orders_v1, chats_raw = await asyncio.gather(orders_v1_task, chats_task,
                                                        return_exceptions=True)
            if isinstance(orders_v1, Exception):
                orders_v1 = []
            if isinstance(chats_raw, Exception):
                chats_raw = []
            yt_orders = [o for o in orders_v1
                         if int(o.get("offer_ggsel_id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
        except Exception:
            yt_orders = []
            chats_raw = []

        # Карта invoice_id → email из чатов (самый надёжный источник)
        chat_email_map: dict = {}
        for ch in (chats_raw if isinstance(chats_raw, list) else []):
            try:
                inv_id = int(ch.get("id_i") or ch.get("invoice_id") or ch.get("id") or 0)
                if not inv_id:
                    continue
                em = (ch.get("email") or ch.get("buyer_email") or ch.get("name")
                      or (ch.get("buyer") or {}).get("email") or "")
                if em and "@" in em:
                    chat_email_map[inv_id] = em
            except Exception:
                pass

        if not yt_orders:
            try:
                orders = await cli.get_last_orders()
                yt_orders = [o for o in orders
                             if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
            except Exception:
                yt_orders = []

        # Сортировка: самые новые (наибольший id) — сверху;
        # возвратные заказы отсеиваем в конец списка (stable sort сохранит порядок)
        refunded = self.get_refunded()
        yt_orders.sort(key=lambda o: int(o.get("invoice_id") or o.get("id") or 0), reverse=True)
        yt_orders.sort(key=lambda o: int(o.get("invoice_id") or o.get("id") or 0) in refunded)

        done = self.get_done()
        order_btns = []

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total_cnt    = len(yt_orders)
        today_cnt    = 0
        today_done   = 0
        refund_cnt   = sum(1 for o in yt_orders
                           if int(o.get("invoice_id") or o.get("id") or 0) in refunded)

        for o in yt_orders:
            inv_i = int(o.get("invoice_id") or o.get("id") or 0)
            p     = self.parse_order(o)
            dt    = p["date"]  # "YYYY-MM-DD HH:MM"
            if dt.startswith(today):
                today_cnt += 1
                if inv_i in done:
                    today_done += 1

        # Шапка — только статистика
        _refund_stat = f"   ·   ↩️ Возвраты: *{refund_cnt}*" if refund_cnt else ""
        lines = [
            "📋 *GGSell — Заказы YouTube Premium*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"📦 Всего: *{total_cnt}*   ·   📅 Сегодня: *{today_cnt}*   ·   ✅ Выдано сегодня: *{today_done}*{_refund_stat}",
            "",
        ]

        if not yt_orders:
            lines.append("_Нет последних заказов YouTube Premium_")

        PAGE_SIZE = 5
        page_orders = yt_orders[offset:offset + PAGE_SIZE]

        # Для заказов без email — запрашиваем через API параллельно
        def _has_email(o: dict, inv_i: int) -> bool:
            p = self.parse_order(o)
            if (chat_email_map.get(inv_i) or p["email"]
                    or o.get("buyer_email") or o.get("email")
                    or (o.get("buyer") or {}).get("email")
                    or (o.get("buyer_info") or {}).get("email")):
                return True
            cached = self.orders.get(inv_i, {})
            if isinstance(cached, dict):
                if (cached.get("buyer_email")
                        or self.parse_order(cached.get("order", {})).get("email", "")):
                    return True
            return bool(self._done_buyer_emails.get(inv_i, ""))

        missing_ids = [
            int(o.get("invoice_id") or o.get("id") or 0)
            for o in page_orders
            if int(o.get("invoice_id") or o.get("id") or 0)
            and not _has_email(o, int(o.get("invoice_id") or o.get("id") or 0))
        ]
        if missing_ids:
            fetched = await asyncio.gather(
                *[cli.get_buyer_email(i) for i in missing_ids],
                return_exceptions=True)
            for inv_i, res in zip(missing_ids, fetched):
                if isinstance(res, str) and res:
                    self._done_buyer_emails[inv_i] = res

        for o in page_orders:
            inv   = o.get("invoice_id") or o.get("id") or "?"
            inv_i = int(inv) if str(inv).isdigit() else 0
            p     = self.parse_order(o)

            if inv_i in refunded:
                icon = "↩️"
            elif inv_i in done:
                icon = "🔵"
            elif inv_i in self.confirm:
                icon = "⏳"
            else:
                icon = "🟢"

            dt_full = p["date"]
            dt_show = dt_full[5:16] if len(dt_full) >= 16 else dt_full

            # Период подписки: "(3)" или "(12)"
            period_prefix = ""
            for opt in p["options"]:
                val = opt.get("value", "")
                m_p = re.search(r"(\d+)\s*(?:мес|год|month|year)", val.lower())
                if m_p:
                    period_prefix = f"({m_p.group(1)}) "
                    break

            # Email: чаты → parse_order → поля объекта → кэш → done_buyer_emails
            email_s = (
                chat_email_map.get(inv_i)
                or p["email"]
                or o.get("buyer_email") or o.get("email")
                or (o.get("buyer") or {}).get("email")
                or (o.get("buyer_info") or {}).get("email")
                or ""
            )
            if not email_s:
                cached = self.orders.get(inv_i, {})
                if isinstance(cached, dict):
                    email_s = (cached.get("buyer_email")
                               or self.parse_order(cached.get("order", {})).get("email", ""))
            if not email_s:
                email_s = self._done_buyer_emails.get(inv_i, "")

            if email_s:
                btn_label = f"{icon} {period_prefix}{email_s[:35]}  {dt_show}"
            else:
                btn_label = f"{icon} {period_prefix}#{inv}  {dt_show}"
            order_btns.append({"text": btn_label[:64],
                               "callback_data": f"ggsell:order:{inv_i}"})

        btn_rows = [[b] for b in order_btns]

        # Пагинация: кнопка "следующие 5" если есть ещё заказы
        if offset + PAGE_SIZE < len(yt_orders):
            btn_rows.append([{"text": f"Показать следующие 5 ›",
                              "callback_data": f"ggsell:orders:{offset + PAGE_SIZE}"}])

        # Кнопка "Выполнить все" если есть невыданные заказы (возвраты отсеяны)
        green_count = sum(
            1 for o in yt_orders
            if int(o.get("invoice_id") or o.get("id") or 0) not in done
            and int(o.get("invoice_id") or o.get("id") or 0) not in self.confirm
            and int(o.get("invoice_id") or o.get("id") or 0) not in refunded
            and int(o.get("invoice_id") or o.get("id") or 0) > 0
        )
        top_rows = []
        if green_count > 0:
            top_rows = [[{"text": f"✅ Выполнить все ({green_count})",
                          "callback_data": "ggsell:fulfill_all"}]]

        kb_rows = top_rows + btn_rows + [
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_fulfill_all(self, cid, mid):
        """Запускает параллельное выполнение всех невыданных заказов."""
        cli = self.get_client()
        if cli is None:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]})
            return
        try:
            orders_v1 = await cli.get_orders_v1(limit=30)
            yt_orders = [o for o in orders_v1
                         if int(o.get("offer_ggsel_id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
            if not yt_orders:
                orders_fb = await cli.get_last_orders()
                yt_orders = [o for o in orders_fb
                             if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка загрузки заказов: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]})
            return

        done = self.get_done()
        _refunded_fa = self.get_refunded()
        green = [
            (int(o.get("invoice_id") or o.get("id") or 0), o)
            for o in yt_orders
            if int(o.get("invoice_id") or o.get("id") or 0) not in done
            and int(o.get("invoice_id") or o.get("id") or 0) not in self.confirm
            and int(o.get("invoice_id") or o.get("id") or 0) not in _refunded_fa
            and int(o.get("invoice_id") or o.get("id") or 0) > 0
        ]

        if not green:
            await self._edit(cid, mid, "✅ Все заказы уже выданы.",
                {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]})
            return

        await self._edit(cid, mid,
            f"🚀 *Выполняю {len(green)} заказов по очереди...*",
            {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]})

        for idx, (inv_i, o) in enumerate(green, 1):
            if inv_i in self._fulfill_cancel:
                break
            self._fulfill_cancel.discard(inv_i)
            await self.bg_fulfill_order(inv_i, o)

    async def bg_chats_page(self, cid, mid):
        cli = self.get_client()
        if cli is None:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        try:
            chats = await cli.get_chats()
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка загрузки чатов: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        parsed = []
        for ch in chats:
            inv_id = 0
            for field in ("id_i", "invoice_id", "id"):
                try:
                    v = int(ch.get(field) or 0)
                    if v:
                        inv_id = v
                        break
                except Exception:
                    pass
            if not inv_id:
                continue
            email    = (ch.get("email") or ch.get("buyer_email") or ch.get("name")
                        or (ch.get("buyer") or {}).get("email") or "")
            cnt_new  = int(ch.get("cnt_new") or 0)
            last_msg = (ch.get("last_message") or ch.get("last_msg")
                        or ch.get("message") or ch.get("text") or "")
            last_time = (ch.get("last_time") or ch.get("time") or ch.get("date")
                         or ch.get("updated_at") or ch.get("date_update") or "")
            if last_time:
                last_time = str(last_time)[:16].replace("T", " ")
            parsed.append({
                "id": inv_id, "email": email, "cnt_new": cnt_new,
                "last_msg": str(last_msg)[:80], "last_time": last_time,
            })

        parsed.sort(key=lambda x: (-x["cnt_new"], -x["id"]))

        total  = len(parsed)
        unread = sum(1 for c in parsed if c["cnt_new"])

        lines = ["💬 *GGSell — Чаты с покупателями*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
        if total:
            stat = f"_Всего чатов: {total}"
            stat += f" · {unread} непрочитанных_" if unread else "_"
            lines.append(stat)
            lines.append("")

        chat_rows = []
        for ch in parsed[:12]:
            inv_id    = ch["id"]
            email     = ch["email"] or f"заказ #{inv_id}"
            cnt_new   = ch["cnt_new"]
            last_msg  = ch["last_msg"]
            last_time = ch["last_time"]

            if cnt_new:
                head = f"🔴 *#{inv_id}* · {email} · *{cnt_new} новых*"
            else:
                head = f"▸ `#{inv_id}` · {email}"
            if last_time:
                head += f" · _{last_time}_"
            lines.append(head)
            if last_msg:
                preview = last_msg[:60] + "…" if len(last_msg) > 60 else last_msg
                lines.append(f"    _{preview}_")

            email_s = email[:30] + "…" if len(email) > 30 else email
            time_s  = f" · {last_time[5:]}" if last_time else ""
            btn = f"{'🔴 ' if cnt_new else '💬 '}#{inv_id} · {email_s}{time_s}"
            chat_rows.append([{"text": btn[:64], "callback_data": f"ggsell:chat:{inv_id}"}])

        if not parsed:
            lines.append("_Нет активных чатов_")

        kb_rows = chat_rows[:8] + [
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_order_view(self, cid, mid, invoice_id: int) -> None:
        cli   = self.get_client()
        item  = self.orders.get(invoice_id, {})
        order = dict(item.get("order", {}))

        if cli:
            # Шаг 1: быстрый v2 (публичный API /api/v1/orders/{id})
            try:
                v2 = await cli.get_order_info_v2(invoice_id)
                if v2:
                    if v2.get("selected_options") and not order.get("selected_options"):
                        order["selected_options"] = v2["selected_options"]
                    if v2.get("buyer_email") and not order.get("buyer_email"):
                        order["buyer_email"] = v2["buyer_email"]
                    if v2.get("seller_reward_amount") and not order.get("sum_seller"):
                        order["sum_seller"] = v2["seller_reward_amount"]
                    if v2.get("amount") and not order.get("sum_t"):
                        order["sum_t"] = v2["amount"]
                    if v2.get("created_at") and not order.get("date"):
                        order["date"] = v2["created_at"]
                    if v2.get("offer_title") and not order.get("name"):
                        order["name"] = v2["offer_title"]
            except Exception:
                pass

            # Шаг 2: Seller API v1 (/purchase/info/) — фоллбэк для email/сумм/опций
            _still_missing = (
                not (order.get("email") or order.get("buyer_email") or item.get("buyer_email"))
                or not order.get("sum_t")
                or not order.get("date")
            )
            if _still_missing:
                try:
                    info = await cli.get_order_info(invoice_id)
                    c = info.get("content", {}) if isinstance(info, dict) else {}
                    if c:
                        # Email из options (YouTube email)
                        if not order.get("buyer_email"):
                            for opt in c.get("options", []):
                                n = (opt.get("name") or "").lower()
                                if any(k in n for k in ("youtube", "почт", "email", "mail")):
                                    em = (opt.get("user_data") or "").strip()
                                    if em and "@" in em:
                                        order["buyer_email"] = em
                                        break
                        if not order.get("buyer_email"):
                            buyer = c.get("buyer_info", {}) or {}
                            em = buyer.get("email") or ""
                            if em:
                                order["buyer_email"] = em
                        # Суммы (profit = выплата продавцу в v1)
                        if not order.get("sum_t"):
                            order["sum_t"] = c.get("sum_t") or c.get("amount") or ""
                        if not order.get("sum_seller"):
                            order["sum_seller"] = (c.get("profit") or c.get("sum_seller")
                                                   or c.get("seller_reward_amount") or "")
                        # Опции для отображения
                        if not order.get("options") and not order.get("selected_options"):
                            order["options"] = c.get("options") or []
                        # Дата (v1 использует purchase_date)
                        if not order.get("date"):
                            order["date"] = (c.get("purchase_date") or c.get("date")
                                             or c.get("created_at") or "")
                        # Название (c["name"] — UUID, берём из product)
                        if not order.get("name"):
                            prod = c.get("product") or {}
                            order["name"] = prod.get("name") or prod.get("product_name") or ""
                except Exception:
                    pass

            # Сохраняем обогащённые данные в кэш
            em_final = order.get("buyer_email") or order.get("email") or ""
            item = dict(item)
            item["order"] = order
            if em_final and not item.get("buyer_email"):
                item["buyer_email"] = em_final
                if invoice_id not in self._done_buyer_emails:
                    self._done_buyer_emails[invoice_id] = em_final
            self.orders[invoice_id] = item
        # Показываем заказ; если есть отзыв — добавляем блок
        text = self.order_text(invoice_id)
        review_raw = None
        if cli:
            try:
                review_raw = await cli.get_order_review(invoice_id)
                if review_raw:
                    r = self.parse_review(review_raw)
                    rv_lines = ["", "━━━━━━━━━━━━━━━━━━━━━━", "⭐ *Отзыв покупателя*"]
                    if r["rating"]:
                        rv_lines.append(self._stars(r["rating"]))
                    if r["text"]:
                        # Не используем italic (_..._) — ломает Markdown если текст содержит _
                        safe_text = r["text"][:300].replace("_", " ").replace("*", "").replace("`", "")
                        rv_lines.append(safe_text)
                    if r["date"]:
                        rv_lines.append(f"📅 {r['date']}")
                    text = text + "\n".join(rv_lines)
            except Exception:
                pass
        kb = self.order_kb(invoice_id, review_exists=bool(review_raw))
        await self._edit(cid, mid, text, kb)

    async def bg_chat(self, cid, mid, invoice_id: int) -> None:
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Назад",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return
        try:
            messages = await cli.get_messages(invoice_id)
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка загрузки чата: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Назад",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return

        lines = [f"💬 *Чат · заказ #{invoice_id}*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
        if messages:
            for msg in messages[-20:]:
                is_seller = bool(
                    msg.get("is_seller") or msg.get("is_seller_msg")
                    or msg.get("sender") == "seller" or msg.get("type") == "seller"
                )
                raw_date = (msg.get("date") or msg.get("created_at")
                            or msg.get("timestamp") or msg.get("date_add") or "")
                t = str(raw_date)[:16].replace("T", " ") if raw_date else ""
                text_m = (msg.get("text") or msg.get("message") or msg.get("body") or "")
                if len(text_m) > 200:
                    text_m = text_m[:200] + "…"
                sender = "🏪 *Вы*" if is_seller else "👤 *Покупатель*"
                header = sender + (f" · _{t}_" if t else "")
                lines.append(header)
                if text_m:
                    lines.append(text_m)
                lines.append("")
        else:
            lines.append("_Сообщений пока нет_")

        kb = {"inline_keyboard": [
            [{"text": "💬 Написать сообщение",
              "callback_data": f"ggsell:reply:{invoice_id}"}],
            [{"text": "◀️ Заказ", "callback_data": f"ggsell:order:{invoice_id}"}],
        ]}
        await self._edit(cid, mid, "\n".join(lines), kb)

    async def bg_link_to_buyer_page(self, cid: int, mid: int, phone: str, link: str, offset: int = 0) -> None:
        """Показать список заказов для отправки ссылки покупателю (пагинация по 5)."""
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Профиль",
                                       "callback_data": f"profile:menu:{phone}:active"}]]})
            return

        try:
            orders_v1 = await cli.get_orders_v1(limit=30)
            yt_orders = [o for o in orders_v1
                         if int(o.get("offer_ggsel_id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
            if not yt_orders:
                orders = await cli.get_last_orders()
                yt_orders = [o for o in orders
                             if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка загрузки заказов: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Профиль",
                                       "callback_data": f"profile:menu:{phone}:active"}]]})
            return

        done = self.get_done()
        _refunded_lb = self.get_refunded()
        # Сортировка от старых к новым (возрастающий id)
        yt_orders.sort(key=lambda o: int(o.get("invoice_id") or o.get("id") or 0))
        pending = [o for o in yt_orders
                   if int(o.get("invoice_id") or o.get("id") or 0) not in done
                   and int(o.get("invoice_id") or o.get("id") or 0) not in _refunded_lb]

        lp = link[8:44] + "…" if len(link) > 52 else link
        lines = [
            "📤 *Отправить ссылку покупателю*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"🔗 `{lp}`", "",
            "Выбери заказ:",
        ]

        page = pending[offset:offset + 5]
        order_rows = []
        for o in page:
            inv_i = int(o.get("invoice_id") or o.get("id") or 0)
            # Email покупателя: из объекта/кэша, иначе дотягиваем через API
            _em = self.parse_order(o).get("email", "")
            if not _em:
                _cc = self.orders.get(inv_i, {})
                _em = (_cc.get("buyer_email") if isinstance(_cc, dict) else "") \
                    or self._done_buyer_emails.get(inv_i, "")
            if not _em:
                try:
                    _em = (await cli.get_buyer_email(inv_i)) or ""
                except Exception:
                    _em = ""
            _dt = self.parse_order(o).get("date", "")
            parts = []
            if _em:
                parts.append(_em[:34])
            if _dt:
                parts.append(_dt)
            label = "  ·  ".join(parts) if parts else f"#{inv_i}"
            order_rows.append([{"text": label[:64],
                                 "callback_data": f"profile:send_to_order:{phone}:{inv_i}"}])

        if not page and offset == 0:
            lines.append("_Нет незавершённых заказов_")

        nav_row = []
        if offset > 0:
            nav_row.append({"text": "◀️ Пред. 5",
                            "callback_data": f"profile:send_to_buyer:{phone}:{offset - 5}"})
        if offset + 5 < len(pending):
            nav_row.append({"text": "Следующие 5 ▶️",
                            "callback_data": f"profile:send_to_buyer:{phone}:{offset + 5}"})

        kb_rows = order_rows
        if nav_row:
            kb_rows.append(nav_row)
        kb_rows.append([{"text": "◀️ Профиль",
                         "callback_data": f"profile:menu:{phone}:active"}])
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_link_to_order(self, cid: int, mid: int, phone: str, link: str, invoice_id: int) -> None:
        """Отправить ссылку конкретному покупателю через GGSell и привязать профиль к заказу."""
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Профиль",
                                       "callback_data": f"profile:menu:{phone}:active"}]]})
            return
        from ggsell.monitor import get_template
        ok = await cli.send_message(invoice_id, get_template("msg_template").format(link=link))
        if ok:
            # Привязываем заказ к профилю по номеру телефона — ссылка не потеряется
            profile_path = ""
            try:
                for _p in (self._m("_load_done_profiles")() or []):
                    _name = _p["path"].name
                    if str(_p.get("username", "")).endswith(phone) or phone in _name:
                        profile_path = str(_p["path"]); break
            except Exception:
                profile_path = ""
            self.mark_done(invoice_id, link, profile_path=profile_path)
            await self._edit(cid, mid,
                f"✅ *Ссылка отправлена покупателю!*\n\n"
                f"Заказ: `#{invoice_id}`\n🔗 `{link}`\n\n"
                f"_Профиль привязан к этому заказу._",
                {"inline_keyboard": [
                    [{"text": "◀️ GGSell", "callback_data": "go:ggsell"}],
                ]})
        else:
            await self._edit(cid, mid,
                f"❌ Не удалось отправить ссылку заказу `#{invoice_id}`.",
                {"inline_keyboard": [
                    [{"text": "📤 Другой заказ", "callback_data": f"profile:send_to_buyer:{phone}:0"}],
                ]})

    # ── Единая выдача заказа по приоритету профилей ──────────────────────────

    def _order_months(self, order: dict) -> int:
        """Срок подписки из ВЫБРАННОЙ опции заказа: 3, 6 или 12 месяцев.
        Название товара не смотрим — там всегда оба срока. По умолчанию 3."""
        try:
            p = self.parse_order(order)
            for opt in p.get("options", []):
                t = (str(opt.get("value", "")) + " " + str(opt.get("name", ""))).lower()
                m = re.search(r"(\d+)\s*(мес|month|год|year)", t)
                if m:
                    n = int(m.group(1))
                    if m.group(2) in ("год", "year"):
                        n *= 12
                    if n >= 12:
                        return 12
                    if n >= 6:
                        return 6
                    return 3
        except Exception:
            pass
        return 3

    async def _resolve_months(self, invoice_id, order: dict) -> int:
        """Надёжно определяет срок: если в заказе нет опций (например, после
        рестарта self.orders пуст) — дотягивает selected_options через API,
        чтобы не купить не тот период (12 vs 3)."""
        order = order or {}
        if self.parse_order(order).get("options"):
            return self._order_months(order)
        cli = self.get_client()
        sel = []
        if cli:
            try:
                v2 = await cli.get_order_info_v2(invoice_id)
                if v2:
                    sel = v2.get("selected_options") or []
            except Exception:
                pass
            if not sel:
                try:
                    info = await cli.get_order_info(invoice_id)
                    c = (info.get("content") if isinstance(info, dict) else {}) or {}
                    sel = c.get("selected_options") or c.get("options") or []
                except Exception:
                    pass
        if sel:
            return self._order_months({"selected_options": sel})
        return self._order_months(order)

    def _order_youtube_email(self, order: dict) -> str:
        """Email для активации YouTube из параметра заказа
        («Ваш адрес электронной почты для YouTube»). Приоритет над email аккаунта."""
        try:
            p = self.parse_order(order)
            for opt in p.get("options", []):
                name = str(opt.get("name", "")).lower()
                if any(k in name for k in ("youtube", "почт", "email", "mail")):
                    val = str(opt.get("value", "")).strip()
                    if "@" in val:
                        return val
        except Exception:
            pass
        return ""

    def _categorize_profiles(self):
        """(paid, hasdata, available) — невыданные профили по статусам:
        paid — Оплаченные (есть ссылка/активация), hasdata — С данными,
        available — Доступные (вход есть, данные не заполнены)."""
        paid, hasdata, available = [], [], []
        try:
            profiles = self._m("_load_done_profiles")() or []
        except Exception:
            return paid, hasdata, available
        for p in profiles:
            if not p.get("login_ts") or p.get("issued_ts"):
                continue
            st = p.get("status") or ""
            has_link  = bool(p.get("black_activation_link") or p.get("black_short_link"))
            is_subact = st in ("activated", "explore_now", "activate_now") or bool(p.get("black_valid_till"))
            is_ready  = bool(p.get("prepared_ts") or p.get("buyer_email") or st == "email_completed")
            if has_link or is_subact:
                paid.append(p)
            elif is_ready:
                hasdata.append(p)
            else:
                available.append(p)
        available.sort(key=lambda x: x.get("login_ts") or 0)
        hasdata.sort(key=lambda x: x.get("prepared_ts") or x.get("login_ts") or 0)
        return paid, hasdata, available

    async def _notify_fulfill(self, cid, mid, text: str, cancel_inv=None) -> None:
        """Сообщение о ходе выдачи: редактируем карточку (кнопка «Выполнить»)
        или шлём всем подписчикам (авто-выдача по сообщению покупателя).
        Если задан cancel_inv — добавляем кнопку «❌ Отмена»."""
        if cancel_inv:
            kb = {"inline_keyboard": [
                [{"text": "❌ Отмена выполнения",
                  "callback_data": f"ggsell:fulfill_cancel:{cancel_inv}"}],
                [{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}],
            ]}
        else:
            kb = {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]}
        if cid and mid:
            try:
                await self._edit(cid, mid, text, kb)
                return
            except Exception:
                pass
        for c in list(self.subs):
            try:
                await self._send(c, text, reply_markup=kb)
            except Exception:
                pass

    async def _send_link_and_bind(self, invoice_id, link, profile_path, phone, cid, mid, source="", gg_months=3) -> None:
        """Отправляет ссылку покупателю шаблоном и привязывает профиль к заказу."""
        from ggsell.monitor import get_template
        cli = self.get_client()
        ok = False
        try:
            buyer_msg = get_template("msg_template").format(link=link)
            if gg_months >= 6:
                buyer_msg += (
                    "\n\n⚠️ При покупке 6 месяцев нужно будет через 3 месяца заново активировать подписку.\n"
                    "Обратитесь ко мне в чат — скажите, что первые 3 месяца прошли. Выдам новую ссылку на активацию."
                )
            ok = await cli.send_message(invoice_id, buyer_msg)
        except Exception as exc:
            logger.error(f"GGSell fulfill #{invoice_id}: ошибка отправки ссылки: {exc}")
        if ok:
            self.mark_done(invoice_id, link, profile_path=str(profile_path))
            _src = f"\n_(из «{source}»)_" if source else ""
            await self._notify_fulfill(cid, mid,
                f"✅ *Заказ #{invoice_id} выполнен!*\n"
                f"📱 Профиль: `{self._disp_phone(phone)}`\n"
                f"🔗 `{link}`\n"
                f"_Ссылка отправлена покупателю, профиль привязан к заказу._{_src}")
        else:
            await self._notify_fulfill(cid, mid,
                f"⚠️ *Заказ #{invoice_id}*: ссылка готова, но не отправилась в чат.\n"
                f"📱 `{self._disp_phone(phone)}`\n🔗 `{link}`")

    @staticmethod
    def _run_menu_coro(coro_factory):
        """Запускает корутину menu.py в отдельном event loop (для run_in_executor)."""
        import asyncio as _aio
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro_factory())
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _read_profile_link(self, profile_path) -> str:
        try:
            meta = json.loads((Path(profile_path) / ".profile_meta.json").read_text(encoding="utf-8"))
            # Покупателю всегда КОРОТКАЯ ссылка (clck.ru); длинная — только запасной.
            return (meta.get("black_short_link") or meta.get("black_activation_link")
                    or meta.get("activation_url") or "")
        except Exception:
            return ""

    async def _notify_oos_delete(self, phone, invoice_id) -> None:
        """Сообщает об OOS на профиле и предлагает удалить его (Да/Нет).
        Удаление — только по подтверждению; заказ при этом продолжаем на другом профиле."""
        for _cid in list(self.subs):
            try:
                await self._send(_cid,
                    f"🚫 *Заказ #{invoice_id}*: профиль `{self._disp_phone(phone)}` — "
                    f"Currently out of stock.\n_Перехожу к следующему профилю._\n\n"
                    f"Удалить этот профиль?",
                    reply_markup={"inline_keyboard": [[
                        {"text": "🗑 Да, удалить", "callback_data": f"profile:oosdel:{phone}"},
                        {"text": "✖️ Нет, оставить", "callback_data": f"profile:ooskeep:{phone}"},
                    ]]})
            except Exception:
                pass

    async def _buy_and_deliver(self, prof, months, invoice_id, buyer_email, cid, mid, source="", gg_months=3) -> str:
        """Покупает срок, выдаёт ссылку и привязывает. Возвращает 'ok' | 'oos' | 'fail' | 'cancelled'."""
        import functools, importlib
        phone = prof.get("username", prof["path"].name)
        _label = f"{gg_months} мес (покупаю {months})" if gg_months != months else f"{months} мес"
        # Ждём своей очереди — одновременно только одна покупка во избежание конфликтов профилей
        if self._buy_lock.locked():
            await self._notify_fulfill(cid, mid,
                f"⏳ *Заказ #{invoice_id}*: покупаю {_label} на `{self._disp_phone(phone)}`"
                + (f" (из «{source}»)" if source else "") + "...\n_Ожидаю завершения предыдущей покупки..._",
                cancel_inv=invoice_id)
        async with self._buy_lock:
            if invoice_id in self._fulfill_cancel:
                return "cancelled"
            await self._notify_fulfill(cid, mid,
                f"💳 *Заказ #{invoice_id}*: покупаю {_label} на `{self._disp_phone(phone)}`"
                + (f" (из «{source}»)" if source else "") + "...\n_Займёт несколько минут._",
                cancel_inv=invoice_id)
            menu = importlib.import_module("menu")
            menu._override_email = buyer_email or ""
            try:
                ok, msg = await asyncio.get_event_loop().run_in_executor(
                    None, functools.partial(
                        self._run_menu_coro,
                        lambda: self._m("_do_buy_membership")(prof["path"], months, None)))
            except Exception as exc:
                ok, msg = False, str(exc)
            finally:
                menu._override_email = ""
        if not ok:
            if str(msg) == "CANCELLED":
                return "cancelled"
            if str(msg).startswith("OUT_OF_STOCK"):
                await self._notify_oos_delete(phone, invoice_id)
                return "oos"
            await self._notify_fulfill(cid, mid,
                f"❌ *Заказ #{invoice_id}*: покупка не прошла на `{self._disp_phone(phone)}`.\n`{str(msg)[:200]}`")
            return "fail"
        link = await self._read_profile_link(prof["path"])
        if not link:
            await self._notify_fulfill(cid, mid,
                f"⚠️ *Заказ #{invoice_id}*: покупка прошла, но ссылка не получена (`{self._disp_phone(phone)}`).")
            return "fail"
        await self._send_link_and_bind(invoice_id, link, prof["path"], phone, cid, mid, source, gg_months=gg_months)
        return "ok"

    def _profile_pick_info(self, prof, status_label: str) -> str:
        """Подробности подобранного профиля: номер, статус, даты создания/переноса."""
        phone = prof.get("username", prof["path"].name)
        lines = [
            f"📱 `{self._disp_phone(phone)}`",
            f"🏷 Статус: *{status_label}*",
            f"📆 Создан: `{prof.get('login_str') or '—'}`",
        ]
        pts = prof.get("prepared_ts")
        if pts:
            try:
                lines.append(f"🗂 Перенесён в «С данными»: `{self._m('_fmt_msk')(float(pts))}`")
            except Exception:
                pass
        vt = prof.get("black_valid_till")
        if vt:
            lines.append(f"⏳ Подписка до: `{vt}`")
        return "\n".join(lines)

    async def bg_fulfill_order(self, invoice_id: int, order: dict, cid=None, mid=None) -> None:
        """Выдача заказа по приоритету профилей:
        1) Оплаченные (есть ссылка на нужный срок) → отдать ссылку;
        2) С данными → докупить срок → отдать ссылку;
        3) Доступные → заполнить данные → купить → отдать ссылку;
        4) Профилей нет → создать новый (логин + покупка) → отдать ссылку.
        Во всех случаях профиль привязывается к заказу."""
        import functools, importlib
        cli = self.get_client()
        if not cli:
            await self._notify_fulfill(cid, mid, f"❌ GGSell не настроен (заказ #{invoice_id}).")
            return

        gg_months = await self._resolve_months(invoice_id, order)
        # 6 мес на GGSell = 2× 3 мес на Flipkart (3 сейчас + 3 через 3 мес вручную).
        # 12 мес переименованы в 6 мес в GGSell — обрабатываем так же.
        buy_months = 3 if gg_months >= 6 else gg_months
        months = buy_months  # для совместимости с кодом ниже
        # Email для активации — из параметра заказа «почта для YouTube».
        # Если в заказе его нет — берём через API (тоже парсит этот параметр).
        buyer_email = self._order_youtube_email(order)
        if not buyer_email:
            try:
                buyer_email = (await cli.get_buyer_email(invoice_id)) or ""
            except Exception:
                buyer_email = ""
        if not buyer_email:
            buyer_email = (order.get("buyer_email")
                           or (order.get("buyer") or {}).get("email")
                           or order.get("email") or "")
        buyer_email = buyer_email.strip()

        self._fulfill_cancel.discard(invoice_id)  # сброс возможной прошлой отмены
        try:
            importlib.import_module("menu")._purchase_cancel.clear()
        except Exception:
            pass

        async def _cancelled() -> bool:
            if invoice_id in self._fulfill_cancel:
                self._fulfill_cancel.discard(invoice_id)
                try:
                    importlib.import_module("menu")._purchase_cancel.clear()
                except Exception:
                    pass
                await self._notify_fulfill(cid, mid,
                    f"🛑 *Заказ #{invoice_id}*: выполнение отменено.")
                return True
            return False

        paid, hasdata, available = self._categorize_profiles()
        _fail_reasons = []   # (phone, причина) — для диагностики в итоговом сообщении

        _months_label = f"{gg_months} мес (покупаю {months})" if gg_months != months else f"{months} мес"

        # 1. Оплаченные с подходящим сроком и готовой ссылкой
        paid_match = [
            p for p in paid
            if (p.get("black_activation_link") or p.get("black_short_link"))
            and int(p.get("subscription_months") or 0) == months
        ]
        if paid_match:
            prof = paid_match[0]
            link = prof.get("black_short_link") or prof.get("black_activation_link")
            phone = prof.get("username", prof["path"].name)
            await self._notify_fulfill(cid, mid,
                f"🔎 *Заказ #{invoice_id}* — подобран профиль ({_months_label}):\n"
                + self._profile_pick_info(prof, "Оплаченные")
                + "\n\n_Отдаю готовую ссылку покупателю..._")
            await self._send_link_and_bind(invoice_id, link, prof["path"], phone, cid, mid, "Оплаченные", gg_months=gg_months)
            return

        # 2. С данными — докупить срок (перебираем по очереди; OOS → следующий)
        for prof in hasdata:
            if await _cancelled():
                return
            await self._notify_fulfill(cid, mid,
                f"🔎 *Заказ #{invoice_id}* — подобран профиль ({_months_label}):\n"
                + self._profile_pick_info(prof, "С данными")
                + "\n\n_Покупаю срок и выдаю ссылку..._", cancel_inv=invoice_id)
            _r = await self._buy_and_deliver(prof, months, invoice_id, buyer_email, cid, mid, "С данными", gg_months=gg_months)
            if _r == "ok":
                return
            if _r == "cancelled":
                await self._notify_fulfill(cid, mid, f"🛑 *Заказ #{invoice_id}*: выполнение отменено.")
                return
            _ph2 = prof.get("username", prof["path"].name)
            _fail_reasons.append((_ph2, "Out of stock" if _r == "oos" else "покупка не прошла"))
            continue

        # 3. Доступные — покупаем напрямую, как при ручной покупке.
        # ВАЖНО: раньше тут был отдельный предварительный шаг
        # _do_fill_address(stop_at_payment=True), а уже потом покупка. Этот путь
        # расходился с рабочим ручным («Купить» → _do_buy_membership) и ломался
        # на всех профилях подряд. _do_buy_membership сам делает «Buy Now → адрес
        # (если нужен) → viewcheckout → почта → оплата», а email покупателя
        # подставляется через _override_email внутри _buy_and_deliver. Поэтому
        # отдельное заполнение убрано — перебираем профили одним проходом.
        for prof in available:
            if await _cancelled():
                return
            phone = prof.get("username", prof["path"].name)
            await self._notify_fulfill(cid, mid,
                f"🔎 *Заказ #{invoice_id}* — подобран профиль ({_months_label}):\n"
                + self._profile_pick_info(prof, "Доступные")
                + "\n\n_Покупаю срок и выдаю ссылку..._", cancel_inv=invoice_id)
            _r = await self._buy_and_deliver(prof, months, invoice_id, buyer_email, cid, mid, "Доступные", gg_months=gg_months)
            if _r == "ok":
                return
            if _r == "cancelled":
                await self._notify_fulfill(cid, mid, f"🛑 *Заказ #{invoice_id}*: выполнение отменено.")
                return
            _fail_reasons.append((phone, "Out of stock" if _r == "oos" else "покупка не прошла"))
            continue

        # 4. Свободных профилей нет ни в одной вкладке.
        # ВАЖНО: автоматически НЕ запускаем создание нового профиля (полную
        # автоматизацию). Она в цикле покупает номера GrizzlySMS и жжёт деньги,
        # т.к. 3DS-код для НОВОГО аккаунта никто не вводит вовремя → таймаут →
        # следующий номер. Профили нужно готовить заранее.
        # Счётчики помогают понять, почему профиль не подобрался.
        _hint = ""
        if paid and not paid_match:
            _hint = f"\n_(в «Оплаченные» есть {len(paid)}, но не на срок {months} мес)_"
        # Причины, почему перепробованные профили не сработали (диагностика)
        _reasons_txt = ""
        if _fail_reasons:
            _lines = [f"  • `{self._disp_phone(ph)}` — {rs}" for ph, rs in _fail_reasons[:8]]
            _reasons_txt = "\n\n*Перепробованы, но не сработали:*\n" + "\n".join(_lines)
        await self._notify_fulfill(cid, mid,
            f"⚠️ *Заказ #{invoice_id}*: не удалось выдать — нет рабочего профиля.\n"
            f"_Оплаченные: {len(paid)} · С данными: {len(hasdata)} · Доступные: {len(available)}_{_hint}"
            f"{_reasons_txt}\n\n"
            f"_Авто-создание с покупкой номеров отключено, чтобы не жечь деньги. "
            f"Подготовьте профиль и нажмите «▶️ Выполнить» снова._")

    async def bg_check_hanging_orders(self) -> None:
        """Постоянно (с момента старта консоли/бота) следит за «висящими»
        заказами — без выданной ссылки и без привязанного профиля, но с уже
        имеющимся сообщением от покупателя — и предлагает начать выполнение
        кнопкой. Повторно по одному заказу не спрашиваем (дедуп)."""
        import asyncio as _aio
        await _aio.sleep(20)  # даём GGSell-клиенту и монитору подняться
        while True:
            try:
                await self._scan_hanging_orders_once()
            except Exception as exc:
                logger.debug(f"GGSell hanging scan: {exc}")
            await _aio.sleep(300)  # перепроверяем каждые 5 минут

    async def _scan_hanging_orders_once(self) -> None:
        """Один проход поиска зависших заказов."""
        cli = self.get_client()
        if not cli:
            return
        try:
            orders = await cli.get_orders_v1(limit=30)
            yt = [o for o in orders
                  if int(o.get("offer_ggsel_id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
            if not yt:
                orders = await cli.get_last_orders()
                yt = [o for o in orders
                      if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
        except Exception as exc:
            logger.debug(f"GGSell hanging scan: {exc}")
            return

        import datetime as _dt
        from ggsell.monitor import get_template
        greeting = get_template("msg_greeting") or ""
        marker = greeting.strip()[:24]

        done = self.get_done()
        _refunded_sc = self.get_refunded()
        prompted = 0
        for o in yt:
            inv = int(o.get("invoice_id") or o.get("id") or 0)
            if not inv or inv in done or inv in _refunded_sc:
                continue
            if self.get_bound_profile(inv):
                continue
            # Сообщения чата: ищем приветствие (от продавца) и сообщения покупателя
            try:
                msgs = await cli.get_messages(inv, id_from=0)
            except Exception:
                continue
            from ggsell.monitor import is_own_sent as _own
            last_buyer = None
            buyer_cnt = 0
            greet_msg = None
            for m in (msgs or []):
                if m.get("system"):
                    continue
                _mtext = str(m.get("text") or m.get("message") or m.get("body") or "")
                # Наше приветствие (по тексту шаблона) — не покупательское
                if marker and marker in _mtext:
                    greet_msg = m
                    continue
                _is_seller = bool(
                    m.get("is_current_user") or m.get("is_seller") or m.get("is_mine")
                    or m.get("from_seller") or m.get("is_seller_msg")
                    or int(m.get("type_message") or m.get("type_msg") or -1) == 1
                )
                if _is_seller or _own(inv, _mtext):   # продавец/наше отправленное — пропускаем
                    continue
                buyer_cnt += 1
                last_buyer = m   # по возрастанию id → последнее перезапишется

            # 1) Приветствие: если ещё не отправляли (нет в чате и не слали в сессии) — отправляем
            greet_time = ""
            if greet_msg:
                _gr = (greet_msg.get("date") or greet_msg.get("created_at")
                       or greet_msg.get("timestamp") or greet_msg.get("date_add") or "")
                greet_time = str(_gr)[:16].replace("T", " ")
            elif inv in self._greeted_sent:
                greet_time = self._greeted_sent[inv]
            elif greeting:
                try:
                    await cli.send_message(inv, greeting)
                    greet_time = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                    self._greeted_sent[inv] = greet_time
                    logger.info(f"GGSell #{inv}: приветствие отправлено (старт-скан)")
                except Exception as exc:
                    logger.warning(f"GGSell #{inv}: ошибка приветствия: {exc}")

            # 2) Сообщений покупателя нет → приветствие отправили, ждём (не спрашиваем)
            if not last_buyer:
                continue

            # 3) Зависший заказ с сообщением → предлагаем выполнение (один раз за сессию)
            if inv in self._auto_pending or inv in self._hanging_prompted:
                continue

            email = ""
            try:
                email = (await cli.get_buyer_email(inv)) or ""
            except Exception:
                pass

            # Название и купленная опция (срок 3/12 мес). Берём из уже обогащённого
            # заказа (self.orders) или ВСЕГДА дотягиваем selected_options через v2/
            # детали заказа — у списочного объекта периода обычно нет.
            _cached = (self.orders.get(inv) or {}) if isinstance(self.orders.get(inv), dict) else {}
            _co = _cached.get("order", {}) if isinstance(_cached.get("order"), dict) else {}
            _sel  = _co.get("selected_options") or o.get("selected_options") or []
            _opts = _co.get("options") or o.get("options") or []
            _name = _co.get("name") or o.get("offer_title") or o.get("name") or ""
            if not _sel:
                try:
                    _v2 = await cli.get_order_info_v2(inv)
                    if _v2:
                        _sel = _v2.get("selected_options") or _sel
                        if not _name:
                            _name = _v2.get("offer_title") or _name
                except Exception:
                    pass
            if not _sel and not _opts:
                try:
                    _info = await cli.get_order_info(inv)
                    _c = (_info.get("content") if isinstance(_info, dict) else {}) or {}
                    _sel  = _c.get("selected_options") or _sel
                    _opts = _c.get("options") or _opts
                    if not _name:
                        _name = _c.get("offer_title") or _name
                except Exception:
                    pass
            _ord_obj = {"selected_options": _sel, "options": _opts, "name": _name}
            _pp = self.parse_order(_ord_obj)
            _name_s = _pp.get("name_short") or _name
            _months = self._order_months(_ord_obj)

            _odate = str(o.get("date") or o.get("created_at")
                         or o.get("date_add") or "").replace("T", " ")[:16]
            _lm_text = str(last_buyer.get("text") or last_buyer.get("message")
                           or last_buyer.get("body") or "…")
            if len(_lm_text) > 300:
                _lm_text = _lm_text[:300] + "…"
            _lm_raw = (last_buyer.get("date") or last_buyer.get("created_at")
                       or last_buyer.get("timestamp") or last_buyer.get("date_add") or "")
            _lm_time = str(_lm_raw)[:16].replace("T", " ") if _lm_raw else ""

            if greet_time:
                _greet_line = f"👋 Приветствие отправлено: `{greet_time}`\n"
            elif greet_msg or inv in self._greeted_sent:
                _greet_line = "👋 _Приветствие отправлено_\n"
            else:
                _greet_line = ""

            _txt = (
                f"⏳ *Зависший заказ* `#{inv}`\n"
                + (f"📦 {_name_s}\n" if _name_s else "")
                + f"🛒 Опция: *{_months} мес*\n"
                + (f"🗓 Создан: `{_odate}`\n" if _odate else "")
                + (f"👤 `{email}`\n" if email else "")
                + _greet_line
                + f"💬 Сообщений от покупателя: *{buyer_cnt}*\n"
                + "━━━━━━━━━━━━━━━━━━━━━━\n"
                + (f"_Последнее" + (f" ({_lm_time})" if _lm_time else "") + ":_\n")
                + f"{_lm_text}\n\n"
                + "_Профиль не привязан, ссылка не выдана._  Начать выполнение?"
            )
            for _cid in list(self.subs):
                try:
                    await self._send(_cid, _txt,
                        reply_markup={"inline_keyboard": [
                            [{"text": "▶️ Начать выполнение",
                              "callback_data": f"ggsell:run:{inv}"}],
                            [{"text": f"📋 Заказ #{inv}",
                              "callback_data": f"ggsell:order:{inv}"}],
                        ]})
                except Exception:
                    pass
            self._hanging_prompted.add(inv)
            prompted += 1
        if prompted:
            logger.info(f"GGSell: предложено выполнить {prompted} зависших заказа(ов)")

    async def bg_prepare_for_order(self, invoice_id: int, order: dict) -> None:
        """Сразу при получении заказа: резервируем профиль и сохраняем email покупателя."""
        import time as _time

        buyer_email = (
            order.get("buyer_email") or
            (order.get("buyer") or {}).get("email") or
            order.get("email") or ""
        ).strip()

        try:
            profiles = self._m("_load_done_profiles")()
        except Exception as exc:
            logger.error(f"GGSell prepare #{invoice_id}: ошибка загрузки профилей: {exc}")
            return

        available = [
            p for p in profiles
            if p.get("login_ts")
            and not p.get("issued_ts")
            and not p.get("prepared_invoice_id")  # не занят другим заказом
        ]

        if not available:
            logger.warning(f"GGSell prepare #{invoice_id}: нет свободных профилей, запускаю авто-логин")
            for _cid in list(self.subs):
                await self._send(_cid,
                    f"⚠️ *Заказ \\#{invoice_id}*: нет свободных профилей Flipkart\\!\n"
                    f"🔄 Запускаю создание нового профиля...")
            try:
                import subprocess as _sp, sys as _sys
                _sp.Popen(
                    [_sys.executable, str(self._root / "main.py"), "--tg-login", "--accounts", "1"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
                )
            except Exception as exc:
                logger.error(f"GGSell prepare #{invoice_id}: ошибка запуска авто-логина: {exc}")
            return

        profile = available[0]
        profile_path = profile["path"]
        phone = profile.get("username", profile_path.name)

        try:
            self._m("_save_meta_field")(
                profile_path,
                buyer_email=buyer_email,
                prepared_ts=_time.time(),
                prepared_invoice_id=invoice_id,
            )
        except Exception as exc:
            logger.error(f"GGSell prepare #{invoice_id}: ошибка сохранения meta: {exc}")
            return

        email_str = f"\n📧 Email покупателя: `{buyer_email}`" if buyer_email else ""
        for _cid in list(self.subs):
            await self._send(_cid,
                f"✅ *Заказ \\#{invoice_id} — профиль зарезервирован*\n"
                f"📱 `+91 {phone}`{email_str}\n"
                f"⏳ Ждём сообщения от покупателя...")

    async def bg_auto_fulfill(self, invoice_id: int, order: dict) -> None:
        """Полная автоматизация нового заказа:
        1. Шлём приветствие покупателю.
        2. Ищем свободный профиль Flipkart (login_ts есть, issued_ts нет).
        3. Запускаем _do_buy_membership() в отдельном потоке.
        4. Читаем ссылку из meta-файла, шлём покупателю, помечаем профиль выданным.
        """
        import time
        import functools
        from ggsell.monitor import get_template

        cli = self.get_client()
        if not cli:
            return

        # 1. Приветствие покупателю
        greeting = get_template("msg_greeting")
        if greeting:
            try:
                await cli.send_message(invoice_id, greeting)
                logger.info(f"GGSell auto #{invoice_id}: приветствие отправлено")
            except Exception as exc:
                logger.warning(f"GGSell auto #{invoice_id}: ошибка приветствия: {exc}")

        # 2. Найти свободный профиль
        try:
            profiles = self._m("_load_done_profiles")()
        except Exception as exc:
            logger.error(f"GGSell auto #{invoice_id}: ошибка загрузки профилей: {exc}")
            for _cid in list(self.subs):
                await self._send(_cid,
                    f"❌ *Авто-выполнение #{invoice_id}*\n"
                    f"Не удалось загрузить профили Flipkart:\n`{exc}`")
            return

        # Ищем профиль, зарезервированный именно под этот заказ (bg_prepare_for_order)
        reserved = next(
            (p for p in profiles
             if p.get("prepared_invoice_id") == invoice_id and not p.get("issued_ts")),
            None
        )
        # Если зарезервированного нет — берём любой доступный по порядку
        available = (
            [reserved] if reserved
            else [p for p in profiles if p.get("login_ts") and not p.get("issued_ts")]
        )

        if not available:
            logger.warning(f"GGSell auto #{invoice_id}: нет доступных профилей")
            for _cid in list(self.subs):
                await self._send(_cid,
                    f"⚠️ *Авто-выполнение заказа \\#{invoice_id}*\n"
                    f"❌ Нет доступных профилей Flipkart\\!\n"
                    f"Выполните заказ вручную через кнопку ▶️")
            return

        # 3. Порядок карт применяется внутри _do_buy_membership (единый card_order.json)
        gg_months = self._order_months(order)   # из заказа: 3, 6 или 12
        months = 3                              # всегда покупаем 3 мес на Flipkart
        do_buy = self._m("_do_buy_membership")

        def _run_purchase_for(p_path):
            import asyncio as _aio
            loop = _aio.new_event_loop()
            _aio.set_event_loop(loop)
            try:
                return loop.run_until_complete(do_buy(p_path, months, card=None))
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        ok, msg = False, "нет доступных профилей"
        profile_path = None
        phone = "?"

        import importlib as _iml
        _menu_mod = _iml.import_module("menu")

        if True:
            for _profile in available:
                profile_path = _profile["path"]
                phone = _profile.get("username", profile_path.name)
                _buyer_email = _profile.get("buyer_email") or ""

                for _cid in list(self.subs):
                    await self._send(_cid,
                        f"🤖 *Авто-выполнение заказа #{invoice_id}*\n"
                        f"📱 Профиль: `+91 {phone}`\n"
                        f"⏳ Запускаю покупку Flipkart Black...\n"
                        f"_Займёт 5–15 минут_")

                _menu_mod._override_email = _buyer_email
                try:
                    ok, msg = await asyncio.get_event_loop().run_in_executor(
                        None, functools.partial(_run_purchase_for, profile_path))
                except Exception as exc:
                    ok, msg = False, str(exc)
                finally:
                    _menu_mod._override_email = ""

                if ok:
                    break

                logger.warning(f"GGSell auto #{invoice_id}: профиль +91 {phone} не сработал: {msg}")
                if _profile is not available[-1]:
                    for _cid in list(self.subs):
                        await self._send(_cid,
                            f"⚠️ *Профиль +91 {phone} не сработал* — пробую следующий...\n"
                            f"`{str(msg)[:200]}`")

        # 4. Обработать результат
        if ok:
            link = ""
            try:
                meta_file = profile_path / ".profile_meta.json"
                if meta_file.exists():
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    link = (meta.get("black_activation_link") or
                            meta.get("activation_url") or "")
            except Exception:
                pass

            if link:
                tpl = get_template("msg_template")
                try:
                    buyer_msg = tpl.format(link=link)
                except Exception:
                    buyer_msg = f"{tpl}\n\n{link}"
                if gg_months >= 6:
                    buyer_msg += (
                        "\n\n⚠️ При покупке 6 месяцев нужно будет через 3 месяца заново активировать подписку.\n"
                        "Обратитесь ко мне в чат — скажите, что первые 3 месяца прошли. Выдам новую ссылку на активацию."
                    )

                try:
                    await cli.send_message(invoice_id, buyer_msg)
                    self.mark_done(invoice_id, link)
                    logger.success(f"GGSell auto #{invoice_id}: ссылка отправлена покупателю")
                except Exception as exc:
                    logger.error(f"GGSell auto #{invoice_id}: ошибка отправки ссылки: {exc}")

                try:
                    self._m("_save_meta_field")(profile_path, issued_ts=time.time())
                except Exception:
                    pass

                for _cid in list(self.subs):
                    await self._send(_cid,
                        f"✅ *Заказ #{invoice_id} выполнен автоматически!*\n"
                        f"📱 `+91 {phone}`\n"
                        f"🔗 Ссылка отправлена покупателю")
            else:
                for _cid in list(self.subs):
                    await self._send(_cid,
                        f"⚠️ *Заказ #{invoice_id}* — покупка прошла, но ссылка не получена\n"
                        f"📱 `+91 {phone}`\n"
                        f"Проверьте профиль и выдайте ссылку вручную\n"
                        f"_{msg}_")
        else:
            logger.error(f"GGSell auto #{invoice_id}: все профили исчерпаны: {msg}")
            for _cid in list(self.subs):
                await self._send(_cid,
                    f"❌ *Авто-выполнение заказа #{invoice_id} не удалось*\n"
                    f"Все доступные профили исчерпаны\n"
                    f"`{str(msg)[:300]}`")

    async def bg_run(self, cid, mid, invoice_id: int) -> None:
        item        = self.orders.get(invoice_id, {})
        buyer_email = item.get("buyer_email") or "?"

        if buyer_email == "?":
            try:
                cli = self.get_client()
                if cli:
                    fetched = await cli.get_buyer_email(invoice_id)
                    if fetched:
                        buyer_email = fetched
                        self.orders.setdefault(invoice_id, {})["buyer_email"] = fetched
            except Exception:
                pass

        await self._edit(cid, mid,
            f"⏳ *Выполняю заказ* `#{invoice_id}`\n\n"
            f"📧 Покупатель: `{buyer_email}`\n\n"
            "_Запускаю автоматизацию — создаю профиль..._\n"
            "_Это займёт несколько минут._",
            {"inline_keyboard": []})

        def _profiles_with_link() -> list:
            """Профили (dict из _load_done_profiles) с готовой ссылкой, не выданные."""
            try:
                res = []
                for p in (self._m("_load_done_profiles")() or []):
                    if p.get("issued_ts"):
                        continue
                    lnk = p.get("black_activation_link") or p.get("black_short_link") or ""
                    if lnk:
                        res.append(p)
                return res
            except Exception:
                return []

        before_paths = {str(p["path"]) for p in _profiles_with_link()}

        args = [
            sys.executable,
            str(self._root / "main.py"),
            "--tg-login", "--accounts", "1",
        ]
        try:
            import os
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            loop = asyncio.get_running_loop()
            proc = await loop.run_in_executor(
                None, lambda: subprocess.Popen(args, creationflags=creationflags))
            await loop.run_in_executor(None, proc.wait)
            code = proc.returncode
        except Exception as exc:
            await self._send(cid, f"❌ Ошибка запуска автоматизации (заказ `#{invoice_id}`): {exc}")
            return

        if code != 0:
            await self._send(cid,
                f"⚠️ Автоматизация завершилась с кодом {code} (заказ `#{invoice_id}`).\n"
                "Проверьте /logs")
            return

        # Ищем профиль, появившийся со ссылкой после запуска (самый свежий)
        after = _profiles_with_link()
        new_profiles = [p for p in after if str(p["path"]) not in before_paths]
        target = None
        if new_profiles:
            target = max(new_profiles, key=lambda p: p.get("login_ts") or 0)
        elif after:
            target = max(after, key=lambda p: p.get("login_ts") or 0)

        if target:
            link = target.get("black_activation_link") or target.get("black_short_link") or ""
            self.confirm[invoice_id] = link
            self._confirm_profile[invoice_id] = str(target["path"])
            await self._send(cid,
                f"✅ *Ссылка для заказа* `#{invoice_id}` *готова!*\n\n"
                f"🔗 `{link}`\n\n"
                f"📧 Покупатель: `{buyer_email}`\n\n"
                "Отправить ссылку покупателю в чат GGSell?",
                reply_markup={"inline_keyboard": [
                    [{"text": "📤 Отправить покупателю",
                      "callback_data": f"ggsell:send:{invoice_id}"}],
                    [{"text": "❌ Не отправлять",
                      "callback_data": f"ggsell:nosend:{invoice_id}"}],
                ]})
        else:
            await self._send(cid,
                f"⚠️ Заказ `#{invoice_id}`: автоматизация завершена, но ссылка в профиле не найдена.\n\n"
                "_Проверьте профиль и отправьте ссылку вручную._")

    # ── Шаблоны в ответе покупателю ──────────────────────────────────────────

    def _reply_prompt_kb(self, invoice_id: int) -> dict:
        """Клавиатура приглашения «напишите сообщение»: шаблоны + отмена."""
        return {"inline_keyboard": [
            [{"text": "📝 Шаблоны", "callback_data": f"ggsell:reply_tpl_list:{invoice_id}"}],
            [{"text": "❌ Отмена", "callback_data": f"ggsell:reply_cancel:{invoice_id}"}],
        ]}

    def _render_template_for_order(self, name: str, invoice_id: int) -> str:
        """Текст шаблона с подстановкой {link} (из заказа/привязанного профиля)."""
        from ggsell.monitor import get_template
        text = get_template(name) or ""
        if "{link}" in text:
            link = self.confirm.get(invoice_id) or self.get_sent_link(invoice_id) or ""
            if not link:
                bound = self.get_bound_profile(invoice_id)
                if bound:
                    meta = self._read_bound_meta(bound)
                    link = (meta.get("black_short_link") or meta.get("issued_link")
                            or meta.get("black_activation_link") or "")
            text = text.replace("{link}", link)
        return text

    def reply_templates_page(self, invoice_id: int) -> tuple:
        """Список шаблонов для отправки в чат покупателю."""
        lines = [
            f"📝 *Шаблоны* · заказ `#{invoice_id}`",
            "",
            "Выбери шаблон — покажу текст и кнопку «Отправить в чат».",
        ]
        rows = [[{"text": f"📄 {label}",
                  "callback_data": f"ggsell:reply_tpl:{invoice_id}:{key}"}]
                for key, (label, _desc) in self._TEMPLATE_NAMES.items()]
        rows.append([{"text": "◀️ Назад", "callback_data": f"ggsell:reply_back:{invoice_id}"}])
        return "\n".join(lines), {"inline_keyboard": rows}

    def reply_template_preview(self, invoice_id: int, name: str) -> tuple:
        """Предпросмотр шаблона + кнопка «Отправить в чат»."""
        label, _desc = self._TEMPLATE_NAMES.get(name, (name, ""))
        text = self._render_template_for_order(name, invoice_id)
        preview = text[:500] + "…" if len(text) > 500 else text
        lines = [
            f"📄 *Шаблон: {label}* · заказ `#{invoice_id}`",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            preview, "",
            "_Отправить этот текст покупателю в чат?_",
        ]
        kb = {"inline_keyboard": [
            [{"text": "📤 Отправить в чат",
              "callback_data": f"ggsell:reply_tpl_send:{invoice_id}:{name}"}],
            [{"text": "◀️ К шаблонам",
              "callback_data": f"ggsell:reply_tpl_list:{invoice_id}"}],
        ]}
        return "\n".join(lines), kb

    async def bg_reply_template_send(self, cid: int, mid: int, invoice_id: int, name: str) -> None:
        """Отправить выбранный шаблон в чат покупателю."""
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell клиент не настроен.", {"inline_keyboard": []})
            return
        text = self._render_template_for_order(name, invoice_id)
        label, _desc = self._TEMPLATE_NAMES.get(name, (name, ""))
        back_kb = {"inline_keyboard": [
            [{"text": "◀️ К шаблонам", "callback_data": f"ggsell:reply_tpl_list:{invoice_id}"}]]}
        try:
            ok = await cli.send_message(invoice_id, text)
        except Exception as exc:
            await self._edit(cid, mid,
                f"❌ Ошибка отправки шаблона (заказ `#{invoice_id}`): {exc}", back_kb)
            return
        if ok:
            self.reply_mode.pop(cid, None)
            preview = text[:300] + "…" if len(text) > 300 else text
            await self._edit(cid, mid,
                f"✅ *Шаблон «{label}» отправлен покупателю!*\n\n"
                f"Заказ: `#{invoice_id}`\n\n{preview}",
                {"inline_keyboard": [
                    [{"text": "💬 Чат",    "callback_data": f"ggsell:chat:{invoice_id}"},
                     {"text": "📋 Заказ",  "callback_data": f"ggsell:order:{invoice_id}"}],
                ]})
        else:
            await self._edit(cid, mid,
                f"⚠️ Не удалось отправить шаблон (заказ `#{invoice_id}`).", back_kb)

    async def bg_reply(self, cid, invoice_id: int, text: str) -> None:
        cli = self.get_client()
        if not cli:
            await self._send(cid, "❌ GGSell клиент не настроен.")
            return

        async def _re_ask():
            self.reply_mode[cid] = invoice_id
            await self._send(cid,
                f"💬 *Ответ покупателю* · заказ `#{invoice_id}`\n\n"
                "Напишите сообщение — оно будет отправлено покупателю в чат GGSell:",
                reply_markup=self._reply_prompt_kb(invoice_id))

        try:
            ok = await cli.send_message(invoice_id, text)
        except Exception as exc:
            await self._send(cid, f"❌ Ошибка отправки (заказ `#{invoice_id}`): {exc}")
            await _re_ask()
            return
        if ok:
            await self._send(cid,
                f"✅ Сообщение отправлено покупателю!\n\n"
                f"Заказ: `#{invoice_id}`\n"
                f"_{text}_")
        else:
            await self._send(cid,
                f"⚠️ Не удалось отправить (заказ `#{invoice_id}`) — попробуйте ещё раз.")
            await _re_ask()

    async def bg_send(self, cid, invoice_id: int) -> None:
        link = self.confirm.pop(invoice_id, None)
        if not link:
            await self._send(cid, f"❌ Ссылка для заказа `#{invoice_id}` не найдена.")
            return
        cli = self.get_client()
        if not cli:
            await self._send(cid, "❌ GGSell клиент не настроен.")
            return
        item        = self.orders.get(invoice_id, {})
        buyer_email = item.get("buyer_email") or "?"
        try:
            from ggsell.monitor import get_template
            ok = await cli.send_message(invoice_id, get_template("msg_template").format(link=link))
        except Exception as exc:
            await self._send(cid, f"❌ Ошибка отправки в GGSell (заказ `#{invoice_id}`): {exc}")
            return
        if ok:
            self.mark_done(invoice_id, link)
            await self._send(cid,
                f"✅ *Ссылка отправлена покупателю!*\n\n"
                f"Заказ: `#{invoice_id}` · ✅ Выполнено\n"
                f"📧 {buyer_email}\n"
                f"🔗 `{link}`")
        else:
            await self._send(cid,
                f"⚠️ Не удалось отправить сообщение в GGSell (заказ `#{invoice_id}`).")

    # ── Уведомления (из monitor queue / webhook queue) ────────────────────────

    async def notify_order(self, item: dict) -> None:
        invoice_id  = item.get("invoice_id")
        order       = dict(item.get("order", {}))
        self.orders[invoice_id] = item

        try:
            cli = self.get_client()
            if cli:
                info = await cli.get_order_info(invoice_id)
                content = (info.get("content") if isinstance(info, dict) else None) or {}
                for fld in ("sum_t", "sum", "amount", "sum_seller", "profit", "status"):
                    if content.get(fld) is not None and not order.get(fld):
                        order[fld] = content[fld]
                bi = content.get("buyer_info") or {}
                if bi.get("email") and not order.get("email"):
                    order.setdefault("buyer", {})["email"] = bi["email"]
                if content.get("options") and not order.get("options"):
                    order["options"] = content["options"]
                if content.get("selected_options") and not order.get("selected_options"):
                    order["selected_options"] = content["selected_options"]
                item["order"] = order
                self.orders[invoice_id] = item

                try:
                    v2 = await cli.get_order_info_v2(invoice_id)
                    if v2:
                        if v2.get("selected_options") and not order.get("selected_options"):
                            order["selected_options"] = v2["selected_options"]
                        if v2.get("buyer_email") and not order.get("buyer_email"):
                            order["buyer_email"] = v2["buyer_email"]
                        if v2.get("seller_reward_amount") and not order.get("sum_seller"):
                            order["sum_seller"] = v2["seller_reward_amount"]
                        if v2.get("amount") and not order.get("sum_t"):
                            order["sum_t"] = v2["amount"]
                        if v2.get("unique_code"):
                            order["unique_code"] = v2["unique_code"]
                        if v2.get("created_at") and not order.get("date"):
                            order["date"] = v2["created_at"]
                        if v2.get("offer_title") and not order.get("name"):
                            order["name"] = v2["offer_title"]
                        item["order"] = order
                        self.orders[invoice_id] = item
                except Exception:
                    pass
        except Exception:
            pass

        p     = self.parse_order(order)
        email = item.get("buyer_email") or p["email"]

        head_parts = [f"*#{invoice_id}*"]
        if p["date"]:
            head_parts.append(p["date"][5:10])
        if p["sum_buy"]:
            head_parts.append(f"💰 *{p['sum_buy']}₽*")
        if p["sum_sell"]:
            head_parts.append(f"💼 *{p['sum_sell']}₽*")
        lines = [
            f"🟢 *Новый заказ* — {'  ·  '.join(head_parts)}",
            f"📦 {p['name_short']}",
        ]
        if email:
            lines.append(f"👤 `{email}`")
        if p["options"]:
            lines.append("")
            for opt in p["options"]:
                n_s = opt["name"][:28] + "…" if len(opt["name"]) > 28 else opt["name"]
                v_s = opt["value"][:40] + "…" if len(opt["value"]) > 40 else opt["value"]
                p_s = f" _(+{opt['price_add']}₽)_" if opt.get("price_add") else ""
                lines.append(f"• _{n_s}_: `{v_s}`{p_s}")
        lines.append("")
        text = "\n".join(lines)
        kb = {"inline_keyboard": [
            [{"text": "▶️ Выполнить заказ",
              "callback_data": f"ggsell:run:{invoice_id}"}],
            [{"text": f"📋 Детали #{invoice_id}",
              "callback_data": f"ggsell:order:{invoice_id}"}],
        ]}
        for _cid in list(self.subs):
            if not self._get(_cid, "ggsel_notify_orders"):
                continue
            try:
                await self._http.post(f"{self._api}/sendMessage",
                                      json={"chat_id": _cid, "text": text,
                                            "parse_mode": "Markdown", "reply_markup": kb})
            except Exception:
                pass

        # При ЛЮБОМ новом заказе сразу шлём приветствие покупателю в чат GGSell.
        try:
            import datetime as _dtg
            from ggsell.monitor import get_template as _gt
            _greet = _gt("msg_greeting")
            _cli2 = self.get_client()
            if _greet and _cli2 and invoice_id not in self._greeted_sent:
                await _cli2.send_message(invoice_id, _greet)
                self._greeted_sent[invoice_id] = _dtg.datetime.now().strftime("%Y-%m-%d %H:%M")
                logger.info(f"GGSell #{invoice_id}: приветствие отправлено покупателю")
        except Exception as exc:
            logger.warning(f"GGSell #{invoice_id}: ошибка приветствия: {exc}")

        # Авто-выполнение: профиль НЕ резервируем заранее (и не пишем какой).
        # Просто ждём первого сообщения покупателя — тогда и подбираем профиль
        # по приоритету (Оплаченные → С данными → Доступные → новый).
        if (any(self._get(_cid, "ggsel_auto_fulfill") for _cid in list(self.subs))
                and invoice_id not in self.get_refunded()):
            self._auto_pending[invoice_id] = order
            logger.info(f"GGSell auto #{invoice_id}: ждём первого сообщения от покупателя")

    async def notify_message(self, item: dict) -> None:
        invoice_id = item.get("invoice_id")
        msg        = item.get("message", {})
        chat       = item.get("chat", {})
        # email покупателя: сначала из author.email сообщения, потом из chat
        email = (
            item.get("buyer_email")
            or (msg.get("author") or {}).get("email")
            or chat.get("email")
            or "?"
        )
        msg_text   = (msg.get("text") or msg.get("message") or msg.get("body") or "…")
        if len(msg_text) > 300:
            msg_text = msg_text[:300] + "…"

        raw_date = (msg.get("date") or msg.get("created_at") or msg.get("timestamp")
                    or msg.get("date_add") or "")
        msg_time = str(raw_date)[:16].replace("T", " ") if raw_date else ""

        is_seller = bool(item.get("is_seller"))
        time_s = f"  │  _{msg_time}_" if msg_time else ""
        _header = ("📤 *Сообщение отправлено покупателю* (от вас)"
                   if is_seller else "💬 *Новое сообщение от покупателя*")
        text = (
            f"{_header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"*Заказ:* `#{invoice_id}`  │  👤 `{email}`{time_s}\n\n"
            f"{msg_text}"
        )

        kb_rows = [
            [{"text": "💬 Ответить",
              "callback_data": f"ggsell:reply:{invoice_id}"},
             {"text": f"📋 Заказ #{invoice_id}",
              "callback_data": f"ggsell:order:{invoice_id}"}],
        ]
        # Кнопку «Начать выполнение» и авто-режим — ТОЛЬКО для сообщений покупателя.
        _will_auto = False
        if not is_seller:
            _will_auto = (invoice_id in self._auto_pending and
                          any(self._get(_c, "ggsel_auto_fulfill") for _c in list(self.subs)))
            _is_pending = bool(invoice_id) and (invoice_id not in self.get_done()) \
                and (invoice_id not in self.get_refunded()) \
                and not self.get_bound_profile(invoice_id)
            if _is_pending and not _will_auto:
                kb_rows.insert(0, [{"text": "▶️ Начать выполнение заказа",
                                    "callback_data": f"ggsell:run:{invoice_id}"}])
                text += "\n\n_⚠️ Профиль не привязан, ссылка не выдана. Начать выполнение?_"
        kb = {"inline_keyboard": kb_rows}
        for _cid in list(self.subs):
            if not self._get(_cid, "ggsel_notify_messages"):
                continue
            try:
                await self._http.post(f"{self._api}/sendMessage",
                                      json={"chat_id": _cid, "text": text,
                                            "parse_mode": "Markdown", "reply_markup": kb})
            except Exception:
                pass

        # Авто-выполнение: ТОЛЬКО по сообщению ПОКУПАТЕЛЯ (не своему), по ожидающему
        # заказу. Подбор профиля по приоритету (Оплаченные → С данными → Доступные → новый).
        if not is_seller and invoice_id and invoice_id in self._auto_pending:
            _order = self._auto_pending.pop(invoice_id)
            if any(self._get(_cid, "ggsel_auto_fulfill") for _cid in list(self.subs)):
                asyncio.create_task(self.bg_fulfill_order(invoice_id, _order))

    # ── Отзывы ───────────────────────────────────────────────────────────────

    @staticmethod
    def parse_review(r: dict) -> dict:
        """Нормализует сырой dict отзыва в единый формат."""
        invoice_id = int(r.get("invoice_id") or r.get("id_i") or r.get("order_id") or 0)
        # Числовой рейтинг (v1 API) или из type/feedback_type (старый API)
        rating = int(r.get("rating") or r.get("score") or r.get("stars") or 0)
        if not rating:
            ft = str(r.get("type") or r.get("feedback_type") or "").lower()
            if any(w in ft for w in ("positive", "good", "great", "excellent")):
                rating = 5
            elif any(w in ft for w in ("negative", "bad")):
                rating = 1
        # Текст отзыва: "info" в старом API, "text" в других
        # "comment" = ответ продавца — не берём
        text  = str(r.get("text") or r.get("info") or r.get("review") or r.get("body") or "").strip()
        date  = str(r.get("date") or r.get("created_at") or r.get("date_add") or "").replace("T", " ")[:16]
        email = str(r.get("email") or r.get("buyer_email") or
                    (r.get("buyer") or {}).get("email") or "").strip()
        rid   = str(r.get("id") or r.get("review_id") or r.get("feedback_id") or "")
        # Тип для отображения: positive / negative
        rtype = str(r.get("type") or r.get("feedback_type") or "").lower()
        return {"invoice_id": invoice_id, "rating": rating, "text": text,
                "date": date, "email": email, "id": rid, "type": rtype}

    @staticmethod
    def _stars(rating: int) -> str:
        if not rating:
            return ""
        n = max(1, min(5, rating))
        return "⭐" * n + "☆" * (5 - n) + f"  {n}/5"

    async def _get_review_promo_code(self, cli) -> str:
        """Вернуть промокод для отзыва — берётся из константы REVIEW_PROMO_CODE."""
        from ggsell.monitor import REVIEW_PROMO_CODE
        return REVIEW_PROMO_CODE

    async def notify_review(self, item: dict) -> None:
        invoice_id = item.get("invoice_id")
        r          = self.parse_review(item.get("review", {}))
        stars      = self._stars(r["rating"])
        type_icon  = "✅" if r["type"] == "positive" or r["rating"] >= 4 else (
                     "❌" if r["type"] == "negative" or (r["rating"] and r["rating"] <= 2) else "⭐")
        lines = [f"{type_icon} *Новый отзыв от покупателя*"]
        if invoice_id:
            lines.append(f"Заказ: `#{invoice_id}`")
        if r["email"]:
            lines.append(f"👤 `{r['email']}`")
        if r["date"]:
            lines.append(f"📅 {r['date']}")
        if stars:
            lines.append(f"\n{stars}")
        if r["text"]:
            lines.append(f"\n_{r['text'][:400]}_")

        promo_sent = False
        # Если 5 звёзд — автоматически отправить промокод покупателю
        if r["rating"] == 5 and invoice_id:
            cli = self.get_client()
            if cli:
                try:
                    promo_code = await self._get_review_promo_code(cli)
                    if promo_code:
                        from ggsell.monitor import get_template
                        msg = get_template("msg_review_promo").format(promo_code=promo_code)
                        ok = await cli.send_message(invoice_id, msg)
                        if ok:
                            promo_sent = True
                            lines.append(f"\n🎁 _Промокод `{promo_code}` отправлен покупателю_")
                        else:
                            lines.append("\n⚠️ _Не удалось отправить промокод покупателю_")
                    else:
                        lines.append("\n⚠️ _Нет активного промокода для отправки_")
                except Exception as exc:
                    logger.error(f"GGSell notify_review promo: {exc}")
                    lines.append(f"\n⚠️ _Ошибка при отправке промокода: {exc}_")

        text = "\n".join(lines)
        kb_rows = []
        if invoice_id:
            kb_rows.append([{"text": f"📋 Заказ #{invoice_id}",
                              "callback_data": f"ggsell:order:{invoice_id}"}])
        kb = {"inline_keyboard": kb_rows}
        for _cid in list(self.subs):
            if not self._get(_cid, "ggsel_notify_reviews"):
                continue
            try:
                await self._http.post(f"{self._api}/sendMessage",
                                      json={"chat_id": _cid, "text": text,
                                            "parse_mode": "Markdown", "reply_markup": kb})
            except Exception:
                pass

    @staticmethod
    def _offer_status_icon(status: str) -> str:
        return {"active": "🟢", "paused": "🟡", "archived": "🔴"}.get(status, "⚪")

    @staticmethod
    def _offer_status_ru(status: str) -> str:
        return {"active": "активен", "paused": "приостановлен", "archived": "архив"}.get(status, status or "—")

    async def bg_offers_page(self, cid: int, mid: int) -> None:
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return
        try:
            offers = await cli.get_offers()
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка загрузки офферов: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        if not offers:
            await self._edit(cid, mid, "📦 *GGSell — Офферы*\n\n_Нет офферов._",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        lines = ["📦 *GGSell — Офферы*", ""]
        kb_rows = []
        for off in offers:
            oid    = int(off.get("id") or 0)
            title  = str(off.get("title_ru") or off.get("title_en") or f"Оффер #{oid}")
            status = off.get("status") or ""
            icon   = self._offer_status_icon(status)
            st_ru  = self._offer_status_ru(status)
            short  = title[:50] + "…" if len(title) > 50 else title

            # Цена и остаток
            price = off.get("price")
            price_s = f"  ·  💰 {int(price)}₽" if price else ""
            if off.get("is_unlimited_quantity"):
                qty_s = "  ·  ∞ в наличии"
            else:
                qty = off.get("quantity") or off.get("in_stock_products_count") or 0
                qty_s = f"  ·  📦 {qty} шт." if qty else ""

            lines.append(f"{icon} *{short}*")
            lines.append(f"   _{st_ru}_{price_s}{qty_s}")
            lines.append("")

            if status == "active":
                kb_rows.append([{"text": f"⏸ Стоп · {short[:32]}",
                                 "callback_data": f"ggsell:offer_toggle:{oid}:paused"}])
            elif status in ("paused", "draft", ""):
                kb_rows.append([{"text": f"▶️ Старт · {short[:32]}",
                                 "callback_data": f"ggsell:offer_toggle:{oid}:active"}])

        kb_rows.append([{"text": "◀️ Назад", "callback_data": "go:ggsell"}])
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_offer_toggle(self, cid: int, mid: int, offer_id: int, new_status: str) -> None:
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Офферы", "callback_data": "ggsell:offers"}]]})
            return
        ok = await cli.set_offer_status(offer_id, new_status)
        if ok:
            icon = self._offer_status_icon(new_status)
            st_ru = self._offer_status_ru(new_status)
            await self._edit(cid, mid,
                f"✅ Оффер `#{offer_id}` → {icon} *{st_ru}*",
                {"inline_keyboard": [[{"text": "📦 Офферы", "callback_data": "ggsell:offers"}]]})
        else:
            await self._edit(cid, mid,
                f"❌ Не удалось изменить статус оффера `#{offer_id}`.",
                {"inline_keyboard": [[{"text": "📦 Офферы", "callback_data": "ggsell:offers"}]]})

    async def bg_reviews_page(self, cid: int, mid: int) -> None:
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return
        try:
            reviews_raw = await cli.get_reviews(limit=50)
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка загрузки отзывов: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        reviews = [self.parse_review(r) for r in reviews_raw]
        reviews.sort(key=lambda r: (r["date"] or "", r["invoice_id"]), reverse=True)

        total    = len(reviews)
        positive = sum(1 for r in reviews if r["type"] == "positive" or r["rating"] >= 4)
        negative = sum(1 for r in reviews if r["type"] == "negative" or (r["rating"] and r["rating"] <= 2))

        lines = ["⭐ *GGSell — Отзывы*", ""]
        if total:
            stat = f"_Всего: {total}"
            if positive:
                stat += f"  ·  ✅ {positive}"
            if negative:
                stat += f"  ·  ❌ {negative}"
            lines.append(stat + "_")
            lines.append("")

        for r in reviews[:15]:
            # Заголовок строки
            if r["type"] == "positive":
                type_icon = "✅"
            elif r["type"] == "negative":
                type_icon = "❌"
            elif r["rating"] >= 4:
                type_icon = "✅"
            elif r["rating"] and r["rating"] <= 2:
                type_icon = "❌"
            else:
                type_icon = "⭐"

            inv_s  = f" `#{r['invoice_id']}`" if r["invoice_id"] else ""
            date_s = f"  ·  _{r['date']}_" if r["date"] else ""
            stars  = self._stars(r["rating"])
            lines.append(f"{type_icon}{inv_s}{date_s}")
            if stars:
                lines.append(f"   {stars}")
            if r["email"]:
                lines.append(f"   👤 `{r['email']}`")
            if r["text"]:
                preview = r["text"][:120] + "…" if len(r["text"]) > 120 else r["text"]
                safe = preview.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
                lines.append(f"   _{safe}_")
            lines.append("")

        if not reviews:
            lines.append("_Отзывов пока нет_")

        kb = {"inline_keyboard": [
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]}
        await self._edit(cid, mid, "\n".join(lines), kb)

    async def bg_order_review(self, cid: int, mid: int, invoice_id: int) -> None:
        """Показать отзыв на конкретный заказ."""
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Заказ",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return
        try:
            raw = await cli.get_order_review(invoice_id)
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка: {exc}",
                {"inline_keyboard": [[{"text": "◀️ Заказ",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return

        if not raw:
            await self._edit(cid, mid,
                f"⭐ *Отзыв на заказ #{invoice_id}*\n\n_Отзыв не найден._",
                {"inline_keyboard": [[{"text": "◀️ Заказ",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return

        logger.debug(f"GGSell review raw #{invoice_id}: {raw}")
        r = self.parse_review(raw)
        lines = [
            f"⭐ *Отзыв на заказ* `#{invoice_id}`",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
        ]
        if r["rating"]:
            lines.append(self._stars(r["rating"]))
        if r["email"]:
            lines.append(f"👤 `{r['email']}`")
        if r["date"]:
            lines.append(f"📅 {r['date']}")
        if r["text"]:
            # Экранируем символы которые ломают Markdown
            safe = r["text"].replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
            lines.append(f"\n{safe}")
        elif raw:
            # Если parse_review не нашёл текст — показываем всё что есть в raw
            for fld in ("message", "description", "answer", "note", "feedback", "content"):
                val = str(raw.get(fld) or "").strip()
                if val:
                    lines.append(f"\n{val}")
                    break
        kb = {"inline_keyboard": [[
            {"text": "◀️ Заказ", "callback_data": f"ggsell:order:{invoice_id}"},
            {"text": "⭐ Все отзывы", "callback_data": "ggsell:reviews"},
        ]]}
        await self._edit(cid, mid, "\n".join(lines), kb)

    # ── Webhook handler ───────────────────────────────────────────────────────

    def make_webhook_handler(self, webhook_queue: asyncio.Queue, aio_web):
        """Вернуть aiohttp handler для приёма POST/GET уведомлений от GGSell."""
        handler = self

        async def _webhook_handler(request):
            try:
                if request.method == "POST":
                    try:
                        body = await request.json()
                    except Exception:
                        return aio_web.Response(text="Bad Request", status=400)
                    params = {
                        "id_i":        str(body.get("ID_I") or body.get("id_i") or ""),
                        "id_d":        str(body.get("ID_D") or body.get("id_d") or ""),
                        "amount":      str(body.get("Amount") or body.get("amount") or ""),
                        "curr":        str(body.get("Currency") or body.get("curr") or ""),
                        "email":       str(body.get("email") or ""),
                        "date":        str(body.get("Date") or body.get("date") or ""),
                        "sha256":      str(body.get("SHA256") or body.get("sha256") or ""),
                        "ip":          str(body.get("IP") or body.get("ip") or ""),
                        "isMyProduct": str(body.get("IsMyProduct") or body.get("isMyProduct") or ""),
                    }
                else:
                    params = {k: str(v) for k, v in request.rel_url.query.items()}

                invoice_id = int(params.get("id_i") or 0)
                product_id = int(params.get("id_d") or 0)
                amount     = params.get("amount", "")
                currency   = params.get("curr", "")
                email      = params.get("email", "")
                date_s     = params.get("date", "")
                sha256_recv = params.get("sha256", "")

                if not invoice_id:
                    return aio_web.Response(text="OK")

                # SHA256 верификация через unique_code заказа (per-order)
                if sha256_recv:
                    try:
                        from ggsell.client import GGSellClient as _GSC
                        _ggs2 = (handler._m("_read_secrets")().get("ggsel") or {})
                        _gsc = _GSC(
                            api_key=_ggs2.get("api_key", ""),
                            seller_id=int(_ggs2.get("seller_id") or 0),
                        )
                        _v2ord = await _gsc.get_order_info_v2(invoice_id)
                        _u_code = (_v2ord.get("unique_code") or "").strip()
                        if _u_code:
                            _exp = hashlib.sha256(
                                f"{_u_code};{invoice_id};{product_id}".encode()
                            ).hexdigest()
                            if _exp.lower() != sha256_recv.lower():
                                await _gsc.close()
                                return aio_web.Response(text="Forbidden", status=403)
                        await _gsc.close()
                    except Exception:
                        pass

                # Добавляем в processed чтобы монитор не продублировал
                try:
                    from ggsell.monitor import _load_processed, _save_processed
                    _proc_set = _load_processed()
                    if invoice_id not in _proc_set:
                        _proc_set.add(invoice_id)
                        _save_processed(_proc_set)
                except Exception:
                    pass

                order = {
                    "invoice_id": invoice_id,
                    "id":         invoice_id,
                    "product":    {"id": product_id, "name": "YouTube Premium"},
                    "sum_t":      amount,
                    "email":      email,
                    "date":       date_s,
                    "buyer":      {"email": email},
                    "currency":   currency,
                }
                webhook_queue.put_nowait({
                    "type":        "new_order",
                    "invoice_id":  invoice_id,
                    "order":       order,
                    "buyer_email": email,
                })
                return aio_web.Response(text="OK")
            except Exception:
                return aio_web.Response(text="Error", status=500)

        return _webhook_handler

    # ── Обработка текстовых сообщений (режим ответа) ─────────────────────────

    async def _delete_after(self, cid: int, mid: int, delay: float = 3.0) -> None:
        await asyncio.sleep(delay)
        try:
            await self._http.post(f"{self._api}/deleteMessage",
                                  json={"chat_id": cid, "message_id": mid})
        except Exception:
            pass

    def check_reply_mode(self, cid: int, text: str) -> Optional[int]:
        """Если cid в режиме ответа GGSell, извлечь invoice_id и выйти из режима.
        Возвращает invoice_id или None."""
        if cid in self.reply_mode and text and not text.startswith("/"):
            return self.reply_mode.pop(cid)
        return None

    # ── Главный диспетчер callback ────────────────────────────────────────────

    async def handle_callback(self, cid: int, mid: int, qid: str, data: str) -> None:
        """Обработать все ggsell: и go:ggsell callback-команды."""

        # Нажатие любой кнопки = пользователь больше не вводит текст. Сбрасываем
        # все режимы ожидания ввода, чтобы кнопки «Отмена» реально отменяли ввод
        # (иначе card_order/template-отмена оставляла бота ждущим, и следующий
        # текст перехватывался зря). Обработчики-сеттеры ниже заново выставят свой
        # режим после сброса. В callback'ах эти словари только пишутся, не читаются.
        self.reply_mode.pop(cid, None)
        self.template_edit_mode.pop(cid, None)
        self.card_order_mode.pop(cid, None)

        if data in ("go:ggsell", "ggsell:refresh"):
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ *GGSell* — загружаю данные...",
                             {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": "go:other"}]]})
            asyncio.create_task(self.bg_info(cid, mid))
            return

        if data == "ggsell:orders" or data.startswith("ggsell:orders:"):
            await self._ack(qid)
            offset = 0
            if data.startswith("ggsell:orders:"):
                try:
                    offset = int(data.split(":", 2)[2])
                except Exception:
                    offset = 0
            await self._edit(cid, mid, "⏳ *GGSell* — загружаю заказы...",
                             {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": "go:ggsell"}]]})
            asyncio.create_task(self.bg_orders_page(cid, mid, offset=offset))
            return

        if data == "ggsell:chats":
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ *GGSell* — загружаю чаты...",
                             {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": "go:ggsell"}]]})
            asyncio.create_task(self.bg_chats_page(cid, mid))
            return

        if data == "ggsell:settings":
            await self._ack(qid)
            txt, kb = self.settings_page(cid)
            await self._edit(cid, mid, txt, kb)
            return

        if data == "ggsell:reviews":
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ Загружаю отзывы...",
                             {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            asyncio.create_task(self.bg_reviews_page(cid, mid))
            return

        if data.startswith("ggsell:review_order:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid)
            await self._edit(cid, mid, f"⏳ Загружаю отзыв на `#{invoice_id}`...",
                             {"inline_keyboard": [[{"text": "◀️ Заказ",
                                                     "callback_data": f"ggsell:order:{invoice_id}"}]]})
            asyncio.create_task(self.bg_order_review(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:chat:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid)
            await self._edit(cid, mid, f"⏳ Загружаю чат `#{invoice_id}`...",
                             {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": f"ggsell:order:{invoice_id}"}]]})
            asyncio.create_task(self.bg_chat(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:order:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid)
            await self._edit(cid, mid, f"⏳ Загружаю заказ `#{invoice_id}`…",
                             {"inline_keyboard": [[{"text": "◀️ Заказы",
                                                     "callback_data": "ggsell:orders"}]]})
            asyncio.create_task(self.bg_order_view(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:link_history:"):
            invoice_id = int(data.split(":")[2])
            bound = self.get_bound_profile(invoice_id)
            meta: dict = {}
            phone_lh = ""
            if bound:
                phone_lh = Path(bound).name.replace("profile_", "")
                meta = self._read_bound_meta(bound)
                if not meta:
                    # Профиль мог быть перенесён в архив — берём мету из записи архива
                    try:
                        _used_dir = Path(bound).parent.parent / "chrome_profiles_used"
                        _recs = sorted(_used_dir.glob(f"record_*{phone_lh}*.json"),
                                       reverse=True)
                        if _recs:
                            meta = json.loads(_recs[0].read_text(encoding="utf-8"))
                    except Exception:
                        pass
            hist = meta.get("link_history") if isinstance(meta.get("link_history"), list) else []
            if not hist:
                _cur = (meta.get("black_short_link") or meta.get("issued_link")
                        or meta.get("black_activation_link") or self.get_sent_link(invoice_id))
                if _cur:
                    hist = [{"ts": meta.get("link_received_ts") or meta.get("issued_ts") or 0,
                             "link": _cur}]
            if not hist:
                await self._ack(qid, "⚠️ Ссылок по этому заказу ещё не было", alert=True)
                return
            from datetime import datetime as _dt_lh, timezone as _tz_lh, timedelta as _td_lh
            _msk = _tz_lh(_td_lh(hours=3))
            lines = [f"📜 *История ссылок* · заказ `#{invoice_id}`"]
            if phone_lh:
                lines.append(f"📱 `{self._disp_phone(phone_lh)}`")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            _tail = hist[-15:]
            for i, h in enumerate(_tail, 1):
                ts = h.get("ts") or 0
                try:
                    dts = _dt_lh.fromtimestamp(float(ts), _msk).strftime("%d.%m.%Y %H:%M") if ts else "—"
                except Exception:
                    dts = "—"
                mark = "  ← текущая" if i == len(_tail) else ""
                lines.append(f"\n{i}. 🕒 `{dts}`{mark}")
                lines.append(f"`{h.get('link') or ''}`")
            if len(hist) > 15:
                lines.append(f"\n_…показаны последние 15 из {len(hist)}_")
            await self._ack(qid)
            await self._send(cid, "\n".join(lines))
            return

        if data == "ggsell:fulfill_all":
            await self._ack(qid, "🚀 Запускаю все...")
            await self._edit(cid, mid,
                "🚀 *Загружаю невыданные заказы...*",
                {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]})
            asyncio.create_task(self.bg_fulfill_all(cid, mid))
            return

        if data.startswith("ggsell:run:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid, "⏳ Подбираю профиль...")
            _ord = (self.orders.get(invoice_id, {}) or {}).get("order", {})
            await self._edit(cid, mid,
                f"⏳ *Выполняю заказ* `#{invoice_id}`\n\n_Подбираю профиль по приоритету:_\n"
                f"_Оплаченные → С данными → Доступные → новый._",
                {"inline_keyboard": [[{"text": "❌ Отмена выполнения",
                                       "callback_data": f"ggsell:fulfill_cancel:{invoice_id}"}]]})
            asyncio.create_task(self.bg_fulfill_order(invoice_id, _ord, cid, mid))
            return

        if data.startswith("ggsell:fulfill_cancel:"):
            invoice_id = int(data.split(":")[2])
            self._fulfill_cancel.add(invoice_id)
            # Флаг для menu.py — прерывает уже запущенную покупку/заполнение
            # (закрывает браузер) в долгих ожиданиях/циклах.
            try:
                import importlib as _il
                _il.import_module("menu")._purchase_cancel.set()
            except Exception:
                pass
            # Принудительно убиваем Chrome в фоне — если покупка застряла в
            # долгом await (wait_for_function/wait_for_url), _ckcancel() не
            # вызывается и браузер не закрывается без жёсткого kill.
            try:
                import grizzly as _gz_c, threading as _thr_c
                _thr_c.Thread(target=_gz_c.kill_all_bot_chrome,
                              daemon=True, name="chrome-kill-cancel").start()
            except Exception:
                pass
            await self._ack(qid, "🛑 Отменяю полностью...")
            await self._edit(cid, mid,
                f"🛑 *Заказ #{invoice_id}*: выполнение отменяется.\n"
                "_Останавливаю покупку и закрываю браузер..._",
                {"inline_keyboard": [[{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}]]})
            return

        if data.startswith("ggsell:send:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid, "⏳ Отправляю...")
            asyncio.create_task(self.bg_send(cid, invoice_id))
            return

        if data.startswith("ggsell:reply:"):
            invoice_id = int(data.split(":")[2])
            self.reply_mode[cid] = invoice_id
            await self._ack(qid)
            await self._send(cid,
                f"💬 *Ответ покупателю* · заказ `#{invoice_id}`\n\n"
                "Напишите сообщение — оно будет отправлено покупателю в чат GGSell:",
                reply_markup=self._reply_prompt_kb(invoice_id))
            return

        if data.startswith("ggsell:reply_back:"):
            invoice_id = int(data.split(":")[2])
            self.reply_mode[cid] = invoice_id
            await self._ack(qid)
            await self._edit(cid, mid,
                f"💬 *Ответ покупателю* · заказ `#{invoice_id}`\n\n"
                "Напишите сообщение — оно будет отправлено покупателю в чат GGSell:",
                self._reply_prompt_kb(invoice_id))
            return

        if data.startswith("ggsell:reply_tpl_list:"):
            invoice_id = int(data.split(":")[2])
            self.reply_mode[cid] = invoice_id
            await self._ack(qid)
            txt, kb = self.reply_templates_page(invoice_id)
            await self._edit(cid, mid, txt, kb)
            return

        if data.startswith("ggsell:reply_tpl_send:"):
            parts = data.split(":")
            invoice_id = int(parts[2])
            name = parts[3]
            await self._ack(qid, "⏳ Отправляю...")
            asyncio.create_task(self.bg_reply_template_send(cid, mid, invoice_id, name))
            return

        if data.startswith("ggsell:reply_tpl:"):
            parts = data.split(":")
            invoice_id = int(parts[2])
            name = parts[3]
            await self._ack(qid)
            txt, kb = self.reply_template_preview(invoice_id, name)
            await self._edit(cid, mid, txt, kb)
            return

        if data.startswith("ggsell:reply_cancel:"):
            invoice_id = int(data.split(":")[2])
            self.reply_mode.pop(cid, None)
            await self._ack(qid, "❌ Отменено")
            await self._edit(cid, mid,
                f"❌ Ответ покупателю · заказ `#{invoice_id}` — отменён.",
                {"inline_keyboard": []})
            asyncio.create_task(self._delete_after(cid, mid, 3.0))
            return

        if data.startswith("ggsell:nosend:"):
            invoice_id = int(data.split(":")[2])
            self.confirm.pop(invoice_id, None)
            await self._ack(qid, "❌ Отправка отменена")
            await self._edit(cid, mid,
                f"❌ Ссылка для заказа `#{invoice_id}` *не отправлена* покупателю.",
                {"inline_keyboard": [
                    [{"text": "◀️ Заказ",  "callback_data": f"ggsell:order:{invoice_id}"},
                     {"text": "◀️ Заказы", "callback_data": "ggsell:orders"}],
                ]})
            return

        if data.startswith("ggsell:mark_done:"):
            invoice_id = int(data.split(":")[2])
            self.mark_done(invoice_id)
            await self._ack(qid, "✅ Отмечено как выдано")
            asyncio.create_task(self.bg_order_view(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:mark_refund:"):
            invoice_id = int(data.split(":")[2])
            self.mark_refunded(invoice_id)
            await self._ack(qid, "↩️ Отмечено как возврат")
            asyncio.create_task(self.bg_order_view(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:unmark_refund:"):
            invoice_id = int(data.split(":")[2])
            self.mark_refunded(invoice_id, undo=True)
            await self._ack(qid, "✅ Пометка возврата снята")
            asyncio.create_task(self.bg_order_view(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:toggle:"):
            kind = data.split(":")[2]
            if kind == "auto_fulfill":
                new_val = not self._get(cid, "ggsel_auto_fulfill")
                self._set(cid, "ggsel_auto_fulfill", new_val)
                label = "🤖 Авто-режим включён ✅" if new_val else "🤖 Авто-режим выключен ❌"
                await self._ack(qid, label)
                asyncio.create_task(self.bg_info(cid, mid))
            elif kind in ("orders", "messages", "reviews"):
                cfg_key = f"ggsel_notify_{kind}"
                new_val = not self._get(cid, cfg_key)
                self._set(cid, cfg_key, new_val)
                label = "🔔 Включено" if new_val else "🔕 Выключено"
                await self._ack(qid, label)
                txt, kb = self.settings_page(cid)
                await self._edit(cid, mid, txt, kb)
            else:
                await self._ack(qid)
            return

        # ── Порядок карт ──────────────────────────────────────────────────────

        if data == "ggsell:card_order":
            await self._ack(qid)
            txt, kb = self.cards_order_page_sync()
            await self._edit(cid, mid, txt, kb)
            return

        if data == "ggsell:card_order_edit":
            self.card_order_mode[cid] = True
            cards = self._load_cards()
            nums  = "  ".join(f"`{i+1}`" for i in range(len(cards)))
            await self._ack(qid)
            await self._send(cid,
                f"✏️ *Введи порядок карт*\n\n"
                f"Доступные номера: {nums}\n\n"
                f"Отправь числа через пробел или запятую.\n"
                f"Например: `1 3 2`  —  попробует 1-ю, затем 3-ю, затем 2-ю.",
                reply_markup={"inline_keyboard": [
                    [{"text": "❌ Отмена", "callback_data": "ggsell:card_order"}],
                ]})
            return

        if data == "ggsell:card_order_reset":
            self._save_card_order([])
            await self._ack(qid, "🔄 Порядок сброшен")
            txt, kb = self.cards_order_page_sync()
            await self._edit(cid, mid, txt, kb)
            return

        # ── Шаблоны сообщений ─────────────────────────────────────────────────

        if data == "ggsell:templates":
            await self._ack(qid)
            txt, kb = self.bg_templates_page_sync(cid)
            await self._edit(cid, mid, txt, kb)
            return

        if data.startswith("ggsell:template_view:"):
            name = data.split(":", 2)[2]
            if name not in self._TEMPLATE_NAMES:
                await self._ack(qid)
                return
            await self._ack(qid)
            txt, kb = self.bg_template_view_sync(name)
            await self._edit(cid, mid, txt, kb)
            return

        if data.startswith("ggsell:template_edit:"):
            name = data.split(":", 2)[2]
            if name not in self._TEMPLATE_NAMES:
                await self._ack(qid)
                return
            self.template_edit_mode[cid] = name
            label, desc = self._TEMPLATE_NAMES[name]
            ph_note = "\n\nИспользуй `{link}` для вставки ссылки." if name == "msg_template" else ""
            await self._ack(qid)
            await self._send(cid,
                f"✏️ *Редактирование шаблона «{label}»*\n\n"
                f"_{desc}_{ph_note}\n\n"
                "Отправь новый текст шаблона следующим сообщением:",
                reply_markup={"inline_keyboard": [
                    [{"text": "❌ Отмена", "callback_data": f"ggsell:template_view:{name}"}],
                ]}
            )
            return

        if data.startswith("ggsell:template_reset:"):
            name = data.split(":", 2)[2]
            if name not in self._TEMPLATE_NAMES:
                await self._ack(qid)
                return
            from ggsell.monitor import save_template
            save_template(name, "")
            label, _ = self._TEMPLATE_NAMES[name]
            await self._ack(qid, f"🔄 Шаблон «{label}» сброшен")
            txt, kb = self.bg_template_view_sync(name)
            await self._edit(cid, mid, txt, kb)
            return

        if data.startswith("ggsell:mark_used:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid, "⏳ Архивирую...")
            asyncio.create_task(self.bg_mark_used(cid, mid, invoice_id))
            return

        if data == "ggsell:offers":
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ Загружаю офферы...",
                             {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            asyncio.create_task(self.bg_offers_page(cid, mid))
            return

        if data.startswith("ggsell:offer_toggle:"):
            parts      = data.split(":")
            offer_id   = int(parts[2])
            new_status = parts[3]
            if new_status not in ("active", "paused"):
                await self._ack(qid)
                return
            action = "Запускаю..." if new_status == "active" else "Останавливаю..."
            await self._ack(qid, f"⏳ {action}")
            asyncio.create_task(self.bg_offer_toggle(cid, mid, offer_id, new_status))
            return

        await self._ack(qid)
