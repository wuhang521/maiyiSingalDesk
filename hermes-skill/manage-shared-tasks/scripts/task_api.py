#!/usr/bin/env python3
"""Deterministic CLI for the SignalDesk shared task API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


STAFF_ALIASES = {
    "杰哥": "杰哥",
    "靓仔": "杰哥",
    "敏总": "敏总",
    "姐": "敏总",
}


def env_value(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None:
        return value
    candidates = []
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        candidates.append(Path(hermes_home) / ".env")
    candidates.extend([Path("/opt/data/.env"), Path.home() / ".hermes" / ".env"])
    for path in candidates:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return default


def config() -> tuple[str, str]:
    base_url = env_value("TASK_API_BASE_URL").rstrip("/")
    api_key = env_value("TASK_API_KEY")
    if not base_url or not api_key:
        raise RuntimeError("TASK_API_BASE_URL 和 TASK_API_KEY 必须配置")
    return base_url, api_key


def request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    base_url, api_key = config()
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-SignalDesk-Key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw or f"HTTP {exc.code}"
        raise RuntimeError(str(detail)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接任务后台：{exc.reason}") from exc


def operator_name() -> str:
    return env_value("TASK_OPERATOR_NAME").strip() or "Hermes 用户"


def list_staff(_: argparse.Namespace) -> Any:
    return request("GET", "/api/v1/staff?include_disabled=true")


def resolve_staff(name: str) -> dict[str, Any]:
    name = STAFF_ALIASES.get(name.strip(), name.strip())
    staff = list_staff(argparse.Namespace())
    exact = [item for item in staff if item["name"].strip() == name]
    if len(exact) == 1:
        return exact[0]
    fuzzy = [item for item in staff if name.strip() in item["name"] or item["name"] in name.strip()]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if not fuzzy:
        raise RuntimeError(f"没有找到人员：{name}")
    names = "、".join(item["name"] for item in fuzzy)
    raise RuntimeError(f"人员不唯一，请明确选择：{names}")


def task_list(args: argparse.Namespace) -> Any:
    query = {"state": args.state, "limit": "500"}
    if args.assignee:
        query["assignee_id"] = str(resolve_staff(args.assignee)["id"])
    if args.search:
        query["search"] = args.search
    return request("GET", f"/api/v1/tasks?{urllib.parse.urlencode(query)}")


def resolve_task(task_number: str, include_deleted: bool = True) -> dict[str, Any]:
    normalized = task_number.replace(" ", "").upper()
    if normalized.startswith("T"):
        normalized = f"T{int(normalized[1:])}"
    return request(
        "GET",
        f"/api/v1/tasks/by-number/{urllib.parse.quote(normalized)}?"
        f"{urllib.parse.urlencode({'include_deleted': str(include_deleted).lower()})}",
    )


def task_detail(args: argparse.Namespace) -> Any:
    return resolve_task(args.task_number)


def task_create(args: argparse.Namespace) -> Any:
    staff = resolve_staff(args.assignee)
    payload = {
        "title": args.title,
        "content": args.content or "",
        "assignee_id": staff["id"],
        "creator_name": operator_name(),
        "priority": args.priority,
        "due_at": args.due_at,
        "remind_enabled": not args.no_reminder,
        "remind_interval_minutes": args.remind_minutes,
        "source_platform": env_value("TASK_SOURCE_PLATFORM", "hermes"),
        "source_message_id": env_value("TASK_SOURCE_MESSAGE_ID") or None,
    }
    return request("POST", "/api/v1/tasks", payload)


def task_update(args: argparse.Namespace) -> Any:
    task = resolve_task(args.task_number, include_deleted=False)
    payload: dict[str, Any] = {"operator_name": operator_name()}
    for field in ("title", "content", "priority", "due_at", "remind_interval_minutes"):
        value = getattr(args, field)
        if value is not None:
            payload[field] = value
    if args.assignee:
        payload["assignee_id"] = resolve_staff(args.assignee)["id"]
    if args.status:
        payload["status"] = args.status
    if args.reminder is not None:
        payload["remind_enabled"] = args.reminder
    return request("PATCH", f"/api/v1/tasks/{task['id']}", payload)


def task_action(args: argparse.Namespace) -> Any:
    task = resolve_task(args.task_number)
    if args.action == "delete":
        query = urllib.parse.urlencode({"operator_name": operator_name()})
        return request("DELETE", f"/api/v1/tasks/{task['id']}?{query}")
    return request(
        "POST",
        f"/api/v1/tasks/{task['id']}/{args.action}",
        {"operator_name": operator_name()},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SignalDesk 共享任务命令行工具")
    sub = parser.add_subparsers(dest="command", required=True)

    staff_parser = sub.add_parser("staff-list", help="列出人员和渠道")
    staff_parser.set_defaults(handler=list_staff)

    list_parser = sub.add_parser("task-list", help="查询任务")
    list_parser.add_argument("--state", choices=["active", "done", "deleted", "all"], default="active")
    list_parser.add_argument("--assignee")
    list_parser.add_argument("--search")
    list_parser.set_defaults(handler=task_list)

    create_parser = sub.add_parser("create", help="创建任务")
    create_parser.add_argument("--assignee", required=True)
    create_parser.add_argument("--title", required=True)
    create_parser.add_argument("--content")
    create_parser.add_argument("--priority", type=int, choices=[1, 2, 3], default=2)
    create_parser.add_argument("--due-at")
    create_parser.add_argument("--remind-minutes", type=int, default=300)
    create_parser.add_argument("--no-reminder", action="store_true")
    create_parser.set_defaults(handler=task_create)

    update_parser = sub.add_parser("update", help="修改任务")
    update_parser.add_argument("task_number")
    update_parser.add_argument("--title")
    update_parser.add_argument("--content")
    update_parser.add_argument("--assignee")
    update_parser.add_argument("--priority", type=int, choices=[1, 2, 3])
    update_parser.add_argument("--due-at")
    update_parser.add_argument("--status", choices=["todo", "doing", "done"])
    update_parser.add_argument("--remind-interval-minutes", type=int)
    reminder_group = update_parser.add_mutually_exclusive_group()
    reminder_group.add_argument("--enable-reminder", dest="reminder", action="store_true")
    reminder_group.add_argument("--disable-reminder", dest="reminder", action="store_false")
    update_parser.set_defaults(handler=task_update, reminder=None)

    detail_parser = sub.add_parser("detail", help="查看任务详情")
    detail_parser.add_argument("task_number")
    detail_parser.set_defaults(handler=task_detail)

    for action in ("complete", "reopen", "delete", "restore"):
        action_parser = sub.add_parser(action, help=f"{action} 任务")
        action_parser.add_argument("task_number")
        action_parser.set_defaults(handler=task_action, action=action)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        result = args.handler(args)
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
