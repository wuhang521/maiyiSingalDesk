import hashlib
import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.orm import Session

from .auth import require_user, require_staff_admin
from .config import get_settings
from .database import get_db
from .knowledge_extract import Extraction, IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, TEXT_EXTENSIONS, text_extraction
from .knowledge_ocr import OCR_EXTENSIONS, configuration as ocr_configuration
from .knowledge_asr import configuration as cloud_asr_configuration, silk_configuration
from .knowledge_models import KnowledgeAsset, KnowledgeChunk, KnowledgeItem, KnowledgeJob, KnowledgeRevision, KnowledgeOrganization, KnowledgeSemanticIndex
from .knowledge_service import (apply_extraction, ask, capacity, check_revision, create_item, enqueue,
                                find_evidence, item_read, new_id, owned_item, purge_item)
from .models import UserAccount, utcnow
from .knowledge_service import PAID_RECOGNITION_MODES
from .knowledge_usage import lock_paid_owner, reserve as reserve_paid, usage as paid_usage
from .knowledge_organization import configuration as organization_configuration, organize, read_result as organization_read
from .knowledge_video_ocr import VIDEO_EXTENSIONS, configuration as video_ocr_configuration
from .knowledge_cloud import ask_cloud, cloud_configuration
from .knowledge_semantic import configuration as semantic_configuration, build_index, info as semantic_info, search as semantic_search

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class ItemCreate(BaseModel):
    title: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=4000)
    content: str = Field(default="", max_length=1_000_000)
    publisher: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def tags_valid(cls, tags):
        if any(len(tag) > 40 for tag in tags):
            raise ValueError("标签不能超过 40 字符")
        return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))


class ItemUpdate(ItemCreate):
    revision: int = Field(ge=1)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class PurgeRequest(RevisionRequest):
    confirmation: str = Field(pattern="^永久删除$")


class OcrRequest(RevisionRequest):
    model_config = ConfigDict(extra="forbid")
    cloud_consent: StrictBool

    @field_validator("cloud_consent")
    @classmethod
    def explicit_consent(cls, value):
        if not value:
            raise ValueError("OCR 需要明确同意发送整份选中的原始文件到豆包云端")
        return value


class OcrConfirm(RevisionRequest):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["已核对"]


class AsrRequest(RevisionRequest):
    model_config = ConfigDict(extra="forbid")
    cloud_consent: StrictBool

    @field_validator("cloud_consent")
    @classmethod
    def explicit_consent(cls, value):
        if not value:
            raise ValueError("云端转写需要明确同意发送所选素材的音轨，可能产生费用")
        return value


class OrganizationRequest(RevisionRequest):
    model_config = ConfigDict(extra="forbid")
    cloud_consent: StrictBool

    @field_validator("cloud_consent")
    @classmethod
    def explicit_consent(cls, value):
        if not value:
            raise ValueError("AI 整理需要明确同意发送这份资料的全部当前正文，可能产生费用")
        return value


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=500)
    mode: Literal["extractive", "cloud"] = "extractive"
    cloud_consent: bool = False

    @model_validator(mode="after")
    def requires_consent(self):
        if self.mode == "cloud" and not self.cloud_consent:
            raise ValueError("云端问答需要明确同意发送相关资料片段")
        return self


class SemanticIndexRequest(RevisionRequest):
    model_config = ConfigDict(extra="forbid")
    cloud_consent: StrictBool

    @field_validator("cloud_consent")
    @classmethod
    def explicit_consent(cls, value):
        if not value:
            raise ValueError("语义索引需要明确同意发送这份当前正文到云端向量模型，可能产生费用")
        return value


class SemanticSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=500)
    cloud_consent: StrictBool

    @field_validator("cloud_consent")
    @classmethod
    def explicit_consent(cls, value):
        if not value:
            raise ValueError("语义检索需要明确同意发送本次问题到云端向量模型，可能产生费用")
        return value


