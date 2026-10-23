"""Prepared, not run: explicit manual original upload with integrity and receipts."""
import base64
import hashlib
from test_knowledge import kb

def signed(user, body, filename="synthetic.txt", sha=None):
    return {**user, "X-Filename-Base64": base64.b64encode(filename.encode()).decode(), "X-Content-SHA256": sha or hashlib.sha256(body).hexdigest(), "Idempotency-Key": "c" * 64, "Content-Type": "application/octet-stream"}

def test_capture_is_owner_scoped_idempotent_and_does_not_authorize_models(kb):
    client, users, factory, _ = kb; body = b"synthetic original"
    headers = signed(users[0], body)
    first = client.post("/api/v1/knowledge/capture/files", headers=headers, content=body)
    assert first.status_code == 200 and first.json()["saved"]
    item = first.json()["item"]; second = client.post("/api/v1/knowledge/capture/files", headers=headers, content=body)
    assert second.json()["duplicate"] and second.json()["item"]["id"] == item["id"]
    other = client.post("/api/v1/knowledge/capture/files", headers=signed(users[1], body), content=body)
    assert other.json()["item"]["id"] != item["id"]
    assert client.get(f"/api/v1/knowledge/items/{item['id']}", headers=users[2]).status_code == 404
    assert client.get("/api/v1/knowledge/usage", headers=users[0]).json()["daily_submissions"] == 0

def test_capture_mismatch_is_unknown_no_replay_and_no_published_original(kb):
    client, users, _, folder = kb; body = b"synthetic original"
    headers = signed(users[0], body, sha="0" * 64)
    assert client.post("/api/v1/knowledge/capture/files", headers=headers, content=body).status_code == 422
    assert client.post("/api/v1/knowledge/capture/files", headers=headers, content=body).status_code == 409
    assert not list((folder / "files").glob("*.txt"))
    assert client.post("/api/v1/knowledge/capture/files", headers=signed(users[0], body, filename="../escape.txt"), content=body).status_code == 422
    assert client.post("/api/v1/knowledge/capture/files", content=body).status_code == 401
