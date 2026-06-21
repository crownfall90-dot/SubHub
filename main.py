"""
Browser Login Automation using Playwright
Автоматизация входа на сайты с изолированными профилями Chrome
"""

import asyncio
import json
import random
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import yaml
from loguru import logger
from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from grizzly_sms import (
    GrizzlySMSClient,
    GrizzlySMSError,
    InsufficientBalanceError,
    NumberUnavailableError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────

_DATA = Path(__file__).parent / "data"
_DATA.mkdir(exist_ok=True)
STATS_FILE = _DATA / "tg_stats.json"

def _stats_empty() -> dict:
    from datetime import datetime
    return {
        "today_date": datetime.now().strftime("%Y-%m-%d"),
        "today":  {"numbers_bought": 0, "otp_received": 0, "logins": 0, "refunds": 0, "spent": 0.0},
        "total":  {"numbers_bought": 0, "otp_received": 0, "logins": 0, "refunds": 0, "spent": 0.0},
        "last_balance": None,
    }

def _load_stats() -> dict:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if STATS_FILE.exists():
            d = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            if d.get("today_date") != today:
                d["today_date"] = today
                d["today"] = {"numbers_bought": 0, "otp_received": 0, "logins": 0, "refunds": 0, "spent": 0.0}
            d.setdefault("total", {"numbers_bought": 0, "otp_received": 0, "logins": 0, "refunds": 0, "spent": 0.0})
            d.setdefault("last_balance", None)
            return d
    except Exception:
        pass
    return _stats_empty()

def _save_stats(s: dict) -> None:
    try:
        STATS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _update_stat(key: str = "", delta: int = 0, money: float = 0.0, balance: Optional[float] = None) -> None:
    s = _load_stats()
    if key and delta:
        s["today"][key] = s["today"].get(key, 0) + delta
        s["total"][key] = s["total"].get(key, 0) + delta
    if money:
        s["today"]["spent"] = round(s["today"].get("spent", 0.0) + money, 6)
        s["total"]["spent"] = round(s["total"].get("spent", 0.0) + money, 6)
    if balance is not None:
        s["last_balance"] = balance
    _save_stats(s)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram Bot Manager
# ─────────────────────────────────────────────────────────────────────────────

class TelegramBotManager:
    """Управляет Telegram-ботом: подписка на уведомления и отправка OTP кодов."""

    SUBSCRIBERS_FILE = Path("tg_subscribers.json")

    def __init__(self, token: str, send_only: bool = False) -> None:
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.active_chats, self.chat_settings = self._load_subscribers()
        self._polling_task: Optional[asyncio.Task] = None
        self._send_only = send_only  # True = только отправка, без getUpdates

    # ── Persistent subscribers ───────────────────────────────────────────────

    def _load_subscribers(self) -> tuple:
        try:
            if self.SUBSCRIBERS_FILE.exists():
                data = json.loads(self.SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
                chats = set(int(c) for c in data.get("chats", []))
                settings = {int(k): v for k, v in data.get("settings", {}).items()}
                if chats:
                    logger.info(f"[Telegram] Загружено подписчиков из файла: {len(chats)}")
                return chats, settings
        except Exception as exc:
            logger.warning(f"[Telegram] Не удалось загрузить подписчиков: {exc}")
        return set(), {}

    def _save_subscribers(self) -> None:
        try:
            self.SUBSCRIBERS_FILE.write_text(
                json.dumps({
                    "chats": list(self.active_chats),
                    "settings": {str(k): v for k, v in self.chat_settings.items()},
                }, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"[Telegram] Не удалось сохранить подписчиков: {exc}")

    def _get_setting(self, chat_id: int, key: str) -> bool:
        return self.chat_settings.get(chat_id, {}).get(key, True)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start_polling(self) -> None:
        if self._send_only:
            logger.info(f"[Telegram] Режим отправки (поллинг в menu.py). Подписчиков: {len(self.active_chats)}")
            return
        if self.active_chats:
            logger.info(f"[Telegram] Бот запущен. Подписчиков: {len(self.active_chats)}")
        else:
            logger.info("[Telegram] Бот запущен. Напишите боту любое сообщение в Telegram для подписки.")
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            logger.info("[Telegram] Бот остановлен")

    # ── Poll loop ────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        try:
            import httpx
        except ImportError:
            logger.error("[Telegram] Пакет httpx не установлен! Запустите: pip install httpx")
            return

        # read timeout должен быть > long-poll timeout (5s) с запасом
        _timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0)
        offset = 0
        consecutive_errors = 0

        async with httpx.AsyncClient(timeout=_timeout) as client:
            while True:
                try:
                    resp = await client.get(
                        f"{self.api_url}/getUpdates",
                        params={"offset": offset, "timeout": 5},
                    )
                    consecutive_errors = 0

                    if resp.status_code == 401:
                        logger.error("[Telegram] Неверный токен бота (401). Проверьте config.yaml → telegram.token")
                        return
                    if resp.status_code == 409:
                        logger.warning("[Telegram] Конфликт: другой экземпляр бота уже запущен (409). Жду...")
                        await asyncio.sleep(5)
                        continue
                    if resp.status_code != 200:
                        logger.warning(f"[Telegram] getUpdates вернул {resp.status_code}")
                        await asyncio.sleep(3)
                        continue

                    data = resp.json()
                    if not data.get("ok"):
                        logger.warning(f"[Telegram] API ответил not ok: {data.get('description', '')}")
                        await asyncio.sleep(3)
                        continue

                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message") or update.get("edited_message")
                        if msg:
                            chat_id = int(msg["chat"]["id"])
                            if chat_id not in self.active_chats:
                                self.active_chats.add(chat_id)
                                self._save_subscribers()
                                logger.success(f"[Telegram] Новый подписчик: chat_id={chat_id}")
                                await self.send_message(
                                    chat_id,
                                    "🔔 Вы подписаны на уведомления!\n"
                                    "Когда вход в аккаунт будет выполнен — вы получите номер телефона и OTP код."
                                )

                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    consecutive_errors += 1
                    wait = min(30, 3 * consecutive_errors)
                    logger.warning(f"[Telegram] Ошибка getUpdates (попытка {consecutive_errors}): {exc} — повтор через {wait}s")
                    await asyncio.sleep(wait)
                    continue

                await asyncio.sleep(1.0)

    # ── Send ─────────────────────────────────────────────────────────────────

    async def send_message(self, chat_id: int, text: str) -> bool:
        try:
            import httpx
        except ImportError:
            return False
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            try:
                resp = await client.post(
                    f"{self.api_url}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                )
                if resp.status_code != 200:
                    logger.warning(f"[Telegram] sendMessage {chat_id} → {resp.status_code}: {resp.text[:100]}")
                    return False
                return True
            except Exception as exc:
                logger.error(f"[Telegram] Ошибка отправки в {chat_id}: {exc}")
                return False

    async def notify_all(self, text: str) -> None:
        if not self.active_chats:
            logger.warning("[Telegram] Нет подписчиков. Напишите боту любое сообщение в Telegram.")
            return
        logger.info(f"[Telegram] Отправка уведомления {len(self.active_chats)} подписчику(ам)")
        tasks = [self.send_message(chat_id, text) for chat_id in self.active_chats]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def notify_filtered(self, text: str, key: str) -> None:
        """Отправка только подписчикам у которых включён ключ настройки."""
        targets = [c for c in self.active_chats if self._get_setting(c, key)]
        if not targets:
            logger.info(f"[Telegram] Уведомление '{key}' пропущено — отключено у всех")
            return
        logger.info(f"[Telegram] Отправка '{key}'-уведомления {len(targets)} подписчику(ам)")
        tasks = [self.send_message(c, text) for c in targets]
        await asyncio.gather(*tasks, return_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Ctrl+X pause / resume monitor
# ─────────────────────────────────────────────────────────────────────────────

def _toggle_pause(pause_event: asyncio.Event) -> None:
    """Вызывается в event loop через call_soon_threadsafe."""
    if pause_event.is_set():
        pause_event.clear()
        logger.warning("⏸  ПАУЗА  —  нажмите Ctrl+X ещё раз для продолжения")
    else:
        pause_event.set()
        logger.success("▶  ПРОДОЛЖЕНИЕ")


def _start_kb_monitor(
    loop: asyncio.AbstractEventLoop,
    pause_event: asyncio.Event,
) -> threading.Event:
    """
    Запускает daemon-поток, слушающий Ctrl+X (Windows msvcrt).
    Возвращает stop_event — установите его для остановки потока.
    """
    stop = threading.Event()

    def _monitor() -> None:
        try:
            import msvcrt
        except ImportError:
            return   # не Windows
        while not stop.is_set():
            try:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key == b'\x18':          # Ctrl+X = ASCII 0x18
                        loop.call_soon_threadsafe(_toggle_pause, pause_event)
            except Exception:
                pass
            stop.wait(0.05)

    threading.Thread(target=_monitor, daemon=True, name="kb-monitor").start()
    return stop


def setup_logging(log_file: str = "automation.log") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="DEBUG",
        colorize=True,
    )
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ConfigManager
# ─────────────────────────────────────────────────────────────────────────────

class ConfigManager:
    """Загружает и валидирует конфиг из YAML или JSON."""

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.config: dict = {}

    def load(self) -> dict:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Конфиг не найден: {self.config_path}")

        suffix = self.config_path.suffix.lower()
        with open(self.config_path, "r", encoding="utf-8") as fh:
            if suffix in (".yaml", ".yml"):
                self.config = yaml.safe_load(fh)
            elif suffix == ".json":
                self.config = json.load(fh)
            else:
                raise ValueError(f"Неподдерживаемый формат: {suffix}")

        self._validate()
        logger.info(f"Конфиг загружен: {self.config_path}")
        return self.config

    def _validate(self) -> None:
        required_top = ("site", "browser", "selectors")
        for key in required_top:
            if key not in self.config:
                raise KeyError(f"Обязательный раздел '{key}' отсутствует в конфиге")
        if not self.config["site"].get("url"):
            raise ValueError("site.url не задан")
        # Аккаунты обязательны только если не задан auto_accounts
        has_accounts = bool(self.config.get("accounts"))
        has_auto = self.config.get("auto_accounts", 0) > 0
        if not has_accounts and not has_auto:
            raise ValueError("Укажите 'accounts' или 'auto_accounts' в конфиге")

    # Удобные свойства-геттеры

    @property
    def site_url(self) -> str:
        return self.config["site"]["url"]

    @property
    def headless(self) -> bool:
        return self.config["browser"].get("headless", False)

    @property
    def profiles_dir(self) -> Path:
        return Path(self.config["browser"].get("profiles_dir", "./chrome_profiles"))

    @property
    def timeout(self) -> int:
        return self.config["browser"].get("timeout", 30000)

    @property
    def slow_mo(self) -> int:
        return self.config["browser"].get("slow_mo", 100)

    @property
    def selectors(self) -> dict:
        return self.config.get("selectors", {})

    @property
    def human_behavior(self) -> dict:
        return self.config.get("human_behavior", {})

    @property
    def otp_config(self) -> dict:
        return self.config.get("otp", {})

    @property
    def captcha_config(self) -> dict:
        return self.config.get("captcha", {})

    @property
    def sms_config(self) -> dict:
        return self.config.get("grizzlysms", {})

    @property
    def auto_accounts_count(self) -> int:
        return int(self.config.get("auto_accounts", 0))

    @property
    def accounts(self) -> list[dict]:
        return self.config.get("accounts", [])

    @property
    def block_media(self) -> bool:
        return self.config["browser"].get("block_media", False)

    @property
    def telegram_config(self) -> dict:
        return self.config.get("telegram", {})


# ─────────────────────────────────────────────────────────────────────────────
# BrowserProfileManager
# ─────────────────────────────────────────────────────────────────────────────

class BrowserProfileManager:
    """Создаёт и удаляет изолированные профили Chrome."""

    META_FILE = ".profile_meta.json"

    def __init__(self, profiles_dir: Path, max_age_days: float = 2.0) -> None:
        self.profiles_dir = profiles_dir
        self.max_age_days = max_age_days
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        # Separate directory for fully logged-in profiles
        self.done_profiles_dir = profiles_dir.parent / (profiles_dir.name + "_done")
        self.done_profiles_dir.mkdir(parents=True, exist_ok=True)

    def profile_path(self, account_index: int, username: str) -> Path:
        safe_name = "".join(c if c.isalnum() else "_" for c in username)
        folder = self.profiles_dir / f"profile_{account_index:04d}_{safe_name}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def done_profile_path(self, account_index: int, username: str) -> Path:
        """Path for a successfully logged-in profile (chrome_profiles_done/)."""
        safe_name = "".join(c if c.isalnum() else "_" for c in username)
        return self.done_profiles_dir / f"profile_{account_index:04d}_{safe_name}"

    def write_meta(self, profile_path: Path, username: str, **extra) -> None:
        """Сохраняет метку времени входа рядом с профилем."""
        meta = {"username": username, "login_ts": time.time(), **extra}
        meta_file = profile_path / self.META_FILE
        with open(meta_file, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)

    def is_expired(self, profile_path: Path) -> bool:
        """Возвращает True, если профиль старше max_age_days."""
        meta_file = profile_path / self.META_FILE
        if not meta_file.exists():
            return True  # нет метки — считаем устаревшим
        try:
            with open(meta_file, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            age_days = (time.time() - meta["login_ts"]) / 86400
            return age_days >= self.max_age_days
        except Exception:
            return True

    def purge_expired(self) -> int:
        """Удаляет устаревшие профили из chrome_profiles/. Успешные (done) не трогает."""
        removed = 0
        for p in self.list_profiles():
            if self.is_expired(p):
                shutil.rmtree(p, ignore_errors=True)
                logger.info(f"Профиль удалён (истёк срок {self.max_age_days}д): {p.name}")
                removed += 1
        return removed

    def delete_profile(self, profile_path: Path) -> None:
        """Удаляет профиль только если он НЕ находится в done_profiles_dir."""
        if not profile_path.exists():
            return
        # Защита: никогда не удалять успешные профили
        try:
            profile_path.resolve().relative_to(self.done_profiles_dir.resolve())
            logger.warning(f"Попытка удалить успешный профиль отклонена: {profile_path.name}")
            return
        except ValueError:
            pass
        shutil.rmtree(profile_path, ignore_errors=True)
        logger.debug(f"Профиль удалён: {profile_path}")

    def purge_incomplete(self) -> int:
        """Удаляет все директории в chrome_profiles/ (неуспешные/в-процессе).
        Успешные профили в chrome_profiles_done/ не трогаются никогда."""
        removed = 0
        if not self.profiles_dir.exists():
            return 0
        for p in list(self.profiles_dir.iterdir()):
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    removed += 1
                elif p.is_file():
                    p.unlink(missing_ok=True)
            except Exception:
                pass
        return removed

    def list_profiles(self) -> list[Path]:
        return sorted(self.profiles_dir.glob("profile_*"))

    def get_next_free_index(self) -> int:
        max_idx = 0
        for p in self.profiles_dir.glob("profile_*"):
            if p.is_dir():
                parts = p.name.split("_")
                if len(parts) >= 2:
                    try:
                        idx = int(parts[1])
                        if idx > max_idx:
                            max_idx = idx
                    except ValueError:
                        pass
        return max_idx + 1


# ─────────────────────────────────────────────────────────────────────────────
# HumanBehavior
# ─────────────────────────────────────────────────────────────────────────────

class HumanBehavior:
    """Случайные задержки для имитации человека."""

    def __init__(self, config: dict) -> None:
        self.action_min = config.get("delay_between_actions_min", 0.5)
        self.action_max = config.get("delay_between_actions_max", 2.0)
        self.type_min = config.get("typing_delay_min", 0.05)
        self.type_max = config.get("typing_delay_max", 0.15)

    async def pause(self) -> None:
        delay = random.uniform(self.action_min, self.action_max)
        await asyncio.sleep(delay)

    async def type_text(self, page: Page, selector: str, text: str) -> None:
        """Печатает текст посимвольно с задержкой."""
        element = await page.wait_for_selector(selector, state="visible")
        await element.click()
        await element.fill("")  # очистка
        for char in text:
            await element.press(char)
            await asyncio.sleep(random.uniform(self.type_min, self.type_max))


# ─────────────────────────────────────────────────────────────────────────────
# LoginAutomation
# ─────────────────────────────────────────────────────────────────────────────

class LoginAutomation:
    """
    Выполняет логин на сайт для одного аккаунта в изолированном профиле.

    Поддерживает:
    - email/username + password
    - телефон + пароль
    - OTP (ручной ввод или API)
    - ручное решение капчи
    """

    def __init__(
        self,
        playwright: Playwright,
        config: ConfigManager,
        profile_manager: BrowserProfileManager,
        sms_client: Optional[GrizzlySMSClient] = None,
        pause_event: Optional[asyncio.Event] = None,
        tg_client: Optional[TelegramBotManager] = None,
        tg_mode: str = "none",
    ) -> None:
        self.playwright = playwright
        self.config = config
        self.profile_manager = profile_manager
        self.human = HumanBehavior(config.human_behavior)
        self.sms_client = sms_client
        self._pause = pause_event if pause_event is not None else asyncio.Event()
        self._pause.set()   # по умолчанию — режим работы (не пауза)
        self._mouse_x: Optional[int] = None
        self._mouse_y: Optional[int] = None
        self._kept_contexts: list[BrowserContext] = []
        self.tg_client = tg_client
        # Глобальный реестр: все купленные активации которые ещё не завершены/отменены.
        # Используется в main().finally для гарантированного возврата денег при любой остановке.
        self._all_pending: dict[str, str] = {}  # act_id → phone
        self.tg_mode = tg_mode
        # Фоновые задачи для номеров, которые нельзя было отменить из-за кулдауна.
        self._background_tasks: list[asyncio.Task] = []
        self._stale_cancelled: bool = False  # очистка старых активаций только 1 раз

    # ── public ──────────────────────────────────────────────────────────────

    async def run_account(self, account: dict, index: int) -> bool:
        """Диспетчер: авто-режим (GrizzlySMS) или ручной (заданный номер)."""
        wants_auto = account.get("phone") in ("auto", None, "")
        if wants_auto:
            if not self.sms_client:
                logger.error(
                    f"[{index}] Авто-режим требует GrizzlySMS, но клиент не инициализирован. "
                    "Проверьте api_key в config.yaml → grizzlysms.api_key"
                )
                return False
            return await self._run_auto(index)
        username = account.get("username") or account.get("email") or account.get("phone", "")
        if not username:
            logger.error(f"[{index}] Не задан номер/логин и GrizzlySMS не настроен")
            return False
        return await self._run_manual(account, username, index)

    # ── AUTO режим (GrizzlySMS: покупка номеров с retry) ────────────────────

    async def _run_auto(self, index: int) -> bool:
        """
        Максимально быстрый режим:
        - Каждый pipeline (поиск номера + phase1) запускается как фоновая задача
        - До max_parallel пайплайнов работают одновременно
        - Главный цикл никогда не блокируется на поиске номера или phase1
        """
        sms_cfg         = self.config.sms_config
        buy_next_after  = sms_cfg.get("buy_next_after_seconds", 20)
        max_parallel    = sms_cfg.get("max_parallel_numbers", 5)

        # Отменяем висящие активации с прошлых сессий — только при первом слоте
        if not self._stale_cancelled:
            self._stale_cancelled = True
            await self._cancel_stale_activations(index)

        # Динамическое ограничение параллельности на основе баланса
        if self.sms_client:
            try:
                balance = await self.sms_client.get_balance()
                price_tiers = sms_cfg.get("price_tiers", [])
                max_price = price_tiers[-1].get("max_price", 0.15) if price_tiers else 0.15
                if max_price > 0:
                    allowed = int(balance // max_price)
                    if allowed < max_parallel:
                        logger.warning(
                            f"[{index}] Баланс ({balance:.4f} руб.) позволяет запустить только {allowed} параллельных номеров (макс. цена: {max_price}). "
                            f"Снижаю лимит параллельности с {max_parallel} до {max(1, allowed)}."
                        )
                        max_parallel = max(1, allowed)
            except Exception as exc:
                logger.warning(f"[{index}] Не удалось получить баланс для ограничения параллельности: {exc}")

        poll_iv         = float(sms_cfg.get("poll_interval", 3))
        number_lifetime = int(sms_cfg.get("number_lifetime_seconds", 180))

        active: dict[str, dict] = {}          # act_id → готовые вкладки, ждут OTP
        pipeline_tasks: set[asyncio.Task] = set()  # задачи поиск+phase1 в процессе
        code_queue: asyncio.Queue = asyncio.Queue()
        # Все купленные номера, которые ещё не отменены и не завершены.
        # Ключ: act_id, значение: phone. _run_auto.finally отменяет всё оставшееся.
        to_cancel: dict[str, str] = {}

        profile_path = self.profile_manager.profile_path(index, f"auto_{index:04d}")

        try:
            loop      = asyncio.get_running_loop()
            last_start = 0.0
            attempt    = 0

            logger.info(f"[{index}] Старт — до {max_parallel} параллельных изолированных контекстов")

            async def _pipeline(attempt_num: int) -> None:
                """Ищет номер + открывает вкладку + phase1 в изолированном временном профиле."""
                act_id = None
                phone = ""
                temp_path = profile_path.parent / f"{profile_path.name}_tmp_{attempt_num}"
                temp_context = None
                in_active = False  # True когда передали владение в словарь active
                try:
                    await self._pause.wait()  # пауза перед покупкой номера
                    num = await self._acquire_number(index)
                    if num is None:
                        return
                    act_id, phone, cost = num
                    purchased_at = loop.time()       # фиксируем момент покупки
                    to_cancel[act_id] = phone       # для _run_auto.finally
                    self._all_pending[act_id] = phone  # глобальный реестр для main().finally
                    self._log_number(index, attempt_num, phone, act_id)
                    bal_after = await self._log_balance(f"[{index}] Куплен +{phone}")
                    await self._notify_bought(phone, cost, bal_after)

                    await self._pause.wait()  # пауза перед запуском браузера
                    temp_context = await self._launch_context(temp_path)
                    tab = await temp_context.new_page()
                    tab.set_default_timeout(self.config.timeout)
                    await tab.goto(self.config.site_url, wait_until="domcontentloaded")
                    ok = await self._login_phase1(tab, phone, index)
                    if ok:
                        await self.sms_client.set_status(act_id, GrizzlySMSClient.STATUS_READY)
                        mon = asyncio.create_task(
                            self._monitor_tab(act_id, phone, poll_iv, code_queue)
                        )
                        active[act_id] = {
                            "phone": phone,
                            "tab": tab,
                            "context": temp_context,
                            "temp_path": temp_path,
                            "task": mon,
                            "bought_at": purchased_at,  # с момента покупки, не phase1
                        }
                        to_cancel.pop(act_id, None)  # теперь в active, не нужен в to_cancel
                        in_active = True
                        logger.info(f"[{index}] +{phone} готов, жду OTP")
                except Exception as exc:
                    logger.error(f"[{index}] Ошибка в пайплайне #{attempt_num}: {exc}")
                except asyncio.CancelledError:
                    pass  # нормальная остановка — номер отменит _run_auto.finally
                finally:
                    # Закрываем браузер если он наш (не передан в active).
                    # Отмену номера делает _run_auto.finally через to_cancel — надёжнее.
                    if not in_active and temp_context:
                        try:
                            await asyncio.shield(temp_context.close())
                        except BaseException:
                            # shield поднял CancelledError — закрываем напрямую фоном
                            asyncio.ensure_future(temp_context.close())
                    if not in_active:
                        self.profile_manager.delete_profile(temp_path)

            while True:
                await self._pause.wait()

                # ── 1. Код пришёл? ────────────────────────────────────────────
                try:
                    act_id, phone, code = code_queue.get_nowait()
                    outcome = await self._handle_winning_code(
                        act_id, phone, code, active, profile_path, index
                    )
                    if outcome is True:
                        for t in pipeline_tasks:
                            t.cancel()
                        return True
                    if outcome is False:
                        last_start = 0.0
                        continue
                except asyncio.QueueEmpty:
                    pass

                # ── 2. Запустить новый пайплайн? ─────────────────────────────
                now = loop.time()
                total = len(active) + len(pipeline_tasks)
                need_start = (total == 0) or (
                    now - last_start >= buy_next_after and total < max_parallel
                )
                if need_start:
                    attempt += 1
                    t = asyncio.create_task(_pipeline(attempt))
                    pipeline_tasks.add(t)
                    t.add_done_callback(pipeline_tasks.discard)
                    last_start = now
                    logger.info(
                        f"[{index}] Пайплайн #{attempt} запущен "
                        f"(активных: {len(active)}, в процессе: {len(pipeline_tasks)})"
                    )

                # ── 3. Ждём OTP ───────────────────────────────────────────────
                if active or pipeline_tasks:
                    try:
                        act_id, phone, code = await asyncio.wait_for(
                            code_queue.get(), timeout=poll_iv
                        )
                        outcome = await self._handle_winning_code(
                            act_id, phone, code, active, profile_path, index
                        )
                        if outcome is True:
                            for t in pipeline_tasks:
                                t.cancel()
                            return True
                        if outcome is False:
                            last_start = 0.0
                            continue
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0.5)

                # ── 4. Возврат истёкших номеров ───────────────────────────────
                now = loop.time()
                for aid in list(active.keys()):
                    age = now - active[aid]["bought_at"]
                    if age > number_lifetime:
                        ph = active[aid]["phone"]
                        active[aid]["task"].cancel()
                        await self._safe_cancel(aid, ph)
                        try:
                            await active[aid]["context"].close()
                        except Exception:
                            pass
                        self.profile_manager.delete_profile(active[aid]["temp_path"])
                        del active[aid]
                        last_start = 0.0
                        logger.info(f"[{index}] +{ph} истёк ({age:.0f}s) → возврат")

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[{index}] Исключение в авто-режиме: {exc}")
            return False
        finally:
            # 1. Отменяем все pipeline-задачи (в полёте: acquire/launch/phase1)
            for t in pipeline_tasks:
                t.cancel()
            if pipeline_tasks:
                await asyncio.gather(*pipeline_tasks, return_exceptions=True)

            # 2. Закрываем и удаляем все контексты, ждущие OTP
            for aid, inf in list(active.items()):
                inf["task"].cancel()
                to_cancel[aid] = inf.get("phone", "")  # добавляем для отмены ниже
                try:
                    await inf["context"].close()
                except Exception:
                    pass
                self.profile_manager.delete_profile(inf["temp_path"])
            active.clear()

            # 3. Единая точка отмены всех купленных, но не завершённых номеров
            if to_cancel:
                logger.info(f"[{index}] Отменяю {len(to_cancel)} номер(а) → возврат средств...")
            for aid, ph in list(to_cancel.items()):
                try:
                    await self._safe_cancel(aid, ph)
                except Exception as _e:
                    logger.warning(f"[{index}] Не удалось отменить {aid}: {_e}")
            to_cancel.clear()

    async def _handle_winning_code(
        self,
        act_id: str,
        phone: str,
        code: str,
        active: dict,
        profile_path: Path,
        index: int,
    ) -> Optional[bool]:
        """
        Обрабатывает победивший OTP: отменяет все прочие, вводит код.
        Возвращает: True — успех, False — OTP отклонён, None — активация устарела.
        """
        self._log_code(index, phone, code)
        _update_stat("otp_received", delta=1)
        if self.tg_client:
            short = phone[2:] if phone.startswith("91") and len(phone) > 10 else phone
            asyncio.create_task(self.tg_client.notify_filtered(
                f"📨 Код получен!\n"
                f"📱 Номер: `+91{short}`\n"
                f"🔑 OTP: `{code}`\n"
                f"⏳ Ввожу в браузере...",
                "otp_code",
            ))
        info = active.get(act_id)
        if not info:
            return None   # активация уже была отменена ранее

        winning_tab = info["tab"]
        winning_context = info["context"]
        winning_temp_path = info["temp_path"]

        # Убираем код страны (+91 / 91)
        short_phone = phone
        if short_phone.startswith("+91"):
            short_phone = short_phone[3:]
        elif short_phone.startswith("91") and len(short_phone) > 10:
            short_phone = short_phone[2:]

        # Переключаемся на вкладку этого номера
        try:
            await winning_tab.bring_to_front()
        except Exception:
            pass
        await asyncio.sleep(0.3)

        # Отменяем и возвращаем деньги за все остальные номера.
        # Если отмена недоступна (кулдаун) — запускаем фоновый мониторинг:
        # если за это время придёт OTP, выполним вход и сохраним профиль.
        for aid, inf in list(active.items()):
            if aid != act_id:
                inf["task"].cancel()   # стоп существующего мониторинга OTP
                ph = inf.get("phone", "")
                try:
                    await self.sms_client.cancel(aid)
                    # Отмена прошла — закрываем браузер и удаляем временный профиль
                    self._all_pending.pop(aid, None)
                    _update_stat("refunds", delta=1)
                    logger.info(f"[{index}] Возврат номера +{ph}")
                    try:
                        await inf["context"].close()
                    except Exception:
                        pass
                    self.profile_manager.delete_profile(inf["temp_path"])
                except Exception:
                    # Кулдаун — номер ещё нельзя отменить.
                    # Запускаем фоновый мониторинг: ждём OTP или истечения кулдауна.
                    logger.info(
                        f"[{index}] +{ph} нельзя отменить (кулдаун) "
                        "→ фоновый мониторинг OTP"
                    )
                    t = asyncio.create_task(
                        self._background_login_monitor(
                            aid, ph, inf["tab"], inf["context"],
                            inf["temp_path"], index,
                        )
                    )
                    self._background_tasks.append(t)
                del active[aid]  # убираем из active — _run_auto.finally не тронет повторно

        if self.tg_mode == "intercept":
            # Отправляем уведомление с номером и кодом (пропуск входа на ПК)
            site_url = self.config.site_url.split("?")[0]
            msg = (
                f"📱 Данные для входа получены:\n"
                f"🔗 Сайт: {site_url}\n"
                f"📱 Телефон: `+91{short_phone}`\n"
                f"🔑 OTP код: `{code}`"
            )
            if self.tg_client:
                await self.tg_client.notify_all(msg)
            logger.success(f"[{index}] Отправлено уведомление в Telegram для +91{phone}. Завершаю сессию на ПК.")

            # Помечаем активацию как завершённую
            self._all_pending.pop(act_id, None)
            await self.sms_client.complete(act_id)

            # Закрываем выигравший контекст
            try:
                await winning_context.close()
            except Exception:
                pass

            # Удаляем временную папку выигравшего
            self.profile_manager.delete_profile(winning_temp_path)

            # Убираем выигравший элемент из active
            if act_id in active:
                del active[act_id]

            return True

        # Пробуем фазу 2 с текущей вкладкой; при исключении — восстанавливаем в новом профиле
        try:
            phase2 = await self._login_phase2(winning_tab, code, index, phone=short_phone)
        except Exception as _phase2_exc:
            logger.warning(
                f"[{index}] Исключение в фазе 2 для +{short_phone}: {_phase2_exc}. "
                "Восстанавливаю вход в новом профиле..."
            )
            # Закрываем повреждённый контекст
            try:
                await winning_context.close()
            except Exception:
                pass
            self.profile_manager.delete_profile(winning_temp_path)
            if act_id in active:
                del active[act_id]

            # Открываем чистый профиль и повторяем фазу 1 + фазу 2
            recovery_path = profile_path.parent / f"{profile_path.name}_rec_{act_id}"
            recovery_context = None
            try:
                recovery_context = await self._launch_context(recovery_path)
                recovery_tab = await recovery_context.new_page()
                await recovery_tab.goto(self.config.site_url, wait_until="domcontentloaded")
                logger.info(f"[{index}] Восстановление: фаза 1 для +{short_phone} в новом профиле")
                if await self._login_phase1(recovery_tab, phone, index):
                    logger.info(f"[{index}] Восстановление: фаза 2 — ввожу код {code}")
                    phase2 = await self._login_phase2(recovery_tab, code, index, phone=short_phone)
                    if phase2:
                        winning_tab = recovery_tab
                        winning_context = recovery_context
                        winning_temp_path = recovery_path
                        recovery_context = None  # не закрывать — теперь это winning
                    else:
                        raise RuntimeError("фаза 2 провалилась в recovery-профиле")
                else:
                    raise RuntimeError("фаза 1 провалилась в recovery-профиле")
            except Exception as _rec_exc:
                logger.error(f"[{index}] Восстановление для +{short_phone} не удалось: {_rec_exc}")
                if recovery_context:
                    try:
                        await recovery_context.close()
                    except Exception:
                        pass
                self.profile_manager.delete_profile(recovery_path)
                await self._safe_cancel(act_id, phone)
                return False

        if phase2:
            # Сначала закрываем контекст чтобы снять блокировки файлов
            try:
                await winning_context.close()
            except Exception:
                pass

            # Перемещаем временную папку в папку успешных профилей
            done_path = self.profile_manager.done_profile_path(index, phone)
            if done_path.exists():
                shutil.rmtree(done_path, ignore_errors=True)
            try:
                shutil.move(str(winning_temp_path), str(done_path))
            except Exception as exc:
                logger.error(f"[{index}] Не удалось перенести профиль: {exc}")
                try:
                    winning_temp_path.rename(done_path)
                except Exception:
                    pass

            self.profile_manager.write_meta(
                done_path, phone,
                otp_code=code,
                site_url=self.config.site_url.split("?")[0],
            )
            self._all_pending.pop(act_id, None)
            await self.sms_client.complete(act_id)

            bal_after = await self._get_balance()
            if bal_after is not None:
                logger.success(
                    f"[{index}] ✅ Вход выполнен! +91{short_phone} | 💰 Баланс: ${bal_after:.4f}"
                )
            else:
                logger.success(f"[{index}] ✅ Вход выполнен! +91{short_phone}")

            # Убираем выигравший элемент из active, чтобы finally не удалил его
            if act_id in active:
                del active[act_id]

            # Открываем постоянный профиль и оставляем его открытым (всегда видимый)
            logger.info(f"[{index}] Запускаю постоянный браузер: {done_path.name}")
            try:
                keep_context = await self._launch_context(done_path, headless=False)
                keep_page = await keep_context.new_page()
                await keep_page.goto("https://www.flipkart.com/flipkart-black-store", wait_until="domcontentloaded")
                self._kept_contexts.append(keep_context)
            except Exception as exc:
                logger.error(f"[{index}] Не удалось запустить постоянный браузер: {exc}")

            _update_stat("logins", delta=1, balance=bal_after)
            if self.tg_mode == "login" and self.tg_client:
                bal_str = f"${bal_after:.4f}" if bal_after is not None else "—"
                site_url = self.config.site_url.split("?")[0]
                msg = (
                    f"🎉 Успешный вход в аккаунт!\n"
                    f"🔗 Сайт: {site_url}\n"
                    f"📱 Телефон: `+91{short_phone}`\n"
                    f"🔑 OTP код: `{code}`\n"
                    f"💰 Баланс: `{bal_str}`\n"
                    f"📁 Профиль: `{done_path.name}`"
                )
                asyncio.create_task(self.tg_client.notify_all(msg))

            return True

        # Проверяем — это блокировка Flipkart или просто неверный код
        is_blocked = await self._is_flipkart_blocked(winning_tab)
        if is_blocked:
            logger.warning(
                f"[{index}] ⛔ Номер +{short_phone} заблокирован Flipkart (Maximum attempts). "
                "Профиль удалён, покупаю новый номер."
            )
            if self.tg_client:
                asyncio.create_task(self.tg_client.notify_all(
                    f"⛔ Номер `+91{short_phone}` заблокирован Flipkart\n"
                    f"Maximum attempts reached. Retry in 24h.\n"
                    f"Профиль удалён — стартую с новым номером."
                ))
        else:
            logger.error(f"[{index}] OTP не принят для +{short_phone}")

        await self._safe_cancel(act_id, phone)
        try:
            await winning_context.close()
        except Exception:
            pass
        self.profile_manager.delete_profile(winning_temp_path)
        if act_id in active:
            del active[act_id]
        return False

    async def _monitor_tab(
        self,
        activation_id: str,
        phone: str,
        poll_interval: int,
        queue: asyncio.Queue,
    ) -> None:
        """Фоновая задача: опрашивает GrizzlySMS и кладёт код в очередь."""
        logger.info(f"Monitor [{activation_id}] +{phone}: опрос каждые {poll_interval}s")
        polls = 0
        try:
            while True:
                try:
                    status = await self.sms_client.get_status(activation_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        f"Monitor [{activation_id}] сетевая ошибка, повтор через {poll_interval}s: {exc}"
                    )
                    await asyncio.sleep(poll_interval)
                    continue
                polls += 1
                stype = status["type"]
                logger.debug(f"Monitor [{activation_id}] #{polls}: {stype}")
                if stype == "OK":
                    logger.success(f"Monitor [{activation_id}] КОД: {status['code']}")
                    await queue.put((activation_id, phone, status["code"]))
                    return
                if stype in ("CANCEL", "UNKNOWN"):
                    logger.warning(f"Monitor [{activation_id}] остановлен: {stype}")
                    return
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            logger.debug(f"Monitor [{activation_id}] отменён после {polls} опросов")
        except Exception as exc:
            logger.error(f"Monitor [{activation_id}] ошибка: {exc}")

    # ── MANUAL режим (заданный номер) ───────────────────────────────────────

    async def _run_manual(self, account: dict, username: str, index: int) -> bool:
        logger.info(f"[{index}] Ручной режим: {username}")
        await self._pause.wait()  # пауза перед запуском браузера
        profile_path = self.profile_manager.profile_path(index, username)
        context: Optional[BrowserContext] = None
        try:
            context = await self._launch_context(profile_path)
            page = await context.new_page()
            page.set_default_timeout(self.config.timeout)
            success = await self._login(page, account, index)
            if success:
                logger.success(f"[{index}] Вход выполнен: {username}")
                self.profile_manager.write_meta(profile_path, username)
            else:
                logger.error(f"[{index}] Вход не удался: {username}")
            return success
        except Exception as exc:
            logger.error(f"[{index}] Исключение: {exc}")
            return False
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

    # ── private ─────────────────────────────────────────────────────────────

    async def _acquire_number(self, index: int) -> Optional[Tuple[str, str, float]]:
        """
        Покупает номер через GrizzlySMS (параллельный режим).
        При NO_NUMBERS несколько слотов опрашивают API одновременно —
        первый успешный результат побеждает.
        Возвращает (activation_id, phone) или None при фатальной ошибке.
        """
        sms_cfg      = self.config.sms_config
        service      = sms_cfg.get("service", "xt")
        country      = sms_cfg.get("country", 22)
        max_price    = sms_cfg.get("max_price")
        slots        = sms_cfg.get("parallel_get_slots", 3)
        poll_delay   = sms_cfg.get("get_number_retry_delay", 5.0)
        acq_timeout  = float(sms_cfg.get("get_number_timeout", 90))
        price_tiers  = sms_cfg.get("price_tiers")  # None → используется max_price весь timeout

        try:
            activation_id, phone, cost = await self.sms_client.get_number_parallel(
                service=service,
                country=country,
                max_price=max_price,
                parallel_slots=slots,
                poll_delay=poll_delay,
                timeout=acq_timeout,
                price_tiers=price_tiers,
            )
            return activation_id, phone, cost
        except InsufficientBalanceError as exc:
            logger.error(f"[{index}] {exc} — пополните баланс GrizzlySMS")
            return None
        except NumberUnavailableError as exc:
            logger.error(f"[{index}] {exc}")
            return None
        except GrizzlySMSError as exc:
            logger.error(f"[{index}] GrizzlySMS ошибка: {exc}")
            return None

    # ── Logging helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _log_number(index: int, attempt: int, phone: str, act_id: str) -> None:
        logger.info(f"[{index}] ┌─────────────────────────────────────────")
        logger.info(f"[{index}] │  НОМЕР  #{attempt}: +{phone}")
        logger.info(f"[{index}] │  ACT ID: {act_id}")
        logger.info(f"[{index}] └─────────────────────────────────────────")

    @staticmethod
    def _log_code(index: int, phone: str, code: str) -> None:
        logger.success(f"[{index}] ╔═════════════════════════════════════════")
        logger.success(f"[{index}] ║  SMS ПОЛУЧЕНА!")
        logger.success(f"[{index}] ║  НОМЕР : +{phone}")
        logger.success(f"[{index}] ║  КОД   : {code}")
        logger.success(f"[{index}] ╚═════════════════════════════════════════")

    async def _get_balance(self) -> Optional[float]:
        if not self.sms_client:
            return None
        try:
            return await self.sms_client.get_balance()
        except Exception:
            return None

    async def _log_balance(self, label: str = "") -> Optional[float]:
        bal = await self._get_balance()
        if bal is not None:
            prefix = f"{label} | " if label else ""
            logger.info(f"  {prefix}💰 Баланс: ${bal:.4f}")
        return bal

    async def _notify_bought(self, phone: str, cost: float, bal_after: Optional[float]) -> None:
        """TG-уведомление о покупке номера: точная цена из API + остаток."""
        _update_stat("numbers_bought", delta=1, money=cost, balance=bal_after)
        if not self.tg_client:
            return
        lines = [f"📲 Куплен номер: `+{phone}`"]
        if cost > 0:
            lines.append(f"💸 Списано: -${cost:.4f}")
        if bal_after is not None:
            lines.append(f"💰 Остаток: ${bal_after:.4f}")
        asyncio.create_task(self.tg_client.notify_filtered("\n".join(lines), "buy_number"))

    async def _safe_cancel(self, activation_id: str, phone: str = "") -> None:
        self._all_pending.pop(activation_id, None)  # снимаем с учёта сразу
        try:
            await self.sms_client.cancel(activation_id)
            bal_after = await self._get_balance()
            if bal_after is not None:
                logger.info(f"  Возврат {activation_id} → Баланс: ${bal_after:.4f}")
            else:
                logger.debug(f"Активация {activation_id} отменена (возврат средств)")
            _update_stat("refunds", delta=1, balance=bal_after)
            # TG-уведомление о возврате
            if self.tg_client:
                lines = []
                if phone:
                    lines.append(f"❌ Возврат номера: `+{phone}`")
                else:
                    lines.append(f"❌ Возврат активации `{activation_id}`")
                if bal_after is not None:
                    lines.append(f"💰 Остаток: ${bal_after:.4f}")
                asyncio.create_task(self.tg_client.notify_filtered("\n".join(lines), "buy_number"))
        except Exception as exc:
            logger.warning(f"Не удалось отменить активацию {activation_id}: {exc}")

    async def _background_login_monitor(
        self,
        act_id: str,
        phone: str,
        tab: "Page",
        ctx: "BrowserContext",
        temp_path: Path,
        index: int,
    ) -> None:
        """
        Мониторинг номера, который нельзя отменить из-за кулдауна GrizzlySMS.
        Если за время кулдауна (обычно 1:30–5 мин) придёт OTP — выполняет
        полноценный вход и сохраняет профиль. Как только отмена станет доступна —
        отменяет номер и возвращает деньги.
        """
        sms_cfg       = self.config.sms_config
        poll_iv       = float(sms_cfg.get("poll_interval", 3))
        max_wait      = int(sms_cfg.get("number_lifetime_seconds", 180)) + 360

        short_phone = phone
        if short_phone.startswith("+91"):
            short_phone = short_phone[3:]
        elif short_phone.startswith("91") and len(short_phone) > 10:
            short_phone = short_phone[2:]

        logged_in = False
        deadline  = time.monotonic() + max_wait

        try:
            while time.monotonic() < deadline:
                # ── 1. Проверяем статус OTP ─────────────────────────────────
                try:
                    status = await self.sms_client.get_status(act_id)
                except Exception:
                    await asyncio.sleep(poll_iv)
                    continue

                if status["type"] == "OK":
                    code = status["code"]
                    _update_stat("otp_received", delta=1)
                    logger.info(f"[{index}] [фон] OTP +{short_phone}: {code}")
                    if self.tg_client:
                        asyncio.create_task(self.tg_client.notify_filtered(
                            f"📨 [Фон] Код для `+91{short_phone}`: `{code}`\n"
                            f"⏳ Выполняю вход...",
                            "otp_code",
                        ))

                    # ── 2. Phase 2: вводим код ───────────────────────────────
                    try:
                        phase2 = await self._login_phase2(tab, code, index, phone=short_phone)
                    except Exception as exc:
                        logger.warning(f"[{index}] [фон] Ошибка phase2 +{short_phone}: {exc}")
                        phase2 = False

                    if phase2:
                        # ── 3. Сохраняем профиль ────────────────────────────
                        try:
                            await ctx.close()
                        except Exception:
                            pass

                        done_path = self.profile_manager.done_profile_path(index, phone)
                        if done_path.exists():
                            shutil.rmtree(done_path, ignore_errors=True)
                        try:
                            shutil.move(str(temp_path), str(done_path))
                        except Exception:
                            try:
                                temp_path.rename(done_path)
                            except Exception:
                                pass

                        self.profile_manager.write_meta(
                            done_path, phone,
                            otp_code=code,
                            site_url=self.config.site_url.split("?")[0],
                        )
                        self._all_pending.pop(act_id, None)
                        try:
                            await self.sms_client.complete(act_id)
                        except Exception:
                            pass

                        bal_after = await self._get_balance()
                        _update_stat("logins", delta=1, balance=bal_after)
                        bal_str = f"${bal_after:.4f}" if bal_after is not None else "—"
                        logger.success(
                            f"[{index}] [фон] ✅ Вход выполнен: +91{short_phone} | 💰 {bal_str}"
                        )
                        if self.tg_mode == "login" and self.tg_client:
                            asyncio.create_task(self.tg_client.notify_all(
                                f"🎉 [Фоновый] Вход выполнен!\n"
                                f"📱 Телефон: `+91{short_phone}`\n"
                                f"🔑 OTP: `{code}`\n"
                                f"💰 Баланс: `{bal_str}`\n"
                                f"📁 Профиль: `{done_path.name}`"
                            ))
                        logged_in = True
                    else:
                        logger.warning(
                            f"[{index}] [фон] Phase2 не прошла для +{short_phone} — профиль не сохранён"
                        )
                    return  # OTP был один — больше ничего не ждём

                elif status["type"] == "CANCEL":
                    logger.info(f"[{index}] [фон] Активация {act_id} отменена провайдером")
                    self._all_pending.pop(act_id, None)
                    return

                # ── 4. Пробуем отменить (кулдаун мог уже пройти) ───────────
                try:
                    await self.sms_client.cancel(act_id)
                    logger.info(f"[{index}] [фон] +{short_phone} отменён (кулдаун прошёл)")
                    self._all_pending.pop(act_id, None)
                    _update_stat("refunds", delta=1)
                    if self.tg_client:
                        asyncio.create_task(self.tg_client.notify_filtered(
                            f"❌ Возврат номера: `+{phone}`", "buy_number"
                        ))
                    return
                except Exception:
                    pass  # кулдаун ещё не прошёл — ждём следующего цикла

                await asyncio.sleep(poll_iv)

            logger.warning(f"[{index}] [фон] Таймаут мониторинга +{short_phone} — принудительно завершаю")

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"[{index}] [фон] Ошибка мониторинга +{short_phone}: {exc}")
        finally:
            if not logged_in:
                try:
                    await ctx.close()
                except Exception:
                    pass
                self.profile_manager.delete_profile(temp_path)
            self._all_pending.pop(act_id, None)

    async def _cancel_stale_activations(self, index: int) -> None:
        """Отменяет все активные активации на GrizzlySMS перед стартом новой сессии."""
        if not self.sms_client:
            return
        try:
            activations = await self.sms_client.get_active_activations()
        except Exception as exc:
            logger.warning(f"[{index}] Не удалось получить активные активации: {exc}")
            return

        if not activations:
            logger.debug(f"[{index}] Активных активаций из прошлых сессий нет")
            return

        # Фильтруем: пропускаем активации текущей сессии (купленные параллельными инстансами)
        stale = [
            a for a in activations
            if str(a.get("activationId") or a.get("id") or "") not in self._all_pending
        ]
        if not stale:
            logger.debug(f"[{index}] Все активные активации принадлежат текущей сессии")
            return

        logger.info(f"[{index}] Найдено {len(stale)} активаций с прошлых сессий — отменяю...")
        cancelled = 0
        for act in stale:
            act_id = str(act.get("activationId") or act.get("id") or "")
            phone  = str(act.get("phoneNumber") or act.get("phone") or "")
            if not act_id:
                continue
            try:
                await self.sms_client.cancel(act_id)
                logger.info(f"[{index}]   ✓ Отменена старая активация +{phone} ({act_id})")
                cancelled += 1
            except Exception as exc:
                logger.debug(f"[{index}]   · Пропуск {act_id}: {exc}")

        if cancelled:
            try:
                bal = await self.sms_client.get_balance()
                logger.info(f"[{index}] Отменено {cancelled} активаций. 💰 Баланс: ${bal:.4f}")
            except Exception:
                pass

    async def _reset_to_login(self, page: Page) -> None:
        try:
            await page.goto(self.config.site_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
        except Exception:
            pass

    # ── Phase helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _format_phone(raw: str) -> str:
        """
        GrizzlySMS: "919119390240" (12 цифр, начинается с 91) → "9119390240" (10 цифр).
        Flipkart принимает только 10-значный номер без кода страны.
        """
        p = "".join(c for c in raw if c.isdigit())   # убираем всё нецифровое
        if p.startswith("91") and len(p) == 12:
            return p[2:]   # убираем +91
        if len(p) > 10:
            return p[-10:]  # берём последние 10 цифр как запасной вариант
        return p

    async def _login_phase1(self, page: Page, phone: str, index: int) -> bool:
        """Ввести номер телефона и нажать кнопку OTP/CONTINUE."""
        display_phone = self._format_phone(phone)
        logger.info(f"[{index}] Ввожу номер: +91 {display_phone}")

        # Ждём если Flipkart показывает bot-challenge ("Are you a human?")
        if not await self._wait_bot_challenge(page, index):
            await self._save_screenshot(page, index, phone, "bot_challenge")
            return False

        # Ждём появления формы — не networkidle (Flipkart его никогда не достигает),
        # а конкретного input-элемента ниже шапки.
        rect = None
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            rect = await page.evaluate("""
                () => {
                    const el = [...document.querySelectorAll('input')].find(i => {
                        const ph = (i.placeholder || '').toLowerCase();
                        const title = (i.title || '').toLowerCase();
                        if (ph.includes('search') || title.includes('search') || ph.includes('поиск') || title.includes('поиск')) return false;
                        const r = i.getBoundingClientRect();
                        return r.top > 40 && r.height > 10 && r.width > 50;
                    });
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return { x: Math.round(r.left + r.width/2),
                             y: Math.round(r.top  + r.height/2) };
                }
            """)
            if rect:
                break
            await asyncio.sleep(0.2)

        if rect is None:
            logger.error(f"[{index}] Поле ввода не найдено")
            await self._save_screenshot(page, index, phone, "no_phone_field")
            return False

        # Лёгкий скролл перед кликом — человек обычно двигает страницу
        await page.mouse.wheel(0, random.randint(30, 80))
        await asyncio.sleep(random.uniform(0.1, 0.25))

        # Двигаем мышь по кривой к полю и кликаем
        await self._human_move_click(page, rect["x"], rect["y"])
        await asyncio.sleep(0.15)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Delete")
        await asyncio.sleep(0.1)

        await page.keyboard.type(display_phone, delay=30)
        await asyncio.sleep(0.2)

        # Считываем то, что оказалось в сфокусированном элементе
        actual = await page.evaluate("() => document.activeElement?.value || ''")
        logger.info(f"[{index}] В activeElement: '{actual}'")

        if actual != display_phone:
            # Запасной: React native value setter + принудительные события
            logger.warning(f"[{index}] keyboard.type дал '{actual}', пробую JS setter")
            actual = await page.evaluate(
                """([x, y, phone]) => {
                    // elementFromPoint может вернуть label/span поверх input
                    let el = document.elementFromPoint(x, y);
                    if (el && el.tagName !== 'INPUT') {
                        el = el.closest('input') ||
                             [...document.querySelectorAll('input')].find(i => {
                                 const ph = (i.placeholder || '').toLowerCase();
                                 const title = (i.title || '').toLowerCase();
                                 if (ph.includes('search') || title.includes('search') || ph.includes('поиск') || title.includes('поиск')) return false;
                                 const r = i.getBoundingClientRect();
                                 return r.top > 40 && r.height > 10 && r.width > 50;
                             });
                    }
                    if (!el || el.tagName !== 'INPUT') return '';
                    el.focus();
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, phone);
                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return el.value;
                }""",
                [rect["x"], rect["y"], display_phone],
            )
            logger.info(f"[{index}] После JS setter: '{actual}'")

        if actual != display_phone:
            logger.error(f"[{index}] Не удалось ввести номер (got '{actual}')")
            await self._save_screenshot(page, index, phone, "no_phone_field")
            return False

        logger.info(f"[{index}] Номер введён: {actual}")

        # Проверяем «Blocked Account» — появляется сразу после ввода номера, без нажатия CONTINUE
        await asyncio.sleep(0.5)
        if await self._is_flipkart_blocked(page):
            logger.warning(
                f"[{index}] +{phone}: Blocked Account — закрываю профиль, беру новый номер"
            )
            await self._save_screenshot(page, index, phone, "blocked_account")
            return False

        if await self._captcha_detected(page):
            if not await self._handle_captcha(page, index, phone):
                return False

        sel = self.config.selectors
        submit_sel = sel.get("submit_button")
        if submit_sel:
            clicked = await self._click_if_exists(page, submit_sel, label="CONTINUE/OTP")
            if not clicked:
                logger.error(f"[{index}] Кнопка не найдена (URL: {page.url})")
                await self._save_screenshot(page, index, phone, "no_submit_btn")
                return False
            logger.info(f"[{index}] Кнопка нажата")
            await asyncio.sleep(1.0)   # коротко ждём появления следующего экрана

            # Второй CONTINUE если появился (не блокируемся долго)
            second = "button:has-text('CONTINUE'), button:has-text('Continue')"
            if await self._element_exists(page, second, timeout=2_000):
                logger.info(f"[{index}] Второй CONTINUE — нажимаю")
                await self._click_if_exists(page, second, label="CONTINUE-2")
                await asyncio.sleep(0.8)

            # Параллельно ждём OTP-поле ИЛИ toast «Maximum attempts».
            # Toast может появиться через 1–5s после нажатия, поэтому опрашиваем до 20s.
            otp_sel_ph1 = "input[type='text'], input[type='number']"
            _ph1_dl = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < _ph1_dl:
                if await self._is_flipkart_blocked(page):
                    logger.warning(
                        f"[{index}] +{phone}: Maximum attempts reached — закрываю, беру новый номер"
                    )
                    await self._save_screenshot(page, index, phone, "blocked_phase1")
                    return False
                try:
                    el = await page.query_selector(otp_sel_ph1)
                    if el and await el.is_visible():
                        break   # OTP-поле появилось
                except Exception:
                    pass
                await asyncio.sleep(0.4)

        logger.info(f"[{index}] Phase1 OK — URL: {page.url}")
        return True

    async def _is_flipkart_blocked(self, page: Page) -> bool:
        """Проверяет блокировку номера/аккаунта на странице Flipkart."""
        try:
            # textContent включает скрытые/исчезнувшие тосты (innerText их упускает)
            text = await page.evaluate("""
                () => (document.body?.textContent || '') + ' ' + (document.body?.innerText || '')
            """)
            text_l = text.lower()
            return (
                "maximum attempts reached" in text_l
                or "verification unsuccessful" in text_l
                or "retry in 24" in text_l
                or "too many attempts" in text_l
                or "blocked account" in text_l
                or "accountvalidation@flipkart" in text_l
            )
        except Exception:
            return False

    async def _login_phase2(self, page: Page, otp_code: str, index: int, phone: str = "") -> bool:
        """Ввести OTP → верифицировать → проверить успех."""
        sel      = self.config.selectors
        otp_wait = self.config.otp_config.get("wait_timeout", 20000)

        # Проверяем bot-challenge перед вводом OTP
        if not await self._wait_bot_challenge(page, index, timeout=15.0):
            await self._save_screenshot(page, index, "phase2", "bot_challenge")
            return False

        # Проверяем блокировку ДО ввода (если Flipkart уже показывает ошибку)
        if await self._is_flipkart_blocked(page):
            logger.warning(f"[{index}] Flipkart заблокировал +{phone}: Maximum attempts reached")
            await self._save_screenshot(page, index, "phase2", "blocked_before")
            return False

        # Ждём появления OTP-поля (одного или split из нескольких ячеек)
        logger.info(f"[{index}] Жду OTP-поле (до {otp_wait//1000}s)...")
        deadline = asyncio.get_running_loop().time() + otp_wait / 1000
        otp_info = None
        while asyncio.get_running_loop().time() < deadline:
            otp_info = await page.evaluate("""
                () => {
                    const inputs = [...document.querySelectorAll('input')];
                    const valid = inputs.filter(i => {
                        const ph  = (i.placeholder || '').toLowerCase();
                        const ttl = (i.title || '').toLowerCase();
                        if (ph.includes('search') || ttl.includes('search')) return false;
                        const r = i.getBoundingClientRect();
                        if (r.top <= 40 || r.height <= 10 || r.width <= 10) return false;
                        const digits = (i.value || '').replace(/[^0-9]/g, '');
                        if (digits.length >= 10) return false;
                        return true;
                    });
                    if (!valid.length) return null;

                    // --- Обнаружение split-OTP (6 отдельных ячеек по 1 цифре) ---
                    const splitCandidates = valid.filter(i => {
                        const ml = parseInt(i.maxLength);
                        if (ml === 1) return true;
                        // ячейки без maxlength но с квадратной формой и типом text/number/tel
                        const r = i.getBoundingClientRect();
                        return (r.width < 60) && ['text','number','tel',''].includes(i.type);
                    });
                    if (splitCandidates.length >= 4) {
                        splitCandidates.sort((a, b) =>
                            a.getBoundingClientRect().left - b.getBoundingClientRect().left
                        );
                        // Дедупликация: убираем элементы на одной X-позиции (±5px)
                        // Некоторые OTP-компоненты имеют наложенные input'ы поверх видимых ячеек
                        const deduped = [];
                        let prevLeft = -999;
                        for (const el of splitCandidates) {
                            const left = Math.round(el.getBoundingClientRect().left);
                            if (left - prevLeft > 5) {
                                deduped.push(el);
                                prevLeft = left;
                            }
                        }
                        const boxes = deduped.slice(0, 6).map(i => {
                            const r = i.getBoundingClientRect();
                            return {
                                x: Math.round(r.left + r.width  / 2),
                                y: Math.round(r.top  + r.height / 2),
                            };
                        });
                        deduped[0].scrollIntoView({block: 'center', behavior: 'instant'});
                        return { split: true, boxes: boxes, x: boxes[0].x, y: boxes[0].y };
                    }

                    // --- Одиночное поле ---
                    const prio = valid.find(i => {
                        const ph = (i.placeholder || '').toLowerCase();
                        return ph.includes('otp') || ph.includes('code')
                            || ph.includes('enter') || ph.includes('verif');
                    }) || valid[0];
                    if (!prio) return null;
                    const r = prio.getBoundingClientRect();
                    prio.scrollIntoView({block: 'center', behavior: 'instant'});
                    return {
                        split:       false,
                        x:           Math.round(r.left + r.width  / 2),
                        y:           Math.round(r.top  + r.height / 2),
                        placeholder: prio.placeholder || '',
                        tag:         prio.tagName,
                        type:        prio.type || '',
                    };
                }
            """)
            if otp_info:
                break
            await asyncio.sleep(0.5)

        if not otp_info:
            logger.error(f"[{index}] Поле OTP не появилось в браузере")
            await self._save_screenshot(page, index, "phase2", "no_otp_field")
            return False

        is_split = otp_info.get("split", False)
        if is_split:
            logger.info(
                f"[{index}] OTP split-поле ({len(otp_info['boxes'])} ячеек), "
                f"первая: ({otp_info['x']}, {otp_info['y']})"
            )
        else:
            logger.info(
                f"[{index}] OTP-поле найдено: placeholder='{otp_info.get('placeholder','')}' "
                f"type='{otp_info.get('type','')}' координаты=({otp_info['x']}, {otp_info['y']})"
            )

        if is_split:
            boxes = otp_info["boxes"]

            # Собираем реальные element handles — надёжнее чем клик по координатам
            raw_handles = await page.query_selector_all(
                'input[maxlength="1"]:not([type="hidden"])'
            )
            positioned: list[tuple[float, object]] = []
            for h in raw_handles:
                try:
                    if not await h.is_visible():
                        continue
                    bb = await h.bounding_box()
                    if bb and bb["width"] > 0:
                        positioned.append((bb["x"], h))
                except Exception:
                    pass
            positioned.sort(key=lambda t: t[0])

            # Дедупликация (те же ±5px что и в JS)
            deduped_handles = []
            prev_x = -999.0
            for x, h in positioned:
                if x - prev_x > 5:
                    deduped_handles.append(h)
                    prev_x = x

            use_handles = len(deduped_handles) >= 4
            if use_handles:
                logger.debug(
                    f"[{index}] Split OTP: {len(deduped_handles)} ячеек через element handles"
                )
                for i, char in enumerate(otp_code):
                    if i >= len(deduped_handles):
                        break
                    el = deduped_handles[i]
                    await el.click()
                    await asyncio.sleep(0.05)
                    await el.fill(char)
                    await asyncio.sleep(random.uniform(0.07, 0.14))

                combined = ""
                for el in deduped_handles[:6]:
                    try:
                        combined += await el.input_value() or ""
                    except Exception:
                        combined += ""
            else:
                # Запасной вариант: координатный ввод
                logger.debug(f"[{index}] Split OTP: element handles не найдены, ввод по координатам")
                for i, char in enumerate(otp_code):
                    box = boxes[i] if i < len(boxes) else boxes[-1]
                    await self._human_move_click(page, box["x"], box["y"])
                    await asyncio.sleep(0.05)
                    await page.keyboard.press("Backspace")
                    await page.keyboard.type(char, delay=0)
                    await asyncio.sleep(random.uniform(0.07, 0.14))
                combined = await page.evaluate("""
                    (boxes) => boxes.map(({x, y}) => {
                        const el = document.elementFromPoint(x, y);
                        return el ? (el.value || '') : '';
                    }).join('')
                """, otp_info["boxes"])

            if otp_code not in combined:
                logger.warning(
                    f"[{index}] Split OTP: собрано '{combined}', ожидали '{otp_code}'"
                )
            else:
                logger.info(f"[{index}] Split OTP '{combined}' введён для +{phone}")
        else:
            # Одиночное поле
            await self._human_move_click(page, otp_info["x"], otp_info["y"])
            await asyncio.sleep(0.15)
            await page.mouse.click(otp_info["x"], otp_info["y"], click_count=3)
            await asyncio.sleep(0.05)
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.05)
            for char in otp_code:
                await page.keyboard.type(char, delay=0)
                await asyncio.sleep(random.uniform(0.07, 0.14))
            # Проверяем что код попал в поле
            actual = await page.evaluate("""
                ([x, y]) => {
                    const el = document.elementFromPoint(x, y);
                    return el ? (el.value || '') : '';
                }
            """, [otp_info["x"], otp_info["y"]])
            if otp_code not in actual:
                logger.warning(f"[{index}] В поле '{actual}' — ожидали '{otp_code}', пробую fill...")
                await page.evaluate("""
                    ([x, y, val]) => {
                        const el = document.elementFromPoint(x, y);
                        if (!el) return;
                        const nativeInput = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value');
                        nativeInput.set.call(el, val);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                """, [otp_info["x"], otp_info["y"], otp_code])
                await asyncio.sleep(0.1)
            logger.info(f"[{index}] OTP '{otp_code}' введён в поле для +{phone}")
        await asyncio.sleep(0.2)

        # Отправляем Enter
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)

        if await self._captcha_detected(page):
            if not await self._handle_captcha(page, index, ""):
                return False

        # Если Enter не сработал или требуется принудительный клик, кликаем по кнопке
        submit_sel = sel.get("otp_submit_button") or sel.get("submit_button")
        if submit_sel:
            clicked = await self._click_if_exists(page, submit_sel, label="OTP Submit")
            if clicked:
                logger.info(f"[{index}] Кнопка OTP нажата")

        # Ждём смены URL (успешный вход/регистрация уходит с login-страниц)
        try:
            await page.wait_for_url(
                lambda url: "login" not in url,
                timeout=self.config.timeout,
            )
            logger.success(f"[{index}] Вход выполнен! URL: {page.url}")
            return True
        except Exception:
            # URL не изменился — разбираемся почему
            if await self._is_flipkart_blocked(page):
                logger.warning(
                    f"[{index}] ⛔ Flipkart заблокировал +{phone}: "
                    "Maximum attempts reached. Retry in 24 hours. "
                    "Профиль будет удалён, номер отменён."
                )
                await self._save_screenshot(page, index, "phase2", "blocked_after")
            else:
                logger.error(f"[{index}] OTP отклонён или URL не изменился для +{phone}")
                await self._save_screenshot(page, index, "phase2", "url_unchanged")
            return False

    # Реалистичные размеры окна — распространённые у индийских пользователей
    _VIEWPORTS = [
        {"width": 1366, "height": 768},
        {"width": 1920, "height": 1080},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
    ]

    # Реалистичные WebGL пары (vendor, renderer) для индийских ноутбуков
    _WEBGL_PROFILES = [
        ("Intel Inc.",        "Intel(R) Iris(TM) Plus Graphics 640"),
        ("Intel Inc.",        "Intel(R) UHD Graphics 620"),
        ("Intel Inc.",        "Intel(R) HD Graphics 620"),
        ("Intel Inc.",        "Intel(R) UHD Graphics 630"),
        ("NVIDIA Corporation","NVIDIA GeForce GTX 1050/PCIe/SSE2"),
        ("NVIDIA Corporation","NVIDIA GeForce MX250/PCIe/SSE2"),
        ("Intel Inc.",        "Intel(R) Iris(TM) Xe Graphics"),
    ]

    @classmethod
    def _build_stealth_js(cls) -> str:
        hw  = random.choice([4, 6, 8, 12])
        mem = random.choice([4, 8])
        wgl_vendor, wgl_renderer = random.choice(cls._WEBGL_PROFILES)
        rtt = random.choice([20, 30, 40, 50, 60])
        return cls._STEALTH_JS_TEMPLATE.replace("__HW_CONCURRENCY__", str(hw)) \
            .replace("__DEVICE_MEMORY__",   str(mem)) \
            .replace("__WEBGL_VENDOR__",    wgl_vendor) \
            .replace("__WEBGL_RENDERER__",  wgl_renderer) \
            .replace("__RTT__",             str(rtt))

    _STEALTH_JS_TEMPLATE = r"""
        (() => {
            const _def = (obj, prop, getter) => {
                try {
                    Object.defineProperty(obj, prop, { get: getter, configurable: true, enumerable: true });
                } catch(_) {}
            };

            // ── 1. webdriver ─────────────────────────────────────────────────
            _def(navigator, 'webdriver', () => undefined);
            try { delete navigator.__proto__.webdriver; } catch(_) {}

            // ── 2. chrome object (full) ──────────────────────────────────────
            const _chrome = {
                app: {
                    isInstalled: false,
                    InstallState: { DISABLED:'disabled', INSTALLED:'installed', NOT_INSTALLED:'not_installed' },
                    RunningState: { CANNOT_RUN:'cannot_run', READY_TO_RUN:'ready_to_run', RUNNING:'running' },
                    getDetails: () => null,
                    getIsInstalled: () => false,
                    installState: (cb) => cb('not_installed'),
                    runningState: () => 'cannot_run',
                },
                runtime: {
                    connect:     () => undefined,
                    sendMessage: () => undefined,
                    id: undefined,
                    onConnect:          { addListener:()=>{}, removeListener:()=>{}, hasListener:()=>false },
                    onMessage:          { addListener:()=>{}, removeListener:()=>{}, hasListener:()=>false },
                    onInstalled:        { addListener:()=>{}, removeListener:()=>{}, hasListener:()=>false },
                    onStartup:          { addListener:()=>{}, removeListener:()=>{}, hasListener:()=>false },
                    onSuspend:          { addListener:()=>{}, removeListener:()=>{}, hasListener:()=>false },
                    onSuspendCanceled:  { addListener:()=>{}, removeListener:()=>{}, hasListener:()=>false },
                },
                loadTimes: () => {
                    const n = Date.now() / 1000;
                    return { requestTime:n-0.5, startLoadTime:n-0.3, commitLoadTime:n-0.1,
                             finishDocumentLoadTime:n, finishLoadTime:n,
                             firstPaintTime:n-0.05, firstPaintAfterLoadTime:0,
                             navigationType:'Other', wasFetchedViaSpdy:false,
                             wasNpnNegotiated:false, npnNegotiatedProtocol:'',
                             wasAlternateProtocolAvailable:false, connectionInfo:'h2' };
                },
                csi: () => ({ startE: Date.now(), onloadT: Date.now(),
                              pageT: Date.now() - (performance.timing ? performance.timing.navigationStart : 0),
                              tran: 15 }),
            };
            if (!window.chrome) window.chrome = {};
            Object.assign(window.chrome, _chrome);

            // ── 3. Plugins (5 real-looking plugins) ──────────────────────────
            const _makePlugin = (name, desc, fn, mimes) => {
                const p = Object.create(Plugin.prototype);
                _def(p, 'name',        () => name);
                _def(p, 'description', () => desc);
                _def(p, 'filename',    () => fn);
                _def(p, 'length',      () => mimes.length);
                mimes.forEach((m, i) => {
                    const mt = Object.create(MimeType.prototype);
                    _def(mt, 'type',        () => m.type);
                    _def(mt, 'suffixes',    () => m.suffixes);
                    _def(mt, 'description', () => m.description);
                    _def(mt, 'enabledPlugin', () => p);
                    p[i] = mt;
                    p[m.type] = mt;
                });
                return p;
            };
            const _pdfMime = [{ type:'application/pdf', suffixes:'pdf', description:'' },
                              { type:'text/pdf',         suffixes:'pdf', description:'' }];
            const _plugins = [
                _makePlugin('PDF Viewer',               'Portable Document Format', 'internal-pdf-viewer', _pdfMime),
                _makePlugin('Chrome PDF Viewer',        '',                         'mhjfbmdgcfjbbpaeojofohoefgiehjai', _pdfMime),
                _makePlugin('Chromium PDF Viewer',      '',                         'internal-pdf-viewer', _pdfMime),
                _makePlugin('Microsoft Edge PDF Viewer','',                         'internal-pdf-viewer', _pdfMime),
                _makePlugin('WebKit built-in PDF',      '',                         'internal-pdf-viewer', _pdfMime),
            ];
            const _fakePluginArray = Object.create(PluginArray.prototype);
            _plugins.forEach((p, i) => { _fakePluginArray[i] = p; _fakePluginArray[p.name] = p; });
            _def(_fakePluginArray, 'length', () => _plugins.length);
            _fakePluginArray.item     = (i) => _plugins[i] || null;
            _fakePluginArray.namedItem= (n) => _plugins.find(p => p.name === n) || null;
            _fakePluginArray.refresh  = () => {};
            _def(navigator, 'plugins', () => _fakePluginArray);

            const _mimeTypes = {};
            _plugins.forEach(p => { for (let i=0; i<p.length; i++) { const m=p[i]; _mimeTypes[m.type]=m; } });
            const _fakeMimeArray = Object.create(MimeTypeArray.prototype);
            Object.entries(_mimeTypes).forEach(([k,v], i) => { _fakeMimeArray[i]=v; _fakeMimeArray[k]=v; });
            _def(_fakeMimeArray, 'length', () => Object.keys(_mimeTypes).length);
            _fakeMimeArray.item      = (i) => Object.values(_mimeTypes)[i] || null;
            _fakeMimeArray.namedItem = (n) => _mimeTypes[n] || null;
            _def(navigator, 'mimeTypes', () => _fakeMimeArray);

            // ── 4. Navigator properties ──────────────────────────────────────
            _def(navigator, 'vendor',              () => 'Google Inc.');
            _def(navigator, 'platform',            () => 'Win32');
            _def(navigator, 'languages',           () => ['en-IN','en-GB','en','hi']);
            _def(navigator, 'language',            () => 'en-IN');
            _def(navigator, 'hardwareConcurrency', () => __HW_CONCURRENCY__);
            _def(navigator, 'deviceMemory',        () => __DEVICE_MEMORY__);
            _def(navigator, 'maxTouchPoints',      () => 0);
            _def(navigator, 'cookieEnabled',       () => true);

            // ── 5. Connection ────────────────────────────────────────────────
            try {
                const _conn = { rtt:__RTT__, downlink:10, effectiveType:'4g', saveData:false,
                                addEventListener:()=>{}, removeEventListener:()=>{} };
                _def(navigator, 'connection',       () => _conn);
                _def(navigator, 'mozConnection',    () => undefined);
                _def(navigator, 'webkitConnection', () => undefined);
            } catch(_) {}

            // ── 6. Permissions ────────────────────────────────────────────────
            const _origQuery = navigator.permissions.query.bind(navigator.permissions);
            navigator.permissions.query = (p) =>
                p.name === 'notifications'
                    ? Promise.resolve({ state: (typeof Notification !== 'undefined' ? Notification.permission : 'default'), onchange: null })
                    : _origQuery(p);

            // ── 7. Canvas fingerprint micro-noise ─────────────────────────────
            try {
                const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {
                    if (this.width > 16 && this.height > 16) {
                        const ctx = this.getContext && this.getContext('2d');
                        if (ctx) {
                            const px = ctx.getImageData(0, 0, 1, 1);
                            px.data[0] = (px.data[0] + 1) & 0xff;
                            ctx.putImageData(px, 0, 0);
                            const res = _origToDataURL.call(this, type, ...args);
                            px.data[0] = (px.data[0] - 1) & 0xff;
                            ctx.putImageData(px, 0, 0);
                            return res;
                        }
                    }
                    return _origToDataURL.call(this, type, ...args);
                };
                const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
                    const d = _origGetImageData.call(this, x, y, w, h);
                    if (w * h > 1) d.data[0] = (d.data[0] + 1) & 0xff;
                    return d;
                };
            } catch(_) {}

            // ── 8. WebGL vendor / renderer ────────────────────────────────────
            try {
                const _patchWebGL = (Cls) => {
                    if (!Cls) return;
                    const _orig = Cls.prototype.getParameter;
                    Cls.prototype.getParameter = function(p) {
                        if (p === 37445) return '__WEBGL_VENDOR__';
                        if (p === 37446) return '__WEBGL_RENDERER__';
                        return _orig.call(this, p);
                    };
                };
                _patchWebGL(window.WebGLRenderingContext);
                _patchWebGL(window.WebGL2RenderingContext);
            } catch(_) {}

            // ── 9. Screen ─────────────────────────────────────────────────────
            try {
                _def(screen, 'colorDepth', () => 24);
                _def(screen, 'pixelDepth', () => 24);
            } catch(_) {}

            // ── 10. Remove CDP / Playwright artifacts ─────────────────────────
            try {
                const cdcKey = Object.keys(document).find(k => k.startsWith('$cdc_') || k.startsWith('$playwright'));
                if (cdcKey) { try { delete document[cdcKey]; } catch(_) {} }
            } catch(_) {}

            // ── 11. window.outerWidth / outerHeight ───────────────────────────
            try {
                if (window.outerWidth === 0) _def(window, 'outerWidth',  () => window.innerWidth);
                if (window.outerHeight === 0) _def(window, 'outerHeight', () => window.innerHeight + 74);
            } catch(_) {}
        })();
    """

    # User-Agents matching real Chrome on Windows (rotate to avoid fingerprint)
    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]

    async def _launch_context(self, profile_path: Path, headless: Optional[bool] = None) -> BrowserContext:
        vp = random.choice(self._VIEWPORTS)
        ua = random.choice(self._USER_AGENTS)

        # Prefer real Chrome for better stealth; fall back to Playwright Chromium
        chrome_exe = _find_chrome()
        if chrome_exe:
            logger.debug(f"Используем реальный Chrome: {chrome_exe}")
        else:
            logger.debug("Реальный Chrome не найден, используем Playwright Chromium")

        use_headless = self.config.headless if headless is None else headless
        context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            executable_path=chrome_exe,   # None → Playwright Chromium
            headless=use_headless,
            slow_mo=0,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
                "--disable-notifications",
                "--disable-save-password-bubble",
                "--disable-features=TranslateUI,OptimizationHints,MediaRouter",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--window-size={},{}".format(vp["width"], vp["height"] + 74),
            ],
            ignore_https_errors=True,
            viewport=vp,
            user_agent=ua,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8,hi;q=0.7",
            },
        )
        await context.add_init_script(self._build_stealth_js())

        if self.config.block_media:
            await context.route(
                "**/*.{png,jpg,jpeg,gif,svg,webp,ico,avif,woff,woff2,ttf,otf,eot,mp4,mp3}",
                lambda route: route.abort()
            )
        logger.debug(f"Контекст: {profile_path.name} | {vp['width']}×{vp['height']} | {ua[:40]}...")
        return context

    async def _wait_bot_challenge(self, page: Page, index: int, timeout: float = 25.0) -> bool:
        """Ждём пока Flipkart's 'Are you a human?' разрешится само."""
        deadline = asyncio.get_running_loop().time() + timeout
        warned = False
        while asyncio.get_running_loop().time() < deadline:
            try:
                title   = await page.title()
                content = await page.evaluate("() => document.body?.innerText?.slice(0,300) || ''")
            except Exception:
                return False
            is_challenge = (
                "recaptcha" in title.lower()
                or "human" in content.lower()
                or "confirming" in content.lower()
            )
            if not is_challenge:
                return True
            if not warned:
                logger.warning(f"[{index}] Обнаружена bot-challenge, жду авторазрешения (до {timeout:.0f}s)...")
                warned = True
            await asyncio.sleep(0.8)
        logger.error(f"[{index}] Bot-challenge не разрешилась за {timeout:.0f}s")
        return False

    async def _login(
        self, page: Page, account: dict, index: int, activation_id: Optional[str] = None
    ) -> bool:
        sel = self.config.selectors
        username = account.get("username") or account.get("email") or account.get("phone", "")

        # 1. Открыть страницу входа
        logger.debug(f"[{index}] Переход на {self.config.site_url}")
        await page.goto(self.config.site_url, wait_until="domcontentloaded")
        await self.human.pause()

        # 2. Нажать кнопку «Login» если она присутствует на главной
        if sel.get("login_button"):
            await self._click_if_exists(page, sel["login_button"], label="login_button")
            await self.human.pause()

        # 3. Заполнить поле логина
        username_sel = sel.get("username_field")
        if username_sel:
            logger.debug(f"[{index}] Ввод логина")
            await self.human.type_text(page, username_sel, username)
            await self.human.pause()
        else:
            logger.warning(f"[{index}] Селектор username_field не задан")

        # 4. Заполнить пароль (если указан в аккаунте)
        password = account.get("password", "")
        password_sel = sel.get("password_field")
        if password and password_sel:
            logger.debug(f"[{index}] Ввод пароля")
            await self.human.type_text(page, password_sel, password)
            await self.human.pause()

        # 5. Обработка капчи (до отправки формы)
        if await self._captcha_detected(page):
            solved = await self._handle_captcha(page, index, username)
            if not solved:
                return False

        # 6. Отправить форму (для Flipkart — кнопка «Request OTP»)
        submit_sel = sel.get("submit_button")
        if submit_sel:
            logger.debug(f"[{index}] Клик по submit")
            await self._click_if_exists(page, submit_sel, label="submit_button", required=True)
            # Ждём завершения AJAX-запроса после нажатия «Request OTP»
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await self.human.pause()

        # Проверка редиректа на регистрацию (номер телефона не зарегистрирован)
        if "signup=true" in page.url or ("signup" in page.url and "login" not in page.url):
            logger.warning(f"[{index}] Номер не зарегистрирован, редирект на signup: {username}")
            await self._save_screenshot(page, index, username, "signup_redirect")
            return False

        # 7. Ожидание OTP-поля после отправки
        otp_sel = sel.get("otp_field")
        otp_wait = self.config.otp_config.get("wait_timeout", self.config.timeout)
        if otp_sel and await self._element_exists(page, otp_sel, timeout=otp_wait):
            otp_entered = await self._handle_otp(page, index, username, otp_sel, activation_id)
            if not otp_entered:
                return False
            # Ждём загрузки после верификации OTP
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await self.human.pause()

        # 8. Проверка успешного входа
        success_sel = sel.get("success_indicator")
        if success_sel:
            try:
                await page.wait_for_selector(success_sel, state="visible", timeout=self.config.timeout)
                return True
            except Exception:
                logger.warning(f"[{index}] success_indicator не найден после входа")
                await self._save_screenshot(page, index, username, "failed")
                return False
        else:
            # Ждём смены URL (Flipkart редиректит на главную после успешного входа)
            try:
                await page.wait_for_url(
                    lambda url: url != login_url and "login" not in url,
                    timeout=self.config.timeout,
                )
                return True
            except Exception:
                await self._save_screenshot(page, index, username, "url_unchanged")
                return False

    async def _human_move_click(self, page: Page, x: int, y: int) -> None:
        """Двигаем мышь к (x, y) по кривой Безье, затем кликаем.
        Имитирует движение руки — антидетект защита от поведенческого анализа."""
        vp  = page.viewport_size or {"width": 1280, "height": 800}
        # Стартовая позиция — из координат предыдущего клика или случайная точка в центре
        if self._mouse_x is None or self._mouse_y is None:
            sx  = random.randint(vp["width"] // 4,  vp["width"]  * 3 // 4)
            sy  = random.randint(vp["height"] // 4, vp["height"] * 3 // 4)
        else:
            sx = self._mouse_x
            sy = self._mouse_y
        # Контрольная точка кривой (случайный изгиб)
        cx  = sx + (x - sx) * random.uniform(0.3, 0.7) + random.randint(-50, 50)
        cy  = sy + (y - sy) * random.uniform(0.3, 0.7) + random.randint(-50, 50)
        steps = random.randint(10, 18)
        for i in range(1, steps + 1):
            t  = i / steps
            px = int((1-t)**2 * sx + 2*(1-t)*t * cx + t**2 * x)
            py = int((1-t)**2 * sy + 2*(1-t)*t * cy + t**2 * y)
            await page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.004, 0.012))
        await page.mouse.click(x, y)
        self._mouse_x = x
        self._mouse_y = y

    async def _click_if_exists(
        self,
        page: Page,
        selector: str,
        label: str = "",
        required: bool = False,
    ) -> bool:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=5000)
            # Берём координаты кнопки и кликаем человечески (через кривую Безье)
            box = await loc.bounding_box()
            if box:
                x = int(box["x"] + box["width"]  * random.uniform(0.3, 0.7))
                y = int(box["y"] + box["height"] * random.uniform(0.3, 0.7))
                await self._human_move_click(page, x, y)
            else:
                await loc.click()
            logger.debug(f"Клик: {label or selector}")
            return True
        except Exception:
            if required:
                raise RuntimeError(f"Обязательный элемент не найден: {label or selector}")
            logger.debug(f"Элемент не найден (необязательный): {label or selector}")
            return False

    async def _element_exists(self, page: Page, selector: str, timeout: int = 3000) -> bool:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    # ── OTP ─────────────────────────────────────────────────────────────────

    async def _handle_otp(
        self,
        page: Page,
        index: int,
        username: str,
        otp_sel: str,
        activation_id: Optional[str] = None,
    ) -> bool:
        otp_cfg = self.config.otp_config
        mode = otp_cfg.get("mode", "manual")

        if mode == "manual":
            prompt = otp_cfg.get(
                "manual_prompt", "Введите OTP для {username}: "
            ).format(username=username)
            otp_code = await asyncio.get_event_loop().run_in_executor(
                None, input, f"\n[{index}] {prompt}"
            )
            otp_code = otp_code.strip()
            if not otp_code:
                logger.warning(f"[{index}] OTP не введён, пропуск аккаунта")
                return False

        elif mode == "api":
            if not self.sms_client:
                logger.error(f"[{index}] otp.mode=api, но GrizzlySMS клиент не настроен")
                return False
            if not activation_id:
                logger.error(f"[{index}] otp.mode=api, но activation_id не задан")
                return False

            # Уведомляем GrizzlySMS что мы готовы принять SMS
            await self.sms_client.set_status(activation_id, GrizzlySMSClient.STATUS_READY)

            poll_timeout  = self.config.sms_config.get("poll_timeout", 300)
            poll_interval = self.config.sms_config.get("poll_interval", 5)
            otp_code = await self.sms_client.wait_for_code(
                activation_id, timeout=poll_timeout, poll_interval=poll_interval
            )
            if not otp_code:
                logger.error(f"[{index}] SMS не получена для {username}")
                return False
            logger.info(f"[{index}] Получен OTP: {otp_code}")

        else:
            logger.error(f"[{index}] Неизвестный режим OTP: {mode}")
            return False

        await self.human.type_text(page, otp_sel, otp_code)
        submit_sel = (
            self.config.selectors.get("otp_submit_button")
            or self.config.selectors.get("submit_button")
        )
        if submit_sel:
            await self._click_if_exists(page, submit_sel, label="otp_submit")
        return True

    # ── Captcha ──────────────────────────────────────────────────────────────

    async def _captcha_detected(self, page: Page) -> bool:
        captcha_indicators = [
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            ".g-recaptcha",
            ".h-captcha",
            "#captcha",
            "div[class*='captcha']",
        ]
        combined_selector = ", ".join(captcha_indicators)
        return await self._element_exists(page, combined_selector, timeout=100)

    async def _handle_captcha(self, page: Page, index: int, username: str) -> bool:
        captcha_cfg = self.config.captcha_config
        mode = captcha_cfg.get("mode", "manual")

        if mode == "manual":
            logger.warning(f"[{index}] Капча обнаружена для {username}. Решите вручную в браузере.")
            prompt = f"[{index}] Нажмите Enter после решения капчи для {username}..."
            await asyncio.get_event_loop().run_in_executor(None, input, prompt)
            return True

        elif mode == "api":
            # Точка расширения: интеграция с 2captcha, anti-captcha и т.д.
            logger.warning("Captcha API не реализован.")
            return False

        return False

    # ── Utilities ────────────────────────────────────────────────────────────

    async def _save_screenshot(self, page: Page, index: int, username: str, tag: str) -> None:
        screenshots_dir = Path("screenshots")
        screenshots_dir.mkdir(exist_ok=True)
        safe_name = "".join(c if c.isalnum() else "_" for c in username)
        path = screenshots_dir / f"{index:04d}_{safe_name}_{tag}.png"
        try:
            await page.screenshot(path=str(path), full_page=True)
            logger.debug(f"Скриншот сохранён: {path}")
        except Exception as exc:
            logger.debug(f"Не удалось сохранить скриншот: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Results tracker
# ─────────────────────────────────────────────────────────────────────────────

class ResultTracker:
    def __init__(self) -> None:
        self.success: list[str] = []
        self.failed: list[str] = []

    def record(self, username: str, ok: bool) -> None:
        (self.success if ok else self.failed).append(username)

    def print_summary(self) -> None:
        total = len(self.success) + len(self.failed)
        logger.info("=" * 50)
        logger.info(f"Итого обработано: {total}")
        logger.success(f"Успешно: {len(self.success)}")
        if self.success:
            for u in self.success:
                logger.success(f"  + {u}")
        logger.error(f"Ошибки: {len(self.failed)}")
        if self.failed:
            for u in self.failed:
                logger.error(f"  - {u}")
        logger.info("=" * 50)

    def save_json(self, path: str = "results.json") -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"success": self.success, "failed": self.failed}, fh, ensure_ascii=False, indent=2)
        logger.info(f"Результаты сохранены: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

async def main(tg_mode: str = "none", accounts_target: Optional[int] = None, force_headless: bool = False) -> None:
    setup_logging()

    config_path = Path("config.yaml")
    if not config_path.exists():
        logger.error("Файл config.yaml не найден. Создайте его по шаблону из README.")
        sys.exit(1)

    config = ConfigManager(config_path)
    config.load()
    if force_headless:
        config.config.setdefault("browser", {})["headless"] = True

    # Читаем secrets.yaml (ключи из него всегда перекрывают плейсхолдеры из config.yaml)
    _secrets_path = Path(__file__).parent / "secrets.yaml"
    if not _secrets_path.exists():
        _secrets_path = Path("secrets.yaml")
    if _secrets_path.exists():
        try:
            import yaml as _sy
            _sec = _sy.safe_load(_secrets_path.read_text(encoding="utf-8")) or {}
            _key = (_sec.get("grizzlysms") or {}).get("api_key", "").strip()
            if _key:
                config.config.setdefault("grizzlysms", {})["api_key"] = _key
            _tok = (_sec.get("telegram") or {}).get("token", "").strip()
            if _tok:
                config.config.setdefault("telegram", {})["token"] = _tok
        except Exception:
            pass

    max_age = config.config.get("browser", {}).get("profile_max_age_days", 2.0)
    profile_manager = BrowserProfileManager(config.profiles_dir, max_age_days=max_age)
    tracker = ResultTracker()

    # Автоочистка при запуске отключена по запросу пользователя. Очистка доступна вручную через меню.
    # removed = profile_manager.purge_expired()
    # if removed:
    #     logger.info(f"Удалено устаревших профилей: {removed}")

    # ── Telegram менеджер ────────────────────────────────────────────────────
    tg_manager = None
    if tg_mode in ("login", "intercept"):
        tg_cfg = config.telegram_config
        token = tg_cfg.get("token")
        if not token:
            logger.error("Токен Telegram-бота не задан в config.yaml или secrets.yaml!")
            sys.exit(1)
        # menu.py уже запускает поллинг в своём фоновом треде.
        # main.py нужен бот только для отправки уведомлений — поллинг не запускаем.
        tg_manager = TelegramBotManager(token, send_only=True)

    # ── GrizzlySMS клиент ────────────────────────────────────────────────────
    sms_client: Optional[GrizzlySMSClient] = None
    sms_cfg = config.sms_config
    if sms_cfg.get("api_key"):
        sms_client = GrizzlySMSClient(
            api_key=sms_cfg["api_key"],
            http_timeout=sms_cfg.get("http_timeout", 30),
        )
        try:
            balance = await sms_client.get_balance()
            logger.info(f"GrizzlySMS баланс: ${balance:.4f}")
        except Exception as exc:
            logger.error(f"Не удалось подключиться к GrizzlySMS: {exc}")
            sms_client = None

    # ── Режим работы ────────────────────────────────────────────────────────────
    is_auto = config.auto_accounts_count > 0 or accounts_target is not None
    if is_auto:
        target = accounts_target if accounts_target is not None else config.auto_accounts_count
        if not sms_client:
            logger.error("Авто-режим требует GrizzlySMS. Проверьте api_key в secrets.yaml → grizzlysms.api_key")
            sys.exit(1)
        logger.info(f"Режим auto: цель — {target} аккаунт(ов) через GrizzlySMS")
    else:
        manual_accounts = config.accounts
        logger.info(f"Аккаунтов к обработке: {len(manual_accounts)}")

    logger.info(f"Сайт: {config.site_url}")
    logger.info("Управление: Ctrl+C = стоп  |  Ctrl+X = пауза / продолжение")

    # Запускаем монитор клавиатуры для Ctrl+X
    pause_event = asyncio.Event()
    pause_event.set()
    kb_stop = _start_kb_monitor(asyncio.get_running_loop(), pause_event)

    automation = None  # определяем до try чтобы finally мог безопасно обратиться
    try:
        async with async_playwright() as pw:
            automation = LoginAutomation(
                pw, config, profile_manager,
                sms_client=sms_client,
                pause_event=pause_event,
                tg_client=tg_manager,
                tg_mode=tg_mode,
            )

            next_idx = profile_manager.get_next_free_index()

            if is_auto:
                # ── Авто-режим: параллельные инстансы ────────────────────────
                max_concurrent = config.config.get("max_concurrent_accounts", 1)
                success_count  = 0
                attempt_count  = 0
                account_idx    = next_idx
                # task → (attempt_num, account_idx)
                pending: dict[asyncio.Task, tuple[int, int]] = {}

                def _fill_slots() -> None:
                    nonlocal attempt_count, account_idx
                    while len(pending) < max_concurrent and success_count < target:
                        attempt_count += 1
                        cur_idx = account_idx
                        account_idx += 1
                        t = asyncio.create_task(
                            automation.run_account({"phone": "auto"}, cur_idx)
                        )
                        pending[t] = (attempt_count, cur_idx)
                        logger.info(
                            f"─── Запуск попытки #{attempt_count} (профиль {cur_idx}) | "
                            f"✅ {success_count}/{target} | "
                            f"В работе: {len(pending)}/{max_concurrent} ───"
                        )

                _fill_slots()
                while pending:
                    done, _ = await asyncio.wait(
                        pending.keys(), return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in done:
                        att_num, cur_idx = pending.pop(t)
                        try:
                            ok = t.result()
                        except Exception as exc:
                            logger.error(
                                f"Ошибка в попытке #{att_num} (профиль {cur_idx}): {exc}"
                            )
                            ok = False
                        tracker.record(f"auto#{att_num}", ok)
                        if ok:
                            success_count += 1
                            logger.success(
                                f"🎉 Аккаунт {success_count}/{target} создан! "
                                f"(попытка #{att_num})"
                            )
                        else:
                            logger.warning(
                                f"Попытка #{att_num} (профиль {cur_idx}) не удалась, "
                                "продолжаю..."
                            )

                    if success_count >= target:
                        for t in list(pending):
                            t.cancel()
                        if pending:
                            await asyncio.gather(*pending.keys(), return_exceptions=True)
                        pending.clear()
                        break

                    _fill_slots()

                # Цель достигнута — финальное сообщение
                logger.success("=" * 52)
                logger.success(f"  🎯 ЦЕЛЬ ДОСТИГНУТА: {success_count}/{target} аккаунтов!")
                logger.success("=" * 52)

                if tg_manager:
                    final_bal_str = "—"
                    if sms_client:
                        try:
                            fb = await sms_client.get_balance()
                            final_bal_str = f"${fb:.4f}"
                        except Exception:
                            pass
                    await tg_manager.notify_all(
                        f"🎯 Задача выполнена!\n"
                        f"✅ Создано аккаунтов: {success_count}/{target}\n"
                        f"💰 Итоговый баланс: {final_bal_str}"
                    )
            else:
                # ── Ручной режим: список аккаунтов из конфига ──────────────────
                for idx, account in enumerate(manual_accounts):
                    current_idx = next_idx + idx
                    username = (
                        account.get("username")
                        or account.get("email")
                        or account.get("phone", f"account_{current_idx}")
                    )
                    logger.info(
                        f"─── Аккаунт {idx + 1}/{len(manual_accounts)} "
                        f"(Индекс: {current_idx}): {username} ───"
                    )
                    ok = await automation.run_account(account, current_idx)
                    tracker.record(username, ok)
                    if idx < len(manual_accounts) - 1:
                        delay = random.uniform(
                            config.human_behavior.get("delay_between_accounts_min", 2.0),
                            config.human_behavior.get("delay_between_accounts_max", 5.0),
                        )
                        await asyncio.sleep(delay)

            # Ждём фоновые входы для номеров с кулдауном
            bg_tasks = getattr(automation, "_background_tasks", [])
            active_bg = [t for t in bg_tasks if not t.done()]
            if active_bg:
                logger.info(
                    f"⏳ Ожидаю завершения {len(active_bg)} фонового мониторинга номеров "
                    "(кулдаун GrizzlySMS)..."
                )
                await asyncio.gather(*active_bg, return_exceptions=True)
                logger.info("✅ Фоновые мониторинги завершены")

            # Если остались открытые успешные браузеры — ждём Enter
            if getattr(automation, "_kept_contexts", None):
                logger.info("=" * 52)
                logger.info(
                    f"Открытых браузеров: {len(automation._kept_contexts)}. "
                    "Нажмите Enter для закрытия..."
                )
                logger.info("=" * 52)
                await asyncio.get_running_loop().run_in_executor(None, input)
    finally:
        kb_stop.set()

        # Гарантированная отмена всех купленных номеров, по которым нет успешного входа.
        # Это страховка: _run_auto.finally уже пытается отменить через to_cancel,
        # но при жёсткой остановке (KeyboardInterrupt, kill) могут остаться «висячие» номера.
        pending = getattr(automation, "_all_pending", {}) if automation else {}
        if pending and sms_client:
            logger.info(f"⟳ Отменяю {len(pending)} активных номер(а) при остановке...")
            for act_id, ph in list(pending.items()):
                try:
                    await sms_client.cancel(act_id)
                    logger.info(f"  ✓ Отменён +{ph} ({act_id})")
                except Exception as _ce:
                    logger.warning(f"  ✗ Не удалось отменить +{ph} ({act_id}): {_ce}")
            pending.clear()
            # Финальный баланс
            try:
                bal = await sms_client.get_balance()
                logger.info(f"  💰 Баланс после возвратов: ${bal:.4f}")
            except Exception:
                pass

        if sms_client:
            await sms_client.close()
        if tg_manager:
            await tg_manager.stop()
        # В авто-режиме: удаляем все незавершённые профили из chrome_profiles/
        # Успешные профили (chrome_profiles_done/) не трогаем
        if is_auto:
            try:
                removed = profile_manager.purge_incomplete()
                if removed:
                    logger.info(f"Удалено незавершённых профилей: {removed}")
            except Exception as _e:
                logger.warning(f"Ошибка при очистке профилей: {_e}")

    tracker.print_summary()
    tracker.save_json(str(_DATA / "results.json"))


import os

_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# Добавляем путь в AppData для Windows (user-level install)
if os.name == "nt":
    _local_appdata = os.environ.get("LOCALAPPDATA", "")
    if _local_appdata:
        _CHROME_PATHS.insert(0, str(Path(_local_appdata) / r"Google\Chrome\Application\chrome.exe"))


def _find_chrome() -> Optional[str]:
    return next((p for p in _CHROME_PATHS if Path(p).exists()), None)


def _get_profiles(profiles_dir: str = "./chrome_profiles") -> list[Path]:
    return sorted(Path(profiles_dir).glob("profile_*"))


def open_profile(profiles_dir: str = "./chrome_profiles", idx: Optional[int] = None) -> None:
    """
    Открывает профиль Chrome по номеру (1-based).
    Если idx не передан — интерактивный выбор.
    Вызов из bat: python main.py --open 2
    """
    import subprocess
    profiles = _get_profiles(profiles_dir)
    if not profiles:
        print("NO_PROFILES")
        return

    if idx is None:
        print("\nСохранённые профили:")
        for i, p in enumerate(profiles, 1):
            print(f"  {i}. {p.name}")
        try:
            idx = int(input("\nВведите номер: ").strip())
        except ValueError:
            print("Неверный ввод.")
            return

    try:
        selected = profiles[idx - 1]
    except IndexError:
        print(f"Профиль #{idx} не найден.")
        return

    chrome_exe = _find_chrome()
    if not chrome_exe:
        print("CHROME_NOT_FOUND")
        print(f'  chrome.exe --user-data-dir="{selected.resolve()}"')
        return

    print(f"OK:{selected.name}")
    subprocess.Popen([chrome_exe, f"--user-data-dir={selected.resolve()}"])


def list_profiles_cli(profiles_dir: str = "./chrome_profiles") -> None:
    """
    Выводит профили построчно: INDEX|USERNAME|AGE|STATUS|FULLPATH
    STATUS: "active" = в процессе, "done" = успешный вход.
    """
    profiles = _get_profiles(profiles_dir)
    done_dir = Path(profiles_dir + "_done")
    done_profiles = sorted(done_dir.glob("profile_*")) if done_dir.exists() else []

    if not profiles and not done_profiles:
        print("EMPTY")
        return

    def _print_profile(i: int, p: Path, status: str) -> None:
        meta_file = p / BrowserProfileManager.META_FILE
        username = p.name
        age_str = "нет данных"
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                age_days = (time.time() - meta["login_ts"]) / 86400
                username = meta.get("username", p.name)
                hours = int(age_days * 24)
                age_str = f"{hours}ч назад"
            except Exception:
                pass
        print(f"{i}|{username}|{age_str}|{status}|{p.resolve()}")

    for i, p in enumerate(profiles, 1):
        _print_profile(i, p, "active")

    offset = len(profiles)
    for i, p in enumerate(done_profiles, 1):
        _print_profile(offset + i, p, "done")


def purge_profiles_cli(profiles_dir: str = "./chrome_profiles") -> None:
    """Удаляет устаревшие профили. Вызов: python main.py --purge"""
    config_path = Path("config.yaml")
    max_age = 2.0
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            max_age = cfg.get("browser", {}).get("profile_max_age_days", 2.0)
        except Exception:
            pass
    pm = BrowserProfileManager(Path(profiles_dir), max_age_days=max_age)
    removed = pm.purge_expired()
    print(f"PURGED:{removed}")


if __name__ == "__main__":
    # На Windows: перехватываем Ctrl+C и Ctrl+Break через Win32 API.
    # Стандартный Python-обработчик не всегда прерывает asyncio event loop
    # когда он заблокирован на уровне C (IOCP). _thread.interrupt_main()
    # явно прерывает bytecode-выполнение и корректно доходит до asyncio.
    try:
        import ctypes as _ctypes, _thread as _thr
        _PHANDLER = _ctypes.WINFUNCTYPE(_ctypes.c_bool, _ctypes.c_ulong)
        def _ctrl_handler(ctrl_type: int) -> bool:
            if ctrl_type in (0, 1):  # 0=CTRL_C, 1=CTRL_BREAK
                _thr.interrupt_main()
                return True
            return False
        _ctrl_handler_ref = _PHANDLER(_ctrl_handler)
        _ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)
    except Exception:
        pass  # не Windows или нет ctypes — работаем со стандартным поведением

    _args = sys.argv[1:]

    def _get_arg(flag: str) -> Optional[str]:
        try:
            i = _args.index(flag)
            return _args[i + 1]
        except (ValueError, IndexError):
            return None

    if "--open" in _args:
        _idx = _get_arg("--open")
        open_profile(idx=int(_idx) if _idx and _idx.isdigit() else None)
    elif "--list-profiles" in _args:
        list_profiles_cli()
    elif "--purge" in _args:
        purge_profiles_cli()
    else:
        # Определяем Telegram-режим
        if "--tg-login" in _args:
            _tg = "login"
        elif "--tg-intercept" in _args:
            _tg = "intercept"
        else:
            _tg = "none"

        # Целевое количество аккаунтов (--accounts N)
        _cnt_str = _get_arg("--accounts")
        _cnt = int(_cnt_str) if _cnt_str and _cnt_str.isdigit() else None

        _headless = "--headless" in _args  # фоновый режим — без окна браузера

        try:
            asyncio.run(main(tg_mode=_tg, accounts_target=_cnt, force_headless=_headless))
        except KeyboardInterrupt:
            pass


# =============================================================================
# README
# =============================================================================
#
# УСТАНОВКА
# ---------
#   pip install playwright loguru pyyaml
#   playwright install chromium
#
# СТРУКТУРА ФАЙЛОВ
# ----------------
#   main.py          — этот скрипт
#   config.yaml      — конфигурация (см. config.yaml рядом)
#   chrome_profiles/ — папка профилей (создаётся автоматически)
#   automation.log   — файл логов
#   results.json     — итоговые результаты
#   screenshots/     — скриншоты при ошибках входа
#
# ЗАПУСК
# ------
#   python main.py
#
# НАСТРОЙКА СЕЛЕКТОРОВ
# --------------------
# Откройте страницу входа в браузере, нажмите F12 → Inspector,
# найдите поля ввода и кнопки. Скопируйте их атрибуты name/id/type
# и вставьте в config.yaml → selectors.
#
# Примеры:
#   username_field: "input[name='email']"
#   password_field: "input[type='password']"
#   submit_button:  "button[type='submit']"
#   success_indicator: "a[href='/logout']"
#
# РАСШИРЕНИЕ
# ----------
# OTP через API:
#   В методе LoginAutomation._fetch_otp_from_api() добавьте HTTP-запрос
#   к вашему TOTP/SMS-сервису и верните строку с кодом.
#
# Прокси:
#   В _launch_context() добавьте параметр proxy={"server": "http://host:port"}
#   в launch_persistent_context().
#
# Разные типы авторизации:
#   Добавьте в account дополнительные поля (phone, totp_secret и т.д.)
#   и обработайте их в LoginAutomation._login().
#
# ВАЖНО: используйте скрипт только для тестирования на собственных аккаунтах
# или с явного разрешения владельца сайта.
# =============================================================================
