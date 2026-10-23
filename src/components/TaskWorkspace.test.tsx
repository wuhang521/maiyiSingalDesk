import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defaultSettings } from "../data/demo";
import { taskApi } from "../lib/taskApi";
import type { TaskItem } from "../types";
import { TaskWorkspace } from "./TaskWorkspace";

vi.mock("../lib/taskApi", () => ({
  taskApi: {
    listTasks: vi.fn(),
    listStaff: vi.fn(),
  },
}));

const tasks: TaskItem[] = [
  createTask({ id: 1, task_no: "T001", title: "整理客户反馈", assignee_id: 1, assignee_name: "杰哥", project_name: "增长项目" }),
  createTask({ id: 2, task_no: "T002", title: "确认下周排期", assignee_id: 1, assignee_name: "杰哥", project_name: "增长项目" }),
  createTask({ id: 3, task_no: "T003", title: "准备运营素材", assignee_id: 2, assignee_name: "敏总" }),
];

describe("TaskWorkspace", () => {
  beforeEach(() => {
    vi.mocked(taskApi.listTasks).mockResolvedValue({ items: tasks, total: tasks.length });
    vi.mocked(taskApi.listStaff).mockResolvedValue([
      { id: 1, name: "杰哥", enabled: true, channels: [], created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" },
      { id: 2, name: "敏总", enabled: true, channels: [], created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z" },
    ]);
  });

  it("可以按人员和项目预览现有任务", async () => {
    const user = userEvent.setup();
    const settings = {
      ...defaultSettings,
      backendUrl: "https://task.example.com",
      backendAuthToken: "test-token",
    };

    render(<TaskWorkspace settings={settings} searchQuery="" currentStaffId={1} />);
    expect(screen.queryByRole("button", { name: "用户管理" })).not.toBeInTheDocument();

    expect(await screen.findByText("整理客户反馈")).toBeInTheDocument();
    expect(screen.getAllByTitle("完成")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "刷新任务" }));
    await waitFor(() => expect(taskApi.listTasks).toHaveBeenCalledTimes(2));
    expect(document.querySelector(".task-filters button")?.textContent).toBe("全部");
    await user.click(screen.getByRole("button", { name: "按人员" }));
    expect(screen.getByRole("heading", { name: "杰哥" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "敏总" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "按项目" }));
    expect(screen.getByRole("heading", { name: "增长项目" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "未分类" })).toBeInTheDocument();
    expect(screen.getByText(/尚未标注项目的任务统一显示为/)).toBeInTheDocument();
  });

  it("只有管理员显示用户管理入口", () => {
    render(<TaskWorkspace settings={{ ...defaultSettings, backendAuthToken: "test-token" }} searchQuery="" currentStaffId={1} canManageMembers />);
    expect(screen.getByRole("button", { name: "用户管理" })).toBeInTheDocument();
  });
});

function createTask(update: Partial<TaskItem>): TaskItem {
  return {
    id: 0,
    task_no: "T000",
    title: "测试任务",
    content: "",
    assignee_id: 1,
    assignee_name: "测试人员",
    creator_name: "测试创建人",
    status: "todo",
    priority: 1,
    due_at: null,
    remind_enabled: false,
    remind_interval_minutes: 120,
    next_remind_at: null,
    last_reminded_at: null,
    reminder_count: 0,
    completed_at: null,
    source_platform: "client",
    deleted_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...update,
  };
}
