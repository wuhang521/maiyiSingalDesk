import type { AppSettings, DesktopPage, StaffMember } from "../types";

export interface ManagedMember extends StaffMember {
  removed_at: string | null;
  user: {
    id: number;
    username: string;
    role: "admin" | "member";
    enabled: boolean;
    last_login_at: string | null;
    allowed_pages?: DesktopPage[] | null;
  } | null;
}

export interface MemberDraft {
  name: string;
  username?: string;
  password?: string;
  role: "admin" | "member";
  enabled: boolean;
  allowed_pages?: DesktopPage[] | null;
}

export interface WechatPairing {
  id: string;
  state: "waiting" | "ready" | "expired" | "confirmed";
  expires_at: string;
  scan_url?: string;
  recipient_hint?: string;
  replacing?: boolean;
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
    const pairing = path.includes("/wechat-pairing");
    if (response.status === 404 && !pairing) throw new Error("后台尚未提供用户管理功能，请先更新后台并执行数据库迁移");
    let message = `操作失败（HTTP ${response.status}），请稍后重试`;
    const payload = await response.json().catch(() => null);
    if (pairing && response.status === 404 && (!payload?.detail || payload.detail === "Not Found")) message = "后台尚未启用微信扫码绑定";
    else if (typeof payload?.detail === "string") message = payload.detail;
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
  startWechatPairing: (settings: AppSettings, staffId: number, signal: AbortSignal) =>
    request<WechatPairing>(settings, `/${staffId}/wechat-pairing`, { method: "POST", signal }),
  readWechatPairing: (settings: AppSettings, staffId: number, id: string, signal: AbortSignal) =>
    request<WechatPairing>(settings, `/${staffId}/wechat-pairing/${id}`, { signal }),
  cancelWechatPairing: (settings: AppSettings, staffId: number, id: string) =>
    request<WechatPairing>(settings, `/${staffId}/wechat-pairing/${id}`, { method: "DELETE" }),
  confirmWechatPairing: (settings: AppSettings, staffId: number, id: string) =>
    request<WechatPairing>(settings, `/${staffId}/wechat-pairing/${id}/confirm`, { method: "POST" }),
};
