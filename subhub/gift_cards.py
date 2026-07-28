"""Гифт-карты Flipkart: хранилище, парсинг, подбор набора под сумму, аудит.

Слой данных, вынесенный из menu.py. Экраны консоли и браузерная часть оплаты
(_do_gift_card_payment и соседи) остались в menu.py — они завязаны на страницу
Playwright и на карточное меню.

Имена функций сохранены как есть (с подчёркиванием): menu.py импортирует их
под теми же именами, а Telegram-бот достаёт их через _m("_parse_gift_cards").
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from common import atomic_write_text, fmt_msk
from paths import ROOT as _ROOT

_DATA = _ROOT / "data"
GIFT_CARDS_FILE = _DATA / "gift_cards.json"
GIFT_USED_FILE  = _DATA / "gift_cards_used.json"   # аудит использованных
GIFT_DENOMS     = (50, 100, 200, 250, 500, 1000)


def _load_gift_cards() -> list:
    """Список гифт-карт: [{denom:int, number:str, pin:str, used:bool, used_ts}]."""
    if GIFT_CARDS_FILE.exists():
        try:
            v = json.loads(GIFT_CARDS_FILE.read_text(encoding="utf-8"))
            return v if isinstance(v, list) else []
        except Exception:
            pass
    return []


def _save_gift_cards(cards: list) -> None:
    atomic_write_text(GIFT_CARDS_FILE, json.dumps(cards, ensure_ascii=False, indent=2))
    runtime_touch("gift_cards")


def _parse_gift_cards(text: str, default_denom: int | None = None) -> tuple[list, list]:
    """Парсит гифт-карты из текста/CSV. Возвращает (список_карт, ошибки)."""
    import re as _re
    denoms = set(GIFT_DENOMS)
    out, errs = [], []
    # Формат «номер на строке, PIN на следующей» → склеиваем в одну запись
    _lines = [ln.strip() for ln in text.splitlines()]
    items, _i = [], 0
    while _i < len(_lines):
        s = _lines[_i]
        if (_re.fullmatch(r"\d{14,19}", s) and _i + 1 < len(_lines)
                and _re.fullmatch(r"\d{4,8}", _lines[_i + 1])):
            items.append((s, _lines[_i + 1]))
            _i += 2
            continue
        items.append(s)
        _i += 1
    for s in items:
        if isinstance(s, tuple):
            number, pin = s
            if not default_denom:
                errs.append(f"«{number}» — не указан номинал")
                continue
            out.append({"denom": int(default_denom), "number": number,
                        "pin": pin, "used": False})
            continue
        if not s:
            continue
        low = s.lower()
        if (("серия" in low or "series" in low)
                or ("pin" in low and ("дата" in low or "expir" in low or "истеч" in low))
                or ("flipkart" in low and "inr" in low)):
            continue
        s2 = _re.sub(r"\d{4}[-/.]\d{2}[-/.]\d{2}", " ", s)
        s2 = _re.sub(r"\d{2}[-/.]\d{2}[-/.]\d{2,4}", " ", s2)
        m = _re.search(r"\b(\d{14,19})\b", s2)
        number = m.group(1) if m else ""
        rest = s2.replace(number, " ", 1) if number else s2
        denom = default_denom
        for t in _re.findall(r"\b(\d{2,4})\b", rest):
            if int(t) in denoms:
                denom = int(t)
                rest = rest.replace(t, " ", 1)
                break
        pin = ""
        for t in _re.findall(r"\b(\d{4,8})\b", rest):
            pin = t
            break
        if not number:
            errs.append(f"«{s[:40]}» — не найден номер (14–19 цифр)")
            continue
        if not pin:
            errs.append(f"«{s[:40]}» — не найден PIN (4–8 цифр)")
            continue
        if not denom:
            errs.append(f"«{s[:40]}» — не указан номинал")
            continue
        out.append({"denom": int(denom), "number": number, "pin": pin, "used": False})
    return out, errs


def _gift_bytes_to_text(fname: str, raw: bytes) -> tuple[str, str]:
    """Извлекает текст из файла гифт-карт (HTML/Excel/CSV/TXT)."""
    import re as _re2
    _low = (fname or "").lower()
    _sniff = raw[:400].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if (_sniff.startswith(b"<html") or _sniff.startswith(b"<table")
            or b"excel.sheet" in _sniff or b"<table" in raw[:2000].lower()):
        try:
            html = raw.decode("utf-8", "replace")
            _lines = []
            for _tr in _re2.findall(r"<tr[^>]*>(.*?)</tr>", html, _re2.I | _re2.S):
                _cells = _re2.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", _tr, _re2.I | _re2.S)
                _vals = [_re2.sub(r"<[^>]+>", "", c).strip() for c in _cells]
                _vals = [v for v in _vals if v]
                if _vals:
                    _lines.append(" ".join(_vals))
            if _lines:
                return "\n".join(_lines), ""
        except Exception as _he:
            return "", f"Не удалось прочитать HTML-таблицу: {_he}"
    if _low.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            import io as _io2
            import openpyxl as _oxl
            wb = _oxl.load_workbook(_io2.BytesIO(raw), read_only=True, data_only=True)
            _lines = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        _lines.append(" ".join(cells))
            return "\n".join(_lines), ""
        except ImportError:
            return "", "Excel требует openpyxl (pip install openpyxl)"
        except Exception as _xe:
            return "", f"Не удалось прочитать Excel: {_xe}"
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc), ""
        except Exception:
            continue
    return raw.decode("utf-8", "replace"), ""


def _add_gift_cards_from_text(text: str, default_denom: int | None = None) -> dict:
    """Добавляет гифт-карты из текста. Возвращает {added, dup, errs, balance}."""
    parsed, errs = _parse_gift_cards(text, default_denom)
    existing = _load_gift_cards()
    _have = {str(c.get("number")) for c in existing}
    _now = time.time()
    added = dup = 0
    for c in parsed:
        if str(c["number"]) in _have:
            dup += 1
            continue
        c["added_ts"] = _now
        existing.append(c)
        _have.add(str(c["number"]))
        added += 1
    if added:
        _save_gift_cards(existing)
    return {
        "added": added, "dup": dup, "errs": errs,
        "balance": _gift_balance(existing), "total": len(existing),
    }


def _mask_gift(number: str) -> str:
    n = "".join(ch for ch in str(number) if ch.isalnum())
    return f"…{n[-4:]}" if len(n) >= 4 else (n or "?")


def _gift_balance(cards: list | None = None) -> int:
    """Сумма номиналов неиспользованных гифт-карт."""
    cards = cards if cards is not None else _load_gift_cards()
    return sum(int(c.get("denom") or 0) for c in cards
               if not c.get("used") and c.get("number") and c.get("pin"))


def _select_gift_cards(total: int, cards: list | None = None):
    """Подбирает набор НЕиспользованных гифт-карт с суммой >= total и МИНИМАЛЬНЫМ
    превышением (меньше «сгорает» баланса), при равенстве — меньше карт.
    Возвращает (список_карт, сумма_набора) или (None, доступный_баланс)."""
    cards = cards if cards is not None else _load_gift_cards()
    unused = [c for c in cards
              if not c.get("used")
              and str(c.get("number") or "").strip()
              and str(c.get("pin") or "").strip()
              and int(c.get("denom") or 0) > 0]
    units = [int(c.get("denom")) // 50 for c in unused]  # номиналы кратны 50
    total_u = -(-int(total) // 50)  # ceil(total/50)
    max_s = sum(units)
    if max_s < total_u:
        return None, max_s * 50
    # 0/1-рюкзак: для каждой достижимой суммы — набор индексов с наим. числом карт
    dp: list = [None] * (max_s + 1)
    dp[0] = []
    for i, u in enumerate(units):
        if u <= 0:
            continue
        for s in range(max_s, u - 1, -1):
            if dp[s - u] is not None:
                cand = dp[s - u] + [i]
                if dp[s] is None or len(cand) < len(dp[s]):
                    dp[s] = cand
    best_s = next((s for s in range(total_u, max_s + 1) if dp[s] is not None), None)
    if best_s is None:
        return None, max_s * 50
    picked = [unused[i] for i in dp[best_s]]
    if len(picked) > 15:
        # Flipkart: не более 15 карт за транзакцию — жадно по убыванию (меньше карт)
        unused.sort(key=lambda c: int(c.get("denom") or 0), reverse=True)
        picked, acc = [], 0
        for c in unused:
            if acc >= total:
                break
            picked.append(c)
            acc += int(c.get("denom") or 0)
        if len(picked) > 15 or acc < total:
            return None, _gift_balance(cards)
        return picked, acc
    return picked, best_s * 50


def _gift_shortage_report(need_amount: int):
    """Отчёт по складу vs остатку заказа.
    «Не хватает» = сколько ДОБАВИТЬ в хранилище (need − bal), не «осталось покрыть».
    Возвращает (текст, баланс, округл_нужно, нехватка_на_складе)."""
    cards = [c for c in _load_gift_cards()
             if not c.get("used") and c.get("number") and c.get("pin")
             and int(c.get("denom") or 0) > 0]
    bal = sum(int(c.get("denom")) for c in cards)
    need = -(-int(need_amount) // 50) * 50   # округление вверх до кратного 50
    short = max(0, need - bal)
    by: dict = {}
    for c in cards:
        d = int(c.get("denom"))
        by[d] = by.get(d, 0) + 1
    breakdown = "  ·  ".join(f"₹{d}×{by[d]}" for d in sorted(by, reverse=True)) or "карт нет"
    lines = [
        f"Осталось покрыть: ₹{need}" + (
            f"  (цена ₹{need_amount}, гифт-картами кратно 50)"
            if need != need_amount else ""
        ),
        f"В хранилище: ₹{bal}  →  {breakdown}",
    ]
    if short > 0:
        lines.append(
            f"Не хватает на складе: ₹{short}  "
            f"(добавьте карт на эту сумму, напр. {max(1, short // 50)}×₹50)"
        )
    else:
        # bal >= need: сумма карт ок, но оплата могла встать (крупные без ОК / брак)
        lines.append(
            f"На складе хватает (₹{bal} ≥ ₹{need}) — дело не в сумме карт"
        )
    return "\n".join(lines), bal, need, short


def _load_gift_used() -> list:
    if GIFT_USED_FILE.exists():
        try:
            v = json.loads(GIFT_USED_FILE.read_text(encoding="utf-8"))
            return v if isinstance(v, list) else []
        except Exception:
            pass
    return []


def _mark_gift_used(card: dict, profile_path=None, status: str = "used") -> None:
    """Помечает гифт-карту использованной: удаляет из хранилища и пишет в аудит-лог.
    status="used" — применена к этому профилю (пишется и в мету профиля).
    status="used_elsewhere" — уже использована/добавлена на ДРУГОМ аккаунте
    (Flipkart отклонил): удаляем и логируем, но в баланс профиля НЕ пишем."""
    import time as _t_gu
    num = str(card.get("number") or "").strip()
    pin = str(card.get("pin") or "").strip()
    denom = int(card.get("denom") or 0)
    ts = _t_gu.time()
    prof_name = ""
    try:
        prof_name = Path(profile_path).name if profile_path else ""
    except Exception:
        prof_name = ""
    # 1. Удаляем из хранилища (по номеру)
    try:
        remaining = [c for c in _load_gift_cards()
                     if str(c.get("number") or "").strip() != num]
        _save_gift_cards(remaining)
    except Exception:
        pass
    # 2. Аудит-лог использованных
    try:
        log = _load_gift_used()
        log.append({"denom": denom, "number": num, "pin": pin, "used_ts": ts,
                    "used_str": fmt_msk(ts), "profile": prof_name, "status": status})
        atomic_write_text(GIFT_USED_FILE, json.dumps(log, ensure_ascii=False, indent=2))
    except Exception:
        pass
    # 3. Запись в мету профиля — только если карта реально применена к этому профилю
    if profile_path and status == "used":
        try:
            _mf = Path(profile_path) / ".profile_meta.json"
            _meta = json.loads(_mf.read_text(encoding="utf-8")) if _mf.exists() else {}
            if not isinstance(_meta, dict):
                _meta = {}
            _gcu = _meta.get("gift_cards_used")
            if not isinstance(_gcu, list):
                _gcu = []
            _gcu.append({"denom": denom, "number": num, "pin": pin,
                         "used_ts": ts, "used_str": fmt_msk(ts)})
            _meta["gift_cards_used"] = _gcu
            atomic_write_text(_mf, json.dumps(_meta, ensure_ascii=False, indent=2))
        except Exception:
            pass
