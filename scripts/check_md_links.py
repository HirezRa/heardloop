#!/usr/bin/env python3
"""Fail on broken relative links and missing #anchors in markdown (no network).

Checks README.md and docs/**/*.md (skips docs/cursor-prompts/).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# [text](target) — skip images (handled by check_assets) and bare URLs without ]
LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _slug(text: str) -> str:
    """GitHub-ish heading slug: lower, strip punctuation, spaces to hyphens."""
    s = text.strip().lower()
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s


def _headings(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found: set[str] = set()
    for m in HEADING.finditer(text):
        title = m.group(2)
        # strip trailing markdown links in headings
        title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
        title = title.strip(" #")
        found.add(_slug(title))
    return found


def _md_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "SECURITY.md", ROOT / "CHANGELOG.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    files.extend(sorted((ROOT / "examples").rglob("*.md")))
    return [p for p in files if p.is_file() and "cursor-prompts" not in p.parts]


def main() -> int:
    files = _md_files()
    heading_cache = {p.resolve(): _headings(p) for p in files}
    errors = 0

    for md in files:
        text = md.read_text(encoding="utf-8")
        for m in LINK.finditer(text):
            target = m.group(2).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                if target.startswith("#"):
                    anchor = target[1:]
                    if anchor and anchor not in heading_cache[md.resolve()]:
                        print(f"{md.relative_to(ROOT)}: missing anchor {target!r}", file=sys.stderr)
                        errors += 1
                continue
            path_part, _, frag = target.partition("#")
            if not path_part:
                continue
            dest = (md.parent / path_part).resolve()
            if not dest.exists():
                print(f"{md.relative_to(ROOT)}: missing file {path_part!r}", file=sys.stderr)
                errors += 1
                continue
            if frag and dest.suffix.lower() in {".md", ""}:
                if dest.is_dir():
                    continue
                heads = heading_cache.get(dest) or (_headings(dest) if dest.suffix.lower() == ".md" else set())
                if frag not in heads:
                    print(
                        f"{md.relative_to(ROOT)}: missing anchor #{frag} in {path_part}",
                        file=sys.stderr,
                    )
                    errors += 1

    if errors:
        return 1
    print(f"ok links in {len(files)} markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
