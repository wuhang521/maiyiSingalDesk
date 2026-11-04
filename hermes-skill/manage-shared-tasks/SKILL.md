---
name: manage-shared-tasks
description: Read SignalDesk shared-task lists and exact task details. All QQ and Weixin task mutations are handled before the model runs by the deterministic shared-task-router plugin.
---

# Shared Tasks Read-only Fallback

The `shared-task-router` plugin is the only component allowed to create, reassign, update, complete, delete, restore, or reopen tasks from QQ or Weixin messages.

If a natural-language mutation reaches this skill:

1. Do not call any mutation API or script command.
2. Do not use built-in todo, cron, calendar, memory, or file tools as a substitute.
3. Reply that nothing was changed and ask for one exact command:

```text
给某人安排任务：具体事项
完成 T编号
删除 T编号
恢复 T编号
T编号 改到明确时间
```

The model may call `scripts/task_api.py` only for:

```text
list
detail T编号
```

For task lists, show every matching task in a compact Markdown table with columns `编号`, `负责人`, `任务`, `截止时间`, and `状态`. Never expose API keys, internal database IDs, stack traces, or raw JSON.
