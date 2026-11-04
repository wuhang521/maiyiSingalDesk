from __future__ import annotations

import sys
import asyncio
import importlib.util
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from router import (  # noqa: E402
    Intent,
    SHANGHAI,
    StaffIdentity,
    TaskRouter,
    parse_chinese_datetime,
    parse_message,
)


NOW = datetime(2026, 7, 31, 10, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
STAFF = [
    StaffIdentity(
        id=1,
        name="杰哥",
        channels=({"platform": "qq", "recipient_id": "qq-jie", "enabled": True},),
    ),
    StaffIdentity(
        id=2,
        name="敏总",
        channels=(
            {"platform": "weixin", "recipient_id": "wx-min", "enabled": True},
        ),
    ),
    StaffIdentity(
        id=3,
        name="振华",
        channels=(
            {"platform": "wecom", "recipient_id": "wecom-zhenhua", "enabled": True},
        ),
    ),
]


def one(text: str, self_name: str = "杰哥") -> Intent:
    result = parse_message(text, STAFF, self_name=self_name, now=NOW)
    assert result is not None
    assert len(result) == 1
    return result[0]


def test_explicit_assignee_is_never_replaced_by_sender() -> None:
    intent = one("给敏总安排下周一下午2点半参加腾讯会议", self_name="杰哥")
    assert intent.kind == "create"
    assert intent.assignee == "敏总"
    assert intent.title == "参加腾讯会议"
    assert intent.due_at == datetime(2026, 8, 3, 14, 30, tzinfo=SHANGHAI)


def test_aliases_are_code_level_not_memory_level() -> None:
    min_intent = one("让姐明天下午4点开会")
    jie_intent = one("通知靓仔后天上午9点发公众号")
    assert min_intent.assignee == "敏总"
    assert jie_intent.assignee == "杰哥"
    assert jie_intent.due_at == datetime(2026, 8, 2, 9, 0, tzinfo=SHANGHAI)


def test_meeting_number_is_content_not_time() -> None:
    intent = one("给敏总安排腾讯会议，会议号123456")
    assert intent.kind == "create"
    assert intent.due_at is None
    assert "123456" in intent.title


def test_calendar_requires_explicit_calendar_action() -> None:
    assert parse_message("帮我把会议加到日历", STAFF, self_name="杰哥", now=NOW) is None
    conflict = one("给敏总安排会议并同步到日历")
    assert conflict.kind == "clarify"
    assert conflict.rule_id == "TASK_CALENDAR_CONFLICT"


def test_meta_discussion_does_not_create_task() -> None:
    assert (
        parse_message(
            "我说给敏总安排任务，但是他识别了给我提醒了",
            STAFF,
            self_name="杰哥",
            now=NOW,
        )
        is None
    )


def test_single_character_alias_does_not_match_jiejie() -> None:
    assert parse_message("姐姐明天下午2点开会", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("姐妹明天下午2点开会", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("让敏总监明天下午2点开会", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("让杰哥们明天下午2点开会", STAFF, self_name="杰哥", now=NOW) is None


def test_unknown_assignee_returns_clarification() -> None:
    intent = one("给张总安排任务：明天下午2点开会")
    assert intent.kind == "clarify"
    assert "没有找到负责人" in (intent.message or "")


def test_list_and_task_number_actions() -> None:
    assert one("姐还有什么活").assignee == "敏总"
    assert one("查看全部待办").kind == "list"
    assert one("详情 T002").task_no == "T2"
    assert one("T2 搞定了").kind == "complete"
    assert one("T2已经完成了").kind == "complete"
    assert one("删除 T2").kind == "delete"
    assert one("恢复 T2").kind == "restore"
    assert one("重新打开 T2").kind == "reopen"
    assert one("我的任务").kind == "list_self"
    assert one("查看我的待办").kind == "list_self"


def test_complete_all_phrases_only_target_current_sender() -> None:
    for text in (
        "全部完成",
        "已全部完成任务",
        "全部任务都完成了",
        "所有任务完成",
        "任务都完成了",
        "我的任务全都搞定了",
        "我已完成全部任务",
    ):
        intent = one(text)
        assert intent.kind == "complete_all"
        assert intent.assignee == "杰哥"

    forbidden = one("敏总完成")
    assert forbidden.kind == "clarify"
    assert forbidden.rule_id == "COMPLETE_OTHER_FORBIDDEN"


def test_multiple_task_commands_are_rejected() -> None:
    intent = one("完成 T2 再删除 T3")
    assert intent.kind == "clarify"
    assert intent.rule_id == "MULTIPLE_TASK_COMMANDS"


def test_ambiguous_clock_asks_instead_of_guessing() -> None:
    intent = one("给敏总安排明天3点开会")
    assert intent.kind == "clarify"
    assert "上午还是下午" in (intent.message or "")


def test_question_does_not_mutate_task() -> None:
    assert one("T2完成了吗").kind == "detail"
    assert one("T2还没做完").kind == "detail"
    assert parse_message("为什么T2完成了还提醒", STAFF, self_name="杰哥", now=NOW) is None
    assert one("不要删除T2").kind == "detail"
    assert one("T2什么时候完成").kind == "detail"
    assert parse_message("给敏总安排任务了吗", STAFF, self_name="杰哥", now=NOW) is None
    assert one("给敏总安排任务了没").kind == "clarify"
    assert one("给敏总安排任务没有").kind == "clarify"


def test_task_queries_are_not_created() -> None:
    assert one("给我看看任务").kind == "list_self"
    assert one("给我发一下任务清单").kind == "list_self"
    intent = one("需要敏总的任务清单")
    assert intent.kind == "list"
    assert intent.assignee == "敏总"
    assert parse_message("给敏总安排什么任务", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("给我看看明天下午2点的会议", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("给我查看明天下午2点谁开会", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("给我写一篇公众号文章", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("给敏总写一段会议通知", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("让我写一篇公众号文章", STAFF, self_name="杰哥", now=NOW) is None
    assert parse_message("通知我明天下午2点开会", STAFF, self_name="杰哥", now=NOW) is None


def test_negative_or_cancelled_assignment_never_creates() -> None:
    samples = (
        "让敏总明天不用开会",
        "让敏总明天不要发公众号",
        "让敏总明天不去开会",
        "通知敏总明天不开会",
        "让敏总不需要参加会议",
        "通知敏总暂不开会",
        "让敏总明天不做公众号",
        "通知敏总不处理这个",
        "让敏总不提交报告",
        "让敏总不联系客户",
        "通知敏总明天不整理资料",
        "让敏总明天不再开会",
        "通知敏总暂停发公众号",
        "让敏总禁止发布",
        "让敏总明天不回复客户",
        "让敏总明天不审核合同",
        "让敏总明天不付款",
        "让敏总明天不打电话",
        "通知敏总别开会",
        "给敏总取消明天下午2点会议",
    )
    for text in samples:
        result = parse_message(text, STAFF, self_name="杰哥", now=NOW)
        assert not result or all(item.kind != "create" for item in result)


def test_unpunctuated_questions_never_create_or_update() -> None:
    create_samples = (
        "给敏总安排明天下午开会是不是",
        "让敏总明天开会对不对",
        "通知敏总明天开会可不可以",
        "给敏总安排明天下午开会行不行",
        "给敏总安排明天几点开会",
        "让敏总什么时候发公众号",
        "需要敏总怎么处理",
        "通知敏总为什么延期",
        "给敏总安排谁开会",
        "给敏总安排明天开会了吧",
        "让敏总明天开会是吧",
        "通知敏总明天开会可以吧",
        "通知敏总可不可以参加会议",
        "让敏总明天该不该开会",
        "让敏总明天能否开会",
        "让敏总明天和谁开会",
        "让敏总明天开会嘛",
        "让敏总明天是不是参加会议",
        "让敏总明天是否开会",
        "让敏总明天为什么开会",
        "让敏总明天有没有开会",
    )
    for text in create_samples:
        result = parse_message(text, STAFF, self_name="杰哥", now=NOW)
        assert not result or all(item.kind != "create" for item in result)

    for text in (
        "T2改成什么内容",
        "T2修改为哪个标题",
        "T2改成这个是不是",
        "T2改给杰哥不对",
        "T2改成啥",
        "T2改成多少",
        "T2改成方案A还是方案B",
    ):
        assert one(text).kind != "update"


def test_multiple_assignees_are_never_silently_reduced_to_one() -> None:
    for text in (
        "让杰哥和敏总明天开会",
        "通知杰哥、敏总后天发公众号",
        "需要姐和靓仔一起整理资料",
        "T2转给敏总和杰哥",
        "T2转给敏总或者杰哥",
        "让杰哥以及敏总明天开会",
        "让杰哥还有敏总明天开会",
        "通知杰哥、还有敏总后天发公众号",
        "需要姐同靓仔一起整理资料",
        "让敏总和我明天开会",
        "T2转给敏总以及杰哥",
        "T2转给敏总还有杰哥",
        "T2转给敏总和我",
        "T2转给敏总，也转给杰哥",
        "T2转给敏总还是杰哥",
        "让敏总和张总明天下午2点开会",
    ):
        intent = one(text)
        assert intent.kind == "clarify"
        assert "一个负责人" in (intent.message or "")


def test_numbered_assignments_create_multiple_structured_intents() -> None:
    result = parse_message(
        "给杰哥安排任务：\n1. 明天下午2点发一篇公众号\n2. 后天上午9点发一篇小红书",
        STAFF,
        self_name="敏总",
        now=NOW,
    )
    assert result is not None
    assert [item.assignee for item in result] == ["杰哥", "杰哥"]
    assert [item.title for item in result] == ["发一篇公众号", "发一篇小红书"]

    one_line = parse_message(
        "1.需要杰哥发公众号 2.需要杰哥发小红书",
        STAFF,
        self_name="敏总",
        now=NOW,
    )
    assert one_line is not None
    assert len(one_line) == 2

    assert (
        parse_message(
            "为什么这些会变成任务：\n1. 给敏总安排开会\n2. 给杰哥安排发公众号",
            STAFF,
            self_name="敏总",
            now=NOW,
        )
        is None
    )
    assert (
        parse_message(
            "例如：\n1. 给敏总完成T2相关核对\n2. 给杰哥后天发公众号",
            STAFF,
            self_name="敏总",
            now=NOW,
        )
        is None
    )
    guarded = parse_message(
        "给杰哥安排任务：\n1. 明天下午2点开会吗\n2. 明天不要发公众号",
        STAFF,
        self_name="敏总",
        now=NOW,
    )
    assert guarded is not None
    assert all(item.kind == "clarify" for item in guarded)

    reversed_people = parse_message(
        "给我安排任务：\n1. 给敏总明天下午2点开会\n2. 给敏总后天发公众号",
        STAFF,
        self_name="杰哥",
        now=NOW,
    )
    assert reversed_people is not None
    assert any(item.kind == "clarify" for item in reversed_people)
    assert all(item.kind != "create" or item.assignee != "杰哥" for item in reversed_people)
    polite_people = parse_message(
        "给我安排任务：\n1. 请给敏总明天下午2点开会\n2. 帮我给敏总后天发公众号",
        STAFF,
        self_name="杰哥",
        now=NOW,
    )
    assert polite_people is not None
    assert any(item.kind == "clarify" for item in polite_people)
    assert all(item.kind != "create" or item.assignee != "杰哥" for item in polite_people)
    unknown_people = parse_message(
        "给我安排任务：\n1. 让张总明天下午2点开会\n2. 张总需要后天上午9点发公众号",
        STAFF,
        self_name="杰哥",
        now=NOW,
    )
    assert unknown_people is not None
    assert all(item.kind == "clarify" for item in unknown_people)


def test_meta_examples_are_intercepted_but_never_executed() -> None:
    samples = (
        "以下是测试：\n1. 让敏总明天下午2点开会\n2. 通知杰哥后天发公众号",
        "测试：1. 让敏总开会 2. 通知杰哥发公众号",
        "下面只是示例：1. 让敏总开会 2. 让杰哥发公众号",
        "请判断以下指令是否会创建任务：1. 让敏总开会 2. 让杰哥发公众号",
        "不要执行，仅供测试：1. 让敏总开会 2. 让杰哥发公众号",
        "这是测试：1. 让敏总开会 2. 让杰哥发公众号",
        "这只是测试：1. 让敏总开会 2. 让杰哥发公众号",
        "我想测试：1. 让敏总开会 2. 让杰哥发公众号",
        "帮我看看这些指令会不会执行：1. 让敏总开会 2. 让杰哥发公众号",
        "仅演示，不要真的创建：1. 让敏总开会 2. 让杰哥发公众号",
        "试试看：1. 让敏总开会 2. 让杰哥发公众号",
        "看看会不会创建任务：1. 让敏总开会 2. 让杰哥发公众号",
    )
    for text in samples:
        assert parse_message(text, STAFF, self_name="杰哥", now=NOW) is None
    conflict = parse_message(
        "1. 给敏总安排开会\n2. 给杰哥安排发布文章并同步到日历",
        STAFF,
        self_name="敏总",
        now=NOW,
    )
    assert conflict is not None
    assert conflict[0].rule_id == "TASK_CALENDAR_CONFLICT"


def test_qq_quote_is_not_used_as_current_instruction() -> None:
    result = parse_message(
        "[Quoted message]:\n给敏总安排明天下午2点开会\n\n好的",
        STAFF,
        self_name="杰哥",
        now=NOW,
    )
    assert result is None


def test_time_matrix() -> None:
    assert parse_chinese_datetime("今天下午4点", NOW).value == datetime(
        2026, 7, 31, 16, 0, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("明晚8点", NOW).value == datetime(
        2026, 8, 1, 20, 0, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("后天上午九点半", NOW).value == datetime(
        2026, 8, 2, 9, 30, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("下周一下午2点半", NOW).value == datetime(
        2026, 8, 3, 14, 30, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("周二14:00", NOW).value == datetime(
        2026, 8, 4, 14, 0, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("腾讯会议号123456", NOW).value is None
    assert parse_chinese_datetime("两小时后", NOW).value == datetime(
        2026, 7, 31, 12, 20, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("今天上午9点", NOW).error
    assert parse_chinese_datetime("今晚8点", datetime(
        2026, 7, 31, 21, 0, tzinfo=SHANGHAI
    )).error
    assert parse_chinese_datetime("7月1日下午2点", NOW).value == datetime(
        2027, 7, 1, 14, 0, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("明天2:00", NOW).error
    assert parse_chinese_datetime("明天下午2点或3点开会", NOW).error
    assert parse_chinese_datetime("明天下午2点还是3点开会", NOW).error
    assert parse_chinese_datetime("明天下午2点整理1/2数据", NOW).value == datetime(
        2026, 8, 1, 14, 0, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("明天下午2点开会，2小时后复盘", NOW).error
    assert parse_chinese_datetime("1.5小时后联系客户", NOW).value == datetime(
        2026, 7, 31, 11, 50, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("半小时后联系客户", NOW).value == datetime(
        2026, 7, 31, 10, 50, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("一个半小时后联系客户", NOW).value == datetime(
        2026, 7, 31, 11, 50, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("1个半小时后联系客户", NOW).value == datetime(
        2026, 7, 31, 11, 50, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("1小时30分钟后联系客户", NOW).value == datetime(
        2026, 7, 31, 11, 50, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("两天后联系客户", NOW).value == datetime(
        2026, 8, 2, 10, 20, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("一个月后联系客户", NOW).error
    assert parse_chinese_datetime("2027-08-10 14:30开会", NOW).value == datetime(
        2027, 8, 10, 14, 30, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("2027/08/10 14:30开会", NOW).value == datetime(
        2027, 8, 10, 14, 30, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("明天下午3-4点开会", NOW).error
    assert parse_chinese_datetime("明晚12点开会", NOW).value == datetime(
        2026, 8, 2, 0, 0, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("下午3点一刻开会", NOW).value == datetime(
        2026, 7, 31, 15, 15, tzinfo=SHANGHAI
    )
    assert parse_chinese_datetime("明天下午发公众号", NOW).error
    assert parse_chinese_datetime("明天发公众号", NOW).error
    assert parse_chinese_datetime("后天8/10下午2点开会", NOW).error
    assert parse_chinese_datetime("1小时后2027-08-10开会", NOW).error
    assert parse_chinese_datetime("1小时后8/10开会", NOW).error
    assert parse_chinese_datetime("下午3点两刻开会", NOW).value == datetime(
        2026, 7, 31, 15, 30, tzinfo=SHANGHAI
    )


class FakeApi:
    def __init__(self) -> None:
        self.created_payloads: list[dict] = []
        self.tasks: list[dict] = []

    def list_staff(self) -> list[StaffIdentity]:
        return STAFF

    def create_task(
        self,
        draft: Intent,
        staff: StaffIdentity,
        creator: str,
        platform: str,
        source_message_id: str | None,
    ) -> dict:
        payload = {
            "id": len(self.tasks) + 1,
            "task_no": f"T{len(self.tasks) + 1}",
            "title": draft.title,
            "content": draft.content,
            "assignee_id": staff.id,
            "assignee_name": staff.name,
            "creator_name": creator,
            "status": "todo",
            "due_at": draft.due_at.replace(tzinfo=None).isoformat() if draft.due_at else None,
            "deleted_at": None,
            "source_platform": platform,
            "source_message_id": source_message_id,
        }
        self.created_payloads.append(payload)
        self.tasks.append(payload)
        return payload

    def list_tasks(self, state: str = "active", assignee_id: int | None = None) -> dict:
        items = self.tasks
        if assignee_id is not None:
            items = [item for item in items if item["assignee_id"] == assignee_id]
        return {"items": items, "total": len(items)}

    def get_task(self, task_no: str, include_deleted: bool = True) -> dict:
        return next(item for item in self.tasks if item["task_no"] == task_no)

    def action(self, action: str, task: dict, operator: str) -> dict:
        if action == "complete":
            task["status"] = "done"
        return task

    def complete_from_channel(
        self,
        platform: str,
        recipient_id: str,
        *,
        task_no: str | None = None,
        all_active: bool = False,
    ) -> dict:
        staff = next(
            person
            for person in self.list_staff()
            if any(
                channel["platform"] == platform
                and channel["recipient_id"] == recipient_id
                and channel.get("enabled", True)
                for channel in person.channels
            )
        )
        if task_no:
            items = [item for item in self.tasks if item["task_no"] == task_no]
            if not items or items[0]["assignee_id"] != staff.id:
                raise RuntimeError("只能完成当前消息账号负责的任务")
        elif all_active:
            items = [
                item
                for item in self.tasks
                if item["assignee_id"] == staff.id and item["status"] != "done"
            ]
        else:
            raise RuntimeError("完成范围不明确")
        targets = [item for item in items if item["status"] != "done"]
        for task in targets:
            task["status"] = "done"
        return {"items": items, "completed_count": len(targets)}


def test_complete_all_runs_for_qq_weixin_and_wecom() -> None:
    identities = (
        ("qqbot", "qq-jie", 1),
        ("weixin", "wx-min", 2),
        ("wecom", "wecom-zhenhua", 3),
    )
    for platform, sender_id, staff_id in identities:
        api = FakeApi()
        api.tasks = [
            {
                "id": 1,
                "task_no": "T1",
                "title": "本人任务一",
                "assignee_id": staff_id,
                "assignee_name": STAFF[staff_id - 1].name,
                "status": "todo",
                "deleted_at": None,
            },
            {
                "id": 2,
                "task_no": "T2",
                "title": "本人任务二",
                "assignee_id": staff_id,
                "assignee_name": STAFF[staff_id - 1].name,
                "status": "doing",
                "deleted_at": None,
            },
            {
                "id": 3,
                "task_no": "T3",
                "title": "他人任务",
                "assignee_id": 2 if staff_id != 2 else 1,
                "assignee_name": "其他人",
                "status": "todo",
                "deleted_at": None,
            },
        ]

        reply = TaskRouter(api=api).handle(
            "已全部完成任务",
            platform=platform,
            sender_id=sender_id,
            chat_id=sender_id,
            message_id=f"complete-all-{platform}",
            now=NOW,
        )

        assert "已完成你的全部 2 项任务" in (reply or "")
        assert [item["status"] for item in api.tasks] == ["done", "done", "todo"]


def test_router_cross_channel_assignment_and_creator_are_separate() -> None:
    api = FakeApi()
    router = TaskRouter(api=api)
    reply = router.handle(
        "给敏总安排下周一下午2点半参加腾讯会议",
        platform="qqbot",
        sender_id="qq-jie",
        chat_id="qq-jie",
        message_id="message-1",
        now=NOW,
    )
    assert "负责人：敏总" in (reply or "")
    assert api.created_payloads[0]["assignee_id"] == 2
    assert api.created_payloads[0]["creator_name"] == "靓仔"
    assert api.created_payloads[0]["source_platform"] == "qq"
    assert api.created_payloads[0]["source_message_id"] == "message-1"


def test_group_chat_does_not_use_chat_id_or_platform_as_sender_identity() -> None:
    api = FakeApi()
    router = TaskRouter(api=api)
    reply = router.handle(
        "我的任务",
        platform="qqbot",
        sender_id="another-user",
        sender_name="张三",
        chat_id="qq-jie",
        chat_type="group",
        message_id="message-2",
        now=NOW,
    )
    assert "没有绑定负责人" in (reply or "")

    created = router.handle(
        "给敏总安排明天下午2点开会",
        platform="qqbot",
        sender_id="another-user",
        sender_name="张三",
        chat_id="qq-jie",
        chat_type="group",
        message_id="message-3",
        now=NOW,
    )
    assert "没有绑定负责人" in (created or "")
    assert api.created_payloads == []


def test_disabled_staff_identity_cannot_expand_my_tasks_to_all() -> None:
    disabled_staff = [
        StaffIdentity(
            id=1,
            name="杰哥",
            channels=({"platform": "qq", "recipient_id": "old-qq", "enabled": True},),
            enabled=False,
        ),
        STAFF[1],
    ]

    class DisabledApi(FakeApi):
        def __init__(self) -> None:
            super().__init__()
            self.list_called = False

        def list_staff(self) -> list[StaffIdentity]:
            return disabled_staff

        def list_tasks(self, state: str = "active", assignee_id: int | None = None) -> dict:
            self.list_called = True
            return super().list_tasks(state, assignee_id)

    api = DisabledApi()
    reply = TaskRouter(api=api).handle(
        "我的任务",
        platform="qqbot",
        sender_id="old-qq",
        chat_id="old-qq",
        message_id="disabled-1",
        now=NOW,
    )
    assert "没有绑定负责人" in (reply or "")
    assert api.list_called is False

    for text in ("给敏总安排明天下午2点开会", "查看全部待办", "详情 T2", "删除 T2", "恢复 T2"):
        result = TaskRouter(api=api).handle(
            text, platform="qqbot", sender_id="old-qq", chat_id="old-qq",
            message_id="disabled-action", now=NOW,
        )
        assert "没有绑定负责人" in (result or "")
    assert api.created_payloads == []
    assert api.list_called is False


def test_gateway_hook_only_rewrites_and_tokens_really_expire() -> None:
    spec = importlib.util.spec_from_file_location("shared_task_router_plugin_test", PLUGIN_DIR / "__init__.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    source = SimpleNamespace(
        platform=SimpleNamespace(value="qqbot"),
        user_id="qq-jie",
        user_name="靓仔",
        chat_id="qq-jie",
        chat_type="dm",
        message_id="source-message",
    )
    event = SimpleNamespace(
        text="给敏总安排明天下午2点开会",
        source=source,
        message_id="event-message",
    )
    rewritten = module._pre_gateway_dispatch(event=event, gateway=object())
    assert rewritten and rewritten["action"] == "rewrite"
    assert rewritten["text"].startswith("/shared-task-result ")

    token = rewritten["text"].split(" ", 1)[1]
    created_at, request = module._REQUESTS[token]
    module._REQUESTS[token] = (created_at - module.TOKEN_TTL_SECONDS - 1, request)
    assert "已过期" in asyncio.run(module._result_command(token))
    assert module._pre_gateway_dispatch(
        event=SimpleNamespace(text="帮我写一篇文章", source=source, message_id="x"),
        gateway=object(),
    ) is None
    assert module._pre_gateway_dispatch(
        event=SimpleNamespace(text="让我写一篇文章", source=source, message_id="x2"),
        gateway=object(),
    ) is None
    meta = module._pre_gateway_dispatch(
        event=SimpleNamespace(
            text="为什么这些会变成任务：给敏总安排开会",
            source=source,
            message_id="meta",
        ),
        gateway=object(),
    )
    assert meta and meta["action"] == "rewrite"
