from datetime import timedelta, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import articles, auth, channel_subscriptions
from app.channel_aliases import SmartChannelAlias
from app.database import Base, get_db
from app.channel_subscription_models import ChannelPushConfig, ChannelSubscription, ChannelWebsiteSnapshot
from app.models import Article, SmartChannel, Staff, StaffChannel, UserAccount, UserSession, utcnow


BASE = "/api/v1/channel-subscriptions"
SERVICE = {"X-SignalDesk-Key": "synthetic-channel-service"}
MEMBER = {"Authorization": "Bearer fixture-member"}
OTHER = {"Authorization": "Bearer fixture-other"}
ADMIN = {"Authorization": "Bearer fixture-admin"}


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(api_key=SERVICE["X-SignalDesk-Key"]))
    with factory() as db:
        db.add(SmartChannel(id="existing", name="频道文章", state="active", schedule="09:00"))
        for identifier, username in enumerate(("member", "other", "admin"), start=1):
            db.add(Staff(id=identifier, name=f"合成人员 {identifier}"))
            db.add(UserAccount(id=identifier, staff_id=identifier, username=username, password_hash="fixture",
                               role="admin" if username == "admin" else "member"))
            db.add(UserSession(user_id=identifier, token_hash=auth.token_digest(f"fixture-{username}"),
                               expires_at=utcnow() + timedelta(days=1)))
        db.add(StaffChannel(id=1, staff_id=1, platform="weixin", recipient_id="synthetic-peer-1"))
        db.add(StaffChannel(id=2, staff_id=2, platform="qq", recipient_id="synthetic-qq"))
        db.commit()

    def test_db():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(channel_subscriptions.router)
    app.include_router(articles.router)
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def test_opt_in_is_personal_idempotent_and_requires_wechat(api):
    client, factory = api
    path = f"{BASE}/existing/subscription"
    assert client.get(f"{BASE}/catalog", headers=SERVICE).status_code == 401
    initial = client.get(f"{BASE}/catalog", headers=MEMBER).json()
    assert initial["wechat_bound"] and not initial["channels"][0]["subscribed"]
    assert "synthetic-peer" not in str(initial)
    assert client.put(path, headers=OTHER).status_code == 409
    assert client.put(path, headers=MEMBER).json() == {"subscribed": True}
    with factory() as db:
        start = db.get(ChannelSubscription, ("existing", 1)).created_at
    assert client.put(path, headers=MEMBER).json() == {"subscribed": True}
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ChannelSubscription)) == 1
        assert db.get(ChannelSubscription, ("existing", 1)).created_at == start
    assert client.delete(path, headers=OTHER).json() == {"subscribed": False}
    assert client.get(f"{BASE}/catalog", headers=MEMBER).json()["channels"][0]["subscribed"]
    assert client.delete(path, headers=MEMBER).json() == {"subscribed": False}
    assert client.delete(path, headers=MEMBER).json() == {"subscribed": False}


def test_rebound_account_must_opt_in_again_even_if_binding_id_is_reused(api):
    client, factory = api
    path = f"{BASE}/existing/subscription"
    assert client.put(path, headers=MEMBER).status_code == 200
    with factory() as db:
        binding = db.get(StaffChannel, 1)
        binding.recipient_id = "synthetic-replacement"
        db.commit()
    assert not client.get(f"{BASE}/catalog", headers=MEMBER).json()["channels"][0]["subscribed"]
    assert client.put(path, headers=MEMBER).status_code == 200
    assert client.get(f"{BASE}/catalog", headers=MEMBER).json()["channels"][0]["subscribed"]


def test_only_admin_can_configure_channel_and_disabled_channel_cannot_be_subscribed(api):
    client, factory = api
    draft = {"name": "公告动态", "description": "只读公告区", "schedule": "09:30", "max_items": 10,
             "enabled": False, "source_type": "website", "source_url": "https://example.org/notices", "selector": "#notices a"}
    assert client.post(f"{BASE}/configs", headers=MEMBER, json=draft).status_code == 403
    invalid = client.post(f"{BASE}/configs", headers=ADMIN, json={**draft, "source_url": "http://127.0.0.1"})
    assert invalid.status_code == 422
    created = client.post(f"{BASE}/configs", headers=ADMIN, json=draft)
    assert created.status_code == 200, created.text
    channel_id = created.json()["id"]
    path = f"{BASE}/{channel_id}/subscription"
    assert client.put(path, headers=MEMBER).status_code == 409
    response = client.post(f"{BASE}/configs", headers=ADMIN, json={**draft, "id": channel_id, "enabled": True})
    assert response.json()["selector"] == "#notices a"
    assert client.put(path, headers=MEMBER).status_code == 200
    assert client.delete(f"/api/v1/channels/{channel_id}", headers=MEMBER).status_code == 403
    assert client.delete(f"/api/v1/channels/{channel_id}", headers=ADMIN).status_code == 409
    with factory() as db:
        assert db.get(SmartChannel, channel_id).topic == ""
        db.get(UserAccount, 1).allowed_pages = ["inbox"]
        db.commit()
    assert client.get(f"{BASE}/catalog", headers=MEMBER).status_code == 403


