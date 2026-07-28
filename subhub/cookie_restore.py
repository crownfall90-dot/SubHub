"""Бэкапы куков Flipkart: поиск файла и оценка живости сессии по JWT.

Вынесено из menu.py. Здесь только то, что работает без браузера; сам импорт
куков в профиль (_restore_profile_from_cookies и соседи) остался в menu.py —
он завязан на Playwright, VPN и запуск профиля.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from common import fmt_msk as _fmt_msk


def _cookies_backup_for_phone(phone: str) -> Path | None:
    """Путь к cookies_backup/cookies_*.json по 10-значному номеру."""
    phone10 = "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]
    if not phone10:
        return None
    bk_dir = Path("cookies_backup")
    if not bk_dir.is_dir():
        return None
    direct = bk_dir / f"cookies_{phone10}.json"
    if direct.exists():
        return direct
    for p in sorted(bk_dir.glob(f"cookies_*{phone10}.json")):
        if p.is_file():
            return p
    return None


def _cookie_backup_state(cookies_json_path) -> tuple[str, str]:
    """Проверяет срок жизни токенов в бэкапе куков БЕЗ запуска браузера.

    Flipkart: `at` (access token) живёт ~30 минут — просроченный `at` это норма,
    сессию поднимает `rt` (refresh token, ~180 дней). Если истёк `rt` — куками
    не поднять вообще, нужен свежий вход по OTP.
    Возвращает (state, описание): "ok" | "at_expired" | "dead" | "unknown".
    """
    import base64 as _b64
    try:
        raw = json.loads(Path(cookies_json_path).read_text(encoding="utf-8"))
    except Exception as e:
        return "unknown", f"не прочитал JSON: {e}"
    if not isinstance(raw, list) or not raw:
        return "dead", "JSON пустой — куков нет"
    vals = {c.get("name"): c.get("value") for c in raw if isinstance(c, dict)}

    def _exp(tok: str) -> float | None:
        parts = str(tok or "").split(".")
        if len(parts) < 2:
            return None
        try:
            pl = json.loads(_b64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            e = float(pl.get("exp") or 0)
        except Exception:
            return None
        return (e / 1000 if e > 1e11 else e) or None

    now = time.time()
    at_e, rt_e = _exp(vals.get("at")), _exp(vals.get("rt"))
    if "rt" not in vals:
        return "dead", "в бэкапе нет rt (refresh-token) — сессию не поднять"
    if rt_e and rt_e <= now:
        return "dead", f"rt истёк {_fmt_msk(rt_e)} — нужен свежий вход по OTP"
    _rt_txt = f"rt действителен до {_fmt_msk(rt_e)}" if rt_e else "срок rt неизвестен"
    if at_e and at_e <= now:
        return "at_expired", f"at истёк {_fmt_msk(at_e)}, {_rt_txt} — поднимаем через rt"
    return "ok", _rt_txt


_COOKIE_DEAD_HINT = ("сервер отозвал сессию (вход с другого устройства / logout) — "
                     "тот же файл куков уже не поможет, нужен свежий вход по OTP")
