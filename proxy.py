"""
proxy.py — Proxy management and Proxy6.net API integration.
Extracted from menu.py.
"""

import asyncio
import json
import os
import pathlib
import threading
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

# ── ANSI цвета (локальные копии с префиксом _ чтобы не конфликтовали) ────────
_R   = "\033[91m"
_G   = "\033[92m"
_Y   = "\033[93m"
_C   = "\033[96m"
_DIM = "\033[2m"
_BLD = "\033[1m"
_RST = "\033[0m"

# ── Глобальные переменные прокси ──────────────────────────────────────────────
_MAX_PROXY_FAILS = 2
_proxy_list_cache: list = []
_proxy_rr_idx: int = 0
_proxy_cache_loaded: bool = False
_proxy_fail_count: dict = {}
_last_proxy_server: str = ""
_local_proxy_servers: dict = {}   # port → asyncio.Server


def _proxy_cfg_path() -> Path:
    return Path(__file__).parent / "config.yaml"


def _read_proxy_cfg() -> dict:
    """Читает секцию proxy из config.yaml. Всегда свежая копия."""
    try:
        import yaml as _y
        with open(_proxy_cfg_path(), encoding="utf-8") as _f:
            cfg = _y.safe_load(_f) or {}
        pcfg = dict(cfg.get("proxy") or {})
        pcfg["enabled"] = False  # прокси отключены глобально (временно)
        return pcfg
    except Exception:
        return {"enabled": False}


