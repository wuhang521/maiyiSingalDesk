import type { AppSettings } from "../types";

export interface KnowledgeItem {
  id: string; title: string; source_url: string; kind: string; publisher: string; tags: string[];
  status: string; status_note: string; revision: number; summary: string; method: string;
  deleted_at: string | null; created_at: string; updated_at: string;
}
export interface KnowledgeDetail extends KnowledgeItem {
  content: string;
  content_revision?: number;
  assets: { id: string; filename: string; size: number }[];
  history: { revision: number; method: string; created_at: string }[];
}
export interface KnowledgeEvidence {
  index?: number; item_id: string; chunk_id: string; revision: number; title: string;
  source_url: string; locator: string; text: string;
}
export interface KnowledgeAnswer { answer: string; mode: string; citations: KnowledgeEvidence[] }
export interface KnowledgeSemanticInfo { state: string; source_revision: number; chunk_count: number }
export interface KnowledgeMediaPart { id: string; asset_id: string; revision: number; method: string; content: string; created_at: string }
export interface KnowledgeUsage {
  daily_submissions: number; monthly_submissions: number; daily_limit: number; monthly_limit: number;
  daily_remaining: number; monthly_remaining: number; next_daily_reset: string; timezone: string; accounting: string;
}
export interface KnowledgeCapabilities { media_merge_available?: boolean; semantic_max_chunks?: number; costs_available?: boolean; silk_available?: boolean }
export interface KnowledgeCosts { enabled: boolean; daily_exposure_cny: string; monthly_exposure_cny: string; daily_limit_cny: string; monthly_limit_cny: string; daily_unpriced: number; monthly_unpriced: number; request_ceiling_cny: Record<string, string> }
export interface KnowledgeCostRow { usage_id: string; operation: string; reserved_cny: string | null; actual_cny: string | null; state: string; owner_id?: number; created_at: string; provider_requests?: { provider: string; request_id: string; response_id: string; http_status: number; usage: Record<string, number>; created_at: string }[] }
export interface KnowledgeOperations {
  job_states: Record<string, number>; oldest_wait_seconds: number; file_cleanup_pending: number;
  remote_cleanup_pending: number; worker_health: string;
  recent_workers?: number; last_worker_seen?: string | null;
  active_alerts?: { kind: string; metrics: Record<string, number>; created_at: string }[];
}
export interface KnowledgeTeam { id: string; name: string; revision: number; can_manage: boolean; is_owner: boolean }
export interface KnowledgePreference { revision: number; desktop_notifications: boolean; hermes_notifications: boolean; admin_alerts: boolean; channel_id: number | null; hermes_ready: boolean; channels?: { id: number; platform: string }[] }
export interface KnowledgeNotification { id: string; item_id: string | null; revision: number; message: string; kind: string; delivery_state: string; created_at: string }
export interface KnowledgeTeamMember { user_id: number; username: string; name: string; role: string; enabled: boolean; is_owner: boolean }
export interface KnowledgeSharedItem { id: string; team_id: string; title: string; publisher: string; tags: string[]; source_url: string; revision: number; shared_at: string; summary: string; content?: string; can_revoke?: boolean }
export interface KnowledgeRevision { revision: number; content: string; method: string; created_at: string }
export interface KnowledgeInput { title: string; source_url: string; publisher: string; tags: string[]; content: string }
export interface KnowledgeOrganization {
  id: string; source_revision: number; state: string; status_note: string; provider: string; model: string; created_at: string;
  payload: { summary?: { text: string; citations: number[] }; points?: { text: string; citations: number[] }[];
    tags?: string[]; citations?: { index: number; locator: string; text: string }[] };
}
export interface KnowledgeCapabilities { teams_available?: boolean; notifications_available?: boolean; semantic_enabled?: boolean; usage_available?: boolean; asr_enabled: boolean; cloud_asr_enabled?: boolean; cloud_asr_provider?: string; cloud_asr_max_minutes?: number; organization_enabled?: boolean; organization_provider?: string; video_ocr_enabled?: boolean; video_ocr_max_frames?: number; ocr_enabled: boolean; ocr_provider?: string; ocr_max_pdf_pages?: number; max_upload_mb: number; scope: string; qa_mode: string; trash_retention_days?: number; cloud_qa_enabled?: boolean; cloud_provider?: string }

