#!/usr/bin/env python3
"""Hermes webhook script: format and deliver one complete reminder batch."""

from __future__ import annotations

import json
import subprocess
import sys

from format_reminder import format_reminder


HERMES_BIN = "/opt/hermes/.venv/bin/hermes"
PLATFORM_NAMES = {
    "qq": "qqbot",
    "qqbot": "qqbot",
    "weixin": "weixin",
    "wecom": "wecom",
}
MAX_MESSAGE_CHARS = 1500


def split_message(message: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split only between lines; keep every task and never truncate."""
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in message.splitlines():
        extra = len(line) + (1 if current else 0)
        if current and current_length + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += extra
    if current:
        chunks.append("\n".join(current))
    total = len(chunks)
    return [f"【待办提醒 {index}/{total}】\n{chunk}" for index, chunk in enumerate(chunks, 1)]


def delivery_target(payload: dict) -> str:
    platform = PLATFORM_NAMES.get(str(payload.get("platform") or "").lower())
    recipient = str(payload.get("recipient_id") or "").strip()
    if not platform or not recipient:
        raise ValueError("缺少有效的 platform 或 recipient_id")
    if any(character in recipient for character in ("\n", "\r", "\0")):
        raise ValueError("recipient_id 格式无效")
    return f"{platform}:{recipient}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        target = delivery_target(payload)
        messages = split_message(format_reminder(payload))
        for message in messages:
            result = subprocess.run(
                [HERMES_BIN, "send", "--to", target, "--file", "-", "--quiet"],
                input=message,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "发送失败").strip())
        # 空输出会让 webhook 停止后续 Agent 流程，避免重复发送和模型费用。
        return 0
    except Exception as exc:
        print(f"任务提醒发送失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
