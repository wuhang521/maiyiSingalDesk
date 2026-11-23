from datetime import timedelta
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth, channel_subscriptions, wechat_pairing
from app.channel_push_worker import profile_wechat_senders
from app.channel_subscription_models import ChannelSubscription
from app.database import Base, get_db
from app.models import NotificationOutbox, SmartChannel, Staff, StaffChannel, UserAccount, UserSession, WechatPairing, utcnow


ADMIN = {"Authorization": "Bearer admin-a"}
OTHER_ADMIN = {"Authorization": "Bearer admin-b"}
MEMBER = {"Authorization": "Bearer member"}
SERVICE = {"X-SignalDesk-Key": "isolated-test-key"}
OLD_PEER = "old-test-peer@im.wechat"
NEW_PEER = "new-test-peer@im.wechat"
PAIRING = "/api/v1/admin/members/3/wechat-pairing"
CLAIM = "/api/v1/wechat-pairing/claim"
SUBSCRIBE = "/api/v1/channel-subscriptions/medical/subscription"


@pytest.fixture
def api(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"bindings": {auth.token_digest(OLD_PEER): "default"}, "routes": {
        "default": {"url": "http://127.0.0.1:9876/p/default/webhooks/signaldesk-channel-weixin-default", "secret": "test-default"},
        "clinic": {"url": "http://127.0.0.1:9876/p/clinic/webhooks/signaldesk-channel-weixin-clinic", "secret": "test-clinic"},
    }}), encoding="utf-8")
    settings = SimpleNamespace(api_key=SERVICE["X-SignalDesk-Key"], channel_wechat_routes_file=str(routes),
                               wechat_pairing_public_base_url="https://pairing.example.test/service")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(wechat_pairing, "get_settings", lambda: settings)
    with factory.begin() as db:
        for identifier, name in enumerate(("admin-a", "admin-b", "member"), 1):
            db.add(Staff(id=identifier, name=name))
            db.add(UserAccount(id=identifier, staff_id=identifier, username=name, password_hash="test",
                               role="member" if identifier == 3 else "admin"))
            db.add(UserSession(user_id=identifier, token_hash=auth.token_digest(name), expires_at=utcnow() + timedelta(days=1)))
        db.add(StaffChannel(id=1, staff_id=3, platform="weixin", recipient_id=OLD_PEER))
        db.add(StaffChannel(id=2, staff_id=2, platform="weixin", recipient_id="another-test-peer@im.wechat"))
        db.add(SmartChannel(id="medical", name="测试频道", state="active", schedule="09:00"))
        db.add(NotificationOutbox(id=1, platform="weixin", recipient_id=OLD_PEER, dedupe_key="test", payload="{}"))

    def test_db():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(wechat_pairing.router)
    app.include_router(channel_subscriptions.router)
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        yield client, factory, settings
    engine.dispose()


def start(client):
    response = client.post(PAIRING, headers=ADMIN)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    scan = urlsplit(result["scan_url"])
    assert scan.path == "/service/api/v1/wechat-pairing/scan" and not scan.query
    assert result["state"] == "waiting"
    return f"{PAIRING}/{result['id']}", scan.fragment


def claim(client, code, peer=NEW_PEER, profile="clinic", headers=SERVICE):
    return client.post(CLAIM, headers=headers, json={"code": code, "recipient_id": peer, "profile": profile})


