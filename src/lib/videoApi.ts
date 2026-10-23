import type { AppSettings } from "../types";

export type ReferenceImageRole = "first_frame" | "last_frame" | "reference_image";

export interface VideoReferenceImage {
  id: string;
  name: string;
  dataUrl: string;
  role: ReferenceImageRole;
}

export interface SeedanceGenerationRequest {
  prompt: string;
  duration: number;
  resolution: "480p" | "720p";
  ratio: "9:16";
  references: VideoReferenceImage[];
}

export interface SeedanceGenerationTask {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "expired";
  videoUrl?: string;
  error?: string;
}

interface ComposePromptRequest {
  target: string;
  instruction?: string;
  context: unknown;
  lockedFields?: string[];
}

export type VideoMode = "standard" | "avatar";
export type VideoReviewStage = "inputs" | "voice" | "generation" | "postprocess" | "video";
export type VideoJobKind = "voice" | "scene" | "scene_batch" | "avatar" | "render";
export type VideoPlatform = "weixin" | "douyin" | "xiaohongshu";
export type VideoCurrency = "CNY" | "RH" | "USD";

export interface VideoSceneConstraints {
  duration_min: number;
  duration_max: number;
  duration_step: number;
  resolutions: string[];
  ratios: string[];
  default_resolution: string;
  default_ratio: string;
}

export interface VideoRuntimeCapability {
  ready: boolean;
  reason?: string;
  source?: string;
  provider?: string;
  model?: string;
  credentials_configured?: boolean;
  credential_source?: string;
  credential_type?: string;
  currency?: VideoCurrency | null;
  supported_currencies?: VideoCurrency[];
  constraints?: VideoSceneConstraints;
}

export type VideoRuntimeCapabilities = Partial<Record<"planning" | "voice" | "scene" | "scene_batch" | "avatar" | "render", VideoRuntimeCapability>> & {
  cost_review_required?: boolean;
  production_mode?: string;
};

export interface VideoPlatformVariant {
  title?: string;
  cover_direction?: string;
  caption?: string;
  layout_notes?: string;
}

export interface VideoProjectScene {
  id: string;
  title: string;
  duration: number;
  narration: string;
  visual_intent: string;
  seedance_prompt: string;
  [key: string]: unknown;
}

export interface VideoInterestHook {
  id: string;
  scene_id: string;
  kind: "opening" | "progression" | "payoff";
  text?: string;
  viewer_question?: string;
  copy?: string;
  visual?: string;
  promise?: string;
  source_refs?: string[];
  payoff_scene_id: string;
}

export interface VideoProjectPlan {
  brief?: { title?: string; audience?: string; core_message?: string; total_duration?: number; [key: string]: unknown };
  audio_plan?: { narration_style?: string; voice?: string; music?: string; sound_notes?: string; [key: string]: unknown };
  scenes?: VideoProjectScene[];
  interest_hooks?: VideoInterestHook[];
  persona?: unknown;
  visual_style?: unknown;
  layout?: unknown;
  platform_variants?: Partial<Record<VideoPlatform, VideoPlatformVariant>>;
  postproduction?: { trim_pauses: boolean; burn_subtitles: boolean };
  medical?: unknown;
  [key: string]: unknown;
}

export interface VideoBudgetGrant {
  max_cost: number;
  currency: VideoCurrency;
  quote: string;
}

export interface VideoProjectJob {
  id: string;
  kind: VideoJobKind;
  scene_id?: string | null;
  scene_ids?: string[];
  status: string;
  provider_task_id?: string | null;
  result?: VideoJobResult | null;
  error?: string | null;
  revision: number;
  estimated_cost?: VideoJobQuote | null;
  budget_grant?: VideoBudgetGrant | null;
  created_at: string;
  updated_at: string;
}

export interface VideoJobBilling {
  currency: "CNY" | "USD";
  known_cost: number;
  complete: boolean;
  source: "provider_usage" | "provider_report";
  unknown_requests?: number;
  third_party_reported_cny?: number;
  reported_amount?: number;
  reported_currency?: "CNY" | "USD" | "UNKNOWN";
  note?: string;
}

export interface VideoJobResult extends Record<string, unknown> {
  billing?: VideoJobBilling;
  provider?: string;
}

