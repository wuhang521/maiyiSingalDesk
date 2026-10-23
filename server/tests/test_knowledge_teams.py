"""Prepared, not run: isolated real API / SQLite and source-bound sharing boundaries."""
import pytest
from test_knowledge import kb, collect
P = "/api/v1/knowledge/teams"

def team_with_member(client, users):
    created = client.post(P, headers=users[0], json={"name": "合成共享团队"})
    assert created.status_code == 201
    team = created.json()
    added = client.post(f"{P}/{team['id']}/members", headers=users[0], json={"username": "kb-test-1", "role": "member", "team_revision": team["revision"]})
    assert added.status_code == 200
    team["revision"] = added.json()["revision"]
    return team

def test_team_member_and_platform_admin_cannot_read_unshared_personal_sources(kb):
    client, users, _, _ = kb; team = team_with_member(client, users)
    item = collect(client, users[0], title="合成私人资料", content="不可通过团队自动公开的正文")
    path = f"{P}/{team['id']}/items/{item['id']}"
    for user in users[1:]:
        response = client.get(path, headers=user); assert response.status_code == 404
        assert "不可通过团队" not in response.text
        assert client.get(f"/api/v1/knowledge/items/{item['id']}", headers=user).status_code == 404
    assert client.get(P, headers=users[2]).json()["items"] == []

@pytest.mark.parametrize("consent", [False, "true", 1, None])
def test_share_requires_strict_explicit_consent(kb, consent):
    client, users, _, _ = kb; team = team_with_member(client, users)
    item = collect(client, users[0], title="合成资料", content="已入库资料正文")
    result = client.post(f"{P}/{team['id']}/shares", headers=users[0], json={"item_id": item["id"], "item_revision": item["revision"], "team_revision": team["revision"], "share_consent": consent})
    assert result.status_code == 422

def test_shared_current_revision_revocation_and_membership_are_enforced(kb):
    client, users, _, _ = kb; team = team_with_member(client, users)
    item = collect(client, users[0], title="合成资料", content="共享当前正文")
    shared = client.post(f"{P}/{team['id']}/shares", headers=users[0], json={"item_id": item["id"], "item_revision": item["revision"], "team_revision": team["revision"], "share_consent": True})
    assert shared.status_code == 200; team["revision"] = shared.json()["revision"]
    path = f"{P}/{team['id']}/items/{item['id']}"
    response = client.get(path, headers=users[1]); assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["content"] == "共享当前正文"
    assert "assets" not in response.json() and "history" not in response.json()
    assert client.delete(f"{P}/{team['id']}/shares/{item['id']}?revision={team['revision']}", headers=users[1]).status_code == 403
    assert client.patch(f"/api/v1/knowledge/items/{item['id']}", headers=users[0], json={"title": item["title"], "content": "未再次授权的新版", "revision": item["revision"]}).status_code == 200
    assert client.get(path, headers=users[1]).status_code == 404
    removed = client.delete(f"{P}/{team['id']}/members/2?revision={team['revision']}", headers=users[0]); assert removed.status_code == 200
    assert client.get(f"{P}/{team['id']}/members", headers=users[1]).status_code == 404
    assert client.get(f"{P}/{team['id']}/items?offset=-1", headers=users[0]).status_code == 422
