import os
import sys
import json
from urllib.parse import quote
from datetime import timedelta
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

os.environ["TASK_DATABASE_URL"] = "sqlite:///./test-signaldesk-tasks.db"
os.environ["TASK_API_KEY"] = "test-key"

from fastapi.testclient import TestClient

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Article, NotificationOutbox, SmartChannel, Task, utcnow
from app.worker import queue_due_notifications


DB_FILE = Path("test-signaldesk-tasks.db")
HEADERS = {"X-SignalDesk-Key": "test-key"}


def personal_headers(client: TestClient, staff_id: int, username: str) -> dict[str, str]:
    created = client.post(
        "/api/v1/auth/users",
        headers=HEADERS,
        json={
            "username": username,
            "password": "test-password",
            "staff_id": staff_id,
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "test-password"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_module() -> None:
    engine.dispose()
    DB_FILE.unlink(missing_ok=True)


def test_task_lifecycle_and_soft_delete() -> None:
    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/tasks")
        assert unauthorized.status_code == 401

        staff = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={
                "name": "杰哥",
                "channels": [
                    {"platform": "qq", "recipient_id": "qq-user-1", "enabled": True}
                ],
            },
        )
        assert staff.status_code == 201
        staff_id = staff.json()["id"]
        assignee_headers = personal_headers(client, staff_id, "jie")

        created = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={
                "title": "发布公众号",
                "content": "今天完成",
                "assignee_id": staff_id,
                "creator_name": "老板",
                "remind_interval_minutes": 300,
            },
        )
        assert created.status_code == 201
        task = created.json()
        assert task["task_no"] == "T1"
        assert task["assignee_name"] == "杰哥"
        assert task["next_remind_at"]
        with SessionLocal() as db:
            outbox = db.scalar(
                select(NotificationOutbox).where(NotificationOutbox.task_id == task["id"])
            )
            payload = json.loads(outbox.payload)
            assert payload["event"] == "task.created"
            assert payload["assignee"] == "杰哥"
            assert payload["creator_name"] == "老板"
            assert payload["tasks"][0]["task_no"] == task["task_no"]
            assert outbox.platform == "qq"
            assert outbox.recipient_id == "qq-user-1"

        completed = client.post(
            f"/api/v1/tasks/{task['id']}/complete",
            headers=assignee_headers,
            json={"operator_name": "杰哥"},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "done"
        assert completed.json()["next_remind_at"] is None

        deleted = client.delete(
            f"/api/v1/tasks/{task['id']}?operator_name=老板",
            headers=HEADERS,
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted_at"]

        active = client.get("/api/v1/tasks?state=active", headers=HEADERS).json()
        assert active["total"] == 0

        restored = client.post(
            f"/api/v1/tasks/{task['id']}/restore",
            headers=HEADERS,
            json={"operator_name": "老板"},
        )
        assert restored.status_code == 200
        assert restored.json()["deleted_at"] is None

        events = client.get(
            f"/api/v1/tasks/{task['id']}/events",
            headers=HEADERS,
        ).json()
        assert {item["event_type"] for item in events} >= {
            "created",
            "completed",
            "deleted",
            "restored",
        }


def test_all_authenticated_clients_can_delete() -> None:
    with TestClient(app) as client:
        staff_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={"name": "敏总"},
        ).json()["id"]
        task_id = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "周二开会", "assignee_id": staff_id},
        ).json()["id"]

        response = client.delete(f"/api/v1/tasks/{task_id}", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["deleted_at"] is not None


def test_only_assignee_can_complete_task() -> None:
    with TestClient(app) as client:
        assignee_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={"name": "杰哥"},
        ).json()["id"]
        other_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={"name": "敏总"},
        ).json()["id"]
        assignee_headers = personal_headers(client, assignee_id, "jie")
        other_headers = personal_headers(client, other_id, "boss")
        task_id = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "完成内容审核", "assignee_id": assignee_id},
        ).json()["id"]

        other_complete = client.post(
            f"/api/v1/tasks/{task_id}/complete",
            headers=other_headers,
            json={"operator_name": "杰哥"},
        )
        assert other_complete.status_code == 403
        assert other_complete.json()["detail"] == "只有任务负责人本人可以完成该任务"

        service_complete = client.post(
            f"/api/v1/tasks/{task_id}/complete",
            headers=HEADERS,
            json={"operator_name": "杰哥"},
        )
        assert service_complete.status_code == 403

        patch_complete = client.patch(
            f"/api/v1/tasks/{task_id}",
            headers=other_headers,
            json={"status": "done", "operator_name": "杰哥"},
        )
        assert patch_complete.status_code == 403
        assert client.get(
            f"/api/v1/tasks/{task_id}", headers=HEADERS
        ).json()["status"] == "todo"

        completed = client.post(
            f"/api/v1/tasks/{task_id}/complete",
            headers=assignee_headers,
            json={"operator_name": "敏总"},
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "done"
        events = client.get(
            f"/api/v1/tasks/{task_id}/events", headers=HEADERS
        ).json()
        completed_event = next(item for item in events if item["event_type"] == "completed")
        assert completed_event["operator_name"] == "杰哥"


def test_channel_identity_can_complete_own_tasks_on_all_three_platforms() -> None:
    with TestClient(app) as client:
        identities = (
            ("杰哥", "qq", "qq-jie"),
            ("敏总", "weixin", "wx-min"),
            ("振华", "wecom", "wecom-zhenhua"),
        )
        staff_ids: dict[str, int] = {}
        for name, platform, recipient_id in identities:
            staff_id = client.post(
                "/api/v1/staff",
                headers=HEADERS,
                json={
                    "name": name,
                    "channels": [
                        {
                            "platform": platform,
                            "recipient_id": recipient_id,
                            "enabled": True,
                        }
                    ],
                },
            ).json()["id"]
            staff_ids[name] = staff_id
            for index in (1, 2):
                client.post(
                    "/api/v1/tasks",
                    headers=HEADERS,
                    json={"title": f"{name}任务{index}", "assignee_id": staff_id},
                )

        for name, platform, recipient_id in identities:
            completed = client.post(
                "/api/v1/channel-task-completions",
                headers=HEADERS,
                json={
                    "platform": platform,
                    "recipient_id": recipient_id,
                    "all_active": True,
                },
            )
            assert completed.status_code == 200
            assert completed.json()["completed_count"] == 2
            assert {item["assignee_name"] for item in completed.json()["items"]} == {name}

        own_task = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "振华新增任务", "assignee_id": staff_ids["振华"]},
        ).json()
        wrong_identity = client.post(
            "/api/v1/channel-task-completions",
            headers=HEADERS,
            json={
                "platform": "qq",
                "recipient_id": "qq-jie",
                "task_no": own_task["task_no"],
            },
        )
        assert wrong_identity.status_code == 403
        assert client.get(
            f"/api/v1/tasks/{own_task['id']}", headers=HEADERS
        ).json()["status"] == "todo"

        completed = client.post(
            "/api/v1/channel-task-completions",
            headers=HEADERS,
            json={
                "platform": "wecom",
                "recipient_id": "wecom-zhenhua",
                "task_no": own_task["task_no"],
            },
        )
        assert completed.status_code == 200
        assert completed.json()["completed_count"] == 1
        repeated = client.post(
            "/api/v1/channel-task-completions",
            headers=HEADERS,
            json={
                "platform": "wecom",
                "recipient_id": "wecom-zhenhua",
                "task_no": own_task["task_no"],
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["completed_count"] == 0


def test_personal_login_can_access_tasks_and_logout() -> None:
    with TestClient(app) as client:
        staff_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={"name": "敏总"},
        ).json()["id"]
        created_user = client.post(
            "/api/v1/auth/users",
            headers=HEADERS,
            json={
                "username": "boss",
                "password": "correct-horse-2026",
                "staff_id": staff_id,
                "role": "admin",
            },
        )
        assert created_user.status_code == 201
        assert created_user.json()["display_name"] == "敏总"

        invalid = client.post(
            "/api/v1/auth/login",
            json={"username": "boss", "password": "wrong-password"},
        )
        assert invalid.status_code == 401

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "BOSS", "password": "correct-horse-2026"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        bearer = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/auth/me", headers=bearer).json()["staff_id"] == staff_id
        assert client.get("/api/v1/tasks", headers=bearer).status_code == 200

        assert client.post("/api/v1/auth/logout", headers=bearer).status_code == 204
        assert client.get("/api/v1/tasks", headers=bearer).status_code == 401


def test_reminder_batches_all_active_tasks_for_one_staff() -> None:
    with TestClient(app) as client:
        staff_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={
                "name": "杰哥",
                "channels": [
                    {"platform": "qq", "recipient_id": "qq-user-1", "enabled": True}
                ],
            },
        ).json()["id"]
        first_id = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "发布公众号", "assignee_id": staff_id},
        ).json()["id"]
        second_id = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "发布小红书", "assignee_id": staff_id},
        ).json()["id"]

    with SessionLocal() as db:
        first = db.get(Task, first_id)
        second = db.get(Task, second_id)
        first.next_remind_at = utcnow() - timedelta(minutes=1)
        second.next_remind_at = utcnow() + timedelta(hours=4)
        db.commit()

    queue_due_notifications()

    with SessionLocal() as db:
        outboxes = list(db.scalars(select(NotificationOutbox)).all())
        payloads = [json.loads(item.payload) for item in outboxes]
        reminder_payloads = [
            payload for payload in payloads if payload["event"] == "task.reminder.batch"
        ]
        assert len(reminder_payloads) == 1
        payload = reminder_payloads[0]
        assert payload["event"] == "task.reminder.batch"
        assert {item["task_no"] for item in payload["tasks"]} == {
            db.get(Task, first_id).task_no,
            db.get(Task, second_id).task_no,
        }
        assert db.get(Task, first_id).next_remind_at > utcnow()
        assert db.get(Task, second_id).next_remind_at > utcnow()


