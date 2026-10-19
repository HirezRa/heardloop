"""Bootstrap the first account: a user, a workspace and an owner membership.

Run inside the api container (interactive, the password is never echoed or logged):
    docker compose exec api python -m heardloop.devtools.create_owner --email you@example.com \
        --name "Your Name" --workspace "My workspace"
    # optional: --time-zone Europe/London  (default: host local zone if IANA, else UTC)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import datetime

from sqlalchemy import select

from heardloop import audit
from heardloop.auth.service import normalize_email
from heardloop.config import get_settings
from heardloop.db import create_engine, create_session_factory
from heardloop.models import Membership, User, Workspace, WorkspaceRole
from heardloop.security.passwords import PasswordPolicyError, hash_password, validate_password
from heardloop.timeutil import available_time_zones, utcnow


def detect_local_time_zone() -> str:
    """IANA zone from the host clock when available; otherwise UTC.

    Uses only the stdlib (no tzlocal): ``datetime.now().astimezone().tzinfo.key`` when that
    key is a known IANA name.
    """
    info = datetime.now().astimezone().tzinfo
    key = getattr(info, "key", None)
    if isinstance(key, str) and key in available_time_zones():
        return key
    return "UTC"


def resolve_time_zone(value: str | None) -> str:
    """Validate an optional IANA name, or fall back to :func:`detect_local_time_zone`."""
    chosen = (value or detect_local_time_zone()).strip()
    if chosen not in available_time_zones():
        raise SystemExit(f"Unknown time zone: {chosen!r} (use an IANA name such as UTC or Europe/London)")
    return chosen


def _read_password(email: str) -> str:
    for _ in range(3):
        first = getpass.getpass("Password (min 12 chars): ")
        second = getpass.getpass("Repeat password: ")
        if first != second:
            print("Passwords do not match.", file=sys.stderr)
            continue
        try:
            validate_password(first, email=email)
        except PasswordPolicyError as exc:
            print(str(exc), file=sys.stderr)
            continue
        return first
    raise SystemExit("Too many attempts.")


async def create_owner(
    email: str,
    name: str,
    workspace_name: str,
    password: str,
    *,
    time_zone: str = "UTC",
) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    now = utcnow()
    try:
        async with factory() as db:
            if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
                raise SystemExit("A user with this e-mail already exists.")
            user = User(
                email=email,
                display_name=name,
                password_hash=hash_password(password),
                password_changed_at=now,
                created_at=now,
            )
            workspace = Workspace(name=workspace_name, time_zone=time_zone, created_at=now)
            db.add_all([user, workspace])
            await db.flush()
            db.add(Membership(user_id=user.id, workspace_id=workspace.id, role=WorkspaceRole.OWNER))
            audit.record(
                db,
                action="owner.bootstrapped",
                entity="workspace",
                entity_id=workspace.id,
                actor_id=user.id,
                workspace_id=workspace.id,
                now=now,
            )
            await db.commit()
            print(f"Created owner {user.id} in workspace {workspace.id} ({time_zone}).")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first owner account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--time-zone",
        default=None,
        help="IANA time zone for the workspace (default: host local zone if detectable, else UTC)",
    )
    args = parser.parse_args()
    email = normalize_email(args.email)
    time_zone = resolve_time_zone(args.time_zone)
    password = _read_password(email)
    asyncio.run(create_owner(email, args.name.strip(), args.workspace.strip(), password, time_zone=time_zone))


if __name__ == "__main__":
    main()
