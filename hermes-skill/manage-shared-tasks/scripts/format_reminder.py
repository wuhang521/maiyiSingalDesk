#!/usr/bin/env python3
"""Format one owner's complete task list into a compact Chinese reminder."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def due_label(value: str | None, now: datetime) -> tuple[str, bool]:
    due_at = parse_time(value)
    if not due_at:
        return "未设置", False
    formatted = due_at.strftime("%m-%d %H:%M")
    return (f"{formatted}（已逾期）", True) if due_at < now else (formatted, False)


def normalized_tasks(payload: dict) -> list[dict]:
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        return [item for item in tasks if isinstance(item, dict)]
    # 兼容旧版单任务事件。
    return [payload]


def sort_key(task: dict, now: datetime) -> tuple:
    due_at = parse_time(task.get("due_at"))
    overdue = bool(due_at and due_at < now)
    return (
        0 if overdue else 1,
        -int(task.get("priority") or 2),
        due_at or datetime.max.replace(tzinfo=SHANGHAI),
        str(task.get("task_no") or ""),
    )


def task_icon(task: dict, now: datetime) -> str:
    due_at = parse_time(task.get("due_at"))
    if due_at and due_at < now:
        return "🔴"
    if int(task.get("priority") or 2) == 3:
        return "🟠"
    return "•"


def format_reminder(payload: dict) -> str:
    tasks = normalized_tasks(payload)
    if not tasks:
        raise ValueError("任务列表为空")
    for task in tasks:
        if not str(task.get("task_no") or "").strip():
            raise ValueError("任务缺少 task_no")
        if not str(task.get("title") or "").strip():
            raise ValueError("任务缺少 title")

    now = datetime.now(SHANGHAI)
    tasks.sort(key=lambda item: sort_key(item, now))
    owner = str(payload.get("assignee") or tasks[0].get("assignee") or "未指定").strip()
    overdue_count = sum(
        1 for task in tasks if parse_time(task.get("due_at")) and parse_time(task.get("due_at")) < now
    )
    urgent_count = sum(1 for task in tasks if int(task.get("priority") or 2) == 3)

    summary = []
    if overdue_count:
        summary.append(f"逾期 {overdue_count}")
    if urgent_count:
        summary.append(f"紧急 {urgent_count}")
    summary_text = f"（{'、'.join(summary)}）" if summary else ""
    is_new_task = payload.get("event") == "task.created"
    if is_new_task:
        lines = [f"【新任务通知】{owner}，你收到 {len(tasks)} 项新任务", ""]
    else:
        lines = [f"【待办提醒】{owner}，你还有 {len(tasks)} 项任务{summary_text}", ""]

    for index, task in enumerate(tasks, start=1):
        task_no = str(task["task_no"]).strip()
        title = str(task["title"]).strip()
        due_text, _ = due_label(task.get("due_at"), now)
        lines.append(f"{task_icon(task, now)} {index}. {task_no} {title}｜{due_text}")

    creator_name = str(payload.get("creator_name") or "").strip()
    if is_new_task and creator_name:
        lines.extend(["", f"安排人：{creator_name}"])

    lines.extend(
        [
            "",
            f"回复“完成 T编号”标记完成，如“完成 {tasks[0]['task_no']}”",
            f"回复“详情 T编号”查看要求，如“详情 {tasks[0]['task_no']}”",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        print(format_reminder(payload))
        return 0
    except Exception as exc:
        print(f"提醒格式化失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
