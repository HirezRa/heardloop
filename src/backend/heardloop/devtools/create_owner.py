"""Bootstrap the first account: a user, a workspace and an owner membership.

Run inside the api container (interactive, the password is never echoed or logged):
    docker compose exec api python -m heardloop.devtools.create_owner --email you@example.com \
        --name "Your Name" --workspace "My workspace"
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from heardloop import audit
from heardloop.auth.service import normalize_email
from heardloop.config import get_settings
from heardloop.db import create_engine, create_session_factory
from heardloop.models import Membership, User, Workspace, WorkspaceRole
from heardloop.security.passwords import PasswordPolicyError, hash_password, validate_password
from heardloop.timeutil import utcnow


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


async def create_owner(email: str, name: str, workspace_name: str, password: str) -> None:
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
            workspace = Workspace(name=workspace_name, created_at=now)
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
            print(f"Created owner {user.id} in workspace {workspace.id}.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first owner account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    email = normalize_email(args.email)
    password = _read_password(email)
    asyncio.run(create_owner(email, args.name.strip(), args.workspace.strip(), password))


if __name__ == "__main__":
    main()
