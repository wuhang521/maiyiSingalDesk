import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TASK_DATABASE_URL", "sqlite:///./test-signaldesk-members.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_user_session, hash_password
from app.config import get_settings
from app.content_models import ContentAccount, ContentEpisode
from app.database import Base, get_db
from app.main import app
from app.models import NotificationOutbox, Staff, StaffChannel, Task, UserAccount, UserSession, utcnow


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    identities = []
    with factory() as db:
        for name, role in [("管理员", "admin"), ("普通成员", "member")]:
            staff = Staff(name=name, enabled=True)
            user = UserAccount(username=role, staff=staff, role=role, enabled=True, password_hash=hash_password("test-password"))
            db.add(user); db.flush()
            token, _ = create_user_session(db, user)
            identities.append((staff.id, user.id, {"Authorization": f"Bearer {token}"}))
        db.commit()
    def session():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = session
    client = TestClient(app)
    try:
        yield client, factory, identities
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close(); engine.dispose()


def create(client, admin, name="新成员", username="newmember", role="member"):
    response = client.post("/api/v1/admin/members", headers=admin,
                           json=dict(name=name, username=username, password="initial-password", role=role))
    assert response.status_code == 201, response.text
    return response.json()


def login(client, username="newmember", password="initial-password"):
    response = client.post("/api/v1/auth/login", json=dict(username=username, password=password))
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def update(client, admin, member, **values):
    return client.patch(f"/api/v1/admin/members/{member['id']}", headers=admin,
                        json=dict(name=member["name"], role=member["user"]["role"], enabled=True, **values))


def test_member_cannot_use_admin_or_legacy_management_endpoints(api):
    client, _, identities = api
    staff_id, _, member = identities[1]
    assert client.get("/api/v1/admin/members").status_code == 401
    operations = [
        ("GET", "/api/v1/admin/members", None),
        ("POST", "/api/v1/admin/members", dict(name="越权", username="blocked", password="test-password")),
        ("PATCH", f"/api/v1/admin/members/{staff_id}", dict(name="越权", role="admin", enabled=True)),
        ("DELETE", f"/api/v1/admin/members/{staff_id}", None),
        ("POST", f"/api/v1/admin/members/{staff_id}/restore", None),
        ("POST", "/api/v1/staff", dict(name="越权人员")),
        ("PATCH", f"/api/v1/staff/{staff_id}", dict(enabled=False)),
        ("POST", f"/api/v1/staff/{staff_id}/channels", dict(platform="qq", recipient_id="spoof")),
        ("DELETE", f"/api/v1/staff/{staff_id}/channels/1", None),
        ("POST", "/api/v1/auth/users", dict(username="root", password="test-password", staff_id=staff_id, role="admin")),
    ]
    for method, url, payload in operations:
        assert client.request(method, url, headers=member, json=payload).status_code == 403, url
    assert client.get("/api/v1/staff", headers=member).status_code == 200


def test_create_is_atomic_and_does_not_expose_secrets(api):
    client, factory, identities = api
    admin = identities[0][2]
    created = create(client, admin, " 新成员 ", " NewMember ")
    assert created["name"] == "新成员" and created["user"]["username"] == "newmember"
    listing = client.get("/api/v1/admin/members", headers=admin)
    assert "password" not in listing.text and "token" not in listing.text
    duplicate = client.post("/api/v1/admin/members", headers=admin,
                            json=dict(name="不应留下的人员", username="NEWMEMBER", password="test-password"))
    assert duplicate.status_code == 409
    with factory() as db:
        assert not db.scalar(select(Staff).where(Staff.name == "不应留下的人员"))
    for data in [dict(name="  ", username="abc", password="test-password"),
                 dict(name="有效姓名", username="  ", password="test-password"),
                 dict(name="有效姓名", username="abc", password="short")]:
        assert client.post("/api/v1/admin/members", headers=admin, json=data).status_code == 422


