"""Combine only two immutable recognitions of the same owned video, without AI."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session
from .auth import require_user
from .database import get_db
from .knowledge_extract import Extraction
from .knowledge_models import KnowledgeAsset, KnowledgeJob, KnowledgeMediaPart
from .knowledge_service import apply_extraction, check_revision, item_read, owned_item
from .models import UserAccount
router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-media"])

class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    speech_id: str = Field(min_length=32, max_length=40)
    frames_id: str = Field(min_length=32, max_length=40)
    acknowledge_draft: StrictBool

@router.get("/items/{item_id}/media-parts")
def parts(item_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id)
    rows = db.scalars(select(KnowledgeMediaPart).where(KnowledgeMediaPart.item_id == item.id).order_by(KnowledgeMediaPart.created_at.desc()).limit(50)).all()
    return dict(items=[dict(id=r.id, asset_id=r.asset_id, revision=r.source_revision, method=r.method, created_at=r.created_at, content=r.content) for r in rows])

@router.post("/items/{item_id}/media-merge")
def merge(item_id: str, payload: MergeRequest, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    if not payload.acknowledge_draft: raise HTTPException(422, "合并不会校正 AI 识别错误，请确认生成待核对正文")
    item = owned_item(db, user.id, item_id, lock=True); check_revision(item, payload.revision)
    if db.scalar(select(KnowledgeJob.id).where(KnowledgeJob.item_id == item.id, KnowledgeJob.state.in_(["queued", "running", "retry_wait"]))):
        raise HTTPException(409, "识别仍在进行，完成后再合并")
    speech = db.scalar(select(KnowledgeMediaPart).where(KnowledgeMediaPart.id == payload.speech_id, KnowledgeMediaPart.item_id == item.id, KnowledgeMediaPart.method == "volc_asr_draft"))
    frames = db.scalar(select(KnowledgeMediaPart).where(KnowledgeMediaPart.id == payload.frames_id, KnowledgeMediaPart.item_id == item.id, KnowledgeMediaPart.method == "video_ocr_draft"))
    if not speech or not frames or speech.asset_id != frames.asset_id: raise HTTPException(422, "只能合并同一原视频的音频转写和画面 OCR")
    asset = db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.id == speech.asset_id, KnowledgeAsset.item_id == item.id))
    if not asset: raise HTTPException(404, "原视频已不存在")
    from pathlib import Path
    if Path(asset.filename).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}: raise HTTPException(422, "所选原件不是视频")
    text = "音频转写（独立识别，待核对）\n" + speech.content + "\n\n采样画面文字（非完整覆盖，待核对）\n" + frames.content
    if len(text) > 100000: raise HTTPException(413, "合并正文超出上限，请拆分原视频")
    chunks = [(f"{'语音' if part is speech else '画面'} · {locator}"[:100], body) for part in (speech, frames) for locator, body in part.chunks]
    if not chunks or len(chunks) > 500: raise HTTPException(413, "合并片段数量超限")
    item.revision += 1; apply_extraction(db, item, Extraction(text, "media_merge_draft", chunks))
    item.status = "needs_review"; item.status_note = "同一视频的语音与采样画面已合并；未推断两者含义对应。请核对后入库，不自动调用模型。"
    db.commit(); return item_read(item, db, True)

@router.post("/items/{item_id}/media-merge/confirm")
def confirm(item_id: str, payload: dict, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    from .knowledge import OcrConfirm, confirm_recognition
    try: parsed = OcrConfirm.model_validate(payload)
    except ValueError: raise HTTPException(422, "确认字段无效") from None
    return confirm_recognition(item_id, parsed, user, db, "media_merge_draft", "media_merge_reviewed", "媒体合并")
