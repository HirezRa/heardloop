"""KNOWN_CODES in notify.py must appear in the frontend publishErrors.ts mapping."""

from pathlib import Path

from heardloop.notify import KNOWN_CODES

_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "publishErrors.ts"


def test_every_known_code_is_in_publish_errors_ts() -> None:
    text = _TS.read_text(encoding="utf-8")
    missing = sorted(code for code in KNOWN_CODES if f"{code}:" not in text and f'"{code}"' not in text)
    assert not missing, f"Add these codes to publishErrors.ts: {missing}"
