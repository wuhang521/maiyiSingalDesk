from __future__ import annotations

import asyncio
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    from .dialog import (
        TaskDialogCoordinator,
        should_intercept_task_dialog,
    )
except ImportError:  # 仅用于直接运行本目录测试；Hermes 加载时走相对导入。
    from dialog import (
        TaskDialogCoordinator,
        should_intercept_task_dialog,
    )

try:
    from .maiyi_office_compat import is_office_request as _is_maiyi_office_request
except ImportError:
    from maiyi_office_compat import is_office_request as _is_maiyi_office_request

from hermes_constants import get_hermes_home


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
    profile_home: str
    source: Any
    gateway: Any


_REQUESTS: dict[str, tuple[float, PendingMessage]] = {}
_LOCK = threading.Lock()
_DIALOG_COORDINATOR = TaskDialogCoordinator()


async def _result_command(raw_args: str) -> str | None:
    token = (raw_args or "").strip().split(" ", 1)[0]
    with _LOCK:
        item = _REQUESTS.pop(token, None)
    if not item or time.monotonic() - item[0] > TOKEN_TTL_SECONDS:
        return "任务处理结果已过期，请重新发送原指令。"

    request = item[1]

    def run():
        return _DIALOG_COORDINATOR.process(
            request.text,
            profile_home=request.profile_home,
            platform=request.platform,
            sender_id=request.sender_id,
            sender_name=request.sender_name,
            chat_id=request.chat_id,
            chat_type=request.chat_type,
            message_id=request.message_id,
        )

    try:
        result = await asyncio.to_thread(run)
        contents = [result.primary]
        if result.followup:
            contents.append(result.followup)
        index = 0
        try:
            adapter = request.gateway._adapter_for_source(request.source)
            if adapter is None:
                raise RuntimeError("当前渠道适配器不可用")
            metadata = request.gateway._thread_metadata_for_source(request.source)
            for index, content in enumerate(contents):
                sent = await adapter.send(
                    request.source.chat_id,
                    content,
                    metadata=metadata,
                )
                if sent is not None and getattr(sent, "success", True) is False:
                    raise RuntimeError(getattr(sent, "error", "任务回执发送失败"))
                logger.info(
                    "任务回执已独立发送：platform=%s part=%s/%s",
                    request.platform,
                    index + 1,
                    len(contents),
                )
            return None
        except Exception:
            logger.exception("任务回执独立发送失败，改用网关回复链路兜底")
            return "\n\n---\n\n".join(contents[index:])
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


def _looks_like_authoritative_research(text: str) -> bool:
    clean = (text or "").strip()
    if not clean:
        return False
    subject = bool(
        re.search(
            r"官方网站|官方链接|官网链接|办理流程|具体流程|如何办理|怎么办理|"
            r"如何开通|怎么开通|政策依据|法规依据|备案要求|资质要求|"
            r"医疗器械注册证|注册证号|收费项目|医保政策",
            clean,
        )
    )
    request = bool(re.search(r"请|想|需要|具体|如何|怎么|哪些|什么|链接|流程|要求", clean))
    return subject and request


def _authoritative_research_prompt(text: str) -> str:
    return "\n".join(
        (
            "【权威资料查询模式】",
            f"用户原问题：{text}",
            "执行规则：",
            "1. 先确认主体、产品名称、生产厂家或注册证号是否足以唯一识别；信息不足时直接追问。",
            "2. 只采用政府部门、事业单位、官方机构、生产厂家官网或官方数据库，并给出可直接打开的链接。",
            "3. 最多尝试两种检索路径；不要启动子任务，不要用终端绕过网络或权限限制。",
            "4. 工具超时、被拒绝或提示 BLOCKED 后立即停止该路径，不得换工具重复绕过。",
            "5. 不向用户发送搜索引擎名称、工具调用、重试过程、报错或内部进度，只发送最终答案。",
            "6. 无法取得权威依据时明确说明，并请用户补充厂家、注册证号或产品链接；禁止猜测。",
            "7. 最终答复使用中文，先给结论，再列办理步骤、所需材料、主管部门和官方链接。",
        )
    )


def _looks_like_wechat_mp_work(text: str) -> bool:
    clean = (text or "").strip()
    return "公众号" in clean and bool(
        re.search(r"生成|写|修改|优化|预览|发布|推送|能不能|可以吗|怎么|如何", clean)
    )


def _wechat_mp_prompt(text: str) -> str:
    return "\n".join(
        (
            "【公众号工作模式】",
            f"用户原问题：{text}",
            "先判断当前阶段：咨询能力、补充素材、生成或修改内容、确认预览、确认发布。",
            "咨询能力时只回答，不生成、不发布；素材不足时只追问缺失信息；明确要求生成时才调用公众号内容 Skill。",
            "发布属于独立动作，必须沿用现有预览和发布确认规则，不得因为用户在讨论内容就自动发布。",
            "只向用户报告可验证结果，不展示工具调用、内部进度或英文系统提示。",
        )
    )


def _pre_gateway_dispatch(*, event: Any, gateway: Any, **_: Any) -> dict[str, str] | None:
    source = getattr(event, "source", None)
    if source is None:
        return None
    text = getattr(event, "text", "") or ""
    if re.match(r"^绑定迈医(?:\s|$)", text.strip()):
        return None
    if _is_maiyi_office_request(text):
        return None
    platform = _platform_value(source)
    sender_id = getattr(source, "user_id", None)
    chat_id = getattr(source, "chat_id", None)
    chat_type = getattr(source, "chat_type", None) or ""
    profile_home = str(get_hermes_home())
    if should_intercept_task_dialog(
        text,
        profile_home=profile_home,
        platform=platform,
        sender_id=sender_id,
        chat_id=chat_id,
        chat_type=chat_type,
    ):
        request = PendingMessage(
            text=text,
            platform=platform,
            sender_id=sender_id,
            sender_name=getattr(source, "user_name", None),
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=getattr(event, "message_id", None) or getattr(source, "message_id", None),
            profile_home=profile_home,
            source=source,
            gateway=gateway,
        )
        token = _store_request(request)
        return {"action": "rewrite", "text": f"/shared-task-result {token}"}
    if _looks_like_authoritative_research(text):
        return {"action": "rewrite", "text": _authoritative_research_prompt(text)}
    if _looks_like_wechat_mp_work(text):
        return {"action": "rewrite", "text": _wechat_mp_prompt(text)}
    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_command(
        "shared-task-result",
        _result_command,
        description="返回确定性共享任务路由的处理结果",
        args_hint="<token>",
    )
