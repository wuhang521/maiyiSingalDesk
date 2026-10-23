"""Read-only, opt-in team grants. Personal owner checks stay unchanged.

Team managers manage membership, not other people's private libraries. A grant
admits one reviewed source revision, never future revisions, files or AI notes.
Revocation is enforced on every read; already downloaded/copied text is not erasable.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .auth import require_user
from .database import get_db
from .knowledge_models import KnowledgeChunk, KnowledgeItem, KnowledgeShare, KnowledgeTeam, KnowledgeTeamEvent, KnowledgeTeamMember
from .knowledge_service import check_revision, new_id, owned_item, search_terms
from .models import Staff, UserAccount, utcnow

router = APIRouter(prefix="/api/v1/knowledge/teams", tags=["knowledge-teams"])


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value): raise ValueError("团队名称无效")
        return value.strip()


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    role: str = Field(default="member", pattern="^(member|manager)$")
    team_revision: int = Field(ge=1)


class ShareCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=40)
    item_revision: int = Field(ge=1)
    team_revision: int = Field(ge=1)
    share_consent: StrictBool

    @field_validator("share_consent")
    @classmethod
    def consent(cls, value):
        if not value: raise ValueError("请明确同意向当前团队共享本份已核对正文、标题、标签和来源")
        return value


def team_access(db, user, team_id, *, manage=False, lock=False):
    query = select(KnowledgeTeam).where(KnowledgeTeam.id == team_id, KnowledgeTeam.enabled.is_(True))
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    team = db.scalar(query)
    member = db.get(KnowledgeTeamMember, (team_id, user.id)) if team else None
    if not team or not member: raise HTTPException(404, "团队不存在或当前账号已不在该团队")
    if manage and team.owner_id != user.id and member.role != "manager":
        raise HTTPException(403, "只有团队负责人可以管理成员或团队设置")
    return team, member


def team_version(team, revision):
    if team.revision != revision: raise HTTPException(409, "团队成员或共享资料已改变，请刷新；本次未覆盖修改。")


def team_read(team, member, user_id):
    return dict(id=team.id, name=team.name, revision=team.revision,
                can_manage=team.owner_id == user_id or member.role == "manager", is_owner=team.owner_id == user_id)


def event(db, team, user, action, **metadata):
    db.add(KnowledgeTeamEvent(id=new_id(), team_id=team.id, actor_id=user.id, action=action, metadata_json=metadata))
    team.revision += 1


def admitted(team_id):
    return select(KnowledgeItem, KnowledgeShare).join(KnowledgeShare, KnowledgeShare.item_id == KnowledgeItem.id).join(
        UserAccount, UserAccount.id == KnowledgeItem.owner_id).join(Staff, Staff.id == UserAccount.staff_id).where(
        KnowledgeShare.team_id == team_id, KnowledgeShare.source_revision == KnowledgeItem.revision,
        KnowledgeItem.status == "ready", KnowledgeItem.deleted_at.is_(None), UserAccount.enabled.is_(True),
        Staff.enabled.is_(True), Staff.removed_at.is_(None))


def shared_read(item, share, *, detail=False, can_revoke=False):
    result = dict(id=item.id, team_id=share.team_id, title=item.title, publisher=item.publisher, tags=item.tags,
        source_url=item.source_url, revision=item.revision, shared_at=share.created_at, summary=item.content[:300], can_revoke=can_revoke)
    if detail: result["content"] = item.content
    return result


@router.get("")
def list_teams(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.execute(select(KnowledgeTeam, KnowledgeTeamMember).join(KnowledgeTeamMember).where(
        KnowledgeTeamMember.user_id == user.id, KnowledgeTeam.enabled.is_(True)).order_by(KnowledgeTeam.created_at).limit(100)).all()
    return dict(items=[team_read(team, member, user.id) for team, member in rows])


@router.post("", status_code=201)
def create_team(payload: TeamCreate, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    from .knowledge_usage import lock_paid_owner
    user = lock_paid_owner(db, user)
    count = db.scalar(select(func.count()).select_from(KnowledgeTeam).where(KnowledgeTeam.owner_id == user.id, KnowledgeTeam.enabled.is_(True)))
    if count >= 10: raise HTTPException(429, "每个账号最多创建 10 个启用团队")
    team = KnowledgeTeam(id=new_id(), name=payload.name, owner_id=user.id)
    db.add(team); db.flush()
    member = KnowledgeTeamMember(team_id=team.id, user_id=user.id, role="manager")
    db.add(member); db.add(KnowledgeTeamEvent(id=new_id(), team_id=team.id, actor_id=user.id, action="created", metadata_json={}))
    db.commit(); return team_read(team, member, user.id)


@router.get("/{team_id}/members")
def members(team_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, _ = team_access(db, user, team_id)
    rows = db.execute(select(KnowledgeTeamMember, UserAccount, Staff).join(UserAccount,
        UserAccount.id == KnowledgeTeamMember.user_id).join(Staff, Staff.id == UserAccount.staff_id).where(
        KnowledgeTeamMember.team_id == team_id).order_by(UserAccount.id).limit(100)).all()
    return dict(items=[dict(user_id=account.id, username=account.username, name=staff.name, role=member.role,
        is_owner=account.id == team.owner_id, enabled=account.enabled and staff.enabled and not staff.removed_at) for member, account, staff in rows])


@router.post("/{team_id}/members")
def add_member(team_id: str, payload: MemberChange, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, _ = team_access(db, user, team_id, manage=True, lock=True); team_version(team, payload.team_revision)
    target = db.scalar(select(UserAccount).join(Staff).where(UserAccount.username == payload.username.strip(),
        UserAccount.enabled.is_(True), Staff.enabled.is_(True), Staff.removed_at.is_(None)))
    if not target: raise HTTPException(404, "没有找到启用的个人账号，请核对登录账号名")
    if target.id == team.owner_id and payload.role != "manager": raise HTTPException(409, "不能降低团队创建者的负责人权限")
    row = db.get(KnowledgeTeamMember, (team_id, target.id))
    if not row:
        count = db.scalar(select(func.count()).select_from(KnowledgeTeamMember).where(KnowledgeTeamMember.team_id == team_id))
        if count >= 100: raise HTTPException(429, "每个团队最多 100 位成员")
        row = KnowledgeTeamMember(team_id=team_id, user_id=target.id); db.add(row)
    row.role = payload.role; event(db, team, user, "member_set", user_id=target.id, role=payload.role)
    db.commit(); return dict(revision=team.revision)


@router.delete("/{team_id}/members/{user_id}")
def remove_member(team_id: str, user_id: int, revision: int = Query(ge=1),
                  user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, _ = team_access(db, user, team_id, manage=True, lock=True); team_version(team, revision)
    if user_id == team.owner_id: raise HTTPException(409, "不能移除团队创建者；创建者可关闭团队")
    db.execute(delete(KnowledgeTeamMember).where(KnowledgeTeamMember.team_id == team_id, KnowledgeTeamMember.user_id == user_id))
    # Leaving members' previous grants are revoked too; never leave their documents exposed.
    db.execute(delete(KnowledgeShare).where(KnowledgeShare.team_id == team_id, KnowledgeShare.shared_by == user_id))
    event(db, team, user, "member_removed", user_id=user_id); db.commit(); return dict(revision=team.revision)


@router.delete("/{team_id}")
def close_team(team_id: str, revision: int = Query(ge=1), user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, _ = team_access(db, user, team_id, manage=True, lock=True); team_version(team, revision)
    if team.owner_id != user.id: raise HTTPException(403, "只有团队创建者可以关闭团队")
    team.enabled = False; event(db, team, user, "closed"); db.commit()
    return dict(closed=True, message="团队已关闭，共享访问已撤销；个人原文和历史仍保留。")


@router.post("/{team_id}/shares")
def share_item(team_id: str, payload: ShareCreate, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, _ = team_access(db, user, team_id, lock=True); team_version(team, payload.team_revision)
    item = owned_item(db, user.id, payload.item_id, lock=True); check_revision(item, payload.item_revision)
    if item.status != "ready" or not item.content.strip(): raise HTTPException(409, "只能共享本人已经核对入库的正文")
    row = db.get(KnowledgeShare, (team_id, item.id))
    if not row: row = KnowledgeShare(team_id=team_id, item_id=item.id); db.add(row)
    row.source_revision = item.revision; row.shared_by = user.id; row.created_at = utcnow()
    event(db, team, user, "shared", item_id=item.id, revision=item.revision)
    db.commit(); return dict(revision=team.revision, message="仅当前已核对版本的正文与来源已共享；原文件不共享，后续修订不自动共享。")


@router.delete("/{team_id}/shares/{item_id}")
def revoke_share(team_id: str, item_id: str, revision: int = Query(ge=1),
                  user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, member = team_access(db, user, team_id, lock=True); team_version(team, revision)
    row = db.get(KnowledgeShare, (team_id, item_id))
    if row and row.shared_by != user.id and team.owner_id != user.id and member.role != "manager":
        raise HTTPException(403, "只能撤回本人共享，团队负责人可以移除团队中的共享")
    if row: db.delete(row)
    event(db, team, user, "share_revoked", item_id=item_id); db.commit(); return dict(revision=team.revision)


@router.get("/{team_id}/items")
def shared_items(team_id: str, offset: int = Query(0, ge=0), user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team_access(db, user, team_id)
    query = admitted(team_id)
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(KnowledgeShare.created_at.desc()).offset(offset).limit(50)).all()
    return dict(items=[shared_read(item, share) for item, share in rows], total=count)


@router.get("/{team_id}/items/{item_id}")
def shared_detail(team_id: str, item_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team, member = team_access(db, user, team_id)
    row = db.execute(admitted(team_id).where(KnowledgeItem.id == item_id)).first()
    if not row: raise HTTPException(404, "共享已撤回、来源已修订 / 停用，或资料不存在；请联系所有者重新共享")
    return shared_read(*row, detail=True, can_revoke=row[1].shared_by == user.id or team.owner_id == user.id or member.role == "manager")


@router.get("/{team_id}/search")
def shared_search(team_id: str, q: str = Query(min_length=2, max_length=500),
                  user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    team_access(db, user, team_id); terms = search_terms(q)[:20]
    if not terms: return dict(items=[])
    allowed = admitted(team_id).with_only_columns(KnowledgeItem.id).subquery()
    chunks = db.scalars(select(KnowledgeChunk).join(KnowledgeItem).where(KnowledgeItem.id.in_(select(allowed.c.id)),
        KnowledgeChunk.revision == KnowledgeItem.revision, or_(*(KnowledgeChunk.content.contains(term, autoescape=True) for term in terms)))
        .order_by(KnowledgeItem.updated_at.desc(), KnowledgeChunk.ordinal).limit(200)).all()
    chunks.sort(key=lambda chunk: -sum(chunk.content.casefold().count(term) for term in terms))
    return dict(items=[dict(item_id=chunk.item_id, team_id=team_id, revision=chunk.revision,
        locator=chunk.locator, text=chunk.content) for chunk in chunks[:5]])
