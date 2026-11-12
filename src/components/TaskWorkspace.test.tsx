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
    createTask: vi.fn(),
  },
}));

const tasks: TaskItem[] = [
  createTask({ id: 1, task_no: "T001", title: "整理客户反馈", assignee_id: 1, assignee_name: "杰哥", project_name: "增长项目" }),
  createTask({ id: 2, task_no: "T002", title: "确认下周排期", assignee_id: 1, assignee_name: "杰哥", project_name: "增长项目" }),
  createTask({ id: 3, task_no: "T003", title: "准备运营素材", assignee_id: 2, assignee_name: "敏总" }),
];

describe("TaskWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

    render(<TaskWorkspace settings={settings} searchQuery="" currentStaffId={1} isAdmin />);
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

  it("普通成员只查询和展示分配给自己的任务，创建时固定分配给本人", async () => {
    const user = userEvent.setup();
    const settings = { ...defaultSettings, backendUrl: "https://task.example.com", backendAuthToken: "test-token" };
    vi.mocked(taskApi.createTask)
      .mockRejectedValueOnce(new Error("当前账号无权创建任务"))
      .mockResolvedValue(tasks[2]);
    render(<TaskWorkspace settings={settings} searchQuery="" currentStaffId={2} />);

    expect(await screen.findByText("准备运营素材")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    expect(screen.queryByText("整理客户反馈")).not.toBeInTheDocument();
    expect(screen.queryByText("杰哥")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "用户管理" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "按人员" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("负责人筛选")).not.toBeInTheDocument();
    expect(taskApi.listTasks).toHaveBeenCalledWith(settings, "active", "", 2);
    expect(taskApi.listStaff).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "按项目" }));
    expect(screen.queryByRole("heading", { name: "增长项目" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "新建任务" }));
    expect(screen.queryByLabelText("负责人")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("任务标题"), "自己的新任务");
    await user.click(screen.getByRole("button", { name: "保存任务" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前账号无权创建任务");
    expect(screen.getByLabelText("任务标题")).toHaveValue("自己的新任务");
    await user.click(screen.getByRole("button", { name: "保存任务" }));
    await waitFor(() => expect(screen.queryByLabelText("任务标题")).not.toBeInTheDocument());
    expect(taskApi.createTask).toHaveBeenCalledWith(settings, expect.objectContaining({ title: "自己的新任务", assignee_id: 2 }));
    expect(taskApi.createTask).toHaveBeenCalledTimes(2);
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
