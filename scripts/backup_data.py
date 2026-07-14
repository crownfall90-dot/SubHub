"""Export backup zip: secrets, cards, gifts, config, app settings (no chrome profiles by default)."""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INCLUDE = (
    "secrets.yaml",
    "config.yaml",
    "VERSION",
    "LICENSE",
    "data/pay_method.txt",
    "data/gift_cards.json",
    "data/cards.json",
    "data/card_order.json",
    "data/app_settings.json",
    "data/ggsel_done.json",
)


def build_backup(out: Path | None = None, *, include_profiles: bool = False) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out or (ROOT / "data" / "backups" / f"subhub_backup_{stamp}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest: list[str] = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in DEFAULT_INCLUDE:
            p = ROOT / rel
            if p.is_file():
                zf.write(p, rel.replace("\\", "/"))
                manifest.append(rel)
        if include_profiles:
            for folder in ("chrome_profiles_done", "cookies_backup"):
                base = ROOT / folder
                if not base.is_dir():
                    continue
                for f in base.rglob("*"):
                    if f.is_file() and f.stat().st_size < 25_000_000:
                        arc = f"{folder}/{f.relative_to(base).as_posix()}"
                        zf.write(f, arc)
                        manifest.append(arc)
        zf.writestr(
            "backup_manifest.json",
            json.dumps({"created": stamp, "files": manifest}, ensure_ascii=False, indent=2),
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SubHub backup zip")
    ap.add_argument("-o", "--out", type=Path, default=None)
    ap.add_argument("--profiles", action="store_true", help="Include chrome_profiles_done + cookies_backup")
    args = ap.parse_args()
    path = build_backup(args.out, include_profiles=args.profiles)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