export interface VideoProject {
  id: string;
  topic: string;
  mode: VideoMode;
  platforms: string[];
  revision: number;
  status: string;
  plan: VideoProjectPlan;
  locked_fields: string[];
  reviews: Partial<Record<VideoReviewStage, { decision: "approved" | "skipped"; revision: number; quote: string; confirmed_at: string; job_id?: string }>>;
  jobs: VideoProjectJob[];
  updated_at: string;
  medical_gate?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
}

export interface VideoJobQuote {
  amount: number | null;
  reservation_amount?: number | null;
  pricing_kind?: "estimate" | "reservation";
  budget_notice?: string;
  provider_hard_cap?: boolean;
  source_url?: string;
  verified_at?: string;
  model?: string;
  provider?: string;
  pricing_source?: string;
  quote_fingerprint?: string;
  request_fingerprint?: string;
  route?: string;
  quoted_at?: string;
  expires_at?: string;
  currency: VideoCurrency;
  basis: string;
  blocked_reason?: string | null;
  revision?: number;
  scope?: string;
  duration_seconds?: number;
  segment_count?: number;
  segments?: Array<{ id?: string; scene_id?: string; duration?: number; duration_seconds?: number; [key: string]: unknown }>;
  credential_type?: string;
  scene_ids?: string[];
}

export class VideoApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "VideoApiError";
  }
}

function endpoint(settings: AppSettings, path: string): string {
  const base = settings.backendUrl.trim().replace(/\/+$/, "");
  if (!base) throw new Error("请先在设置中填写后台地址");
  return `${base}${path}`;
}

async function resolveConnection(settings: AppSettings, path: string): Promise<{ url: string; accessToken: string; local: boolean }> {
  const accessToken = settings.backendAuthToken.trim();
  if (!accessToken) throw new Error("请先登录并连接内容生成后台");
  const bridge = window.signalDesk;
  if (/^\/api\/v1\/video\/projects(?:[/?]|$)/.test(path) && bridge?.getVideoConnection) {
    const connection = await bridge.getVideoConnection({ backendUrl: settings.backendUrl, accessToken: settings.backendAuthToken });
    if (connection !== null) {
      const match = typeof connection?.baseUrl === "string" && /^http:\/\/127\.0\.0\.1:([1-9]\d{0,4})\/?$/.exec(connection.baseUrl);
      if (!match || Number(match[1]) > 65535 || typeof connection.accessToken !== "string" || !connection.accessToken.trim() || connection.accessToken.trim() === accessToken || /[\r\n]/.test(connection.accessToken)) {
        throw new Error("本机视频服务连接无效，请重启客户端后重试");
      }
      return { url: `${connection.baseUrl.replace(/\/$/, "")}${path}`, accessToken: connection.accessToken.trim(), local: true };
    }
  }
  return { url: endpoint(settings, path), accessToken, local: false };
}

