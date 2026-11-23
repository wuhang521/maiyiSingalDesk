"""Short-lived WeChat binding requests confirmed by an authenticated administrator."""

import base64
from datetime import timedelta, timezone
import hashlib
import json
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .auth import AuthPrincipal, require_shared_api_key, require_staff_admin, token_digest
from .channel_push_worker import profile_wechat_senders
from .channel_subscription_models import ChannelSubscription
from .channel_subscriptions import lock_sqlite_writer
from .config import get_settings
from .database import get_db
from .member_service import commit_member, load_member, lock_membership, require_assignable
from .models import NotificationOutbox, StaffChannel, WechatPairing, utcnow


router = APIRouter(prefix="/api/v1", tags=["wechat-pairing"])
MEMBER_PATH = "/admin/members/{staff_id}/wechat-pairing"
SCAN_PATH = "/wechat-pairing/scan"
EXPIRES_IN = timedelta(minutes=5)


class WechatClaim(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")
    recipient_id: str = Field(max_length=160, pattern=r"^[A-Za-z0-9_.-]+@im\.wechat$")
    profile: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


def pairing_admin(principal: AuthPrincipal = Depends(require_staff_admin)) -> AuthPrincipal:
    if principal.user is None:
        raise HTTPException(401, "请使用管理员账号登录")
    return principal


def scan_base() -> str:
    value = get_settings().wechat_pairing_public_base_url.strip().rstrip("/")
    try:
        url = urlsplit(value)
        if (url.hostname and url.username is None and url.password is None and not url.query and not url.fragment
                and not any(character.isspace() for character in value)
                and (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}))):
            return value
    except ValueError:
        pass
    raise HTTPException(503, "后台尚未配置微信扫码绑定入口")


def require_profile(recipient: str, profile: str) -> None:
    path = get_settings().channel_wechat_routes_file
    try:
        if path and profile_wechat_senders(path)(recipient, profile):
            return
    except (OSError, ValueError):
        raise HTTPException(503, "Hermes 微信路由暂不可用") from None
    raise HTTPException(409, "请向已接入迈医协同的 Hermes 微信机器人发送绑定指令")


