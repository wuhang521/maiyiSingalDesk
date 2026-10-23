import hashlib
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .knowledge_extract import Extraction, normalize_url, text_extraction
from .knowledge_models import KnowledgeAsset, KnowledgeChunk, KnowledgeFileCleanup, KnowledgeItem, KnowledgeJob, KnowledgeRevision, KnowledgeOrganization, KnowledgeSemanticIndex, KnowledgeShare, KnowledgeMediaPart
from .models import UserAccount, utcnow

PAID_RECOGNITION_MODES = frozenset({"doubao_ocr", "volc_asr", "video_ocr"})


def new_id():
    return uuid.uuid4().hex


def owned_item(db: Session, user_id: int, item_id: str, *, removed=False, lock=False):
    query = select(KnowledgeItem).where(KnowledgeItem.id == item_id, KnowledgeItem.owner_id == user_id)
    if not removed:
        query = query.where(KnowledgeItem.deleted_at.is_(None))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    item = db.scalar(query)
    if not item:
        raise HTTPException(404, "知识资料不存在或无权访问")
    return item


def check_revision(item, revision):
    if item.revision != revision:
        raise HTTPException(409, "资料已被更新，请刷新后重试；本次没有覆盖修改。")


def purge_item(db, user_id, item_id, revision):
    # Same owner -> item order as uploads, serializing quota changes.
    db.scalar(select(UserAccount).where(UserAccount.id == user_id).with_for_update())
    item = owned_item(db, user_id, item_id, removed=True, lock=True)
    check_revision(item, revision)
    if not item.deleted_at:
        raise HTTPException(409, "请先将资料移入回收站，才能永久删除。")
    assets = db.scalars(select(KnowledgeAsset).where(KnowledgeAsset.item_id == item_id)).all()
    for asset in assets:
        db.add(KnowledgeFileCleanup(id=new_id(), storage_name=asset.storage_name))
    for model in (KnowledgeShare, KnowledgeMediaPart, KnowledgeSemanticIndex, KnowledgeOrganization, KnowledgeJob, KnowledgeChunk, KnowledgeRevision, KnowledgeAsset):
        db.execute(delete(model).where(model.item_id == item_id))
    db.delete(item)
    db.commit()  # Record removal and file deletion intents become durable together.
    return dict(deleted=True, files_pending=len(assets))


def item_read(item, db=None, detail=False):
    data = {field: getattr(item, field) for field in (
        "id", "title", "source_url", "kind", "publisher", "tags", "status", "status_note",
        "revision", "summary", "method", "deleted_at", "created_at", "updated_at",
    )}
    if item.deleted_at:
        data["status_note"] = "已移入知识回收站，不再参与检索。原始资料仍保留，可恢复。"
        if get_settings().knowledge_trash_retention_days:
            data["status_note"] += f"后台已启用回收站保留策略：移除 {get_settings().knowledge_trash_retention_days} 天后自动永久删除。"
    if detail:
        data["content"] = item.content
        data["content_revision"] = db.scalar(select(func.max(KnowledgeRevision.revision)).where(KnowledgeRevision.item_id == item.id)) or 0
        data["assets"] = [dict(id=a.id, filename=a.filename, size=a.size) for a in db.scalars(
            select(KnowledgeAsset).where(KnowledgeAsset.item_id == item.id).order_by(KnowledgeAsset.created_at)).all()]
        data["history"] = [dict(revision=r.revision, method=r.method, created_at=r.created_at) for r in db.scalars(
            select(KnowledgeRevision).where(KnowledgeRevision.item_id == item.id).order_by(KnowledgeRevision.revision.desc()).limit(20)).all()]
    return data


def apply_extraction(db: Session, item: KnowledgeItem, result: Extraction):
    # Original revisions are immutable. Current chunks are just a replaceable projection.
    db.execute(delete(KnowledgeSemanticIndex).where(KnowledgeSemanticIndex.item_id == item.id))
    item.content = result.text
    item.summary = result.text[:300]
    item.method = result.method
    item.status = "ready"
    item.status_note = "已入库。摘要为原文节选；识别内容请与原始材料核对。"
    item.updated_at = utcnow()
    if result.title:
        item.title = result.title
    if result.publisher:
        item.publisher = result.publisher
    db.add(KnowledgeRevision(id=new_id(), item_id=item.id, revision=item.revision,
                             content=result.text, method=result.method))
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.item_id == item.id))
    db.add_all([KnowledgeChunk(id=new_id(), item_id=item.id, revision=item.revision,
                              ordinal=i, locator=loc, content=text) for i, (loc, text) in enumerate(result.chunks)])


