"""Hermes first-contact subscription bridge, bound to a verified private receiver."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_shared_api_key
from .channel_subscriptions import (MemberSubscriptionDraft, active_wechat_binding, apply_member_subscriptions,
                                    lock_sqlite_writer, member_subscription_state)
from .database import get_db
from .member_service import load_member
from .models import SmartChannel, StaffChannel
from .page_access import can_access
from .wechat_pairing import require_profile

router = APIRouter(prefix='/api/v1/channel-onboarding', tags=['channel-onboarding'],
                   dependencies=[Depends(require_shared_api_key)])


class Receiver(BaseModel):
    model_config = ConfigDict(extra='forbid')
    recipient_id: str = Field(max_length=160, pattern=r'^[A-Za-z0-9_.-]+@im\.wechat$')
    profile: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')


class ReceiverSubscriptions(Receiver, MemberSubscriptionDraft):
    pass


def receiver_member(db, payload, *, lock=False):
    query = select(StaffChannel).where(StaffChannel.platform == 'weixin', StaffChannel.enabled.is_(True),
        StaffChannel.recipient_id == payload.recipient_id, StaffChannel.wechat_profile == payload.profile)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    bindings = list(db.scalars(query))
    if len(bindings) != 1:
        raise HTTPException(404, '当前微信尚未关联唯一的迈医协同成员账号，请联系管理员完成绑定')
    binding = bindings[0]
    staff = load_member(db, binding.staff_id)
    user = staff.user
    if not staff.enabled or not user or not user.enabled or not can_access(user.allowed_pages, user.role, frozenset({'channels'})):
        raise HTTPException(403, '当前成员账号不可用或没有智能频道权限')
    active = active_wechat_binding(db, staff.id)
    if active is None or active.id != binding.id:
        raise HTTPException(409, '当前成员的微信绑定需要管理员核对')
    require_profile(payload.recipient_id, payload.profile)
    return staff


def public_state(state):
    return dict(binding_revision=state['binding_revision'], subscribed_channel_ids=state['subscribed_channel_ids'],
                channels=[dict(id=row['id'], name=row['name'], schedule=row['schedule'])
                          for row in state['channels'] if row['can_subscribe']])


@router.post('/catalog')
def catalog(payload: Receiver, db: Session = Depends(get_db)):
    staff = receiver_member(db, payload)
    return public_state(member_subscription_state(db, staff))


@router.put('/subscriptions')
def subscribe(payload: ReceiverSubscriptions, db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    # Retain the channel-first lock order shared with administrator and personal edits.
    db.scalars(select(SmartChannel).order_by(SmartChannel.id).with_for_update()).all()
    staff = receiver_member(db, payload, lock=True)
    draft = MemberSubscriptionDraft(previous_channel_ids=payload.previous_channel_ids,
        channel_ids=sorted(set(payload.previous_channel_ids + payload.channel_ids)), binding_revision=payload.binding_revision)
    return public_state(apply_member_subscriptions(staff.id, draft, db))
