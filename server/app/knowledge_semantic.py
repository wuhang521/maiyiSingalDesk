"""Bounded opt-in text embeddings; originals and reviewed chunks remain truth.

Official text API, NOT multimodal: https://api.volcengine.com/api-docs/view?action=Embeddings&serviceCode=ark&version=2024-01-01
No provider retries, automatic corpus export, stale vectors or cross-owner ANN pool.
Bounded exact search: 40 chunks/document, configurable owner-scoped limit; no shared ANN pool.
"""
import asyncio
from datetime import timedelta
import hashlib
import json
import math
import re
import time

from fastapi import HTTPException
import httpx
from sqlalchemy import delete, func, select

from .config import get_settings
from .knowledge_cloud import Admission
from .knowledge_models import KnowledgeChunk, KnowledgeItem, KnowledgeSemanticIndex
from .knowledge_service import check_revision, new_id, owned_item
from .knowledge_usage import lock_paid_owner, reserve
from .models import utcnow

ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/embeddings"
admission = Admission()


def configuration():
    settings = get_settings()
    return bool(settings.knowledge_semantic_enabled and settings.knowledge_embedding_api_key.strip()
                and re.fullmatch(r"doubao-embedding(?:-large)?-text-\d{6}", settings.knowledge_embedding_model.strip()))


def normalized(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 4096:
        raise ValueError("invalid dimension")
    if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1_000_000 for value in values):
        raise ValueError("invalid coordinate")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("invalid norm")
    return [value / norm for value in values]


def validated(data, count):
    if not isinstance(data, dict) or not isinstance(data.get("model"), str) or not 0 < len(data["model"]) <= 200:
        raise ValueError("missing resolved model")
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("missing vectors")
    result = {}; dimension = None
    for row in rows:
        if not isinstance(row, dict) or type(row.get("index")) is not int or not 0 <= row["index"] < count or row["index"] in result:
            raise ValueError("invalid indexes")
        vector = normalized(row.get("embedding"))
        if dimension is not None and len(vector) != dimension:
            raise ValueError("mixed dimensions")
        dimension = len(vector); result[row["index"]] = vector
    return [result[index] for index in range(count)], data["model"]


async def embed(texts, *, transport=None, may_send=lambda: True):
    settings = get_settings()
    deadline = time.monotonic() + 20
    if not configuration() or not texts or len(texts) > 40 or any(not text.strip() or len(text.encode("utf-8")) > 5000 for text in texts):
        raise HTTPException(422, "向量化配置或文本超出限制；未发送资料。")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(18, connect=5), trust_env=False,
                                    follow_redirects=False, transport=transport) as client:
            if not may_send():
                raise HTTPException(409, "账号、正文或索引状态已改变，未发送后续向量化请求。")
            async with client.stream("POST", ENDPOINT, headers={"Authorization": f"Bearer {settings.knowledge_embedding_api_key.strip()}"},
                json=dict(model=settings.knowledge_embedding_model.strip(), input=texts, encoding_format="float")) as response:
                if response.status_code != 200:
                    from .knowledge_provider_trace import record
                    record("ark", response)
                    raise ValueError("provider rejected")
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 5_000_000 or time.monotonic() > deadline:
                        raise ValueError("response exceeded budget")
                data = json.loads(body)
                from .knowledge_provider_trace import record
                record("ark", response, data)
                result, model = validated(data, len(texts))
                if model != settings.knowledge_embedding_model.strip():
                    raise ValueError("unexpected resolved model")
                return result, model
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(502, "向量化结果未确认，未自动重试；已发出请求可能计费。原件和正文不变。") from None


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def info(row, revision):
    if not row or row.source_revision != revision:
        return dict(state="absent", chunk_count=0, source_revision=revision)
    state = row.state
    if state == "ready" and row.model != get_settings().knowledge_embedding_model.strip():
        state = "stale_model"
    if state == "processing" and row.created_at < utcnow() - timedelta(seconds=60):
        state = "unknown"
    return dict(state=state, chunk_count=row.chunk_count, source_revision=row.source_revision)


def current_indexes(owner_id):
    return select(KnowledgeSemanticIndex).join(KnowledgeItem).where(KnowledgeItem.owner_id == owner_id,
        KnowledgeItem.deleted_at.is_(None), KnowledgeItem.status == "ready",
        KnowledgeSemanticIndex.source_revision == KnowledgeItem.revision, KnowledgeSemanticIndex.state == "ready",
        KnowledgeSemanticIndex.model == get_settings().knowledge_embedding_model.strip())


