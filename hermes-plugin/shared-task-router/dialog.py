from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from .router import (
        PreparedRoute,
        TaskRouter,
        looks_like_explicit_task_message,
        normalize_task_command,
        task_capability_reply,
        task_capability_targets,
    )
except ImportError:  # 仅用于直接运行本目录测试。
    from router import (
        PreparedRoute,
        TaskRouter,
        looks_like_explicit_task_message,
        normalize_task_command,
        task_capability_reply,
        task_capability_targets,
    )


DIALOG_TTL_SECONDS = 15 * 60
_DIALOG_RULES = {
    "CREATE_MISSING_BODY": "content",
    "CREATE_MISSING_DUE": "due",
    "CREATE_MISSING_DATE": "date",
    "CREATE_TIME_AMBIGUOUS": "time",
    "UPDATE_MISSING_FIELDS": "update",
    "UPDATE_TIME_AMBIGUOUS": "time",
}
TASK_USAGE_HELP = (
    "没有识别出可执行的任务指令，本次没有创建或修改任何任务。\n\n"
    "请按下面的格式发送：\n"
    "- 安排任务：给吴航安排任务：周四18点前完成项目测试\n"
    "- 多人任务：给敏总和杰哥安排任务：周五10点参加会议\n"
    "- 完成任务：完成 T8\n"
    "- 修改时间：把 T8 截止时间改到周五18点\n"
    "- 修改内容：把 T8 内容改为准备会议材料\n"
    "- 修改负责人：把 T8 转给敏总\n"
    "- 修改提醒：把 T8 提醒改为每2小时一次\n"
    "- 查看详情：详情 T8"
)


@dataclass(frozen=True)
class TaskDialog:
    identity_key: str
    base_text: str
    mode: str
    awaiting: str
    expires_at: float


@dataclass(frozen=True)
class TaskDialogResult:
    primary: str
    followup: str | None = None


class TaskDialogStore:
    def __init__(self, profile_home: str):
        self.path = Path(profile_home) / "task_dialog_state.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_task_dialog (
                identity_key TEXT PRIMARY KEY,
                base_text TEXT NOT NULL,
                mode TEXT NOT NULL,
                awaiting TEXT NOT NULL,
                expires_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        return connection

    def get(self, identity_key: str, now: float | None = None) -> TaskDialog | None:
        current = time.time() if now is None else now
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM pending_task_dialog WHERE expires_at <= ?",
                    (current,),
                )
                row = connection.execute(
                    "SELECT identity_key, base_text, mode, awaiting, expires_at "
                    "FROM pending_task_dialog WHERE identity_key = ?",
                    (identity_key,),
                ).fetchone()
        return TaskDialog(*row) if row else None

    def put(
        self,
        identity_key: str,
        base_text: str,
        mode: str,
        awaiting: str,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else now
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO pending_task_dialog
                        (identity_key, base_text, mode, awaiting, expires_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(identity_key) DO UPDATE SET
                        base_text = excluded.base_text,
                        mode = excluded.mode,
                        awaiting = excluded.awaiting,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        identity_key,
                        base_text,
                        mode,
                        awaiting,
                        current + DIALOG_TTL_SECONDS,
                        current,
                    ),
                )

    def delete(self, identity_key: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM pending_task_dialog WHERE identity_key = ?",
                    (identity_key,),
                )


def task_dialog_identity(
    platform: str,
    sender_id: str | None,
    chat_id: str | None,
    chat_type: str,
) -> str:
    raw = "\x1f".join(
        (
            (platform or "").lower(),
            str(chat_type or ""),
            str(chat_id or ""),
            str(sender_id or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_cancel(text: str) -> bool:
    clean = normalize_task_command(text).strip("。.!！?？ ")
    return bool(re.fullmatch(r"(?:取消|算了|不安排了|先不安排|放弃|停止)", clean))


def _is_new_task_control(text: str) -> bool:
    clean = normalize_task_command(text)
    return bool(
        re.search(r"(?:这是|这个是|算作|作为)(?:一个)?新任务", clean)
        or re.search(r"(?:和|跟)\s*T\s*0*[1-9]\d*\s*没关系", clean, re.I)
        or re.search(r"不是(?:在)?修改\s*T\s*0*[1-9]\d*", clean, re.I)
        or re.search(r"不要(?:修改|改动|动)\s*T\s*0*[1-9]\d*", clean, re.I)
    )


def _is_standalone_time_fragment(text: str) -> bool:
    clean = normalize_task_command(text).strip("。.!！?？,， ")
    return bool(
        re.fullmatch(
            r"(?:(?:今天|明天|后天|大后天|"
            r"(?:下下周|下周|本周|这周|周|星期|礼拜)[一二三四五六日天])\s*)?"
            r"(?:(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|今晚|明早|明晚)\s*)?"
            r"(?:(?:[01]?\d|2[0-3]):[0-5]\d|"
            r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点"
            r"(?:(?:半)|(?:一刻|两刻|三刻)|"
            r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})分?)?)?",
            clean,
        )
        and bool(re.search(r"\d|点|今天|明天|后天|周|星期|礼拜", clean))
    )


