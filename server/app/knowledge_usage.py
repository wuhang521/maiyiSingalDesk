"""Conservative paid-recognition admission; persistent even after item deletion.

Counts accepted submissions, not provider-confirmed currency charges. Reservations
are never refunded automatically: a timeout may already have incurred a charge.
"""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from .config import get_settings
from .knowledge_models import KnowledgeCloudUsage
from .models import UserAccount, utcnow


def lock_paid_owner(db, user):
    owner_id = user.id
    # Auth's earlier consistent read must not survive the owner lock under
    # MySQL REPEATABLE READ, or concurrent requests could both see old usage.
    db.rollback()
    locked = db.scalar(select(UserAccount).where(UserAccount.id == owner_id).with_for_update()
                       .execution_options(populate_existing=True))
    if not locked or not locked.enabled or not locked.staff.enabled or locked.staff.removed_at:
        raise HTTPException(403, "账号已停用，未创建云端识别任务")
    return locked


def periods():
    # Explicit billing admission boundaries: UTC, independent of server timezone.
    now = utcnow()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return day, month, day + timedelta(days=1)


def usage(db, owner_id):
    day, month, reset = periods()
    s = get_settings()
    def count(since):
        return db.scalar(select(func.count(KnowledgeCloudUsage.id)).where(
            KnowledgeCloudUsage.owner_id == owner_id, KnowledgeCloudUsage.created_at >= since)) or 0
    daily, monthly = count(day), count(month)
    return dict(daily_submissions=daily, monthly_submissions=monthly,
                daily_limit=s.knowledge_paid_daily_limit, monthly_limit=s.knowledge_paid_monthly_limit,
                daily_remaining=max(0, s.knowledge_paid_daily_limit - daily),
                monthly_remaining=max(0, s.knowledge_paid_monthly_limit - monthly),
                next_daily_reset=reset, timezone="UTC", accounting="submission_reservations_not_currency")


def reserve(db, owner_id, job_id, operation):
    # Caller acquired owner -> item locks first, consistent with upload/purge.
    db.scalar(select(UserAccount).where(UserAccount.id == owner_id).with_for_update())
    status = usage(db, owner_id)
    if not status["daily_remaining"] or not status["monthly_remaining"]:
        raise HTTPException(429, "本账号的云端识别提交额度已用完，未创建新识别任务；请等待额度重置或联系管理员。")
    from .knowledge_costs import reserve_money
    cost = reserve_money(db, owner_id, job_id, operation)
    db.add(KnowledgeCloudUsage(id=job_id, owner_id=owner_id, operation=operation))
    db.flush()
    db.add(cost); db.flush()
