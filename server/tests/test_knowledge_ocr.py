"""Real isolated API/database; only the paid Ark boundary is virtualized."""
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from test_knowledge import kb, collect
from app.config import get_settings
from app.knowledge_models import KnowledgeItem, KnowledgeJob, KnowledgeRemoteCleanup
from app.knowledge_worker import claim, finish, run_once
from app.models import UserAccount, utcnow
from app import knowledge_ocr


def configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_ocr_enabled", True)
    monkeypatch.setattr(settings, "knowledge_ocr_api_key", "isolated-test-key")


def image_item(client, user):
    item = collect(client, user, title="截图")
    result = client.post(f"/api/v1/knowledge/items/{item['id']}/assets", headers=user,
                         params={"filename": "截图.png", "revision": item["revision"]},
                         content=b"\x89PNG\r\n\x1a\nsynthetic")
    assert result.status_code == 200, result.text
    return result.json()


def endpoint(item):
    return f"/api/v1/knowledge/items/{item['id']}/assets/{item['assets'][0]['id']}/ocr"


def test_ocr_consent_config_owner_and_revision(kb, monkeypatch):
    client, users, factory, _ = kb
    monkeypatch.setattr(get_settings(), "knowledge_ocr_enabled", False)
    item = image_item(client, users[0])
    payload = {"revision": item["revision"], "cloud_consent": True}
    assert client.post(endpoint(item), headers=users[0], json=payload).status_code == 503
    configured(monkeypatch)
    for consent in [False, "true", 1, None]:
        assert client.post(endpoint(item), headers=users[0], json={**payload, "cloud_consent": consent}).status_code == 422
    for user in users[1:]:
        assert client.post(endpoint(item), headers=user, json=payload).status_code == 404
    assert client.post(endpoint(item), headers=users[0], json={**payload, "revision": 999}).status_code == 409
    result = client.post(endpoint(item), headers=users[0], json=payload)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("application/json")
    assert result.json()["status"] == "received"
    assert client.post(endpoint(item), headers=users[0], json=payload).status_code == 409
    with factory() as db:
        jobs = db.scalars(select(KnowledgeJob).where(KnowledgeJob.mode == "doubao_ocr")).all()
        assert len(jobs) == 1 and jobs[0].attempts == 0


def test_draft_is_excluded_until_owner_confirmation(kb, monkeypatch):
    client, users, factory, _ = kb
    configured(monkeypatch)
    item = image_item(client, users[0])
    item = client.post(endpoint(item), headers=users[0], json={"revision": item["revision"], "cloud_consent": True}).json()
    job = claim(factory)
    assert job["request"]["mode"] == "doubao_ocr"
    finish(job, {"state": "ready", "text": "健康知识截图内容", "chunks": [["第 1 页（AI OCR）", "健康知识截图内容"]], "method": "doubao_ocr_draft"}, factory)
    item = client.get(f"/api/v1/knowledge/items/{item['id']}", headers=users[0]).json()
    assert item["status"] == "needs_review"
    assert client.post("/api/v1/knowledge/ask", headers=users[0], json={"question": "健康知识"}).json()["citations"] == []
    url = f"/api/v1/knowledge/items/{item['id']}/ocr/confirm"
    payload = {"revision": item["revision"], "confirmation": "已核对"}
    assert client.post(url, headers=users[1], json=payload).status_code == 404
    assert client.post(url, headers=users[0], json={**payload, "confirmation": "yes"}).status_code == 422
    confirmed = client.post(url, headers=users[0], json=payload)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"
    assert confirmed.json()["method"] == "doubao_ocr_reviewed"
    assert confirmed.json()["revision"] == item["revision"] + 1
    assert client.post(url, headers=users[0], json=payload).status_code == 409
    cited = client.post("/api/v1/knowledge/ask", headers=users[0], json={"question": "健康知识"}).json()["citations"]
    assert cited[0]["locator"] == "第 1 页（AI OCR）"
    assert client.post("/api/v1/knowledge/ask", headers=users[1], json={"question": "健康知识"}).json()["citations"] == []


