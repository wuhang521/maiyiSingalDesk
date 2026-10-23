import {
  ArchiveRestore,
  Check,
  CircleDot,
  FolderKanban,
  LayoutList,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  UserRound,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { taskApi } from "../lib/taskApi";
import type {
  AppSettings,
  StaffMember,
  TaskDraft,
  TaskItem,
  TaskListState,
} from "../types";
import { TaskDialog } from "./TaskDialog";
import { MemberManagement } from "./MemberManagement";

interface TaskWorkspaceProps {
  settings: AppSettings;
  searchQuery: string;
  currentStaffId?: number;
  canManageMembers?: boolean;
}

type TaskView = "all" | "assignee" | "project";

interface TaskGroup {
  key: string;
  label: string;
  tasks: TaskItem[];
}

const stateLabels: Record<TaskListState, string> = {
  active: "进行中",
  done: "已完成",
  deleted: "回收站",
  all: "全部",
};

const stateOrder: TaskListState[] = ["all", "active", "done", "deleted"];

export function TaskWorkspace({ settings, searchQuery, currentStaffId, canManageMembers = false }: TaskWorkspaceProps) {
  const [mode, setMode] = useState<"tasks" | "staff">("tasks");
  const [taskView, setTaskView] = useState<TaskView>("all");
  const [state, setState] = useState<TaskListState>("active");
  const [assigneeId, setAssigneeId] = useState(0);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<TaskItem | null>(null);

  const configured = Boolean(settings.backendUrl.trim() && settings.backendAuthToken.trim());

  const load = useCallback(async () => {
    if (!configured) return;
    setLoading(true);
    setError("");
    try {
      const [taskResult, staffResult] = await Promise.all([
        taskApi.listTasks(settings, state, searchQuery, assigneeId || undefined),
        taskApi.listStaff(settings),
      ]);
      setTasks(taskResult.items);
      setStaff(staffResult);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [configured, settings, state, searchQuery, assigneeId]);

  useEffect(() => {
    const timer = window.setTimeout(load, 200);
    return () => window.clearTimeout(timer);
  }, [load]);

  const counts = useMemo(
    () => ({
      urgent: tasks.filter((task) => task.priority === 3).length,
      overdue: tasks.filter(
        (task) => task.status !== "done" && task.due_at && new Date(task.due_at) < new Date(),
      ).length,
    }),
    [tasks],
  );

  const groupedTasks = useMemo(
    () => taskView === "all" ? [] : groupTasks(tasks, taskView),
    [tasks, taskView],
  );

  async function saveTask(draft: TaskDraft) {
    try {
      if (editing) await taskApi.updateTask(settings, editing.id, draft);
      else await taskApi.createTask(settings, draft);
      setDialogOpen(false);
      setEditing(null);
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
      throw caught;
    }
  }

  async function perform(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      await load();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  if (!configured) {
    return (
      <div className="task-empty-state">
        <CircleDot size={36} />
        <h2>任务后台尚未连接</h2>
        <p>请到“设置”填写 113 服务器上的任务后台地址和访问令牌。</p>
      </div>
    );
  }

  return (
    <div className="task-page">
      <div className="task-toolbar">
        <div className="task-mode-switch">
          <button
            className={mode === "tasks" ? "active" : ""}
            type="button"
            onClick={() => setMode("tasks")}
          >
            <Check size={15} />任务
          </button>
          {canManageMembers && <button
            className={mode === "staff" ? "active" : ""}
            type="button"
            onClick={() => setMode("staff")}
          >
            <Users size={15} />用户管理
          </button>}
        </div>
        <div className="task-toolbar-actions">
          {mode === "tasks" && <button
            className="icon-button"
            type="button"
            onClick={load}
            title={loading ? "正在刷新任务" : "刷新任务"}
            aria-label="刷新任务"
            disabled={loading}
          >
            <RefreshCw size={16} className={loading ? "spin" : ""} />
          </button>}
          {mode === "tasks" && (
            <button
              className="primary-button"
              type="button"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus size={16} />新建任务
            </button>
          )}
        </div>
      </div>

      {error && mode === "tasks" && <div className="task-error">{error}</div>}

      {mode === "tasks" ? (
        <>
          <div className="task-summary">
            <div><strong>{tasks.length}</strong><span>{stateLabels[state]}</span></div>
            <div><strong>{counts.urgent}</strong><span>紧急</span></div>
            <div><strong>{counts.overdue}</strong><span>已逾期</span></div>
          </div>
          <div className="task-viewbar">
            <div className="task-view-switch" role="group" aria-label="任务展示方式">
              <button
                className={taskView === "all" ? "active" : ""}
                type="button"
                onClick={() => setTaskView("all")}
              >
                <LayoutList size={14} />全部
              </button>
              <button
                className={taskView === "assignee" ? "active" : ""}
                type="button"
                onClick={() => setTaskView("assignee")}
              >
                <UserRound size={14} />按人员
              </button>
              <button
                className={taskView === "project" ? "active" : ""}
                type="button"
                onClick={() => setTaskView("project")}
              >
                <FolderKanban size={14} />按项目
              </button>
            </div>
            <span>
              {taskView === "all" && "查看完整任务明细和操作"}
              {taskView === "assignee" && "按负责人快速查看任务分布"}
              {taskView === "project" && "按任务中的项目名称归类"}
            </span>
          </div>
          <div className="task-filters">
            <div>
              {stateOrder.map((item) => (
                <button
                  key={item}
                  className={state === item ? "active" : ""}
                  type="button"
                  onClick={() => setState(item)}
                >
                  {stateLabels[item]}
                </button>
              ))}
            </div>
            <select value={assigneeId} onChange={(event) => setAssigneeId(Number(event.target.value))}>
              <option value={0}>全部负责人</option>
              {staff.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </div>

          {taskView === "all" ? (
            <div className="task-table-wrap">
              <table className="task-table">
                <thead>
                  <tr>
                    <th>编号 / 任务</th>
                    <th>负责人</th>
                    <th>状态</th>
                    <th>截止时间</th>
                    <th>提醒</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((task) => (
                    <tr key={task.id}>
                      <td>
                        <div className="task-title-cell">
                          <span className={`priority-dot p${task.priority}`} />
                          <div>
                            <strong>{task.title}</strong>
                            <span>{task.task_no}{task.content ? ` · ${task.content}` : ""}</span>
                          </div>
                        </div>
                      </td>
                      <td>{task.assignee_name}</td>
                      <td><StatusBadge task={task} /></td>
                      <td>{formatDate(task.due_at)}</td>
                      <td>{formatReminder(task)}</td>
                      <td>
                        <div className="task-row-actions">
                          {!task.deleted_at && task.status !== "done" && task.assignee_id === currentStaffId && (
                            <button
                              type="button"
                              title="完成"
                              onClick={() => perform(() => taskApi.action(settings, task.id, "complete"))}
                            ><Check size={15} /></button>
                          )}
                          {!task.deleted_at && task.status === "done" && (
                            <button
                              type="button"
                              title="重新打开"
                              onClick={() => perform(() => taskApi.action(settings, task.id, "reopen"))}
                            ><ArchiveRestore size={15} /></button>
                          )}
                          {!task.deleted_at && (
                            <button
                              type="button"
                              title="编辑"
                              onClick={() => {
                                setEditing(task);
                                setDialogOpen(true);
                              }}
                            ><Pencil size={15} /></button>
                          )}
                          {!task.deleted_at ? (
                            <button
                              className="danger"
                              type="button"
                              title="删除"
                              onClick={() => {
                                if (window.confirm(`确定删除任务“${task.title}”吗？`)) {
                                  perform(() => taskApi.deleteTask(settings, task.id));
                                }
                              }}
                            ><Trash2 size={15} /></button>
                          ) : (
                            <button
                              type="button"
                              title="恢复"
                              onClick={() => perform(() => taskApi.action(settings, task.id, "restore"))}
                            ><ArchiveRestore size={15} /></button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!loading && tasks.length === 0 && (
                <div className="task-table-empty">当前没有任务</div>
              )}
            </div>
          ) : (
            <>
              {taskView === "project" && (
                <div className="task-project-note">
                  <FolderKanban size={16} />
                  <span>项目分类来自任务数据；尚未标注项目的任务统一显示为“未分类”。</span>
                </div>
              )}
              <div className="task-group-grid">
                {groupedTasks.map((group) => (
                  <section className="task-group" key={group.key}>
                    <header className="task-group-heading">
                      <span className="task-group-icon">
                        {taskView === "assignee"
                          ? <UserRound size={17} />
                          : <FolderKanban size={17} />}
                      </span>
                      <div>
                        <h3>{group.label}</h3>
                        <p>{group.tasks.filter((task) => task.status !== "done").length} 条待处理</p>
                      </div>
                      <strong>{group.tasks.length}<span>条任务</span></strong>
                    </header>
                    <div className="task-preview-list">
                      {group.tasks.map((task) => (
                        <article
                          className={`task-preview-row${taskView === "project" ? " with-owner" : ""}`}
                          key={task.id}
                        >
                          <span className={`priority-dot p${task.priority}`} />
                          <div className="task-preview-copy">
                            <strong>{task.title}</strong>
                            <span>{task.task_no}{task.content ? ` · ${task.content}` : ""}</span>
                          </div>
                          {taskView === "project" && (
                            <span className="task-preview-owner"><UserRound size={13} />{task.assignee_name}</span>
                          )}
                          <div className="task-preview-meta">
                            <StatusBadge task={task} />
                            <span>{formatDate(task.due_at)}</span>
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
              {!loading && groupedTasks.length === 0 && (
                <div className="task-table-empty">当前没有可分组的任务</div>
              )}
            </>
          )}
        </>
      ) : (
        canManageMembers && <MemberManagement settings={settings} currentStaffId={currentStaffId} onMembersChanged={load} />
      )}

      <TaskDialog
        open={dialogOpen}
        task={editing}
        staff={staff}
        onClose={() => {
          setDialogOpen(false);
          setEditing(null);
        }}
        onSave={saveTask}
      />
    </div>
  );
}

function StatusBadge({ task }: { task: TaskItem }) {
  if (task.deleted_at) return <span className="status-badge deleted">已删除</span>;
  if (task.status === "done") return <span className="status-badge done">已完成</span>;
  if (task.due_at && new Date(task.due_at) < new Date()) {
    return <span className="status-badge overdue">已逾期</span>;
  }
  return <span className={`status-badge ${task.status}`}>{task.status === "doing" ? "进行中" : "待处理"}</span>;
}

function groupTasks(tasks: TaskItem[], view: Exclude<TaskView, "all">): TaskGroup[] {
  const groups = new Map<string, TaskGroup>();
  for (const task of tasks) {
    const label = view === "assignee"
      ? task.assignee_name.trim() || "未分配"
      : task.project_name?.trim() || "未分类";
    const key = `${view}:${label}`;
    const group = groups.get(key) || { key, label, tasks: [] };
    group.tasks.push(task);
    groups.set(key, group);
  }
  return Array.from(groups.values()).sort((a, b) => {
    if (a.label === "未分类") return 1;
    if (b.label === "未分类") return -1;
    return a.label.localeCompare(b.label, "zh-CN");
  });
}

function formatReminder(task: TaskItem): string {
  if (!task.remind_enabled) return "关闭";
  if (task.remind_interval_minutes % 60 === 0) {
    return `${task.remind_interval_minutes / 60} 小时`;
  }
  return `${task.remind_interval_minutes} 分钟`;
}

function formatDate(value: string | null): string {
  if (!value) return "未设置";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "未知错误");
}