def test_wecom_new_task_notification_keeps_all_active_tasks() -> None:
    with TestClient(app) as client:
        staff_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={
                "name": "企业微信负责人",
                "channels": [
                    {"platform": "wecom", "recipient_id": "wecom-user-1", "enabled": True}
                ],
            },
        ).json()["id"]
        client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "第一项任务", "assignee_id": staff_id},
        )
        second = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={"title": "第二项任务", "assignee_id": staff_id},
        ).json()

    with SessionLocal() as db:
        outbox = db.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.dedupe_key == f"created:{second['id']}:1"
            )
        )
        payload = json.loads(outbox.payload)
        assert {item["title"] for item in payload["tasks"]} == {
            "第一项任务",
            "第二项任务",
        }


def test_create_is_idempotent_by_source_message_and_number_lookup_is_exact() -> None:
    with TestClient(app) as client:
        staff_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={"name": "敏总"},
        ).json()["id"]
        payload = {
            "title": "参加会议",
            "assignee_id": staff_id,
            "source_platform": "qq",
            "source_message_id": "qq-message-100",
        }
        first = client.post("/api/v1/tasks", headers=HEADERS, json=payload)
        second = client.post("/api/v1/tasks", headers=HEADERS, json=payload)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert client.get("/api/v1/tasks?state=all", headers=HEADERS).json()["total"] == 1

        exact = client.get(
            f"/api/v1/tasks/by-number/{first.json()['task_no']}",
            headers=HEADERS,
        )
        assert exact.status_code == 200
        assert exact.json()["id"] == first.json()["id"]