async def build_index(db, user, item_id, revision):
    if not configuration():
        raise HTTPException(503, "语义检索尚未启用或缺少专用文本向量模型配置。")
    owner_id = user.id
    with admission.enter(owner_id):
        user = lock_paid_owner(db, user)
        item = owned_item(db, owner_id, item_id, lock=True); check_revision(item, revision)
        if item.status != "ready" or not item.content.strip() or len(item.content) > 24000:
            raise HTTPException(422, "请先核对入库，单份正文最多 24000 字符；超限请拆分，不自动只发送前半部分。")
        existing = db.get(KnowledgeSemanticIndex, item.id)
        if existing and info(existing, revision)["state"] == "ready":
            return info(existing, revision)  # Idempotent, no cost reservation.
        if existing and existing.state == "processing" and existing.created_at > utcnow() - timedelta(seconds=60):
            raise HTTPException(409, "语义索引正在处理，请勿重复提交。")
        chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.item_id == item.id,
            KnowledgeChunk.revision == revision).order_by(KnowledgeChunk.ordinal)).all()
        if not chunks or len(chunks) > 40 or sum(len(chunk.content) for chunk in chunks) > 30000 or any(len(chunk.content.encode("utf-8")) > 5000 for chunk in chunks):
            raise HTTPException(422, "完整正文索引超过单次限制，请拆分资料；未发送资料。")
        count = db.scalar(select(func.coalesce(func.sum(KnowledgeSemanticIndex.chunk_count), 0)).join(KnowledgeItem).where(
            KnowledgeItem.owner_id == owner_id, KnowledgeItem.deleted_at.is_(None), KnowledgeItem.status == "ready",
            KnowledgeSemanticIndex.source_revision == KnowledgeItem.revision, KnowledgeSemanticIndex.state.in_(["ready", "processing"]),
            KnowledgeSemanticIndex.item_id != item_id, KnowledgeSemanticIndex.model == get_settings().knowledge_embedding_model.strip()))
        limit = get_settings().knowledge_semantic_max_chunks
        if count + len(chunks) > limit:
            raise HTTPException(413, f"本人语义索引超过 {limit} 个片段，请移除不需要的语义索引；原文保留。")
        snapshot = [(chunk.id, fingerprint(chunk.content), chunk.content) for chunk in chunks]
        request_id = new_id(); model = get_settings().knowledge_embedding_model.strip()
        db.execute(delete(KnowledgeSemanticIndex).where(KnowledgeSemanticIndex.item_id == item_id))
        db.add(KnowledgeSemanticIndex(item_id=item_id, source_revision=revision, request_id=request_id, state="processing",
            model=model, chunk_count=len(chunks), payload=[]))
        reserve(db, owner_id, request_id, "semantic_index"); db.commit()
        def may_send():
            try:
                lock_paid_owner(db, user)
                current = owned_item(db, owner_id, item_id)
                pending = db.get(KnowledgeSemanticIndex, item_id)
                return bool(current.revision == revision and current.status == "ready" and pending
                    and pending.request_id == request_id and pending.state == "processing")
            except HTTPException:
                return False
            finally:
                db.rollback()  # Never hold the identity/item transaction across HTTP.
        try:
            from .knowledge_provider_trace import span
            with span(request_id, db=db):
                vectors, resolved = await asyncio.wait_for(embed([row[2] for row in snapshot], may_send=may_send), timeout=20)
        except (asyncio.CancelledError, Exception) as error:
            row = db.get(KnowledgeSemanticIndex, item_id)
            if row and row.request_id == request_id:
                row.state = "unknown"; row.payload = []; db.commit()
            if isinstance(error, (asyncio.CancelledError, HTTPException)):
                raise
            raise HTTPException(502, "语义索引结果未确认，未自动重试；原文不变，再次建立索引可能再次计费。") from None
        user = lock_paid_owner(db, user)
        item = owned_item(db, owner_id, item_id, lock=True); check_revision(item, revision)
        row = db.get(KnowledgeSemanticIndex, item_id)
        if not row or row.request_id != request_id or item.status != "ready" or model != get_settings().knowledge_embedding_model.strip():
            raise HTTPException(409, "识别期间正文、账号或索引配置已改变，没有恢复旧索引。")
        row.payload = [dict(chunk_id=chunk_id, sha256=digest, vector=vector) for (chunk_id, digest, _), vector in zip(snapshot, vectors)]
        row.state = "ready"; row.resolved_model = resolved; db.commit()
        return info(row, revision)