def _looks_like_unrelated_question(text: str) -> bool:
    clean = normalize_task_command(text)
    return clean.endswith(("吗", "么", "呢", "?", "？")) or bool(
        re.match(r"^(?:为什么|为啥|怎么|如何|什么是|帮我查|查一下|写一篇|生成公众号)", clean)
    )


def should_intercept_task_dialog(
    text: str,
    *,
    profile_home: str,
    platform: str,
    sender_id: str | None,
    chat_id: str | None,
    chat_type: str,
) -> bool:
    if (
        task_capability_targets(text)
        or looks_like_explicit_task_message(text)
        or _is_new_task_control(text)
        or _is_standalone_time_fragment(text)
    ):
        return True
    identity_key = task_dialog_identity(platform, sender_id, chat_id, chat_type)
    pending = TaskDialogStore(profile_home).get(identity_key)
    if not pending:
        return False
    if _is_cancel(text):
        return True
    if _looks_like_unrelated_question(text):
        return False
    if pending.awaiting == "content":
        return bool(normalize_task_command(text))
    return bool(
        re.search(
            r"今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]|"
            r"上午|下午|晚上|\d{1,2}:\d{2}|\d{1,2}点|不设截止时间|"
            r"负责人|标题|内容|提醒|改成|改到|改为|转给|关闭|开启",
            normalize_task_command(text),
        )
    )


def _mode(prepared: PreparedRoute) -> str:
    kinds = {item.kind for item in prepared.intents}
    return "update" if "update" in kinds or any(item.task_no for item in prepared.intents) else "create"


def _dialog_rule(prepared: PreparedRoute) -> str | None:
    if len(prepared.intents) != 1:
        return None
    intent = prepared.intents[0]
    if intent.kind != "clarify":
        return None
    return _DIALOG_RULES.get(intent.rule_id)


def _merge_followup(pending: TaskDialog, text: str) -> str:
    clean = normalize_task_command(text)
    base = pending.base_text
    if looks_like_explicit_task_message(clean):
        return clean
    if pending.awaiting == "time" and re.search(
        r"(?:凌晨|早上|早晨|上午|中午|下午|傍晚|晚上).*(?:\d{1,2}:\d{2}|\d{1,2}点)",
        clean,
    ):
        base = re.sub(
            r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)|"
            r"(?<!\d)(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点"
            r"(?:(?:半)|(?:一刻|两刻|三刻)|"
            r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})分?)?",
            " ",
            base,
        )
    return f"{base} {clean}".strip()


class TaskDialogCoordinator:
    def __init__(self, router_factory: Callable[[], TaskRouter] = TaskRouter):
        self.router_factory = router_factory

    def handle(
        self,
        text: str,
        *,
        profile_home: str,
        platform: str,
        sender_id: str | None,
        sender_name: str | None,
        chat_id: str | None,
        chat_type: str,
        message_id: str | None,
    ) -> str:
        return self.process(
            text,
            profile_home=profile_home,
            platform=platform,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
        ).primary

    def process(
        self,
        text: str,
        *,
        profile_home: str,
        platform: str,
        sender_id: str | None,
        sender_name: str | None,
        chat_id: str | None,
        chat_type: str,
        message_id: str | None,
    ) -> TaskDialogResult:
        identity_key = task_dialog_identity(platform, sender_id, chat_id, chat_type)
        store = TaskDialogStore(profile_home)
        pending = store.get(identity_key)
        clean = normalize_task_command(text)

        if _is_cancel(clean):
            store.delete(identity_key)
            return TaskDialogResult("已取消当前任务安排，本次没有创建或修改任何任务。")

        capability_targets = task_capability_targets(clean)
        if capability_targets:
            base_targets = ["我" if item == "我" else item for item in capability_targets]
            target_text = "和".join(base_targets)
            store.put(identity_key, f"给{target_text}安排任务：", "create", "content")
            return TaskDialogResult(
                task_capability_reply(clean) or "可以，请告诉我任务内容、截止日期和时间。"
            )

        if _is_new_task_control(clean):
            if pending and pending.mode == "create":
                return TaskDialogResult("明白，这是新任务，不会修改任何旧任务。请继续补充当前缺少的信息。")
            store.put(identity_key, "", "create", "content")
            return TaskDialogResult("明白，这是新任务，不会修改任何旧任务。请说明负责人、任务内容、截止日期和时间。")

        if not pending and _is_standalone_time_fragment(clean):
            return TaskDialogResult("当前没有正在补充的任务。请同时说明负责人、任务内容、截止日期和时间。")

        candidate = _merge_followup(pending, clean) if pending else clean
        router = self.router_factory()
        prepared = router.prepare(
            candidate,
            platform=platform,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
        )
        if not prepared:
            return TaskDialogResult(TASK_USAGE_HELP)

        awaiting = _dialog_rule(prepared)
        reply = router.execute(prepared)
        followup = None
        if awaiting:
            store.put(identity_key, candidate, _mode(prepared), awaiting)
        else:
            store.delete(identity_key)
            if any(item.kind == "create" for item in prepared.intents):
                followup = router.created_assignees_summary(prepared)
        return TaskDialogResult(
            reply or "本次没有创建或修改任何任务。",
            followup,
        )
