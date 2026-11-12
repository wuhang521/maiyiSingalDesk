from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app import channel_push_worker as worker
from app.channel_subscription_models import ChannelPushConfig, ChannelPushDelivery, ChannelSubscription, ChannelWebsiteSnapshot
from app.channel_subscriptions import binding_fingerprint
from app.channel_website import WebsiteItem
from app.database import Base
from app.models import Article, SmartChannel, Staff, StaffChannel, UserAccount


DAY = datetime(2026, 11, 11, 1)
ENROLLED = DAY - timedelta(minutes=5)
BOUND = ENROLLED - timedelta(hours=1)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "channel-push.sqlite"), connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        db.add(SmartChannel(id="ai", name="AI 决策雷达", state="active", schedule="09:00", max_items=2))
    yield factory
    engine.dispose()


def add_person(db, id, *, subscribe=True, binding=True, platform="weixin", opted_at=ENROLLED, **values):
    staff = Staff(id=id, name=f"Member {id}", enabled=values.pop("staff_enabled", True),
                  removed_at=values.pop("removed_at", None))
    user = UserAccount(id=id, staff_id=id, username=f"member{id}", password_hash="fixture", **values)
    db.add_all((staff, user))
    if binding:
        route = StaffChannel(id=id, staff_id=id, platform=platform, recipient_id=f"wx-{id}@im.wechat",
                             created_at=BOUND, updated_at=BOUND)
        db.add(route)
    if subscribe:
        db.add(ChannelSubscription(channel_id="ai", user_id=id, binding_id=id,
            binding_fingerprint=binding_fingerprint(route) if binding else "missing", created_at=opted_at))


def add_article(db, article_id, title, published_at=DAY, trusted=True, body="Synthetic text", link="https://publisher.example/item"):
    db.add(Article(id=article_id, channel_id="ai", title=title, body=body, click_url=link,
                   published_at=published_at, content_hash=article_id, channel_push_trusted=trusted))


def add_website_config(db, **changes):
    db.add(ChannelPushConfig(channel_id="ai", **{
        "name": "测试网页", "enabled": True, "schedule": "09:00", "max_items": 3,
        "source_type": "website", "source_url": "https://news.example/", "selector": "main article",
        "updated_at": BOUND, **changes,
    }))


def test_existing_channel_filters_content_and_only_sends_to_current_opted_in_person(factory):
    with factory.begin() as db:
        add_person(db, 1)
        add_person(db, 2, subscribe=False)
        add_person(db, 3, binding=False)
        add_person(db, 4, platform="qq")
        add_person(db, 5, enabled=False)
        add_person(db, 6, staff_enabled=False)
        add_person(db, 7, allowed_pages=["tasks"])
        add_person(db, 8, removed_at=DAY)
        add_person(db, 9, opted_at=DAY + timedelta(seconds=1))
        add_person(db, 10)
        db.add(StaffChannel(id=101, staff_id=10, platform="weixin", recipient_id="extra@im.wechat"))
        add_article(db, "old", "不可补发", published_at=ENROLLED - timedelta(seconds=1))
        add_article(db, "forged", "用户伪造内容", trusted=False)
        add_article(db, "valid", "新的可信文章")
    calls = []
    send = lambda *args: calls.append(args) or "sent"
    assert worker.run_cycle(factory, now=DAY - timedelta(seconds=1), send=send) == 0
    assert worker.run_cycle(factory, now=DAY, send=send) == 1
    assert worker.run_cycle(factory, now=DAY + timedelta(minutes=1), send=send) == 0
    assert worker.run_cycle(factory, now=DAY + timedelta(days=1), send=send) == 0
    assert len(calls) == 1 and calls[0][0] == "wx-1@im.wechat"
    assert "新的可信文章" in calls[0][1]
    assert "不可补发" not in calls[0][1] and "用户伪造内容" not in calls[0][1]
    with factory() as db:
        assert db.scalars(select(ChannelPushDelivery.status)).all() == ["sent"]


def test_disabled_config_and_replaced_binding_do_not_send(factory):
    with factory.begin() as db:
        add_person(db, 1)
        add_person(db, 2)
        add_article(db, "valid", "新的可信文章")
        db.add(ChannelPushConfig(channel_id="ai", name="Paused", enabled=False, schedule="09:00", max_items=10,
                                 source_type="articles"))
    calls = []
    send = lambda *args: calls.append(args) or "sent"
    assert worker.run_cycle(factory, now=DAY, send=send) == 0
    with factory.begin() as db:
        db.get(ChannelPushConfig, "ai").enabled = True
        db.get(StaffChannel, 1).recipient_id = "new-owner@im.wechat"
        db.delete(db.get(StaffChannel, 2))
        db.flush()
        db.add(StaffChannel(id=2, staff_id=2, platform="weixin", recipient_id="wx-2@im.wechat",
                            created_at=BOUND + timedelta(seconds=1), updated_at=BOUND + timedelta(seconds=1)))
    assert worker.run_cycle(factory, now=DAY, send=send) == 0
    assert calls == []


