"""Prepared for joint acceptance; only a mocked third-party Hermes transport."""
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select
from app.config import get_settings
from app.knowledge_models import KnowledgeItem, KnowledgeNotification, KnowledgePreference, KnowledgeWorkerHeartbeat
from app.knowledge_notifications import queue_completion, send_one
from app.knowledge_dispatch import observations, monitor
from app.models import StaffChannel, utcnow
from test_knowledge import kb, collect

def configured(monkeypatch):
    s = get_settings(); monkeypatch.setattr(s, "knowledge_dispatch_enabled", True)
    monkeypatch.setattr(s, "knowledge_notification_webhook_base", "http://127.0.0.1:18644")
    monkeypatch.setattr(s, "knowledge_notification_webhook_secret", "synthetic-not-production")

def queued(kb, monkeypatch):
    client, users, factory, _ = kb; configured(monkeypatch)
    item = collect(client, users[0], title="通知不准带出的合成标题", content="通知不准带出的合成正文")
    with factory() as db:
        source = db.get(KnowledgeItem, item["id"])
        channel = db.scalar(select(StaffChannel).where(StaffChannel.recipient_id == "preview-sender-0"))
        db.add(KnowledgePreference(owner_id=source.owner_id, desktop_notifications=True, hermes_notifications=True, channel_id=channel.id)); db.flush()
        queue_completion(db, source, "e" * 32); db.commit()
    return item

@pytest.mark.parametrize("result, state", [("delivered", "sent"), ("duplicate", "unknown"), ("accepted", "unknown")])
def test_direct_delivery_requires_positive_ack_and_unknown_never_auto_retries(kb, monkeypatch, result, state):
    queued(kb, monkeypatch); client, users, factory, _ = kb; calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/webhooks/signaldesk-knowledge-weixin"
        assert "X-Request-ID" in request.headers and "X-Webhook-Signature-V2" in request.headers
        assert "合成标题" not in request.content.decode() and "合成正文" not in request.content.decode()
        return httpx.Response(200, json={"status": result, "delivery_id": request.headers["X-Request-ID"]})
    transport = httpx.MockTransport(handler)
    assert send_one(factory, transport=transport)
    assert not send_one(factory, transport=transport)
    assert len(calls) == 1
    with factory() as db: assert db.get(KnowledgeNotification, "e" * 32).state == state
    assert client.get("/api/v1/knowledge/notifications", headers=users[1]).json()["items"] == []
    assert client.post("/api/v1/knowledge/notifications/" + "e" * 32 + "/read", headers=users[2]).status_code == 404

def test_notification_read_cancel_and_source_revision_fences(kb, monkeypatch):
    item = queued(kb, monkeypatch); client, users, factory, _ = kb
    assert client.post("/api/v1/knowledge/notifications/" + "e" * 32 + "/read", headers=users[0]).status_code == 200
    calls = []
    assert not send_one(factory, transport=httpx.MockTransport(lambda req: calls.append(req)))
    assert not calls and client.get("/api/v1/knowledge/notifications", headers=users[0]).json()["items"] == []
    with factory() as db:
        row = db.get(KnowledgeNotification, "e" * 32); row.read_at = None; row.state = "pending"
        source = db.get(KnowledgeItem, item["id"]); source.revision += 1; db.commit()
    assert send_one(factory, transport=httpx.MockTransport(lambda req: calls.append(req)))
    assert not calls

def test_preferences_reject_peer_channel_and_nonadmin_alerts(kb, monkeypatch):
    client, users, factory, _ = kb; configured(monkeypatch)
    with factory() as db: peer = db.scalar(select(StaffChannel).where(StaffChannel.recipient_id == "preview-sender-1"))
    payload = dict(revision=0, desktop_notifications=True, hermes_notifications=True, admin_alerts=False, channel_id=peer.id)
    assert client.put("/api/v1/knowledge/preferences", headers=users[0], json=payload).status_code == 422
    payload.update(hermes_notifications=False, admin_alerts=True, channel_id=None)
    assert client.put("/api/v1/knowledge/preferences", headers=users[0], json=payload).status_code == 403

def test_actual_heartbeat_and_single_incident_alert(kb):
    _, _, factory, _ = kb
    with factory() as db: assert observations(db)["worker_health"] == "not_observed"
    monitor(factory); monitor(factory)
    with factory() as db:
        assert len(observations(db)["active_alerts"]) == 1
        db.add(KnowledgeWorkerHeartbeat(id="synthetic-worker", role="extract", state="idle", last_seen=utcnow())); db.commit()
    monitor(factory)
    with factory() as db:
        value = observations(db); assert value["worker_health"] == "recent_heartbeat" and not value["active_alerts"]
        row = db.get(KnowledgeWorkerHeartbeat, "synthetic-worker"); row.last_seen = utcnow() - timedelta(seconds=60); db.commit()
    with factory() as db: assert observations(db)["worker_health"] == "stale_heartbeat"
