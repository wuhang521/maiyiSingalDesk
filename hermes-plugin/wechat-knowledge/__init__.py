"""Explicit private-chat commands, trusted gateway context and signed requests only."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_PENDING = {}
_LOCK = threading.Lock()
_ATTACHMENT_MODES = {}
_SEMANTIC_CONFIRMATIONS = {}
PREFIX = re.compile(r"^\s*(收藏到我的知识库|收藏笔记到我的知识库|我的知识库搜索|我的知识库问|我的知识库状态)\s*[:：]\s*(.+?)\s*$", re.S)
BARE_WECHAT_LINK = re.compile(r"https://(?:mp\.weixin\.qq\.com|weixin\.qq\.com)/[^\s<>]+", re.I)
ATTACHMENT_MODE = re.compile(r"^我的知识库附件收藏\s*[:：]\s*(开启|关闭)$")
SEMANTIC_PREVIEW = re.compile(r"^我的知识库语义问\s*[:：]\s*(.+?)\s*$", re.S)
SEMANTIC_CONFIRM = re.compile(r"^确认语义检索\s*[:：]\s*([A-Za-z0-9_-]{20,64})$")
OPERATIONS = {"收藏到我的知识库": "collect", "收藏笔记到我的知识库": "note", "我的知识库搜索": "search", "我的知识库问": "ask", "我的知识库状态": "status"}


def connection():
    # Dedicated host-mounted file avoids altering any existing Hermes/profile .env.
    names = {"TASK_KNOWLEDGE_API_BASE_URL", "TASK_KNOWLEDGE_GATEWAY_SECRET"}
    values = {}
    path = Path("/opt/data/signaldesk-knowledge.env")
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in names:
                values[key] = value.strip()
    except OSError:
        pass
    base = os.environ.get("TASK_KNOWLEDGE_API_BASE_URL") or values.get("TASK_KNOWLEDGE_API_BASE_URL") or os.environ.get("TASK_API_BASE_URL", "")
    secret = os.environ.get("TASK_KNOWLEDGE_GATEWAY_SECRET") or values.get("TASK_KNOWLEDGE_GATEWAY_SECRET", "")
    return base.rstrip("/"), secret


def dispatch(payload):
    if payload["chat_type"] not in {"dm", "private", "direct"}:
        return "个人知识库只接受私聊指令，请私聊 Hermes 后重发；群聊中不会展示私人资料。"
    if payload["operation"] == "reply": return payload["value"]
    base, secret = connection()
    if not base or not secret:
        return "Hermes 知识库连接尚未配置。可先在客户端的“知识库”中收集资料。"
    if payload["operation"] == "attachment":
        return import_attachments(payload, base, secret)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
    signature = hmac.new(secret.encode(), timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body, hashlib.sha256).hexdigest()
    request = Request(base + "/api/v1/knowledge/gateway", data=body, headers={
        "Content-Type": "application/json", "X-Knowledge-Time": timestamp,
        "X-Knowledge-Nonce": nonce, "X-Knowledge-Signature": signature,
    }, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read(500000))
    except HTTPError as error:
        try:
            detail = json.loads(error.read(16000)).get("detail")
            return detail if isinstance(detail, str) else "知识库请求失败，请检查账号绑定或重试。"
        except ValueError:
            return "知识库请求失败，请检查后台部署。"
    except Exception:
        if payload["operation"] == "note":
            return "文字收藏结果未确认，未自动重试。请先在客户端核对是否保存，再决定是否重新发送。"
        return "知识库后台暂时无法连接。请在客户端检查是否已经保存，重复收藏同一链接会去重。"
    if result.get("message"):
        if result.get("attachment_mode"):
            key = (payload["platform"], payload["sender_id"])
            with _LOCK:
                for expired in [entry for entry, until in _ATTACHMENT_MODES.items() if until < time.monotonic()]: _ATTACHMENT_MODES.pop(expired, None)
                if result["attachment_mode"] == "enabled":
                    if len(_ATTACHMENT_MODES) >= 1000 and key not in _ATTACHMENT_MODES: return "附件收藏模式当前人数达到限制，请稍后重发开启指令。"
                    _ATTACHMENT_MODES[key] = time.monotonic() + 600
                else: _ATTACHMENT_MODES.pop(key, None)
        return result["message"]
    if result.get("semantic_ready"):
        token = secrets.token_urlsafe(24)
        with _LOCK:
            for old in [key for key, value in _SEMANTIC_CONFIRMATIONS.items() if value[0] < time.monotonic()]: _SEMANTIC_CONFIRMATIONS.pop(old, None)
            if len(_SEMANTIC_CONFIRMATIONS) >= 1000: return "待确认语义请求已满，请稍后重发。未调用模型。"
            _SEMANTIC_CONFIRMATIONS[token] = (time.monotonic() + 300, payload)
        return "语义检索会把本条问题发送到云端文本向量模型，可能计费并占用一次提交额度；只查你主动建立的当前索引，不自动上传其他资料。不同意请忽略。5 分钟内发送：\n确认语义检索：" + token
    if "answer" in result:
        citations = "\n".join(f"[{row['index']}] {row['title']} · {row['locator']} · 版本 {row['revision']} · 编号 {row['item_id']}" for row in result["citations"])
        return result["answer"] + ("\n\n出处：\n" + citations if citations else "")
    rows = result.get("items", [])
    return "\n\n".join(f"{row['title']} · {row['locator']}\n{row['text']}\n资料编号：{row['item_id']}" for row in rows) or "没有找到已识别的相关正文，请换关键词或先补充素材。"


def import_attachments(payload, base, secret):
    from gateway.platforms.base import get_image_cache_dir, get_audio_cache_dir, get_video_cache_dir, get_document_cache_dir
    roots = [getter().resolve() for getter in (get_image_cache_dir, get_audio_cache_dir, get_video_cache_dir, get_document_cache_dir)]
    results = []
    def identity(info):
        # Windows lstat reports birth time as ctime while fstat may report
        # change time. Compare shared identity fields across path/descriptor,
        # and compare each ctime only against its own initial observation.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
    for index, value in enumerate(payload["_attachments"]):
        try:
            path = Path(value)
            resolved = path.resolve()
            if not path.is_absolute() or path.is_symlink() or not any(resolved.is_relative_to(root) for root in roots):
                raise ValueError()
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1: raise ValueError()
            filename = re.sub(r"^doc_[a-f0-9]{12}_", "", path.name)
            if len(filename) > 200 or any(ord(c) < 32 for c in filename): raise ValueError()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream, tempfile.TemporaryFile() as snapshot:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 500 * 1024 * 1024
                        or identity(info) != identity(before) or path.resolve() != resolved): raise ValueError()
                digest = hashlib.sha256()
                def unchanged():
                    current, named = os.fstat(stream.fileno()), path.lstat()
                    return (identity(current) == identity(info) and current.st_ctime_ns == info.st_ctime_ns
                            and identity(named) == identity(before) and named.st_ctime_ns == before.st_ctime_ns
                            and path.resolve() == resolved)
                copied = 0
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    copied += len(block)
                    if copied > info.st_size or not unchanged(): raise ValueError()
                    digest.update(block); snapshot.write(block)
                if copied != info.st_size or not unchanged(): raise ValueError()
                metadata = {key: payload[key] for key in ("platform", "sender_id", "chat_type")}
                metadata.update(message_id=f"{payload['_message_id']}:{index}", filename=filename)
                encoded = base64.b64encode(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()).decode()
                stamp = str(int(time.time())); nonce = secrets.token_urlsafe(24); sha256 = digest.hexdigest()
                signature = hmac.new(secret.encode(), f"{stamp}\n{nonce}\n{encoded}\n{sha256}".encode(), hashlib.sha256).hexdigest()
                # Only transmit the verified private snapshot. A cache writer
                # cannot change the bytes between a later check and socket read.
                snapshot.seek(0)
                def original_chunks():
                    sent = 0
                    while True:
                        block = snapshot.read(256 * 1024)
                        if not block: break
                        sent += len(block)
                        if sent > info.st_size: raise ValueError()
                        yield block
                    if sent != info.st_size: raise ValueError()
                request = Request(base + "/api/v1/knowledge/gateway/attachment", data=original_chunks(), headers={
                    "Content-Type": "application/octet-stream", "Content-Length": str(info.st_size),
                    "X-Knowledge-Time": stamp, "X-Knowledge-Nonce": nonce, "X-Knowledge-Signature": signature,
                    "X-Knowledge-Metadata": encoded, "X-Knowledge-Content-SHA256": sha256}, method="POST")
                with urlopen(request, timeout=120) as response:
                    result = json.loads(response.read(500000))
                results.append(result.get("message", "本附件结果未确认，请查看客户端。"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read(16000)).get("detail")
                results.append(detail if isinstance(detail, str) else "附件保存失败，请先检查客户端；未自动重试。")
            except (ValueError, TypeError): results.append("附件保存失败，未自动重试。")
        except (ValueError, OSError):
            results.append("附件不是有效的适配器缓存文件、已过期或超过大小限制，未保存；请重新发送原件。")
        except Exception:
            results.append("附件保存结果未确认，未自动重试；请先查看客户端，避免重复保存。")
    return "\n\n".join(results)


async def result_command(raw_args):
    token = (raw_args or "").strip().split(" ", 1)[0]
    with _LOCK:
        item = _PENDING.pop(token, None)
    if not item or time.monotonic() - item[0] > 300:
        return "知识库指令已过期，请重新发送原指令。"
    return await asyncio.to_thread(dispatch, item[1])


def pre_gateway_dispatch(*, event, gateway, **kwargs):
    source = getattr(event, "source", None)
    text = (getattr(event, "text", "") or "").strip()
    match = PREFIX.match(text)
    bare = BARE_WECHAT_LINK.fullmatch(text)
    mode = ATTACHMENT_MODE.fullmatch(text)
    semantic_preview = SEMANTIC_PREVIEW.fullmatch(text)
    semantic_confirm = SEMANTIC_CONFIRM.fullmatch(text)
    media = getattr(event, "media_urls", None) or []
    if source is None:
        return None
    # The operation never gets sender_id from text, model arguments, or caller-selected user_id.
    platform = getattr(source, "platform", "")
    identity = dict(platform=str(getattr(platform, "value", platform) or ""),
                   sender_id=str(getattr(source, "user_id", "") or ""),
                   chat_type=str(getattr(source, "chat_type", "") or ""))
    if identity["platform"] == "qqbot": identity["platform"] = "qq"
    key = (identity["platform"], identity["sender_id"])
    with _LOCK:
        for expired in [entry for entry, until in _ATTACHMENT_MODES.items() if until < time.monotonic()]: _ATTACHMENT_MODES.pop(expired, None)
        capture = _ATTACHMENT_MODES.get(key, 0) >= time.monotonic()
    if media and (text == "收藏附件到我的知识库" or (capture and identity["chat_type"] in {"dm", "private", "direct"})):
        # Consume only explicitly requested KB attachment events; keep normal chats unchanged.
        payload = dict(identity, operation="attachment", _attachments=list(media), _message_id=str(getattr(event, "message_id", "") or secrets.token_urlsafe(24))[:200]) if isinstance(media, list) and 1 <= len(media) <= 5 and all(isinstance(path, str) for path in media) else dict(identity, operation="reply", value="每条附件消息最多 5 个文件，本条未保存；请分批重发。不自动发起模型识别。")
        event.media_urls = []; event.media_types = []
    elif mode:
        payload = dict(identity, operation="attachment_mode", value=mode[1])
    elif semantic_preview:
        payload = dict(identity, operation="semantic_preview", value=semantic_preview[1])
    elif semantic_confirm:
        with _LOCK:
            pending = _SEMANTIC_CONFIRMATIONS.get(semantic_confirm[1])
            valid = bool(pending and pending[0] > time.monotonic() and all(pending[1][k] == v for k, v in identity.items()) and identity["chat_type"] in {"dm", "private", "direct"})
            if valid: _SEMANTIC_CONFIRMATIONS.pop(semantic_confirm[1], None)
        payload = dict(identity, operation="semantic", value=pending[1]["value"], cloud_consent=True) if valid else dict(identity, operation="reply", value="语义确认码已过期、已使用或不属于当前私聊账号，请重新发起问题。")
    elif match or bare:
        payload = dict(identity, operation=OPERATIONS[match[1]] if match else "collect", value=match[2].strip() if match else text)
    else:
        return None
    now, token = time.monotonic(), secrets.token_urlsafe(24)
    with _LOCK:
        for expired in [key for key, (at, _) in _PENDING.items() if now - at > 300]:
            _PENDING.pop(expired, None)
        if len(_PENDING) >= 1000:
            return None
        _PENDING[token] = (now, payload)
    return {"action": "rewrite", "text": f"/wechat-knowledge-result {token}"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_command("wechat-knowledge-result", result_command, description="返回私人知识库指令结果", args_hint="<token>")
