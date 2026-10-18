"""Post lifecycle - single source of truth for allowed status transitions."""

from enum import StrEnum


class PostStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


S = PostStatus

# (from, to) -> allowed when approval is required? / allowed when it is not?
_RULES: dict[tuple[PostStatus, PostStatus], tuple[bool, bool]] = {
    (S.DRAFT, S.PENDING_APPROVAL): (True, False),
    (S.DRAFT, S.SCHEDULED): (False, True),
    (S.DRAFT, S.PUBLISHING): (False, True),
    (S.PENDING_APPROVAL, S.APPROVED): (True, True),
    (S.PENDING_APPROVAL, S.DRAFT): (True, True),  # rejected with a comment
    (S.APPROVED, S.SCHEDULED): (True, True),
    (S.APPROVED, S.PUBLISHING): (True, True),  # publish now
    (S.APPROVED, S.DRAFT): (True, True),  # edited after approval
    (S.SCHEDULED, S.PUBLISHING): (True, True),  # worker only
    (S.SCHEDULED, S.APPROVED): (True, False),  # unschedule
    (S.SCHEDULED, S.DRAFT): (True, True),  # edit or unschedule
    (S.PUBLISHING, S.PUBLISHED): (True, True),
    (S.PUBLISHING, S.FAILED): (True, True),
    (S.FAILED, S.SCHEDULED): (True, True),  # manual retry
    (S.FAILED, S.DRAFT): (True, True),
}


class InvalidTransitionError(ValueError):
    def __init__(self, from_status: PostStatus, to_status: PostStatus) -> None:
        super().__init__(f"Post cannot move from {from_status} to {to_status}.")
        self.from_status = from_status
        self.to_status = to_status


def can_transition(from_status: PostStatus, to_status: PostStatus, *, require_approval: bool) -> bool:
    rule = _RULES.get((from_status, to_status))
    if rule is None:
        return False
    with_approval, without_approval = rule
    return with_approval if require_approval else without_approval


def ensure_transition(from_status: PostStatus, to_status: PostStatus, *, require_approval: bool) -> None:
    if not can_transition(from_status, to_status, require_approval=require_approval):
        raise InvalidTransitionError(from_status, to_status)
