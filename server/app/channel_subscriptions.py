"""Personal WeChat opt-ins and administrator-owned channel delivery settings."""

import hashlib
import json
import re
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_user
from .channel_aliases import SmartChannelAlias, active_channel_catalog, is_managed, resolve_channel_id
from .channel_subscription_models import ChannelPushConfig, ChannelSubscription
from .channel_website import validate_website_config
from .database import get_db
from .models import SmartChannel, Staff, StaffChannel, UserAccount, utcnow


router = APIRouter(prefix="/api/v1/channel-subscriptions", tags=["channel-subscriptions"])
TIME_PATTERN = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"


class ChannelPushDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    schedule: str = Field(pattern=TIME_PATTERN)
    max_items: int = Field(ge=1, le=100)
    source_type: Literal["articles", "website"]
    source_url: str | None = Field(default=None, max_length=2000)
    selector: str | None = Field(default=None, max_length=300)
    include_terms: list[str] = Field(default_factory=list, max_length=30)
    exclude_terms: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("include_terms", "exclude_terms")
    @classmethod
    def normalize_terms(cls, terms: list[str]) -> list[str]:
        if any(len(term) > 80 for term in terms):
            raise ValueError("每个关键词最多 80 字")
        return list(dict.fromkeys(term for term in terms if term))


class ChannelEnabled(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)


ChannelId = Annotated[str, Field(min_length=1, max_length=80)]


class ChannelOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_ids: list[ChannelId]
    channel_ids: list[ChannelId]


AccountId = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]


class ChannelAudience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_user_ids: list[AccountId] | None
    allowed_user_ids: list[AccountId] | None

    @field_validator("previous_user_ids", "allowed_user_ids")
    @classmethod
    def normalize_user_ids(cls, ids: list[int] | None) -> list[int] | None:
        return None if ids is None else sorted(set(ids))


def subscription_allowed(channel: SmartChannel, user_id: int) -> bool:
    return channel.subscription_user_ids is None or user_id in channel.subscription_user_ids


def require_channel_admin(user: UserAccount = Depends(require_user)) -> UserAccount:
    if user.role != "admin":
        raise HTTPException(403, "只有管理员可以配置频道")
    return user


def lock_sqlite_writer(db: Session) -> None:
    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def active_wechat_binding(db: Session, staff_id: int, *, lock: bool = False) -> StaffChannel | None:
    query = select(StaffChannel).where(
        StaffChannel.staff_id == staff_id,
        StaffChannel.platform == "weixin",
        StaffChannel.enabled.is_(True),
    ).order_by(StaffChannel.id).limit(2)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    bindings = db.scalars(query).all()
    if len(bindings) != 1:
        return None
    recipient = bindings[0].recipient_id
    if not re.fullmatch(r"[A-Za-z0-9_@.-]{1,160}", recipient) or recipient.lower().endswith(("@chatroom", "@im.bot")):
        return None
    return bindings[0]


def binding_fingerprint(binding: StaffChannel) -> str:
    identity = [binding.id, binding.staff_id, binding.recipient_id, binding.created_at.isoformat()]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def subscription_matches(subscription: ChannelSubscription | None, binding: StaffChannel | None) -> bool:
    return bool(subscription and binding and subscription.binding_id == binding.id
                and subscription.binding_fingerprint == binding_fingerprint(binding))


def channel_push_settings(channel: SmartChannel, config: ChannelPushConfig | None) -> dict:
    if config is not None:
        return dict(id=channel.id, name=config.name, description=config.description, enabled=config.enabled,
                    schedule=config.schedule, max_items=config.max_items, source_type=config.source_type,
                    source_url=config.source_url, selector=config.selector, include_terms=config.include_terms,
                    exclude_terms=config.exclude_terms)
    return dict(id=channel.id, name=channel.name, description=channel.description, enabled=channel.state == "active",
                schedule=channel.schedule if re.fullmatch(TIME_PATTERN, channel.schedule) else "09:00",
                max_items=max(1, min(100, channel.max_items)), source_type="articles", source_url=None, selector=None,
                include_terms=channel.include_terms or [], exclude_terms=channel.exclude_terms or [])


def locked_channel(db: Session, channel_id: str) -> SmartChannel | None:
    return db.scalar(select(SmartChannel).where(SmartChannel.id == resolve_channel_id(db, channel_id))
                     .with_for_update().execution_options(populate_existing=True))


def locked_subscription(db: Session, channel_id: str, user_id: int) -> ChannelSubscription | None:
    return db.scalar(select(ChannelSubscription).where(ChannelSubscription.channel_id == channel_id,
                     ChannelSubscription.user_id == user_id).with_for_update().execution_options(populate_existing=True))


