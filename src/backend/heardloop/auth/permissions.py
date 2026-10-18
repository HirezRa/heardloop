"""Role -> permission mapping. Endpoints check permissions, never role names directly."""

from enum import StrEnum

from heardloop.models import WorkspaceRole


class Permission(StrEnum):
    WORKSPACE_READ = "workspace.read"
    POSTS_WRITE = "posts.write"
    POSTS_APPROVE = "posts.approve"
    MEMBERS_MANAGE = "members.manage"
    SETTINGS_MANAGE = "settings.manage"
    ACCOUNTS_MANAGE = "accounts.manage"
    AUDIT_READ = "audit.read"


P = Permission

ROLE_PERMISSIONS: dict[WorkspaceRole, frozenset[Permission]] = {
    WorkspaceRole.OWNER: frozenset(Permission),
    WorkspaceRole.EDITOR: frozenset({P.WORKSPACE_READ, P.POSTS_WRITE}),
    WorkspaceRole.APPROVER: frozenset({P.WORKSPACE_READ, P.POSTS_WRITE, P.POSTS_APPROVE}),
    WorkspaceRole.VIEWER: frozenset({P.WORKSPACE_READ}),
}


def has_permission(role: WorkspaceRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
