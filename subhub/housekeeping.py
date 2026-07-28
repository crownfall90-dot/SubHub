"""Уборка на диске: кэши Chrome в профилях и ротация debug/.

Вынесено из menu.py. Логика не менялась: чистка кэша висит на закрытии
браузера, ротация debug/ — на _startup_cleanup().
"""
from __future__ import annotations

import contextlib
import time
from pathlib import Path

from common import dir_size as _dir_size
from paths import ROOT as _HERE

PROFILES_DIR        = _HERE / "chrome_profiles"
DONE_PROFILES_DIR   = _HERE / "chrome_profiles_done"
USED_PROFILES_DIR   = _HERE / "chrome_profiles_used"
BACKUP_PROFILES_DIR = _HERE / "chrome_profiles_backup"


# Кэши Chrome, которые можно удалять: браузер пересоздаёт их сам. Сессия живёт
# в Default/Network/* (куки), Default/Preferences, Local State и
# .profile_meta.json — их здесь нет и быть не должно. Только явные пути,
# никаких glob'ов, чтобы нельзя было случайно снести сессию.
_PROFILE_CACHE_DIRS = (
    "BrowserMetrics", "GrShaderCache", "ShaderCache", "GraphiteDawnCache",
    "Crashpad", "component_crx_cache", "extensions_crx_cache",
    # Скачиваемые Chrome компоненты и ML-модели: в самом крупном профиле это
    # 120 МБ из 282. Нужны для голоса/подсказок/фишинга — нашим сценариям нет.
    "Safe Browsing", "optimization_guide_model_store", "WasmTtsEngine",
    "OnDeviceHeadSuggestModel", "ActorSafetyLists", "SafetyTips",
    "AutofillStates", "TrustTokenKeyCommitments", "MEIPreload",
    "PrivacySandboxAttestationsPreloaded", "ClientSidePhishing",
    "OriginTrials", "Subresource Filter", "FileTypePolicies",
    "Default/Cache", "Default/Code Cache", "Default/GPUCache",
    "Default/DawnCache", "Default/DawnGraphiteCache", "Default/DawnWebGPUCache",
    "Default/Service Worker/CacheStorage", "Default/Service Worker/ScriptCache",
)


# Аргументы Chrome, не дающие профилю разрастаться. Выделены отдельно, чтобы
# бенчмарк мог запустить браузер и с ними, и без них (scripts/bench_slim_args.py).
_PROFILE_SLIM_ARGS = (
    "--disable-component-update",      # не качать Safe Browsing / ML-модели
    "--disable-breakpad",              # не писать Crashpad-дампы
    "--disable-gpu-shader-disk-cache",
    "--disk-cache-size=1048576",
    "--media-cache-size=1048576",
)


def _prune_profile_cache(profile_path, measure: bool = False) -> int:
    """Удаляет кэши Chrome внутри профиля, не затрагивая сессию.

    measure=True — посчитать освобождённое (медленно: обход файлов). На закрытии
    браузера считать незачем, там важна скорость.
    Возвращает освобождённые байты (0 при measure=False).
    """
    import shutil as _sh_pc
    p = Path(profile_path)
    # Страховка от чужого пути: без этих признаков это не профиль Chrome
    if not (p / "Default").is_dir() and not (p / "Local State").exists():
        return 0
    freed = 0
    for rel in _PROFILE_CACHE_DIRS:
        d = p / rel
        if not d.is_dir():
            continue
        before = _dir_size(d) if measure else 0
        _sh_pc.rmtree(str(d), ignore_errors=True)
        if measure:
            # Chrome мог держать часть файлов — считаем только реально удалённое
            freed += max(0, before - (_dir_size(d) if d.exists() else 0))
    return freed


def _all_profile_dirs() -> list:
    """Все папки профилей во всех каталогах (done / новые / used / backup)."""
    out = []
    for _d in (DONE_PROFILES_DIR, PROFILES_DIR, USED_PROFILES_DIR, BACKUP_PROFILES_DIR):
        if _d.exists():
            out.extend(p for p in sorted(_d.glob("profile_*")) if p.is_dir())
    return out


_DEBUG_KEEP_FILES = 50
_DEBUG_KEEP_DAYS  = 7.0


def _rotate_debug_dir(keep: int = _DEBUG_KEEP_FILES,
                      max_age_days: float = _DEBUG_KEEP_DAYS,
                      root: Path | None = None) -> int:
    """Оставляет в debug/ только свежую диагностику: не старше max_age_days и
    не больше keep файлов (самые новые). Возвращает освобождённые байты.

    Скриншоты пишут больше десятка мест по всему коду; ротация одна на старте —
    дешевле, чем ограничение в каждом месте записи. Старый скриншот не нужен:
    разбираем всегда последний прогон.
    """
    # root задаётся явно, чтобы тест физически не мог попасть в настоящий debug/:
    # раньше он подменял _HERE у menu.py, и после выноса функции подмена стала
    # незаметным no-op — ротация ушла чистить реальные файлы.
    dbg = (Path(root) if root is not None else _HERE) / "debug"
    if not dbg.is_dir():
        return 0
    files = []
    for f in dbg.rglob("*"):
        with contextlib.suppress(OSError):
            if f.is_file():
                st = f.stat()
                files.append((st.st_mtime, st.st_size, f))
    if not files:
        return 0
    files.sort(reverse=True)                      # новые первыми
    cutoff = time.time() - max_age_days * 86400
    freed = 0
    for i, (mtime, size, f) in enumerate(files):
        if i < keep and mtime >= cutoff:
            continue
        try:
            f.unlink()
            freed += size
        except OSError:
            pass
    return freed