def test_claim_requires_robot_and_binding_changes_only_on_admin_confirmation(api):
    client, factory, settings = api
    assert client.post(PAIRING, headers=MEMBER).status_code == 403
    assert client.post(PAIRING, headers=SERVICE).status_code == 401
    assert client.put(SUBSCRIBE, headers=MEMBER).status_code == 200
    path, code = start(client)
    with factory() as db:
        assert db.get(WechatPairing, 3).code_hash == auth.token_digest(code)
    assert client.get(path, headers=OTHER_ADMIN).status_code == 404
    assert client.post(f"{path}/confirm", headers=ADMIN).status_code == 409
    assert claim(client, code, headers=ADMIN).status_code == 401
    assert claim(client, code, "test@chatroom").status_code == 422
    assert claim(client, code, profile="unconfigured").status_code == 409
    assert claim(client, code, "another-test-peer@im.wechat").status_code == 409
    assert claim(client, code).json() == {"state": "ready"}
    assert claim(client, code).status_code == 200
    assert claim(client, code, OLD_PEER).status_code == 409
    assert client.post(f"{path}/confirm", headers=OTHER_ADMIN).status_code == 404
    status = client.get(path, headers=ADMIN).json()
    assert status["state"] == "ready" and status["replacing"]
    assert status["recipient_hint"] == "new…eer@im.wechat" and code not in str(status)
    with factory() as db:
        assert db.get(StaffChannel, 1).recipient_id == OLD_PEER
        assert db.get(ChannelSubscription, ("medical", 3)) is not None
    assert client.post(f"{path}/confirm", headers=ADMIN).json()["state"] == "confirmed"
    assert client.post(f"{path}/confirm", headers=ADMIN).status_code == 200
    assert claim(client, code).status_code == 409
    with factory() as db:
        binding = channel_subscriptions.active_wechat_binding(db, 3)
        assert (binding.recipient_id, binding.wechat_profile) == (NEW_PEER, "clinic")
        assert db.get(StaffChannel, 1) is None
        assert db.get(ChannelSubscription, ("medical", 3)) is None
        assert db.get(NotificationOutbox, 1).status == "cancelled"
        sender = profile_wechat_senders(settings.channel_wechat_routes_file)
        assert sender(binding.recipient_id, binding.wechat_profile).route.endswith("-clinic")
        assert sender(OLD_PEER).route.endswith("-default")
    assert client.put(SUBSCRIBE, headers=MEMBER).status_code == 200


def test_same_wechat_keeps_subscription(api):
    client, factory, _ = api
    assert client.put(SUBSCRIBE, headers=MEMBER).status_code == 200
    with factory() as db:
        before = db.get(StaffChannel, 1).updated_at
    path, code = start(client)
    assert claim(client, code, OLD_PEER).status_code == 200
    assert client.get(path, headers=ADMIN).json()["replacing"] is False
    assert client.post(f"{path}/confirm", headers=ADMIN).status_code == 200
    with factory() as db:
        binding = db.get(StaffChannel, 1)
        subscription = db.get(ChannelSubscription, ("medical", 3))
        assert binding.wechat_profile == "clinic"
        assert binding.updated_at == before
        assert channel_subscriptions.subscription_matches(subscription, binding)
        assert db.get(NotificationOutbox, 1).status == "pending"


def test_regeneration_cancel_expiry_and_concurrent_binding_change(api):
    client, factory, _ = api
    old_path, old_code = start(client)
    path, code = start(client)
    assert client.get(old_path, headers=ADMIN).status_code == 404
    assert claim(client, old_code).status_code == 409
    assert client.delete(path, headers=ADMIN).json()["state"] == "expired"
    assert claim(client, code).status_code == 409
    path, code = start(client)
    with factory.begin() as db:
        db.get(WechatPairing, 3).expires_at = utcnow() - timedelta(seconds=1)
    assert client.get(path, headers=ADMIN).json()["state"] == "expired"
    assert claim(client, code).status_code == 409
    path, code = start(client)
    assert claim(client, code).status_code == 200
    with factory.begin() as db:
        db.get(StaffChannel, 1).enabled = False
    assert client.post(f"{path}/confirm", headers=ADMIN).status_code == 409
    with factory() as db:
        assert db.get(StaffChannel, 1).recipient_id == OLD_PEER


def test_scan_page_no_cache_and_feature_disabled_without_base(api):
    client, _, settings = api
    scan = client.get("/api/v1/wechat-pairing/scan")
    assert scan.status_code == 200 and scan.headers["cache-control"] == "no-store"
    assert scan.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in scan.headers["content-security-policy"]
    assert "复制绑定指令" in scan.text
    settings.wechat_pairing_public_base_url = "http://insecure.example.test"
    assert client.post(PAIRING, headers=ADMIN).status_code == 503