def enabled_state(channel: SmartChannel, config: ChannelPushConfig, enabled: bool, managed: bool) -> None:
    config.enabled = enabled
    if not managed:
        channel.state = "active" if enabled else "paused"


def admin_channel_read(db: Session, user: UserAccount, channel: SmartChannel, config: ChannelPushConfig, managed: bool):
    binding = active_wechat_binding(db, user.staff_id)
    subscription = db.get(ChannelSubscription, (channel.id, user.id))
    allowed = subscription_allowed(channel, user.id)
    return dict(**channel_push_settings(channel, config), can_delete=not managed, subscription_allowed=allowed,
                subscribed=allowed and subscription_matches(subscription, binding))


@router.get("/catalog")
def catalog(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    binding = active_wechat_binding(db, user.staff_id)
    managed = select(SmartChannelAlias.alias_id).where(SmartChannelAlias.canonical_id == SmartChannel.id).exists()
    rows = db.execute(active_channel_catalog().add_columns(ChannelPushConfig, ChannelSubscription, managed)
                      .outerjoin(ChannelPushConfig, ChannelPushConfig.channel_id == SmartChannel.id)
                      .outerjoin(ChannelSubscription, (ChannelSubscription.channel_id == SmartChannel.id)
                                 & (ChannelSubscription.user_id == user.id))).all()
    channels = []
    for channel, config, subscription, is_managed_channel in rows:
        settings = channel_push_settings(channel, config)
        if user.role != "admin":
            settings.update(source_url=None, selector=None, include_terms=[], exclude_terms=[])
        allowed = subscription_allowed(channel, user.id)
        channels.append(dict(**settings, can_delete=user.role == "admin" and not is_managed_channel,
                             subscription_allowed=allowed, subscribed=allowed and subscription_matches(subscription, binding)))
    archived_ids = db.scalars(select(SmartChannel.id).where(SmartChannel.archived_at.is_not(None))
                              .order_by(SmartChannel.id)).all()
    return dict(wechat_bound=binding is not None, can_manage=user.role == "admin", management_supported=True,
                audience_supported=True,
                archived_ids=archived_ids, channels=channels)


def audience_members():
    return select(UserAccount.id, Staff.name, UserAccount.username).join(Staff, Staff.id == UserAccount.staff_id).where(
        UserAccount.enabled.is_(True), Staff.enabled.is_(True), Staff.removed_at.is_(None),
    )


@router.get("/configs/{channel_id}/audience")
def get_audience(channel_id: str, _user: UserAccount = Depends(require_channel_admin),
                 db: Session = Depends(get_db)):
    channel = db.get(SmartChannel, resolve_channel_id(db, channel_id))
    if channel is None or channel.archived_at is not None:
        raise HTTPException(404, "频道不存在")
    members = db.execute(audience_members().order_by(Staff.name, UserAccount.username)).all()
    return {
        "allowed_user_ids": channel.subscription_user_ids,
        "members": [dict(id=id_, name=name, username=username) for id_, name, username in members],
    }


@router.put("/configs/{channel_id}/audience")
def save_audience(channel_id: str, payload: ChannelAudience, user: UserAccount = Depends(require_channel_admin),
                  db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    channel = locked_channel(db, channel_id)
    if channel is None or channel.archived_at is not None:
        raise HTTPException(404, "频道不存在")
    current = channel.subscription_user_ids
    allowed = payload.allowed_user_ids
    if current != payload.previous_user_ids and current != allowed:
        raise HTTPException(409, "订阅权限已被更新，请重新加载后再编辑")
    if current != allowed:
        if allowed is not None:
            members = db.scalars(audience_members().where(UserAccount.id.in_(allowed))).all()
            if len(members) != len(allowed):
                raise HTTPException(422, "名单包含已停用或移除的账号，请重新加载人员列表")
            db.execute(delete(ChannelSubscription).where(ChannelSubscription.channel_id == channel.id,
                                                        ChannelSubscription.user_id.not_in(allowed)))
        channel.subscription_user_ids = allowed
    db.commit()
    return {"subscription_allowed": subscription_allowed(channel, user.id)}


@router.post("/configs")
def save_config(payload: ChannelPushDraft, user: UserAccount = Depends(require_channel_admin),
                db: Session = Depends(get_db)):
    if payload.source_type == "website":
        try:
            validate_website_config(payload.source_url or "", payload.selector or "")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    lock_sqlite_writer(db)
    if payload.id:
        channel = locked_channel(db, payload.id)
        if channel is None or channel.archived_at is not None:
            raise HTTPException(404, "频道不存在")
    else:
        channel = SmartChannel(id=str(uuid4()), name=payload.name, description=payload.description,
                               schedule=payload.schedule, max_items=payload.max_items,
                               state="active" if payload.enabled else "paused")
        db.add(channel)
        db.flush()

    config = db.get(ChannelPushConfig, channel.id, with_for_update=True)
    if config is None:
        config = ChannelPushConfig(channel_id=channel.id)
        db.add(config)
    fields = payload.model_dump(exclude={"id"})
    if payload.source_type == "articles":
        fields.update(source_url=None, selector=None)
    else:
        fields.update(include_terms=[], exclude_terms=[])
    for key, value in fields.items():
        setattr(config, key, value)
    # Keep the reading list and older clients on the same display name.
    channel.name = payload.name
    channel.description = payload.description
    managed = is_managed(db, channel.id)
    enabled_state(channel, config, payload.enabled, managed)
    db.commit()
    return admin_channel_read(db, user, channel, config, managed)


@router.put("/configs/{channel_id}/enabled")
def set_enabled(channel_id: str, payload: ChannelEnabled, user: UserAccount = Depends(require_channel_admin),
                db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    channel = locked_channel(db, channel_id)
    if channel is None or channel.archived_at is not None:
        raise HTTPException(404, "频道不存在")
    config = db.get(ChannelPushConfig, channel.id, with_for_update=True)
    if config is None:
        values = channel_push_settings(channel, None)
        values.pop("id")
        config = ChannelPushConfig(channel_id=channel.id, **values)
        db.add(config)
    managed = is_managed(db, channel.id)
    enabled_state(channel, config, payload.enabled, managed)
    db.commit()
    return admin_channel_read(db, user, channel, config, managed)


@router.delete("/configs/{channel_id}", status_code=204, response_class=Response)
def archive_channel(channel_id: str, _user: UserAccount = Depends(require_channel_admin),
                    db: Session = Depends(get_db)) -> Response:
    lock_sqlite_writer(db)
    channel = locked_channel(db, channel_id)
    if channel is None:
        raise HTTPException(404, "频道不存在")
    if is_managed(db, channel.id):
        raise HTTPException(409, "此频道由服务器统一管理，不能从客户端删除")
    if channel.archived_at is None:
        channel.archived_at = utcnow()
        channel.state = "paused"
        config = db.get(ChannelPushConfig, channel.id, with_for_update=True)
        if config is not None:
            config.enabled = False
    db.commit()
    return Response(status_code=204)


@router.put("/order")
def update_order(payload: ChannelOrder, _user: UserAccount = Depends(require_channel_admin),
                 db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    # Lock channels in ID order before evaluating the order supplied by the client.
    db.scalars(select(SmartChannel.id).order_by(SmartChannel.id).with_for_update()).all()
    channels = db.scalars(active_channel_catalog().with_for_update()).all()
    current_ids = [channel.id for channel in channels]
    if payload.previous_ids != current_ids:
        raise HTTPException(409, "频道列表已更新，请重新开始排序")
    if len(payload.channel_ids) != len(current_ids) or set(payload.channel_ids) != set(current_ids):
        raise HTTPException(422, "排序必须包含当前的全部频道，且每个频道只能出现一次")
    positions = {channel_id: index for index, channel_id in enumerate(payload.channel_ids)}
    for channel in channels:
        channel.display_order = positions[channel.id]
    db.commit()
    return {"channel_ids": payload.channel_ids}


@router.put("/{channel_id}/subscription")
def subscribe(channel_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    lock_sqlite_writer(db)
    channel = locked_channel(db, channel_id)
    if channel is None or channel.archived_at is not None:
        raise HTTPException(404, "频道不存在")
    if not subscription_allowed(channel, user.id):
        raise HTTPException(403, "管理员未向你的账号开放此频道的订阅权限")
    if not channel_push_settings(channel, db.get(ChannelPushConfig, channel.id))["enabled"]:
        raise HTTPException(409, "该频道已暂停微信推送")
    subscription = locked_subscription(db, channel.id, user.id)
    binding = active_wechat_binding(db, user.staff_id, lock=True)
    if binding is None:
        raise HTTPException(409, "请联系管理员为你的账号绑定一个有效的微信接收渠道")
    if not subscription_matches(subscription, binding):
        if subscription is None:
            subscription = ChannelSubscription(channel_id=channel.id, user_id=user.id)
            db.add(subscription)
        subscription.binding_id = binding.id
        subscription.binding_fingerprint = binding_fingerprint(binding)
        subscription.created_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not subscription_matches(db.get(ChannelSubscription, (channel.id, user.id)),
                                    active_wechat_binding(db, user.staff_id)):
            raise
    return {"subscribed": True}


@router.delete("/{channel_id}/subscription")
def unsubscribe(channel_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    channel = locked_channel(db, channel_id)
    if channel is not None:
        subscription = locked_subscription(db, channel.id, user.id)
        if subscription is not None:
            db.delete(subscription)
    db.commit()
    return {"subscribed": False}
