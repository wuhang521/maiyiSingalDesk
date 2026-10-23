from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .auth import (
    AuthPrincipal,
    bearer_token,
    create_user_session,
    hash_password,
    normalize_username,
    require_staff_admin,
    require_user,
    token_digest,
    verify_password,
)
from .database import get_db
from .models import UserAccount, UserSession, utcnow
from .member_service import commit_member, load_member, lock_membership
from .schemas import LoginRequest, LoginResponse, UserCreate, UserProfile


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def profile(user: UserAccount) -> UserProfile:
    return UserProfile(
        id=user.id,
        username=user.username,
        display_name=user.staff.name,
        staff_id=user.staff_id,
        role=user.role,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.scalar(
        select(UserAccount)
        .options(selectinload(UserAccount.staff))
        .where(UserAccount.username == normalize_username(payload.username))
        .with_for_update()
    )
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not user.enabled or not user.staff.enabled or user.staff.removed_at:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")

    token, session = create_user_session(db, user)
    user.last_login_at = utcnow()
    db.commit()
    return LoginResponse(
        access_token=token,
        expires_at=session.expires_at,
        user=profile(user),
    )


@router.get("/me", response_model=UserProfile)
def me(user: UserAccount = Depends(require_user)) -> UserProfile:
    return profile(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    authorization: str = Header(default=""),
    user: UserAccount = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    token = bearer_token(authorization)
    session = db.get(UserSession, token_digest(token)) if token else None
    if session and session.user_id == user.id:
        db.delete(session)
        db.commit()


@router.post(
    "/users",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
)
def create_user(payload: UserCreate, principal: AuthPrincipal = Depends(require_staff_admin),
                db: Session = Depends(get_db)) -> UserProfile:
    lock_membership(db, principal)
    username = normalize_username(payload.username)
    if db.scalar(select(UserAccount.id).where(UserAccount.username == username)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="登录账号已存在")
    staff = load_member(db, payload.staff_id)
    if db.scalar(select(UserAccount.id).where(UserAccount.staff_id == payload.staff_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该人员已经绑定登录账号")

    user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        staff_id=payload.staff_id,
        role=payload.role,
        enabled=staff.enabled,
    )
    db.add(user)
    commit_member(db)
    user = db.scalar(
        select(UserAccount)
        .options(selectinload(UserAccount.staff))
        .where(UserAccount.id == user.id)
    )
    return profile(user)
