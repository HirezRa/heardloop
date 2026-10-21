"""Workspaces and members (FR-02). Every route checks membership + permission server-side."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from heardloop import audit
from heardloop.api.deps import (
    CurrentPrincipal,
    Db,
    WorkspaceAccess,
    client_ip,
    require_client_header,
    require_permission,
)
from heardloop.api.routes_mfa import confirm_second_factor
from heardloop.api.schemas import (
    AddMemberRequest,
    MemberOut,
    MembershipOut,
    MfaCodeRequest,
    UpdateMemberRequest,
)
from heardloop.auth.permissions import Permission
from heardloop.auth.service import revoke_other_sessions
from heardloop.models import (
    AuditLogEntry,
    LinkedInAccount,
    LinkedInAccountStatus,
    Membership,
    Notification,
    RecoveryCode,
    User,
    UserSession,
    Workspace,
    WorkspaceRole,
)
from heardloop.security.passwords import PasswordPolicyError, hash_password_async, validate_password
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces", tags=["workspaces"], dependencies=[Depends(require_client_header)]
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
ManageMembers = Annotated[WorkspaceAccess, Depends(require_permission(Permission.MEMBERS_MANAGE))]


@router.get("")
async def list_workspaces(principal: CurrentPrincipal, db: Db) -> list[MembershipOut]:
    rows = (
        await db.execute(
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(Membership.user_id == principal.user.id)
            .order_by(Workspace.name)
        )
    ).all()
    return [MembershipOut(workspace_id=w.id, workspace_name=w.name, role=m.role) for m, w in rows]


@router.get("/{workspace_id}/members")
async def list_members(access: ReadAccess, db: Db) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == access.workspace_id,
                User.is_active.is_(True),
                User.deactivated_at.is_(None),
            )
            .order_by(User.display_name)
        )
    ).all()
    return [MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role) for m, u in rows]


@router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(body: AddMemberRequest, access: ManageMembers, request: Request, db: Db) -> MemberOut:
    now = utcnow()
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None:
        if not body.initial_password:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "initial_password_required")
        try:
            validate_password(body.initial_password, email=body.email)
        except PasswordPolicyError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        user = User(
            email=body.email,
            display_name=body.display_name,
            password_hash=await hash_password_async(body.initial_password),
            password_changed_at=now,
            created_at=now,
            email_notifications=False,  # an owner typed this address in: the person opts in themselves
        )
        db.add(user)
        await db.flush()
    else:
        existing = await db.get(Membership, (user.id, access.workspace_id))
        if existing is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "already_member")

    db.add(Membership(user_id=user.id, workspace_id=access.workspace_id, role=body.role))
    audit.record(
        db,
        action="member.added",
        entity="membership",
        entity_id=user.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes={"role": body.role.value},
        now=now,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()  # a parallel request added the same user/membership first
        raise HTTPException(status.HTTP_409_CONFLICT, "already_member") from None
    return MemberOut(user_id=user.id, email=user.email, display_name=user.display_name, role=body.role)


async def _owner_count(db: Db, workspace_id: uuid.UUID) -> int:
    """Locks the owner rows, so two owners demoting/removing each other cannot both pass the check."""
    owners = (
        await db.execute(
            select(Membership.user_id)
            .where(Membership.workspace_id == workspace_id, Membership.role == WorkspaceRole.OWNER)
            .with_for_update()
        )
    ).all()
    return len(owners)


async def _get_membership(db: Db, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    membership = await db.get(Membership, (user_id, workspace_id))
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member_not_found")
    return membership


@router.patch("/{workspace_id}/members/{user_id}")
async def update_member(
    user_id: uuid.UUID, body: UpdateMemberRequest, access: ManageMembers, request: Request, db: Db
) -> MemberOut:
    membership = await _get_membership(db, access.workspace_id, user_id)
    old_role = membership.role
    demoting_owner = old_role == WorkspaceRole.OWNER and body.role != WorkspaceRole.OWNER
    if demoting_owner and await _owner_count(db, access.workspace_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "last_owner")
    membership.role = body.role
    audit.record(
        db,
        action="member.role_changed",
        entity="membership",
        entity_id=user_id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes={"from": old_role.value, "to": body.role.value},
        now=utcnow(),
    )
    await db.commit()
    user = await db.get(User, user_id)
    assert user is not None
    return MemberOut(user_id=user.id, email=user.email, display_name=user.display_name, role=membership.role)


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, access: ManageMembers, request: Request, db: Db) -> None:
    membership = await _get_membership(db, access.workspace_id, user_id)
    if membership.role == WorkspaceRole.OWNER and await _owner_count(db, access.workspace_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "last_owner")
    await db.delete(membership)
    remaining = (
        await db.execute(select(func.count()).select_from(Membership).where(Membership.user_id == user_id))
    ).scalar_one()
    if remaining == 0:
        # User has no workspace left: end their sessions.
        await revoke_other_sessions(db, user_id, keep=None)
    audit.record(
        db,
        action="member.removed",
        entity="membership",
        entity_id=user_id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        now=utcnow(),
    )
    await db.commit()


@router.post("/{workspace_id}/members/{user_id}/anonymise", status_code=status.HTTP_204_NO_CONTENT)
async def anonymise_member(
    user_id: uuid.UUID, body: MfaCodeRequest, access: ManageMembers, request: Request, db: Db
) -> None:
    """Deactivates a member and strips their personal data; the row stays so history still resolves.

    Owner with two-step verification, who confirms with a fresh code (no password). Refused for yourself,
    for the last owner, and for people who also belong to another workspace (their account is not ours
    to close). Everything happens in one transaction.
    """
    target_id = user_id
    await _get_membership(db, access.workspace_id, target_id)  # 404 for people outside this workspace
    if target_id == access.principal.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot_anonymise_self")
    elsewhere = (
        await db.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.user_id == target_id, Membership.workspace_id != access.workspace_id)
        )
    ).scalar_one()
    if elsewhere:
        raise HTTPException(status.HTTP_409_CONFLICT, "member_in_other_workspaces")

    await confirm_second_factor(db, request, access.principal.user.id, body.code)

    target = (await db.execute(select(User).where(User.id == target_id).with_for_update())).scalar_one()
    membership = await _get_membership(db, access.workspace_id, target_id)
    if membership.role == WorkspaceRole.OWNER and await _owner_count(db, access.workspace_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "last_owner")

    now = utcnow()
    # Accounts this person connected: forget the tokens (rows stay, posts reference them).
    connected = (
        await db.execute(
            select(AuditLogEntry.entity_id).where(
                AuditLogEntry.action == "linkedin.connected",
                AuditLogEntry.actor_id == target_id,
                AuditLogEntry.workspace_id == access.workspace_id,
            )
        )
    ).scalars()
    account_ids = [uuid.UUID(e) for e in set(connected) if e]
    if account_ids:
        for acc in (
            await db.execute(select(LinkedInAccount).where(LinkedInAccount.id.in_(account_ids)))
        ).scalars():
            acc.access_token_encrypted = ""
            acc.refresh_token_encrypted = None
            acc.status = LinkedInAccountStatus.REVOKED
            acc.needs_reconnect_reason = None
            acc.needs_reconnect_at = None

    await db.execute(delete(Membership).where(Membership.user_id == target_id))
    await db.execute(delete(UserSession).where(UserSession.user_id == target_id))
    await db.execute(delete(Notification).where(Notification.user_id == target_id))
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == target_id))
    # Their sign-in and activity rows stay for history, without the IP address.
    await db.execute(
        update(AuditLogEntry)
        .where(AuditLogEntry.actor_id == target_id, AuditLogEntry.ip_address.is_not(None))
        .values(ip_address=None)
        .execution_options(synchronize_session=False)
    )
    target.email = f"deleted+{target.id}@invalid"
    target.display_name = "Former member"
    target.password_hash = None  # nothing verifies against it
    target.mfa_secret_encrypted = None
    target.mfa_enabled_at = None
    target.mfa_last_used_step = None
    target.email_notifications = False
    target.ui_prefs = {}
    target.failed_login_count = 0
    target.locked_until = None
    target.is_active = False
    target.deactivated_at = now
    audit.record(
        db,
        action="member.anonymised",
        entity="user",
        entity_id=target_id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        now=now,
    )
    await db.commit()
