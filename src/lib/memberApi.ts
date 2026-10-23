import type { AppSettings, StaffMember } from "../types";

export interface ManagedMember extends StaffMember {
  removed_at: string | null;
  user: {
    id: number;
    username: string;
    role: "admin" | "member";
    enabled: boolean;
    last_login_at: string | null;
  } | null;
}

export interface MemberDraft {
  name: string;
  username?: string;
  password?: string;
  role: "admin" | "member";
  enabled: boolean;
}

async function request<T>(settings: AppSettings, path = "", init?: RequestInit): Promise<T> {
  const base = settings.backendUrl.trim().replace(/\/+$/, "");
  if (!base || !settings.backendAuthToken.trim()) throw new Error("请先登录后再管理用户");
  let response: Response;
  try {
    response = await fetch(`${base}/api/v1/admin/members${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.backendAuthToken}` },
    });
  } catch {
    throw new Error("无法连接用户管理服务，请检查网络后重试");
  }
  if (!response.ok) {
    if (response.status === 404) throw new Error("后台尚未提供用户管理功能，请先更新后台并执行数据库迁移");
    let message = `操作失败（HTTP ${response.status}），请稍后重试`;
    const payload = await response.json().catch(() => null);
    if (typeof payload?.detail === "string") message = payload.detail;
    else if (response.status === 422) message = "请检查姓名、登录账号和密码长度后重试";
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const memberApi = {
  list: (settings: AppSettings) => request<ManagedMember[]>(settings),
  create: (settings: AppSettings, draft: MemberDraft) => request<ManagedMember>(settings, "", {
    method: "POST", body: JSON.stringify(draft),
  }),
  update: (settings: AppSettings, id: number, draft: MemberDraft) => request<ManagedMember>(settings, `/${id}`, {
    method: "PATCH", body: JSON.stringify(draft),
  }),
  remove: (settings: AppSettings, id: number) => request<ManagedMember>(settings, `/${id}`, { method: "DELETE" }),
  restore: (settings: AppSettings, id: number) => request<ManagedMember>(settings, `/${id}/restore`, { method: "POST" }),
};
