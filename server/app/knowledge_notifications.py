"""Private completion inbox and opt-in direct Hermes delivery, never an LLM turn."""
from datetime import timedelta
import hashlib
import hmac
import json
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.orm import Session

from .auth import require_user
from .config import get_settings
from .database import get_db, SessionLocal
from .knowledge_models import KnowledgeItem, KnowledgeNotification, KnowledgePreference, KnowledgeWorkerHeartbeat
from .knowledge_service import new_id
from .knowledge_usage import lock_paid_owner
from .models import StaffChannel, UserAccount, utcnow

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-notifications"])
FINISHED = {"ready": "处理结束，正文已入库", "needs_review": "识别结束，请核对后入库", "failed": "处理失败，请查看原件与处理状态", "needs_input": "需要补充素材，请查看处理状态"}
ALERTS = {"worker_missing": "近期没有观察到识别进程心跳", "queue_wait": "知识库队列等待超过阈值", "queue_full": "知识库队列达到容量阈值"}


def configuration():
    settings = get_settings(); url = urlsplit(settings.knowledge_notification_webhook_base.strip())
    return bool(settings.knowledge_dispatch_enabled and settings.knowledge_notification_webhook_secret.strip()
        and url.hostname and not url.username and not url.password and not url.query and not url.fragment
        and (url.scheme == "https" or url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}))


def recipient_hash(channel):
    return hashlib.sha256(f"{channel.platform}\n{channel.recipient_id}".encode()).hexdigest()


def preference_read(row):
    return dict(revision=row.revision if row else 0, desktop_notifications=row.desktop_notifications if row else False,
        hermes_notifications=row.hermes_notifications if row else False, admin_alerts=row.admin_alerts if row else False,
        channel_id=row.channel_id if row else None, hermes_ready=configuration())


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    desktop_notifications: StrictBool
    hermes_notifications: StrictBool
    admin_alerts: StrictBool = False
    channel_id: int | None = Field(default=None, ge=1)


def private_channel(db, user, channel_id):
    return db.scalar(select(StaffChannel).where(StaffChannel.id == channel_id, StaffChannel.staff_id == user.staff_id,
        StaffChannel.enabled.is_(True), StaffChannel.platform.in_(["qq", "weixin", "wecom"])))


@router.get("/preferences")
def read_preferences(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    result = preference_read(db.get(KnowledgePreference, user.id))
    channels = db.scalars(select(StaffChannel).where(StaffChannel.staff_id == user.staff_id, StaffChannel.enabled.is_(True))).all()
    result["channels"] = [dict(id=channel.id, platform=channel.platform) for channel in channels if channel.platform in {"qq", "weixin", "wecom"}]
    return result


@router.put("/preferences")
def save_preferences(payload: PreferenceUpdate, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    user = lock_paid_owner(db, user)
    row = db.get(KnowledgePreference, user.id)
    if (row.revision if row else 0) != payload.revision: raise HTTPException(409, "通知设置已改变，请刷新后重试")
    if payload.hermes_notifications and (not configuration() or not private_channel(db, user, payload.channel_id)):
        raise HTTPException(422, "后台尚未启用通知投递或所选渠道不属于本人 / 已停用")
    if payload.hermes_notifications and not private_channel(db, user, payload.channel_id).recipient_id.strip():
        raise HTTPException(422, "所选渠道缺少本人私聊接收账号，请先修正绑定")
    if payload.admin_alerts and user.role != "admin": raise HTTPException(403, "只有平台管理员可以订阅后台告警")
    if not row: row = KnowledgePreference(owner_id=user.id, revision=0); db.add(row)
    row.revision += 1; row.desktop_notifications = payload.desktop_notifications
    row.hermes_notifications = payload.hermes_notifications; row.admin_alerts = payload.admin_alerts
    row.channel_id = payload.channel_id if payload.hermes_notifications else None
    if not row.hermes_notifications:
        db.execute(update(KnowledgeNotification).where(KnowledgeNotification.owner_id == user.id,
            KnowledgeNotification.state == "pending").values(state="cancelled"))
    db.commit(); return preference_read(row)


def message(row):
    if row.kind.startswith("alert."):
        return "知识库后台提示：" + ALERTS.get(row.kind[6:], "请查看队列与运行状态") + "。请在客户端管理员队列概况中核查。"
    return f"你的知识资料{FINISHED.get(row.kind, '处理状态已改变')}。资料编号：{row.item_id}。请在客户端查看；通知不代表全文识别准确。"


def admitted(db, row):
    if row.item_id:
        item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == row.item_id, KnowledgeItem.owner_id == row.owner_id,
            KnowledgeItem.deleted_at.is_(None), KnowledgeItem.revision == row.source_revision))
        return bool(item and item.status == row.kind)
    return row.kind.startswith("alert.")


