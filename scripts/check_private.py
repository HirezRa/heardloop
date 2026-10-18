#!/usr/bin/env python3
"""Blocks commits that would leak personal or environment details.

Checks every given file (or all tracked files with --all) for:
  - IPv4 addresses other than 127.0.0.1 / 0.0.0.0 (CIDR ranges like a/12 are allowed)
  - absolute personal paths (C:\\Users\\..., D:\\..., /home/<user>, /Users/<user>)
  - e-mail addresses other than *@example.com / *@users.noreply.github.com
  - private host names (*.local, *.lan, *.internal, *.home)
  - any term listed in .private-terms (git-ignored, one term per line: your name,
    user names, home domain, server names, public IP ...). A line starting with `re:` is a
    case-sensitive regular expression instead of a plain (case-insensitive) term.

Also used as a commit-msg hook (pre-commit passes the message file), so commit messages and their
trailers get the same checks.

Secrets/tokens are covered separately by gitleaks.
Mark a deliberate false positive by putting `privacy-allow` on the same line.
Exit code 1 if anything is found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".lock"}
SKIP_NAMES = {"package-lock.json", "uv.lock", "check_private.py"}

# Network ranges in CIDR notation (e.g. a Docker network range) are configuration, not host addresses.
IPV4 = re.compile(
    r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d.]|/\d)"
)
IP_ALLOW = {"127.0.0.1", "0.0.0.0"}  # privacy-allow
WIN_PATH = re.compile(r"\b[A-Za-z]:\\(?:Users|Documents and Settings|[A-Za-z0-9_ .-]+\\)")
UNIX_HOME = re.compile(r"(?:/home/|/Users/)(?!runner\b|app\b|<)[A-Za-z0-9._-]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_ALLOW = re.compile(r"@(example\.(com|org)|users\.noreply\.github\.com)$", re.IGNORECASE)
PRIVATE_HOST = re.compile(r"\b[a-z0-9-]+\.(?:local|lan|internal|home)\b", re.IGNORECASE)


def load_terms() -> tuple[list[str], list[re.Pattern[str]]]:
    path = ROOT / ".private-terms"
    if not path.exists():
        return [], []
    lines = [t.strip() for t in path.read_text(encoding="utf-8").splitlines()]
    lines = [t for t in lines if t and not t.startswith("#")]
    plain = [t for t in lines if not t.startswith("re:")]
    patterns = [re.compile(t[3:]) for t in lines if t.startswith("re:")]
    return plain, patterns


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines()]


def scan(path: Path, terms: list[str], patterns: list[re.Pattern[str]]) -> list[str]:
    if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    findings: list[str] = []
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    for no, line in enumerate(text.splitlines(), 1):
        if "privacy-allow" in line:
            continue
        hits: list[str] = []
        hits += [f"IP {m}" for m in IPV4.findall(line) if m not in IP_ALLOW]
        hits += [f"path {m.group(0)}" for m in WIN_PATH.finditer(line)]
        hits += [f"home path {m.group(0)}" for m in UNIX_HOME.finditer(line)]
        hits += [f"e-mail {m}" for m in EMAIL.findall(line) if not EMAIL_ALLOW.search(m)]
        hits += [f"private host {m}" for m in PRIVATE_HOST.findall(line)]
        low = line.lower()
        hits += ["private term (see .private-terms)" for t in terms if t.lower() in low]
        hits += ["private pattern (see .private-terms)" for p in patterns if p.search(line)]
        findings += [f"{rel}:{no}: {h}" for h in hits]
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--all", action="store_true", help="scan all tracked files")
    args = parser.parse_args()
    files = tracked_files() if args.all else [Path(f).resolve() for f in args.files]
    terms, patterns = load_terms()
    findings = [f for p in files for f in scan(p, terms, patterns)]
    for f in findings:
        print(f)
    if findings:
        print(f"\n{len(findings)} privacy finding(s). Remove them, or add `privacy-allow` if intentional.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
