"""Regenerate vendor/motionsites_prompts/INDEX.md from downloaded .md files."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "vendor" / "motionsites_prompts"
CATALOG = DIR / "catalog.json"


def main() -> None:
    cat = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {"prompts": []}
    by_slug = {p["slug"]: p for p in cat.get("prompts", [])}
    files = sorted(DIR.glob("*.md"))
    lines = ["# MotionSites — скачанные промпты\n", "| Slug | Title | Category | File |", "|------|-------|----------|------|"]
    for f in files:
        if f.name.upper() == "INDEX.MD":
            continue
        slug = f.stem
        meta = by_slug.get(slug, {})
        title = meta.get("title", slug)
        cat_name = meta.get("category", "—")
        lines.append(f"| `{slug}` | {title} | {cat_name} | [{f.name}]({f.name}) |")
    missing = [p for p in cat.get("prompts", []) if p.get("tier") == "free" and not (DIR / f"{p['slug']}.md").exists()]
    if missing:
        lines.append("\n## Не скачано (free)\n")
        for p in missing:
            lines.append(f"- `{p['slug']}` — {p['title']}")
    (DIR / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"INDEX.md: {len(files)} files, {len(missing)} free missing")


if __name__ == "__main__":
    main()
