"""Хранилище привязок «заказ GGSell ↔ Chrome-профиль».

Один заказ может обслуживаться несколькими профилями: если покупателю
понадобилась вторая ссылка (первая не сработала, продление и т.п.), к тому же
заказу привязывается второй профиль. Поэтому связь здесь — 1:N.

Данные лежат в data/ggsel_done.json. Исторически там были одиночные карты
`links: {inv: url}` и `profile_paths: {inv: path}`; они по-прежнему пишутся и
читаются ради совместимости со старыми записями и внешним кодом, а полная
история живёт в `bindings: {inv: [{profile_path, link, ts, buyer_email}, …]}`.

Модуль используют и Telegram-бот, и консоль, поэтому вся запись идёт атомарно
и под общим замком — параллельные потоки не затирают файл друг друга.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from paths import ROOT

_DONE_FILE = ROOT / "data" / "ggsel_done.json"
_LOCK = threading.RLock()


# ── низкоуровневый доступ к файлу ────────────────────────────────────────────

def _load() -> dict:
    try:
        raw = json.loads(_DONE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save(raw: dict) -> None:
    """Атомарная запись: сначала во временный файл, потом подмена."""
    try:
        _DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _DONE_FILE.with_suffix(f".json.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_DONE_FILE)
    except Exception:
        pass


def _norm_path(p) -> str:
    """Ключ профиля — путь относительно корня проекта.

    В файле исторически лежат относительные пути (chrome_profiles_done\\…),
    а код чаще передаёт абсолютные. Без приведения к общему виду один и тот же
    профиль попал бы в список дважды. Наружу пути отдаёт _abs_path().
    """
    if not p:
        return ""
    try:
        path = Path(p)
        # Относительные пути в файле заданы от корня проекта, а не от cwd,
        # поэтому достраиваем их сами — иначе resolve() промахнётся
        if not path.is_absolute():
            path = ROOT / path
        try:
            path = path.resolve().relative_to(ROOT.resolve())
        except (ValueError, OSError):
            pass
        return str(path).rstrip("\\/")
    except Exception:
        return str(p).strip()


def _abs_path(p) -> str:
    """Путь профиля для работы с диском: относительный достраиваем от корня."""
    if not p:
        return ""
    try:
        path = Path(p)
        return str(path if path.is_absolute() else (ROOT / path))
    except Exception:
        return str(p)


# ── чтение привязок ──────────────────────────────────────────────────────────

def get_bindings(invoice_id: int) -> list[dict]:
    """Все профили, привязанные к заказу: [{profile_path, link, ts, buyer_email}].

    Старые записи (одиночные profile_paths/links) поднимаются на лету, так что
    заказы, оформленные до перехода на 1:N, тоже показывают свою привязку.
    """
    return _bindings_from(_load(), invoice_id)


def _bindings_from(raw: dict, invoice_id: int) -> list[dict]:
    key = str(invoice_id)
    out: list[dict] = []
    seen: set[str] = set()

    for b in (raw.get("bindings", {}).get(key) or []):
        if not isinstance(b, dict):
            continue
        pp = _norm_path(b.get("profile_path"))
        out.append({
            "profile_path": _abs_path(pp),
            "link":         b.get("link") or "",
            "ts":           float(b.get("ts") or 0),
            "buyer_email":  b.get("buyer_email") or "",
        })
        if pp:
            seen.add(pp.lower())

    # Совместимость: заказ из старого формата ещё не переехал в bindings
    legacy_pp = _norm_path(raw.get("profile_paths", {}).get(key))
    if legacy_pp and legacy_pp.lower() not in seen:
        out.append({
            "profile_path": _abs_path(legacy_pp),
            "link":         raw.get("links", {}).get(key) or "",
            "ts":           0.0,
            "buyer_email":  raw.get("buyer_emails", {}).get(key) or "",
        })
    return out


def get_bound_profiles(invoice_id: int) -> list[str]:
    """Пути профилей, привязанных к заказу (в порядке привязки)."""
    return [b["profile_path"] for b in get_bindings(invoice_id) if b["profile_path"]]


def get_links(invoice_id: int) -> list[str]:
    """Все выданные по заказу ссылки, без повторов и пустых."""
    out: list[str] = []
    for b in get_bindings(invoice_id):
        link = b.get("link") or ""
        if link and link not in out:
            out.append(link)
    return out


def get_orders_for_profile(profile_path) -> list[int]:
    """Заказы, к которым привязан профиль. Обычно один, но может быть больше."""
    target = _norm_path(profile_path).lower()
    if not target:
        return []
    raw = _load()
    invoices: set[int] = set()

    for key in set(list(raw.get("bindings", {}).keys())
                   + list(raw.get("profile_paths", {}).keys())):
        try:
            inv = int(key)
        except (TypeError, ValueError):
            continue
        for b in _bindings_from(raw, inv):
            # _bindings_from отдаёт абсолютные пути — сравниваем по общему ключу
            if _norm_path(b["profile_path"]).lower() == target:
                invoices.add(inv)
                break
    return sorted(invoices)


def is_profile_bound(profile_path) -> bool:
    return bool(get_orders_for_profile(profile_path))


def counts_by_order() -> dict[int, int]:
    """{invoice_id: сколько профилей привязано} — для пометок в списках."""
    raw = _load()
    out: dict[int, int] = {}
    for key in set(list(raw.get("bindings", {}).keys())
                   + list(raw.get("profile_paths", {}).keys())):
        try:
            inv = int(key)
        except (TypeError, ValueError):
            continue
        n = len([b for b in _bindings_from(raw, inv) if b["profile_path"]])
        if n:
            out[inv] = n
    return out


# ── статусы заказов ──────────────────────────────────────────────────────────

def get_done() -> dict[int, str]:
    """{invoice_id: 'YYYY-MM-DD HH:MM'} — заказы, по которым ссылка отправлена."""
    out: dict[int, str] = {}
    for k, v in (_load().get("done", {}) or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            continue
    return out


def get_refunded() -> dict[int, str]:
    out: dict[int, str] = {}
    for k, v in (_load().get("refunded", {}) or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            continue
    return out


def get_used() -> set[int]:
    out: set[int] = set()
    for k in (_load().get("used", {}) or {}):
        try:
            out.add(int(k))
        except (TypeError, ValueError):
            continue
    return out


# ── запись привязки ──────────────────────────────────────────────────────────

def add_binding(invoice_id: int, profile_path, link: str = "",
                buyer_email: str = "", *, mark_done: bool = True) -> int:
    """Привязывает профиль к заказу. Возвращает итоговое число привязок.

    Повторная привязка того же профиля не плодит дубль — обновляет ссылку.
    Заказ помечается выполненным (mark_done=False — если ссылку ещё не слали).
    """
    inv_key = str(int(invoice_id))
    pp = _norm_path(profile_path)
    now = time.time()

    with _LOCK:
        raw = _load()
        blist = raw.setdefault("bindings", {}).setdefault(inv_key, [])

        # Переносим старую одиночную привязку, иначе она потеряется при записи.
        # В файл пути кладём в относительном виде — как и все остальные.
        if not blist:
            for b in _bindings_from(raw, int(invoice_id)):
                if b["profile_path"]:
                    blist.append({**b,
                                  "profile_path": _norm_path(b["profile_path"])})

        # Без пути профиля привязывать нечего — но заказ всё равно отмечаем
        if pp:
            found = None
            for b in blist:
                if isinstance(b, dict) and \
                        _norm_path(b.get("profile_path")).lower() == pp.lower():
                    found = b
                    break
            if found is None:
                blist.append({"profile_path": pp, "link": link,
                              "ts": now, "buyer_email": buyer_email})
            else:
                if link:
                    found["link"] = link
                if buyer_email:
                    found["buyer_email"] = buyer_email
                found["ts"] = now

        # Одиночные карты — «последняя выданная», для старого кода и бота
        if pp:
            raw.setdefault("profile_paths", {})[inv_key] = pp
        if link:
            raw.setdefault("links", {})[inv_key] = link
        if buyer_email:
            raw.setdefault("buyer_emails", {})[inv_key] = buyer_email
        if mark_done:
            raw.setdefault("done", {})[inv_key] = \
                datetime.now().strftime("%Y-%m-%d %H:%M")

        _save(raw)
        return len([b for b in blist if _norm_path(b.get("profile_path"))])


def remove_binding(invoice_id: int, profile_path) -> bool:
    """Снимает привязку профиля к заказу. True — если что-то удалили."""
    inv_key = str(int(invoice_id))
    target = _norm_path(profile_path).lower()
    if not target:
        return False

    with _LOCK:
        raw = _load()
        blist = raw.setdefault("bindings", {}).setdefault(inv_key, [])
        if not blist:
            for b in _bindings_from(raw, int(invoice_id)):
                if b["profile_path"]:
                    blist.append({**b,
                                  "profile_path": _norm_path(b["profile_path"])})

        before = len(blist)
        blist[:] = [b for b in blist
                    if _norm_path(b.get("profile_path")).lower() != target]
        if len(blist) == before:
            return False

        # Одиночные карты подтягиваем к оставшейся последней привязке
        if _norm_path(raw.get("profile_paths", {}).get(inv_key)).lower() == target:
            if blist:
                last = blist[-1]
                raw["profile_paths"][inv_key] = _norm_path(last.get("profile_path"))
                if last.get("link"):
                    raw.setdefault("links", {})[inv_key] = last["link"]
            else:
                raw["profile_paths"].pop(inv_key, None)
                raw.get("links", {}).pop(inv_key, None)

        _save(raw)
        return True


def bind_meta_fields(invoice_id: int, link: str = "",
                     buyer_email: str = "") -> dict:
    """Поля для .profile_meta.json профиля, привязываемого к заказу."""
    fields: dict = {"issued_ts": time.time(), "issued_invoice_id": int(invoice_id)}
    if link:
        fields["issued_link"] = link
    if buyer_email:
        fields["buyer_email"] = buyer_email
    return fields