def test_paid_lease_recovery_never_repeats_inference(kb, monkeypatch):
    client, users, factory, _ = kb
    configured(monkeypatch)
    item = image_item(client, users[0])
    client.post(endpoint(item), headers=users[0], json={"revision": item["revision"], "cloud_consent": True})
    job = claim(factory)
    with factory() as db:
        db.get(KnowledgeJob, job["id"]).lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert claim(factory) is None
    with factory() as db:
        assert db.get(KnowledgeItem, item["id"]).status == "needs_input"
        assert db.get(KnowledgeJob, job["id"]).state == "needs_input"


def test_disabled_account_cancels_before_paid_call(kb, monkeypatch):
    client, users, factory, _ = kb
    configured(monkeypatch)
    item = image_item(client, users[0])
    client.post(endpoint(item), headers=users[0], json={"revision": item["revision"], "cloud_consent": True})
    with factory() as db:
        db.get(UserAccount, 1).enabled = False
        db.commit()
    assert claim(factory) is None


def response(text):
    return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def test_image_adapter_uses_fixed_private_contract(tmp_path, monkeypatch):
    configured(monkeypatch)
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/responses"
        data = json.loads(request.content)
        assert data["store"] is False and data["thinking"] == {"type": "disabled"}
        assert data["input"][0]["content"][0]["type"] == "input_image"
        assert b"image.png" not in request.content
        return httpx.Response(200, json=response(json.dumps({"pages": [{"page": 1, "text": "健康截图文字"}]})))
    result = knowledge_ocr.recognize(path, "截图.png", lambda _: None, transport=httpx.MockTransport(handler))
    assert result.method == "doubao_ocr_draft"
    assert result.text == "健康截图文字" and len(requests) == 1


@pytest.mark.parametrize("output", ["not json", '{"pages":[]}', '{"pages":[{"page":1,"text":""}]}',
    '{"pages":[{"page":2,"text":"wrong"}]}', '{"pages":[{"page":1,"text":"a"},{"page":1,"text":"b"}]}'])
def test_invalid_outputs_never_become_evidence(tmp_path, monkeypatch, output):
    configured(monkeypatch)
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    with pytest.raises(knowledge_ocr.OcrError):
        knowledge_ocr.recognize(path, "x.png", lambda _: None,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(output))))


def test_pdf_records_cleanup_before_inference_and_sets_expiry(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    configured(monkeypatch)
    path = tmp_path / "doc.pdf"
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    with path.open("wb") as stream:
        writer.write(stream)
    events = []
    def handler(request):
        if request.method == "POST" and request.url.path.endswith("/files"):
            assert b'expire_at' in request.content and b'user_data' in request.content
            events.append("upload")
            return httpx.Response(200, json={"id": "file-test", "status": "active"})
        assert events == ["upload", "persist:file-test"]
        return httpx.Response(200, json=response('{"pages":[{"page":1,"text":"合成PDF"}]}'))
    result = knowledge_ocr.recognize(path, "报告.pdf", lambda id: events.append(f"persist:{id}"), transport=httpx.MockTransport(handler))
    assert result.text == "合成PDF"


def test_remote_cleanup_survives_purge_and_retries(kb, monkeypatch):
    client, users, factory, _ = kb
    item = image_item(client, users[0])
    knowledge_ocr.record_cleanup("file-test", factory)
    with factory() as db:
        row = db.scalar(select(KnowledgeRemoteCleanup))
        assert row.available_at > utcnow()
        row.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    client.delete(f"/api/v1/knowledge/items/{item['id']}", headers=users[0], params={"revision": item["revision"]})
    removed = client.get(f"/api/v1/knowledge/items/{item['id']}", headers=users[0]).json()
    client.post(f"/api/v1/knowledge/items/{item['id']}/purge", headers=users[0], json={"revision": removed["revision"], "confirmation": "永久删除"})
    monkeypatch.setattr(knowledge_ocr, "delete_remote", lambda _: False)
    assert knowledge_ocr.cleanup_remote_once(factory)
    with factory() as db:
        row = db.scalar(select(KnowledgeRemoteCleanup))
        assert row and row.attempts == 1
        row.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(knowledge_ocr, "delete_remote", lambda _: True)
    knowledge_ocr.cleanup_remote_once(factory)
    with factory() as db:
        assert db.scalar(select(KnowledgeRemoteCleanup)) is None
