"""Application version from the repo VERSION file and optional GIT_SHA build arg."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _candidates() -> list[Path]:
    here = Path(__file__).resolve()
    # Docker: /app/heardloop/version.py -> /app/VERSION. Dev: .../src/backend/heardloop -> repo root.
    out = [Path("/app/VERSION"), Path.cwd() / "VERSION"]
    for i in (1, 3):
        if len(here.parents) > i:
            out.append(here.parents[i] / "VERSION")
    return out


@lru_cache
def app_version() -> str:
    for path in _candidates():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _VERSION_RE.fullmatch(text):
            return text
    return "0.0.0"


def git_sha() -> str:
    value = (os.environ.get("HEARDLOOP_GIT_SHA") or os.environ.get("GIT_SHA") or "unknown").strip()
    return value or "unknown"
