from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def task_client(monkeypatch):
    from app.auth import token_digest
    from app.config import get_settings
    from app.database import Base, get_db
    from app.main import app
    from app.models import Staff, StaffChannel, Task, TaskEvent, UserAccount, UserSession, utcnow

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    now = utcnow()
    with session_factory.begin() as db:
        for staff_id, enabled in ((21, True), (22, True), (23, True), (24, False), (25, True)):
            db.add(Staff(id=staff_id, name=f"员工{staff_id}", enabled=enabled))
        db.add(StaffChannel(staff_id=22, platform="wecom", recipient_id="other-staff-channel"))
        for token, staff_id, role, pages, enabled in (
            ("member", 21, "member", ["tasks"], True),
            ("admin", 23, "admin", [], True),
            ("disabled-staff", 24, "member", ["tasks"], True),
            ("disabled-account", 25, "member", ["tasks"], False),
            ("denied", 22, "member", [], True),
        ):
            user = UserAccount(
                username=token, password_hash="unused-for-session-fixture",
                staff_id=staff_id, role=role, allowed_pages=pages, enabled=enabled,
            )
            db.add(UserSession(
                token_hash=token_digest(token), user=user,
                expires_at=now + timedelta(days=1), last_used_at=now,
            ))
        for task_id, staff_id, task_status, deleted in (
            (1, 21, "todo", False), (2, 22, "todo", False),
            (3, 21, "done", False), (4, 22, "done", False),
            (5, 21, "todo", True), (6, 22, "todo", True),
            (7, 21, "todo", False),
        ):
            db.add(Task(
                id=task_id, task_no=f"T{task_id}", title=f"任务{task_id}",
                content="仅同事任务有此搜索词" if staff_id == 22 else "我的内容",
                assignee_id=staff_id, creator_name="另一个人", status=task_status,
                deleted_at=now if deleted else None,
                updated_at=now + timedelta(seconds=task_id),
            ))
            if task_id != 7:
                db.add(TaskEvent(task_id=task_id, event_type="created", operator_name="种子员工"))

    def get_test_db():
        with session_factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, get_test_db)
    monkeypatch.setattr(get_settings(), "api_key", "task-scope-service-key")
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        engine.dispose()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


MEMBER = bearer("member")
ADMIN = bearer("admin")
SERVICE = {"X-SignalDesk-Key": "task-scope-service-key"}


@pytest.mark.parametrize(("state", "expected"), [
    ("active", {1, 7}), ("done", {3}), ("deleted", {5}), ("all", {1, 3, 5, 7}),
])
def test_member_list_and_total_are_scoped(task_client, state, expected):
    response = task_client.get("/api/v1/tasks", params={"state": state}, headers=MEMBER)
    assert response.status_code == 200
    assert response.json()["total"] == len(expected)
    assert {item["id"] for item in response.json()["items"]} == expected


def test_filters_and_pagination_cannot_expand_member_scope(task_client):
    for filters in ({"assignee_id": 22}, {"search": "仅同事任务有此搜索词"}, {"search": "T6"}):
        response = task_client.get("/api/v1/tasks", params={"state": "all", **filters}, headers=MEMBER)
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}
    response = task_client.get("/api/v1/tasks", params={"assignee_id": 21, "limit": 1, "offset": 1}, headers=MEMBER)
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert [item["id"] for item in response.json()["items"]] == [1]


@pytest.mark.parametrize(("method", "path", "body", "expected"), [
    ("GET", "/tasks/2", None, 404),
    ("GET", "/tasks/by-number/T2", None, 404),
    ("GET", "/tasks/by-number/T6?include_deleted=true", None, 404),
    ("GET", "/tasks/2/events", None, 404),
    ("GET", "/tasks/6/events", None, 404),
    ("PATCH", "/tasks/2", {"title": "越界修改"}, 404),
    ("DELETE", "/tasks/2", None, 404),
    ("POST", "/tasks/4/reopen", {}, 404),
    ("POST", "/tasks/6/restore", {}, 404),
    ("POST", "/tasks/2/complete", {}, 403),
    ("POST", "/tasks/999/complete", {}, 403),
    ("PATCH", "/tasks/2", {"status": "done", "assignee_id": 21}, 403),
])
def test_other_assignee_cannot_be_read_or_mutated(task_client, method, path, body, expected):
    response = task_client.request(method, f"/api/v1{path}", headers=MEMBER, json=body)
    assert response.status_code == expected
    assert set(response.json()) == {"detail"}
    tasks = task_client.get("/api/v1/tasks?state=all&assignee_id=22", headers=ADMIN).json()["items"]
    assert {(item["id"], item["title"], item["status"], bool(item["deleted_at"])) for item in tasks} == {
        (2, "任务2", "todo", False), (4, "任务4", "done", False), (6, "任务6", "todo", True),
    }


