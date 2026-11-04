from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    from .router import (
        TaskRouter,
        looks_like_explicit_task_message,
    )
except ImportError:  # 仅用于直接运行本目录测试；Hermes 加载时走相对导入。
    from router import (
        TaskRouter,
        looks_like_explicit_task_message,
    )


logger = logging.getLogger(__name__)
TOKEN_TTL_SECONDS = 300


@dataclass(frozen=True)
class PendingMessage:
    text: str
    platform: str
    sender_id: str | None
    sender_name: str | None
    chat_id: str | None
    chat_type: str
    message_id: str | None


_REQUESTS: dict[str, tuple[float, PendingMessage]] = {}
_LOCK = threading.Lock()
_ROUTER: TaskRouter | None = None


async def _result_command(raw_args: str) -> str:
    global _ROUTER
    token = (raw_args or "").strip().split(" ", 1)[0]
    with _LOCK:
        item = _REQUESTS.pop(token, None)
    if not item or time.monotonic() - item[0] > TOKEN_TTL_SECONDS:
        return "任务处理结果已过期，请重新发送原指令。"

    request = item[1]

    def run() -> str:
        global _ROUTER
        if _ROUTER is None:
            _ROUTER = TaskRouter()
        reply = _ROUTER.handle(
            request.text,
            platform=request.platform,
            sender_id=request.sender_id,
            sender_name=request.sender_name,
            chat_id=request.chat_id,
            chat_type=request.chat_type,
            message_id=request.message_id,
        )
        return reply or "这句话不是可执行的任务指令，本次没有修改任何任务。"

    try:
        return await asyncio.to_thread(run)
    except Exception as exc:
        logger.exception("确定性任务路由执行失败")
        return "任务后台暂时无法连接，本次任务没有创建或修改，请稍后重试。"


def _store_request(request: PendingMessage) -> str:
    now = time.monotonic()
    token = secrets.token_urlsafe(18)
    with _LOCK:
        expired = [
            key
            for key, value in _REQUESTS.items()
            if now - value[0] > TOKEN_TTL_SECONDS
        ]
        for key in expired:
            _REQUESTS.pop(key, None)
        _REQUESTS[token] = (now, request)
    return token


def _platform_value(source: Any) -> str:
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform) or "")


def _pre_gateway_dispatch(*, event: Any, gateway: Any, **_: Any) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    if source is None:
        return None
    text = getattr(event, "text", "") or ""
    if not looks_like_explicit_task_message(text):
        return None
    request = PendingMessage(
        text=text,
        platform=_platform_value(source),
        sender_id=getattr(source, "user_id", None),
        sender_name=getattr(source, "user_name", None),
        chat_id=getattr(source, "chat_id", None),
        chat_type=getattr(source, "chat_type", None) or "",
        message_id=getattr(event, "message_id", None) or getattr(source, "message_id", None),
    )
    token = _store_request(request)
    return {"action": "rewrite", "text": f"/shared-task-result {token}"}


def register(ctx: Any) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_command(
        "shared-task-result",
        _result_command,
        description="返回确定性共享任务路由的处理结果",
        args_hint="<token>",
    )
