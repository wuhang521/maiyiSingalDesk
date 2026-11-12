"""Persistent channel aliases and the lock shared by managed-channel writers.

A channel becomes managed only when a persisted alias points to it. Ordinary
channels retain their existing CRUD behavior. Alias mappings must point directly
to a canonical channel; invalid mappings fail closed instead of creating one.
"""
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base
from .models import SmartChannel


class SmartChannelAlias(Base):
    __tablename__ = 'smart_channel_alias'

    alias_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    canonical_id: Mapped[str] = mapped_column(String(80), index=True)


def active_channel_catalog():
    return select(SmartChannel).where(
        SmartChannel.archived_at.is_(None),
        ~select(SmartChannelAlias.alias_id).where(SmartChannelAlias.alias_id == SmartChannel.id).exists(),
    ).order_by(SmartChannel.display_order.is_(None), SmartChannel.display_order, SmartChannel.name, SmartChannel.id)


def resolve_channel_id(db: Session, channel_id: str) -> str:
    alias = db.get(SmartChannelAlias, channel_id)
    if alias is None:
        return channel_id
    # A direct mapping keeps locking and migration semantics unambiguous.
    if alias.canonical_id == channel_id or db.get(SmartChannelAlias, alias.canonical_id) is not None:
        raise HTTPException(409, '频道映射无效，请联系管理员检查配置')
    return alias.canonical_id


def aliases_for(db: Session, channel_id: str) -> list[str]:
    canonical_id = resolve_channel_id(db, channel_id)
    return list(db.scalars(select(SmartChannelAlias.alias_id)
                           .where(SmartChannelAlias.canonical_id == canonical_id)
                           .order_by(SmartChannelAlias.alias_id)).all())


def is_managed(db: Session, channel_id: str) -> bool:
    return bool(aliases_for(db, channel_id))


def lock_managed_channels(db: Session, channel_ids: Iterable[str]) -> dict[str, SmartChannel]:
    """Lock canonical rows in stable order before article/channel/state writes.

    The maintenance merge uses the same canonical lock. A whole incoming batch
    takes these locks before changing any articles, avoiding opposite item-order
    deadlocks. SQLite ignores FOR UPDATE; MySQL concurrency is tested separately.
    """
    canonical_ids = sorted({resolve_channel_id(db, value) for value in channel_ids if is_managed(db, value)})
    if not canonical_ids:
        return {}
    rows = list(db.scalars(select(SmartChannel).where(SmartChannel.id.in_(canonical_ids))
                           .order_by(SmartChannel.id).with_for_update()
                           .execution_options(populate_existing=True)).all())
    if len(rows) != len(canonical_ids):
        raise HTTPException(409, '受管频道尚未配置完成，请联系管理员')
    return {row.id:row for row in rows}