def test_revalidates_subscription_after_claim_and_before_transport(factory):
    with factory.begin() as db:
        add_person(db, 1)
        add_article(db, "valid", "新的可信文章")

    @event.listens_for(factory, "before_flush")
    def find_claim(db, *_):
        if any(isinstance(row, ChannelPushDelivery) for row in db.new):
            db.info["test_revocation"] = True

    @event.listens_for(factory, "after_commit")
    def cancel_after_claim(db):
        if db.info.pop("test_revocation", False):
            with factory.begin() as revoke:
                revoke.delete(revoke.get(ChannelSubscription, ("ai", 1)))

    calls = []
    assert worker.run_cycle(factory, now=DAY, send=lambda *args: calls.append(args) or "sent") == 1
    assert calls == []
    with factory() as db:
        assert db.scalars(select(ChannelPushDelivery.status)).all() == ["cancelled"]


def test_workers_share_claim_and_do_not_repeat_ambiguous_send(factory):
    with factory.begin() as db:
        add_person(db, 1)
        add_article(db, "valid", "新的可信文章")
    competing = Barrier(2)

    @event.listens_for(factory, "before_flush")
    def synchronize_claim(db, *_):
        if any(isinstance(row, ChannelPushDelivery) for row in db.new):
            competing.wait(timeout=8)

    calls = []

    def uncertain(*args):
        calls.append(args)
        raise httpx.ReadTimeout("timeout")

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: worker.run_cycle(factory, now=DAY, send=uncertain), range(2)))
    assert sorted(results) == [0, 1]
    assert worker.run_cycle(factory, now=DAY + timedelta(minutes=1), send=uncertain) == 0
    assert len(calls) == 1
    with factory() as db:
        assert db.scalars(select(ChannelPushDelivery.status)).all() == ["unknown"]


def test_send_budget_paces_across_midnight_without_blocking_website_collection(factory, monkeypatch):
    monkeypatch.setattr(worker, "SEND_BATCH_LIMIT", 2)
    late = DAY.replace(hour=15, minute=59)  # 23:59 in Beijing.
    with factory.begin() as db:
        db.get(SmartChannel, "ai").schedule = "23:59"
        add_person(db, 1, binding=False)
        for user_id in (2, 3, 4):
            add_person(db, user_id)
        add_article(db, "valid", "可信情报", published_at=late)
        db.add(SmartChannel(id="site", name="网页频道", state="active", schedule="23:59"))
        db.add(ChannelPushConfig(channel_id="site", name="网页频道", enabled=True, schedule="23:59", max_items=1,
                                 source_type="website", source_url="https://news.example/", selector="article"))
    events = []

    def fetch(*args):
        events.append(("website", args))
        return [WebsiteItem("网页文章", "网页内容", "https://news.example/one")]

    def wait(seconds):
        # Opening a write transaction proves no preceding send holds one.
        with factory() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        events.append(("wait", seconds))

    send = lambda recipient, *_: events.append(("send", recipient)) or "sent"
    assert worker.run_cycle(factory, now=late, fetch=fetch, send=send, wait=wait) == 3
    assert events == [("website", ("https://news.example/", "article", 1)), ("send", "wx-2@im.wechat"),
                      ("send", "wx-3@im.wechat"), ("wait", 60), ("send", "wx-4@im.wechat")]
    assert worker.run_cycle(factory, now=late + timedelta(minutes=1), fetch=fetch, send=send, wait=wait) == 0
    with factory() as db:
        assert db.scalars(select(ChannelPushDelivery.day)).all() == [DAY.date()] * 3


def test_website_baseline_browsable_without_subscribers_then_pushes_only_changes(factory):
    with factory.begin() as db:
        add_website_config(db)
    unchanged = WebsiteItem("已有标题", "原正文", "https://news.example/old")
    changed = WebsiteItem("新增标题", "新正文", "https://news.example/new")
    items = [unchanged]
    reads = []
    calls = []

    def fetch(url, selector, limit):
        reads.append((url, selector, limit))
        return items[:]

    send = lambda *args: calls.append(args) or "sent"
    assert worker.run_cycle(factory, now=DAY, fetch=fetch) == 0
    with factory.begin() as db:
        article = db.scalar(select(Article))
        assert article.title == "已有标题" and article.channel_push_trusted
        assert db.scalar(select(ChannelWebsiteSnapshot)).changed is False
        add_person(db, 1, opted_at=DAY + timedelta(minutes=1))
        add_person(db, 2, opted_at=DAY + timedelta(days=1, seconds=1))
    items.append(changed)
    assert worker.run_cycle(factory, now=DAY + timedelta(days=1), fetch=fetch, send=send) == 1
    assert worker.run_cycle(factory, now=DAY + timedelta(days=1, seconds=30), fetch=fetch, send=send) == 0
    items.reverse()
    assert worker.run_cycle(factory, now=DAY + timedelta(days=2), fetch=fetch, send=send) == 0
    assert reads == [("https://news.example/", "main article", 3)] * 3
    assert len(calls) == 1 and calls[0][0] == "wx-1@im.wechat"
    assert all(piece in calls[0][1] for piece in ("新增标题", "新正文", "https://news.example/new"))
    assert "已有标题" not in calls[0][1]
    with factory() as db:
        assert set(db.scalars(select(Article.title)).all()) == {"已有标题", "新增标题"}