def capacity(db: Session):
    count = db.scalar(select(func.count(KnowledgeJob.id)).where(KnowledgeJob.state.in_(["queued", "running", "retry_wait"]))) or 0
    if count >= get_settings().knowledge_queue_limit:
        raise HTTPException(429, "识别队列已满，请稍后再试；已保存的素材不会丢失。")


def enqueue(db, item, asset_id=None, mode="local"):
    capacity(db)
    db.execute(KnowledgeJob.__table__.update().where(KnowledgeJob.item_id == item.id,
               KnowledgeJob.state.in_(["queued", "running", "retry_wait"])).values(state="cancelled"))
    job = KnowledgeJob(id=new_id(), item_id=item.id, revision=item.revision, asset_id=asset_id, mode=mode)
    db.add(job)
    item.status = "received"
    item.summary = ""
    item.status_note = "已保存，等待知识库识别进程处理。"
    item.updated_at = utcnow()
    return job


def create_item(db, user_id, payload):
    try:
        url, kind = normalize_url(payload.source_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    text = payload.content.strip()
    if not url and not text and not payload.title.strip():
        raise HTTPException(422, "请提供链接、正文或文件标题。")
    source_key = hashlib.sha256(url.encode()).hexdigest() if url else None
    if source_key:
        existing = db.scalar(select(KnowledgeItem).where(KnowledgeItem.owner_id == user_id, KnowledgeItem.source_key == source_key))
        if existing:
            if existing.deleted_at:
                raise HTTPException(409, "该链接已在知识库回收站，请先恢复资料。")
            return existing
    item = KnowledgeItem(id=new_id(), owner_id=user_id, title=payload.title.strip() or ("公众号文章" if kind == "article" else "视频号素材" if kind == "channels" else "新资料"),
                         source_key=source_key, source_url=url, kind=kind, tags=payload.tags,
                         publisher=payload.publisher.strip())
    try:
        db.add(item)
        db.flush()
        if text:
            apply_extraction(db, item, text_extraction(text))
        elif kind == "article":
            enqueue(db, item)
        else:
            item.status = "needs_input"
            item.status_note = "链接已保存，请补充视频、音频或字幕。" if kind == "channels" else "请上传文件或粘贴正文。"
        db.commit()
    except IntegrityError:
        db.rollback()
        if not source_key:
            raise
        existing = db.scalar(select(KnowledgeItem).where(KnowledgeItem.owner_id == user_id, KnowledgeItem.source_key == source_key))
        if not existing or existing.deleted_at:
            raise HTTPException(409, "资料正在更新，请刷新后重试。") from None
        return existing
    return item


def search_terms(query):
    words = re.findall(r"[a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query.casefold())
    terms = []
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word) and len(word) > 4:
            terms.extend(word[i:i + 2] for i in range(len(word) - 1) if word[i:i + 2] not in {"什么", "如何", "哪些", "我的", "资料", "关于", "可以", "内容"})
        else:
            terms.append(word)
    return list(dict.fromkeys(terms))[:12]


def find_evidence(db, user_id, query, limit=8):
    terms = search_terms(query)
    if not terms:
        return []
    rows = db.execute(select(KnowledgeChunk, KnowledgeItem).join(KnowledgeItem, KnowledgeChunk.item_id == KnowledgeItem.id).where(
        KnowledgeItem.owner_id == user_id, KnowledgeItem.deleted_at.is_(None), KnowledgeItem.status == "ready",
        KnowledgeChunk.revision == KnowledgeItem.revision,
        or_(*[or_(KnowledgeChunk.content.contains(term, autoescape=True), KnowledgeItem.title.contains(term, autoescape=True)) for term in terms]),
    ).order_by(KnowledgeItem.updated_at.desc(), KnowledgeChunk.ordinal).limit(400)).all()
    ranked = sorted(rows, key=lambda row: sum(row[0].content.casefold().count(t) + 3 * row[1].title.casefold().count(t) for t in terms), reverse=True)
    return [dict(item_id=item.id, chunk_id=chunk.id, revision=chunk.revision, title=item.title,
                 source_url=item.source_url, locator=chunk.locator, text=chunk.content) for chunk, item in ranked[:limit]]


def ask(db, user_id, question):
    evidence = find_evidence(db, user_id, question)
    if not evidence:
        return dict(answer="知识库中没有找到足够依据。请换一个关键词，或先补充相关正文 / 字幕。", mode="extractive", citations=[])
    # No unsupported generation: works offline and never follows instructions in source documents.
    answer = "找到以下相关原文（这是资料摘录，不是 AI 综合结论）：\n\n" + "\n\n".join(
        f"[{i}] {row['text']}" for i, row in enumerate(evidence[:5], 1))
    return dict(answer=answer, mode="extractive", citations=[dict(index=i, **row) for i, row in enumerate(evidence[:5], 1)])
