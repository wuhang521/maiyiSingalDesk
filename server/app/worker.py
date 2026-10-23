import asyncio
import hashlib
import hmac
import json
from datetime import datetime
from collections import defaultdict

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .config import get_settings
from .database import SessionLocal
from .articles import purge_expired_article_states
from .models import NotificationOutbox, Staff, StaffChannel, Task, utcnow
from .reminders import next_reminder_at, within_reminder_window as in_reminder_window


def within_reminder_window() -> bool:
    settings = get_settings()
    return in_reminder_window(
        datetime.now().astimezone(),
        settings.reminder_start_hour,
        settings.reminder_end_hour,
    )


def queue_due_notifications() -> None:
    now = utcnow()
    settings = get_settings()
    with SessionLocal() as db:
        due_tasks = db.scalars(
            select(Task)
            .options(selectinload(Task.assignee).selectinload(Staff.channels))
            .where(
                Task.deleted_at.is_(None),
                Task.status != "done",
                Task.remind_enabled.is_(True),
                Task.next_remind_at.is_not(None),
                Task.next_remind_at <= now,
            )
            .limit(100)
        ).all()
        due_staff_ids = {task.assignee_id for task in due_tasks}
        if not due_staff_ids:
            return

        # 只要某位负责人有任务到达提醒时间，就把他全部未完成任务合并为一条。
        all_active_tasks = db.scalars(
            select(Task)
            .options(selectinload(Task.assignee).selectinload(Staff.channels))
            .where(
                Task.assignee_id.in_(due_staff_ids),
                Task.deleted_at.is_(None),
                Task.status != "done",
            )
            .order_by(Task.assignee_id, Task.priority.desc(), Task.due_at)
        ).all()
        tasks_by_staff: dict[int, list[Task]] = defaultdict(list)
        for task in all_active_tasks:
            tasks_by_staff[task.assignee_id].append(task)

        queued_staff_ids: set[int] = set()
        for staff_id, tasks in tasks_by_staff.items():
            staff = tasks[0].assignee
            if not staff.enabled or staff.removed_at:
                continue
            enabled_channels = [channel for channel in staff.channels if channel.enabled]
            if not enabled_channels:
                continue
            task_payloads = [
                {
                    "task_id": task.id,
                    "task_no": task.task_no,
                    "title": task.title,
                    "content": task.content,
                    "priority": task.priority,
                    "status": task.status,
                    "reminder_count": task.reminder_count + 1,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                }
                for task in tasks
            ]
            for channel in enabled_channels:
                recipient_hash = hashlib.sha256(
                    f"{channel.platform}:{channel.recipient_id}".encode("utf-8")
                ).hexdigest()[:20]
                minute_slot = int(now.timestamp()) // 60
                dedupe_key = f"batch:{recipient_hash}:{minute_slot}"
                payload = {
                    "event": "task.reminder.batch",
                    "assignee": staff.name,
                    "platform": channel.platform,
                    "recipient_id": channel.recipient_id,
                    "tasks": task_payloads,
                }
                db.add(
                    NotificationOutbox(
                        task_id=None,
                        platform=channel.platform,
                        recipient_id=channel.recipient_id,
                        dedupe_key=dedupe_key,
                        payload=json.dumps(payload, ensure_ascii=False),
                    )
                )
            queued_staff_ids.add(staff_id)

        # 同一负责人的任务对齐到下一次提醒，避免刚汇总完又被另一项任务触发。
        for staff_id in queued_staff_ids:
            for task in tasks_by_staff[staff_id]:
                task.last_reminded_at = now
                task.reminder_count += 1
                task.next_remind_at = next_reminder_at(
                    now,
                    task.remind_interval_minutes,
                    settings.reminder_start_hour,
                    settings.reminder_end_hour,
                )
        db.commit()


def pending_notification_groups(
    items: list[NotificationOutbox],
) -> list[tuple[list[NotificationOutbox], dict]]:
    """合并同一轮、同一收件人的新任务，避免批量创建时连续刷屏。"""
    groups: list[tuple[list[NotificationOutbox], dict]] = []
    created_groups: dict[tuple[str, ...], tuple[list[NotificationOutbox], dict]] = {}
    for item in items:
        payload = json.loads(item.payload)
        if payload.get("event") != "task.created":
            groups.append(([item], payload))
            continue
        key = (
            item.platform,
            item.recipient_id,
            str(payload.get("audience") or ""),
            str(payload.get("assignee") or ""),
            str(payload.get("creator_name") or ""),
            str(payload.get("operator_name") or ""),
        )
        group = created_groups.get(key)
        if group is None:
            group = ([item], payload)
            created_groups[key] = group
            groups.append(group)
            continue
        group[0].append(item)
        existing_numbers = {
            str(task.get("task_no") or "") for task in group[1].get("tasks") or []
        }
        for task in payload.get("tasks") or []:
            if str(task.get("task_no") or "") not in existing_numbers:
                group[1].setdefault("tasks", []).append(task)
    return groups


async def send_pending_notifications() -> None:
    settings = get_settings()
    if not settings.hermes_webhook_url:
        return
    with SessionLocal() as db:
        items = db.scalars(
            select(NotificationOutbox)
            .where(NotificationOutbox.status == "pending")
            .order_by(NotificationOutbox.id)
            .limit(100)
        ).all()
        async with httpx.AsyncClient(timeout=20) as client:
            for grouped_items, payload in pending_notification_groups(items):
                channel = db.scalar(select(StaffChannel).options(selectinload(StaffChannel.staff)).where(
                    StaffChannel.platform == grouped_items[0].platform,
                    StaffChannel.recipient_id == grouped_items[0].recipient_id,
                ))
                if channel and (not channel.enabled or not channel.staff.enabled or channel.staff.removed_at):
                    for item in grouped_items:
                        item.status = "cancelled"
                        item.last_error = "接收人员或渠道已停用"
                    db.commit()
                    continue
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                timestamp = str(int(datetime.now().timestamp()))
                signature = hmac.new(
                    settings.hermes_webhook_secret.encode("utf-8"),
                    timestamp.encode("utf-8") + b"." + body,
                    hashlib.sha256,
                ).hexdigest()
                try:
                    response = await client.post(
                        settings.hermes_webhook_url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Timestamp": timestamp,
                            "X-Webhook-Signature-V2": signature,
                        },
                    )
                    response.raise_for_status()
                    for item in grouped_items:
                        item.status = "sent"
                        item.sent_at = utcnow()
                        item.last_error = ""
                    print(
                        "[task-worker] delivered "
                        f"outbox={','.join(str(item.id) for item in grouped_items)} "
                        f"platform={grouped_items[0].platform}",
                        flush=True,
                    )
                except Exception as exc:
                    for item in grouped_items:
                        item.attempts += 1
                        item.last_error = str(exc)[:1000]
                        if item.attempts >= 10:
                            item.status = "failed"
                    print(
                        "[task-worker] delivery failed; will retry "
                        f"outbox={','.join(str(item.id) for item in grouped_items)} "
                        f"attempt={grouped_items[0].attempts} error={str(exc)[:300]}",
                        flush=True,
                    )
                db.commit()


async def run() -> None:
    settings = get_settings()
    while True:
        with SessionLocal() as db:
            if purge_expired_article_states(db):
                db.commit()
        if within_reminder_window():
            queue_due_notifications()
        # 新任务通知需要立即发送，不受定时提醒时段限制。
        await send_pending_notifications()
        await asyncio.sleep(settings.reminder_poll_seconds)


if __name__ == "__main__":
    asyncio.run(run())