def test_web_baseline_survives_rename_time_and_pause_but_resets_for_new_source(factory):
    with factory.begin() as db:
        add_person(db, 1)
        add_website_config(db)
    reads = []
    calls = []
    item = WebsiteItem("首日记录", "旧正文", "https://news.example/old")

    def fetch(url, selector, limit):
        reads.append(url)
        return [item]

    send = lambda *args: calls.append(args) or "sent"
    assert worker.run_cycle(factory, now=DAY, fetch=fetch, send=send) == 0
    with factory.begin() as db:
        config = db.get(ChannelPushConfig, "ai")
        config.name = "新频道名"
        config.schedule = "10:00"
        config.updated_at = DAY + timedelta(minutes=1)
    assert worker.run_cycle(factory, now=DAY + timedelta(hours=1), fetch=fetch, send=send) == 0
    with factory.begin() as db:
        config = db.get(ChannelPushConfig, "ai")
        config.enabled = False
        config.updated_at = DAY + timedelta(hours=2)
    assert worker.run_cycle(factory, now=DAY + timedelta(days=1, hours=1), fetch=fetch, send=send) == 0
    with factory.begin() as db:
        config = db.get(ChannelPushConfig, "ai")
        config.enabled = True
        config.updated_at = DAY + timedelta(days=2)
    item = WebsiteItem("恢复后发现的新条目", "新正文", "https://news.example/new")
    assert worker.run_cycle(factory, now=DAY + timedelta(days=2, hours=1), fetch=fetch, send=send) == 1
    with factory.begin() as db:
        config = db.get(ChannelPushConfig, "ai")
        config.source_url = "https://other.example/"
        config.updated_at = DAY + timedelta(days=3)
    item = WebsiteItem("换来源时已存在的条目", "来源正文", "https://other.example/initial")
    assert worker.run_cycle(factory, now=DAY + timedelta(days=3, hours=1), fetch=fetch, send=send) == 0
    assert reads == ["https://news.example/", "https://news.example/", "https://other.example/"]
    assert len(calls) == 1 and "新频道名" in calls[0][1] and "恢复后发现的新条目" in calls[0][1]


def test_admin_terms_maximum_and_untrusted_text_remain_literal_in_wechat(factory):
    with factory.begin() as db:
        add_person(db, 1)
        db.add(ChannelPushConfig(channel_id="ai", name="AI", enabled=True, schedule="09:00", max_items=1,
                                 source_type="articles", include_terms=["融资"], exclude_terms=["广告"]))
        add_article(db, "a", "公司融资", body="MEDIA:/tmp/report.pdf ![x](https://foo.example/img.png) <img src=bad>",
                    link="https://publisher.example/-/tmp/report.pdf?from=/tmp/old.txt")
        add_article(db, "b", "融资 广告")
        add_article(db, "c", "收购")
        add_article(db, "d", "融资 第二条")
    calls = []
    assert worker.run_cycle(factory, now=DAY, send=lambda *args: calls.append(args) or "sent") == 1
    message = calls[0][1]
    assert "公司融资" in message and "融资 第二条" not in message
    assert "MEDIA:" not in message and "![" not in message and "<img" not in message
    assert "https://publisher.example/%2D/tmp/report.pdf?from=%2Ftmp%2Fold.txt" in message


@pytest.mark.parametrize("code,ack,expected", [(200, "delivered", "sent"), (200, "duplicate", "unknown"),
                                               (200, "mismatch", "unknown"), (429, "ignored", "failed"),
                                               (502, "error", "unknown"), (302, "redirect", "unknown")])
