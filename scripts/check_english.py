#!/usr/bin/env python3
"""Keeps the repository English-only.

Fails if any text file contains characters from non-Latin scripts (Hebrew, Arabic, Cyrillic, CJK, ...).
Exceptions: UI copy in src/frontend/src/i18n.ts and the Hebrew user documentation in docs/he/.

Usage: python scripts/check_english.py [files...]   or   --all (all tracked files)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {Path("src/frontend/src/i18n.ts")}
ALLOWED_PREFIXES = (Path("docs/he"),)
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".lock"}
SKIP_NAMES = {"package-lock.json", "uv.lock"}
NON_LATIN = re.compile(
    "["
    "\u0370-\u03ff"  # Greek
    "\u0400-\u052f"  # Cyrillic
    "\u0590-\u05ff"  # Hebrew
    "\u0600-\u06ff\u0750-\u077f"  # Arabic
    "\u0e00-\u0e7f"  # Thai
    "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"  # Japanese, Chinese
    "\uac00-\ud7af"  # Korean
    "\ufb1d-\ufb4f"  # Hebrew presentation forms
    "]"
)


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines()]


def _allowed(rel: Path) -> bool:
    if rel in ALLOWED:
        return True
    return any(rel == prefix or prefix in rel.parents for prefix in ALLOWED_PREFIXES)


def scan(path: Path) -> list[str]:
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:
        rel = path
    if _allowed(rel) or path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return []
    return [f"{rel}:{no}: non-English text" for no, line in enumerate(lines, 1) if NON_LATIN.search(line)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    files = tracked_files() if args.all else [Path(f) for f in args.files]
    findings = [f for p in files for f in scan(p)]
    for f in findings:
        print(f)
    if findings:
        print(
            f"\n{len(findings)} line(s) with non-English text. The repo is English-only "
            "(exceptions: src/frontend/src/i18n.ts and docs/he/)."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
