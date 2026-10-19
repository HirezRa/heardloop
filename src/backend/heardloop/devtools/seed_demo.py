"""Fill an EMPTY database with fictional demo data for README screenshots.

Run only against the separate demo stack (see docs/DEMO.md):
    docker compose -p heardloop-demo ... exec api python -m heardloop.devtools.seed_demo

Safety:
- Refuses unless APP_ENV=development and the database has no users yet.
- All names use example.com / invented labels; the LinkedIn account has a fake token and nothing is
  ever published (the demo stack does not run the worker).
- The demo owner has a password (printed once) and TOTP already enabled with a fixed demo secret
  (DEMO_TOTP_SECRET below) so the screenshot script can compute the second factor itself.
"""

from __future__ import annotations

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
from heardloop.delivery import webhook_secret_context
from heardloop.domain.workflow import PostStatus
from heardloop.models import (
    AppMeta,
    Approval,
    ApprovalDecision,
    Campaign,
    CampaignStatus,
    LinkedInAccount,
    LinkedInAccountType,
    MediaAsset,
    Membership,
    MetricSource,
    Post,
    PostMetric,
    PostTemplate,
    PostVersion,
    User,
    Workspace,
    WorkspaceRole,
)
from heardloop.security import totp
from heardloop.security.crypto import TokenProtector
from heardloop.security.passwords import hash_password
from heardloop.timeutil import utcnow
from heardloop.updates import META_KEY, UpdateCheckState
from heardloop.version import app_version

# Fixed demo TOTP secret (development / screenshots only). Documented in docs/DEMO.md.
DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # noqa: S105 — public demo seed, not a real credential
DEMO_OWNER_EMAIL = "alex@example.com"
DEMO_PASSWORD_ENV = "DEMO_PASSWORD"  # noqa: S105 — env var name only


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
                name="Acme Studio",
                time_zone="Europe/London",
                require_approval=True,
                webhook_url="https://hooks.example.com/heardloop",
                webhook_include_text=False,
                created_at=now,
            )
            owner = User(
                email=DEMO_OWNER_EMAIL,
                display_name="Alex Demo",
                password_hash=hash_password(password),
                password_changed_at=now,
                locale="en",
                ui_prefs={
                    "locale": "en",
                    "date_format": "dmy",
                    "time_format": "24h",
                    "font_size": "m",
                    "theme": "light",
                    "accent": "#0a66c2",
                    "dismissed_update": None,
                },
                created_at=now,
            )
            editor = User(email="sam@example.com", display_name="Sam Writer", locale="en", created_at=now)
            approver = User(
                email="robin@example.com", display_name="Robin Reviewer", locale="en", created_at=now
            )
            s.add_all([ws, owner, editor, approver])
            await s.flush()

            owner.mfa_secret_encrypted = protector.protect(DEMO_TOTP_SECRET, f"user-mfa:{owner.id}")
            owner.mfa_enabled_at = now
            ws.webhook_secret_encrypted = protector.protect(
                "demo-webhook-secret-not-real", webhook_secret_context(ws.id)
            )

            s.add_all(
                [
                    Membership(user_id=owner.id, workspace_id=ws.id, role=WorkspaceRole.OWNER),
                    Membership(user_id=editor.id, workspace_id=ws.id, role=WorkspaceRole.EDITOR),
                    Membership(user_id=approver.id, workspace_id=ws.id, role=WorkspaceRole.APPROVER),
                ]
            )

            campaign = Campaign(
                workspace_id=ws.id,
                name="Launch week",
                goal="Introduce Heardloop to our network",
                audience="Ops and marketing leads",
                start_date=now.date() - timedelta(days=7),
                end_date=now.date() + timedelta(days=21),
                tags=["launch", "product"],
                color="#0a66c2",
                status=CampaignStatus.ACTIVE,
                created_at=now,
            )
            s.add(campaign)
            await s.flush()

            template = PostTemplate(
                workspace_id=ws.id,
                name="Weekly tip",
                body="Tip for {{date}}:\n\n{{custom_tip}}\n\n— {{name}}",
                link_url="https://example.com/heardloop",
                link_title="Heardloop",
                link_description="Plan, approve and schedule LinkedIn posts.",
                campaign_id=campaign.id,
                tags=["tips"],
                created_by=owner.id,
                created_at=now,
                updated_at=now,
                use_count=2,
            )
            s.add(template)

            acc = LinkedInAccount(
                workspace_id=ws.id,
                type=LinkedInAccountType.PERSON,
                urn="urn:li:person:demo-alex",
                display_name="Alex Demo",
                access_token_encrypted="",
                access_token_expires_at=now + timedelta(days=52),
                scopes=["openid", "profile", "w_member_social"],
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

            published_post: Post | None = None
            history_post: Post | None = None

            for index, (status, body, with_image) in enumerate(POSTS):
                created = now - timedelta(hours=6 * (index + 1))
                post = Post(
                    workspace_id=ws.id,
                    account_id=acc.id,
                    campaign_id=campaign.id if index < 2 else None,
                    created_at=created,
                    updated_at=created,
                )
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

                # Second version on the scheduled post so History shows a diff.
                if status is PostStatus.SCHEDULED:
                    older = PostVersion(
                        post_id=post.id,
                        body=body.replace("just shipped", "are shipping"),
                        media_ids=[asset.id] if with_image else [],
                        author_id=author.id,
                        created_at=created - timedelta(hours=1),
                    )
                    s.add(older)
                    history_post = post

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
                published_post = post

            if published_post is not None:
                s.add(
                    PostMetric(
                        post_id=published_post.id,
                        captured_at=now - timedelta(days=1),
                        impressions=4200,
                        reactions=86,
                        comments=12,
                        reposts=5,
                        clicks=140,
                        saves=9,
                        followers=1200,
                        note="Demo numbers",
                        recorded_by=owner.id,
                        source=MetricSource.MANUAL,
                    )
                )

            # Fake a newer release so the update banner appears for owners.
            current = app_version()
            parts = [int(p) for p in current.split(".")]
            parts[-1] += 1
            fake_latest = ".".join(str(p) for p in parts)
            state = UpdateCheckState(
                checked_at=now.isoformat(),
                error=None,
                latest=fake_latest,
                url=f"https://github.com/HirezRa/heardloop/releases/tag/v{fake_latest}",
                published_at=now.isoformat(),
                notes="## Demo release\n\nFictional notes for the screenshot banner.",
            )
            s.add(AppMeta(key=META_KEY, value=state.model_dump_json(), updated_at=now))

            await s.commit()
            _ = history_post
    finally:
        await engine.dispose()

    code = totp.code_at(DEMO_TOTP_SECRET, totp.time_step(now))
    print(
        f"Demo data ready. Sign in as {DEMO_OWNER_EMAIL} with "
        f"{DEMO_PASSWORD_ENV}={password} DEMO_TOTP_SECRET={DEMO_TOTP_SECRET} "
        f"(example code now={code})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