@router.get("/notifications")
def notifications(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    source_valid = exists(select(KnowledgeItem.id).where(KnowledgeItem.id == KnowledgeNotification.item_id,
        KnowledgeItem.owner_id == user.id, KnowledgeItem.deleted_at.is_(None),
        KnowledgeItem.revision == KnowledgeNotification.source_revision, KnowledgeItem.status == KnowledgeNotification.kind))
    query = select(KnowledgeNotification).where(KnowledgeNotification.owner_id == user.id,
        KnowledgeNotification.read_at.is_(None), KnowledgeNotification.created_at > utcnow() - timedelta(days=30),
        or_(source_valid, KnowledgeNotification.item_id.is_(None) & KnowledgeNotification.kind.startswith("alert.") & (user.role == "admin")))
    rows = db.scalars(query.order_by(KnowledgeNotification.created_at.desc()).limit(50)).all()
    return dict(items=[dict(id=row.id, item_id=row.item_id, revision=row.source_revision, message=message(row),
        kind=row.kind, delivery_state=row.state, created_at=row.created_at) for row in rows if admitted(db, row)][:50])


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    row = db.scalar(select(KnowledgeNotification).where(KnowledgeNotification.id == notification_id, KnowledgeNotification.owner_id == user.id).with_for_update())
    if not row: raise HTTPException(404, "通知不存在或无权访问")
    row.read_at = utcnow()
    if row.state == "pending": row.state = "cancelled"
    db.commit(); return dict(read=True)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_duplicate_risk: StrictBool


@router.post("/notifications/{notification_id}/retry")
def retry_notification(notification_id: str, payload: RetryRequest,
                        user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    if not payload.acknowledge_duplicate_risk: raise HTTPException(422, "重发前需明确同意可能出现重复通知")
    row = db.scalar(select(KnowledgeNotification).where(KnowledgeNotification.id == notification_id, KnowledgeNotification.owner_id == user.id).with_for_update())
    if not row or row.read_at or not admitted(db, row): raise HTTPException(404, "通知不存在、已读或来源已经失效")
    if row.state not in {"unknown", "failed"} or row.attempts >= 3: raise HTTPException(409, "当前通知不支持重发，或已达到 3 次尝试上限")
    row.delivery_id = new_id(); row.state = "pending"; row.available_at = utcnow(); db.commit(); return dict(queued=True)


def queue_completion(db, item, job_id):
    prefs = db.get(KnowledgePreference, item.owner_id)
    if not prefs or not (prefs.desktop_notifications or prefs.hermes_notifications) or item.status not in FINISHED: return
    if db.get(KnowledgeNotification, job_id): return
    count = db.scalar(select(func.count()).select_from(KnowledgeNotification).where(KnowledgeNotification.owner_id == item.owner_id))
    if count >= 1000:
        item.status_note += " 本人通知记录达到容量上限，本次未生成新通知；请到资料列表查看。"
        return
    user = db.get(UserAccount, item.owner_id)
    channel = private_channel(db, user, prefs.channel_id) if user and prefs.hermes_notifications else None
    db.add(KnowledgeNotification(id=job_id, owner_id=item.owner_id, item_id=item.id, source_revision=item.revision,
        kind=item.status, channel_id=channel.id if channel else None, recipient_hash=recipient_hash(channel) if channel else "",
        delivery_id=job_id, state="pending" if channel else "inbox"))


def delivery_target(db, row):
    if row.read_at: return None
    user = db.get(UserAccount, row.owner_id); prefs = db.get(KnowledgePreference, row.owner_id)
    if not user or not user.enabled or not user.staff.enabled or user.staff.removed_at or not prefs or not prefs.hermes_notifications:
        return None
    if row.kind.startswith("alert.") and (user.role != "admin" or not prefs.admin_alerts): return None
    channel = private_channel(db, user, row.channel_id)
    if not channel or prefs.channel_id != channel.id or recipient_hash(channel) != row.recipient_hash or not admitted(db, row): return None
    return channel


def send_one(factory=SessionLocal, *, transport=None):
    if not configuration(): return False
    with factory() as db:
        db.execute(update(KnowledgeNotification).where(KnowledgeNotification.state == "sending", KnowledgeNotification.lease_until < utcnow()).values(state="unknown"))
        row = db.scalar(select(KnowledgeNotification).where(KnowledgeNotification.state == "pending", KnowledgeNotification.available_at <= utcnow())
            .order_by(KnowledgeNotification.created_at).with_for_update(skip_locked=True).limit(1))
        if not row: db.commit(); return False
        channel = delivery_target(db, row)
        if not channel: row.state = "cancelled"; db.commit(); return True
        payload = dict(event_type="knowledge.alert" if row.kind.startswith("alert.") else "knowledge.finished",
            recipient_id=channel.recipient_id, message=message(row))
        notification_id, delivery_id = row.id, row.delivery_id
        platform = channel.platform; row.state = "sending"; row.attempts += 1; row.lease_until = utcnow() + timedelta(seconds=60)
        db.commit()
    settings = get_settings(); body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    with factory() as db:
        current = db.get(KnowledgeNotification, notification_id)
        if not current or current.state != "sending" or current.delivery_id != delivery_id or not delivery_target(db, current):
            if current and current.state == "sending": current.state = "cancelled"; db.commit()
            return True
    stamp = str(int(time.time())); signature = hmac.new(settings.knowledge_notification_webhook_secret.strip().encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    state = "unknown"
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False, follow_redirects=False, transport=transport) as client:
            with client.stream("POST", settings.knowledge_notification_webhook_base.rstrip("/") + "/webhooks/signaldesk-knowledge-" + platform,
                content=body, headers={"Content-Type": "application/json", "X-Webhook-Timestamp": stamp,
                    "X-Webhook-Signature-V2": signature, "X-Request-ID": delivery_id}) as response:
                response_body = bytearray(); deadline = time.monotonic() + 20
                for chunk in response.iter_bytes():
                    response_body.extend(chunk)
                    if len(response_body) > 16000 or time.monotonic() > deadline: raise ValueError()
                result = json.loads(response_body)
                if not isinstance(result, dict): raise ValueError()
                if response.status_code == 200 and result.get("status") == "delivered" and result.get("delivery_id") == delivery_id: state = "sent"
                elif response.status_code == 429: state = "pending"
                elif response.status_code in {400, 401, 403, 404, 413}: state = "failed"
                # duplicate / accepted / 5xx are not proof of delivery; never auto-resend.
    except httpx.ConnectError: state = "pending"  # No HTTP connection was established.
    except (httpx.HTTPError, ValueError, TypeError): pass
    with factory() as db:
        row = db.get(KnowledgeNotification, notification_id)
        if row and row.state == "sending" and row.delivery_id == delivery_id:
            row.state = "failed" if state == "pending" and row.attempts >= 3 else state
            row.available_at = utcnow() + timedelta(seconds=30 * 2 ** row.attempts); row.lease_until = None; db.commit()
    return True


def start_heartbeat(role, factory=SessionLocal):
    identity = new_id(); stopped = threading.Event(); activity = dict(state="idle")
    def update_heartbeat():
        with factory() as db:
            row = db.get(KnowledgeWorkerHeartbeat, identity)
            if not row: row = KnowledgeWorkerHeartbeat(id=identity, role=role); db.add(row)
            row.state = activity["state"]; row.last_seen = utcnow()
            db.execute(delete(KnowledgeWorkerHeartbeat).where(KnowledgeWorkerHeartbeat.last_seen < utcnow() - timedelta(days=2)))
            db.commit()
    def pulse():
        while not stopped.is_set():
            try: update_heartbeat()
            except Exception: pass  # The monitor sees missing heartbeat; no secrets logged.
            stopped.wait(10)
    thread = threading.Thread(target=pulse, daemon=True, name="knowledge-heartbeat"); thread.start()
    return stopped, activity
