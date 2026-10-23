"""仅供本地端到端测试；使用独立测试数据库与确定性 AI 替身。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import content
from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Staff, UserAccount
from app.content import AccountWrite, write_account
from test_content_api import account_data


def seed():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.query(UserAccount).count():
            return
        staff = Staff(name="内容审核员（测试）")
        db.add(staff)
        db.flush()
        user = UserAccount(username="content-preview", password_hash=hash_password("content-preview-only"), staff_id=staff.id, role="admin")
        db.add(user)
        db.flush()
        user.staff = staff
        data = account_data(user.id, user.id)
        data["account_name"] = "家庭决策观察 · 测试账号"
        data["expertise_policy"]["required"] = False
        data["expertise_policy"]["scope"] = "普通价值讨论"
        data["default_platforms"] = ["视频号"]
        account = content.ContentAccount(id=content.uid(), owner_id=user.id, document=dict(data={}, status="draft", active_version=0,
            active_data=None, config_history=[], owner=content.actor(user), created_at=content.now(), updated_at=content.now()))
        write_account(db, account, AccountWrite(data=data, action="activate"), user)


async def fake_generate(doc, index, instruction, history):
    topic = doc["brief"]["topic_brief"]
    values = [
        dict(approved_topic=topic, audience_scenario="家庭成员围坐讨论前", core_question="怎样让每个人的问题被听见", primary_objective="提供实用方法",
             audience_takeaway="一份可以共同补充的问题清单", column_fit="家庭沟通：围绕日常讨论中的不同处境", novelty_note="暂无历史定稿，本期为首篇内容"),
        dict(core_position="先把问题列清楚，再讨论怎样选择。", stance_basis="引用账号立场：先理解条件", scope_and_exceptions="适用于日常家庭沟通；紧急情况不适用",
             narrative_angle="从家庭会议的具体场景切入", competing_perspectives="每个人关注的事项可能不同", intended_response="理解彼此关注的问题"),
        dict(content_attributes=["普通价值讨论"], professional_scope="普通价值讨论，无额外专业主张", authenticity_tasks=[], claims=[
            dict(id="claim-core", claim_text="先把问题列清楚，再讨论怎样选择。", claim_type="价值判断", supporting_sources=[], applicability="日常家庭沟通",
                 ai_check_note="价值建议，需要人工确认表述边界", verification_result="待核查", verification_note="")]),
        dict(opening="每个人想解决的问题，可能并不一样。", outline_sections=[dict(id="outline-1", purpose="具体方法", points="先列清单，再说明条件", claim_refs=["claim-core"], source_refs=[], length=90)],
             deeper_insight="讨论同一件事，背后可能有不同的担心", ending_takeaway="一份可以共同补充的问题清单", engagement_prompt="你会先写下什么问题？"),
        dict(draft_body="**先把问题列清楚，再讨论怎样选择。**\n\n每个人想解决的问题，可能并不一样。\n\n先写下自己的问题，再邀请家人补充。", visual_notes="固定画面，配合旁白；当前仅交付文案", inline_comments=[], revision_brief="", editorial_confirmed=False),
        dict(platform_variants=[dict(id="variant-1", platform="视频号", final_title=topic, final_body=doc["steps"][4]["data"].get("draft_body", ""),
                                    summary="从一份问题清单开始讨论。", shared_body=True, cover="", metadata="", public_notes="本篇为家庭沟通的价值建议。", confirmed=False)]),
    ]
    return values[index]


seed()
content.generate_data = fake_generate

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18777, log_level="warning")
