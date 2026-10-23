import { useEffect, useState } from "react";
import type { StaffMember, TaskDraft, TaskItem } from "../types";

interface TaskDialogProps {
  open: boolean;
  task: TaskItem | null;
  staff: StaffMember[];
  onClose: () => void;
  onSave: (draft: TaskDraft) => Promise<void>;
}

function localDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function TaskDialog({ open, task, staff, onClose, onSave }: TaskDialogProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [assigneeId, setAssigneeId] = useState(0);
  const [priority, setPriority] = useState(2);
  const [dueAt, setDueAt] = useState("");
  const [remindEnabled, setRemindEnabled] = useState(true);
  const [intervalHours, setIntervalHours] = useState(5);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle(task?.title || "");
    setContent(task?.content || "");
    setAssigneeId(task?.assignee_id || staff.find((item) => item.enabled)?.id || 0);
    setPriority(task?.priority || 2);
    setDueAt(localDateTime(task?.due_at));
    setRemindEnabled(task?.remind_enabled ?? true);
    setIntervalHours((task?.remind_interval_minutes || 300) / 60);
  }, [open, task, staff]);

  if (!open) return null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim() || !assigneeId) return;
    setSaving(true);
    try {
      await onSave({
        title: title.trim(),
        content: content.trim(),
        assignee_id: assigneeId,
        priority,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
        remind_enabled: remindEnabled,
        remind_interval_minutes: Math.round(intervalHours * 60),
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <form
        className="task-dialog"
        onSubmit={submit}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="task-dialog-heading">
          <div>
            <h2>{task ? "编辑任务" : "新建任务"}</h2>
            <p>任务保存后会同步到共享数据库，并按设置提醒负责人。</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>

        <label className="task-form-field">
          <span>任务标题</span>
          <input
            autoFocus
            value={title}
            maxLength={240}
            placeholder="例如：周二前发布一篇公众号文章"
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>

        <label className="task-form-field">
          <span>详细要求</span>
          <textarea
            rows={5}
            value={content}
            placeholder="补充素材、验收标准、注意事项等"
            onChange={(event) => setContent(event.target.value)}
          />
        </label>

        <div className="task-form-grid">
          <label className="task-form-field">
            <span>负责人</span>
            <select
              value={assigneeId}
              onChange={(event) => setAssigneeId(Number(event.target.value))}
            >
              <option value={0}>请选择</option>
              {staff.filter((item) => item.enabled).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </label>
          <label className="task-form-field">
            <span>优先级</span>
            <select value={priority} onChange={(event) => setPriority(Number(event.target.value))}>
              <option value={1}>普通</option>
              <option value={2}>重要</option>
              <option value={3}>紧急</option>
            </select>
          </label>
          <label className="task-form-field">
            <span>截止时间</span>
            <input
              type="datetime-local"
              value={dueAt}
              onChange={(event) => setDueAt(event.target.value)}
            />
          </label>
          <label className="task-form-field">
            <span>提醒间隔</span>
            <select
              disabled={!remindEnabled}
              value={intervalHours}
              onChange={(event) => setIntervalHours(Number(event.target.value))}
            >
              <option value={1}>每 1 小时</option>
              <option value={3}>每 3 小时</option>
              <option value={5}>每 5 小时</option>
              <option value={12}>每 12 小时</option>
              <option value={24}>每天</option>
            </select>
          </label>
        </div>

        <label className="task-reminder-toggle">
          <input
            type="checkbox"
            checked={remindEnabled}
            onChange={(event) => setRemindEnabled(event.target.checked)}
          />
          <span>任务未完成时持续提醒负责人</span>
        </label>

        <div className="task-dialog-actions">
          <button className="secondary-button" type="button" onClick={onClose}>取消</button>
          <button className="primary-button" disabled={saving || !title.trim() || !assigneeId}>
            {saving ? "保存中…" : "保存任务"}
          </button>
        </div>
      </form>
    </div>
  );
}
