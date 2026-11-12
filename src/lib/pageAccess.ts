import type { AuthUser, DesktopPage, ViewKey } from "../types";

export const DESKTOP_PAGES = [
  { id: "today", label: "今日" },
  { id: "inbox", label: "全部信息" },
  { id: "channels", label: "智能频道" },
  { id: "tasks", label: "任务中心" },
  { id: "internal", label: "内部管理系统" },
  { id: "knowledge", label: "知识库" },
  { id: "content", label: "内容生成" },
  { id: "saved", label: "收藏" },
  { id: "trash", label: "回收站" },
  { id: "settings", label: "设置" },
] as const satisfies readonly { id: DesktopPage; label: string }[];

export function canAccessDesktopPage(user: AuthUser | undefined, page: ViewKey | "internal"): boolean {
  if (page === "members") return user?.role === "admin";
  return user?.role === "admin" || user?.allowedPages == null || user.allowedPages.includes(page);
}
