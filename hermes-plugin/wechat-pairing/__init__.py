"""Deterministic WeChat pairing for authorized Hermes private conversations."""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


COMMAND = re.compile(r"^绑定迈医(?:\s|$)")
CODE = re.compile(r"^绑定迈医\s+([A-Za-z0-9_-]{32})$")
PEER = re.compile(r"^[A-Za-z0-9_.-]+@im\.wechat$")
TTL_SECONDS = 300
UNAVAILABLE = "迈医协同暂时无法验证绑定，请稍后重试。"
CLAIM_ERRORS = {
    "绑定指令已失效，请在电脑上重新生成二维码",
    "此绑定指令已由一个微信会话验证",
    "这个微信已关联其他迈医协同账号，请联系管理员核对",
    "请向已接入迈医协同的 Hermes 微信机器人发送绑定指令",
}


@dataclass(frozen=True)
class PendingPairing:
    text: str
    recipient: str | None
    profile: str | None
    deadline: float


_PENDING: dict[str, PendingPairing] = {}
_LOCK = Lock()


def _api_setting(name: str) -> str:
    if value := os.environ.get(name):
        return value
    home = os.environ.get("HERMES_HOME")
    locations = [Path(home) / ".env"] if home else []
    locations.extend([Path("/opt/data/.env"), Path.home() / ".hermes/.env"])
    for path in locations:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def _submit_claim(code: str, recipient: str, profile: str) -> str:
    base = _api_setting("TASK_API_BASE_URL").rstrip("/")
    key = _api_setting("TASK_API_KEY")
    if not base or not key:
        return UNAVAILABLE
    data = json.dumps({"code": code, "recipient_id": recipient, "profile": profile}).encode()
    request = Request(base + "/api/v1/wechat-pairing/claim", data=data,
                      headers={"Content-Type": "application/json", "X-SignalDesk-Key": key})
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("detail")
        except (AttributeError, ValueError):
            return UNAVAILABLE
        return detail if isinstance(detail, str) and detail in CLAIM_ERRORS else UNAVAILABLE
    except (URLError, OSError, ValueError):
        return UNAVAILABLE
    if isinstance(result, dict) and result.get("state") == "ready":
        return "微信已验证，请回到迈医协同客户端确认绑定。"
    return UNAVAILABLE


def _receiving_profile(source, gateway) -> str | None:
    if getattr(getattr(gateway, "config", None), "multiplex_profiles", False):
        from gateway.session_identity import transport_profile_of

        return transport_profile_of(source)
    active_profile = getattr(gateway, "_active_profile_name", None)
    return getattr(gateway, "_primary_profile_name", None) or (active_profile() if callable(active_profile) else None)


def _pre_gateway_dispatch(*, event, gateway, **_) -> dict[str, str] | None:
    text = (getattr(event, "text", "") or "").strip()
    source = getattr(event, "source", None)
    if source is None or not COMMAND.match(text):
        return None
    if getattr(event, "internal", False) or getattr(source, "is_bot", False):
        return {"action": "skip", "reason": "WeChat pairing requires a human sender"}
    platform = getattr(source, "platform", None)
    sender = getattr(source, "user_id", None)
    private = (getattr(platform, "value", platform) == "weixin" and getattr(source, "chat_type", None) == "dm"
               and isinstance(sender, str) and sender == getattr(source, "chat_id", None) and PEER.fullmatch(sender))
    profile = _receiving_profile(source, gateway) if private else None
    now = time.monotonic()
    token = secrets.token_urlsafe(18)
    with _LOCK:
        for expired in [key for key, item in _PENDING.items() if item.deadline <= now]:
            del _PENDING[expired]
        _PENDING[token] = PendingPairing(text, sender if private else None, profile, now + TTL_SECONDS)
    return {"action": "rewrite", "text": f"/signaldesk-wechat-pairing {token}"}


async def _result_command(raw_args: str) -> str:
    with _LOCK:
        pending = _PENDING.pop(raw_args.strip(), None)
    if pending is None or pending.deadline <= time.monotonic():
        return "绑定指令已失效，请在电脑上重新生成二维码。"
    if not pending.recipient:
        return "请在 Hermes 微信私聊中发送绑定指令。"
    match = CODE.fullmatch(pending.text)
    if not match:
        return "请重新扫描电脑端的二维码，复制完整的绑定指令。"
    if not pending.profile:
        return "当前机器人暂时无法验证绑定，请联系管理员。"
    return await asyncio.to_thread(_submit_claim, match[1], pending.recipient, pending.profile)


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_command("signaldesk-wechat-pairing", _result_command,
                         description="验证迈医协同的一次性微信绑定指令", args_hint="<token>")