def test_member_can_operate_owned_task_and_view_owned_history(task_client):
    own = "/api/v1/tasks/1"
    assert task_client.get(own, headers=MEMBER).json()["id"] == 1
    assert task_client.get("/api/v1/tasks/by-number/T5", headers=MEMBER).json()["id"] == 5
    assert task_client.get("/api/v1/tasks/by-number/T5?include_deleted=false", headers=MEMBER).status_code == 404
    assert task_client.get("/api/v1/tasks/7/events", headers=MEMBER).json() == []
    assert task_client.patch(own, headers=MEMBER, json={"title": "新标题", "assignee_id": 21}).json()["title"] == "新标题"
    assert task_client.post(f"{own}/complete", headers=MEMBER, json={}).json()["status"] == "done"
    assert task_client.post(f"{own}/reopen", headers=MEMBER, json={}).json()["status"] == "todo"
    assert task_client.delete(own, headers=MEMBER).json()["deleted_at"]
    events = task_client.get(f"{own}/events", headers=MEMBER).json()
    assert {item["event_type"] for item in events} == {"created", "updated", "completed", "reopened", "deleted"}
    assert task_client.post(f"{own}/restore", headers=MEMBER, json={}).json()["deleted_at"] is None


def test_member_cannot_assign_or_reassign_others(task_client):
    forbidden = task_client.post("/api/v1/tasks", headers=MEMBER, json={"title": "越界分配", "assignee_id": 22})
    assert forbidden.status_code == 403
    assert task_client.patch("/api/v1/tasks/1", headers=MEMBER, json={"assignee_id": 22}).status_code == 403
    assert task_client.get("/api/v1/tasks/1", headers=ADMIN).json()["assignee_id"] == 21
    own = task_client.post("/api/v1/tasks", headers=MEMBER, json={"title": "成员新建", "assignee_id": 21})
    assert own.status_code == 201
    assert own.json()["assignee_id"] == 21


def test_member_idempotency_does_not_return_another_assignees_task(task_client):
    payload = {"title": "原始服务任务", "assignee_id": 22, "source_platform": "wecom", "source_message_id": "known-id"}
    original = task_client.post("/api/v1/tasks", headers=SERVICE, json=payload)
    assert original.status_code == 201
    own_payload = {**payload, "title": "成员自己的任务", "assignee_id": 21}
    created = task_client.post("/api/v1/tasks", headers=MEMBER, json=own_payload)
    assert created.status_code == 201
    assert created.json()["assignee_id"] == 21
    assert created.json()["id"] != original.json()["id"]
    repeated = task_client.post("/api/v1/tasks", headers=MEMBER, json=own_payload)
    assert repeated.json()["id"] == created.json()["id"]
    assert task_client.post("/api/v1/tasks", headers=SERVICE, json=payload).json()["id"] == original.json()["id"]
    assert task_client.post("/api/v1/tasks", headers=MEMBER, json=payload).status_code == 403
    legacy_own = {**own_payload, "source_message_id": "legacy-own-id"}
    legacy = task_client.post("/api/v1/tasks", headers=SERVICE, json=legacy_own)
    assert legacy.status_code == 201
    assert task_client.post("/api/v1/tasks", headers=MEMBER, json=legacy_own).json()["id"] == legacy.json()["id"]
    assert task_client.patch(
        f"/api/v1/tasks/{created.json()['id']}", headers=ADMIN, json={"assignee_id": 22},
    ).status_code == 200
    inaccessible_replay = task_client.post("/api/v1/tasks", headers=MEMBER, json=own_payload)
    assert inaccessible_replay.status_code == 409
    assert set(inaccessible_replay.json()) == {"detail"}


@pytest.mark.parametrize("headers", [ADMIN, SERVICE])
def test_privileged_scope_and_staff_filter(task_client, headers):
    response = task_client.get("/api/v1/tasks?state=all", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 7
    assert task_client.get("/api/v1/tasks/by-number/T6", headers=headers).json()["id"] == 6
    assert task_client.get("/api/v1/tasks/2/events", headers=headers).json()[0]["task_id"] == 2
    staff = task_client.get("/api/v1/staff?include_disabled=true", headers=headers).json()
    assert {item["id"] for item in staff} == {21, 22, 23, 24, 25}
    updated = task_client.patch("/api/v1/tasks/2", headers=headers, json={"assignee_id": 21})
    assert updated.status_code == 200
    assert updated.json()["assignee_id"] == 21
    assert updated.json()["assignee_name"] == "员工21"
    assert task_client.get("/api/v1/tasks/2", headers=MEMBER).status_code == 200
    assert task_client.post("/api/v1/tasks/2/complete", headers=headers, json={}).status_code == 403


def test_staff_and_tasks_require_enabled_personal_identity_and_page(task_client):
    staff = task_client.get("/api/v1/staff?include_disabled=true", headers=MEMBER)
    assert staff.status_code == 200
    assert [item["id"] for item in staff.json()] == [21]
    for token in ("disabled-staff", "disabled-account", "denied"):
        assert task_client.get("/api/v1/staff", headers=bearer(token)).status_code == 403
        assert task_client.get("/api/v1/tasks?state=all", headers=bearer(token)).status_code == 403
