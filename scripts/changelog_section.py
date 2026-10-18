#!/usr/bin/env python3
"""Extract one Keep a Changelog section from CHANGELOG.md. Usage: python scripts/changelog_section.py 0.9.0"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: changelog_section.py X.Y.Z", file=sys.stderr)
        return 2
    ver = sys.argv[1].lstrip("v")
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = re.compile(rf"(?ms)^## \[{re.escape(ver)}\][^\n]*\n(.*?)(?=^## |\Z)")
    m = pattern.search(text)
    if not m:
        print(f"No CHANGELOG section for {ver}", file=sys.stderr)
        return 1
    body = m.group(0).strip() + "\n"
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
