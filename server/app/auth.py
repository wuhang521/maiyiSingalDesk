from dataclasses import dataclass
from datetime import timedelta
import base64
import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import get_db
from .models import UserAccount, UserSession, utcnow


SESSION_DAYS = 90


@dataclass(frozen=True)
class AuthPrincipal:
    user: UserAccount | None
    service: bool = False


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_user_session(db: Session, user: UserAccount) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session = UserSession(
        token_hash=token_digest(token),
        user_id=user.id,
        expires_at=now + timedelta(days=SESSION_DAYS),
        last_used_at=now,
    )
    db.add(session)
    return token, session


def bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.casefold() == "bearer" else ""


def require_shared_api_key(x_signaldesk_key: str = Header(default="")) -> None:
    expected = get_settings().api_key
    if expected and secrets.compare_digest(x_signaldesk_key, expected):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="访问令牌无效")


def require_api_key(
    authorization: str = Header(default=""),
    x_signaldesk_key: str = Header(default=""),
    db: Session = Depends(get_db),
) -> AuthPrincipal:
    expected = get_settings().api_key
    if expected and secrets.compare_digest(x_signaldesk_key, expected):
        return AuthPrincipal(user=None, service=True)

    token = bearer_token(authorization)
    session = db.scalar(
        select(UserSession)
        .options(selectinload(UserSession.user).selectinload(UserAccount.staff))
        .where(UserSession.token_hash == token_digest(token))
    ) if token else None
    now = utcnow()
    if not session or session.expires_at <= now:
        if session:
            db.delete(session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")
    if not session.user.enabled or not session.user.staff.enabled or session.user.staff.removed_at:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")

    if session.last_used_at < now - timedelta(days=1):
        session.last_used_at = now
        session.expires_at = now + timedelta(days=SESSION_DAYS)
        db.commit()
    return AuthPrincipal(user=session.user)


def require_user(principal: AuthPrincipal = Depends(require_api_key)) -> UserAccount:
    if principal.user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请使用个人账号登录")
    return principal.user


def require_staff_admin(principal: AuthPrincipal = Depends(require_api_key)) -> AuthPrincipal:
    """Allow personal admins and the existing server-to-server management key."""
    if not principal.service and (not principal.user or principal.user.role != "admin"):
        raise HTTPException(status_code=403, detail="只有管理员可以管理用户")
    return principal
