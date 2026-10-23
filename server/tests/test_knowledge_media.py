"""Prepared, not run: synthetic immutable media parts, no decoder or model calls."""
from sqlalchemy import select
from app.knowledge_models import KnowledgeAsset, KnowledgeItem, KnowledgeMediaPart
from test_knowledge import kb, collect

def seeded(kb):
    client, users, factory, _ = kb
    item = collect(client, users[0], title="合成视频", content="旧正文")
    with factory() as db:
        source = db.get(KnowledgeItem, item["id"]); source.revision = 5
        db.add_all([KnowledgeAsset(id="1" * 32, item_id=source.id, filename="synthetic.mp4", storage_name="synthetic.mp4", sha256="1" * 64, size=100), KnowledgeAsset(id="2" * 32, item_id=source.id, filename="other.mp4", storage_name="other.mp4", sha256="2" * 64, size=100)])
        db.add_all([KnowledgeMediaPart(id="a" * 32, item_id=source.id, asset_id="1" * 32, source_revision=2, method="volc_asr_draft", content="合成语音原文", chunks=[["1.00–2.00s", "合成语音原文"]]),
            KnowledgeMediaPart(id="b" * 32, item_id=source.id, asset_id="1" * 32, source_revision=4, method="video_ocr_draft", content="合成采样画面原文", chunks=[["1.00s 采样画面", "合成采样画面原文"]]),
            KnowledgeMediaPart(id="c" * 32, item_id=source.id, asset_id="2" * 32, source_revision=3, method="video_ocr_draft", content="其他原件画面", chunks=[["1.00s", "其他原件画面"]])]); db.commit()
    return item, dict(revision=5, speech_id="a" * 32, frames_id="b" * 32, acknowledge_draft=True)

def test_media_merge_draft_stays_out_of_retrieval_until_owner_confirms(kb):
    item, payload = seeded(kb); client, users, _, _ = kb
    path = f"/api/v1/knowledge/items/{item['id']}"
    result = client.post(path + "/media-merge", headers=users[0], json=payload)
    assert result.status_code == 200 and result.json()["method"] == "media_merge_draft" and result.json()["status"] == "needs_review"
    assert "合成语音原文" in result.json()["content"] and "合成采样画面原文" in result.json()["content"]
    assert client.post("/api/v1/knowledge/ask", headers=users[0], json={"question": "合成语音"}).json()["citations"] == []
    confirmed = client.post(path + "/media-merge/confirm", headers=users[0], json={"revision": result.json()["revision"], "confirmation": "已核对"})
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "ready"
    assert client.post("/api/v1/knowledge/ask", headers=users[0], json={"question": "合成语音"}).json()["citations"]
    assert len(client.get(path + "/media-parts", headers=users[0]).json()["items"]) == 3

def test_media_merge_owner_original_and_revision_fences(kb):
    item, payload = seeded(kb); client, users, _, _ = kb
    path = f"/api/v1/knowledge/items/{item['id']}"
    for user in users[1:]:
        assert client.get(path + "/media-parts", headers=user).status_code == 404
        assert client.post(path + "/media-merge", headers=user, json=payload).status_code == 404
    assert client.post(path + "/media-merge", headers=users[0], json={**payload, "frames_id": "c" * 32}).status_code == 422
    assert client.post(path + "/media-merge", headers=users[0], json={**payload, "revision": 1}).status_code == 409
    assert client.post(path + "/media-merge", headers=users[0], json={**payload, "acknowledge_draft": "true"}).status_code == 422