def test_source_message_id_is_normalized_and_scoped_by_platform() -> None:
    with TestClient(app) as client:
        staff_id = client.post(
            "/api/v1/staff",
            headers=HEADERS,
            json={"name": "杰哥"},
        ).json()["id"]
        base = {
            "title": "整理材料",
            "assignee_id": staff_id,
            "source_message_id": " AbC-100 ",
        }
        qq = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={**base, "source_platform": "QQ"},
        )
        qq_retry = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={**base, "source_platform": "qq"},
        )
        weixin = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={**base, "source_platform": "weixin"},
        )
        empty_id = client.post(
            "/api/v1/tasks",
            headers=HEADERS,
            json={
                "title": "无消息编号",
                "assignee_id": staff_id,
                "source_platform": "qq",
                "source_message_id": "   ",
            },
        )

        assert qq.json()["id"] == qq_retry.json()["id"]
        assert qq.json()["id"] != weixin.json()["id"]
        assert empty_id.status_code == 201


def test_article_routes_require_key_and_operator_name() -> None:
    with TestClient(app) as client:
        assert client.get("/api/v1/articles").status_code == 401
        assert client.get("/api/v1/articles", headers=HEADERS).status_code == 400
        named = client.get(
            "/api/v1/articles",
            headers={**HEADERS, "X-SignalDesk-Operator": quote("不存在")},
        )
        assert named.status_code == 200


