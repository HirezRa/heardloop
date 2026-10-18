#!/usr/bin/env python3
"""Fail if VERSION, package.json and pyproject.toml disagree. Run from repo root."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"VERSION is not semver: {version!r}", file=sys.stderr)
        return 1
    pkg = json.loads((ROOT / "src" / "frontend" / "package.json").read_text(encoding="utf-8"))
    py = (ROOT / "src" / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', py)
    py_ver = m.group(1) if m else ""
    pkg_ver = str(pkg.get("version", ""))
    ok = version == pkg_ver == py_ver
    if not ok:
        print(
            f"Version mismatch: VERSION={version} package.json={pkg_ver} pyproject.toml={py_ver}",
            file=sys.stderr,
        )
        return 1
    print(f"ok {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