def storage_root():
    root = Path(get_settings().knowledge_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@router.get("/capabilities")
def capabilities(user: UserAccount = Depends(require_user)):
    s = get_settings()
    return dict(asr_enabled=bool(s.knowledge_asr_enabled and s.knowledge_asr_command), ocr_enabled=ocr_configuration(),
                cloud_asr_enabled=cloud_asr_configuration(), cloud_asr_provider="豆包语音 / 火山引擎",
                cloud_asr_max_minutes=s.knowledge_cloud_asr_max_minutes,
                organization_enabled=organization_configuration(), organization_provider=cloud_configuration()[1],
                video_ocr_enabled=video_ocr_configuration(), video_ocr_max_frames=s.knowledge_video_ocr_max_frames,
                usage_available=True,
                semantic_enabled=semantic_configuration(),
                teams_available=True, notifications_available=True, media_merge_available=True,
                semantic_max_chunks=s.knowledge_semantic_max_chunks, costs_available=True,
                silk_available=silk_configuration(),
                ocr_provider="豆包 / 火山方舟", ocr_max_pdf_pages=s.knowledge_ocr_max_pdf_pages,
                max_upload_mb=s.knowledge_max_upload_mb, scope="private", qa_mode="extractive",
                trash_retention_days=s.knowledge_trash_retention_days,
                cloud_qa_enabled=cloud_configuration()[0], cloud_provider=cloud_configuration()[1])


@router.get("/items")
def list_items(q: str = Query(default="", max_length=200), state: str = Query(default="active", pattern="^(active|inbox|removed)$"),
               limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
               user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    filters = [KnowledgeItem.owner_id == user.id,
               KnowledgeItem.deleted_at.is_not(None) if state == "removed" else KnowledgeItem.deleted_at.is_(None)]
    if state == "inbox":
        filters.append(KnowledgeItem.status != "ready")
    if q.strip():
        filters.append(or_(KnowledgeItem.title.contains(q.strip(), autoescape=True),
                           KnowledgeItem.content.contains(q.strip(), autoescape=True),
                           KnowledgeItem.publisher.contains(q.strip(), autoescape=True),
                           KnowledgeItem.tags.cast(String).contains(q.strip(), autoescape=True)))
    total = db.scalar(select(func.count(KnowledgeItem.id)).where(*filters)) or 0
    items = db.scalars(select(KnowledgeItem).where(*filters).order_by(KnowledgeItem.updated_at.desc(), KnowledgeItem.id).limit(limit).offset(offset)).all()
    return dict(items=[item_read(item) for item in items], total=total)


@router.post("/items", status_code=201)
def collect(payload: ItemCreate, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return item_read(create_item(db, user.id, payload), db, True)


@router.get("/items/{item_id}")
def read_item(item_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return item_read(owned_item(db, user.id, item_id, removed=True), db, True)


@router.get("/items/{item_id}/revisions")
def list_revisions(item_id: str, before: int | None = Query(default=None, ge=1), limit: int = Query(default=20, ge=1, le=50),
                   user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    owned_item(db, user.id, item_id, removed=True)
    query = select(KnowledgeRevision).where(KnowledgeRevision.item_id == item_id)
    if before is not None:
        query = query.where(KnowledgeRevision.revision < before)
    rows = db.scalars(query.order_by(KnowledgeRevision.revision.desc()).limit(limit + 1)).all()
    total = db.scalar(select(func.count(KnowledgeRevision.id)).where(KnowledgeRevision.item_id == item_id)) or 0
    return dict(items=[dict(revision=r.revision, method=r.method, created_at=r.created_at) for r in rows[:limit]],
                total=total, next_before=rows[limit - 1].revision if len(rows) > limit else None)


@router.get("/items/{item_id}/revisions/{revision}")
def read_revision(item_id: str, revision: int, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    owned_item(db, user.id, item_id, removed=True)
    version = db.scalar(select(KnowledgeRevision).where(KnowledgeRevision.item_id == item_id, KnowledgeRevision.revision == revision))
    if not version:
        raise HTTPException(404, "正文版本不存在")
    return dict(revision=version.revision, content=version.content, method=version.method, created_at=version.created_at)


@router.post("/items/{item_id}/purge")
def purge(item_id: str, payload: PurgeRequest, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return purge_item(db, user.id, item_id, payload.revision)


@router.patch("/items/{item_id}")
def update_item(item_id: str, payload: ItemUpdate, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id, lock=True)
    check_revision(item, payload.revision)
    if not payload.title.strip():
        raise HTTPException(422, "资料标题不能为空")
    if not payload.content.strip():
        raise HTTPException(422, "正文不能为空；仅保存链接请使用收集功能。")
    item.title, item.publisher, item.tags = payload.title.strip(), payload.publisher.strip(), payload.tags
    item.revision += 1
    apply_extraction(db, item, text_extraction(payload.content))
    db.execute(KnowledgeJob.__table__.update().where(KnowledgeJob.item_id == item.id,
               KnowledgeJob.state.in_(["queued", "running", "retry_wait"])).values(state="cancelled"))
    db.commit()
    return item_read(item, db, True)


@router.post("/items/{item_id}/assets")
async def upload(item_id: str, request: Request, filename: str = Query(max_length=200, min_length=1), revision: int = Query(ge=1),
                 user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return await store_upload(item_id, request, filename, revision, user, db)


async def store_upload(item_id, request, filename, revision, user, db, *, expected_sha256=None):
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "文件名称无效")
    extension = Path(filename).suffix.lower()
    if extension not in TEXT_EXTENSIONS | MEDIA_EXTENSIONS | IMAGE_EXTENSIONS:
        raise HTTPException(422, "支持 JPG、PNG、TXT、Markdown、HTML、PDF、SRT、VTT 及常见音视频。")
    item = owned_item(db, user.id, item_id)
    check_revision(item, revision)
    capacity(db)
    settings = get_settings()
    owner_id = user.id
    maximum = min(settings.knowledge_max_upload_mb, 500 if extension in MEDIA_EXTENSIONS else 20) * 1024 * 1024
    quota = settings.knowledge_user_quota_mb * 1024 * 1024
    used = db.scalar(select(func.coalesce(func.sum(KnowledgeAsset.size), 0)).join(KnowledgeItem, KnowledgeAsset.item_id == KnowledgeItem.id).where(KnowledgeItem.owner_id == user.id))
    root = storage_root()
    if shutil.disk_usage(root).free < maximum + 128 * 1024 * 1024:
        raise HTTPException(507, "后台可用空间不足，请联系管理员；未覆盖原有资料。")
    # Do not hold a DB transaction while waiting for the client upload.
    db.rollback()
    descriptor, temporary = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=root)
    size, digest, final = 0, hashlib.sha256(), None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            incoming = request.stream().__aiter__(); deadline = time.monotonic() + 300
            while True:
                if time.monotonic() >= deadline: raise HTTPException(408, "原件上传超过 5 分钟，未提交文件；请拆分后手动上传")
                try: block = await asyncio.wait_for(incoming.__anext__(), timeout=max(.05, deadline - time.monotonic()))
                except StopAsyncIteration: break
                except asyncio.TimeoutError: raise HTTPException(408, "原件上传超过 5 分钟，未提交文件；请检查网络或拆分，不自动重试") from None
                size += len(block)
                if size > maximum or used + size > quota:
                    raise HTTPException(413, "文件过大或个人知识库空间已满，请拆分文件后重试。")
                stream.write(block)
                digest.update(block)
            stream.flush()
            os.fsync(stream.fileno())
        if size == 0:
            raise HTTPException(422, "不能上传空文件")
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise HTTPException(422, "附件内容与可信消息签名不符，未保存文件；请重新发送原件。")
        # Begin the final transaction with the per-owner lock BEFORE consistent reads;
        # MySQL REPEATABLE READ must not retain a pre-lock quota snapshot.
        active_user = db.scalar(select(UserAccount).where(UserAccount.id == owner_id).with_for_update().execution_options(populate_existing=True))
        if not active_user or not active_user.enabled or not active_user.staff.enabled or active_user.staff.removed_at:
            raise HTTPException(403, "上传期间账号已停用，未继续保存附件。")
        item = owned_item(db, owner_id, item_id, lock=True)
        check_revision(item, revision)
        previous = db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.item_id == item_id, KnowledgeAsset.sha256 == digest.hexdigest()))
        if previous:
            return item_read(item, db, True)
        used = db.scalar(select(func.coalesce(func.sum(KnowledgeAsset.size), 0)).join(KnowledgeItem, KnowledgeAsset.item_id == KnowledgeItem.id).where(KnowledgeItem.owner_id == owner_id))
        if used + size > quota:
            raise HTTPException(413, "个人知识库空间已满，请联系管理员。")
        asset_id = new_id()
        final = root / f"{asset_id}{extension}"
        os.replace(temporary, final)
        if os.name != "nt":
            final.chmod(0o600)
        asset = KnowledgeAsset(id=asset_id, item_id=item.id, filename=filename, storage_name=final.name, sha256=digest.hexdigest(), size=size)
        db.add(asset)
        item.revision += 1
        enqueue(db, item, asset_id)
        db.commit()
        final = None  # Committed originals must not be cleaned up.
        return item_read(item, db, True)
    finally:
        Path(temporary).unlink(missing_ok=True)
        if final:
            db.rollback()
            final.unlink(missing_ok=True)


@router.get("/assets/{asset_id}")
def download(asset_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    asset = db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.id == asset_id))
    if not asset:
        raise HTTPException(404, "文件不存在")
    owned_item(db, user.id, asset.item_id)
    root = storage_root()
    path = (root / asset.storage_name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "原始文件暂不可用，请联系管理员检查文件备份。")
    return FileResponse(path, filename=asset.filename, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/items/{item_id}/retry")
def retry(item_id: str, payload: RevisionRequest, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id, lock=True)
    check_revision(item, payload.revision)
    asset = db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.item_id == item.id).order_by(KnowledgeAsset.created_at.desc()).limit(1))
    if not asset and item.kind != "article":
        raise HTTPException(422, "请先上传视频 / 字幕或补充正文。视频号链接无法直接转写。")
    item.revision += 1
    enqueue(db, item, asset.id if asset else None)
    db.commit()
    return item_read(item, db, True)


@router.post("/items/{item_id}/assets/{asset_id}/ocr")
def request_ocr(item_id: str, asset_id: str, payload: OcrRequest,
                user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    user = lock_paid_owner(db, user)
    item = owned_item(db, user.id, item_id, lock=True)
    check_revision(item, payload.revision)
    asset = db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.id == asset_id, KnowledgeAsset.item_id == item.id))
    if not asset:
        raise HTTPException(404, "原始文件不存在或无权访问")
    if Path(asset.filename).suffix.lower() not in OCR_EXTENSIONS or asset.size > 20 * 1024 * 1024:
        raise HTTPException(422, "请选择不超过 20 MB 的 JPG、PNG 或 PDF 原件。")
    if not ocr_configuration():
        raise HTTPException(503, "后台尚未启用豆包 OCR；原件已保存，请联系管理员。")
    if db.scalar(select(KnowledgeJob.id).where(KnowledgeJob.item_id == item.id, KnowledgeJob.mode.in_(PAID_RECOGNITION_MODES),
            KnowledgeJob.state.in_(["queued", "running", "retry_wait"]))):
        raise HTTPException(409, "OCR 已提交，不能重复发送。请等待处理完成。")
    item.revision += 1
    job = enqueue(db, item, asset.id, mode="doubao_ocr")
    reserve_paid(db, user.id, job.id, "doubao_ocr")
    item.status_note = "已明确同意将选中的整份原件发送到豆包云端，等待 OCR；不会自动再次提交付费识别。"
    db.commit()
    return item_read(item, db, True)


@router.post("/items/{item_id}/assets/{asset_id}/asr")
def request_asr(item_id: str, asset_id: str, payload: AsrRequest,
                user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    user = lock_paid_owner(db, user)
    item = owned_item(db, user.id, item_id, lock=True)
    check_revision(item, payload.revision)
    asset = db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.id == asset_id, KnowledgeAsset.item_id == item.id))
    if not asset:
        raise HTTPException(404, "原始文件不存在或无权访问")
    if Path(asset.filename).suffix.lower() not in MEDIA_EXTENSIONS:
        raise HTTPException(422, "请选择音频或视频原件；分享链接不能直接转写。")
    if not cloud_asr_configuration():
        raise HTTPException(503, "后台尚未配置独立云端语音识别权限或转换工具；原件已保留。")
    if Path(asset.filename).suffix.lower() == ".silk":
        if not silk_configuration(): raise HTTPException(503, "原生微信语音已保存，但本地 SILK 解码器未就绪；未预留费用、未提交云模型")
    if db.scalar(select(KnowledgeJob.id).where(KnowledgeJob.item_id == item.id,
            KnowledgeJob.mode.in_(PAID_RECOGNITION_MODES), KnowledgeJob.state.in_(["queued", "running", "retry_wait"]))):
        raise HTTPException(409, "已有付费识别在处理，请勿重复提交。")
    item.revision += 1
    job = enqueue(db, item, asset.id, mode="volc_asr")
    reserve_paid(db, user.id, job.id, "volc_asr")
    item.status_note = "已同意本地提取音轨并发送到豆包语音，等待转写；不自动重复付费提交。"
    db.commit()
    return item_read(item, db, True)


