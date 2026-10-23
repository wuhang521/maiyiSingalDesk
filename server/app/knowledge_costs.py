"""Configured CNY ceilings plus immutable manual bill reconciliation, not price guesses."""
from decimal import Decimal, InvalidOperation
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .auth import require_user
from .config import get_settings
from .database import get_db
from .knowledge_models import KnowledgeCloudUsage, KnowledgeCostReservation
from .models import UserAccount, utcnow
router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-costs"])
OPERATIONS = {"doubao_ocr", "volc_asr", "video_ocr", "cloud_organize", "semantic_index", "semantic_query", "cloud_qa"}

def micros(value):
    if not isinstance(value, str) or len(value) > 30: raise ValueError()
    try: amount = Decimal(value)
    except InvalidOperation: raise ValueError() from None
    if not amount.is_finite() or amount < 0 or amount > 1000000 or amount * 1000000 != (amount * 1000000).to_integral_value(): raise ValueError()
    return int(amount * 1000000)

def cny(value): return f"{Decimal(value) / 1000000:.6f}"

def pricing():
    s = get_settings()
    try:
        raw = json.loads(s.knowledge_request_ceiling_cny)
        if not isinstance(raw, dict) or len(raw) > 20 or set(raw) - OPERATIONS: raise ValueError()
        return micros(s.knowledge_budget_daily_cny), micros(s.knowledge_budget_monthly_cny), {k: micros(v) for k, v in raw.items()}
    except (ValueError, TypeError): raise HTTPException(503, "人民币预算 / 单次最高预留配置无效，未发送新模型请求；请联系管理员") from None

def exposure(db, owner_id, since):
    # MySQL has no aggregate FILTER syntax: calculate unknown records separately.
    base = select(KnowledgeCloudUsage.id).where(KnowledgeCloudUsage.owner_id == owner_id, KnowledgeCloudUsage.created_at >= since)
    total = db.scalar(select(func.coalesce(func.sum(func.coalesce(KnowledgeCostReservation.actual_micros, KnowledgeCostReservation.reserved_micros, 0)), 0))
        .select_from(KnowledgeCloudUsage).outerjoin(KnowledgeCostReservation).where(KnowledgeCloudUsage.id.in_(base))) or 0
    unknown = db.scalar(select(func.count()).select_from(KnowledgeCloudUsage).outerjoin(KnowledgeCostReservation).where(KnowledgeCloudUsage.id.in_(base),
        KnowledgeCostReservation.actual_micros.is_(None), KnowledgeCostReservation.reserved_micros.is_(None))) or 0
    return int(total), unknown

def reserve_money(db, owner_id, usage_id, operation):
    s = get_settings(); amount = None
    if s.knowledge_money_budget_enabled:
        from .knowledge_usage import periods
        day, month, _ = periods(); daily_limit, monthly_limit, prices = pricing()
        if operation not in prices or not prices[operation]: raise HTTPException(503, "本操作没有有效的单次最高费用预留，请管理员配置后再提交")
        daily, day_unknown = exposure(db, owner_id, day); monthly, month_unknown = exposure(db, owner_id, month)
        if day_unknown or month_unknown: raise HTTPException(409, "当前账期存在未定价的历史提交，请先核对账单；人民币预算不把未知费用当作零")
        amount = prices[operation]
        if daily + amount > daily_limit or monthly + amount > monthly_limit: raise HTTPException(429, "本次费用预留将超出人民币日 / 月预算，未发起新模型请求")
    return KnowledgeCostReservation(usage_id=usage_id, reserved_micros=amount)