function base(settings: AppSettings) {
  if (!settings.backendUrl.trim() || !settings.backendAuthToken.trim()) throw new Error("请先登录后台，再使用个人知识库。");
  return `${settings.backendUrl.trim().replace(/\/+$/, "")}/api/v1/knowledge`;
}

async function responseError(response: Response): Promise<never> {
  const data = await response.json().catch(() => ({}));
  throw new Error(typeof data.detail === "string" ? data.detail : response.status === 404
    ? "后台尚未安装知识库模块，或该资料已经不可访问。请刷新或更新后台。"
    : response.status === 422 ? "部分字段格式不正确，请检查链接、标题和文件格式。"
    : `请求失败（${response.status}），请重试。`);
}

async function request<T>(s: AppSettings, path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${base(s)}${path}`, {
    method, signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000), headers: { Authorization: `Bearer ${s.backendAuthToken.trim()}`, "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) await responseError(response);
  return response.json();
}

export const knowledgeApi = {
  costs: (s: AppSettings, signal?: AbortSignal) => request<KnowledgeCosts>(s, "/costs", "GET", undefined, signal),
  costLedger: (s: AppSettings, offset: number, admin = false, signal?: AbortSignal) => request<{ items: KnowledgeCostRow[] }>(s, `${admin ? "/admin" : ""}/costs/ledger?offset=${offset}`, "GET", undefined, signal),
  reconcileCosts: (s: AppSettings, rows: { usage_id: string; amount_cny: string; invoice_reference: string }[]) => request<{ reconciled: number }>(s, "/admin/costs/reconcile", "POST", { rows, verified_provider_bill: true }),
  mediaParts: (s: AppSettings, id: string, signal?: AbortSignal) => request<{ items: KnowledgeMediaPart[] }>(s, `/items/${id}/media-parts`, "GET", undefined, signal),
  mergeMedia: (s: AppSettings, item: KnowledgeDetail, speech: string, frames: string) => request<KnowledgeDetail>(s, `/items/${item.id}/media-merge`, "POST", { revision: item.revision, speech_id: speech, frames_id: frames, acknowledge_draft: true }),
  confirmMedia: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}/media-merge/confirm`, "POST", { revision: item.revision, confirmation: "已核对" }),
  preferences: (s: AppSettings, signal?: AbortSignal) => request<KnowledgePreference>(s, "/preferences", "GET", undefined, signal),
  savePreferences: (s: AppSettings, p: KnowledgePreference) => request<KnowledgePreference>(s, "/preferences", "PUT", { revision: p.revision, desktop_notifications: p.desktop_notifications, hermes_notifications: p.hermes_notifications, admin_alerts: p.admin_alerts, channel_id: p.channel_id }),
  notifications: (s: AppSettings, signal?: AbortSignal) => request<{ items: KnowledgeNotification[] }>(s, "/notifications", "GET", undefined, signal),
  readNotification: (s: AppSettings, id: string) => request(s, `/notifications/${id}/read`, "POST"),
  retryNotification: (s: AppSettings, id: string) => request(s, `/notifications/${id}/retry`, "POST", { acknowledge_duplicate_risk: true }),
  teams: (s: AppSettings, signal?: AbortSignal) => request<{ items: KnowledgeTeam[] }>(s, "/teams", "GET", undefined, signal),
  createTeam: (s: AppSettings, name: string) => request<KnowledgeTeam>(s, "/teams", "POST", { name }),
  teamMembers: (s: AppSettings, id: string, signal?: AbortSignal) => request<{ items: KnowledgeTeamMember[] }>(s, `/teams/${id}/members`, "GET", undefined, signal),
  setTeamMember: (s: AppSettings, team: KnowledgeTeam, username: string, role: string) => request<{ revision: number }>(s, `/teams/${team.id}/members`, "POST", { username, role, team_revision: team.revision }),
  removeTeamMember: (s: AppSettings, team: KnowledgeTeam, id: number) => request<{ revision: number }>(s, `/teams/${team.id}/members/${id}?revision=${team.revision}`, "DELETE"),
  closeTeam: (s: AppSettings, team: KnowledgeTeam) => request(s, `/teams/${team.id}?revision=${team.revision}`, "DELETE"),
  share: (s: AppSettings, team: KnowledgeTeam, item: KnowledgeDetail) => request(s, `/teams/${team.id}/shares`, "POST", { item_id: item.id, item_revision: item.revision, team_revision: team.revision, share_consent: true }),
  revokeShare: (s: AppSettings, team: KnowledgeTeam, itemId: string) => request(s, `/teams/${team.id}/shares/${itemId}?revision=${team.revision}`, "DELETE"),
  teamItems: (s: AppSettings, id: string, offset: number, signal?: AbortSignal) => request<{ items: KnowledgeSharedItem[]; total: number }>(s, `/teams/${id}/items?offset=${offset}`, "GET", undefined, signal),
  teamDetail: (s: AppSettings, id: string, itemId: string, signal?: AbortSignal) => request<KnowledgeSharedItem>(s, `/teams/${id}/items/${itemId}`, "GET", undefined, signal),
  teamSearch: (s: AppSettings, id: string, question: string) => request<{ items: { item_id: string; revision: number; locator: string; text: string }[] }>(s, `/teams/${id}/search?${new URLSearchParams({ q: question })}`),
  usage: (s: AppSettings, signal?: AbortSignal) => request<KnowledgeUsage>(s, "/usage", "GET", undefined, signal),
  operations: (s: AppSettings, signal?: AbortSignal) => request<KnowledgeOperations>(s, "/admin/operations", "GET", undefined, signal),
  semanticInfo: (s: AppSettings, id: string, signal?: AbortSignal) => request<KnowledgeSemanticInfo>(s, `/items/${id}/semantic-index`, "GET", undefined, signal),
  buildSemantic: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeSemanticInfo>(s, `/items/${item.id}/semantic-index`, "POST", { revision: item.revision, cloud_consent: true }),
  removeSemantic: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeSemanticInfo>(s, `/items/${item.id}/semantic-index?revision=${item.revision}`, "DELETE"),
  semanticSearch: (s: AppSettings, question: string) => request<KnowledgeAnswer>(s, "/semantic-search", "POST", { question, cloud_consent: true }),
  capabilities: (s: AppSettings) => request<KnowledgeCapabilities>(s, "/capabilities"),
  items: (s: AppSettings, q: string, state: string, offset = 0, signal?: AbortSignal) =>
    request<{ items: KnowledgeItem[]; total: number }>(s, `/items?${new URLSearchParams({ q, state, offset: String(offset) })}`, "GET", undefined, signal),
  detail: (s: AppSettings, id: string, signal?: AbortSignal) => request<KnowledgeDetail>(s, `/items/${id}`, "GET", undefined, signal),
  collect: (s: AppSettings, input: KnowledgeInput) => request<KnowledgeDetail>(s, "/items", "POST", input),
  update: (s: AppSettings, item: KnowledgeDetail, input: KnowledgeInput) => request<KnowledgeDetail>(s, `/items/${item.id}`, "PATCH", { ...input, revision: item.revision }),
  retry: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}/retry`, "POST", { revision: item.revision }),
  ocr: (s: AppSettings, item: KnowledgeDetail, assetId: string) => request<KnowledgeDetail>(s, `/items/${item.id}/assets/${assetId}/ocr`, "POST", { revision: item.revision, cloud_consent: true }),
  confirmOcr: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}/ocr/confirm`, "POST", { revision: item.revision, confirmation: "已核对" }),
  asr: (s: AppSettings, item: KnowledgeDetail, assetId: string) => request<KnowledgeDetail>(s, `/items/${item.id}/assets/${assetId}/asr`, "POST", { revision: item.revision, cloud_consent: true }),
  confirmAsr: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}/asr/confirm`, "POST", { revision: item.revision, confirmation: "已核对" }),
  videoOcr: (s: AppSettings, item: KnowledgeDetail, assetId: string) => request<KnowledgeDetail>(s, `/items/${item.id}/assets/${assetId}/video-ocr`, "POST", { revision: item.revision, cloud_consent: true }),
  confirmVideoOcr: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}/video-ocr/confirm`, "POST", { revision: item.revision, confirmation: "已核对" }),
  organization: (s: AppSettings, id: string, signal?: AbortSignal) => request<{ result: KnowledgeOrganization | null }>(s, `/items/${id}/organization`, "GET", undefined, signal),
  organize: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeOrganization>(s, `/items/${item.id}/organization`, "POST", { revision: item.revision, cloud_consent: true }),
  remove: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}?revision=${item.revision}`, "DELETE"),
  restore: (s: AppSettings, item: KnowledgeDetail) => request<KnowledgeDetail>(s, `/items/${item.id}/restore`, "POST", { revision: item.revision }),
  purge: (s: AppSettings, item: KnowledgeDetail, confirmation: string) => request<{ deleted: boolean; files_pending: number }>(s, `/items/${item.id}/purge`, "POST", { revision: item.revision, confirmation }),
  revisions: (s: AppSettings, id: string, before: number, signal?: AbortSignal) => request<{ items: KnowledgeDetail["history"]; total: number; next_before: number | null }>(s, `/items/${id}/revisions?before=${before}`, "GET", undefined, signal),
  revision: (s: AppSettings, id: string, revision: number, signal?: AbortSignal) => request<KnowledgeRevision>(s, `/items/${id}/revisions/${revision}`, "GET", undefined, signal),
  ask: (s: AppSettings, question: string, cloud = false) => request<KnowledgeAnswer>(s, "/ask", "POST", cloud ? { question, mode: "cloud", cloud_consent: true } : { question }),
  upload: async (s: AppSettings, item: KnowledgeDetail, file: File, maxMb: number) => {
    if (!file.size || file.size > maxMb * 1024 * 1024) throw new Error(`文件不能为空，且不能超过 ${maxMb} MB。`);
    const response = await fetch(`${base(s)}/items/${item.id}/assets?${new URLSearchParams({ filename: file.name, revision: String(item.revision) })}`, {
      method: "POST", signal: AbortSignal.timeout(300000), headers: { Authorization: `Bearer ${s.backendAuthToken.trim()}`, "Content-Type": "application/octet-stream" }, body: file,
    });
    if (!response.ok) await responseError(response);
    return response.json() as Promise<KnowledgeDetail>;
  },
  download: async (s: AppSettings, asset: { id: string; filename: string }) => {
    if (window.signalDesk?.downloadKnowledgeAsset) {
      await window.signalDesk.downloadKnowledgeAsset(asset);
      return;
    }
    const response = await fetch(`${base(s)}/assets/${asset.id}`, { signal: AbortSignal.timeout(300000), headers: { Authorization: `Bearer ${s.backendAuthToken.trim()}` } });
    if (!response.ok) await responseError(response);
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = asset.filename; document.body.append(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};

export const knowledgeStatus: Record<string, string> = {
  received: "等待识别", extracting: "正在识别", retry_wait: "等待重试", needs_input: "待补充素材", needs_review: "待核对", failed: "识别失败", ready: "已入库",
};
export const knowledgeKind: Record<string, string> = { note: "资料", article: "公众号", channels: "视频号" };