@router.get("/usage")
def own_usage(user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return paid_usage(db, user.id)


@router.post("/items/{item_id}/assets/{asset_id}/video-ocr")
def request_video_ocr(item_id: str, asset_id: str, payload: OcrRequest,
                      user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    user=lock_paid_owner(db,user)
    item=owned_item(db,user.id,item_id,lock=True); check_revision(item,payload.revision)
    asset=db.scalar(select(KnowledgeAsset).where(KnowledgeAsset.id==asset_id,KnowledgeAsset.item_id==item.id))
    if not asset:
        raise HTTPException(404,"视频原件不存在或无权访问")
    if Path(asset.filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(422,"请选择视频原件；音频或分享链接没有可采样画面。")
    if not video_ocr_configuration():
        raise HTTPException(503,"视频画面 OCR 尚未启用、缺少 OCR 凭据或转换工具")
    if db.scalar(select(KnowledgeJob.id).where(KnowledgeJob.item_id==item.id,
        KnowledgeJob.mode.in_(PAID_RECOGNITION_MODES),KnowledgeJob.state.in_(["queued","running","retry_wait"]))):
        raise HTTPException(409,"已有付费识别正在处理，请勿重复提交。")
    item.revision+=1
    job=enqueue(db,item,asset.id,mode="video_ocr"); reserve_paid(db,user.id,job.id,"video_ocr")
    item.status_note="已同意将采样画面发送豆包 OCR，非完整视频识别；原正文保留在历史，不自动重试。"
    db.commit(); return item_read(item,db,True)


@router.post("/items/{item_id}/video-ocr/confirm")
def confirm_video_ocr(item_id: str, payload: OcrConfirm, user: UserAccount=Depends(require_user),db: Session=Depends(get_db)):
    return confirm_recognition(item_id,payload,user,db,"video_ocr_draft","video_ocr_reviewed","采样画面 OCR")


@router.get("/items/{item_id}/organization")
def read_organization(item_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item=owned_item(db,user.id,item_id)
    row=db.scalar(select(KnowledgeOrganization).where(KnowledgeOrganization.item_id==item.id,
        KnowledgeOrganization.source_revision==item.revision).order_by(KnowledgeOrganization.created_at.desc()).limit(1))
    return dict(result=organization_read(row))


@router.post("/items/{item_id}/organization")
async def request_organization(item_id: str, payload: OrganizationRequest,
                               user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return await organize(db,user,item_id,payload.revision)


@router.get("/admin/operations")
def operations(principal=Depends(require_staff_admin), db: Session = Depends(get_db)):
    # Aggregate metadata only; no titles, excerpts, user IDs or source links.
    from .knowledge_models import KnowledgeFileCleanup, KnowledgeRemoteCleanup
    from .knowledge_dispatch import observations
    return dict(**observations(db),
                file_cleanup_pending=db.scalar(select(func.count(KnowledgeFileCleanup.id))) or 0,
                remote_cleanup_pending=db.scalar(select(func.count(KnowledgeRemoteCleanup.id))) or 0)


@router.get("/items/{item_id}/semantic-index")
def read_semantic_index(item_id: str, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id)
    return semantic_info(db.get(KnowledgeSemanticIndex, item_id), item.revision)


@router.post("/items/{item_id}/semantic-index")
async def create_semantic_index(item_id: str, payload: SemanticIndexRequest,
                                user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return await build_index(db, user, item_id, payload.revision)


@router.delete("/items/{item_id}/semantic-index")
def forget_semantic_index(item_id: str, revision: int = Query(ge=1),
                           user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id, lock=True); check_revision(item, revision)
    db.execute(delete(KnowledgeSemanticIndex).where(KnowledgeSemanticIndex.item_id == item_id)); db.commit()
    return dict(state="absent", source_revision=revision, chunk_count=0)


@router.post("/semantic-search")
async def query_semantic(payload: SemanticSearchRequest,
                         user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return await semantic_search(db, user, payload.question.strip())


def confirm_recognition(item_id, payload, user, db, draft_method, reviewed_method, name):
    item = owned_item(db, user.id, item_id, lock=True)
    check_revision(item, payload.revision)
    if item.status != "needs_review" or item.method != draft_method:
        raise HTTPException(409, f"当前资料没有待核对的{name}正文，请刷新后重试。")
    chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.item_id == item.id,
                       KnowledgeChunk.revision == item.revision).order_by(KnowledgeChunk.ordinal)).all()
    if not chunks or not item.content.strip():
        raise HTTPException(409, f"{name}正文不完整，请手动补充正文。")
    item.revision += 1
    apply_extraction(db, item, Extraction(item.content, reviewed_method, [(c.locator, c.content) for c in chunks]))
    item.status_note = f"{name}正文已由你核对入库；Hermes 可检索引用。AI 识别仍可能有误，请以原件为准。"
    db.commit()
    return item_read(item, db, True)


@router.post("/items/{item_id}/asr/confirm")
def confirm_asr(item_id: str, payload: OcrConfirm, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return confirm_recognition(item_id, payload, user, db, "volc_asr_draft", "volc_asr_reviewed", "音频转写")


@router.post("/items/{item_id}/ocr/confirm")
def confirm_ocr(item_id: str, payload: OcrConfirm, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return confirm_recognition(item_id, payload, user, db, "doubao_ocr_draft", "doubao_ocr_reviewed", "OCR")


@router.delete("/items/{item_id}")
def remove(item_id: str, revision: int = Query(ge=1), user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id, lock=True)
    check_revision(item, revision)
    item.deleted_at, item.updated_at = utcnow(), utcnow()
    db.execute(KnowledgeJob.__table__.update().where(KnowledgeJob.item_id == item.id,
               KnowledgeJob.state.in_(["queued", "running", "retry_wait"])).values(state="cancelled"))
    item.revision += 1
    db.commit()
    return item_read(item, db, True)


@router.post("/items/{item_id}/restore")
def restore(item_id: str, payload: RevisionRequest, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    item = owned_item(db, user.id, item_id, removed=True, lock=True)
    check_revision(item, payload.revision)
    if not item.deleted_at:
        return item_read(item, db, True)
    item.deleted_at, item.updated_at = None, utcnow()
    item.revision += 1
    if item.content.strip() and item.status == "ready":
        chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.item_id == item.id).order_by(KnowledgeChunk.ordinal)).all()
        result = text_extraction(item.content, item.method)
        if chunks:
            result.chunks = [(chunk.locator, chunk.content) for chunk in chunks]
        apply_extraction(db, item, result)
    else:
        item.status, item.status_note = "needs_input", "资料已恢复，请补充正文或重试识别。"
    db.commit()
    return item_read(item, db, True)


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=500), user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    return dict(items=find_evidence(db, user.id, q))


@router.post("/ask")
async def question(payload: AskRequest, user: UserAccount = Depends(require_user), db: Session = Depends(get_db)):
    if payload.mode == "cloud":
        return await ask_cloud(db, user.id, payload.question)
    return ask(db, user.id, payload.question)
