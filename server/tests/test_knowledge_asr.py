"""Deferred joint acceptance cases; only synthetic media and mock cloud transport."""
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app import knowledge_asr
from app.config import get_settings
from app.knowledge_models import KnowledgeCloudUsage, KnowledgeItem, KnowledgeJob
from app.knowledge_worker import claim, finish
from test_knowledge import kb, collect


def media_item(client, headers):
    item = collect(client, headers, title="合成转写材料")
    response = client.post(f"/api/v1/knowledge/items/{item['id']}/assets", params={"filename": "sample.wav", "revision": item["revision"]},
        headers={**headers, "Content-Type": "application/octet-stream"}, content=b"RIFF-synthetic-test")
    assert response.status_code == 200
    return response.json()


def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "knowledge_cloud_asr_enabled", True)
    monkeypatch.setattr(get_settings(), "knowledge_cloud_asr_api_key", "synthetic-key")
    monkeypatch.setattr(knowledge_asr.shutil, "which", lambda _: "synthetic-executable")


def test_asr_owner_consent_draft_confirm_and_time_citation(kb, monkeypatch):
    client, users, factory, _ = kb
    enabled(monkeypatch)
    item = media_item(client, users[0]); asset = item["assets"][0]
    endpoint = f"/api/v1/knowledge/items/{item['id']}/assets/{asset['id']}/asr"
    body = dict(revision=item["revision"], cloud_consent=True)
    for value in [False, "true", 1, None]:
        assert client.post(endpoint, headers=users[0], json={**body, "cloud_consent": value}).status_code == 422
    for user in users[1:]:
        assert client.post(endpoint, headers=user, json=body).status_code == 404
    response = client.post(endpoint, headers=users[0], json=body)
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    queued = response.json()
    assert client.post(endpoint, headers=users[0], json={**body, "revision": queued["revision"]}).status_code == 409
    job = claim(factory)
    assert job["request"]["mode"] == "volc_asr" and job["request"]["request_id"] == job["id"]
    assert finish(job, dict(state="ready", text="合成转写文字", method="volc_asr_draft", chunks=[("0.10s–1.20s（AI 转写）", "合成转写文字")]), factory)
    before = client.post("/api/v1/knowledge/ask", headers=users[0], json={"question": "合成转写"}).json()
    assert before["citations"] == []
    confirmation = f"/api/v1/knowledge/items/{item['id']}/asr/confirm"
    assert client.post(confirmation, headers=users[0], json=dict(revision=queued["revision"], confirmation="跳过核对")).status_code == 422
    approved = client.post(confirmation, headers=users[0], json=dict(revision=queued["revision"], confirmation="已核对"))
    assert approved.status_code == 200 and approved.json()["method"] == "volc_asr_reviewed"
    after = client.post("/api/v1/knowledge/ask", headers=users[0], json={"question": "合成转写"}).json()
    assert after["citations"][0]["locator"] == "0.10s–1.20s（AI 转写）"
    assert client.post("/api/v1/knowledge/ask", headers=users[1], json={"question": "合成转写"}).json()["citations"] == []


def test_usage_is_private_and_survives_content_purge(kb, monkeypatch):
    client, users, factory, _ = kb
    enabled(monkeypatch)
    monkeypatch.setattr(get_settings(), "knowledge_paid_daily_limit", 1)
    item = media_item(client, users[0]); endpoint=f"/api/v1/knowledge/items/{item['id']}/assets/{item['assets'][0]['id']}/asr"
    queued = client.post(endpoint, headers=users[0], json=dict(revision=item["revision"], cloud_consent=True)).json()
    finish(claim(factory), dict(state="needs_input", note="合成失败，无云调用"), factory)
    assert client.post(endpoint, headers=users[0], json=dict(revision=queued["revision"], cloud_consent=True)).status_code == 429
    removed = client.delete(f"/api/v1/knowledge/items/{item['id']}", params={"revision": queued["revision"]}, headers=users[0])
    assert removed.status_code == 200
    current = removed.json()
    assert client.post(f"/api/v1/knowledge/items/{item['id']}/purge", headers=users[0], json=dict(revision=current["revision"], confirmation="永久删除")).status_code == 200
    assert client.get("/api/v1/knowledge/usage", headers=users[0]).json()["daily_remaining"] == 0
    assert client.get("/api/v1/knowledge/usage", headers=users[1]).json()["daily_submissions"] == 0
    assert client.get("/api/v1/knowledge/usage").status_code == 401
    assert client.get("/api/v1/knowledge/admin/operations", headers=users[0]).status_code == 403
    ops = client.get("/api/v1/knowledge/admin/operations", headers=users[2])
    assert ops.status_code == 200 and "job_states" in ops.json()
    assert "sample.wav" not in ops.text
    with factory() as db:
        assert db.scalar(select(KnowledgeCloudUsage.id))


def sample_result():
    return dict(audio_info=dict(duration=2000), result=dict(text="合成文字。", utterances=[dict(start_time=100,end_time=1500,text="合成文字。")]))


def test_flash_fixed_endpoint_audio_only_and_no_retry(kb, monkeypatch):
    enabled(monkeypatch)
    calls=[]
    def prepare(path, filename, output):
        output.write_bytes(b"synthetic-private-audio"); return 2.0
    def handler(request):
        calls.append(request)
        assert str(request.url) == knowledge_asr.ENDPOINT
        body=json.loads(request.content)
        assert set(body["audio"]) == {"data"} and "private-original.mp4" not in request.content.decode()
        assert request.headers["X-Api-Resource-Id"] == "volc.bigasr.auc_turbo"
        return httpx.Response(200, headers={"X-Api-Status-Code":"20000000"}, json=sample_result())
    result=knowledge_asr.recognize(Path("synthetic"),"private-original.mp4","0123456789abcdef0123456789abcdef",
        transport=httpx.MockTransport(handler),audio_preparer=prepare)
    assert result.method == "volc_asr_draft" and result.chunks[0][0] == "0.10s–1.50s（AI 转写）"
    assert len(calls)==1
    failures=[]
    def fail(request):
        failures.append(request); return httpx.Response(500,json={"secret-error":"never echo"})
    with pytest.raises(knowledge_asr.AsrError,match="未自动重试"):
        knowledge_asr.recognize(Path("synthetic"),"sample.wav","0123456789abcdef0123456789abcdef",
            transport=httpx.MockTransport(fail),audio_preparer=prepare)
    assert len(failures)==1


@pytest.mark.parametrize("mutate", [
    lambda p:p["result"].update(utterances=[]),
    lambda p:p["result"].update(text="不同正文"),
    lambda p:p["audio_info"].update(duration=999999),
    lambda p:p["result"]["utterances"][0].update(start_time=-1),
    lambda p:p["result"]["utterances"][0].update(end_time=9999),
    lambda p:p["result"]["utterances"][0].update(start_time=True),
])
def test_incomplete_or_invalid_timeline_not_admitted(mutate):
    payload=sample_result(); mutate(payload)
    with pytest.raises(knowledge_asr.AsrError): knowledge_asr.decode_result(payload,2.0)
