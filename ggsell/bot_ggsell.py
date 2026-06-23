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
    ):
        self.orders = orders
        self.confirm = confirm
        self._done = done
        self._done_loaded = done_loaded
        self.reply_mode = reply_mode
        self.pool_pick_pending = pool_pick_pending
        self._done_links        = done_links
        self._done_buyer_emails: dict = {}  # {invoice_id: buyer_email}
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

    # ── Пул ссылок ───────────────────────────────────────────────────────────

    _STALE_HOURS = 2  # ссылка считается устаревшей через N часов

    @staticmethod
    def _parse_pool_entry(entry) -> dict:
        if isinstance(entry, str):
            return {"url": entry, "added_at": "", "profile_path": ""}
        return {
            "url":          entry.get("url", ""),
            "added_at":     entry.get("added_at", ""),
            "profile_path": entry.get("profile_path", ""),
        }

    def read_pool(self) -> list:
        """Список URL-строк (backward compat)."""
        try:
            f = _DATA_DIR / "ggsel_links.json"
            raw = json.loads(f.read_text(encoding="utf-8")).get("links", [])
            return [self._parse_pool_entry(e)["url"] for e in raw]
        except Exception:
            return []

    def read_pool_full(self) -> list:
        """Список словарей {url, added_at, profile_path}."""
        try:
            f = _DATA_DIR / "ggsel_links.json"
            raw_data = json.loads(f.read_text(encoding="utf-8"))
            profile_map = raw_data.get("profile_map", {})
            result = []
            for e in raw_data.get("links", []):
                entry = self._parse_pool_entry(e)
                if not entry["profile_path"] and entry["url"] in profile_map:
                    entry["profile_path"] = profile_map[entry["url"]]
                result.append(entry)
            return result
        except Exception:
            return []

    def remove_link(self, link: str) -> None:
        try:
            f = _DATA_DIR / "ggsel_links.json"
            raw = json.loads(f.read_text(encoding="utf-8"))
            raw["links"] = [e for e in raw.get("links", [])
                            if self._parse_pool_entry(e)["url"] != link]
            raw.get("profile_map", {}).pop(link, None)
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _pool_age(added_at: str):
        """Возвращает (возраст в секундах, строка времени) или (-1, '')."""
        if not added_at:
            return -1, ""
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(added_at)
            age = (datetime.now() - dt).total_seconds()
            time_str = dt.strftime("%d %b %H:%M")
            return age, time_str
        except Exception:
            return -1, ""

    def pool_text(self) -> str:
        avail = self.read_pool_full()
        done_links = self._done_links
        self.get_done()

        avail_cnt = len(avail)
        done_cnt  = len(done_links)
        used_cnt  = len(self.get_used())
        stale_sec = self._STALE_HOURS * 3600

        lines = [
            "🔗 *Ссылки GGSell*",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🟢 Невыдано: *{avail_cnt}*  ·  🔵 Выдано: *{done_cnt}*  ·  🟡 Использовано: *{used_cnt}*",
            "",
        ]

        if avail:
            lines.append("*Невыданные ссылки:*")
            for i, entry in enumerate(avail[:10]):
                lnk = entry["url"]
                short = lnk[8:50] + "…" if len(lnk[8:]) > 42 else lnk[8:]
                age, time_str = self._pool_age(entry["added_at"])
                if age >= stale_sec:
                    h, m = int(age // 3600), int((age % 3600) // 60)
                    age_s = f"{h}ч {m}м" if m else f"{h}ч"
                    lines.append(f"⚠️ `{i + 1}. {short}` _{time_str} · {age_s} — устарела_")
                elif time_str:
                    lines.append(f"🟢 `{i + 1}. {short}` _({time_str})_")
                else:
                    lines.append(f"🟢 `{i + 1}. {short}`")
            if avail_cnt > 10:
                lines.append(f"_...ещё {avail_cnt - 10}_")
        else:
            lines.append("_Невыданных ссылок нет_")

        if done_links:
            lines.append("")
            lines.append("*Выданные:*")
            done_sorted = sorted(done_links.items(), key=lambda x: -int(x[0]))[:6]
            for inv_id, lnk in done_sorted:
                short = lnk[8:38] + "…" if len(lnk[8:]) > 30 else lnk[8:]
                # Email: сначала из постоянного хранилища, потом из кэша
                email = self._done_buyer_emails.get(int(inv_id), "")
                if not email:
                    cached = self.orders.get(int(inv_id), {})
                    if isinstance(cached, dict):
                        email = (cached.get("buyer_email") or
                                 self.parse_order(cached.get("order", {})).get("email", "")) or ""
                who = (email[:30] + "…" if len(email) > 30 else email) if email else f"#{inv_id}"
                lines.append(f"🔵 {who} → `{short}`")

        return "\n".join(lines)

    def _pool_kb(self, avail: list) -> dict:
        """Клавиатура страницы пула. avail — список dict из read_pool_full()."""
        stale_sec = self._STALE_HOURS * 3600
        link_btns = []
        for idx, entry in enumerate(avail[:8]):
            lnk = entry["url"] if isinstance(entry, dict) else entry
            pp  = entry.get("profile_path", "") if isinstance(entry, dict) else ""
            added_at = entry.get("added_at", "") if isinstance(entry, dict) else ""
            preview = lnk[8:28] + "…" if len(lnk[8:]) > 20 else lnk[8:]
            age, time_str = self._pool_age(added_at)
            t_label = f" · {time_str}" if time_str else ""
            if age >= stale_sec and pp:
                link_btns.append([{"text": f"⚠️ №{idx + 1} · {preview}{t_label} — Обновить",
                                    "callback_data": f"ggsell:pool_refresh:{idx}"}])
            else:
                icon = "⚠️" if age >= stale_sec else "🟢"
                link_btns.append([{"text": f"{icon} №{idx + 1} · {preview}{t_label} — Выдать",
                                    "callback_data": f"ggsell:pool_pick:{idx}"}])
        return {"inline_keyboard": link_btns + [
            [{"text": "🟡 Архив", "callback_data": "ggsell:used"}],
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]}

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

    def mark_done(self, invoice_id: int, link: str = "") -> None:
        dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.get_done()[invoice_id] = dt_str
        if link:
            self._done_links[invoice_id] = link

        # Email покупателя из кэша заказов
        _cached = self.orders.get(invoice_id, {})
        buyer_email = ""
        if isinstance(_cached, dict):
            buyer_email = (
                _cached.get("buyer_email") or
                self.parse_order(_cached.get("order", {})).get("email", "")
            ) or ""
        if buyer_email:
            self._done_buyer_emails[invoice_id] = buyer_email

        # Читаем profile_path ДО того как _mark_profile_issued его удалит из profile_map
        profile_path_str = ""
        if link:
            try:
                lf = _DATA_DIR / "ggsel_links.json"
                profile_path_str = (
                    json.loads(lf.read_text(encoding="utf-8"))
                    .get("profile_map", {}).get(link, "")
                )
            except Exception:
                pass

        try:
            f = _DATA_DIR / "ggsel_done.json"
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                raw = {"done": {}}
            raw.setdefault("done", {})[str(invoice_id)] = dt_str
            if link:
                raw.setdefault("links", {})[str(invoice_id)] = link
            if profile_path_str:
                raw.setdefault("profile_paths", {})[str(invoice_id)] = profile_path_str
            if buyer_email:
                raw.setdefault("buyer_emails", {})[str(invoice_id)] = buyer_email
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        if link:
            self._mark_profile_issued(link)

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
            [{"text": "🟡 Архив", "callback_data": "ggsell:used"},
             {"text": "◀️ Заказ", "callback_data": f"ggsell:order:{invoice_id}"}],
        ]})

    def used_text(self) -> str:
        """Текст страницы архива использованных ссылок."""
        try:
            raw  = json.loads((_DATA_DIR / "ggsel_done.json").read_text(encoding="utf-8"))
            used  = raw.get("used", {})
            links = raw.get("links", {})
        except Exception:
            used = {}; links = {}

        if not used:
            return "🟡 *Архив*\n\n_Пусто_"

        lines = [
            "🟡 *Архив*",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"Всего использовано: *{len(used)}*", "",
        ]
        for inv_id, dt_str in sorted(used.items(), key=lambda x: -int(x[0]))[:15]:
            lnk   = links.get(str(inv_id), "")
            short = (lnk[8:38] + "…" if len(lnk[8:]) > 30 else lnk[8:]) if lnk else "—"
            cached    = self.orders.get(int(inv_id), {})
            order_obj = cached.get("order", {}) if isinstance(cached, dict) else {}
            email     = (cached.get("buyer_email") if isinstance(cached, dict) else "") or ""
            if not email and order_obj:
                email = self.parse_order(order_obj).get("email", "")
            who = (email[:28] + "…" if len(email) > 28 else email) if email else f"#{inv_id}"
            lines.append(f"🟡 *{who}* · _{dt_str}_")
            if short != "—":
                lines.append(f"   `{short}`")
        if len(used) > 15:
            lines.append(f"\n_...ещё {len(used) - 15}_")
        return "\n".join(lines)

    def used_kb(self) -> dict:
        return {"inline_keyboard": [[
            {"text": "◀️ Ссылки", "callback_data": "ggsell:pool"},
        ]]}

    def _mark_profile_issued(self, link: str) -> None:
        """По ссылке находит Chrome-профиль в profile_map и ставит issued_ts."""
        try:
            import time as _time
            links_file = _DATA_DIR / "ggsel_links.json"
            try:
                raw = json.loads(links_file.read_text(encoding="utf-8"))
            except Exception:
                return
            profile_map: dict = raw.get("profile_map", {})
            profile_path_str = profile_map.pop(link, "")
            if link in raw.get("profile_map", {}):
                raw["profile_map"] = profile_map
                links_file.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            if not profile_path_str:
                return
            meta_file = Path(profile_path_str) / ".profile_meta.json"
            if not meta_file.exists():
                return
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["issued_ts"]   = _time.time()
            meta["issued_link"] = link
            meta_file.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"GGSell: профиль {Path(profile_path_str).name} помечен как выданный")
        except Exception as exc:
            logger.debug(f"GGSell: _mark_profile_issued: {exc}")

    def get_sent_link(self, invoice_id: int) -> str:
        self.get_done()
        return self._done_links.get(invoice_id, "")

    # ── Метка заказа для кнопок ─────────────────────────────────────────────

    def _order_label(self, order: dict, invoice_id: int) -> str:
        """Краткая читаемая метка: email · период · дата."""
        p = self.parse_order(order)
        email = p["email"]
        if not email:
            cached = self.orders.get(invoice_id, {})
            email = (cached.get("buyer_email")
                     or self.parse_order(cached.get("order", {})).get("email", "")
                     if cached else "")

        # Период подписки из options ("3 месяца" → "3м", "12 месяцев" → "12м")
        period = ""
        for opt in p["options"]:
            val = opt.get("value", "")
            m = re.search(r"(\d+)\s*(мес|год|month|year)", val.lower())
            if m:
                unit = "м" if m.group(2) in ("мес", "month") else "г"
                period = f"{m.group(1)}{unit}"
                break

        parts = []
        if period:
            parts.append(period)
        if email:
            parts.append(email[:32])
        return " · ".join(parts) if parts else f"#{invoice_id}"

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
            lines.append(f"💰 Сумма покупки: *{p['sum_buy']}₽*")
        if p["sum_sell"]:
            lines.append(f"💼 Твоя выплата: *{p['sum_sell']}₽*")

        # Дата
        if p["date"]:
            lines.append(f"🕒 {p['date']}")

        # Статус
        lines.append("")
        if invoice_id in used_ids:
            lines.append("🟡 *Статус: в архиве*")
            if sent_link:
                lines.append(f"🔗 `{sent_link}`")
        elif invoice_id in done:
            lines.append("🔵 *Статус: выдано*")
            if sent_link:
                lines.append(f"🔗 `{sent_link}`")
        elif confirm_lnk:
            lines.append("⏳ *Статус: ждёт подтверждения*")
            lines.append(f"🔗 `{confirm_lnk}`")
        else:
            lines.append("🟢 *Статус: новый*")

        return "\n".join(lines)

    def order_kb(self, invoice_id: int, review_exists: bool = False) -> dict:
        done        = self.get_done()
        used_ids    = self.get_used()
        confirm_lnk = self.confirm.get(invoice_id)
        rows = []
        if confirm_lnk:
            rows.append([
                {"text": "📤 Отправить",      "callback_data": f"ggsell:send:{invoice_id}"},
                {"text": "📦 В пул",           "callback_data": f"ggsell:topool:{invoice_id}"},
            ])
            rows.append([{"text": "❌ Не отправлять", "callback_data": f"ggsell:nosend:{invoice_id}"}])
        elif invoice_id not in done:
            rows.append([{"text": "▶️ Выполнить",      "callback_data": f"ggsell:run:{invoice_id}"}])
            rows.append([
                {"text": "📦 Из пула",         "callback_data": f"ggsell:pool_for:{invoice_id}"},
                {"text": "✅ Отметить выдано",  "callback_data": f"ggsell:mark_done:{invoice_id}"},
            ])
        elif invoice_id not in used_ids:
            rows.append([{"text": "🟡 Использована", "callback_data": f"ggsell:mark_used:{invoice_id}"}])
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
        "msg_greeting":    ("Приветствие",      "Отправляется покупателю автоматически при получении нового заказа (авто-режим)."),
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

    _CARD_ORDER_FILE = _DATA_DIR / "ggsel_card_order.json"

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
        pool        = len(self.read_pool())
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
        stat_parts = [f"🔗 Пул: *{pool}*", f"✅ Выдано: *{done_cnt}*"]
        if pending_cnt:
            stat_parts.append(f"⏳ В работе: *{pending_cnt}*")
        lines.append("   ·   ".join(stat_parts))

        auto_label = "🤖 Автоматизация:  ВКЛ ✅" if auto_on else "🤖 Автоматизация:  ВЫКЛ ❌"
        kb_rows = [
            [{"text": auto_label, "callback_data": "ggsell:toggle:auto_fulfill"}],
            [{"text": "🔗 Ссылки",    "callback_data": "ggsell:pool"},
             {"text": "📋 Заказы",    "callback_data": "ggsell:orders"},
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

        # Сортировка: самые новые (наибольший id) — сверху
        yt_orders.sort(key=lambda o: int(o.get("invoice_id") or o.get("id") or 0), reverse=True)

        done = self.get_done()
        order_btns = []

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        total_cnt    = len(yt_orders)
        today_cnt    = 0
        today_done   = 0

        for o in yt_orders:
            inv_i = int(o.get("invoice_id") or o.get("id") or 0)
            p     = self.parse_order(o)
            dt    = p["date"]  # "YYYY-MM-DD HH:MM"
            if dt.startswith(today):
                today_cnt += 1
                if inv_i in done:
                    today_done += 1

        # Шапка — только статистика
        lines = [
            "📋 *GGSell — Заказы YouTube Premium*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"📦 Всего: *{total_cnt}*   ·   📅 Сегодня: *{today_cnt}*   ·   ✅ Выдано сегодня: *{today_done}*",
            "",
        ]

        if not yt_orders:
            lines.append("_Нет последних заказов YouTube Premium_")

        PAGE_SIZE = 5
        page_orders = yt_orders[offset:offset + PAGE_SIZE]

        for o in page_orders:
            inv   = o.get("invoice_id") or o.get("id") or "?"
            inv_i = int(inv) if str(inv).isdigit() else 0
            p     = self.parse_order(o)

            if inv_i in done:
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

        kb_rows = btn_rows + [
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

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
                        # Суммы
                        if not order.get("sum_t"):
                            order["sum_t"] = c.get("sum_t") or c.get("amount") or ""
                        if not order.get("sum_seller"):
                            order["sum_seller"] = c.get("sum_seller") or c.get("seller_reward_amount") or ""
                        # Опции для отображения
                        if not order.get("options") and not order.get("selected_options"):
                            order["options"] = c.get("options") or []
                        # Дата
                        if not order.get("date"):
                            order["date"] = c.get("date") or c.get("created_at") or ""
                        # Название
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
                        rv_lines.append(f"_{r['text'][:300]}_")
                    if r["date"]:
                        rv_lines.append(f"📅 _{r['date']}_")
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

    async def bg_pool_pick(self, cid, mid, link: str) -> None:
        cli = self.get_client()
        try:
            orders_v1 = await cli.get_orders_v1(limit=30) if cli else []
            yt_orders = [o for o in orders_v1
                         if int(o.get("offer_ggsel_id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
            if not yt_orders:
                orders = await cli.get_last_orders() if cli else []
                yt_orders = [o for o in orders
                             if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
        except Exception:
            yt_orders = []

        done = self.get_done()
        lp = link[8:55] + "…" if len(link[8:]) > 47 else link[8:]
        lines = [
            "📦 *Выдать ссылку покупателю*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"🟢 `{lp}`", "",
            "Кому отправить? (от старых к новым):",
        ]
        # Сортировка: от самого старого заказа к самому новому
        yt_orders.sort(key=lambda o: int(o.get("invoice_id") or o.get("id") or 0))
        order_rows = []
        for o in yt_orders:
            inv_i = int(o.get("invoice_id") or o.get("id") or 0)
            if inv_i in done:
                continue
            p = self.parse_order(o)
            email = p["email"]
            if not email:
                cached = self.orders.get(inv_i, {})
                if isinstance(cached, dict):
                    email = (cached.get("buyer_email") or
                             self.parse_order(cached.get("order", {})).get("email", "")) or ""
            label = email[:48] if email else f"#{inv_i}"
            order_rows.append([{"text": label[:64],
                                 "callback_data": f"ggsell:pool_order:{inv_i}"}])
        if not order_rows:
            lines.append("\n_Все заказы уже выполнены_")
        kb_rows = order_rows[:8] + [
            [{"text": "◀️ Ссылки", "callback_data": "ggsell:pool"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_pool_for(self, cid, mid, invoice_id: int) -> None:
        links = self.read_pool()
        # Email покупателя для отображения
        _cached = self.orders.get(invoice_id, {})
        _buyer_email = ""
        if isinstance(_cached, dict):
            _buyer_email = (_cached.get("buyer_email") or
                            self.parse_order(_cached.get("order", {})).get("email", "")) or ""
        _who = _buyer_email[:40] if _buyer_email else f"#{invoice_id}"

        if not links:
            await self._edit(cid, mid,
                f"🔗 *Ссылок нет*\n\nНет доступных ссылок для покупателя `{_who}`.",
                {"inline_keyboard": [[{"text": "◀️ Заказ",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return
        lines = [
            "📦 *Выбрать ссылку из пула*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"Покупатель: `{_who}`",
            f"Доступно ссылок: *{len(links)}*", "",
            "Нажмите на ссылку чтобы отправить покупателю:",
        ]
        link_rows = []
        for idx, lnk in enumerate(links[:8]):
            preview = lnk[8:52] + "…" if len(lnk) > 52 else lnk
            link_rows.append([{"text": f"🔗 {idx+1}. {preview}",
                               "callback_data": f"ggsell:pool_for_pick:{invoice_id}:{idx}"}])
        kb_rows = link_rows + [[{"text": "◀️ Назад",
                                  "callback_data": f"ggsell:order:{invoice_id}"}]]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_pool_send(self, cid, mid, invoice_id: int, link: str) -> None:
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ GGSell", "callback_data": "go:ggsell"}]]})
            return
        # Email покупателя для отображения
        _cached = self.orders.get(invoice_id, {})
        _buyer_email = ""
        if isinstance(_cached, dict):
            _buyer_email = (_cached.get("buyer_email") or
                            self.parse_order(_cached.get("order", {})).get("email", "")) or ""
        _who = _buyer_email[:40] if _buyer_email else f"#{invoice_id}"

        from ggsell.monitor import get_template
        ok = await cli.send_message(invoice_id, get_template("msg_template").format(link=link))
        self.remove_link(link)
        if ok:
            self.mark_done(invoice_id, link)
            await self._edit(cid, mid,
                f"✅ *Ссылка отправлена покупателю!*\n\n"
                f"Покупатель: `{_who}`\n🔗 `{link}`",
                {"inline_keyboard": [
                    [{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"},
                     {"text": "◀️ GGSell",     "callback_data": "go:ggsell"}],
                ]})
        else:
            await self._edit(cid, mid,
                f"❌ Не удалось отправить ссылку покупателю `{_who}`.\n\nСсылка возвращена в пул.",
                {"inline_keyboard": [[{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"}]]})
            from ggsell.monitor import add_link_to_pool
            add_link_to_pool(link)

    async def bg_refresh_link(self, cid: int, mid: int, link_idx: int) -> None:
        """Обновить устаревшую ссылку в пуле: зайти в профиль Flipkart и взять новую."""
        from pathlib import Path as _Path
        pool = self.read_pool_full()
        if link_idx >= len(pool):
            await self._edit(cid, mid, "❌ Ссылка не найдена (пул изменился).",
                {"inline_keyboard": [[{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"}]]})
            return
        entry = pool[link_idx]
        old_url = entry["url"]
        profile_path = entry.get("profile_path", "")
        if not profile_path or not _Path(profile_path).exists():
            await self._edit(cid, mid,
                "❌ Профиль Flipkart не привязан к этой ссылке — обновите вручную.",
                {"inline_keyboard": [[{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"}]]})
            return

        await self._edit(cid, mid, "⏳ Захожу в Flipkart и обновляю ссылку…",
            {"inline_keyboard": []})

        try:
            pp = _Path(profile_path)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: asyncio.run(
                self._m("_check_black_store_activation")(pp, headless=True)))
        except Exception as exc:
            await self._edit(cid, mid, f"❌ Ошибка при обновлении ссылки: {exc}",
                {"inline_keyboard": [[{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"}]]})
            return

        if not isinstance(result, dict):
            await self._edit(cid, mid, "❌ Не удалось получить ссылку из профиля.",
                {"inline_keyboard": [[{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"}]]})
            return

        new_url = result.get("short_link") or result.get("activation_url") or ""
        if not new_url:
            st = result.get("status", "?")
            await self._edit(cid, mid,
                f"❌ Статус профиля: *{st}* — свежая ссылка недоступна.",
                {"inline_keyboard": [[{"text": "🔗 Ссылки", "callback_data": "ggsell:pool"}]]})
            return

        self.remove_link(old_url)
        from ggsell.monitor import add_link_to_pool
        add_link_to_pool(new_url, profile_path=str(profile_path))

        short_p = new_url[8:44] + "…" if len(new_url) > 52 else new_url
        avail = self.read_pool_full()
        await self._edit(cid, mid,
            f"✅ *Ссылка обновлена!*\n\n🔗 `{short_p}`",
            self._pool_kb(avail))

    async def bg_link_to_buyer_page(self, cid: int, mid: int, phone: str, link: str, offset: int = 0) -> None:
        """Показать список заказов для отправки ссылки покупателю (пагинация по 5)."""
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "🔗 Добавить в ссылки",
                                       "callback_data": f"profile:topool:{phone}"}]]})
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
                {"inline_keyboard": [[{"text": "🔗 Добавить в ссылки",
                                       "callback_data": f"profile:topool:{phone}"}]]})
            return

        done = self.get_done()
        # Сортировка от старых к новым (возрастающий id)
        yt_orders.sort(key=lambda o: int(o.get("invoice_id") or o.get("id") or 0))
        pending = [o for o in yt_orders
                   if int(o.get("invoice_id") or o.get("id") or 0) not in done]

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
            label = self._order_label(o, inv_i)
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
        kb_rows.append([{"text": "🔗 Добавить в ссылки",
                         "callback_data": f"profile:topool:{phone}"}])
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_link_to_order(self, cid: int, mid: int, phone: str, link: str, invoice_id: int) -> None:
        """Отправить ссылку конкретному покупателю через GGSell и обновить сообщение."""
        cli = self.get_client()
        if not cli:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "🔗 Добавить в ссылки",
                                       "callback_data": f"profile:topool:{phone}"}]]})
            return
        from ggsell.monitor import get_template
        ok = await cli.send_message(invoice_id, get_template("msg_template").format(link=link))
        if ok:
            self.mark_done(invoice_id, link)
            await self._edit(cid, mid,
                f"✅ *Ссылка отправлена покупателю!*\n\n"
                f"Заказ: `#{invoice_id}`\n🔗 `{link}`",
                {"inline_keyboard": [
                    [{"text": "🔗 Добавить в ссылки", "callback_data": f"profile:topool:{phone}"},
                     {"text": "◀️ GGSell",            "callback_data": "go:ggsell"}],
                ]})
        else:
            await self._edit(cid, mid,
                f"❌ Не удалось отправить ссылку заказу `#{invoice_id}`.",
                {"inline_keyboard": [
                    [{"text": "🔗 Добавить в ссылки",    "callback_data": f"profile:topool:{phone}"}],
                    [{"text": "📤 Другой заказ",          "callback_data": f"profile:send_to_buyer:{phone}:0"}],
                ]})

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

        # 3. Загрузить и упорядочить карты (один раз для всего цикла профилей)
        cards_file     = self._root / "data" / "cards.json"
        original_cards_raw: Optional[str] = None
        months = 3
        do_buy = self._m("_do_buy_membership")

        try:
            if cards_file.exists():
                original_cards_raw = cards_file.read_text(encoding="utf-8")
                all_cards: list = json.loads(original_cards_raw) or []
                card_order = self._load_card_order()
                if card_order and all_cards:
                    ordered: list = []
                    used: set = set()
                    for idx in card_order:
                        if 0 <= idx < len(all_cards):
                            ordered.append(all_cards[idx])
                            used.add(idx)
                    for i, c in enumerate(all_cards):
                        if i not in used:
                            ordered.append(c)
                    cards_file.write_text(
                        json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
                    logger.info(f"GGSell auto #{invoice_id}: порядок карт {[i+1 for i in card_order]}")
        except Exception as exc:
            logger.debug(f"GGSell auto #{invoice_id}: ошибка применения порядка карт: {exc}")
            original_cards_raw = None

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

        try:
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
        finally:
            # Восстанавливаем оригинальный cards.json
            if original_cards_raw is not None:
                try:
                    cards_file.write_text(original_cards_raw, encoding="utf-8")
                except Exception:
                    pass

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

        before_links = set(self.read_pool())

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

        after_links = self.read_pool()
        new_links   = [l for l in after_links if l not in before_links]

        if new_links:
            link = new_links[0]
            self.remove_link(link)
            self.confirm[invoice_id] = link
            await self._send(cid,
                f"✅ *Ссылка для заказа* `#{invoice_id}` *готова!*\n\n"
                f"🔗 `{link}`\n\n"
                f"📧 Покупатель: `{buyer_email}`\n\n"
                "Отправить ссылку покупателю в чат GGSell?",
                reply_markup={"inline_keyboard": [
                    [{"text": "📤 Отправить покупателю",
                      "callback_data": f"ggsell:send:{invoice_id}"}],
                    [{"text": "📦 В пул ссылок",
                      "callback_data": f"ggsell:topool:{invoice_id}"},
                     {"text": "❌ Не отправлять",
                      "callback_data": f"ggsell:nosend:{invoice_id}"}],
                ]})
        else:
            await self._send(cid,
                f"⚠️ Заказ `#{invoice_id}`: автоматизация завершена, но новая ссылка не найдена.\n\n"
                "_Добавьте ссылку вручную или повторите запуск._")

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
                reply_markup={"inline_keyboard": [
                    [{"text": "❌ Отмена",
                      "callback_data": f"ggsell:reply_cancel:{invoice_id}"}],
                ]})

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
              "callback_data": f"ggsell:order:{invoice_id}"},
             {"text": "📦 Из пула",
              "callback_data": f"ggsell:pool_for:{invoice_id}"}],
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

        # Авто-выполнение: сразу резервируем профиль, покупку запускаем по первому сообщению
        if any(self._get(_cid, "ggsel_auto_fulfill") for _cid in list(self.subs)):
            self._auto_pending[invoice_id] = order
            logger.info(f"GGSell auto #{invoice_id}: ждём первого сообщения от покупателя")
            asyncio.create_task(self.bg_prepare_for_order(invoice_id, order))

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

        time_s = f"  │  _{msg_time}_" if msg_time else ""
        text = (
            f"💬 *Новое сообщение от покупателя*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"*Заказ:* `#{invoice_id}`  │  👤 `{email}`{time_s}\n\n"
            f"{msg_text}"
        )
        kb = {"inline_keyboard": [
            [{"text": "💬 Ответить",
              "callback_data": f"ggsell:reply:{invoice_id}"},
             {"text": f"📋 Заказ #{invoice_id}",
              "callback_data": f"ggsell:order:{invoice_id}"}],
        ]}
        for _cid in list(self.subs):
            if not self._get(_cid, "ggsel_notify_messages"):
                continue
            try:
                await self._http.post(f"{self._api}/sendMessage",
                                      json={"chat_id": _cid, "text": text,
                                            "parse_mode": "Markdown", "reply_markup": kb})
            except Exception:
                pass

        # Авто-выполнение: запускаем при первом сообщении покупателя по ожидающему заказу
        if invoice_id and invoice_id in self._auto_pending:
            _order = self._auto_pending.pop(invoice_id)
            if any(self._get(_cid, "ggsel_auto_fulfill") for _cid in list(self.subs)):
                asyncio.create_task(self.bg_auto_fulfill(invoice_id, _order))

    # ── Отзывы ───────────────────────────────────────────────────────────────

    @staticmethod
    def parse_review(r: dict) -> dict:
        """Нормализует сырой dict отзыва в единый формат."""
        invoice_id = int(r.get("invoice_id") or r.get("id_i") or r.get("order_id") or 0)
        # Числовой рейтинг (v1 API) или из type/feedback_type (старый API)
        rating = int(r.get("rating") or r.get("score") or r.get("stars") or 0)
        if not rating:
            ft = str(r.get("type") or r.get("feedback_type") or "").lower()
            if "positive" in ft:
                rating = 5
            elif "negative" in ft:
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
        """Вернуть активный промокод для отзыва (applies_to_all_offers=False)."""
        try:
            codes = await cli.get_promo_codes()
            for code in codes:
                if (code.get("status", {}).get("slug") == "active"
                        and not code.get("applies_to_all_offers")):
                    limit = code.get("activation_limit")
                    count = code.get("activation_count", 0)
                    if limit is None or count < limit:
                        return code.get("code", "")
        except Exception as exc:
            logger.debug(f"GGSell get_review_promo_code: {exc}")
        return ""

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

        if data == "ggsell:pool":
            await self._ack(qid)
            avail = self.read_pool_full()
            await self._edit(cid, mid, self.pool_text(), self._pool_kb(avail))
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

        if data.startswith("ggsell:run:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid, "⏳ Запускаю автоматизацию...")
            asyncio.create_task(self.bg_run(cid, mid, invoice_id))
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
                reply_markup={"inline_keyboard": [
                    [{"text": "❌ Отмена",
                      "callback_data": f"ggsell:reply_cancel:{invoice_id}"}],
                ]})
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

        if data.startswith("ggsell:pool_for:"):
            invoice_id = int(data.split(":")[2])
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ Загружаю пул ссылок...",
                             {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": f"ggsell:order:{invoice_id}"}]]})
            asyncio.create_task(self.bg_pool_for(cid, mid, invoice_id))
            return

        if data.startswith("ggsell:pool_for_pick:"):
            parts      = data.split(":")
            invoice_id = int(parts[2])
            idx        = int(parts[3])
            links      = self.read_pool()
            if idx >= len(links):
                await self._ack(qid, "❌ Пул изменился, обновите список", alert=True)
                return
            link = links[idx]
            self.pool_pick_pending[cid] = link
            await self._ack(qid, "⏳ Отправляю...")
            asyncio.create_task(self.bg_pool_send(cid, mid, invoice_id, link))
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

        if data.startswith("ggsell:topool:"):
            invoice_id = int(data.split(":")[2])
            link = self.confirm.pop(invoice_id, None)
            if not link:
                await self._ack(qid, "❌ Ссылка не найдена", alert=True)
                return
            from ggsell.monitor import add_link_to_pool
            add_link_to_pool(link)
            await self._ack(qid, "📦 Добавлено в пул!")
            avail = self.read_pool_full()
            await self._edit(cid, mid, self.pool_text(), self._pool_kb(avail))
            return

        if data.startswith("ggsell:pool_pick:"):
            idx   = int(data.split(":")[2])
            links = self.read_pool()
            if idx >= len(links):
                await self._ack(qid, "❌ Пул изменился, обновите список", alert=True)
                return
            link = links[idx]
            self.pool_pick_pending[cid] = link
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ Загружаю заказы...",
                             {"inline_keyboard": [[{"text": "❌ Отмена",
                                                     "callback_data": "ggsell:pool"}]]})
            asyncio.create_task(self.bg_pool_pick(cid, mid, link))
            return

        if data.startswith("ggsell:pool_refresh:"):
            idx = int(data.split(":")[2])
            await self._ack(qid)
            asyncio.create_task(self.bg_refresh_link(cid, mid, idx))
            return

        if data.startswith("ggsell:pool_order:"):
            invoice_id = int(data.split(":")[2])
            link = self.pool_pick_pending.pop(cid, None)
            if not link:
                await self._ack(qid, "❌ Сессия истекла, выберите ссылку снова", alert=True)
                return
            await self._ack(qid, "⏳ Отправляю...")
            asyncio.create_task(self.bg_pool_send(cid, mid, invoice_id, link))
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

        if data == "ggsell:used":
            await self._ack(qid)
            await self._edit(cid, mid, self.used_text(), self.used_kb())
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
