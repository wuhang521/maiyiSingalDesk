from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


LongText = Text().with_variant(mysql.LONGTEXT(), "mysql")
DateTime6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime6, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    channels: Mapped[list[StaffChannel]] = relationship(
        back_populates="staff",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="assignee")
    user: Mapped[UserAccount | None] = relationship(back_populates="staff")


class UserAccount(Base):
    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime6, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow, onupdate=utcnow)

    staff: Mapped[Staff] = relationship(back_populates="user")
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserSession(Base):
    __tablename__ = "user_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime6, index=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)

    user: Mapped[UserAccount] = relationship(back_populates="sessions")


class StaffChannel(Base):
    __tablename__ = "staff_channel"
    __table_args__ = (
        UniqueConstraint("platform", "recipient_id", name="uq_staff_channel_recipient"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    recipient_id: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    staff: Mapped[Staff] = relationship(back_populates="channels")


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_due_reminder", "status", "next_remind_at", "deleted_at"),
        UniqueConstraint(
            "source_platform",
            "source_message_id",
            name="uq_task_source_message",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_no: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text, default="")
    assignee_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    creator_name: Mapped[str] = mapped_column(String(80), default="客户端用户")
    status: Mapped[str] = mapped_column(String(20), default="todo", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remind_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remind_interval_minutes: Mapped[int] = mapped_column(Integer, default=300)
    next_remind_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_platform: Mapped[str] = mapped_column(String(20), default="client")
    source_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    assignee: Mapped[Staff] = relationship(back_populates="tasks")
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskEvent(Base):
    __tablename__ = "task_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    operator_name: Mapped[str] = mapped_column(String(80), default="系统")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    task: Mapped[Task] = relationship(back_populates="events")


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id"),
        nullable=True,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(20))
    recipient_id: Mapped[str] = mapped_column(String(160))
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SmartChannel(Base):
    __tablename__ = "smart_channel"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(LongText, default="")
    topic: Mapped[str] = mapped_column(String(200), default="")
    server: Mapped[str] = mapped_column(String(500), default="")
    schedule: Mapped[str] = mapped_column(String(50), default="")
    max_items: Mapped[int] = mapped_column(Integer, default=20)
    include_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclude_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    color: Mapped[str] = mapped_column(String(20), default="green")
    state: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow, onupdate=utcnow)


class Article(Base):
    __tablename__ = "article"

    id: Mapped[str] = mapped_column(String(191), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(LongText, default="")
    source: Mapped[str] = mapped_column(String(240), default="")
    click_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    published_at: Mapped[datetime] = mapped_column(DateTime6, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow, onupdate=utcnow)


class ArticleUserState(Base):
    __tablename__ = "article_user_state"
    __table_args__ = (
        Index("ix_article_user_state_trash", "operator_name", "deleted_at", "permanently_deleted_at"),
    )

    operator_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("article.id"), primary_key=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime6, nullable=True)
    purge_at: Mapped[datetime | None] = mapped_column(DateTime6, nullable=True)
    permanently_deleted_at: Mapped[datetime | None] = mapped_column(DateTime6, nullable=True)
    state_updated_at_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime6, default=utcnow, onupdate=utcnow)