async def search(db, user, question):
    if not configuration():
        raise HTTPException(503, "语义检索尚未配置，可使用关键词与原文摘录。")
    if not question.strip():
        raise HTTPException(422, "请输入问题或关键词。")
    owner_id = user.id
    with admission.enter(owner_id):
        user = lock_paid_owner(db, user)
        if not db.scalar(current_indexes(owner_id).limit(1)):
            return dict(mode="semantic", answer="还没有当前版本的语义索引。请在资料详情主动建立索引，或改用关键词检索。", citations=[])
        metadata(db, owner_id)  # Reject over-cap corpora before paying for the question.
        request_id = new_id(); reserve(db, owner_id, request_id, "semantic_query"); db.commit()
        try:
            from .knowledge_provider_trace import span
            with span(request_id, db=db): query, resolved = await asyncio.wait_for(embed([question]), timeout=20)
        except TimeoutError:
            raise HTTPException(502, "语义检索超时，未自动重试；本次提交预留未自动退还。") from None
        user = lock_paid_owner(db, user)
        document_ids = [row[0] for row in metadata(db, owner_id)]
        ranked = []; deadline = time.monotonic() + 10
        # At most eight document payloads in memory, and one chunk query per batch.
        for begin in range(0, len(document_ids), 8):
            ids = document_ids[begin:begin + 8]
            rows = db.scalars(current_indexes(owner_id).where(KnowledgeSemanticIndex.item_id.in_(ids))).all()
            pairs = {row.item_id: row.source_revision for row in rows}; chunks_by_id = {}
            for chunk in db.scalars(select(KnowledgeChunk).join(KnowledgeItem).where(KnowledgeItem.owner_id == owner_id,
                KnowledgeItem.deleted_at.is_(None), KnowledgeItem.status == "ready", KnowledgeChunk.revision == KnowledgeItem.revision,
                KnowledgeChunk.item_id.in_(ids))):
                if chunk.revision == pairs.get(chunk.item_id): chunks_by_id[chunk.id] = chunk
            try:
                best = await asyncio.wait_for(asyncio.to_thread(rank_batch, rows, chunks_by_id, query[0], resolved), timeout=max(.05, deadline - time.monotonic()))
            except TimeoutError: raise HTTPException(504, "本地语义匹配超过时间预算，请减少索引；不自动重新发送问题") from None
            ranked = sorted(ranked + best, key=lambda row: (-row[0], row[1]))[:5]
        user = lock_paid_owner(db, user)  # Fresh source/model/consent checks after CPU work.
        citations = []
        for score, chunk_id, item_id, revision, source_request, sha in ranked:
            chunk = db.scalar(select(KnowledgeChunk).join(KnowledgeItem).join(KnowledgeSemanticIndex,
                KnowledgeSemanticIndex.item_id == KnowledgeItem.id).where(KnowledgeItem.owner_id == owner_id,
                KnowledgeItem.status == "ready", KnowledgeItem.deleted_at.is_(None), KnowledgeChunk.id == chunk_id,
                KnowledgeChunk.revision == revision, KnowledgeItem.revision == revision,
                KnowledgeSemanticIndex.state == "ready", KnowledgeSemanticIndex.source_revision == revision,
                KnowledgeSemanticIndex.request_id == source_request, KnowledgeSemanticIndex.model == get_settings().knowledge_embedding_model.strip()))
            if not chunk or fingerprint(chunk.content) != sha: continue
            item = owned_item(db, owner_id, chunk.item_id)
            citations.append(dict(index=len(citations) + 1, item_id=item.id, chunk_id=chunk.id, revision=chunk.revision,
                title=item.title, source_url=item.source_url, locator=chunk.locator, text=chunk.content, score=round(score, 4)))
        text = "\n\n".join(f"[{row['index']}] {row['title']} · {row['locator']}\n{row['text']}" for row in citations)
        return dict(mode="semantic", answer=text or "当前授权索引中没有足够相似的片段，请换问法或改用关键词。",
                    citations=citations)


def metadata(db, owner_id):
    limit = get_settings().knowledge_semantic_max_chunks
    rows = db.execute(current_indexes(owner_id).with_only_columns(KnowledgeSemanticIndex.item_id,
        KnowledgeSemanticIndex.source_revision, KnowledgeSemanticIndex.chunk_count).limit(limit + 1)).all()
    if any(not 1 <= row[2] <= 40 for row in rows): raise HTTPException(409, "语义索引计数无效，请移除异常索引后重新授权建立")
    if sum(row[2] for row in rows) > limit or len(rows) > limit:
        raise HTTPException(413, "当前语义索引超过个人检索限制，请移除部分索引。")
    return rows


def rank_batch(rows, chunks, question, model):
    """CPU-only, no SQL or network in the worker thread; retain only five winners."""
    ranked = []
    for row in rows:
        if row.resolved_model != model or not isinstance(row.payload, list) or len(row.payload) > 40: continue
        for entry in row.payload:
            if not isinstance(entry, dict): continue
            vector = entry.get("vector"); chunk = chunks.get(entry.get("chunk_id"))
            if not isinstance(vector, list) or len(vector) != len(question) or not chunk or chunk.item_id != row.item_id or chunk.revision != row.source_revision or fingerprint(chunk.content) != entry.get("sha256"): continue
            try: score = sum(left * right for left, right in zip(question, normalized(vector)))
            except (ValueError, TypeError, OverflowError): continue
            if score >= .25:
                ranked.append((score, chunk.id, chunk.item_id, chunk.revision, row.request_id, entry["sha256"]))
                ranked = sorted(ranked, key=lambda value: (-value[0], value[1]))[:5]
    return ranked
