from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import DateTime6, utcnow


class ChannelPushConfig(Base):
    __tablename__ = "channel_push_config"

    channel_id: Mapped[str] = mapped_column(ForeignKey("smart_channel.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule: Mapped[str] = mapped_column(String(5))
    max_items: Mapped[int]
    source_type: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text)
    selector: Mapped[str | None] = mapped_column(String(300))
    include_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclude_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow, onupdate=utcnow)


class ChannelSubscription(Base):
    __tablename__ = "channel_subscription"

    channel_id: Mapped[str] = mapped_column(ForeignKey("smart_channel.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True)
    binding_id: Mapped[int]
    binding_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)


class ChannelPushDelivery(Base):
    __tablename__ = "channel_push_delivery"
    __table_args__ = (UniqueConstraint("channel_id", "user_id", "day", name="uq_channel_push_delivery_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("smart_channel.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)
    status: Mapped[str] = mapped_column(String(20))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime6)


class ChannelWebsiteSnapshot(Base):
    __tablename__ = "channel_website_snapshot"

    channel_id: Mapped[str] = mapped_column(ForeignKey("smart_channel.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime6)
    config_updated_at: Mapped[datetime] = mapped_column(DateTime6)
    changed: Mapped[bool]
    items: Mapped[list[dict]] = mapped_column(JSON, default=list)
