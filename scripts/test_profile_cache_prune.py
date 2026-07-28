"""Self-check: чистка кэша профиля освобождает место и НЕ трогает сессию."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))

# То, что Chrome пересоздаёт сам — должно исчезнуть
JUNK = [
    "BrowserMetrics/BrowserMetrics-1.pma",
    "GrShaderCache/blob.bin",
    "ShaderCache/x.bin",
    "Crashpad/reports/r.dmp",
    "component_crx_cache/c.crx",
    "extensions_crx_cache/e.crx",
    "Safe Browsing/list.store",
    "optimization_guide_model_store/model.tflite",
    "WasmTtsEngine/engine.wasm",
    "OnDeviceHeadSuggestModel/model.bin",
    "Default/Cache/Cache_Data/f_000001",
    "Default/Code Cache/js/index",
    "Default/GPUCache/data_0",
    "Default/DawnCache/d.bin",
    "Default/Service Worker/CacheStorage/sw/data",
    "Default/Service Worker/ScriptCache/s.bin",
]
# То, на чём держится вход — должно остаться байт в байт
SESSION = [
    "Local State",
    ".profile_meta.json",
    "Default/Preferences",
    "Default/Network/Cookies",
    "Default/Network/Network Persistent State",
    "Default/Local Storage/leveldb/000003.log",
    "Default/IndexedDB/https_flipkart.com_0.indexeddb.leveldb/CURRENT",
    "Default/Session Storage/000004.log",
    "Default/Web Data",
]


def _make_profile() -> Path:
    p = Path(tempfile.mkdtemp(prefix="prof_")) / "profile_0001_9876543210"
    for rel in JUNK:
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"j" * 4096)
    for rel in SESSION:
        f = p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"keep-me")
    return p


def main() -> None:
    import menu as m

    prof = _make_profile()
    before = m._dir_size(prof)
    assert before > len(JUNK) * 4000, before

    freed = m._prune_profile_cache(prof, measure=True)
    assert freed > len(JUNK) * 4000, f"освободилось подозрительно мало: {freed}"

    for rel in JUNK:
        assert not (prof / rel).exists(), f"кэш не удалён: {rel}"
    for rel in SESSION:
        f = prof / rel
        assert f.exists(), f"УДАЛЕНО ЛИШНЕЕ — сессия сломана: {rel}"
        assert f.read_bytes() == b"keep-me", f"файл сессии повреждён: {rel}"

    assert m._dir_size(prof) < before
    # Повторный прогон безопасен и уже нечего освобождать
    assert m._prune_profile_cache(prof, measure=True) == 0

    # measure=False не считает, но чистит
    prof2 = _make_profile()
    assert m._prune_profile_cache(prof2) == 0
    assert not (prof2 / "Default/Cache/Cache_Data/f_000001").exists()
    assert (prof2 / "Default/Network/Cookies").exists()

    # Чужой путь (не профиль Chrome) не трогаем вообще
    alien = Path(tempfile.mkdtemp(prefix="alien_"))
    (alien / "Default").mkdir()          # только каталог, без признаков профиля
    (alien / "important.txt").write_text("data", encoding="utf-8")
    junk_in_alien = alien / "Default" / "Cache" / "f"
    junk_in_alien.parent.mkdir(parents=True)
    junk_in_alien.write_bytes(b"x")
    m._prune_profile_cache(alien)
    assert (alien / "important.txt").exists()

    stray = Path(tempfile.mkdtemp(prefix="stray_"))
    (stray / "payload.bin").write_bytes(b"x" * 100)
    assert m._prune_profile_cache(stray, measure=True) == 0
    assert (stray / "payload.bin").exists(), "не-профиль не должен чиститься"

    src = (ROOT / "subhub" / "menu.py").read_text(encoding="utf-8", errors="replace")
    # Чистка обязана висеть на закрытии браузера, иначе кэш копится как раньше
    i = src.find("async def _close_browser_session")
    assert "_prune_profile_cache" in src[i: i + 2000], "нет хука на закрытие браузера"
    # В списке кэшей не должно быть ничего от сессии
    i = src.find("_PROFILE_CACHE_DIRS = (")
    lst = src[i: src.find(")", i)]
    for bad in ("Network", "Preferences", "Local State", "Local Storage",
                "IndexedDB", "Session Storage", "Web Data", "profile_meta"):
        assert bad not in lst, f"в списке на удаление оказалось: {bad}"

    print("PASS profile_cache_prune")


if __name__ == "__main__":
    main()
