"""Separate bounded notification/monitor worker. Nothing starts on module import."""
from datetime import timedelta
import hashlib
import time

from sqlalchemy import delete, func, select, update

from .config import get_settings
from .database import Base, engine, SessionLocal
from .knowledge_models import KnowledgeJob, KnowledgeNotification, KnowledgeOperationalAlert, KnowledgePreference, KnowledgeWorkerHeartbeat
from .knowledge_notifications import private_channel, recipient_hash, send_one, start_heartbeat
from .knowledge_service import new_id
from .models import Staff, UserAccount, utcnow


def observations(db):
    now = utcnow()
    hearts = db.scalars(select(KnowledgeWorkerHeartbeat).where(KnowledgeWorkerHeartbeat.role == "extract").order_by(KnowledgeWorkerHeartbeat.last_seen.desc()).limit(100)).all()
    recent = [row for row in hearts if row.last_seen > now - timedelta(seconds=45)]
    states = dict(db.execute(select(KnowledgeJob.state, func.count()).group_by(KnowledgeJob.state)).all())
    oldest = db.scalar(select(func.min(KnowledgeJob.created_at)).where(KnowledgeJob.state.in_(["queued", "retry_wait"])))
    alerts = db.scalars(select(KnowledgeOperationalAlert).where(KnowledgeOperationalAlert.active_key.is_not(None)).order_by(KnowledgeOperationalAlert.created_at).limit(20)).all()
    return dict(worker_health="recent_heartbeat" if recent else "stale_heartbeat" if hearts else "not_observed",
        recent_workers=len(recent), last_worker_seen=hearts[0].last_seen if hearts else None,
        job_states=states, oldest_wait_seconds=max(0, (now - oldest).total_seconds()) if oldest else 0,
        active_alerts=[dict(kind=row.kind, metrics=row.metrics, created_at=row.created_at) for row in alerts])


def monitor(factory=SessionLocal):
    with factory() as db:
        mutex = db.scalar(select(KnowledgeWorkerHeartbeat).where(KnowledgeWorkerHeartbeat.id == "monitor-lock").with_for_update())
        if not mutex: mutex = KnowledgeWorkerHeartbeat(id="monitor-lock", role="control", state="idle"); db.add(mutex); db.flush()
        mutex.last_seen = utcnow()
        value = observations(db)
        pending = sum(value["job_states"].get(state, 0) for state in ("queued", "running", "retry_wait"))
        conditions = dict(worker_missing=value["worker_health"] != "recent_heartbeat",
            queue_wait=value["oldest_wait_seconds"] > 900,
            queue_full=pending >= get_settings().knowledge_queue_limit)
        for kind, active in conditions.items():
            row = db.scalar(select(KnowledgeOperationalAlert).where(KnowledgeOperationalAlert.active_key == kind))
            if not active:
                if row: row.active_key = None; row.resolved_at = utcnow()
                continue
            metrics = dict(pending=pending, wait_seconds=int(value["oldest_wait_seconds"]), recent_workers=value["recent_workers"])
            if row: row.metrics = metrics; continue  # Same incident does not repeatedly notify.
            row = KnowledgeOperationalAlert(id=new_id(), kind=kind, active_key=kind, metrics=metrics); db.add(row)
            subscribers = db.execute(select(KnowledgePreference, UserAccount).join(UserAccount).join(Staff).where(
                KnowledgePreference.admin_alerts.is_(True), UserAccount.role == "admin", UserAccount.enabled.is_(True),
                Staff.enabled.is_(True), Staff.removed_at.is_(None)).limit(100)).all()
            for prefs, user in subscribers:
                if not (prefs.desktop_notifications or prefs.hermes_notifications): continue
                count = db.scalar(select(func.count()).select_from(KnowledgeNotification).where(KnowledgeNotification.owner_id == user.id))
                if count >= 1000: continue
                channel = private_channel(db, user, prefs.channel_id) if prefs.hermes_notifications else None
                identity = hashlib.sha256(f"{row.id}:{user.id}".encode()).hexdigest()
                db.add(KnowledgeNotification(id=identity, owner_id=user.id, kind="alert." + kind, delivery_id=identity,
                    channel_id=channel.id if channel else None, recipient_hash=recipient_hash(channel) if channel else "",
                    state="pending" if channel else "inbox"))
        db.execute(update(KnowledgeNotification).where(KnowledgeNotification.state == "sending",
            KnowledgeNotification.lease_until < utcnow()).values(state="unknown"))
        expired = db.scalars(select(KnowledgeNotification.id).where(KnowledgeNotification.created_at < utcnow() - timedelta(days=30)).limit(200)).all()
        if expired: db.execute(delete(KnowledgeNotification).where(KnowledgeNotification.id.in_(expired)))
        old_alerts = db.scalars(select(KnowledgeOperationalAlert.id).where(KnowledgeOperationalAlert.resolved_at < utcnow() - timedelta(days=30)).limit(100)).all()
        if old_alerts: db.execute(delete(KnowledgeOperationalAlert).where(KnowledgeOperationalAlert.id.in_(old_alerts)))
        db.commit()


def main():
    if not get_settings().knowledge_dispatch_enabled:
        print("knowledge_dispatch: disabled; no notifications or monitoring worker started", flush=True); return
    Base.metadata.create_all(engine)
    stopped, activity = start_heartbeat("dispatch")
    try:
        while True:
            try:
                activity["state"] = "busy"; monitor()
                for _ in range(10):
                    if not send_one(): break
                activity["state"] = "idle"
            except Exception:
                activity["state"] = "error"
                print("knowledge_dispatch: cycle failed; no document content or provider error logged", flush=True)
            time.sleep(15)
    finally: stopped.set()


if __name__ == "__main__": main()
