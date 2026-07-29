"""Live-check: обновление с GitHub реально скачается.

Ходит в сеть, поэтому не входит в общий набор (имя не `test_`).
Запуск: python scripts/check_ota_reachable.py

Проверяет то, что ломает обновление молча: файл есть в `_UPDATE_FILES`, но на
GitHub его нет (не закоммитили / переименовали) — тогда обновление откажется
применяться целиком.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "subhub"))


def main() -> None:
    import menu as m

    owner, repo, token = m._parse_git_remote()
    assert owner and repo, "не прочитал репозиторий из .git/config"
    print(f"  репозиторий: {owner}/{repo}")

    files = list(m._UPDATE_FILES)
    t0 = time.time()
    got = m._gh_get_many(owner, repo, files, token)
    dt = time.time() - t0

    missing = [f for f in files if f not in got]
    # Догружаем поштучно — ровно как это делает само обновление
    for name in list(missing):
        try:
            got[name] = m._gh_get(
                f"https://raw.githubusercontent.com/{owner}/{repo}/master/{name}", token)
            missing.remove(name)
        except Exception as exc:
            print(f"  ✘ {name}: {exc}")

    assert not missing, ("недоступны на GitHub — обновление не применится:\n  "
                         + "\n  ".join(missing))
    print(f"  скачано {len(got)}/{len(files)} за {dt:.1f}с (одно соединение)")

    diff = []
    for name, data in got.items():
        local = ROOT / name
        if not local.exists():
            continue
        # Сравниваем без учёта перевода строки: git отдаёт LF, локально CRLF,
        # и это не расхождение содержимого.
        want = local.read_bytes().replace(b"\r\n", b"\n")
        if want != data.replace(b"\r\n", b"\n"):
            diff.append(name)
    print("  локальные копии совпадают с master" if not diff
          else f"  отличаются (есть незапушенное): {', '.join(diff)}")

    ref = m._gh_get(
        f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/master", token)
    assert b"sha" in ref, "GitHub API не отдал SHA ветки master"
    print("  GitHub API отвечает, SHA master получен")
    print("PASS ota_reachable")


if __name__ == "__main__":
    main()
