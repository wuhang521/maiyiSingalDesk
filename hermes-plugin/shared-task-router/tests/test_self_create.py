"""Natural first-person task creation uses the authenticated channel binding."""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

SOURCE = Path(os.environ.get("TASK_ROUTER_SOURCE") or Path(__file__).parents[1] / "router.py")
spec = importlib.util.spec_from_file_location("self_creation_router", SOURCE)
router = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = router
spec.loader.exec_module(router)
sys.modules["router"] = router
dialog_spec = importlib.util.spec_from_file_location("self_creation_dialog", Path(__file__).parents[1] / "dialog.py")
dialog = importlib.util.module_from_spec(dialog_spec)
sys.modules[dialog_spec.name] = dialog
dialog_spec.loader.exec_module(dialog)
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class RecordingApi:
    def __init__(self):
        self.created = []
        self.staff = [
            router.StaffIdentity(id=101, name="吴航", channels=(
                {"platform": "weixin", "recipient_id": "wx-wu", "enabled": True},
            )),
            router.StaffIdentity(id=102, name="敏总", channels=(
                {"platform": "weixin", "recipient_id": "wx-min", "enabled": True},
            )),
        ]

    def list_staff(self):
        return self.staff

    def list_tasks(self, state="active", assignee_id=None):
        return {"items": [row for row in self.created if row["assignee_id"] == assignee_id]}

    def create_task(self, draft, staff, creator, platform, source_message_id):
        row = {
            "task_no": "T1", "assignee_id": staff.id, "assignee_name": staff.name,
            "title": draft.title, "content": draft.content, "creator_name": creator,
            "due_at": draft.due_at.isoformat() if draft.due_at else None,
            "source_platform": platform, "source_message_id": source_message_id,
        }
        self.created.append(row)
        return row


class SelfCreationTests(unittest.TestCase):
    def test_natural_create_phrases_enter_task_flow_with_clean_titles(self):
        phrases = [
            "给我添加一个任务是测试系统",
            "给我添加一个任务：测试系统",
            "给我 添加 一个任务 是 测试系统",
            "给我添加个任务：测试系统",
            "给我添加一项待办，内容是测试系统",
            "给我自己添加一个任务：测试系统",
            "给本人添加一个任务为测试系统",
            "给我安排一个任务是测试系统",
            "给我新增一个任务：测试系统",
            "给我创建一个任务：测试系统",
        ]
        api = RecordingApi()
        for text in phrases:
            with self.subTest(text=text):
                self.assertTrue(router.looks_like_explicit_task_message(text))
                intents = router.parse_message(text + "，不设截止时间", api.staff, self_name="吴航", now=NOW)
                self.assertEqual(len(intents), 1)
                self.assertEqual(intents[0].kind, "create")
                self.assertEqual(intents[0].assignee, "吴航")
                self.assertEqual(intents[0].title, "测试系统")

    def test_self_is_resolved_from_each_bound_sender(self):
        for recipient, expected in [("wx-wu", "吴航"), ("wx-min", "敏总")]:
            with self.subTest(recipient=recipient):
                api = RecordingApi()
                reply = router.TaskRouter(api=api).handle(
                    "给我添加一个任务是测试系统，不设截止时间", platform="weixin", sender_id=recipient,
                    sender_name="untrusted nickname", chat_id=recipient, chat_type="dm",
                    message_id="synthetic-create-1", now=NOW,
                )
                self.assertEqual(len(api.created), 1)
                self.assertEqual(api.created[0]["assignee_name"], expected)
                self.assertEqual(api.created[0]["title"], "测试系统")
                self.assertEqual(api.created[0]["source_message_id"], "synthetic-create-1")
                self.assertIn("T1", reply)

    def test_explicit_assignee_remains_distinct_from_sender(self):
        api = RecordingApi()
        router.TaskRouter(api=api).handle(
            "给敏总添加一个任务是测试系统，不设截止时间", platform="weixin", sender_id="wx-wu",
            chat_id="wx-wu", message_id="synthetic-named-1", now=NOW,
        )
        self.assertEqual(api.created[0]["assignee_name"], "敏总")

    def test_exact_original_phrase_enters_dialog_then_accepts_due_followup(self):
        for recipient, expected in [("wx-wu", "吴航"), ("wx-min", "敏总")]:
            with self.subTest(recipient=recipient), tempfile.TemporaryDirectory() as home:
                api = RecordingApi()
                coordinator = dialog.TaskDialogCoordinator(lambda: router.TaskRouter(api=api))
                context = dict(profile_home=home, platform="weixin", sender_id=recipient,
                               sender_name="untrusted nickname", chat_id=recipient, chat_type="dm")
                text = "给我添加一个任务是测试系统"
                self.assertTrue(dialog.should_intercept_task_dialog(text, **{
                    key: value for key, value in context.items() if key != "sender_name"
                }))
                first = coordinator.process(text, message_id="synthetic-initial", **context)
                self.assertIn("截止", first.primary)
                self.assertEqual(api.created, [])
                second = coordinator.process("不设截止时间", message_id="synthetic-followup", **context)
                self.assertEqual(len(api.created), 1)
                self.assertEqual(api.created[0]["assignee_name"], expected)
                self.assertEqual(api.created[0]["title"], "测试系统")
                self.assertIn("T1", second.primary)

    def test_unbound_or_group_identity_cannot_use_someone_elses_binding(self):
        for sender, chat, kind in [(None, "wx-wu", "group"), ("unknown", "wx-wu", "group"), ("unknown", "unknown", "dm")]:
            with self.subTest(sender=sender, kind=kind):
                api = RecordingApi()
                reply = router.TaskRouter(api=api).handle(
                    "给我添加一个任务是测试系统", platform="weixin", sender_id=sender,
                    chat_id=chat, chat_type=kind, message_id="synthetic-unbound", now=NOW,
                )
                self.assertEqual(api.created, [])
                self.assertIn("绑定", reply)

    def test_questions_negation_and_discussion_do_not_create_tasks(self):
        api = RecordingApi()
        for text in [
            "不要给我添加一个任务是测试系统",
            "给我添加一个任务是不要测试系统",
            "给我添加一个任务是测试系统吗",
            "为什么给我添加一个任务是测试系统",
            "判断“给我添加一个任务是测试系统”会不会创建任务",
        ]:
            with self.subTest(text=text):
                intents = router.parse_message(text, api.staff, self_name="吴航", now=NOW)
                self.assertFalse(any(intent.kind == "create" for intent in intents or []))

    def test_non_task_additions_do_not_enter_assignment_flow(self):
        for text in ["给我添加到日历", "给我添加一个微信好友", "给我添加一个文件"]:
            with self.subTest(text=text):
                self.assertFalse(router.looks_like_explicit_task_message(text))
                self.assertFalse(any(item.kind == "create" for item in router.parse_message(
                    text, RecordingApi().staff, self_name="吴航", now=NOW,
                ) or []))

    def test_missing_task_content_requests_clarification(self):
        for text in ["给我添加一个任务", "给我添加个任务：", "给我添加一项待办内容是"]:
            with self.subTest(text=text):
                intents = router.parse_message(text, RecordingApi().staff, self_name="吴航", now=NOW)
                self.assertEqual(intents[0].kind, "clarify")


if __name__ == "__main__":
    unittest.main()
