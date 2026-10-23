"""Idempotent original-file cleanup, and opt-in trash retention."""
from datetime import timedelta
from pathlib import Path
import re

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .knowledge_models import KnowledgeAsset, KnowledgeFileCleanup, KnowledgeItem
from .knowledge_service import purge_item
from .models import utcnow


def cleanup_once(factory=SessionLocal):
    with factory() as db:
        job = db.scalar(select(KnowledgeFileCleanup).where(KnowledgeFileCleanup.available_at <= utcnow())
                        .order_by(KnowledgeFileCleanup.available_at, KnowledgeFileCleanup.id).limit(1).with_for_update())
        if not job:
            return False
        root = Path(get_settings().knowledge_storage_dir).resolve()
        path = (root / job.storage_name).resolve()
        valid = re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{1,10}", job.storage_name) and path.parent == root and path.name == job.storage_name
        registered = db.scalar(select(KnowledgeAsset.id).where(KnowledgeAsset.storage_name == job.storage_name).limit(1))
        try:
            if not valid or registered:
                raise ValueError("Unsafe or still registered cleanup target")
            path.unlink(missing_ok=True)  # No recursive delete; retries tolerate already absent files.
            db.delete(job)
        except (OSError, ValueError):
            job.attempts += 1
            job.available_at = utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
        db.commit()
        return True


def expire_trash_once(factory=SessionLocal):
    days = get_settings().knowledge_trash_retention_days
    if not days:
        return False  # Retention is off unless an operator explicitly configures it.
    cutoff = utcnow() - timedelta(days=days)
    with factory() as db:
        candidate = db.scalar(select(KnowledgeItem).where(KnowledgeItem.deleted_at < cutoff)
                              .order_by(KnowledgeItem.deleted_at).limit(1))
        if not candidate:
            return False
        owner_id, item_id = candidate.owner_id, candidate.id
        db.rollback()  # Start locking before any MySQL repeatable-read snapshot.
        from .models import UserAccount
        db.scalar(select(UserAccount).where(UserAccount.id == owner_id).with_for_update())
        current = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == item_id).with_for_update().execution_options(populate_existing=True))
        if not current or not current.deleted_at or current.deleted_at >= cutoff:
            return False  # Restore or another purge won the race.
        purge_item(db, owner_id, item_id, current.revision)
        return True
