"""Private originals and versioned evidence; chunks are rebuildable projections."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import utcnow

large_text = Text().with_variant(LONGTEXT(), "mysql")
date_type = DateTime().with_variant(DATETIME(fsp=6), "mysql")


class KnowledgeItem(Base):
    __tablename__ = "knowledge_item"
    __table_args__ = (UniqueConstraint("owner_id", "source_key", name="uq_knowledge_source"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    source_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30), default="note")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="received", index=True)
    status_note: Mapped[str] = mapped_column(String(500), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(large_text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(40), default="")
    deleted_at: Mapped[datetime | None] = mapped_column(date_type, nullable=True)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeAsset(Base):
    __tablename__ = "knowledge_asset"
    __table_args__ = (UniqueConstraint("item_id", "sha256", name="uq_knowledge_asset"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    storage_name: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunk"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    locator: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)


class KnowledgeMediaPart(Base):
    """Immutable timestamped recognitions; later runs cannot overwrite prior parts."""
    __tablename__ = "knowledge_media_part"
    __table_args__ = (UniqueConstraint("item_id", "source_revision", name="uq_knowledge_media_part"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(40))
    source_revision: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(large_text)
    chunks: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeRevision(Base):
    __tablename__ = "knowledge_revision"
    __table_args__ = (UniqueConstraint("item_id", "revision", name="uq_knowledge_revision"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(large_text)
    method: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeJob(Base):
    __tablename__ = "knowledge_job"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mode: Mapped[str] = mapped_column(String(30), default="local", server_default="local")
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(date_type, nullable=True)
    available_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeNonce(Base):
    __tablename__ = "knowledge_nonce"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)


class KnowledgeFileCleanup(Base):
    """Durable deletion intent, independent of the already removed item."""
    __tablename__ = "knowledge_file_cleanup"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    storage_name: Mapped[str] = mapped_column(String(80), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeRemoteCleanup(Base):
    """Provider deletion intent outlives local item removal and worker crashes."""
    __tablename__ = "knowledge_remote_cleanup"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider_file_id: Mapped[str] = mapped_column(String(160), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeCloudUsage(Base):
    """Acceptance ledger has no item FK: purging content must not reset budget."""
    __tablename__ = "knowledge_cloud_usage"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)


class KnowledgeCostReservation(Base):
    """Currency admission is a configured ceiling, not a provider invoice."""
    __tablename__ = "knowledge_cost_reservation"
    usage_id: Mapped[str] = mapped_column(ForeignKey("knowledge_cloud_usage.id"), primary_key=True)
    reserved_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actual_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    invoice_reference: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    reconciled_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(date_type, nullable=True)


class KnowledgeProviderTrace(Base):
    """Bill-correlation metadata only: no request / response bodies or credentials."""
    __tablename__ = "knowledge_provider_trace"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    usage_id: Mapped[str] = mapped_column(ForeignKey("knowledge_cloud_usage.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    request_id: Mapped[str] = mapped_column(String(160), default="")
    response_id: Mapped[str] = mapped_column(String(160), default="")
    http_status: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeOrganization(Base):
    """AI notes never replace source text or silently become retrieval evidence."""
    __tablename__ = "knowledge_organization"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), index=True)
    source_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status_note: Mapped[str] = mapped_column(String(500), default="")
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)


class KnowledgeSemanticIndex(Base):
    """Explicitly consented derived projection of one current ready revision."""
    __tablename__ = "knowledge_semantic_index"

    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), primary_key=True)
    source_revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    resolved_model: Mapped[str] = mapped_column(String(200), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeTeam(Base):
    __tablename__ = "knowledge_team"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeTeamMember(Base):
    __tablename__ = "knowledge_team_member"
    team_id: Mapped[str] = mapped_column(ForeignKey("knowledge_team.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeShare(Base):
    __tablename__ = "knowledge_share"
    team_id: Mapped[str] = mapped_column(ForeignKey("knowledge_team.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_item.id"), primary_key=True)
    source_revision: Mapped[int] = mapped_column(Integer)
    shared_by: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeTeamEvent(Base):
    __tablename__ = "knowledge_team_event"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("knowledge_team.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    action: Mapped[str] = mapped_column(String(30))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgeIngressReceipt(Base):
    """Hashed message identity survives purging an imported original."""
    __tablename__ = "knowledge_ingress_receipt"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(40))  # Not an FK: deletion must not replay an old message.
    state: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)


class KnowledgePreference(Base):
    __tablename__ = "knowledge_preference"
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    desktop_notifications: Mapped[bool] = mapped_column(Boolean, default=False)
    hermes_notifications: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("staff_channel.id"), nullable=True)


class KnowledgeNotification(Base):
    __tablename__ = "knowledge_notification"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_revision: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(40))
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recipient_hash: Mapped[str] = mapped_column(String(64), default="")
    delivery_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="inbox", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(date_type, default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(date_type, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(date_type, nullable=True)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)


class KnowledgeWorkerHeartbeat(Base):
    __tablename__ = "knowledge_worker_heartbeat"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    last_seen: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)


class KnowledgeOperationalAlert(Base):
    __tablename__ = "knowledge_operational_alert"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    active_key: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(date_type, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(date_type, nullable=True)
