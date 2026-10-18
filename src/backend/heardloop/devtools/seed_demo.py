"""Fill an EMPTY database with fictional demo data for README screenshots.

Run only against the separate demo stack (see docs/DEMO.md):
    docker compose -p heardloop-demo ... exec api python -m heardloop.devtools.seed_demo

Safety:
- Refuses unless APP_ENV=development and the database has no users yet.
- All names use example.com / "Example"; the LinkedIn account has a fake token and nothing is ever published
  (the demo stack does not run the worker).
- Prints a random one-time password for demo@example.com, needed only by the screenshot script.
"""

import asyncio
import secrets
import struct
import sys
import uuid
import zlib
from datetime import timedelta

from sqlalchemy import func, select

from heardloop import media
from heardloop.config import get_settings
from heardloop.db import create_engine, create_session_factory
from heardloop.domain.workflow import PostStatus
from heardloop.models import (
    Approval,
    ApprovalDecision,
    LinkedInAccount,
    LinkedInAccountType,
    MediaAsset,
    Membership,
    Post,
    PostVersion,
    User,
    Workspace,
    WorkspaceRole,
)
from heardloop.security.crypto import TokenProtector
from heardloop.security.passwords import hash_password
from heardloop.timeutil import utcnow

DEMO_EMAIL = "demo@example.com"

POSTS: list[tuple[PostStatus, str, bool]] = [
    (
        PostStatus.SCHEDULED,
        "We just shipped approval flows in Heardloop.\n\n"
        "Writers draft, reviewers approve, and the post goes out at the time you picked - "
        "in your own time zone.\n\n"
        "What changed for our team:\n"
        '- No more "is this the final version?" threads\n'
        "- Every edit is saved as a version\n"
        "- A change after approval sends the post back for review\n\n"
        "How does your team review posts before they go live?\n\n#ContentOps #LinkedInTips",
        True,
    ),
    (
        PostStatus.PENDING_APPROVAL,
        "Three lessons from a year of weekly posting:\n\n"
        "1. A plan beats inspiration.\n2. Short posts get read, long posts get saved.\n"
        "3. The first two lines decide everything.\n\nWhich one surprised you?",
        False,
    ),
    (
        PostStatus.DRAFT,
        "Draft: behind the scenes of our product launch - what went well, what we would do differently, "
        "and the checklist we now use for every release.",
        False,
    ),
    (
        PostStatus.PUBLISHED,
        "Hello LinkedIn! This is our first post planned, approved and scheduled with Heardloop.",
        False,
    ),
]


def _demo_png(width: int = 1200, height: int = 627) -> bytes:
    """A plain blue gradient PNG built with the standard library only."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            t = (x / width + y / height) / 2
            rows += bytes((int(29 + 40 * t), int(78 + 90 * t), int(216 + 30 * t)))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(rows), 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


async def main() -> int:
    settings = get_settings()
    if not settings.is_development:
        print("Refusing to seed: APP_ENV is not 'development'.", file=sys.stderr)
        return 2

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    protector = TokenProtector(settings.token_key.get_secret_value())
    now = utcnow()
    password = secrets.token_urlsafe(18)
    try:
        async with factory() as s:
            if (await s.execute(select(func.count()).select_from(User))).scalar_one():
                print("Refusing to seed: the database already has users.", file=sys.stderr)
                return 2

            ws = Workspace(
                name="Example Studio", time_zone="Europe/London", require_approval=True, created_at=now
            )
            # The demo sign-in is an approver: sees posts and accounts, and owners' MFA banner stays away.
            owner = User(email="dana@example.com", display_name="Dana Example", locale="en", created_at=now)
            editor = User(email="sam@example.com", display_name="Sam Writer", locale="en", created_at=now)
            approver = User(
                email=DEMO_EMAIL,
                display_name="Alex Reviewer",
                password_hash=hash_password(password),
                password_changed_at=now,
                locale="en",
                created_at=now,
            )
            s.add_all([ws, owner, editor, approver])
            await s.flush()
            s.add_all(
                [
                    Membership(user_id=owner.id, workspace_id=ws.id, role=WorkspaceRole.OWNER),
                    Membership(user_id=editor.id, workspace_id=ws.id, role=WorkspaceRole.EDITOR),
                    Membership(user_id=approver.id, workspace_id=ws.id, role=WorkspaceRole.APPROVER),
                ]
            )

            acc = LinkedInAccount(
                workspace_id=ws.id,
                type=LinkedInAccountType.PERSON,
                urn="urn:li:person:demo-example",
                display_name="Dana Example",
                access_token_encrypted="",
                access_token_expires_at=now + timedelta(days=52),
                scopes=["openid", "profile", "email", "w_member_social"],
                connected_at=now - timedelta(days=8),
            )
            s.add(acc)
            await s.flush()
            acc.access_token_encrypted = protector.protect(
                "demo-placeholder-token", f"linkedin-account:{acc.id}"
            )

            png = _demo_png()
            asset = MediaAsset(
                workspace_id=ws.id,
                storage_key=media.save(settings.media_root, ws.id, png, media.ImageType("image/png", "png")),
                mime_type="image/png",
                size_bytes=len(png),
                sha256=media.sha256(png),
                created_at=now,
            )
            s.add(asset)
            await s.flush()

            for index, (status, body, with_image) in enumerate(POSTS):
                created = now - timedelta(hours=6 * (index + 1))
                post = Post(workspace_id=ws.id, account_id=acc.id, created_at=created, updated_at=created)
                s.add(post)
                await s.flush()
                author = editor if index % 2 else owner
                version = PostVersion(
                    post_id=post.id,
                    body=body,
                    media_ids=[asset.id] if with_image else [],
                    author_id=author.id,
                    created_at=created,
                )
                s.add(version)
                await s.flush()
                post.current_version_id = version.id
                if status is PostStatus.DRAFT:
                    continue
                post.transition_to(PostStatus.PENDING_APPROVAL, require_approval=True, now=created)
                if status is PostStatus.PENDING_APPROVAL:
                    continue
                post.transition_to(PostStatus.APPROVED, require_approval=True, now=created)
                s.add(
                    Approval(
                        post_id=post.id,
                        version_id=version.id,
                        reviewer_id=approver.id,
                        decision=ApprovalDecision.APPROVED,
                        decided_at=created,
                    )
                )
                post.transition_to(PostStatus.SCHEDULED, require_approval=True, now=created)
                if status is PostStatus.SCHEDULED:
                    tomorrow = now + timedelta(days=1)
                    post.scheduled_at = tomorrow.replace(hour=8, minute=0, second=0, microsecond=0)
                    continue
                post.scheduled_at = created
                post.transition_to(PostStatus.PUBLISHING, require_approval=True, now=created)
                post.mark_published(f"urn:li:share:{uuid.uuid4().int % 10**19}", created)

            await s.commit()
    finally:
        await engine.dispose()

    print(f"Demo data ready. Sign in as {DEMO_EMAIL} with DEMO_PASSWORD={password}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
