"""Shared helpers for whether a post's current version has been approved."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from heardloop.models import Approval, ApprovalDecision, Post


async def version_approved(db: AsyncSession, post: Post) -> bool:
    """True when the post's current version has an APPROVED decision (used by schedule and retry-failed)."""
    found = (
        await db.execute(
            select(Approval.id)
            .where(
                Approval.post_id == post.id,
                Approval.version_id == post.current_version_id,
                Approval.decision == ApprovalDecision.APPROVED,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None
