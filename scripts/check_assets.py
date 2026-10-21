#!/usr/bin/env python3
"""Fail if docs/assets images are unreferenced or if markdown points at missing ones.

Scans README.md and every .md under docs/ for image paths (markdown ![ ](path) and HTML src="...").
Only files under docs/assets/ are checked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

MD_IMG = re.compile(r"!\[[^\]]*]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_SRC = re.compile(r"""(?:src|href)=["']([^"']+)["']""", re.IGNORECASE)
# Plain mentions like `docs/assets/logo.png` or docs/assets/foo.png in prose.
PATH_MENTION = re.compile(
    r"(?:`|(?<![/\w]))((?:docs/)?assets/[A-Za-z0-9_./-]+\.(?:png|jpe?g|gif|svg|webp))`?"
)


def _md_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "AGENTS.md", ROOT / "CHANGELOG.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    files.extend(sorted((ROOT / "examples").rglob("*.md")))
    return [p for p in files if p.is_file() and "cursor-prompts" not in p.parts]  # privacy-allow


def _resolve(ref: str, md_file: Path) -> Path | None:
    path = ref.split("#", 1)[0].strip()
    if not path or path.startswith(("http://", "https://", "data:", "mailto:")):
        return None
    if path.startswith("/"):
        return None
    # README uses docs/assets/...; docs/*.md uses assets/... or ../assets/...
    candidate = (md_file.parent / path).resolve()
    try:
        candidate.relative_to(ASSETS.resolve())
    except ValueError:
        return None
    return candidate


def main() -> int:
    if not ASSETS.is_dir():
        print("docs/assets/ missing", file=sys.stderr)
        return 1

    on_disk = {
        p.resolve()
        for p in ASSETS.rglob("*")
        if p.is_file() and p.suffix.lower() in ASSET_SUFFIXES
    }
    referenced: set[Path] = set()
    missing: list[str] = []

    def consider(ref: str, md_file: Path) -> None:
        target = _resolve(ref, md_file)
        if target is None and ref.startswith("docs/assets/"):
            target = (ROOT / ref).resolve()
            try:
                target.relative_to(ASSETS.resolve())
            except ValueError:
                return
        if target is None:
            return
        if not target.is_file():
            missing.append(f"{md_file.relative_to(ROOT)} -> {ref}")
        else:
            referenced.add(target)

    for md in _md_files():
        text = md.read_text(encoding="utf-8")
        for match in (*MD_IMG.finditer(text), *HTML_SRC.finditer(text)):
            consider(match.group(1), md)
        for match in PATH_MENTION.finditer(text):
            consider(match.group(1), md)

    unreferenced = sorted(p.relative_to(ROOT).as_posix() for p in on_disk - referenced)
    errors = 0
    for item in missing:
        print(f"missing asset: {item}", file=sys.stderr)
        errors += 1
    for item in unreferenced:
        print(f"unreferenced asset: {item}", file=sys.stderr)
        errors += 1
    if errors:
        return 1
    print(f"ok {len(on_disk)} assets referenced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