def _write_proxy_cfg(pcfg: dict) -> None:
    """Записывает секцию proxy обратно в config.yaml. Атомарная запись через temp-файл."""
    import yaml as _y
    path = _proxy_cfg_path()
    try:
        with open(path, encoding="utf-8") as _f:
            cfg = _y.safe_load(_f) or {}
    except Exception as _re:
        print(f"  {_Y}⚠ Не удалось прочитать config.yaml перед записью: {_re} — запись отменена{_RST}")
        return
    cfg["proxy"] = pcfg
    tmp = path.with_suffix(".yaml.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as _f:
            _y.dump(cfg, _f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp.replace(path)
    except Exception as _we:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
        print(f"  {_Y}⚠ Не удалось сохранить config.yaml: {_we}{_RST}")
        return
    global _proxy_list_cache, _proxy_cache_loaded
    _proxy_list_cache = []
    _proxy_cache_loaded = False


def _load_proxy_list(force: bool = False) -> list:
    """Читает список прокси из config.yaml. Кэширует на сессию.
    enabled: false — всегда возвращает [] независимо от force.
    force=True — игнорирует кэш и перечитывает список."""
    global _proxy_list_cache, _proxy_cache_loaded
    if _proxy_cache_loaded and not force:
        return _proxy_list_cache
    try:
        pcfg = _read_proxy_cfg()
        # enabled=false — мастер-выключатель, игнорируется даже при force=True
        if not pcfg.get("enabled"):
            _proxy_cache_loaded = True
            _proxy_list_cache = []
            return []
        proxies: list = []
        if pcfg.get("list"):
            for p in pcfg["list"]:
                if p and p.get("server"):
                    entry = {"server": str(p["server"])}
                    if p.get("username"): entry["username"] = str(p["username"])
                    if p.get("password"): entry["password"] = str(p["password"])
                    if p.get("expires"):  entry["expires"]  = str(p["expires"])
                    if p.get("p6id"):     entry["p6id"]     = str(p["p6id"])
                    if p.get("country"):  entry["country"]  = str(p["country"])
                    proxies.append(entry)
        elif pcfg.get("server"):
            entry = {"server": str(pcfg["server"])}
            if pcfg.get("username"): entry["username"] = str(pcfg["username"])
            if pcfg.get("password"): entry["password"] = str(pcfg["password"])
            proxies.append(entry)
        _proxy_cache_loaded = True
        _proxy_list_cache = proxies
        if proxies:
            print(f"  {_DIM}Прокси: загружено {len(proxies)} шт.{_RST}")
        return proxies
    except Exception:
        # Не помечаем кэш как загруженный — при следующем вызове повторная попытка
        return _proxy_list_cache


def _proxy_server_bare(server: str) -> str:
    """Возвращает host:port без схемы и учётных данных — ключ для счётчика отказов."""
    if "://" in server:
        server = server.split("://", 1)[1]
    if "@" in server:
        server = server.split("@", 1)[1]
    return server


def _is_proxy_error(exc) -> bool:
    msg = str(exc)
    return any(e in msg for e in (
        "ERR_TUNNEL_CONNECTION_FAILED", "ERR_INVALID_AUTH_CREDENTIALS",
        "ERR_PROXY_CONNECTION_FAILED", "ERR_NO_SUPPORTED_PROXIES",
        "net::ERR_PROXY", "ERR_CONNECTION_REFUSED", "ERR_CONNECTION_TIMED_OUT",
        "ERR_SOCKS_CONNECTION_FAILED", "ERR_EMPTY_RESPONSE",
        "ERR_CONNECTION_RESET", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_ABORTED",
        "ERR_ADDRESS_UNREACHABLE", "ERR_NETWORK_CHANGED",
        # Chrome крашится сразу при запуске через нерабочий прокси
        "Target page, context or browser has been closed",
    ))


def _mark_proxy_failed(server: str):
    global _proxy_fail_count
    bare = _proxy_server_bare(server)
    _proxy_fail_count[bare] = _proxy_fail_count.get(bare, 0) + 1
    print(f"  {_Y}⚠ Прокси {bare} помечен как нерабочий "
          f"(отказов: {_proxy_fail_count[bare]}){_RST}")


def _mark_proxy_ok(server: str):
    _proxy_fail_count.pop(_proxy_server_bare(server), None)


def _phone_from_path(profile_path: Path) -> str:
    """Извлекает 10-значный номер из папки профиля (profile_9876543210 → 9876543210)."""
    name = profile_path.name
    parts = name.rsplit("_", 1)
    if len(parts) > 1:
        candidate = parts[-1]
        if candidate.isdigit():
            return candidate
    return ""


def _pick_proxy(force: bool = False, phone: str = "",
                skip_servers=None):
    """Возвращает прокси для данного сеанса.
    phone задан       → детерминировано: один и тот же аккаунт всегда на одном IP.
    phone пустой      → round-robin.
    force=True        → берёт прокси даже если enabled: false.
    skip_servers      → множество bare host:port которые нужно пропустить."""
    import hashlib as _hl
    global _proxy_rr_idx, _last_proxy_server
    proxies = _load_proxy_list(force=force)
    if not proxies:
        return None
    skip = skip_servers or set()
    # Отсеиваем прокси с >= _MAX_PROXY_FAILS отказами и явно исключённые
    live = [p for p in proxies
            if _proxy_fail_count.get(_proxy_server_bare(p.get("server", "")), 0) < _MAX_PROXY_FAILS
            and _proxy_server_bare(p.get("server", "")) not in skip]
    if not live:
        # Все прокси пробовали — сбрасываем счётчики и берём без skip
        _proxy_fail_count.clear()
        live = [p for p in proxies if _proxy_server_bare(p.get("server", "")) not in skip]
    if not live:
        live = [p for p in proxies if _proxy_server_bare(p.get("server", "")) not in skip] or proxies
    if phone:
        idx = int(_hl.md5(phone.encode()).hexdigest(), 16) % len(live)
    else:
        idx = _proxy_rr_idx % len(live)
        _proxy_rr_idx += 1
    chosen = live[idx]
    _last_proxy_server = chosen.get("server", "")
    return chosen


# ── Локальный HTTP CONNECT туннель (авто-авторизация прокси) ─────────────────
# Chrome подключается к 127.0.0.1:PORT без пароля — диалога нет.
# Туннель сам добавляет Proxy-Authorization к CONNECT-запросам upstream.

async def _lp_pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try: writer.close()
        except Exception: pass


async def _lp_handle(reader, writer, up_host: str, up_port: int, auth_b64: str):
    uw = None
    _uw_owned = True   # мы владеем uw и должны закрыть его в finally
    try:
        first = await asyncio.wait_for(reader.readline(), timeout=10)
        if not first:
            return
        parts = first.decode("latin-1", errors="replace").split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        target = parts[1]
        while True:                         # пропускаем заголовки от Chrome
            h = await asyncio.wait_for(reader.readline(), timeout=5)
            if h in (b"\r\n", b"\n", b""):
                break
        ur, uw = await asyncio.wait_for(
            asyncio.open_connection(up_host, up_port), timeout=15)
        uw.write(
            f"CONNECT {target} HTTP/1.1\r\n"
            f"Host: {target}\r\n"
            f"Proxy-Authorization: Basic {auth_b64}\r\n"
            f"Proxy-Connection: keep-alive\r\n\r\n"
            .encode("latin-1"))
        await uw.drain()
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = await asyncio.wait_for(ur.read(4096), timeout=15)
            if not chunk:
                break
            resp += chunk
        if b" 200 " in resp.split(b"\r\n")[0]:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            _uw_owned = False  # _lp_pipe закроет uw сам в своём finally
            await asyncio.gather(_lp_pipe(reader, uw), _lp_pipe(ur, writer))
        else:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
    except Exception:
        pass
    finally:
        try: writer.close()
        except Exception: pass
        if _uw_owned and uw is not None:
            try: uw.close()
            except Exception: pass


async def _start_local_auth_proxy(up_host: str, up_port: int,
                                   username: str, password: str) -> int:
    """Запускает локальный CONNECT-туннель на свободном порту. Возвращает порт."""
    import base64 as _b64
    auth_b64 = _b64.b64encode(f"{username}:{password}".encode()).decode()

    async def _h(r, w):
        await _lp_handle(r, w, up_host, up_port, auth_b64)

    server = await asyncio.start_server(_h, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    _local_proxy_servers[port] = server
    return port


async def _stop_local_auth_proxy(port: int) -> None:
    server = _local_proxy_servers.pop(port, None)
    if server:
        server.close()
        try: await asyncio.wait_for(server.wait_closed(), timeout=2)
        except Exception: pass


# ── Proxy6.net API ────────────────────────────────────────────────────────────

def _p6_cfg() -> dict:
    """Читает секцию proxy6 из config.yaml; api_key — всегда из secrets.yaml."""
    result: dict = {}
    try:
        import yaml as _y
        with open(_proxy_cfg_path(), encoding="utf-8") as _f:
            result = (_y.safe_load(_f) or {}).get("proxy6") or {}
    except Exception:
        pass
    result.pop("api_key", None)
    # api_key всегда из secrets.yaml (единственный источник)
    try:
        import yaml as _y
        _sp = Path(_proxy_cfg_path()).parent / "secrets.yaml"
        if _sp.exists():
            with open(_sp, encoding="utf-8") as _sf:
                _sec = _y.safe_load(_sf) or {}
            result["api_key"] = (_sec.get("proxy6") or {}).get("api_key", "")
    except Exception:
        pass
    return result


def _p6_write_cfg(p6: dict) -> None:
    import yaml as _y
    path = _proxy_cfg_path()
    try:
        with open(path, encoding="utf-8") as _f:
            cfg = _y.safe_load(_f) or {}
    except Exception as _re:
        print(f"  {_Y}⚠ Не удалось прочитать config.yaml перед записью proxy6: {_re}{_RST}")
        return
    cfg["proxy6"] = p6
    tmp = path.with_suffix(".yaml.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as _f:
            _y.dump(cfg, _f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        tmp.replace(path)
    except Exception as _we:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
        print(f"  {_Y}⚠ Не удалось сохранить config.yaml (proxy6): {_we}{_RST}")


def _p6_api(key: str, method: str, _mutating: bool = False, **params) -> dict:
    """GET запрос к API px6.link. Обходит системный прокси.
    _mutating=True — один запрос без повторов (buy, prolong, delete не идемпотентны).
    _mutating=False — до 3 попыток (для read-only методов: getprice, getproxy и т.д.)."""
    import urllib.request, urllib.parse, json as _j, time as _t
    url = f"https://px6.link/api/{key}/{method}/"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # Явно отключаем системный прокси — иначе urllib попытается использовать
    # тот же прокси что и браузер (который может быть мёртв или не пропускать HTTPS)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    max_attempts = 1 if _mutating else 3
    last_err = None
    raw = ""
    for attempt in range(max_attempts):
        try:
            with opener.open(req, timeout=30) as r:
                raw = r.read().decode()
            break
        except Exception as _e:
            last_err = _e
            if attempt < max_attempts - 1:
                _t.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"px6.link недоступен: {last_err}")
    try:
        data = _j.loads(raw)
    except _j.JSONDecodeError:
        raise RuntimeError(f"px6.link вернул не-JSON: {raw[:200]}")
    if data.get("status") != "yes":
        raise RuntimeError(str(data.get("error_id", "?")) + ": " + str(data.get("error", "")))
    return data


def _p6_balance(key: str) -> tuple:
    """Возвращает (баланс, валюта) — баланс есть в любом ответе API."""
    d = _p6_api(key, "getprice")
    return d.get("balance", "?"), d.get("currency", "RUB")


def _p6_buy(key: str, count: int, period: int, country: str = "in",
            proxy_type: str = "http") -> list:
    """
    Покупает count IPv4 Shared прокси на period дней на px6.link.
    version=3 = IPv4 Shared (дешевле), version=4 = выделенный IPv4.
    Возвращает список dict готовых к использованию прокси.
    """
    d = _p6_api(key, "buy", _mutating=True,
                count=count, period=period,
                country=country, version=3, type=proxy_type)
    result = []
    for p6id, item in (d.get("list") or {}).items():
        host = item.get("host") or item.get("ip", "")
        port = item.get("port", "")
        user = item.get("user", "")
        pwd  = item.get("pass", "")
        exp  = item.get("date_end", "")
        if host and port:
            _scheme = "socks5" if proxy_type in ("socks", "socks5") else "http"
            entry = {"server": f"{_scheme}://{host}:{port}", "p6id": str(p6id),
                     "country": country.upper()}
            if user: entry["username"] = user
            if pwd:  entry["password"] = pwd
            if exp:  entry["expires"]  = exp
            result.append(entry)
    return result


def _p6_getlist(key: str, state: str = "active") -> list:
    """Загружает прокси из аккаунта px6.link (метод getproxy)."""
    d = _p6_api(key, "getproxy", state=state)
    result = []
    for p6id, item in (d.get("list") or {}).items():
        host    = item.get("host") or item.get("ip", "")
        port    = item.get("port", "")
        user    = item.get("user", "")
        pwd     = item.get("pass", "")
        exp     = item.get("date_end", "")
        country = item.get("country", "").upper()
        if host and port:
            _ptype  = item.get("type", "http")
            _scheme = "socks5" if _ptype in ("socks", "socks5") else "http"
            entry = {"server": f"{_scheme}://{host}:{port}", "p6id": str(p6id)}
            if user:    entry["username"] = user
            if pwd:     entry["password"] = pwd
            if exp:     entry["expires"]  = exp
            if country: entry["country"]  = country
            result.append(entry)
    return result


def _p6_prolong(key: str, ids: list, period: int) -> int:
    """Продлевает прокси по списку p6id на period дней. Возвращает кол-во."""
    d = _p6_api(key, "prolong", _mutating=True, period=period, ids=",".join(ids))
    return int(d.get("count", 0))


def _p6_buy_affordable(key: str, count: int, period: int, country: str = "in",
                       proxy_type: str = "http") -> tuple:
    """Покупает count прокси; если баланса не хватает — покупает сколько можно.
    Возвращает (список прокси, информационное сообщение)."""
    try:
        proxies = _p6_buy(key, count, period, country=country, proxy_type=proxy_type)
        return proxies, f"Куплено {len(proxies)} шт."
    except RuntimeError as _e:
        if "400" not in str(_e):
            raise
        # Ошибка 400 = недостаточно средств.
        # Узнаём цену одного прокси и текущий баланс через getprice.
        pd = _p6_api(key, "getprice", count=1, period=period, version=3)
        balance     = float(pd.get("balance", 0) or 0)
        price_one   = float(pd.get("price_single", 0) or 0)
        if price_one <= 0:
            raise RuntimeError(f"Баланс исчерпан (баланс: {balance:.2f} ₽)")
        affordable = int(balance / price_one)
        if affordable <= 0:
            raise RuntimeError(
                f"Недостаточно средств: баланс {balance:.2f} ₽, "
                f"цена одного прокси {price_one:.2f} ₽"
            )
        proxies = _p6_buy(key, affordable, period, country=country, proxy_type=proxy_type)
        return proxies, (
            f"Куплено {len(proxies)} из {count} шт. "
            f"(баланса хватило на {affordable}, остаток ≈ {balance - price_one * affordable:.2f} ₽)"
        )