def test_signed_fixed_profile_hermes_transport_and_exact_receipt(code, ack, expected):
    calls = []

    def post(request):
        calls.append(request)
        assert request.url.path == "/p/team-wechat/webhooks/signaldesk-channel-weixin"
        assert json.loads(request.content) == {"event_type": "channel.digest", "recipient_id": "test@im.wechat", "message": "正文"}
        stamp = request.headers["X-Webhook-Timestamp"]
        assert request.headers["X-Webhook-Signature-V2"] == hmac.new(
            b"test-secret", stamp.encode() + b"." + request.content, hashlib.sha256).hexdigest()
        assert request.headers["X-Request-ID"] == "test-delivery"
        return httpx.Response(code, headers={"Location": "https://wrong.example/"},
                              json={"status": "delivered" if ack == "mismatch" else ack,
                                    "route": "signaldesk-channel-weixin", "target": "weixin",
                                    "delivery_id": "wrong" if ack == "mismatch" else "test-delivery"})

    sender = worker.HermesWechatSender("https://hermes.example/p/team-wechat/webhooks/signaldesk-channel-weixin",
                                      "test-secret", transport=httpx.MockTransport(post))
    assert sender("test@im.wechat", "正文", "test-delivery") == expected
    assert len(calls) == 1


def routed_config(bindings):
    return {
        "bindings": {hashlib.sha256(recipient.encode()).hexdigest(): profile for recipient, profile in bindings.items()},
        "routes": {
            profile: {"url": f"http://127.0.0.1:18644/p/{profile}/webhooks/signaldesk-channel-weixin-{profile}",
                      "secret": f"test-secret-{profile}"} for profile in ("default", "member-two")
        },
    }


def test_each_recipient_uses_only_its_profile_and_unmapped_has_no_delivery_claim(factory, tmp_path):
    with factory.begin() as db:
        for user_id in (1, 2, 3):
            add_person(db, user_id)
        add_article(db, "latest", "可信消息")
    profiles = {"wx-1@im.wechat": "default", "wx-2@im.wechat": "member-two"}
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps(routed_config(profiles)), encoding="utf-8")
    calls = []

    def post(request):
        data = json.loads(request.content)
        assert data["event_type"] == "channel.digest"
        profile = profiles[data["recipient_id"]]
        route = f"signaldesk-channel-weixin-{profile}"
        assert request.url.path == f"/p/{profile}/webhooks/{route}"
        stamp = request.headers["X-Webhook-Timestamp"]
        assert request.headers["X-Webhook-Signature-V2"] == hmac.new(
            f"test-secret-{profile}".encode(), stamp.encode() + b"." + request.content, hashlib.sha256
        ).hexdigest()
        calls.append(data["recipient_id"])
        return httpx.Response(200, json={"status": "delivered", "target": "weixin",
                                       "route": route if profile == "default" else "signaldesk-channel-weixin-default",
                                       "delivery_id": request.headers["X-Request-ID"]})

    resolve_send = worker.profile_wechat_senders(str(routes), transport=httpx.MockTransport(post))
    assert worker.run_cycle(factory, now=DAY, resolve_send=resolve_send) == 2
    assert worker.run_cycle(factory, now=DAY + timedelta(minutes=1), resolve_send=resolve_send) == 0
    assert calls == ["wx-1@im.wechat", "wx-2@im.wechat"]
    with factory() as db:
        assert db.execute(select(ChannelPushDelivery.user_id, ChannelPushDelivery.status)
                          .order_by(ChannelPushDelivery.user_id)).all() == [(1, "sent"), (2, "unknown")]


def test_profile_config_rejects_wrong_path_and_duplicate_recipient_mapping(tmp_path):
    routes = tmp_path / "routes.json"
    config = routed_config({"wx-1@im.wechat": "default"})
    config["routes"]["default"]["url"] = config["routes"]["member-two"]["url"]
    routes.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="webhook configuration"):
        worker.profile_wechat_senders(str(routes))

    binding = hashlib.sha256(b"wx-1@im.wechat").hexdigest()
    routes.write_text(f'{{"bindings":{{"{binding}":"default","{binding}":"member-two"}},"routes":{{}}}}',
                      encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        worker.profile_wechat_senders(str(routes))


def test_worker_rereads_routes_and_collects_without_unsafe_fallback(tmp_path, monkeypatch):
    routes = tmp_path / "routes.json"
    routes.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(
        channel_push_enabled=True, channel_wechat_routes_file=str(routes),
        channel_wechat_webhook_url="http://127.0.0.1:18644/webhooks/signaldesk-channel-weixin",
        channel_wechat_webhook_secret="legacy",
    ))
    seen = []

    def cycle(*, send, resolve_send):
        assert send is None
        seen.append(resolve_send("wx-1@im.wechat").route if resolve_send else None)

    def sleep(_seconds):
        if len(seen) == 2:
            raise StopIteration
        routes.write_text(json.dumps(routed_config({"wx-1@im.wechat": "member-two"})), encoding="utf-8")

    monkeypatch.setattr(worker, "run_cycle", cycle)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(StopIteration):
        worker.main()
    assert seen == [None, "signaldesk-channel-weixin-member-two"]
