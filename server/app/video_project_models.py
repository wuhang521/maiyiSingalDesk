"""Persistent video production records, separate from the task domain tables."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import utcnow


class VideoProject(Base):
    __tablename__ = "video_project"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    topic: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20), default="standard")
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    locked_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VideoProjectReview(Base):
    __tablename__ = "video_project_review"
    __table_args__ = (UniqueConstraint("project_id", "revision", "stage", name="uq_video_review_stage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_project.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(20))
    quote: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VideoProjectJob(Base):
    __tablename__ = "video_project_job"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_video_job_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("video_project.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    scene_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    provider_task_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    budget_grant: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lease_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