@router.get("/costs")
def overview(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    from .knowledge_usage import periods
    day, month, _ = periods(); daily, du = exposure(db, user.id, day); monthly, mu = exposure(db, user.id, month)
    enabled = get_settings().knowledge_money_budget_enabled
    dl, ml, prices = pricing() if enabled else (0, 0, {})
    return dict(enabled=enabled, daily_exposure_cny=cny(daily), monthly_exposure_cny=cny(monthly), daily_limit_cny=cny(dl), monthly_limit_cny=cny(ml),
        daily_unpriced=du, monthly_unpriced=mu, request_ceiling_cny={k: cny(v) for k, v in prices.items()},
        accounting="configured_ceiling_or_manually_reconciled_invoice", timezone="UTC")

@router.get("/costs/ledger")
def ledger(offset: int = Query(default=0, ge=0), user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.execute(select(KnowledgeCloudUsage, KnowledgeCostReservation).outerjoin(KnowledgeCostReservation).where(KnowledgeCloudUsage.owner_id == user.id)
        .order_by(KnowledgeCloudUsage.created_at.desc(), KnowledgeCloudUsage.id).offset(offset).limit(50)).all()
    from .knowledge_provider_trace import traces
    metadata = traces(db, [u.id for u, _ in rows])
    return dict(items=[dict(provider_requests=metadata[u.id], **read_row(u, c)) for u, c in rows])

def read_row(u, c):
    return dict(usage_id=u.id, operation=u.operation, created_at=u.created_at,
        reserved_cny=cny(c.reserved_micros) if c and c.reserved_micros is not None else None,
        actual_cny=cny(c.actual_micros) if c and c.actual_micros is not None else None,
        state="manually_reconciled" if c and c.actual_micros is not None else "reserved_not_invoice" if c and c.reserved_micros is not None else "unpriced",
        reconciled_at=c.reconciled_at if c else None)

class BillRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    usage_id: str = Field(pattern="^[a-f0-9]{32}$")
    amount_cny: str = Field(pattern="^(0|[1-9][0-9]{0,6})(\\.[0-9]{1,6})?$")
    invoice_reference: str = Field(min_length=1, max_length=160)

class BillImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rows: list[BillRow] = Field(min_length=1, max_length=100)
    verified_provider_bill: StrictBool

@router.get("/admin/costs/ledger")
def admin_ledger(offset: int = Query(default=0, ge=0), user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(403, "仅个人管理员可以核对费用")
    rows = db.execute(select(KnowledgeCloudUsage, KnowledgeCostReservation).outerjoin(KnowledgeCostReservation)
        .order_by(KnowledgeCloudUsage.created_at.desc(), KnowledgeCloudUsage.id).offset(offset).limit(50)).all()
    from .knowledge_provider_trace import traces
    metadata = traces(db, [u.id for u, _ in rows])
    return dict(items=[dict(owner_id=u.owner_id, provider_requests=metadata[u.id], **read_row(u, c)) for u, c in rows])

@router.post("/admin/costs/reconcile")
def reconcile(payload: BillImport, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    if user.role != "admin": raise HTTPException(403, "仅个人管理员可以核对费用")
    if not payload.verified_provider_bill: raise HTTPException(422, "需逐行核对服务商账单与本地提交；模型返回 token 数不等同账单金额")
    if len({r.usage_id for r in payload.rows}) != len(payload.rows): raise HTTPException(422, "提交编号重复")
    admin_id = user.id; db.rollback()
    rows = db.scalars(select(KnowledgeCloudUsage).where(KnowledgeCloudUsage.id.in_([r.usage_id for r in payload.rows]))).all()
    if len(rows) != len(payload.rows): raise HTTPException(404, "账单包含不存在的提交编号")
    accounts = {}
    for owner in sorted({r.owner_id for r in rows} | {admin_id}):
        accounts[owner] = db.scalar(select(UserAccount).where(UserAccount.id == owner).with_for_update().execution_options(populate_existing=True))
    user = accounts[admin_id]
    if not user or not user.enabled or user.role != "admin": raise HTTPException(403, "管理员账号权限已改变，未保存账单")
    changed = 0
    for incoming in payload.rows:
        try: actual = micros(incoming.amount_cny)
        except ValueError: raise HTTPException(422, "费用必须是有限、非负人民币金额，最多 6 位小数") from None
        reference = incoming.invoice_reference.strip()
        if not reference or any(ord(c) < 32 for c in reference): raise HTTPException(422, "账单行引用无效")
        cost = db.scalar(select(KnowledgeCostReservation).where(KnowledgeCostReservation.usage_id == incoming.usage_id).with_for_update().execution_options(populate_existing=True))
        if cost and cost.actual_micros is not None:
            if cost.actual_micros == actual and cost.invoice_reference == reference: continue
            raise HTTPException(409, "已有对账记录不可覆盖，请联系管理员处理账单更正")
        if not cost: cost = KnowledgeCostReservation(usage_id=incoming.usage_id); db.add(cost)
        cost.actual_micros = actual; cost.invoice_reference = reference; cost.reconciled_by = user.id; cost.reconciled_at = utcnow(); changed += 1
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409, "账单行已用于其他提交，整批没有保存") from None
    return dict(reconciled=changed, accounting="manually_verified_provider_bill")
