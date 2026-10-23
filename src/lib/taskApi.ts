import type {
  AppSettings,
  StaffMember,
  StaffPlatform,
  TaskDraft,
  TaskItem,
  TaskListState,
  TaskStatus,
} from "../types";

interface TaskListResponse {
  items: TaskItem[];
  total: number;
}

interface StaffDraft {
  name: string;
  platform?: StaffPlatform;
  recipientId?: string;
}

function endpoint(settings: AppSettings, path: string): string {
  const base = settings.backendUrl.trim().replace(/\/+$/, "");
  if (!base) throw new Error("请先在设置中填写任务后台地址");
  return `${base}${path}`;
}

async function request<T>(
  settings: AppSettings,
  path: string,
  init?: RequestInit,
): Promise<T> {
  if (!settings.backendAuthToken.trim()) {
    throw new Error("请先在设置中填写任务后台访问令牌");
  }
  const response = await fetch(endpoint(settings, path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${settings.backendAuthToken.trim()}`,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = `后台返回 HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // 保留 HTTP 状态作为错误信息。
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const taskApi = {
  async listTasks(
    settings: AppSettings,
    state: TaskListState,
    search = "",
    assigneeId?: number,
  ): Promise<TaskListResponse> {
    const query = new URLSearchParams({ state });
    if (search.trim()) query.set("search", search.trim());
    if (assigneeId) query.set("assignee_id", String(assigneeId));
    return request(settings, `/api/v1/tasks?${query}`);
  },

  listStaff(settings: AppSettings, includeDisabled = true): Promise<StaffMember[]> {
    return request(settings, `/api/v1/staff?include_disabled=${includeDisabled}`);
  },

  createTask(settings: AppSettings, draft: TaskDraft): Promise<TaskItem> {
    return request(settings, "/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({
        ...draft,
        creator_name: settings.operatorName.trim() || "客户端用户",
        source_platform: "client",
      }),
    });
  },

  updateTask(
    settings: AppSettings,
    taskId: number,
    update: Partial<TaskDraft> & { status?: TaskStatus },
  ): Promise<TaskItem> {
    return request(settings, `/api/v1/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify({
        ...update,
        operator_name: settings.operatorName.trim() || "客户端用户",
      }),
    });
  },

  deleteTask(settings: AppSettings, taskId: number): Promise<TaskItem> {
    const operator = encodeURIComponent(settings.operatorName.trim() || "客户端用户");
    return request(settings, `/api/v1/tasks/${taskId}?operator_name=${operator}`, {
      method: "DELETE",
    });
  },

  action(
    settings: AppSettings,
    taskId: number,
    action: "complete" | "reopen" | "restore",
  ): Promise<TaskItem> {
    return request(settings, `/api/v1/tasks/${taskId}/${action}`, {
      method: "POST",
      body: JSON.stringify({
        operator_name: settings.operatorName.trim() || "客户端用户",
      }),
    });
  },

  createStaff(settings: AppSettings, draft: StaffDraft): Promise<StaffMember> {
    const channels =
      draft.platform && draft.recipientId?.trim()
        ? [
            {
              platform: draft.platform,
              recipient_id: draft.recipientId.trim(),
              enabled: true,
            },
          ]
        : [];
    return request(settings, "/api/v1/staff", {
      method: "POST",
      body: JSON.stringify({ name: draft.name.trim(), enabled: true, channels }),
    });
  },

  updateStaff(
    settings: AppSettings,
    staffId: number,
    update: { name?: string; enabled?: boolean },
  ): Promise<StaffMember> {
    return request(settings, `/api/v1/staff/${staffId}`, {
      method: "PATCH",
      body: JSON.stringify(update),
    });
  },

  addStaffChannel(
    settings: AppSettings,
    staffId: number,
    platform: StaffPlatform,
    recipientId: string,
  ): Promise<StaffMember> {
    return request(settings, `/api/v1/staff/${staffId}/channels`, {
      method: "POST",
      body: JSON.stringify({
        platform,
        recipient_id: recipientId.trim(),
        enabled: true,
      }),
    });
  },

  deleteStaffChannel(
    settings: AppSettings,
    staffId: number,
    channelId: number,
  ): Promise<void> {
    return request(settings, `/api/v1/staff/${staffId}/channels/${channelId}`, {
      method: "DELETE",
    });
  },
};