def test_client_articles_are_not_trusted_or_allowed_to_replace_trusted_content(api):
    client, factory = api
    first = {"id": "fixture-article", "channelId": "existing", "title": "可信上游文章", "body": "合成内容",
             "source": "测试", "timestamp": 1786400000123, "tags": [], "priority": 3}
    second = {**first, "id": "fixture-local", "title": "桌面导入内容"}
    assert client.post("/api/v1/articles/batch", headers=SERVICE, json={"items": [first]}).status_code == 200
    assert client.post("/api/v1/articles/batch", headers=MEMBER,
                       json={"items": [{**first, "title": "伪造的推送标题"}, second]}).status_code == 200
    with factory() as db:
        assert db.get(Article, first["id"]).channel_push_trusted
        assert db.get(Article, first["id"]).title == first["title"]
        assert not db.get(Article, second["id"]).channel_push_trusted


def test_legacy_member_cache_upload_allows_article_sync_without_editing_channels(api):
    client, factory = api
    cached = {"id": "existing", "name": "过期的本地频道名", "topic": "legacy-local", "state": "paused"}
    assert client.post("/api/v1/channels", headers=MEMBER, json=cached).json() == {"id": "existing"}
    assert client.post("/api/v1/channels", headers=MEMBER,
                       json={**cached, "id": "local-only"}).json() == {"id": "local-only"}
    assert client.get("/api/v1/channels", headers=MEMBER).status_code == 200
    with factory() as db:
        assert db.get(SmartChannel, "existing").name == "频道文章"
        assert db.get(SmartChannel, "existing").state == "active"
        assert db.get(SmartChannel, "local-only") is None


def test_admin_pause_and_delete_stop_collection_preserving_history(api):
    from app.channel_push_worker import run_cycle
    from app.channel_website import WebsiteItem

    client, factory = api
    draft = {"name": "管理测试公告", "schedule": "00:00", "max_items": 10, "source_type": "website",
             "source_url": "https://example.org/notices", "selector": "#notices a"}
    created = client.post(f"{BASE}/configs", headers=ADMIN, json=draft)
    assert created.status_code == 200 and created.json()["can_delete"]
    channel_id = created.json()["id"]
    settings = f"{BASE}/configs/{channel_id}"
    opt_in = f"{BASE}/{channel_id}/subscription"
    assert client.put(opt_in, headers=MEMBER).status_code == 200
    assert client.put(f"{settings}/enabled", headers=MEMBER, json={"enabled": False}).status_code == 403
    assert client.delete(settings, headers=MEMBER).status_code == 403
    assert client.put(f"{settings}/enabled", headers=ADMIN, json={"enabled": "false"}).status_code == 422
    assert not client.put(f"{settings}/enabled", headers=ADMIN, json={"enabled": False}).json()["enabled"]

    calls = []
    def fetch(url, selector, limit):
        calls.append((url, selector, limit))
        return [WebsiteItem("公告正文", "合成原文", "https://example.org/notices/1")]

    now = utcnow()
    run_cycle(factory, now=now, fetch=fetch)
    assert calls == []
    member_row = next(row for row in client.get(f"{BASE}/catalog", headers=MEMBER).json()["channels"]
                      if row["id"] == channel_id)
    assert member_row["subscribed"] and not member_row["enabled"] and not member_row["can_delete"]
    assert client.put(f"{settings}/enabled", headers=ADMIN, json={"enabled": True}).json()["enabled"]
    run_cycle(factory, now=now, fetch=fetch)
    assert calls == [(draft["source_url"], draft["selector"], 10)]

    assert client.delete(settings, headers=ADMIN).status_code == 204
    assert client.delete(settings, headers=ADMIN).status_code == 204
    assert client.post(f"{BASE}/configs", headers=ADMIN, json={**draft, "id": channel_id}).status_code == 404
    assert client.put(f"{settings}/enabled", headers=ADMIN, json={"enabled": True}).status_code == 404
    assert client.put(opt_in, headers=MEMBER).status_code == 404
    cached = {"id": channel_id, "name": "旧版频道缓存", "topic": "old-topic", "state": "active"}
    assert client.post("/api/v1/channels", headers=ADMIN, json=cached).json() == {"id": channel_id}
    assert channel_id not in [row["id"] for row in client.get("/api/v1/channels", headers=ADMIN).json()]
    catalog = client.get(f"{BASE}/catalog", headers=MEMBER).json()
    assert catalog["management_supported"] and catalog["archived_ids"] == [channel_id]
    assert channel_id not in [row["id"] for row in catalog["channels"]]
    run_cycle(factory, now=now + timedelta(days=1), fetch=fetch)
    assert len(calls) == 1
    with factory() as db:
        assert db.get(SmartChannel, channel_id).archived_at
        assert not db.get(ChannelPushConfig, channel_id).enabled
        assert db.get(ChannelSubscription, (channel_id, 1))
        assert db.scalar(select(func.count()).select_from(Article).where(Article.channel_id == channel_id)) == 1
        assert db.scalar(select(func.count()).select_from(ChannelWebsiteSnapshot)
                         .where(ChannelWebsiteSnapshot.channel_id == channel_id)) == 1


