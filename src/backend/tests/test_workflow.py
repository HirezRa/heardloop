import pytest

from heardloop.domain.workflow import InvalidTransitionError, PostStatus, can_transition, ensure_transition

S = PostStatus


@pytest.mark.parametrize(
    ("from_status", "to_status", "require_approval", "expected"),
    [
        (S.DRAFT, S.PENDING_APPROVAL, True, True),
        (S.DRAFT, S.PENDING_APPROVAL, False, False),
        (S.DRAFT, S.SCHEDULED, True, False),
        (S.DRAFT, S.SCHEDULED, False, True),
        (S.PENDING_APPROVAL, S.APPROVED, True, True),
        (S.PENDING_APPROVAL, S.DRAFT, True, True),
        (S.PENDING_APPROVAL, S.PUBLISHED, True, False),
        (S.APPROVED, S.SCHEDULED, True, True),
        (S.SCHEDULED, S.PUBLISHING, True, True),
        (S.SCHEDULED, S.APPROVED, False, False),
        (S.PUBLISHING, S.PUBLISHED, True, True),
        (S.PUBLISHING, S.FAILED, False, True),
        (S.FAILED, S.SCHEDULED, True, True),
        (S.PUBLISHED, S.DRAFT, True, False),
    ],
)
def test_can_transition(from_status: S, to_status: S, require_approval: bool, expected: bool) -> None:
    assert can_transition(from_status, to_status, require_approval=require_approval) is expected


@pytest.mark.parametrize("require_approval", [True, False])
def test_published_is_final(require_approval: bool) -> None:
    assert not any(can_transition(S.PUBLISHED, to, require_approval=require_approval) for to in S)


def test_ensure_transition_raises_with_details() -> None:
    with pytest.raises(InvalidTransitionError) as info:
        ensure_transition(S.DRAFT, S.PUBLISHED, require_approval=True)
    assert info.value.from_status is S.DRAFT
    assert info.value.to_status is S.PUBLISHED
