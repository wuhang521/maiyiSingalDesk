from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .auth import AuthPrincipal, hash_password, normalize_username, require_staff_admin
from .database import get_db
from .member_service import (
    commit_member, load_member, lock_membership, protect_access, revoke_sessions, set_member_enabled,
)
from .models import Staff, UserAccount, utcnow
from .schemas import StaffRead

router = APIRouter(prefix="/api/v1/admin/members", tags=["user-management"])


class AccountRead(BaseModel):
    id: int
    username: str
    role: Literal["admin", "member"]
    enabled: bool
    last_login_at: datetime | None
    model_config = ConfigDict(from_attributes=True)


class MemberRead(StaffRead):
    removed_at: datetime | None
    user: AccountRead | None


class MemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "member"] = "member"

    @field_validator("name", "username", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class MemberUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # Username is immutable once an account exists. For existing staff without
    # an account, username + password opens an account on the same staff record.
    username: str | None = Field(default=None, min_length=2, max_length=80)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Literal["admin", "member"] = "member"
    enabled: bool

    @field_validator("name", "username", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.get("", response_model=list[MemberRead])
def list_members(principal: AuthPrincipal = Depends(require_staff_admin), db: Session = Depends(get_db)):
    return db.scalars(select(Staff).options(selectinload(Staff.user), selectinload(Staff.channels))
                      .order_by(Staff.name, Staff.id)).all()


@router.post("", response_model=MemberRead, status_code=201)
def create_member(payload: MemberCreate, principal: AuthPrincipal = Depends(require_staff_admin),
                  db: Session = Depends(get_db)):
    lock_membership(db, principal)
    staff = Staff(name=payload.name, enabled=True)
    staff.user = UserAccount(username=normalize_username(payload.username),
                             password_hash=hash_password(payload.password), role=payload.role, enabled=True)
    db.add(staff)
    commit_member(db)
    return load_member(db, staff.id)


@router.patch("/{staff_id}", response_model=MemberRead)
def update_member(staff_id: int, payload: MemberUpdate,
                  principal: AuthPrincipal = Depends(require_staff_admin), db: Session = Depends(get_db)):
    lock_membership(db, principal)
    staff = load_member(db, staff_id)
    user = staff.user
    if not payload.enabled or (user and user.role == "admin" and payload.role != "admin"):
        protect_access(db, staff, principal)
    if user and payload.username is not None and normalize_username(payload.username) != user.username:
        raise HTTPException(422, "已有登录账号不能修改")
    if not user:
        if not payload.username or not payload.password:
            raise HTTPException(422, "开通登录需要填写账号和至少 8 位密码")
        staff.user = user = UserAccount(username=normalize_username(payload.username),
                                        password_hash=hash_password(payload.password), role=payload.role)
    elif payload.password:
        if principal.user and principal.user.id == user.id:
            raise HTTPException(409, "请由其他管理员重置当前账号密码")
        user.password_hash = hash_password(payload.password)
        revoke_sessions(db, staff)
    if user.role != payload.role:
        revoke_sessions(db, staff)
    user.role = payload.role
    staff.name = payload.name
    set_member_enabled(db, staff, payload.enabled)
    commit_member(db)
    return load_member(db, staff_id)


@router.delete("/{staff_id}", response_model=MemberRead)
def remove_member(staff_id: int, principal: AuthPrincipal = Depends(require_staff_admin),
                  db: Session = Depends(get_db)):
    lock_membership(db, principal)
    staff = load_member(db, staff_id, allow_removed=True)
    protect_access(db, staff, principal)
    set_member_enabled(db, staff, False)
    if not staff.removed_at:
        staff.removed_at = utcnow()
    commit_member(db)
    return staff


@router.post("/{staff_id}/restore", response_model=MemberRead)
def restore_member(staff_id: int, principal: AuthPrincipal = Depends(require_staff_admin),
                   db: Session = Depends(get_db)):
    lock_membership(db, principal)
    staff = load_member(db, staff_id, allow_removed=True)
    if not staff.removed_at:
        raise HTTPException(409, "该人员未被移除")
    staff.removed_at = None
    # Restore into disabled state; enabling is a separate, explicit decision.
    set_member_enabled(db, staff, False)
    commit_member(db)
    return staff