def test_shared_order_rejects_duplicate_and_stale_lists_and_protects_managed_channels(api):
    client, factory = api
    with factory() as db:
        db.add(SmartChannel(id="managed", name="B", state="active", schedule="09:00", topic="server-relay"))
        db.add(SmartChannelAlias(alias_id="old-managed", canonical_id="managed"))
        db.commit()
    path = f"{BASE}/order"
    ids = [row["id"] for row in client.get(f"{BASE}/catalog", headers=ADMIN).json()["channels"]]
    swapped = ids[::-1]
    assert client.put(path, headers=MEMBER, json={"previous_ids": ids, "channel_ids": swapped}).status_code == 403
    assert client.put(path, headers=ADMIN, json={"previous_ids": ids, "channel_ids": ids[:1] * 2}).status_code == 422
    assert client.put(path, headers=ADMIN, json={"previous_ids": ids, "channel_ids": swapped}).json() == {
        "channel_ids": swapped,
    }
    assert client.put(path, headers=ADMIN, json={"previous_ids": ids, "channel_ids": swapped}).status_code == 409
    assert [row["id"] for row in client.get(f"{BASE}/catalog", headers=MEMBER).json()["channels"]] == swapped
    assert [row["id"] for row in client.get("/api/v1/channels", headers=ADMIN).json()] == swapped
    managed = f"{BASE}/configs/managed"
    assert client.delete(managed, headers=ADMIN).status_code == 409
    assert not client.put(f"{managed}/enabled", headers=ADMIN, json={"enabled": False}).json()["enabled"]
    with factory() as db:
        assert db.get(SmartChannel, "managed").state == "active"
        assert db.get(SmartChannel, "managed").topic == "server-relay"
        assert db.get(SmartChannel, "managed").archived_at is None


def test_admin_can_limit_subscriptions_and_revocation_requires_fresh_opt_in(api):
    from app.channel_push_worker import run_cycle

    client, factory = api
    audience = f"{BASE}/configs/existing/audience"
    opt_in = f"{BASE}/existing/subscription"
    with factory() as db:
        db.get(SmartChannel, "existing").schedule = "00:00"
        db.add(StaffChannel(staff_id=2, platform="weixin", recipient_id="synthetic-peer-2"))
        db.commit()

    def save(previous, allowed, headers=ADMIN):
        return client.put(audience, headers=headers, json={"previous_user_ids": previous, "allowed_user_ids": allowed})

    assert client.get(audience, headers=MEMBER).status_code == 403
    assert client.get(audience, headers=SERVICE).status_code == 401
    assert save(None, [1], MEMBER).status_code == 403
    initial = client.get(audience, headers=ADMIN).json()
    assert initial["allowed_user_ids"] is None
    assert {member["id"] for member in initial["members"]} == {1, 2, 3}
    assert client.put(opt_in, headers=MEMBER).status_code == 200
    assert client.put(opt_in, headers=OTHER).status_code == 200
    assert save(None, [999]).status_code == 422
    assert save(None, [2**80]).status_code == 422
    assert save(None, [2]).status_code == 200
    assert save(None, [2]).status_code == 200
    assert save(None, [1]).status_code == 409
    assert client.put(opt_in, headers=MEMBER).status_code == 403
    catalog = client.get(f"{BASE}/catalog", headers=MEMBER).json()
    assert not catalog["channels"][0]["subscription_allowed"]
    assert not catalog["channels"][0]["subscribed"]
    assert "allowed_user_ids" not in str(catalog)
    assert "synthetic-peer" not in str(catalog)
    assert client.get(f"{BASE}/catalog", headers=OTHER).json()["channels"][0]["subscribed"]

    now = utcnow() + timedelta(seconds=1)
    article = {"id": "audience-digest", "channelId": "existing", "title": "测试推送", "body": "合成内容",
               "source": "测试", "timestamp": int(now.replace(tzinfo=timezone.utc).timestamp() * 1000),
               "tags": [], "priority": 3}
    assert client.post("/api/v1/articles/batch", headers=SERVICE, json={"items": [article]}).status_code == 200
    recipients = []
    def send(recipient, *_):
        recipients.append(recipient)
        return "sent"
    assert run_cycle(factory, now=now, send=send) == 1
    assert recipients == ["synthetic-peer-2"]

    assert save([2], []).status_code == 200
    assert client.put(opt_in, headers=OTHER).status_code == 403
    assert save([], None).status_code == 200
    for headers in (MEMBER, OTHER):
        row = client.get(f"{BASE}/catalog", headers=headers).json()["channels"][0]
        assert row["subscription_allowed"] and not row["subscribed"]
    assert client.put(opt_in, headers=MEMBER).status_code == 200


