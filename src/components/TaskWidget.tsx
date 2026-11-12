import {
  Check,
  Expand,
  Filter,
  Lock,
  Maximize2,
  Minimize2,
  Plus,
  RefreshCw,
  Unlock,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { defaultSettings } from "../data/demo";
import { authApi } from "../lib/authApi";
import { canAccessDesktopPage } from "../lib/pageAccess";
import { storage } from "../lib/storage";
import { taskApi } from "../lib/taskApi";
import type { AppSettings, AuthSession, AuthUser, TaskItem } from "../types";

type WidgetFilter = "mine" | "all";

export function TaskWidget() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [authSession, setAuthSession] = useState<AuthSession | null>(null);
  const [verifiedUser, setVerifiedUser] = useState<AuthUser | null>(null);
  const [filter, setFilter] = useState<WidgetFilter>("mine");
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [locked, setLocked] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [completingTaskId, setCompletingTaskId] = useState<number | null>(null);
  const [addedTaskId, setAddedTaskId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const addedTaskTimer = useRef<number | null>(null);
  const requestSequence = useRef(0);

  const configured = Boolean(settings?.backendUrl.trim() && settings.backendAuthToken.trim());
  const currentStaffId = verifiedUser?.staffId;
  const canViewTeam = verifiedUser?.role === "admin";
  const activeFilter = canViewTeam ? filter : "mine";
  const visibleTasks = activeFilter === "all" ? tasks : tasks.filter((task) => task.assignee_id === currentStaffId);

  const load = useCallback(async () => {
    if (!mounted.current) return;
    if (!configured || !settings || !authSession) {
      setError("请先登录迈医协同主程序");
      return;
    }
    const sequence = ++requestSequence.current;
    const isCurrent = () => mounted.current && sequence === requestSequence.current;
    setLoading(true);
    setError("");
    try {
      const user = await authApi.me(authSession);
      if (!isCurrent()) return;
      if (!user.staffId || !canAccessDesktopPage(user, "tasks")) {
        setVerifiedUser(null);
        setTasks([]);
        setError("当前账号无权访问任务中心");
        return;
      }
      setVerifiedUser(user);
      const assigneeId = user.role === "admin" && filter === "all" ? undefined : user.staffId;
      const result = await taskApi.listTasks(
        settings,
        "active",
        "",
        assigneeId,
      );
      if (isCurrent()) setTasks(selectWidgetTasks(assigneeId === undefined
        ? result.items : result.items.filter((task) => task.assignee_id === assigneeId)));
    } catch (caught) {
      if (isCurrent()) {
        setVerifiedUser(null);
        setTasks([]);
        setError(errorMessage(caught));
      }
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [authSession, configured, filter, settings]);

  useEffect(() => {
    mounted.current = true;
    document.documentElement.classList.add("task-widget-mode");
    return () => {
      mounted.current = false;
      if (addedTaskTimer.current !== null) window.clearTimeout(addedTaskTimer.current);
      addedTaskTimer.current = null;
      document.documentElement.classList.remove("task-widget-mode");
    };
  }, []);

  useEffect(() => {
    const bridge = window.signalDesk;
    if (!bridge?.loadAuthSession) {
      setError("请从迈医协同客户端打开桌面挂件");
      return;
    }
    let cancelled = false;
    Promise.all([
      bridge.loadAuthSession(),
      bridge.getTaskWidgetState?.() || Promise.resolve({ locked: false, collapsed: false }),
    ]).then(([session, widgetState]) => {
      if (cancelled) return;
      setLocked(widgetState.locked);
      setCollapsed(widgetState.collapsed);
      if (!session) {
        setError("请先登录迈医协同主程序");
        return;
      }
      setAuthSession(session);
      setSettings({
        ...storage.loadSettings(defaultSettings),
        backendUrl: session.backendUrl,
        backendAuthToken: session.accessToken,
        operatorName: session.user.displayName,
      });
    }).catch((caught) => { if (!cancelled) setError(errorMessage(caught)); });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!settings) return;
    void load();
    const refreshOnFocus = () => void load();
    const timer = window.setInterval(load, 15_000);
    window.addEventListener("focus", refreshOnFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshOnFocus);
      requestSequence.current++;
    };
  }, [load, settings]);

  async function completeTask(taskId: number) {
    if (!settings || !currentStaffId || !visibleTasks.some((task) => task.id === taskId && task.assignee_id === currentStaffId)) return;
    setCompletingTaskId(taskId);
    try {
      await taskApi.action(settings, taskId, "complete");
      if (!mounted.current) return;
      await wait(180);
      if (mounted.current) setTasks((current) => current.filter((task) => task.id !== taskId));
    } catch (caught) {
      if (mounted.current) setError(errorMessage(caught));
    } finally {
      if (mounted.current) setCompletingTaskId(null);
    }
  }

  async function addTask(event: React.FormEvent) {
    event.preventDefault();
    const value = title.trim();
    if (!value || !settings || !currentStaffId) return;
    setSaving(true);
    setError("");
    try {
      const endOfToday = new Date();
      endOfToday.setHours(23, 59, 0, 0);
      const created = await taskApi.createTask(settings, {
        title: value,
        content: "",
        assignee_id: currentStaffId,
        priority: 1,
        due_at: endOfToday.toISOString(),
        remind_enabled: false,
        remind_interval_minutes: 300,
      });
      if (!mounted.current) return;
      if (addedTaskTimer.current !== null) window.clearTimeout(addedTaskTimer.current);
      setTitle("");
      setAddedTaskId(created.id);
      await load();
      if (!mounted.current) return;
      addedTaskTimer.current = window.setTimeout(() => {
        addedTaskTimer.current = null;
        if (mounted.current) setAddedTaskId(null);
      }, 620);
    } catch (caught) {
      if (mounted.current) setError(errorMessage(caught));
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  async function toggleLocked() {
    const next = !locked;
    setLocked(next);
    await window.signalDesk?.setTaskWidgetLocked(next);
  }

  async function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    await window.signalDesk?.setTaskWidgetCollapsed(next);
  }

  return (
    <section className={`task-widget${locked ? " locked" : ""}${collapsed ? " collapsed" : ""}`}>
      <header className="task-widget-header">
        <div>
          <strong>{activeFilter === "mine" ? "我的待办" : "团队待办"}</strong>
          <span>
            {visibleTasks.length} 项未完成 · {activeFilter === "mine" ? verifiedUser?.displayName || "我的任务" : "全部人员"}
          </span>
        </div>
        <nav aria-label="桌面挂件操作">
          {!collapsed && (
            <>
              <button
                type="button"
                title={loading ? "正在刷新" : "刷新"}
                aria-label="刷新"
                disabled={loading}
                onClick={load}
              >
                <RefreshCw size={16} className={loading ? "spin" : ""} />
              </button>
              {verifiedUser && <button
                type="button"
                title="打开任务中心"
                aria-label="打开任务中心"
                onClick={() => window.signalDesk?.openTaskWorkspace()}
              >
                <Expand size={16} />
              </button>}
              {canViewTeam && <button
                className={activeFilter === "all" ? "active" : ""}
                type="button"
                title={activeFilter === "mine" ? "显示全部人员" : "只显示我的任务"}
                aria-label={activeFilter === "mine" ? "显示全部人员" : "只显示我的任务"}
                onClick={() => setFilter((current) => current === "mine" ? "all" : "mine")}
              >
                <Filter size={16} />
              </button>}
              <button
                className={locked ? "active" : ""}
                type="button"
                title={locked ? "解除位置锁定" : "锁定位置"}
                aria-label={locked ? "解除位置锁定" : "锁定位置"}
                onClick={toggleLocked}
              >
                {locked ? <Lock size={16} /> : <Unlock size={16} />}
              </button>
            </>
          )}
          <button
            type="button"
            title={collapsed ? "展开挂件" : "收起挂件"}
            aria-label={collapsed ? "展开挂件" : "收起挂件"}
            onClick={toggleCollapsed}
          >
            {collapsed ? <Maximize2 size={16} /> : <Minimize2 size={16} />}
          </button>
          <button
            type="button"
            title="关闭桌面挂件"
            aria-label="关闭桌面挂件"
            onClick={() => window.signalDesk?.hideTaskWidget()}
          >
            <X size={16} />
          </button>
        </nav>
      </header>

      <div className="task-widget-list" aria-live="polite" aria-busy={loading}>
        {error && <div className="task-widget-message error">{error}</div>}
        {!error && !loading && visibleTasks.length === 0 && (
          <div className="task-widget-message">当前没有未完成任务</div>
        )}
        {visibleTasks.map((task) => {
          const overdue = isOverdue(task);
          return (
            <article
              className={`task-widget-row${overdue ? " overdue-task" : ""}${!overdue && task.priority === 3 ? " urgent-task" : ""}${completingTaskId === task.id ? " completing" : ""}${addedTaskId === task.id ? " added" : ""}`}
              key={task.id}
            >
              <button
                className="task-widget-check"
                type="button"
                aria-label={task.assignee_id === currentStaffId
                  ? `完成任务：${task.title}`
                  : `仅负责人可完成：${task.title}`}
                title={task.assignee_id === currentStaffId ? "完成任务" : "只有任务负责人可以完成"}
                disabled={task.assignee_id !== currentStaffId}
                onClick={() => completeTask(task.id)}
              >
                <Check size={13} />
              </button>
              <button
                className="task-widget-task"
                type="button"
                title={`${task.title} · ${task.task_no}`}
                onClick={() => window.signalDesk?.openTaskWorkspace()}
              >
                <strong>{task.title}</strong>
                {activeFilter === "all" && <span>{task.assignee_name}</span>}
              </button>
              <time
                className={`${task.due_at ? "dated" : ""}${overdue ? " overdue" : ""}${!task.due_at && task.priority === 3 ? " urgent" : ""}`}
                dateTime={task.due_at || undefined}
              >
                {formatDue(task)}
              </time>
            </article>
          );
        })}
      </div>

      <form className="task-widget-quick-add" onSubmit={addTask}>
        <input
          aria-label="快速添加任务"
          value={title}
          maxLength={240}
          placeholder={currentStaffId ? "快速添加，回车确认" : "请先登录主程序"}
          disabled={!configured || !currentStaffId || saving}
          onChange={(event) => setTitle(event.target.value)}
        />
        <button
          type="submit"
          title="添加任务"
          aria-label="添加任务"
          disabled={!title.trim() || !currentStaffId || saving}
        >
          <Plus size={17} />
        </button>
      </form>
    </section>
  );
}

export function selectWidgetTasks(tasks: TaskItem[], now = new Date()): TaskItem[] {
  const endOfToday = new Date(now);
  endOfToday.setHours(23, 59, 59, 999);
  return tasks
    .filter((task) => task.status !== "done" && !task.deleted_at)
    .sort((left, right) => {
      const leftRank = widgetTaskRank(left, now, endOfToday);
      const rightRank = widgetTaskRank(right, now, endOfToday);
      const leftDue = left.due_at ? new Date(left.due_at).getTime() : Number.MAX_SAFE_INTEGER;
      const rightDue = right.due_at ? new Date(right.due_at).getTime() : Number.MAX_SAFE_INTEGER;
      return leftRank - rightRank
        || leftDue - rightDue
        || right.priority - left.priority
        || left.id - right.id;
    });
}

function widgetTaskRank(task: TaskItem, now: Date, endOfToday: Date): number {
  if (!task.due_at) return task.priority === 3 ? 2 : 4;
  const due = new Date(task.due_at);
  if (due < now) return 0;
  if (due <= endOfToday) return 1;
  return task.priority === 3 ? 2 : 3;
}

function isOverdue(task: TaskItem): boolean {
  return Boolean(task.due_at && new Date(task.due_at) < new Date());
}

function formatDue(task: TaskItem): string {
  if (!task.due_at) return task.priority === 3 ? "紧急" : "未排期";
  const due = new Date(task.due_at);
  return `${due.getMonth() + 1}/${due.getDate()}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "未知错误");
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
