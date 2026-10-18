#!/usr/bin/env python3
"""Smoke test for the docs/he/ English-checker exception. Run: python scripts/test_check_english.py"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_english  # noqa: E402

# Escape form so this file stays English-only for check_english.py.
HEBREW = "\u05de\u05d3\u05e8\u05d9\u05da \u05d4\u05ea\u05e7\u05e0\u05d4"


def main() -> int:
    he_file = ROOT / "docs" / "he" / "INSTALL.md"
    if not he_file.is_file():
        print("missing docs/he/INSTALL.md", file=sys.stderr)
        return 1
    assert check_english.scan(he_file) == [], "Hebrew under docs/he/ must pass"

    bad = ROOT / "docs" / "x.md"
    try:
        bad.write_text(HEBREW + "\n", encoding="utf-8")
        findings = check_english.scan(bad)
        assert findings, "Hebrew under docs/x.md must fail"
        assert any("non-English" in f for f in findings)
    finally:
        bad.unlink(missing_ok=True)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
