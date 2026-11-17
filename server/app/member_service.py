"""Membership invariants shared by the admin screen and legacy staff endpoints."""
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import AuthPrincipal
from .models import NotificationOutbox, Staff, UserAccount, UserSession


def set_supervisor(db: Session, staff: Staff, supervisor_id: int | None) -> None:
    """Validate a reporting change while the membership rows are locked."""
    if supervisor_id is None:
        staff.supervisor_id = None
        return
    supervisor = db.get(Staff, supervisor_id, with_for_update=True, populate_existing=True)
    account_enabled = select(UserAccount.id).where(
        UserAccount.staff_id == supervisor_id, UserAccount.enabled.is_(True),
    ).with_for_update()
    if not supervisor or not supervisor.enabled or supervisor.removed_at or not db.scalar(account_enabled):
        raise HTTPException(422, "请选择已启用且已开通登录的直属上级")
    visited = {staff.id}
    ancestor = supervisor
    while ancestor:
        if ancestor.id in visited:
            raise HTTPException(422, "直属上级不能是本人或任何下级")
        visited.add(ancestor.id)
        ancestor_id = ancestor.supervisor_id
        ancestor = db.get(Staff, ancestor_id, with_for_update=True, populate_existing=True) if ancestor_id is not None else None
    staff.supervisor_id = supervisor_id


def staff_and_report_ids(db: Session, supervisor_id: int) -> set[int]:
    reports: dict[int, list[int]] = {}
    rows = db.execute(select(Staff.id, Staff.supervisor_id).where(
        Staff.supervisor_id.is_not(None), Staff.removed_at.is_(None),
    ))
    for staff_id, parent_id in rows:
        reports.setdefault(parent_id, []).append(staff_id)
    visible = {supervisor_id}
    pending = list(reports.get(supervisor_id, ()))
    while pending:
        staff_id = pending.pop()
        if staff_id not in visible:
            visible.add(staff_id)
            pending.extend(reports.get(staff_id, ()))
    return visible


def lock_membership(db: Session, principal: AuthPrincipal) -> None:
    # Stable lock ordering serializes role/availability changes in MySQL, including
    # two administrators trying to remove each other or the last active admin.
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    db.scalars(select(UserAccount).order_by(UserAccount.id).with_for_update()
               .execution_options(populate_existing=True)).all()
    db.scalars(select(Staff).order_by(Staff.id).with_for_update()
               .execution_options(populate_existing=True)).all()
    if principal.user:
        user = principal.user
        if user.role != "admin" or not user.enabled or not user.staff.enabled or user.staff.removed_at:
            raise HTTPException(403, "管理员身份已失效，请重新登录")


def load_member(db: Session, staff_id: int, *, allow_removed: bool = False, lock: bool = False) -> Staff:
    query = select(Staff).options(selectinload(Staff.user), selectinload(Staff.channels)).where(Staff.id == staff_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    staff = db.scalar(query)
    if not staff:
        raise HTTPException(404, "人员不存在")
    if staff.removed_at and not allow_removed:
        raise HTTPException(409, "该人员已移除，请先恢复用户")
    return staff


def protect_access(db: Session, staff: Staff, principal: AuthPrincipal) -> None:
    """Called while membership rows are locked, before removing admin access."""
    user = staff.user
    if user and user.enabled and staff.enabled and not staff.removed_at and user.role == "admin":
        others = db.scalar(select(UserAccount.id).join(Staff).where(
            UserAccount.id != user.id, UserAccount.role == "admin",
            UserAccount.enabled.is_(True), Staff.enabled.is_(True), Staff.removed_at.is_(None),
        ).limit(1))
        if others is None:
            raise HTTPException(409, "至少保留一名启用的管理员，请先添加其他管理员")
    if principal.user and principal.user.staff_id == staff.id:
        raise HTTPException(409, "不能停用、移除或降级当前登录的管理员")


def revoke_sessions(db: Session, staff: Staff) -> None:
    if staff.user:
        db.execute(delete(UserSession).where(UserSession.user_id == staff.user.id))


def set_member_enabled(db: Session, staff: Staff, enabled: bool) -> None:
    staff.enabled = enabled
    if staff.user:
        staff.user.enabled = enabled
    if not enabled:
        revoke_sessions(db, staff)
        # Cancel queued reminders, preserving delivery history. The worker also
        # re-checks membership before delivery to cover older queue entries.
        for channel in staff.channels:
            for item in db.scalars(select(NotificationOutbox).where(
                NotificationOutbox.platform == channel.platform,
                NotificationOutbox.recipient_id == channel.recipient_id,
                NotificationOutbox.status == "pending",
            )):
                item.status = "cancelled"
                item.last_error = "人员已停用或移除"


def commit_member(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "姓名、登录账号或渠道接收 ID 已存在，请检查后重试") from None


def require_assignable(staff: Staff) -> None:
    if not staff.enabled or staff.removed_at:
        raise HTTPException(409, "该人员已停用或移除，请选择其他负责人")