def test_queued_dispatch_rechecks_the_current_audience(api):
    from app.channel_push_worker import BEIJING, read_rule, send_digest
    from app.channel_subscription_models import ChannelPushDelivery

    client, factory = api
    assert client.put(f"{BASE}/existing/subscription", headers=MEMBER).status_code == 200
    with factory() as db:
        cached_rule = read_rule(db, "existing")
        # Simulate an opt-in retained by an older process while the policy changed.
        db.get(SmartChannel, "existing").subscription_user_ids = []
        db.commit()
    now = utcnow() + timedelta(seconds=1)
    article = {"id": "queued-audience-digest", "channelId": "existing", "title": "测试待发送",
               "body": "合成内容", "source": "测试",
               "timestamp": int(now.replace(tzinfo=timezone.utc).timestamp() * 1000), "tags": [], "priority": 3}
    assert client.post("/api/v1/articles/batch", headers=SERVICE, json={"items": [article]}).status_code == 200

    def forbidden_send(*_):
        pytest.fail("Stale audience allowed an excluded recipient")

    day = now.replace(tzinfo=timezone.utc).astimezone(BEIJING).date()
    assert send_digest(factory, cached_rule, 1, day, now, lambda _: forbidden_send)
    with factory() as db:
        assert db.scalar(select(ChannelPushDelivery.status)) == "cancelled"


def test_api_opt_in_to_signed_delivery_excludes_other_user_and_deduplicates(api):
    from app.channel_push_worker import HermesWechatSender, run_cycle

    client, factory = api
    path = f"{BASE}/existing/subscription"
    with factory() as db:
        db.get(SmartChannel, "existing").schedule = "00:00"
        db.add(StaffChannel(staff_id=2, platform="weixin", recipient_id="synthetic-peer-2"))
        db.commit()
    assert client.put(path, headers=MEMBER).status_code == 200
    publish_time = utcnow() + timedelta(seconds=2)
    article = {"id": "synthetic-digest-article", "channelId": "existing", "title": "订阅后的新公告",
               "body": "合成摘要", "source": "测试", "clickUrl": "https://example.org/notices/42",
               "timestamp": int(publish_time.replace(tzinfo=timezone.utc).timestamp() * 1000),
               "tags": [], "priority": 3}
    assert client.post("/api/v1/articles/batch", headers=SERVICE, json={"items": [article]}).status_code == 200
    requests = []

    def webhook(request):
        requests.append(json.loads(request.content))
        assert request.headers.get("X-Webhook-Signature-V2")
        return httpx.Response(200, json={"status": "delivered", "route": "signaldesk-channel-weixin",
                                         "target": "weixin", "delivery_id": request.headers["X-Request-ID"]})

    send = HermesWechatSender("https://gateway.example.org/p/fixture/webhooks/signaldesk-channel-weixin",
                              "synthetic-webhook-secret", transport=httpx.MockTransport(webhook))
    assert run_cycle(factory, now=publish_time, send=send) == 1
    assert run_cycle(factory, now=publish_time + timedelta(seconds=1), send=send) == 0
    assert [request["recipient_id"] for request in requests] == ["synthetic-peer-1"]
    assert "订阅后的新公告" in requests[0]["message"]