def test_channel_upsert_ignores_auth_token() -> None:
    payload = {
        "id": "health",
        "name": "健康管理",
        "topic": "topic-1",
        "server": "https://ntfy.sh",
        "authToken": "must-not-be-stored",
        "maxItems": 20,
        "includeTerms": ["健康"],
        "excludeTerms": [],
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/channels", headers=HEADERS, json=payload)
        second = client.post(
            "/api/v1/channels",
            headers=HEADERS,
            json={**payload, "name": "健康管理情报"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        preserved = client.post(
            "/api/v1/channels?preserve_connection=true",
            headers=HEADERS,
            json={**payload, "topic": "", "server": "", "name": "每日入库名称"},
        )
        assert preserved.status_code == 200
        listed = client.get("/api/v1/channels", headers=HEADERS)
        assert listed.status_code == 200
        assert listed.json()[0]["name"] == "每日入库名称"
        assert listed.json()[0]["topic"] == "topic-1"
        assert listed.json()[0]["server"] == "https://ntfy.sh"
        assert "authToken" not in listed.json()[0]
    with SessionLocal() as db:
        channel = db.get(SmartChannel, "health")
        assert channel.name == "每日入库名称"
        assert not hasattr(channel, "auth_token")


def test_articles_are_shared_but_user_state_is_separate() -> None:
    article = {
        "id": "message-1:1",
        "channelId": "health",
        "title": "企业健康管理",
        "body": "正文",
        "timestamp": 1786400000000,
        "source": "example.com",
        "tags": ["health"],
        "priority": 4,
    }
    with TestClient(app) as client:
        for name in ("老板", "运营"):
            created = client.post("/api/v1/staff", headers=HEADERS, json={"name": name})
            assert created.status_code == 201

        batch = client.post(
            "/api/v1/articles/batch",
            headers=HEADERS,
            json={"items": [article, article]},
        )
        assert batch.status_code == 200
        with SessionLocal() as db:
            assert len(list(db.scalars(select(Article)).all())) == 1

        boss_headers = {**HEADERS, "X-SignalDesk-Operator": quote("老板")}
        ops_headers = {**HEADERS, "X-SignalDesk-Operator": quote("运营")}
        changed = client.patch(
            "/api/v1/articles/message-1:1/state",
            headers=boss_headers,
            json={"read": True, "saved": True, "feedback": "useful", "stateUpdatedAt": 100},
        )
        assert changed.status_code == 200
        boss_item = client.get("/api/v1/articles", headers=boss_headers).json()["items"][0]
        ops_item = client.get("/api/v1/articles", headers=ops_headers).json()["items"][0]
        assert (boss_item["read"], boss_item["saved"], boss_item["feedback"]) == (
            True,
            True,
            "useful",
        )
        assert (ops_item["read"], ops_item["saved"], ops_item["feedback"]) == (
            False,
            False,
            None,
        )


def test_article_batch_deduplicates_same_content_from_ntfy_and_publisher() -> None:
    base = {
        "channelId": "health",
        "title": "同一篇文章",
        "body": "相同正文",
        "timestamp": 1786400000000,
        "source": "example.com",
        "clickUrl": "https://example.com/same",
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/articles/batch",
            headers=HEADERS,
            json={
                "items": [
                    {**base, "id": "daily:health:2026-08-11:1"},
                    {**base, "id": "ntfy-message:1"},
                ]
            },
        )
        assert response.status_code == 200
    with SessionLocal() as db:
        articles = list(db.scalars(select(Article)).all())
        assert len(articles) == 1
        assert articles[0].id == "daily:health:2026-08-11:1"


def test_article_trash_restore_permanent_and_last_write_wins() -> None:
    item = {
        "id": "message-2",
        "channelId": "ai",
        "title": "AI 情报",
        "body": "正文",
        "timestamp": 1786400000000,
        "source": "example.com",
        "read": True,
        "saved": False,
        "feedback": None,
        "deletedAt": None,
        "purged": False,
        "stateUpdatedAt": 200,
    }
    with TestClient(app) as client:
        client.post("/api/v1/staff", headers=HEADERS, json={"name": "敏总"})
        headers = {**HEADERS, "X-SignalDesk-Operator": quote("敏总")}
        synced = client.post("/api/v1/articles/sync", headers=headers, json={"items": [item]})
        assert synced.status_code == 200

        stale = client.patch(
            "/api/v1/articles/message-2/state",
            headers=headers,
            json={"read": False, "stateUpdatedAt": 100},
        )
        assert stale.json()["read"] is True

        deleted = client.patch(
            "/api/v1/articles/message-2/state",
            headers=headers,
            json={"action": "delete", "stateUpdatedAt": 300},
        )
        assert deleted.json()["deletedAt"] is not None
        assert client.get("/api/v1/articles?state=active", headers=headers).json()["total"] == 0
        assert client.get("/api/v1/articles?state=trash", headers=headers).json()["total"] == 1

        restored = client.patch(
            "/api/v1/articles/message-2/state",
            headers=headers,
            json={"action": "restore", "stateUpdatedAt": 400},
        )
        assert restored.json()["deletedAt"] is None

        permanent = client.patch(
            "/api/v1/articles/message-2/state",
            headers=headers,
            json={"action": "permanent", "stateUpdatedAt": 500},
        )
        assert permanent.json()["purged"] is True
        assert client.get("/api/v1/articles?state=all", headers=headers).json()["total"] == 0
