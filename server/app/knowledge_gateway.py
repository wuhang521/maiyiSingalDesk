"""Hermes-only signed provenance. Never accept a model-provided owner ID."""
from datetime import timedelta
import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import get_db
from .knowledge import ItemCreate, store_upload
from .knowledge_models import KnowledgeIngressReceipt, KnowledgeItem, KnowledgeNonce
from .knowledge_extract import TEXT_EXTENSIONS, IMAGE_EXTENSIONS, MEDIA_EXTENSIONS
from .knowledge_usage import lock_paid_owner
from .knowledge_service import ask, create_item, find_evidence, item_read, new_id, owned_item
from .models import StaffChannel, UserAccount, utcnow

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge-gateway"])


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["qq", "weixin", "wecom"]
    sender_id: str = Field(min_length=1, max_length=200)
    chat_type: Literal["dm", "private", "direct"]
    operation: Literal["collect", "note", "search", "ask", "status", "attachment_mode", "semantic_preview", "semantic"]
    value: str = Field(min_length=1, max_length=4000)
    cloud_consent: StrictBool = False


async def verified_body(request: Request):
    secret = get_settings().knowledge_gateway_secret
    if not secret:
        raise HTTPException(503, "Hermes 知识库入口尚未配置")
    raw = bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw) > 16000:
            raise HTTPException(413, "请求过大")
    timestamp = request.headers.get("X-Knowledge-Time", "")
    nonce = request.headers.get("X-Knowledge-Nonce", "")
    try:
        valid_time = abs(time.time() - int(timestamp)) <= 60
    except ValueError:
        valid_time = False
    if not valid_time or not re.fullmatch(r"[a-zA-Z0-9_-]{20,64}", nonce):
        raise HTTPException(401, "知识库请求已过期或签名无效")
    expected = hmac.new(secret.encode(), timestamp.encode() + b"\n" + nonce.encode() + b"\n" + bytes(raw), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Knowledge-Signature", ""), expected):
        raise HTTPException(401, "知识库请求签名无效")
    try:
        return GatewayRequest.model_validate(json.loads(raw)), nonce
    except (ValueError, TypeError):
        raise HTTPException(422, "知识库消息格式无效") from None


@router.post("/gateway")
async def gateway(request: Request, db: Session = Depends(get_db)):
    payload, nonce = await verified_body(request)
    db.execute(delete(KnowledgeNonce).where(KnowledgeNonce.created_at < utcnow() - timedelta(minutes=5)))
    db.add(KnowledgeNonce(nonce=nonce))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "重复的知识库请求已拒绝，请重新发送指令") from None
    user = sender_user(db, payload)
    if payload.operation == "attachment_mode":
        if payload.value not in {"开启", "关闭"}: raise HTTPException(422, "附件收藏模式仅支持开启 / 关闭")
        return dict(attachment_mode="enabled" if payload.value == "开启" else "disabled",
            message="已开启私聊附件收藏，持续 10 分钟；只保存原件，不自动调用付费 OCR / 转写。发送‘我的知识库附件收藏：关闭’可提前停止。"
            if payload.value == "开启" else "已关闭附件自动收藏；此前已保存的资料不删除。")
    if payload.operation == "collect":
        result = create_item(db, user.id, ItemCreate(source_url=payload.value))
        return dict(item=item_read(result), message=f"已保存到你的个人知识库。资料编号：{result.id}。{result.status_note}")
    if payload.operation == "note":
        text = payload.value.strip()
        if not text:
            raise HTTPException(422, "请在收藏笔记指令后粘贴正文，最多 4000 字符。")
        result = create_item(db, user.id, ItemCreate(title=text.splitlines()[0][:60], content=text))
        return dict(item=item_read(result), message=f"粘贴的文字已存入你的个人知识库。资料编号：{result.id}。请自行核对来源；未自动调用模型。")
    if payload.operation == "status":
        item = owned_item(db, user.id, payload.value)
        return dict(item=item_read(item), message=f"{item.title}：{item.status_note}")
    if len(payload.value) > 500:
        raise HTTPException(422, "搜索问题不能超过 500 字符")
    if payload.operation in {"semantic_preview", "semantic"}:
        from .knowledge_semantic import configuration, search
        if not configuration(): raise HTTPException(503, "语义检索尚未配置，请先使用‘我的知识库搜索：关键词’。")
        if payload.operation == "semantic_preview": return dict(semantic_ready=True)
        if not payload.cloud_consent: raise HTTPException(422, "语义检索需要明确同意发送问题到云模型并可能计费")
        return await search(db, user, payload.value)
    if payload.operation == "search":
        return dict(items=find_evidence(db, user.id, payload.value))
    return ask(db, user.id, payload.value)