def test_remove_restore_preserves_history_and_revokes_every_session(api):
    client, factory, identities = api
    admin = identities[0][2]
    member = create(client, admin)
    tokens = [login(client), login(client)]
    with factory() as db:
        staff = db.get(Staff, member["id"])
        staff.channels.append(StaffChannel(platform="qq", recipient_id="test-recipient"))
        task = Task(title="历史任务", assignee_id=staff.id, status="todo", remind_enabled=True)
        db.add(task); db.flush()
        db.add(ContentAccount(id="history", owner_id=member["user"]["id"], document={"name": "历史内容"}))
        db.add(ContentEpisode(id="episode", account_id="history", document={"approvals": [{"by": member["user"]["id"]}]}))
        db.add(NotificationOutbox(platform="qq", recipient_id="test-recipient", dedupe_key="test", payload="{}"))
        db.commit()
        task_id = task.id
    path = f"/api/v1/admin/members/{member['id']}"
    removed = client.delete(path, headers=admin)
    assert removed.status_code == 200 and removed.json()["removed_at"]
    for token in tokens:
        assert client.get("/api/v1/tasks", headers=token).status_code == 401
    assert client.post("/api/v1/auth/login", json=dict(username="newmember", password="initial-password")).status_code == 403
    assert member["id"] not in [m["id"] for m in client.get("/api/v1/staff?include_disabled=true", headers=admin).json()]
    assert client.post("/api/v1/tasks", headers=admin, json=dict(title="不允许分配", assignee_id=member["id"])).status_code == 409
    assert client.patch(f"/api/v1/staff/{member['id']}", headers=admin, json=dict(enabled=True)).status_code == 409
    with factory() as db:
        assert db.get(Task, task_id).title == "历史任务"
        assert db.get(ContentAccount, "history").owner_id == member["user"]["id"]
        assert db.get(ContentEpisode, "episode").document["approvals"]
        assert db.scalar(select(NotificationOutbox)).status == "cancelled"
        assert not db.scalar(select(UserSession).where(UserSession.user_id == member["user"]["id"]))
    restored = client.post(f"{path}/restore", headers=admin)
    assert restored.status_code == 200 and restored.json()["enabled"] is False
    assert restored.json()["removed_at"] is None
    assert update(client, admin, member).status_code == 200
    assert client.get("/api/v1/auth/me", headers=login(client)).status_code == 200
    for token in tokens:
        assert client.get("/api/v1/auth/me", headers=token).status_code == 401


def test_self_and_last_admin_protection_including_legacy_endpoint(api):
    client, _, identities = api
    staff_id, _, admin = identities[0]
    service = {"X-SignalDesk-Key": get_settings().api_key}
    path = f"/api/v1/admin/members/{staff_id}"
    assert client.delete(path, headers=service).status_code == 409
    assert client.patch(path, headers=service, json=dict(name="管理员", role="member", enabled=True)).status_code == 409
    assert client.patch(f"/api/v1/staff/{staff_id}", headers=admin, json=dict(enabled=False)).status_code == 409
    create(client, admin, "另一管理员", "secondadmin", "admin")
    assert client.delete(path, headers=admin).status_code == 409
    assert client.patch(path, headers=admin, json=dict(name="管理员", role="member", enabled=True)).status_code == 409
    assert client.patch(path, headers=admin, json=dict(name="管理员", role="admin", enabled=False)).status_code == 409


def test_existing_staff_account_password_reset_and_disable(api):
    client, _, identities = api
    admin = identities[0][2]
    staff = client.post("/api/v1/staff", headers=admin, json=dict(name="原有负责人")).json()
    path = f"/api/v1/admin/members/{staff['id']}"
    grant = client.patch(path, headers=admin, json=dict(name="原有负责人", username="legacy", password="initial-password", role="member", enabled=True))
    assert grant.status_code == 200 and grant.json()["id"] == staff["id"]
    old = login(client, "legacy")
    reset = client.patch(path, headers=admin, json=dict(name="原有负责人", password="replacement-password", role="member", enabled=True))
    assert reset.status_code == 200
    assert client.get("/api/v1/auth/me", headers=old).status_code == 401
    assert client.post("/api/v1/auth/login", json=dict(username="legacy", password="initial-password")).status_code == 401
    new = login(client, "legacy", "replacement-password")
    assert client.patch(f"/api/v1/staff/{staff['id']}", headers=admin, json=dict(enabled=False)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=new).status_code == 401
    assert client.patch(f"/api/v1/staff/{staff['id']}", headers=admin, json=dict(enabled=True)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=new).status_code == 401


def test_role_change_revokes_old_admin_tokens(api):
    client, _, identities = api
    admin = identities[0][2]
    second = create(client, admin, "第二管理员", "secondadmin", "admin")
    token = login(client, "secondadmin")
    response = client.patch(f"/api/v1/admin/members/{second['id']}", headers=admin,
                            json=dict(name=second["name"], role="member", enabled=True))
    assert response.status_code == 200
    assert client.get("/api/v1/admin/members", headers=token).status_code == 401
    assert client.get("/api/v1/admin/members", headers=login(client, "secondadmin")).status_code == 403


def test_disabled_staff_does_not_queue_reminders(api, monkeypatch):
    from app import worker
    client, factory, identities = api
    admin = identities[0][2]
    member = create(client, admin)
    with factory() as db:
        staff = db.get(Staff, member["id"])
        staff.enabled = False
        staff.channels.append(StaffChannel(platform="qq", recipient_id="disabled"))
        db.add(Task(title="保留的任务", assignee_id=staff.id, remind_enabled=True, next_remind_at=utcnow()))
        db.commit()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    worker.queue_due_notifications()
    with factory() as db:
        assert db.scalars(select(NotificationOutbox)).all() == []
