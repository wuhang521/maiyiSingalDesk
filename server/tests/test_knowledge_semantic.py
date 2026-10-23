"""Prepared for final joint acceptance; synthetic vectors and no paid model calls."""
import asyncio
import json

import httpx
import pytest

from app import knowledge_semantic
from app.config import get_settings
from app.knowledge_cloud import Admission
from test_knowledge import kb, collect


def configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_semantic_enabled", True)
    monkeypatch.setattr(settings, "knowledge_embedding_api_key", "synthetic-never-network")
    monkeypatch.setattr(settings, "knowledge_embedding_model", "doubao-embedding-text-240515")
    monkeypatch.setattr(knowledge_semantic, "admission", Admission())


@pytest.mark.parametrize("rows", [
    [dict(index=True, embedding=[1., 0.])], [dict(index=2, embedding=[1., 0.])],
    [dict(index=0, embedding=[False, 1.])], [dict(index=0, embedding=[0., 0.])],
    [dict(index=0, embedding=[float("nan"), 1.])], [dict(index=0, embedding=[1.] * 4097)],
])
def test_embeddings_reject_invalid_dimensions_coordinates_and_indexes(rows):
    with pytest.raises(ValueError):
        knowledge_semantic.validated(dict(model="synthetic", data=rows), 1)


def test_embedding_transport_is_single_text_only_fixed_ark_request(kb, monkeypatch):
    configured(monkeypatch); calls = []
    async def handler(request):
        calls.append(request)
        assert str(request.url) == knowledge_semantic.ENDPOINT
        assert json.loads(request.content) == dict(model="doubao-embedding-text-240515", input=["合成正文"], encoding_format="float")
        return httpx.Response(200, json=dict(model="doubao-embedding-text-240515", data=[dict(index=0, embedding=[3., 4.])]))
    result, _ = asyncio.run(knowledge_semantic.embed(["合成正文"], transport=httpx.MockTransport(handler)))
    assert result == [[.6, .8]] and len(calls) == 1


def test_semantic_private_idempotent_and_revision_invalidated(kb, monkeypatch):
    client, users, _, _ = kb
    configured(monkeypatch); calls = []
    async def embed(texts, **kwargs):
        calls.append(texts)
        assert "别人的私有内容" not in str(texts)
        return [[1., 0.] for _ in texts], "doubao-embedding-text-240515"
    monkeypatch.setattr(knowledge_semantic, "embed", embed)
    item = collect(client, users[0], title="合成向量资料", content="原文应保存来源并人工核对。")
    collect(client, users[1], title="秘密", content="别人的私有内容")
    endpoint = f"/api/v1/knowledge/items/{item['id']}/semantic-index"
    body = dict(revision=item["revision"], cloud_consent=True)
    for user in users[1:]:
        assert client.get(endpoint, headers=user).status_code == 404
    for consent in [False, "true", 1, None]:
        assert client.post(endpoint, headers=users[0], json={**body, "cloud_consent": consent}).status_code == 422
    assert client.post(endpoint, headers=users[0], json=body).json()["state"] == "ready"
    # Reset only the mock process admission; production throttling stays enabled.
    monkeypatch.setattr(knowledge_semantic, "admission", Admission())
    assert client.post(endpoint, headers=users[0], json=body).json()["state"] == "ready"
    assert len(calls) == 1 and client.get("/api/v1/knowledge/usage", headers=users[0]).json()["daily_submissions"] == 1
    monkeypatch.setattr(knowledge_semantic, "admission", Admission())
    response = client.post("/api/v1/knowledge/semantic-search", headers=users[0], json=dict(question="保存资料", cloud_consent=True))
    assert response.status_code == 200 and response.json()["citations"][0]["item_id"] == item["id"]
    assert calls[-1] == ["保存资料"]
    other = client.post("/api/v1/knowledge/semantic-search", headers=users[1], json=dict(question="保存资料", cloud_consent=True))
    assert other.json()["citations"] == [] and len(calls) == 2
    revised = client.patch(f"/api/v1/knowledge/items/{item['id']}", headers=users[0],
        json=dict(title=item["title"], content="修订后的合成正文", revision=item["revision"]))
    assert revised.status_code == 200
    assert client.get(endpoint, headers=users[0]).json()["state"] == "absent"
    assert len(calls) == 2  # No automatic rebuild after edits.