def channels(db: Session, staff_id: int, *, lock: bool = False) -> list[StaffChannel]:
    query = select(StaffChannel).where(StaffChannel.staff_id == staff_id, StaffChannel.platform == "weixin").order_by(StaffChannel.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list(db.scalars(query))


def binding_hash(bindings: list[StaffChannel]) -> str:
    return token_digest(json.dumps([
        [item.id, item.recipient_id, item.enabled, item.wechat_profile, item.updated_at.isoformat()]
        for item in bindings
    ]))


def owned_pairing(db: Session, staff_id: int, pairing_id: str, principal: AuthPrincipal, *, lock: bool = False) -> WechatPairing:
    row = db.get(WechatPairing, staff_id, with_for_update=lock, populate_existing=lock)
    if row is None or row.id != pairing_id or row.requested_by != principal.user.id:
        raise HTTPException(404, "扫码请求已失效，请重新生成二维码")
    return row


def pairing_status(row: WechatPairing, bindings: list[StaffChannel] | None = None) -> dict:
    state = "confirmed" if row.confirmed_at else "expired" if row.expires_at <= utcnow() else "ready" if row.recipient_id else "waiting"
    result = {"id": row.id, "state": state, "expires_at": row.expires_at.replace(tzinfo=timezone.utc).isoformat()}
    if state == "ready":
        account = row.recipient_id.removesuffix("@im.wechat")
        result.update(recipient_hint=f"{account[:3]}…{account[-3:]}@im.wechat",
                      replacing=any(item.enabled and item.recipient_id != row.recipient_id for item in bindings or []))
    return result


@router.post(MEMBER_PATH)
def create_pairing(staff_id: int, response: Response, principal: AuthPrincipal = Depends(pairing_admin), db: Session = Depends(get_db)):
    base = scan_base()
    if not get_settings().channel_wechat_routes_file:
        raise HTTPException(503, "后台尚未配置 Hermes 微信路由")
    lock_sqlite_writer(db)
    lock_membership(db, principal)
    staff = load_member(db, staff_id)
    require_assignable(staff)
    if not staff.user or not staff.user.enabled:
        raise HTTPException(409, "请先为该成员启用迈医协同账号")
    code = secrets.token_urlsafe(24)
    row = db.get(WechatPairing, staff_id, with_for_update=True)
    if row is None:
        row = WechatPairing(staff_id=staff_id)
        db.add(row)
    row.id = uuid4().hex
    row.requested_by = principal.user.id
    row.code_hash = token_digest(code)
    row.previous_binding_hash = binding_hash(channels(db, staff_id, lock=True))
    row.expires_at = utcnow() + EXPIRES_IN
    row.recipient_id = row.profile = row.confirmed_at = None
    commit_member(db)
    response.headers["Cache-Control"] = "no-store"
    return dict(pairing_status(row), scan_url=f"{base}/api/v1{SCAN_PATH}#{code}")


@router.get(MEMBER_PATH + "/{pairing_id}")
def read_pairing(staff_id: int, pairing_id: str, response: Response, principal: AuthPrincipal = Depends(pairing_admin), db: Session = Depends(get_db)):
    row = owned_pairing(db, staff_id, pairing_id, principal)
    response.headers["Cache-Control"] = "no-store"
    return pairing_status(row, channels(db, staff_id) if row.recipient_id else None)


@router.delete(MEMBER_PATH + "/{pairing_id}")
def cancel_pairing(staff_id: int, pairing_id: str, principal: AuthPrincipal = Depends(pairing_admin), db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    row = owned_pairing(db, staff_id, pairing_id, principal, lock=True)
    if row.confirmed_at is None:
        row.expires_at = utcnow()
        row.recipient_id = row.profile = None
    db.commit()
    return pairing_status(row)


@router.post("/wechat-pairing/claim", dependencies=[Depends(require_shared_api_key)])
def claim_pairing(payload: WechatClaim, db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    row = db.scalar(select(WechatPairing).where(WechatPairing.code_hash == token_digest(payload.code)).with_for_update())
    if row is None or row.confirmed_at or row.expires_at <= utcnow():
        raise HTTPException(409, "绑定指令已失效，请在电脑上重新生成二维码")
    if row.recipient_id and (row.recipient_id != payload.recipient_id or row.profile != payload.profile):
        raise HTTPException(409, "此绑定指令已由一个微信会话验证")
    existing = db.scalar(select(StaffChannel).where(StaffChannel.platform == "weixin", StaffChannel.recipient_id == payload.recipient_id))
    if existing and existing.staff_id != row.staff_id:
        raise HTTPException(409, "这个微信已关联其他迈医协同账号，请联系管理员核对")
    require_profile(payload.recipient_id, payload.profile)
    row.recipient_id = payload.recipient_id
    row.profile = payload.profile
    db.commit()
    return {"state": "ready"}


@router.post(MEMBER_PATH + "/{pairing_id}/confirm")
def confirm_pairing(staff_id: int, pairing_id: str, principal: AuthPrincipal = Depends(pairing_admin), db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    lock_membership(db, principal)
    staff = load_member(db, staff_id)
    require_assignable(staff)
    if not staff.user or not staff.user.enabled:
        raise HTTPException(409, "该成员的迈医协同账号已停用")
    row = owned_pairing(db, staff_id, pairing_id, principal, lock=True)
    if row.confirmed_at:
        return pairing_status(row)
    if not row.recipient_id or not row.profile or row.expires_at <= utcnow():
        raise HTTPException(409, "请发送本次绑定指令，或重新生成二维码")
    bindings = channels(db, staff_id, lock=True)
    if binding_hash(bindings) != row.previous_binding_hash:
        raise HTTPException(409, "微信绑定已被修改，请重新生成二维码核对")
    require_profile(row.recipient_id, row.profile)
    active = [item.recipient_id for item in bindings if item.enabled]
    selected = next((item for item in bindings if item.recipient_id == row.recipient_id), None)
    if selected is None:
        selected = StaffChannel(staff_id=staff_id, platform="weixin", recipient_id=row.recipient_id, wechat_profile=row.profile)
        db.add(selected)
    elif not selected.enabled:
        selected.enabled = True
        selected.wechat_profile = row.profile
    elif selected.wechat_profile != row.profile:
        # An active account keeps its delivery window when only its bot route is verified.
        db.execute(update(StaffChannel).where(StaffChannel.id == selected.id)
                   .values(wechat_profile=row.profile, updated_at=StaffChannel.updated_at))
    retired = [item for item in bindings if item is not selected]
    if retired:
        db.execute(update(NotificationOutbox).where(NotificationOutbox.platform == "weixin",
                   NotificationOutbox.recipient_id.in_([item.recipient_id for item in retired]),
                   NotificationOutbox.status == "pending").values(status="cancelled", last_error="微信绑定已更换"))
        for item in retired:
            db.delete(item)
    if active != [row.recipient_id]:
        db.execute(delete(ChannelSubscription).where(ChannelSubscription.user_id == staff.user.id))
    row.confirmed_at = utcnow()
    row.recipient_id = row.profile = None
    commit_member(db)
    return pairing_status(row)


SCAN_STYLE = """*{box-sizing:border-box}body{margin:0;background:#f4f7f4;color:#222d27;font:16px/1.6 system-ui,sans-serif}
main{max-width:30rem;margin:8vh auto;padding:1.6rem}h1{margin:0;font-size:1.6rem}p{margin:.7rem 0 1rem}
textarea,button{width:100%;padding:.8rem;border-radius:.5rem;font:inherit}textarea{border:1px solid #cad7cf;background:#fff;resize:none}
button{border:0;background:#246b49;color:#fff;font-weight:600;margin-top:.6rem}button:disabled{opacity:.5}small{display:block;margin-top:.7rem}"""
SCAN_SCRIPT = """const code = location.hash.slice(1);
const command = document.querySelector('textarea');
const button = document.querySelector('button');
const status = document.querySelector('[role=status]');
if (/^[A-Za-z0-9_-]{32}$/.test(code)) {
  command.value = '绑定迈医 ' + code;
  button.disabled = false;
  history.replaceState(null, '', location.pathname);
  button.addEventListener('click', async () => {
    try {
      if (!navigator.clipboard) throw new Error();
      await navigator.clipboard.writeText(command.value);
      status.textContent = '已复制，请粘贴到 Hermes 微信私聊并发送。';
    } catch {
      command.focus(); command.select();
      status.textContent = '请长按选中的指令，选择复制。';
    }
  });
} else { status.textContent = '请重新扫描电脑端的绑定二维码。'; }"""


def csp_hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


@router.get(SCAN_PATH, response_class=HTMLResponse, include_in_schema=False)
def pairing_scan():
    scan_base()
    return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>迈医协同 · 微信绑定</title>
<style>{SCAN_STYLE}</style><main><h1>绑定迈医协同</h1><p>复制指令，发给你正在使用的 Hermes 微信机器人。发送后回到电脑确认。</p>
<textarea rows="2" readonly aria-label="一次性绑定指令"></textarea><button disabled>复制绑定指令</button>
<small role="status">指令 5 分钟内有效。</small></main><script>{SCAN_SCRIPT}</script></html>""", headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": f"default-src 'none'; script-src {csp_hash(SCAN_SCRIPT)}; style-src {csp_hash(SCAN_STYLE)}; frame-ancestors 'none'; base-uri 'none'",
    })
