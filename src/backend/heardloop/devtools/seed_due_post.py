"""Development smoke test: creates a workspace, a fake LinkedIn account and one post that is due now.

Run inside the api container:  docker compose exec api python -m heardloop.devtools.seed_due_post
Refuses to run unless APP_ENV=development. The account has a fake token, so within one worker poll the post
ends up `failed` with `linkedin_not_configured` (no LinkedIn app in .env) or `token_invalid` (LinkedIn
rejected the fake token). Either proves the claim/attempt path works without posting anything.
"""

import asyncio
import sys
from datetime import timedelta

from sqlalchemy import select

from heardloop.config import get_settings
from heardloop.db import create_engine, create_session_factory
from heardloop.domain.workflow import PostStatus
from heardloop.models import LinkedInAccount, LinkedInAccountType, Post, PostVersion, User, Workspace
from heardloop.security.crypto import TokenProtector
from heardloop.timeutil import utcnow


async def main() -> int:
    settings = get_settings()
    if not settings.is_development:
        print("Refusing to seed: APP_ENV is not 'development'.", file=sys.stderr)
        return 2

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    protector = TokenProtector(settings.token_key.get_secret_value())
    now = utcnow()
    async with factory() as s:
        ws = Workspace(name="Dev workspace", require_approval=False, created_at=now)
        s.add(ws)
        await s.flush()
        acc = LinkedInAccount(
            workspace_id=ws.id,
            type=LinkedInAccountType.PERSON,
            urn="urn:li:person:dev-placeholder",
            display_name="Dev placeholder",
            access_token_encrypted="",
            access_token_expires_at=now + timedelta(days=60),
            connected_at=now,
        )
        s.add(acc)
        await s.flush()
        acc.access_token_encrypted = protector.protect("dev-placeholder-token", f"linkedin-account:{acc.id}")
        post = Post(workspace_id=ws.id, account_id=acc.id, created_at=now, updated_at=now)
        post.transition_to(PostStatus.SCHEDULED, require_approval=False, now=now)
        post.scheduled_at = now - timedelta(seconds=5)
        s.add(post)
        author = (await s.execute(select(User).limit(1))).scalar_one_or_none()
        if author is None:
            print("Create the owner first (create_owner).", file=sys.stderr)
            return 2
        await s.flush()
        version = PostVersion(
            post_id=post.id, body="Heardloop dev smoke test", author_id=author.id, created_at=now
        )
        s.add(version)
        await s.flush()
        post.current_version_id = version.id
        await s.commit()
        print(f"Seeded due post {post.id}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
