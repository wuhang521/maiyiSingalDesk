"""Joint-acceptance cases prepared during development; no real cloud calls."""
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import knowledge_cloud, knowledge_organization
from app.config import get_settings
from app.models import utcnow
from test_knowledge import kb, collect


def payload():
    return dict(summary=dict(text="保存原始来源。", citations=[1]),
                points=[dict(text="先核对正文，再入库。", citations=[1])], tags=["资料管理"])


@pytest.mark.parametrize("field,value", [
    ("summary", dict(text="虚构出处", citations=[2])),
    ("summary", dict(text="虚构出处", citations=[True])),
    ("summary", dict(text="https://invalid.example", citations=[1])),
    ("summary", dict(text="[99] 虚构标号", citations=[1])),
    ("summary", dict(text="字" * 601, citations=[1])),
    ("points", []), ("tags", ["非法\n标签"]), ("tags", ["字" * 21]),
])
def test_organization_rejects_untraceable_structure(field, value):
    data = payload(); data[field] = value
    with pytest.raises(HTTPException) as error:
        knowledge_organization.validated(data, [dict(locator="段落 1", text="合成资料")])
    assert error.value.status_code == 502


def test_organization_unknown_processing_is_not_success():
    row = SimpleNamespace(id="synthetic", source_revision=1, state="processing", payload={}, status_note="等待",
        provider="mock", model="mock", created_at=utcnow() - timedelta(seconds=61))
    result = knowledge_organization.read_result(row)
    assert result["state"] == "unknown" and "不会自动" in result["status_note"]
    assert row.state == "processing"  # A read does not mutate a running row.


def test_organization_private_and_separate_from_original(kb, monkeypatch):
    client, users, _, _ = kb
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_organization_enabled", True)
    monkeypatch.setattr(settings, "prompt_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "prompt_api_key", "synthetic-never-network")
    monkeypatch.setattr(settings, "prompt_model", "synthetic-model")
    monkeypatch.setattr(knowledge_organization, "admission", knowledge_cloud.Admission())
    item = collect(client, users[0], title="合成资料", content="保存原始来源，先核对正文，再入库。")
    collect(client, users[1], title="另一账号", content="其他人员的私有正文，绝不能发送。")
    calls = []
    async def generate(question, evidence, *, system):
        calls.append(evidence)
        assert "其他人员" not in str(evidence)
        assert all(set(entry) == {"title", "locator", "text"} for entry in evidence)
        return payload()
    monkeypatch.setattr(knowledge_organization, "_request", generate)
    endpoint = f"/api/v1/knowledge/items/{item['id']}/organization"
    for user in users[1:]:
        assert client.get(endpoint, headers=user).status_code == 404
    body = dict(revision=item["revision"], cloud_consent=True)
    for consent in [False, "true", 1]:
        assert client.post(endpoint, headers=users[0], json={**body, "cloud_consent": consent}).status_code == 422
    result = client.post(endpoint, headers=users[0], json=body)
    assert result.status_code == 200 and result.json()["state"] == "draft"
    current = client.get(f"/api/v1/knowledge/items/{item['id']}", headers=users[0]).json()
    assert (current["content"], current["revision"], current["tags"]) == (item["content"], item["revision"], item["tags"])
    assert client.get(endpoint, headers=users[0]).json()["result"]["payload"]["tags"] == ["资料管理"]
    assert client.get("/api/v1/knowledge/usage", headers=users[0]).json()["daily_submissions"] == 1
    assert len(calls) == 1