async function request<T>(settings: AppSettings, path: string, init: RequestInit): Promise<T> {
  const connection = await resolveConnection(settings, path);
  const response = await fetch(connection.url, {
    ...init,
    ...(connection.local ? { redirect: "error" as const } : {}),
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
      Authorization: `Bearer ${connection.accessToken}`,
    },
  });
  if (!response.ok) {
    let detail = `后台返回 HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail && typeof payload.detail === "object") {
        const data = payload.detail as Record<string, unknown>;
        detail = String(data.message || data.reason || data.error || JSON.stringify(data));
      }
    } catch {
      // 保留 HTTP 状态。
    }
    throw new VideoApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const videoApi = {
  uploadAvatarAsset(settings: AppSettings, id: string, payload: { name: string; data_url: string; role: "avatar_image" }) {
    return request<{ path: string; name: string; sha256: string; size: number }>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/assets`, { method: "POST", body: JSON.stringify(payload) });
  },
  projectCapabilities(settings: AppSettings) {
    return request<VideoRuntimeCapabilities>(settings, "/api/v1/video/projects/capabilities", { method: "GET" });
  },
  listProjects(settings: AppSettings) {
    return request<{ items: VideoProject[] }>(settings, "/api/v1/video/projects", { method: "GET" });
  },

  createProject(settings: AppSettings, payload: { topic: string; mode: VideoMode; platforms: string[] }) {
    return request<VideoProject>(settings, "/api/v1/video/projects", { method: "POST", body: JSON.stringify(payload) });
  },

  getProject(settings: AppSettings, id: string) {
    return request<VideoProject>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}`, { method: "GET" });
  },

  composeProject(settings: AppSettings, id: string, payload: { expected_revision: number; instruction?: string }) {
    return request<VideoProject>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/compose`, { method: "POST", body: JSON.stringify(payload) });
  },

  saveProject(settings: AppSettings, id: string, payload: { expected_revision: number; plan: VideoProjectPlan; locked_fields: string[] }) {
    return request<VideoProject>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/plan`, { method: "PUT", body: JSON.stringify(payload) });
  },

  reviewProject(settings: AppSettings, id: string, payload: { expected_revision: number; stage: VideoReviewStage; decision: "approved" | "skipped"; quote: string; job_id?: string }) {
    return request<VideoProject>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/reviews`, { method: "POST", body: JSON.stringify(payload) });
  },

  quoteProjectJob(settings: AppSettings, id: string, kind: VideoJobKind, sceneId?: string, sceneIds?: string[]) {
    const query = new URLSearchParams({ kind });
    if (sceneId) query.set("scene_id", sceneId);
    sceneIds?.forEach((value) => query.append("scene_ids", value));
    return request<VideoJobQuote>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/quote?${query}`, { method: "GET" });
  },

  createProjectJob(settings: AppSettings, id: string, payload: { expected_revision: number; kind: VideoJobKind; scene_id?: string; scene_ids?: string[]; idempotency_key: string; generation_consent?: boolean; expected_provider?: string; quote_fingerprint?: string; budget_grant?: VideoBudgetGrant }) {
    return request<VideoProjectJob>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/jobs`, { method: "POST", body: JSON.stringify(payload) });
  },

  refreshProjectJob(settings: AppSettings, id: string, jobId: string) {
    return request<VideoProjectJob>(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/jobs/${encodeURIComponent(jobId)}/refresh`, { method: "POST" });
  },

  async projectFile(settings: AppSettings, id: string, relativePath: string): Promise<Blob> {
    if (!relativePath || relativePath.split(/[\\/]/).some((part) => part === "..")) throw new Error("项目文件路径无效");
    const path = relativePath.split(/[\\/]/).map(encodeURIComponent).join("/");
    return authenticatedBlob(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/files/${path}`);
  },

  packageProject(settings: AppSettings, id: string): Promise<Blob> {
    return authenticatedBlob(settings, `/api/v1/video/projects/${encodeURIComponent(id)}/package`);
  },

  async compose<T>(settings: AppSettings, payload: ComposePromptRequest): Promise<T> {
    const response = await request<{ value: T }>(settings, "/api/v1/video/prompts/compose", {
      method: "POST",
      body: JSON.stringify({
        target: payload.target,
        instruction: payload.instruction,
        context: payload.context,
        locked_fields: payload.lockedFields || [],
      }),
    });
    return response.value;
  },

  createGeneration(settings: AppSettings, payload: SeedanceGenerationRequest) {
    return request<SeedanceGenerationTask>(settings, "/api/v1/video/generations", {
      method: "POST",
      body: JSON.stringify({
        ...payload,
        references: payload.references.map(({ name, dataUrl, role }) => ({
          name,
          data_url: dataUrl,
          role,
        })),
      }),
    });
  },

  getGeneration(settings: AppSettings, taskId: string) {
    return request<SeedanceGenerationTask>(
      settings,
      `/api/v1/video/generations/${encodeURIComponent(taskId)}`,
      { method: "GET" },
    );
  },
};

async function authenticatedBlob(settings: AppSettings, path: string): Promise<Blob> {
  const connection = await resolveConnection(settings, path);
  const response = await fetch(connection.url, { headers: { Authorization: `Bearer ${connection.accessToken}` }, ...(connection.local ? { redirect: "error" as const } : {}) });
  if (!response.ok) {
    let message = `文件读取失败（HTTP ${response.status}）`;
    try { const body = await response.json(); if (typeof body.detail === "string") message = body.detail; } catch { /* 保留状态 */ }
    throw new VideoApiError(message, response.status);
  }
  return response.blob();
}
