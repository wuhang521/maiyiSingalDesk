"""Membership invariants shared by the admin screen and legacy staff endpoints."""
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import AuthPrincipal
from .models import NotificationOutbox, Staff, UserAccount, UserSession


def lock_membership(db: Session, principal: AuthPrincipal) -> None:
    # Stable lock ordering serializes role/availability changes in MySQL, including
    # two administrators trying to remove each other or the last active admin.
    db.scalars(select(UserAccount).order_by(UserAccount.id).with_for_update()
               .execution_options(populate_existing=True)).all()
    db.scalars(select(Staff).order_by(Staff.id).with_for_update()
               .execution_options(populate_existing=True)).all()
    if principal.user:
        user = principal.user
        if user.role != "admin" or not user.enabled or not user.staff.enabled or user.staff.removed_at:
            raise HTTPException(403, "管理员身份已失效，请重新登录")


def load_member(db: Session, staff_id: int, *, allow_removed: bool = False) -> Staff:
    staff = db.scalar(select(Staff).options(selectinload(Staff.user), selectinload(Staff.channels))
                      .where(Staff.id == staff_id))
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
