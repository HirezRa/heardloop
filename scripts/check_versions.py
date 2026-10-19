#!/usr/bin/env python3
"""Fail if VERSION, package.json and pyproject.toml disagree. Run from repo root.

Also fails when react and react-dom (package.json or package-lock.json) differ —
React 19 refuses to run when the two packages disagree.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _react_pair(pkg: dict) -> tuple[str, str]:
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    return str(deps.get("react", "")), str(deps.get("react-dom", ""))


def _lock_react_pair(lock: dict) -> tuple[str, str]:
    packages = lock.get("packages") or {}
    react = (packages.get("node_modules/react") or {}).get("version", "")
    react_dom = (packages.get("node_modules/react-dom") or {}).get("version", "")
    return str(react), str(react_dom)


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"VERSION is not semver: {version!r}", file=sys.stderr)
        return 1
    pkg_path = ROOT / "src" / "frontend" / "package.json"
    lock_path = ROOT / "src" / "frontend" / "package-lock.json"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
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

    react, react_dom = _react_pair(pkg)
    if not react or not react_dom:
        print("package.json must declare both react and react-dom", file=sys.stderr)
        return 1
    if react != react_dom:
        print(
            f"react / react-dom mismatch in package.json: react={react} react-dom={react_dom}",
            file=sys.stderr,
        )
        return 1

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_react, lock_dom = _lock_react_pair(lock)
    if not lock_react or not lock_dom:
        print("package-lock.json must pin both react and react-dom", file=sys.stderr)
        return 1
    if lock_react != lock_dom:
        print(
            f"react / react-dom mismatch in package-lock.json: "
            f"react={lock_react} react-dom={lock_dom}",
            file=sys.stderr,
        )
        return 1
    if lock_react != react:
        print(
            f"package-lock.json react={lock_react} does not match package.json react={react}",
            file=sys.stderr,
        )
        return 1

    print(f"ok {version} (react {react})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