def sender_user(db, payload):
    channel = db.scalar(select(StaffChannel).options(selectinload(StaffChannel.staff)).where(
        StaffChannel.platform == payload.platform, StaffChannel.recipient_id == payload.sender_id,
        StaffChannel.enabled.is_(True)))
    if not channel or not channel.staff.enabled or channel.staff.removed_at:
        raise HTTPException(403, "消息账号尚未绑定启用人员，请在任务中心的用户管理中绑定。")
    user = db.scalar(select(UserAccount).where(UserAccount.staff_id == channel.staff_id, UserAccount.enabled.is_(True)))
    if not user:
        raise HTTPException(403, "请先为当前人员开通并启用个人客户端账号。")
    return user


class AttachmentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["qq", "weixin", "wecom"]
    sender_id: str = Field(min_length=1, max_length=200)
    chat_type: Literal["dm", "private", "direct"]
    message_id: str = Field(min_length=1, max_length=220)
    filename: str = Field(min_length=1, max_length=200)


@router.post("/gateway/attachment")
async def gateway_attachment(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().knowledge_gateway_secret
    if not secret: raise HTTPException(503, "Hermes 附件入口尚未配置")
    encoded = request.headers.get("X-Knowledge-Metadata", "")
    digest = request.headers.get("X-Knowledge-Content-SHA256", "")
    stamp = request.headers.get("X-Knowledge-Time", ""); nonce = request.headers.get("X-Knowledge-Nonce", "")
    try:
        if len(encoded) > 5000 or abs(time.time() - int(stamp)) > 60 or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", nonce) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        expected = hmac.new(secret.encode(), f"{stamp}\n{nonce}\n{encoded}\n{digest}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("X-Knowledge-Signature", ""), expected): raise ValueError()
        payload = AttachmentMessage.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        raise HTTPException(401, "附件消息签名、身份或时间无效；未读取原件") from None
    filename = payload.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if Path(filename).suffix.lower() not in TEXT_EXTENSIONS | MEDIA_EXTENSIONS | IMAGE_EXTENSIONS or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "附件格式不支持，请发送 JPG / PNG / PDF、支持的音视频或文字文件。")
    db.add(KnowledgeNonce(nonce=nonce))
    db.execute(delete(KnowledgeNonce).where(KnowledgeNonce.created_at < utcnow() - timedelta(minutes=5)))
    try: db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "重复的附件签名已拒绝") from None
    user = sender_user(db, payload); owner_id = user.id
    user = lock_paid_owner(db, user)
    receipt_id = hashlib.sha256(f"{owner_id}:{payload.platform}:{payload.message_id}:{digest}".encode()).hexdigest()
    receipt = db.get(KnowledgeIngressReceipt, receipt_id)
    if receipt:
        if receipt.state == "saved":
            item = db.scalar(select(KnowledgeItem).where(KnowledgeItem.id == receipt.item_id, KnowledgeItem.owner_id == owner_id, KnowledgeItem.deleted_at.is_(None)))
            return dict(message="该附件消息已经处理，不重复入库；原资料已被移除时也不会恢复。", item=item_read(item) if item else None)
        raise HTTPException(409, "该附件消息正在处理或结果未确认；请先查看客户端，不自动再次保存。")
    count = db.scalar(select(func.count()).select_from(KnowledgeIngressReceipt).where(KnowledgeIngressReceipt.owner_id == owner_id))
    if count >= 10000: raise HTTPException(429, "附件消息去重记录达到本账号上限，请联系管理员")
    item = KnowledgeItem(id=new_id(), owner_id=owner_id, title=filename, kind="note", status="needs_input", status_note="Hermes 原件正在保存，尚未识别。")
    receipt = KnowledgeIngressReceipt(id=receipt_id, owner_id=owner_id, item_id=item.id, state="processing")
    db.add(item); db.add(receipt); db.commit()
    try:
        result = await store_upload(item.id, request, filename, item.revision, user, db, expected_sha256=digest)
    except (asyncio.CancelledError, Exception):
        db.rollback()
        receipt = db.get(KnowledgeIngressReceipt, receipt_id)
        if receipt: receipt.state = "unknown"; db.commit()
        raise
    receipt = db.get(KnowledgeIngressReceipt, receipt_id)
    if receipt: receipt.state = "saved"; db.commit()
    return dict(item=result, message=f"原件已保存到本人知识库，资料编号：{item.id}。保存原件不代表识别完成；没有自动发起付费识别。")
