import type { AppSettings } from "../types";

export type ContentData = Record<string, any>;
export interface ContentField {
  key: string; label: string; kind: string; required: boolean; hint?: string;
  options?: string[]; fields?: ContentField[]; show_if?: [string, unknown];
}
export interface ContentMember { id: number; name: string }
export interface ContentSchema {
  account_fields: ContentField[]; episode_fields: ContentField[];
  steps: { title: string; description: string; fields: ContentField[] }[];
  current_user: ContentMember;
}
export interface ContentAccount {
  id: string; revision: number; data: ContentData; active_data: ContentData | null;
  active_version: number; status: string; owner: ContentMember;
  config_history: { version: number; data: ContentData; at: string; actor: ContentMember }[];
}
export interface ContentStep {
  data: ContentData; version: number; status: string; unresolved_items: string[]; last_error?: string;
  history: { version: number; data: ContentData; diff: string; actor: ContentMember; at: string; reason: string }[];
}
export interface ContentEpisode {
  id: string; revision: number; account_id: string; account_config_version: number; account_snapshot: ContentData;
  brief: ContentData; episode_number: number; current_step: number; status: string; assignee: number;
  steps: ContentStep[];
  approvals: { step: number; version: number; decision: string; comment: string; reviewer: ContentMember; role: string; at: string }[];
  exports: { format: string; at: string; actor: ContentMember; versions: number[] }[];
}
export interface ContentSummary {
  id: string; account_id: string; title: string; status: string; current_step: number;
  updated_at: string; episode_number: number; assignee: number;
  approved_topic?: string;
}

async function request<T>(settings: AppSettings, path: string, method = "GET", body?: unknown): Promise<T> {
  if (!settings.backendUrl.trim() || !settings.backendAuthToken.trim()) throw new Error("请先登录并连接后台，再使用内容创作工作台。");
  const response = await fetch(`${settings.backendUrl.trim().replace(/\/+$/, "")}/api/v1/content${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.backendAuthToken.trim()}` },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const detail = typeof result.detail === "string" ? result.detail : result.detail ? "部分字段格式不正确，请检查填写内容。" : "";
    throw new Error(detail || (response.status === 404 ? "后台尚未安装内容创作模块，请更新后台后重试。" : `请求失败（${response.status}），请重试。`));
  }
  return response.json();
}

export const contentApi = {
  schema: (s: AppSettings) => request<ContentSchema>(s, "/schema"),
  members: (s: AppSettings) => request<ContentMember[]>(s, "/members"),
  accounts: (s: AppSettings) => request<ContentAccount[]>(s, "/accounts"),
  saveAccount: (s: AppSettings, account: ContentAccount | undefined, data: ContentData, action: string) =>
    request<ContentAccount>(s, account ? `/accounts/${account.id}` : "/accounts", account ? "PUT" : "POST", { revision: account?.revision || 0, data, action }),
  episodes: (s: AppSettings) => request<ContentSummary[]>(s, "/episodes"),
  episode: (s: AppSettings, id: string) => request<ContentEpisode>(s, `/episodes/${id}`),
  create: (s: AppSettings, accountId: string, brief: ContentData) => request<ContentEpisode>(s, "/episodes", "POST", { account_id: accountId, brief }),
  saveBrief: (s: AppSettings, e: ContentEpisode, data: ContentData) => request<ContentEpisode>(s, `/episodes/${e.id}/brief`, "PUT", { revision: e.revision, data }),
  saveStep: (s: AppSettings, e: ContentEpisode, step: number, data: ContentData, unresolved: string[], changeKind: string) =>
    request<ContentEpisode>(s, `/episodes/${e.id}/steps/${step}`, "PUT", { revision: e.revision, data, unresolved_items: unresolved, change_kind: changeKind }),
  generate: (s: AppSettings, e: ContentEpisode, step: number, instruction = "") =>
    request<ContentEpisode>(s, `/episodes/${e.id}/steps/${step}/generate`, "POST", { revision: e.revision, instruction }),
  review: (s: AppSettings, e: ContentEpisode, step: number, decision: string, comment: string) =>
    request<ContentEpisode>(s, `/episodes/${e.id}/steps/${step}/review`, "POST", { revision: e.revision, version: e.steps[step].version, decision, comment }),
  migrate: (s: AppSettings, e: ContentEpisode) => request<ContentEpisode>(s, `/episodes/${e.id}/migrate`, "POST", { revision: e.revision }),
  recover: (s: AppSettings, e: ContentEpisode, step: number) => request<ContentEpisode>(s, `/episodes/${e.id}/steps/${step}/recover`, "POST", { revision: e.revision }),
  export: (s: AppSettings, e: ContentEpisode, format: string) => request<{ filename: string; content: string; episode: ContentEpisode }>(s, `/episodes/${e.id}/export`, "POST", { revision: e.revision, format }),
  importMaterial: async (s: AppSettings, file: File) => {
    if (file.size > 10 * 1024 * 1024) throw new Error("文件不能超过 10 MB。");
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("文件读取失败，请重试。"));
      reader.readAsDataURL(file);
    });
    return request<{ content: string }>(s, "/materials/import", "POST", { name: file.name, base64_data: data });
  },
};

export function blankContent(fields: ContentField[]): ContentData {
  return Object.fromEntries(fields.map((f) => [f.key,
    ["records", "multi", "lines", "sources", "claims"].includes(f.kind) ? [] : f.kind === "object" ? blankContent(f.fields || []) :
      f.kind === "boolean" ? false : ["number", "reviewer"].includes(f.kind) ? 0 : "",
  ]));
}

export const contentStatus: Record<string, string> = {
  draft: "草稿", pending: "待确认", active: "已生效", disabled: "已停用", approved: "已通过",
  returned: "已退回", needs_review: "待复核", generating: "生成中", final: "已定稿",
};
