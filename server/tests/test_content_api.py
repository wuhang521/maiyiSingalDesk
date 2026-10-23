import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("TASK_DATABASE_URL", "sqlite:///./test-signaldesk-content.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import content
from app.auth import create_user_session
from app.database import Base, get_db
from app.main import app
from app.models import Staff, UserAccount


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        users = []
        for name in ["创作负责人", "专业审核员", "无关成员"]:
            staff = Staff(name=name)
            db.add(staff)
            db.flush()
            user = UserAccount(username=name, password_hash="unused", staff_id=staff.id, role="member")
            db.add(user)
            db.flush()
            token, _ = create_user_session(db, user)
            users.append((user.id, {"Authorization": f"Bearer {token}"}))
        db.commit()

    def session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    client = TestClient(app)
    yield client, users
    app.dependency_overrides.clear()
    client.close()
    engine.dispose()


def account_data(owner=1, professional=2):
    return dict(account_name="家庭决策编辑部", positioning="帮助家庭明确问题", core_audience="家庭决策人",
                persona_facts=[dict(fact="机构内容编辑部", source="机构资料", public_scope="机构身份", approved_expression="内容编辑部")],
                stance_rules=[dict(topic="决策", position="先理解条件", reason="便于沟通", conditions="一般沟通", exceptions="紧急情形另行处理")],
                pillars_and_columns=[dict(name="家庭沟通", question="怎样讲清楚问题", takeaway="问题清单", formats=["口播文案"])],
                extension_rules="不延展", prohibited_content="不虚构经历", voice_rules="克制。正例：先了解条件；反例：一定正确。",
                writing_templates=[dict(name="问题与方法", structure="场景—问题—方法—总结")], approved_examples=[],
                default_platforms=["视频号", "小红书"], format_defaults=dict(format="口播文案", target=120, maximum=300, unit="秒"),
                expertise_policy=dict(required=True, scope="专业事实", source_requirements="核对原始资料"),
                knowledge_collections=[dict(id="source-1", title="演示材料", kind="专业来源", publisher="测试机构", date="2026-09-15",
                                           locator="第一段", url="https://example.test/source", content="仅供软件测试的来源内容。")],
                reviewer_assignments=dict(content=owner, professional=professional, final=owner), presentation_templates="")


def create(client, owner):
    response = client.post("/api/v1/content/accounts", headers=owner, json=dict(data=account_data(), action="activate"))
    assert response.status_code == 201, response.text
    account = response.json()
    response = client.post("/api/v1/content/episodes", headers=owner, json=dict(account_id=account["id"], brief=dict(topic_brief="如何准备家庭沟通")))
    assert response.status_code == 201, response.text
    return account, response.json()


def save(client, episode, step, data, headers, expected=200, **extra):
    response = client.put(f"/api/v1/content/episodes/{episode['id']}/steps/{step}", headers=headers,
                          json=dict(revision=episode["revision"], data=data, **extra))
    assert response.status_code == expected, response.text
    return response.json()


def approve(client, episode, step, headers, expected=200, **extra):
    response = client.post(f"/api/v1/content/episodes/{episode['id']}/steps/{step}/review", headers=headers,
                           json=dict(revision=episode["revision"], version=episode["steps"][step]["version"], decision="approve", **extra))
    assert response.status_code == expected, response.text
    return response.json()


def first_two(client, episode, owner):
    episode = save(client, episode, 0, dict(approved_topic="如何准备家庭沟通", audience_scenario="家庭讨论前", core_question="如何列清问题",
                                          primary_objective="提供实用方法", audience_takeaway="一份问题清单", column_fit="家庭沟通", novelty_note="暂无历史内容"), owner)
    episode = approve(client, episode, 0, owner)
    episode = save(client, episode, 1, dict(core_position="先列清楚问题再讨论", stance_basis="先理解条件", scope_and_exceptions="一般沟通",
                                          narrative_angle="从实际场景切入", intended_response="理解不同处境"), owner)
    return approve(client, episode, 1, owner)


def verify_claims(client, episode, professional):
    data = deepcopy(episode["steps"][2]["data"])
    for claim in data["claims"]:
        claim.update(claim_type="价值判断", applicability="一般沟通", verification_result="不适用", verification_note="本句为价值建议，不含外部事实")
    episode = save(client, episode, 2, data, professional)
    return approve(client, episode, 2, professional)


def third(client, episode, professional):
    data = dict(content_attributes=["其他专业内容"], professional_scope="专业事实", authenticity_tasks=[], claims=[
        dict(id="core", claim_text="先列清楚问题再讨论", claim_type="价值判断", supporting_sources=[], applicability="一般沟通",
             verification_result="不适用", verification_note="价值建议，不含外部事实")])
    return approve(client, save(client, episode, 2, data, professional), 2, professional)


def test_draft_configuration_cannot_generate_and_nonmembers_cannot_read(api):
    client, users = api
    owner, stranger = users[0][1], users[2][1]
    draft = client.post("/api/v1/content/accounts", headers=owner, json=dict(data=dict(account_name="草稿"))).json()
    assert client.post("/api/v1/content/episodes", headers=owner, json=dict(account_id=draft["id"], brief=dict(topic_brief="主题"))).status_code == 422
    assert client.get("/api/v1/content/accounts", headers=stranger).json() == []
    assert client.get("/api/v1/content/schema").status_code == 401


def test_predecessor_permission_and_stale_revision_are_enforced(api):
    client, users = api
    owner, professional, stranger = [u[1] for u in users]
    _, episode = create(client, owner)
    save(client, episode, 3, dict(opening="跳步"), owner, expected=409)
    before = deepcopy(episode)
    episode = first_two(client, episode, owner)
    assert client.get(f"/api/v1/content/episodes/{episode['id']}", headers=stranger).status_code == 404
    save(client, before, 0, {}, owner, expected=409)
    approve(client, episode, 1, professional, expected=403)
    assert client.post(f"/api/v1/content/episodes/{episode['id']}/export", headers=owner,
                       json=dict(revision=episode["revision"])).status_code == 409


def test_facts_need_sources_and_actual_professional_review(api):
    client, users = api
    owner, professional = users[0][1], users[1][1]
    _, episode = create(client, owner)
    episode = first_two(client, episode, owner)
    data = dict(content_attributes=["其他专业内容"], professional_scope="事实", claims=[
        dict(id="core", claim_text="先列清楚问题再讨论", claim_type="专业事实", applicability="一般沟通", supporting_sources=[],
             verification_result="支持", verified_by_at="伪造确认人")])
    episode = save(client, episode, 2, data, owner)
    assert episode["steps"][2]["data"]["claims"][0]["verification_result"] == "待核查"
    assert not episode["steps"][2]["data"]["claims"][0]["verified_by_at"]
    episode = save(client, episode, 2, data, professional)
    result = approve(client, episode, 2, professional, expected=422)
    assert "支持资料" in result["detail"]
    data["claims"][0]["supporting_sources"] = ["source-1"]
    episode = save(client, episode, 2, data, professional)
    episode = approve(client, episode, 2, professional)
    assert episode["steps"][2]["status"] == "approved"


def test_full_workflow_preserves_unrelated_claims_and_exports_confirmed_versions(api):
    client, users = api
    owner, professional = users[0][1], users[1][1]
    _, episode = create(client, owner)
    episode = third(client, first_two(client, episode, owner), professional)
    original_verified = episode["steps"][2]["data"]["claims"][0]["verified_by_at"]
    outline = dict(opening="提出问题", outline_sections=[dict(purpose="方法", points="整理清单", claim_refs=["core"], source_refs=[], length=90)],
                   deeper_insight="理解不同处境", ending_takeaway="问题清单")
    episode = approve(client, save(client, episode, 3, outline, owner), 3, owner)
    body = dict(draft_body="先列出想讨论的问题。\n\n给每个人表达的机会。", editorial_confirmed=True)
    episode = save(client, episode, 4, body, owner)
    assert episode["steps"][2]["status"] == "needs_review"
    assert episode["steps"][3]["status"] == "approved"
    assert episode["steps"][2]["data"]["claims"][0]["verified_by_at"] == original_verified
    assert episode["steps"][4]["data"]["editorial_confirmed"] is False
    episode = verify_claims(client, episode, professional)
    body["editorial_confirmed"] = True
    episode = save(client, episode, 4, body, owner)
    episode = approve(client, episode, 4, owner)
    data = dict(platform_variants=[dict(platform=p, final_title="先把问题列清楚", final_body=body["draft_body"], confirmed=True, shared_body=True) for p in ["视频号", "小红书"]])
    episode = save(client, episode, 5, data, owner)
    episode = verify_claims(client, episode, professional)
    data = deepcopy(episode["steps"][5]["data"])
    for variant in data["platform_variants"]:
        variant["confirmed"] = True
    episode = save(client, episode, 5, data, owner)
    episode = approve(client, episode, 5, owner)
    assert episode["status"] == "final"
    exported = client.post(f"/api/v1/content/episodes/{episode['id']}/export", headers=owner, json=dict(revision=episode["revision"], format="markdown"))
    assert exported.status_code == 200, exported.text
    assert "视频号" in exported.json()["content"] and "小红书" in exported.json()["content"]
    assert body["draft_body"] in exported.json()["content"]
    episode = exported.json()["episode"]
    body["draft_body"] += "\n\n新增一个待核查表述。"
    episode = save(client, episode, 4, body, owner)
    assert episode["status"] == "draft" and episode["steps"][5]["status"] == "needs_review"
    assert len(episode["steps"][4]["history"]) == 3


def test_generation_cannot_fake_review_or_start_without_approval(api, monkeypatch):
    client, users = api
    owner = users[0][1]
    _, episode = create(client, owner)
    calls = []

    async def fake(doc, index, instruction, history):
        calls.append(index)
        return dict(approved_topic="AI 选题", audience_scenario="家庭", core_question="如何沟通", primary_objective="提供实用方法",
                    audience_takeaway="问题清单", column_fit="家庭沟通", novelty_note="无历史")

    monkeypatch.setattr(content, "generate_data", fake)
    response = client.post(f"/api/v1/content/episodes/{episode['id']}/steps/0/generate", headers=owner, json=dict(revision=episode["revision"]))
    assert response.status_code == 200, response.text
    episode = response.json()
    assert episode["steps"][0]["status"] == "pending" and not episode["approvals"]
    response = client.post(f"/api/v1/content/episodes/{episode['id']}/steps/1/generate", headers=owner, json=dict(revision=episode["revision"]))
    assert response.status_code == 409 and calls == [0]


def test_ai_failure_preserves_saved_draft_and_can_retry(api, monkeypatch):
    client, users = api
    owner = users[0][1]
    _, episode = create(client, owner)

    async def failing(*args):
        raise content.HTTPException(503, "未配置 AI")

    monkeypatch.setattr(content, "generate_data", failing)
    response = client.post(f"/api/v1/content/episodes/{episode['id']}/steps/0/generate", headers=owner, json=dict(revision=episode["revision"]))
    assert response.status_code == 503
    restored = client.get(f"/api/v1/content/episodes/{episode['id']}", headers=owner).json()
    assert restored["steps"][0]["status"] == "draft"
    assert restored["brief"] == episode["brief"]
    assert restored["revision"] > episode["revision"]


def test_config_update_is_not_silently_applied(api):
    client, users = api
    owner = users[0][1]
    account, episode = create(client, owner)
    account = client.get("/api/v1/content/accounts", headers=owner).json()[0]
    data = deepcopy(account["data"])
    data["voice_rules"] = "更新后的风格"
    changed = client.put(f"/api/v1/content/accounts/{account['id']}", headers=owner,
                         json=dict(revision=account["revision"], data=data, action="activate"))
    assert changed.status_code == 200, changed.text
    restored = client.get(f"/api/v1/content/episodes/{episode['id']}", headers=owner).json()
    assert restored["account_config_version"] == 1
    assert restored["account_snapshot"]["voice_rules"] != "更新后的风格"
    migrated = client.post(f"/api/v1/content/episodes/{episode['id']}/migrate", headers=owner, json=dict(revision=restored["revision"]))
    assert migrated.status_code == 200
    assert migrated.json()["account_config_version"] == 2


def test_adding_material_does_not_restart_confirmed_planning(api):
    client, users = api
    owner, professional = users[0][1], users[1][1]
    _, episode = create(client, owner)
    episode = third(client, first_two(client, episode, owner), professional)
    brief = deepcopy(episode["brief"])
    brief["reference_materials"] = [dict(id="new-source", title="补充参考", kind="专业来源", content="补充材料摘录")]
    response = client.put(f"/api/v1/content/episodes/{episode['id']}/brief", headers=owner,
                          json=dict(revision=episode["revision"], data=brief))
    assert response.status_code == 200, response.text
    assert all(s["status"] == "approved" for s in response.json()["steps"][:3])


def test_professional_reviewer_cannot_reassign_content_approval_to_self(api):
    client, users = api
    owner, professional = users[0][1], users[1][1]
    _, episode = create(client, owner)
    brief = deepcopy(episode["brief"])
    brief["episode_reviewers"]["content"] = users[1][0]
    response = client.put(f"/api/v1/content/episodes/{episode['id']}/brief", headers=professional,
                          json=dict(revision=episode["revision"], data=brief))
    assert response.status_code == 403
