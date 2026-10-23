import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { taskApi } from "../lib/taskApi";
import type { TaskItem } from "../types";
import { selectWidgetTasks, TaskWidget } from "./TaskWidget";

vi.mock("../lib/taskApi", () => ({
  taskApi: {
    listStaff: vi.fn(),
    listTasks: vi.fn(),
    action: vi.fn(),
    createTask: vi.fn(),
  },
}));

const currentTask: TaskItem = {
  id: 1,
  task_no: "T1",
  title: "完成健康内容审核",
  content: "",
  assignee_id: 7,
  assignee_name: "敏总",
  creator_name: "老板",
  status: "todo",
  priority: 3,
  due_at: null,
  remind_enabled: false,
  remind_interval_minutes: 300,
  next_remind_at: null,
  last_reminded_at: null,
  reminder_count: 0,
  completed_at: null,
  source_platform: "client",
  deleted_at: null,
  created_at: "2026-08-18T01:00:00Z",
  updated_at: "2026-08-18T01:00:00Z",
};

describe("桌面任务挂件", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    window.localStorage.setItem(
      "signaldesk.settings.v1",
      JSON.stringify({
        backendUrl: "https://api.example.com",
        backendAuthToken: "shared-key",
        operatorName: "敏总",
      }),
    );
    vi.mocked(taskApi.listStaff).mockResolvedValue([
      {
        id: 7,
        name: "敏总",
        enabled: true,
        channels: [],
        created_at: "2026-08-18T01:00:00Z",
        updated_at: "2026-08-18T01:00:00Z",
      },
    ]);
    vi.mocked(taskApi.listTasks).mockResolvedValue({ items: [currentTask], total: 1 });
    vi.mocked(taskApi.action).mockResolvedValue({ ...currentTask, status: "done" });
    vi.mocked(taskApi.createTask).mockResolvedValue(currentTask);
    Object.defineProperty(window, "signalDesk", {
      configurable: true,
      value: {
        loadAuthSession: vi.fn().mockResolvedValue({
          backendUrl: "https://api.example.com",
          accessToken: "personal-token",
          expiresAt: "2026-11-16T08:00:00",
          user: {
            id: 3,
            username: "boss",
            displayName: "敏总",
            staffId: 7,
            role: "admin",
          },
        }),
        getTaskWidgetState: vi.fn().mockResolvedValue({ locked: false, collapsed: false }),
        setTaskWidgetLocked: vi.fn().mockResolvedValue(true),
        setTaskWidgetCollapsed: vi.fn().mockResolvedValue(true),
        openTaskWorkspace: vi.fn().mockResolvedValue(true),
        hideTaskWidget: vi.fn().mockResolvedValue(true),
      },
    });
  });

  it("显示当前操作人的未完成任务并支持快速完成", async () => {
    const user = userEvent.setup();
    render(<TaskWidget />);

    expect(await screen.findByText("完成健康内容审核")).toBeInTheDocument();
    expect(taskApi.listTasks).toHaveBeenCalledWith(expect.anything(), "active", "", 7);
    await user.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(taskApi.listTasks).toHaveBeenCalledTimes(2));

    await user.click(screen.getByRole("button", { name: "完成任务：完成健康内容审核" }));
    expect(taskApi.action).toHaveBeenCalledWith(expect.anything(), 1, "complete");
    await waitFor(() =>
      expect(screen.queryByText("完成健康内容审核")).not.toBeInTheDocument(),
    );
  });

  it("回车快速创建一条当天任务", async () => {
    const user = userEvent.setup();
    render(<TaskWidget />);

    const input = await screen.findByRole("textbox", { name: "快速添加任务" });
    await user.type(input, "整理周会资料{enter}");

    expect(taskApi.createTask).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        title: "整理周会资料",
        assignee_id: 7,
        priority: 1,
        remind_enabled: false,
      }),
    );
  });

  it("卸载时清除新增任务的动画定时器和自动刷新", async () => {
    vi.useFakeTimers();
    try {
      const { unmount } = render(<TaskWidget />);
      await act(async () => {});
      const input = screen.getByRole("textbox", { name: "快速添加任务" });
      fireEvent.change(input, { target: { value: "卸载前新增任务" } });
      await act(async () => { fireEvent.submit(input.closest("form")!); });
      expect(taskApi.createTask).toHaveBeenCalledTimes(1);
      expect(document.querySelector(".task-widget-row.added")).toBeInTheDocument();
      expect(vi.getTimerCount()).toBeGreaterThan(0);
      unmount();
      expect(vi.getTimerCount()).toBe(0);
      await act(async () => { vi.advanceTimersByTime(1_000); });
    } finally {
      vi.useRealTimers();
    }
  });

  it("创建请求在卸载后才返回时，不再刷新或安排动画定时器", async () => {
    vi.useFakeTimers();
    try {
      let finishCreate!: (task: TaskItem) => void;
      vi.mocked(taskApi.createTask).mockImplementationOnce(() => new Promise((resolve) => { finishCreate = resolve; }));
      const { unmount } = render(<TaskWidget />);
      await act(async () => {});
      const input = screen.getByRole("textbox", { name: "快速添加任务" });
      fireEvent.change(input, { target: { value: "尚在提交的任务" } });
      fireEvent.submit(input.closest("form")!);
      expect(taskApi.createTask).toHaveBeenCalledTimes(1);
      const initialLoads = vi.mocked(taskApi.listTasks).mock.calls.length;
      unmount();
      await act(async () => { finishCreate(currentTask); });
      expect(taskApi.listTasks).toHaveBeenCalledTimes(initialLoads);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("查看他人任务时不能代替负责人完成", async () => {
    vi.mocked(taskApi.listTasks).mockResolvedValue({
      items: [
        currentTask,
        { ...currentTask, id: 2, title: "杰哥的任务", assignee_id: 8, assignee_name: "杰哥" },
      ],
      total: 2,
    });

    render(<TaskWidget />);

    const otherTaskButton = await screen.findByRole("button", {
      name: "仅负责人可完成：杰哥的任务",
    });
    expect(otherTaskButton).toBeDisabled();
    expect(screen.getByRole("button", { name: "完成任务：完成健康内容审核" })).toBeEnabled();
  });

  it("展示全部未完成任务，并按逾期、今天、紧急、未来和未排期排序", () => {
    const now = new Date("2026-08-18T10:00:00+08:00");
    const tasks: TaskItem[] = [
      { ...currentTask, id: 1, title: "无日期紧急", priority: 3 },
      { ...currentTask, id: 2, title: "无日期普通", priority: 1 },
      { ...currentTask, id: 3, title: "今天任务", due_at: "2026-08-18T12:00:00+08:00" },
      { ...currentTask, id: 4, title: "明天任务", priority: 1, due_at: "2026-08-19T12:00:00+08:00" },
      { ...currentTask, id: 5, title: "逾期任务", due_at: "2026-08-17T12:00:00+08:00" },
      { ...currentTask, id: 6, title: "已完成任务", status: "done" },
    ];

    expect(selectWidgetTasks(tasks, now).map((task) => task.title)).toEqual([
      "逾期任务",
      "今天任务",
      "无日期紧急",
      "明天任务",
      "无日期普通",
    ]);
  });

  it("显示 Hermes 创建的普通未排期任务", async () => {
    vi.mocked(taskApi.listTasks).mockResolvedValue({
      items: [{ ...currentTask, title: "杰哥跟进渠道素材", priority: 1, due_at: null }],
      total: 1,
    });

    render(<TaskWidget />);

    expect(await screen.findByText("杰哥跟进渠道素材")).toBeInTheDocument();
    expect(screen.getByText("未排期")).toBeInTheDocument();
  });

  it("使用月/日格式展示截止日期", async () => {
    vi.mocked(taskApi.listTasks).mockResolvedValue({
      items: [{ ...currentTask, due_at: "2026-08-17T12:00:00" }],
      total: 1,
    });

    render(<TaskWidget />);

    expect(await screen.findByText("8/17")).toBeInTheDocument();
  });

  it("展示全部任务并支持锁定和收起挂件", async () => {
    const user = userEvent.setup();
    const tasks = Array.from({ length: 9 }, (_, index) => ({
      ...currentTask,
      id: index + 1,
      title: `待办任务 ${index + 1}`,
    }));
    vi.mocked(taskApi.listTasks).mockResolvedValue({ items: tasks, total: tasks.length });

    render(<TaskWidget />);

    expect(await screen.findByText("待办任务 9")).toBeInTheDocument();
    expect(screen.queryByText(/打开任务中心查看/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "锁定位置" }));
    expect(window.signalDesk?.setTaskWidgetLocked).toHaveBeenCalledWith(true);
    await user.click(screen.getByRole("button", { name: "收起挂件" }));
    expect(window.signalDesk?.setTaskWidgetCollapsed).toHaveBeenCalledWith(true);
    expect(screen.getByRole("button", { name: "展开挂件" })).toBeInTheDocument();
  });
});
