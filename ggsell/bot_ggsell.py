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
from datetime import datetime
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
    ):
        self.orders = orders
        self.confirm = confirm
        self._done = done
        self._done_loaded = done_loaded
        self.reply_mode = reply_mode
        self.pool_pick_pending = pool_pick_pending
        self._done_links = done_links
        self._cli = cli_holder
        self.subs = subs

        self._edit  = edit_fn
        self._send  = send_fn
        self._ack   = ack_fn
        self._get   = get_fn
        self._set   = set_fn
        self._m     = m_fn
        self._http  = http_client
        self._api   = tg_api_url
        self._root  = project_root

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

    def read_pool(self) -> list:
        try:
            f = _DATA_DIR / "ggsel_links.json"
            return json.loads(f.read_text(encoding="utf-8")).get("links", [])
        except Exception:
            return []

    def remove_link(self, link: str) -> None:
        try:
            f = _DATA_DIR / "ggsel_links.json"
            raw = json.loads(f.read_text(encoding="utf-8"))
            raw["links"] = [l for l in raw.get("links", []) if l != link]
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def pool_text(self) -> str:
        avail = self.read_pool()
        done_links = self._done_links  # загружаем через get_done() ниже
        self.get_done()  # убеждаемся что done_links загружены

        avail_cnt = len(avail)
        done_cnt  = len(done_links)

        lines = [
            "📦 *Пул ссылок GGSell*",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"🟢 Невыдано: *{avail_cnt}*  ·  🔵 Выдано: *{done_cnt}*",
            "",
        ]

        if avail:
            lines.append("*Невыданные ссылки:*")
            for i, lnk in enumerate(avail[:10]):
                short = lnk[8:55] + "…" if len(lnk[8:]) > 47 else lnk[8:]
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
                short = lnk[8:42] + "…" if len(lnk[8:]) > 34 else lnk[8:]
                lines.append(f"🔵 `#{inv_id}` → `{short}`")

        return "\n".join(lines)

    def _pool_kb(self, avail: list) -> dict:
        """Клавиатура страницы пула."""
        link_btns = []
        for idx, lnk in enumerate(avail[:8]):
            preview = lnk[8:32] + "…" if len(lnk[8:]) > 24 else lnk[8:]
            link_btns.append([{"text": f"🟢 №{idx + 1} · {preview} — Выдать",
                                "callback_data": f"ggsell:pool_pick:{idx}"}])
        nav = [{"text": "🔄 Обновить", "callback_data": "ggsell:pool"},
               {"text": "◀️ Назад",    "callback_data": "go:ggsell"}]
        return {"inline_keyboard": link_btns + [nav]}

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
            except Exception:
                pass
            self._done_loaded[0] = True
        return self._done

    def mark_done(self, invoice_id: int, link: str = "") -> None:
        dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.get_done()[invoice_id] = dt_str
        if link:
            self._done_links[invoice_id] = link
        try:
            f = _DATA_DIR / "ggsel_done.json"
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                raw = {"done": {}}
            raw.setdefault("done", {})[str(invoice_id)] = dt_str
            if link:
                raw.setdefault("links", {})[str(invoice_id)] = link
            f.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_sent_link(self, invoice_id: int) -> str:
        self.get_done()
        return self._done_links.get(invoice_id, "")

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
        email       = item.get("buyer_email") or p["email"] or ""
        confirm_lnk = self.confirm.get(invoice_id)
        done        = self.get_done()
        sent_link   = self.get_sent_link(invoice_id)

        lines = [
            f"📋 *Заказ GGSell* `#{invoice_id}`",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"📦 *{p['name_short']}*", "",
        ]
        if p["sum_buy"] or p["sum_sell"]:
            parts = []
            if p["sum_buy"]:
                parts.append(f"💰 Оплачено: *{p['sum_buy']}₽*")
            if p["sum_sell"]:
                parts.append(f"💼 Выплата: *{p['sum_sell']}₽*")
            lines.append("  ·  ".join(parts))
        if email:
            lines.append(f"👤 Email: `{email}`")
        if p["date"]:
            lines.append(f"📅 Дата: {p['date']}")
        if p["status"]:
            lines.append(f"📍 Статус API: `{p['status']}`")
        if p["options"]:
            lines.append("")
            lines.append("📝 *Параметры заказа:*")
            for opt in p["options"]:
                n_s = opt["name"][:32] + "…" if len(opt["name"]) > 32 else opt["name"]
                v_s = opt["value"][:45] + "…" if len(opt["value"]) > 45 else opt["value"]
                p_s = f" _(+{opt['price_add']}₽)_" if opt.get("price_add") else ""
                lines.append(f"  • _{n_s}_: `{v_s}`{p_s}")
        lines.append("")
        if invoice_id in done:
            lines.append(f"🔵 *Выдано* · {done[invoice_id]}")
            if sent_link:
                lines.append(f"🔗 `{sent_link}`")
        elif confirm_lnk:
            lines.append("⏳ *Ждёт подтверждения отправки*")
            lines.append(f"🔗 `{confirm_lnk}`")
        else:
            lines.append("🟢 *Новый* — ожидает выполнения")
        return "\n".join(lines)

    def order_kb(self, invoice_id: int) -> dict:
        done        = self.get_done()
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
        rows.append([
            {"text": "💬 Чат",      "callback_data": f"ggsell:chat:{invoice_id}"},
            {"text": "🔄 Обновить", "callback_data": f"ggsell:order:{invoice_id}"},
        ])
        rows.append([{"text": "◀️ Заказы", "callback_data": "ggsell:orders"}])
        return {"inline_keyboard": rows}

    def settings_page(self, cid) -> tuple:
        ord_on = self._get(cid, "ggsel_notify_orders")
        msg_on = self._get(cid, "ggsel_notify_messages")
        lines = [
            "⚙️ *GGSell — Настройки*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            "*Уведомления:*",
            f"  {'🔔' if ord_on else '🔕'} Заказы: {'включены' if ord_on else 'выключены'}",
            f"  {'🔔' if msg_on else '🔕'} Сообщения: {'включены' if msg_on else 'выключены'}",
        ]
        kb = {"inline_keyboard": [
            [{"text": ("🔔 Заказы: Вкл"    if ord_on else "🔕 Заказы: Выкл"),
              "callback_data": "ggsell:toggle:orders"},
             {"text": ("🔔 Сообщения: Вкл" if msg_on else "🔕 Сообщения: Выкл"),
              "callback_data": "ggsell:toggle:messages"}],
            [{"text": "◀️ Назад", "callback_data": "go:ggsell"}],
        ]}
        return "\n".join(lines), kb

    # ── Баланс ───────────────────────────────────────────────────────────────

    async def _fetch_balance(self, cli):
        bal_s = lock_s = plus_s = payment_date_s = ""
        try:
            bi = await cli.get_balance_info()
            bal_s  = f"${bi['free']:.2f}"
            lock_s = f"${bi['lock']:.2f}" if bi["lock"] else ""
            plus_s = f"${bi['plus']:.2f}" if bi["plus"] else ""
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
                "💰 *GGSell*\n\n❌ _Не настроен. Заполните_ `ggsel` _в_ `secrets.yaml`_._",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:other"}]]})
            return

        pool = len(self.read_pool())
        try:
            f = _DATA_DIR / "ggsel_orders.json"
            processed_cnt = len(json.loads(f.read_text(encoding="utf-8")).get("processed", []))
        except Exception:
            processed_cnt = 0

        bal_s, lock_s, plus_s, payment_date_s = await self._fetch_balance(cli)

        total_sales = total_revenue = ""
        try:
            stat = await cli.get_stats()
            if isinstance(stat, dict):
                c = stat.get("content") or stat
                total_sales   = c.get("total_sales") or c.get("cnt_sales") or c.get("cnt") or ""
                total_revenue = c.get("total_revenue") or c.get("revenue") or c.get("sum") or ""
        except Exception:
            pass

        pending_cnt = len(self.confirm)

        lines = ["💰 *GGSell — Панель продавца*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
        lines.append(f"💵 Баланс: *{bal_s}*" + (f"  ·  🔒 {lock_s}" if lock_s else ""))
        if plus_s:
            dp = f" (поступит {payment_date_s})" if payment_date_s else ""
            lines.append(f"⏳ К поступлению: *{plus_s}*{dp}")
        lines.append("")
        if total_sales:
            lines.append(f"🛒 Продаж: *{total_sales}*" + (f"  ·  💰 *${float(total_revenue):.2f}*" if total_revenue else ""))
        lines.append(f"📦 Ссылок в пуле: *{pool}*  ·  ✅ Обработано: *{processed_cnt}*")
        if pending_cnt:
            lines.append(f"⏳ Ждут подтверждения: *{pending_cnt}*")

        kb_rows = [
            [{"text": "📋 Заказы",     "callback_data": "ggsell:orders"},
             {"text": "💬 Чаты",       "callback_data": "ggsell:chats"}],
            [{"text": "📦 Пул ссылок", "callback_data": "ggsell:pool"},
             {"text": "⚙️ Настройки",  "callback_data": "ggsell:settings"}],
            [{"text": "🔄 Обновить",   "callback_data": "ggsell:refresh"},
             {"text": "◀️ Назад",      "callback_data": "go:other"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_orders_page(self, cid, mid):
        cli = self.get_client()
        if cli is None:
            await self._edit(cid, mid, "❌ GGSell не настроен.",
                {"inline_keyboard": [[{"text": "◀️ Назад", "callback_data": "go:ggsell"}]]})
            return

        try:
            orders = await cli.get_last_orders()
            yt_orders = [o for o in orders
                         if int((o.get("product") or {}).get("id") or 0) == YOUTUBE_PREMIUM_PRODUCT_ID]
        except Exception:
            yt_orders = []

        done = self.get_done()
        lines = ["📋 *GGSell — Заказы*", "━━━━━━━━━━━━━━━━━━━━━━", ""]
        order_btns = []

        if yt_orders:
            for o in yt_orders[:10]:
                inv   = o.get("invoice_id") or o.get("id") or "?"
                inv_i = int(inv) if str(inv).isdigit() else 0
                p     = self.parse_order(o)

                if inv_i in done:
                    icon, status_s = "🔵", "Выдано"
                elif inv_i in self.confirm:
                    icon, status_s = "⏳", "Ожидает"
                else:
                    icon, status_s = "🟢", "Новый"

                date_s = p["date"][5:16] if len(p["date"]) >= 16 else p["date"]
                line_parts = [f"{icon} *#{inv}*"]
                if date_s:
                    line_parts.append(date_s)
                if p["sum_buy"]:
                    line_parts.append(f"💰{p['sum_buy']}₽")
                lines.append("  ·  ".join(line_parts))

                sub_parts = []
                if p["email"]:
                    email_s = p["email"][:28] + "…" if len(p["email"]) > 28 else p["email"]
                    sub_parts.append(f"👤 {email_s}")
                sub_parts.append(status_s)
                lines.append("   " + "  ·  ".join(sub_parts))
                lines.append("")

                order_btns.append({"text": f"{icon} #{inv}",
                                   "callback_data": f"ggsell:order:{inv_i}"})
        else:
            lines.append("_Нет последних заказов YouTube Premium_")

        btn_rows = []
        for i in range(0, len(order_btns), 2):
            row = [order_btns[i]]
            if i + 1 < len(order_btns):
                row.append(order_btns[i + 1])
            btn_rows.append(row)

        kb_rows = btn_rows[:5] + [
            [{"text": "🔄 Обновить", "callback_data": "ggsell:orders"},
             {"text": "◀️ Назад",    "callback_data": "go:ggsell"}],
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
            [{"text": "🔄 Обновить", "callback_data": "ggsell:chats"},
             {"text": "◀️ Назад",    "callback_data": "go:ggsell"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_order_view(self, cid, mid, invoice_id: int) -> None:
        cli   = self.get_client()
        item  = self.orders.get(invoice_id, {})
        order = dict(item.get("order", {}))
        if cli and not order.get("selected_options") and not order.get("options"):
            try:
                v2 = await cli.get_order_info_v2(invoice_id)
                if v2:
                    if v2.get("selected_options"):
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
                    item = dict(item)
                    item["order"] = order
                    self.orders[invoice_id] = item
            except Exception:
                pass
        await self._edit(cid, mid, self.order_text(invoice_id), self.order_kb(invoice_id))

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
            [{"text": "🔄 Обновить",  "callback_data": f"ggsell:chat:{invoice_id}"},
             {"text": "📋 Заказ",     "callback_data": f"ggsell:order:{invoice_id}"},
             {"text": "◀️ Чаты",      "callback_data": "ggsell:chats"}],
        ]}
        await self._edit(cid, mid, "\n".join(lines), kb)

    async def bg_pool_pick(self, cid, mid, link: str) -> None:
        cli = self.get_client()
        try:
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
            "Кому отправить? (незавершённые заказы):",
        ]
        order_rows = []
        for o in yt_orders[:10]:
            inv_i = int(o.get("invoice_id") or o.get("id") or 0)
            if inv_i in done:
                continue
            p = self.parse_order(o)
            email_s = f" · {p['email'][:24]}" if p["email"] else ""
            label = f"#{inv_i}{email_s}"
            order_rows.append([{"text": label,
                                 "callback_data": f"ggsell:pool_order:{inv_i}"}])
        if not order_rows:
            lines.append("\n_Все заказы уже выполнены_")
        kb_rows = order_rows[:8] + [
            [{"text": "◀️ Пул", "callback_data": "ggsell:pool"}],
        ]
        await self._edit(cid, mid, "\n".join(lines), {"inline_keyboard": kb_rows})

    async def bg_pool_for(self, cid, mid, invoice_id: int) -> None:
        links = self.read_pool()
        if not links:
            await self._edit(cid, mid,
                f"📦 *Пул ссылок пуст*\n\nНет доступных ссылок для заказа `#{invoice_id}`.",
                {"inline_keyboard": [[{"text": "◀️ Заказ",
                                        "callback_data": f"ggsell:order:{invoice_id}"}]]})
            return
        lines = [
            "📦 *Выбрать ссылку из пула*",
            "━━━━━━━━━━━━━━━━━━━━━━", "",
            f"Заказ: `#{invoice_id}`",
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
        from ggsell.monitor import MSG_TEMPLATE
        ok = await cli.send_message(invoice_id, MSG_TEMPLATE.format(link=link))
        self.remove_link(link)
        if ok:
            self.mark_done(invoice_id, link)
            await self._edit(cid, mid,
                f"✅ *Ссылка отправлена покупателю!*\n\n"
                f"Заказ: `#{invoice_id}`\n🔗 `{link}`",
                {"inline_keyboard": [
                    [{"text": "📦 Пул ссылок", "callback_data": "ggsell:pool"},
                     {"text": "◀️ GGSell",     "callback_data": "go:ggsell"}],
                ]})
        else:
            await self._edit(cid, mid,
                f"❌ Не удалось отправить ссылку заказу `#{invoice_id}`.\n\nСсылка возвращена в пул.",
                {"inline_keyboard": [[{"text": "📦 Пул", "callback_data": "ggsell:pool"}]]})
            from ggsell.monitor import add_link_to_pool
            add_link_to_pool(link)

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
                f"💬 *Ответ на заказ* `#{invoice_id}`\n\n"
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
            from ggsell.monitor import MSG_TEMPLATE
            ok = await cli.send_message(invoice_id, MSG_TEMPLATE.format(link=link))
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

        lines = [f"💸 *Новый заказ* `#{invoice_id}`", "━━━━━━━━━━━━━━━━━━━━━━", ""]
        lines.append(f"📦 *{p['name_short']}*")
        if p["sum_buy"] or p["sum_sell"]:
            sum_parts = []
            if p["sum_buy"]:
                sum_parts.append(f"💰 *{p['sum_buy']}₽*")
            if p["sum_sell"]:
                sum_parts.append(f"💼 выплата *{p['sum_sell']}₽*")
            lines.append("  ·  ".join(sum_parts))
        if p["date"]:
            lines.append(f"📅 {p['date']}")
        if p["options"]:
            lines.append("")
            for opt in p["options"]:
                n_s = opt["name"][:28] + "…" if len(opt["name"]) > 28 else opt["name"]
                v_s = opt["value"][:35] + "…" if len(opt["value"]) > 35 else opt["value"]
                p_s = f" (+{opt['price_add']}₽)" if opt.get("price_add") else ""
                lines.append(f"  _{n_s}_: `{v_s}`{p_s}")
        elif email:
            lines.append(f"👤 `{email}`")
        lines.append("")
        text = "\n".join(lines)
        kb = {"inline_keyboard": [
            [{"text": f"📋 Детали #{invoice_id}",
              "callback_data": f"ggsell:order:{invoice_id}"}],
            [{"text": "▶️ Выполнить заказ",
              "callback_data": f"ggsell:run:{invoice_id}"}],
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

    async def notify_message(self, item: dict) -> None:
        invoice_id = item.get("invoice_id")
        msg        = item.get("message", {})
        chat       = item.get("chat", {})
        email      = chat.get("email") or "?"
        msg_text   = (msg.get("text") or msg.get("message") or msg.get("body") or "…")
        if len(msg_text) > 300:
            msg_text = msg_text[:300] + "…"

        raw_date = (msg.get("date") or msg.get("created_at") or msg.get("timestamp")
                    or msg.get("date_add") or "")
        msg_time = str(raw_date)[:16].replace("T", " ") if raw_date else ""

        text = (
            f"💬 *Новое сообщение · заказ* `#{invoice_id}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📧 {email}" + (f" · 📅 {msg_time}" if msg_time else "") + "\n\n"
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

        if data == "ggsell:orders":
            await self._ack(qid)
            await self._edit(cid, mid, "⏳ *GGSell* — загружаю заказы...",
                             {"inline_keyboard": [[{"text": "◀️ Назад",
                                                     "callback_data": "go:ggsell"}]]})
            asyncio.create_task(self.bg_orders_page(cid, mid))
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

        if data == "ggsell:pool":
            await self._ack(qid)
            avail = self.read_pool()
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
                f"💬 *Ответ на заказ* `#{invoice_id}`\n\n"
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
                f"❌ Ответ на заказ `#{invoice_id}` отменён.",
                {"inline_keyboard": [[{"text": "◀️ GGSell",
                                        "callback_data": "go:ggsell"}]]})
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
            await self._edit(cid, mid, self.order_text(invoice_id), self.order_kb(invoice_id))
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
            if kind in ("orders", "messages"):
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
            avail = self.read_pool()
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

        if data.startswith("ggsell:pool_order:"):
            invoice_id = int(data.split(":")[2])
            link = self.pool_pick_pending.pop(cid, None)
            if not link:
                await self._ack(qid, "❌ Сессия истекла, выберите ссылку снова", alert=True)
                return
            await self._ack(qid, "⏳ Отправляю...")
            asyncio.create_task(self.bg_pool_send(cid, mid, invoice_id, link))
            return

        await self._ack(qid)
