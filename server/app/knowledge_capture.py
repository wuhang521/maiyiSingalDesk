"""Idempotent manual original capture, authenticated as the current personal owner."""
import asyncio
import base64
import hashlib
from pathlib import Path
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .auth import require_user
from .database import get_db
from .knowledge import store_upload
from .knowledge_extract import TEXT_EXTENSIONS, IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, normalize_url
from .knowledge_models import KnowledgeIngressReceipt, KnowledgeItem
from .knowledge_service import item_read, new_id
from .knowledge_usage import lock_paid_owner
from .models import UserAccount
router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-capture"])

@router.post("/capture/files")
async def upload_original(request: Request, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    sha = request.headers.get("X-Content-SHA256", ""); key = request.headers.get("Idempotency-Key", "")
    try:
        encoded_source = request.headers.get("X-Source-Base64", "")
        if len(encoded_source) > 22000: raise ValueError()
        source = base64.b64decode(encoded_source, validate=True).decode("utf-8") if encoded_source else ""
        if len(source) > 4000: raise ValueError()
        source, kind = normalize_url(source)
        encoded = request.headers.get("X-Filename-Base64", "")
        if len(encoded) > 1200: raise ValueError()
        filename = base64.b64decode(encoded, validate=True).decode("utf-8")
        if not 1 <= len(filename) <= 200 or filename != Path(filename).name or "\\" in filename or "/" in filename or any(ord(c) < 32 for c in filename): raise ValueError()
        if not re.fullmatch(r"[a-f0-9]{64}", sha) or not re.fullmatch(r"[a-f0-9]{64}", key): raise ValueError()
        if Path(filename).suffix.lower() not in TEXT_EXTENSIONS | IMAGE_EXTENSIONS | MEDIA_EXTENSIONS: raise ValueError()
    except (ValueError, UnicodeError): raise HTTPException(422, "采集文件格式、名称或校验值无效") from None
    user = lock_paid_owner(db, user)
    receipt_id = hashlib.sha256(f"manual-capture:{user.id}:{key}:{sha}".encode()).hexdigest()
    receipt = db.get(KnowledgeIngressReceipt, receipt_id)
    if receipt:
        if receipt.state != "saved": raise HTTPException(409, "上次采集结果未确认，请检查知识库；不会自动重放")
        item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == receipt.item_id, KnowledgeItem.owner_id == user.id, KnowledgeItem.deleted_at.is_(None)))
        return dict(saved=True, duplicate=True, item=item_read(item) if item else None)
    if (db.scalar(select(func.count()).select_from(KnowledgeIngressReceipt).where(KnowledgeIngressReceipt.owner_id == user.id)) or 0) >= 10000:
        raise HTTPException(429, "本账号原件采集去重记录达到上限，请联系管理员")
    item = KnowledgeItem(id=new_id(), owner_id=user.id, title=filename, source_url=source, kind=kind, status="needs_input", status_note="本人授权原件正在保存；未自动调用云模型。")
    receipt = KnowledgeIngressReceipt(id=receipt_id, owner_id=user.id, item_id=item.id, state="processing")
    db.add(item); db.add(receipt); db.commit()
    try: result = await store_upload(item.id, request, filename, item.revision, user, db, expected_sha256=sha)
    except (asyncio.CancelledError, Exception):
        db.rollback(); receipt = db.get(KnowledgeIngressReceipt, receipt_id)
        if receipt: receipt.state = "unknown"; db.commit()
        raise
    receipt = db.get(KnowledgeIngressReceipt, receipt_id)
    if receipt: receipt.state = "saved"; db.commit()
    return dict(saved=True, duplicate=False, item=result)
