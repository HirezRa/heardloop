import pytest

from heardloop.auth.permissions import Permission, has_permission
from heardloop.models import WorkspaceRole
from heardloop.security.passwords import (
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)


def test_hash_and_verify() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery") == (True, False)
    assert verify_password(h, "wrong password here")[0] is False


def test_verify_without_hash_never_matches() -> None:
    assert verify_password(None, "anything at all") == (False, False)


@pytest.mark.parametrize(
    ("password", "email"),
    [
        ("short", None),
        ("x" * 129, None),
        (" leading-space-pass", None),
        ("user@example.com", "user@example.com"),
    ],
)
def test_policy_rejects(password: str, email: str | None) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password(password, email=email)


def test_policy_accepts_long_passphrase() -> None:
    validate_password("a long enough passphrase", email="user@example.com")


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        (WorkspaceRole.OWNER, Permission.MEMBERS_MANAGE, True),
        (WorkspaceRole.EDITOR, Permission.POSTS_WRITE, True),
        (WorkspaceRole.EDITOR, Permission.POSTS_APPROVE, False),
        (WorkspaceRole.APPROVER, Permission.POSTS_APPROVE, True),
        (WorkspaceRole.APPROVER, Permission.MEMBERS_MANAGE, False),
        (WorkspaceRole.VIEWER, Permission.WORKSPACE_READ, True),
        (WorkspaceRole.VIEWER, Permission.POSTS_WRITE, False),
    ],
)
def test_role_permissions(role: WorkspaceRole, permission: Permission, allowed: bool) -> None:
    assert has_permission(role, permission) is allowed


def test_every_role_can_read() -> None:
    assert all(has_permission(r, Permission.WORKSPACE_READ) for r in WorkspaceRole)
