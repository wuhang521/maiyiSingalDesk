import { AudioLines, CheckCircle2, Clapperboard, Download, FileText, Lock, LockOpen, Plus, RefreshCw, Save, Sparkles, UserRound, WandSparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { AppSettings } from "../types";
import { VideoApiError, videoApi, type VideoJobBilling, type VideoJobKind, type VideoJobQuote, type VideoMode, type VideoPlatform, type VideoProject, type VideoProjectJob, type VideoProjectPlan, type VideoProjectScene, type VideoReviewStage, type VideoRuntimeCapabilities } from "../lib/videoApi";
import "./VideoGenerationWorkspace.css";

const platformOptions = ["视频号", "抖音", "小红书"];
const platformKeys: Array<[VideoPlatform, string]> = [["weixin", "视频号"], ["douyin", "抖音"], ["xiaohongshu", "小红书"]];
const stageNames: Record<VideoReviewStage, string> = { inputs: "内容方案", voice: "声音试听", generation: "生成方案", postprocess: "原片与后处理", video: "成片验收" };
const jobNames: Record<VideoJobKind, string> = { voice: "生成整条配音试听", scene: "生成当前镜头", scene_batch: "制作剩余全部镜头", avatar: "生成整条数字人口播", render: "合成完整视频" };
const statusNames: Record<string, string> = { queued: "排队中", pending: "等待中", running: "生成中", submitting: "提交中", submitted: "已提交", succeeded: "已完成", completed: "已完成", failed: "失败", expired: "已过期", blocked: "等待配置", needs_attention: "需要处理", cancelled: "已取消" };
const activeStatuses = new Set(["queued", "running", "pending", "submitting", "submitted"]);
const softBudgetNotice = "费用由供应商实际结算，预留不是云端硬上限；已提交的请求可能继续计费。超预算会停止后续新请求，重做和后续任务需另行确认。";
const objectLabels: Record<string, string> = { name: "名称", role: "身份", personality: "性格", description: "说明", voice: "声音", appearance: "形象", style: "风格", palette: "配色", typography: "字体", subtitles: "字幕", layout: "布局", ratio: "画幅", safe_zone: "安全区", platform: "平台", title: "标题", duration: "时长", notes: "说明", status: "状态", reason: "原因", required: "需要审核", reviewer: "审核人", sources: "来源", approved: "已审核", blocked: "等待审核", pending: "待审核" };

function errorMessage(error: unknown) {
  if (error instanceof VideoApiError && error.status === 409 && /报价|预算|价格|指纹|通道|供应商|模型/.test(error.message)) return error.message;
  if (error instanceof VideoApiError && error.status === 409) return "项目版本已更新。请重新载入最新版本后再修改；当前编辑内容仍保留在页面。";
  if (error instanceof VideoApiError && error.status === 404) return "后台暂未提供视频项目功能，请更新本机视频服务后重试。";
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

function formatValue(value: unknown): string {
  if (value == null || value === "") return "待规划";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(formatValue).join("；");
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${objectLabels[key] || key}：${formatValue(item)}`).join("\n");
  return objectLabels[String(value)] || String(value);
}

function reviewCurrent(project: VideoProject, stage: VideoReviewStage) {
  const review = project.reviews[stage];
  return review && review.revision === project.revision;
}

function budgetMinimum(quote: VideoJobQuote): number | null {
  const amount = quote.reservation_amount ?? quote.amount;
  return typeof amount === "number" && Number.isFinite(amount) && amount >= 0 ? amount : null;
}

function usesBudgetReservation(quote: VideoJobQuote) {
  return quote.provider === "runninghub" || quote.provider_hard_cap === false || Boolean(quote.pricing_kind && quote.provider_hard_cap !== true);
}

function providerName(provider: unknown) {
  return provider === "runninghub" || provider === "oldx-runninghub" ? "RunningHub" : provider === "ark" || provider === "seedance" ? "火山方舟" : "";
}

function currencyUnit(currency: unknown) {
  return currency === "CNY" ? "元" : currency === "USD" ? "美元（USD）" : currency === "RH" ? "RH币" : "币种待核";
}

function currencyName(currency: unknown) {
  return currency === "CNY" ? "人民币（CNY）" : currencyUnit(currency);
}

function quoteIssue(quote: VideoJobQuote) {
  if (quote.provider === "runninghub" && (typeof quote.quote_fingerprint !== "string" || !quote.quote_fingerprint.trim())) return "本次 RunningHub 报价缺少校验凭据，请取消后重新核算。";
  if (!["CNY", "USD", "RH"].includes(quote.currency)) return "本次报价币种尚未核验，请重新核算。";
  if (quote.provider === "runninghub" && !["CNY", "USD"].includes(quote.currency)) return "RunningHub 模型调用需取得人民币或美元报价，RH币余额不能替代模型调用预算。";
  if (quote.expires_at && Number.isFinite(Date.parse(quote.expires_at)) && Date.parse(quote.expires_at) <= Date.now()) return "本次报价已过期，请取消后重新核算。";
  return "";
}

function quoteTime(value: string) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString("zh-CN", { hour12: false }) : value;
}

function officialPricingSource(value: string | undefined) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.port
      && ["docs.volcengine.com", "help.aliyun.com", "www.runninghub.cn"].includes(url.hostname) ? url.href : null;
  } catch { return null; }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

interface JobRequest { kind: VideoJobKind; sceneId?: string; sceneIds?: string[]; quote: VideoJobQuote; key: string; revision: number }

export function VideoGenerationWorkspace({ settings }: { settings: AppSettings }) {
  const [projects, setProjects] = useState<VideoProject[]>([]);
  const [project, setProject] = useState<VideoProject | null>(null);
  const [plan, setPlan] = useState<VideoProjectPlan>({});
  const [locks, setLocks] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<VideoMode>("standard");
  const [platforms, setPlatforms] = useState(platformOptions);
  const [sceneId, setSceneId] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [instruction, setInstruction] = useState("");
  const [jobRequest, setJobRequest] = useState<JobRequest | null>(null);
  const [maxCost, setMaxCost] = useState("");
  const [budgetAccepted, setBudgetAccepted] = useState(false);
  const [media, setMedia] = useState<{ url: string; kind: string; label: string; jobId?: string; sourceJob?: VideoProjectJob } | null>(null);
  const [mediaError, setMediaError] = useState(false);
  const [playedJobs, setPlayedJobs] = useState<string[]>([]);
  const [capabilities, setCapabilities] = useState<VideoRuntimeCapabilities>({});
  const directSubmission = useRef<{ scope: string; key: string } | null>(null);
  const currentId = useRef<string | null>(null);
  const mediaUrl = useRef("");
  const requestEpoch = useRef(0);
  const accountScope = `${settings.backendUrl}\n${settings.backendAuthToken}`;
  const accountScopeRef = useRef(accountScope);
  accountScopeRef.current = accountScope;
  const connected = Boolean(settings.backendUrl.trim() && settings.backendAuthToken.trim());
  const scenes = plan.scenes || [];
  const selectedScene = scenes.find((scene) => scene.id === sceneId) || scenes[0];
  const totalDuration = scenes.reduce((total, scene) => total + Number(scene.duration || 0), 0);
  const hasPlan = Boolean(plan.brief && scenes.length);
  const postproduction = plan.postproduction || { trim_pauses: false, burn_subtitles: mode === "standard" };
  const needsPostprocessReview = mode === "avatar" && postproduction.trim_pauses;
  const currentJobs = (project?.jobs || []).filter((job) => job.revision === project?.revision);
  const latestSucceeded = (kind: VideoJobKind) => [...currentJobs].reverse().find((job) => job.kind === kind && job.status === "succeeded" && job.result);
  const voiceJob = latestSucceeded("voice");
  const avatarJob = latestSucceeded("avatar");
  const renderJob = latestSucceeded("render");
  const remainingScenes = scenes.filter((scene) => !currentJobs.some((job) => job.kind === "scene" && job.status === "succeeded" && job.scene_id === scene.id || job.kind === "scene_batch" && ((job.result?.scene_results as Array<{ scene_id: string; status: string }> | undefined) || []).some((item) => item.scene_id === scene.id && item.status === "succeeded")));
  const isGenerating = currentJobs.some((job) => activeStatuses.has(job.status));
  const directMode = capabilities.cost_review_required === false;
  const missingSceneIds = remainingScenes.map((scene) => scene.id);
  const sceneCapability = capabilities.scene || capabilities.scene_batch;
  const sceneModelName = sceneCapability?.model === "doubao-seedance-2-0-mini-260615" ? "Seedance 2.0 Mini" : sceneCapability?.model;
  const constraints = sceneCapability?.constraints;
  const durationLimits = constraints && Number.isFinite(constraints.duration_min) && Number.isFinite(constraints.duration_max)
    && constraints.duration_min > 0 && constraints.duration_max >= constraints.duration_min
    && Number.isFinite(constraints.duration_step) && constraints.duration_step > 0 ? constraints : null;
  const sceneSpecification = durationLimits
    ? `当前通道单镜头为 ${durationLimits.duration_min}～${durationLimits.duration_max} 秒，步长 ${durationLimits.duration_step} 秒${constraints?.default_resolution ? `，默认 ${constraints.default_resolution}` : ""}${constraints?.default_ratio ? ` · ${constraints.default_ratio}` : ""}。`
    : "当前通道的镜头规格待后台核验。";

  function generationIssue(kind: VideoJobKind): string {
    if (!directMode) return "";
    if (kind === "scene" || kind === "scene_batch") {
      const capability = capabilities[kind] || sceneCapability;
      if (capability?.ready !== true) return capability?.reason || "普通视频生成通道尚未就绪，请检查配置后重试。";
      if (capability.provider !== "seedance" && capability.provider !== "runninghub") return "普通视频生成通道尚未核验，请刷新后重试。";
    } else if (capabilities[kind]?.ready === false) return capabilities[kind]?.reason || "生成服务尚未就绪，请检查配置后重试。";
    return "";
  }

  function scenePosition(id: string | undefined) {
    const index = scenes.findIndex((scene) => scene.id === id);
    if (index < 0) return "待补充";
    const start = scenes.slice(0, index).reduce((total, scene) => total + Number(scene.duration || 0), 0);
    return `${scenes[index].title}（约 ${start}–${start + scenes[index].duration} 秒）`;
  }

  function acceptProject(next: VideoProject) {
    if (accountScopeRef.current !== accountScope) return false;
    currentId.current = next.id; setProject(next); setPlan(next.plan || {}); setLocks(next.locked_fields || []);
    setTopic(next.topic); setMode(next.mode); setPlatforms(next.platforms); setDirty(false); setJobRequest(null);
    setProjects((current) => [next, ...current.filter((item) => item.id !== next.id)]);
    return true;
  }

  function clearMedia() {
    if (mediaUrl.current) URL.revokeObjectURL(mediaUrl.current);
    mediaUrl.current = ""; setMedia(null); setMediaError(false);
  }

  useEffect(() => {
    let disposed = false;
    requestEpoch.current += 1; currentId.current = null;
    setProject(null); setPlan({}); setProjects([]); setTopic(""); setMode("standard"); setPlatforms(platformOptions); setDirty(false); setError(""); setNotice(""); setBusy(""); setJobRequest(null); setCapabilities({}); setPlayedJobs([]); clearMedia();
    if (!connected) return;
    setBusy("list");
    void videoApi.listProjects(settings).then((result) => { if (!disposed) setProjects(result.items); })
      .catch((cause) => { if (!disposed) setError(errorMessage(cause)); }).finally(() => { if (!disposed) setBusy(""); });
    void videoApi.projectCapabilities(settings).then((result) => { if (!disposed) setCapabilities(result); }).catch(() => { /* 项目列表错误或实际操作会呈现原因。 */ });
    return () => { disposed = true; requestEpoch.current += 1; };
  }, [settings.backendUrl, settings.backendAuthToken, connected]);

  useEffect(() => () => { if (mediaUrl.current) URL.revokeObjectURL(mediaUrl.current); }, []);

  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  useEffect(() => {
    if (!project || dirty || busy || !project.jobs.some((job) => activeStatuses.has(job.status))) return;
    const id = project.id; const revision = project.revision; const epoch = requestEpoch.current;
    const timer = window.setTimeout(async () => {
      try {
        const results = await Promise.allSettled(project.jobs.filter((job) => activeStatuses.has(job.status)).map((job) => videoApi.refreshProjectJob(settings, id, job.id)));
        const failed = results.find((result) => result.status === "rejected" && !(result.reason instanceof VideoApiError && result.reason.status === 409));
        if (failed?.status === "rejected") throw failed.reason;
        const latest = await videoApi.getProject(settings, id);
        if (currentId.current === id && requestEpoch.current === epoch) {
          setProject(latest); setProjects((current) => current.map((item) => item.id === id ? latest : item));
          if (latest.revision !== revision) { setPlan(latest.plan); setLocks(latest.locked_fields); }
        }
      } catch (cause) {
        if (currentId.current === id && requestEpoch.current === epoch) setError(`任务状态暂未更新：${errorMessage(cause)}。可点击“刷新状态”重试。`);
      }
    }, 6500);
    return () => window.clearTimeout(timer);
  }, [project, dirty, busy, settings]);

  async function action(label: string, callback: () => Promise<void>) {
    if (busy) return;
    setBusy(label); setError("");
    try { await callback(); } catch (cause) { if (accountScopeRef.current === accountScope) setError(errorMessage(cause)); } finally { if (accountScopeRef.current === accountScope) setBusy(""); }
  }

  function canLeave() { return !dirty || window.confirm("当前方案尚未保存。放弃这些修改并切换项目吗？"); }
  function newProject() {
    if (!canLeave()) return;
    requestEpoch.current += 1; currentId.current = null;
    setProject(null); setPlan({}); setLocks([]); setTopic(""); setMode("standard"); setPlatforms(platformOptions);
    setDirty(false); setJobRequest(null); setInstruction(""); setNotice(""); setError(""); setPlayedJobs([]); clearMedia();
  }

  async function openProject(id: string) {
    if (!canLeave()) return;
    await action("open", async () => { const next = await videoApi.getProject(settings, id); requestEpoch.current += 1; clearMedia(); setPlayedJobs([]); acceptProject(next); setNotice("已载入后台保存的项目，可继续编辑或查看任务。"); });
  }

  async function compose(target?: string) {
    if (dirty) { setError("请先保存修改，再让 AI 调整方案。"); return; }
    await action("compose", async () => {
      let current = project;
      if (!current) {
        if (!topic.trim()) throw new Error("请先输入视频主题");
        current = await videoApi.createProject(settings, { topic: topic.trim(), mode, platforms }); if (!acceptProject(current)) return;
      }
      const directive = [target ? `仅调整 ${target}，保留其他模块和已锁定字段。` : "", instruction.trim()].filter(Boolean).join("\n");
      const next = await videoApi.composeProject(settings, current.id, { expected_revision: current.revision, ...(directive ? { instruction: directive } : {}) });
      if (!acceptProject(next)) return;
      setInstruction(""); setNotice("AI 方案已保存。请查看人设、旁白、兴趣钩子和兑现位置，确认后再制作。");
    });
  }

  async function save() {
    if (!project) return;
    await action("save", async () => { acceptProject(await videoApi.saveProject(settings, project.id, { expected_revision: project.revision, plan, locked_fields: locks })); setNotice("方案已保存。内容变化后的确认状态由后台重新校验。"); });
  }
  function edit(patch: Partial<VideoProjectPlan>) { setPlan((current) => ({ ...current, ...patch })); setDirty(true); setJobRequest(null); }
  function toggleLock(field: string) { setLocks((current) => current.includes(field) ? current.filter((item) => item !== field) : [...current, field]); setDirty(true); }
  function editScene(patch: Partial<VideoProjectScene>) { if (selectedScene) edit({ scenes: scenes.map((scene) => scene.id === selectedScene.id ? { ...scene, ...patch } : scene) }); }

  async function submitReview(stage: VideoReviewStage, decision: "approved" | "skipped") {
    if (!project || dirty) return;
    await action(`review-${stage}`, async () => {
      const quote = `用户在 Hermes 客户端点击“${decision === "skipped" ? "跳过" : "确认"}${stageNames[stage]}”（项目版本 ${project.revision}）`;
      acceptProject(await videoApi.reviewProject(settings, project.id, { expected_revision: project.revision, stage, decision, quote, ...(stage === "postprocess" && avatarJob ? { job_id: avatarJob.id } : {}) }));
      setNotice(`${stageNames[stage]}${decision === "skipped" ? "已明确跳过" : "已确认"}，本次操作不授权付费生成。`);
    });
  }

  async function prepareJob(kind: VideoJobKind) {
    if (!project) return;
    if (dirty) { setError(directMode ? "请先保存方案，再开始生成。" : "请先保存方案，再核算本次生成费用。"); return; }
    if (directMode) {
      const issue = generationIssue(kind);
      if (issue) { setError(issue); return; }
      const capability = capabilities[kind] || sceneCapability;
      const scene = kind === "scene" ? selectedScene?.id : undefined;
      const selectedIds = kind === "scene_batch" ? missingSceneIds : undefined;
      if (kind === "scene_batch" && !selectedIds?.length) { setError("当前没有缺失镜头，可继续合成完整视频。"); return; }
      const scope = JSON.stringify({ accountScope, project: project.id, revision: project.revision, kind, scene, selectedIds, ...(["scene", "scene_batch"].includes(kind) ? { provider: capability?.provider } : {}) });
      if (directSubmission.current?.scope !== scope) directSubmission.current = { scope, key: globalThis.crypto?.randomUUID?.() || `video-${Date.now()}-${Math.random().toString(36).slice(2)}` };
      const key = directSubmission.current.key;
      await action("submit", async () => {
        await videoApi.createProjectJob(settings, project.id, { expected_revision: project.revision, kind,
          ...(scene ? { scene_id: scene } : {}), ...(selectedIds ? { scene_ids: selectedIds } : {}),
          generation_consent: true, ...(["scene", "scene_batch"].includes(kind) ? { expected_provider: capability?.provider } : {}), idempotency_key: key });
        if (accountScopeRef.current !== accountScope) return;
        const next = await videoApi.getProject(settings, project.id);
        if (acceptProject(next)) { directSubmission.current = null; setNotice("任务已提交。完成后可继续试听或查看视频。"); }
      });
      return;
    }
    await action("quote", async () => {
      const scope = kind === "scene" ? selectedScene?.id : undefined;
      const quote = await videoApi.quoteProjectJob(settings, project.id, kind, scope);
      if (accountScopeRef.current !== accountScope) return;
      if (quote.revision != null && quote.revision !== project.revision) throw new VideoApiError("项目版本已更新", 409);
      setJobRequest({ kind, sceneId: scope, ...(kind === "scene_batch" ? { sceneIds: quote.scene_ids } : {}), quote, revision: project.revision, key: globalThis.crypto?.randomUUID?.() || `video-${Date.now()}-${Math.random().toString(36).slice(2)}` });
      const minimum = budgetMinimum(quote);
      setMaxCost(minimum == null ? "" : String(minimum)); setBudgetAccepted(false);
      setNotice("请核对本次任务范围、币种和预算上限。点击授权才会提交。");
      window.setTimeout(() => document.getElementById("video-budget-review")?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }), 0);
    });
  }

  async function submitJob() {
    if (!project || !jobRequest || !budgetAccepted || jobRequest.quote.blocked_reason || jobRequest.quote.amount == null || jobRequest.kind === "scene_batch" && !jobRequest.sceneIds?.length) return;
    const ceiling = Number(maxCost);
    const issue = quoteIssue(jobRequest.quote);
    if (issue) { setError(issue); return; }
    const minimum = budgetMinimum(jobRequest.quote);
    if (!maxCost.trim() || minimum == null || !Number.isFinite(ceiling) || ceiling < minimum) { setError("本次预算需不低于建议预算预留。"); return; }
    await action("submit", async () => {
      const budgetDescription = usesBudgetReservation(jobRequest.quote) ? `软件预算预留 ${ceiling} ${jobRequest.quote.currency}。${softBudgetNotice}` : `费用上限 ${ceiling} ${jobRequest.quote.currency}`;
      await videoApi.createProjectJob(settings, project.id, { expected_revision: jobRequest.revision, kind: jobRequest.kind, ...(jobRequest.sceneId ? { scene_id: jobRequest.sceneId } : {}), ...(jobRequest.sceneIds ? { scene_ids: jobRequest.sceneIds } : {}), ...(jobRequest.quote.quote_fingerprint ? { quote_fingerprint: jobRequest.quote.quote_fingerprint } : {}), idempotency_key: jobRequest.key, budget_grant: { max_cost: ceiling, currency: jobRequest.quote.currency, quote: `用户在 Hermes 客户端勾选并点击授权：仅本次${jobNames[jobRequest.kind]}${jobRequest.sceneId ? `（${jobRequest.sceneId}）` : jobRequest.sceneIds ? `（${jobRequest.sceneIds.join("、")}）` : ""}，${budgetDescription}，不含后续重做或其他任务。` } });
      acceptProject(await videoApi.getProject(settings, project.id)); setNotice("本次任务已提交。可离开页面，重新打开项目后继续查看进度。");
    });
  }

  async function refresh() {
    if (!project || dirty) return;
    await action("refresh", async () => {
      for (const job of project.jobs.filter((item) => activeStatuses.has(item.status) || item.status === "needs_attention")) {
        try { await videoApi.refreshProjectJob(settings, project.id, job.id); } catch (cause) { if (!(cause instanceof VideoApiError && cause.status === 409)) throw cause; }
      }
      acceptProject(await videoApi.getProject(settings, project.id)); setNotice("已更新项目与任务状态。");
    });
  }

  async function previewJob(job: VideoProjectJob) {
    if (!project) return;
    await action("media", async () => {
      const result = job.result || {}; const file = result.audio_path || result.path || result.video_path || result.output_path;
      const remote = result.video_url || result.audio_url || result.videoUrl || result.url;
      clearMedia();
      if (typeof file === "string") { const blob = await videoApi.projectFile(settings, project.id, file); if (accountScopeRef.current !== accountScope) return; const url = URL.createObjectURL(blob); mediaUrl.current = url; setMedia({ url, kind: job.kind, label: jobNames[job.kind], jobId: job.id, sourceJob: job }); }
      else if (typeof remote === "string" && /^https?:\/\//i.test(remote)) setMedia({ url: remote, kind: job.kind, label: jobNames[job.kind], jobId: job.id, sourceJob: job });
      else throw new Error("该任务暂未返回可预览文件，请查看任务说明或刷新状态。");
    });
  }

  async function uploadAvatar(file: File | undefined) {
    if (!project || !file) return;
    if (file.size > 10 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError("请选择 10MB 以内的 PNG、JPEG 或 WebP 人物图。"); return; }
    await action("asset", async () => {
      const dataUrl = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("人物图读取失败，请重新选择")); reader.readAsDataURL(file); });
      const asset = await videoApi.uploadAvatarAsset(settings, project.id, { name: file.name, data_url: dataUrl, role: "avatar_image" });
      if (accountScopeRef.current !== accountScope) return;
      const avatar = typeof plan.avatar === "object" && plan.avatar ? plan.avatar : {};
      edit({ avatar: { ...avatar, image_path: asset.path, image_use_confirmed: false } });
      clearMedia();
      const blob = await videoApi.projectFile(settings, project.id, asset.path); if (accountScopeRef.current !== accountScope) return; const url = URL.createObjectURL(blob); mediaUrl.current = url;
      setMedia({ url, kind: "avatar_image", label: "待确认人物图" }); setNotice("人物图已上传。请查看图片、核对使用授权，保存后确认内容方案。");
    });
  }

  const avatar = typeof plan.avatar === "object" && plan.avatar ? plan.avatar as Record<string, unknown> : {};
  const avatarPreflight = voiceJob?.result?.avatar_preflight && typeof voiceJob.result.avatar_preflight === "object" ? voiceJob.result.avatar_preflight as Record<string, unknown> : null;

  async function exportPackage() {
    if (!project) return;
    await action("export", async () => { downloadBlob(await videoApi.packageProject(settings, project.id), `视频发布包-${project.id}.zip`); setNotice("发布包已下载，请在发布前核对平台版本和标识。"); });
  }

  const pendingMinimum = jobRequest ? budgetMinimum(jobRequest.quote) : null;
  const pendingQuoteIssue = jobRequest ? quoteIssue(jobRequest.quote) : "";
  const pendingBudget = jobRequest && (!budgetAccepted || jobRequest.quote.amount == null || pendingMinimum == null || Boolean(jobRequest.quote.blocked_reason) || Boolean(pendingQuoteIssue) || jobRequest.kind === "scene_batch" && !jobRequest.sceneIds?.length || !maxCost.trim() || !Number.isFinite(Number(maxCost)) || Number(maxCost) < pendingMinimum);
  const pricingSource = jobRequest ? officialPricingSource(jobRequest.quote.source_url) : null;
  const locked = (path: string) => locks.includes(path);
  function canReview(stage: VideoReviewStage) {
    if (!project || !hasPlan || dirty || busy || reviewCurrent(project, stage)) return false;
    if (stage === "inputs" && project.mode === "avatar") return Boolean(avatar.image_path && avatar.image_use_confirmed === true);
    if (stage === "voice" || stage === "video" || stage === "postprocess") {
      const job = stage === "voice" ? voiceJob : stage === "video" ? renderJob : avatarJob;
      return Boolean(reviewCurrent(project, stage === "voice" ? "inputs" : "generation") && job && playedJobs.includes(job.id));
    }
    if (stage === "generation") return Boolean(reviewCurrent(project, "inputs") && reviewCurrent(project, "voice"));
    return true;
  }
  function markPlayed() { if (media?.jobId) setPlayedJobs((current) => current.includes(media.jobId!) ? current : [...current, media.jobId!]); }
  const reviewStages = (Object.keys(stageNames) as VideoReviewStage[]).filter((stage) => stage !== "postprocess" || needsPostprocessReview);
  let nextStep = { title: "输入主题，AI 先规划内容", description: "普通视频默认使用场景与图解；需要人物出镜时选择数字人口播。", label: "AI 规划方案", run: () => void compose(), disabled: !topic.trim() || !connected };
  if (hasPlan && project) {
    if (dirty) nextStep = { title: "先保存本次修改", description: "保存后，会重新校验受影响的确认与生成素材。", label: "保存方案并继续", run: () => void save(), disabled: false };
    else if (!reviewCurrent(project, "inputs")) nextStep = { title: "第一步：确认内容方案", description: "核对人设、文案、兴趣钩子和平台方案。确认方案不产生视频生成费用。", label: "确认方案并继续", run: () => void submitReview("inputs", "approved"), disabled: !canReview("inputs") };
    else if (!voiceJob) nextStep = { title: "第二步：制作整条配音", description: generationIssue("voice") || (directMode ? "生成整条真实音频，试听确认后再制作画面。" : "先核对配音费用，再生成可试听的真实音频；用实际声音测量整片时长。"), label: directMode ? "生成配音试听" : "核算配音试听", run: () => void prepareJob("voice"), disabled: Boolean(generationIssue("voice")) };
    else if (!reviewCurrent(project, "voice")) nextStep = playedJobs.includes(voiceJob.id)
      ? { title: "第二步：确认听到的声音", description: "核对语气、读音与语速，确认后继续。", label: "确认声音并继续", run: () => void submitReview("voice", "approved"), disabled: false }
      : { title: "第二步：先播放配音试听", description: "已生成真实配音。点击播放，听过后再确认。", label: "打开配音试听", run: () => void previewJob(voiceJob), disabled: false };
    else if (!reviewCurrent(project, "generation")) nextStep = { title: "第三步：核对实际制作范围", description: mode === "avatar" ? "数字人按整条配音自动分段，生成全部口播；不按当前选中的策划镜头计费。" : `按已确认方案制作整片，共 ${scenes.length} 镜；已完成镜头会复用。`, label: "确认生成方案并继续", run: () => void submitReview("generation", "approved"), disabled: !canReview("generation") };
    else if (mode === "avatar" && !avatarJob) nextStep = { title: "第四步：制作整条数字人口播", description: generationIssue("avatar") || (directMode ? "按已确认的整条配音和人物方案生成全部口播。" : "核对实际分段、总时长、币种与预算预留，一次授权仅覆盖本条口播。"), label: directMode ? "生成整条数字人口播" : "核算整条数字人口播", run: () => void prepareJob("avatar"), disabled: Boolean(generationIssue("avatar")) };
    else if (mode === "standard" && remainingScenes.length) nextStep = { title: "第四步：按方案制作全部镜头", description: generationIssue("scene_batch") || (directMode ? `本次制作 ${remainingScenes.length} 个缺失镜头，已完成素材会复用。点击后开始云端生成。` : `本次只制作 ${remainingScenes.length} 个缺失镜头。一次授权绑定本批名单，失败或重做需另行确认。`), label: directMode ? "生成剩余全部镜头" : "核算剩余全部镜头", run: () => void prepareJob("scene_batch"), disabled: Boolean(generationIssue("scene_batch")) };
    else if (needsPostprocessReview && !reviewCurrent(project, "postprocess") && avatarJob) nextStep = playedJobs.includes(avatarJob.id)
      ? { title: "第五步：确认原片与去停顿", description: `将对这条原片去除停顿${postproduction.burn_subtitles ? "并烧录字幕" : ""}，处理后还需验收完整成片。`, label: "确认后处理并继续", run: () => void submitReview("postprocess", "approved"), disabled: false }
      : { title: "第五步：先查看数字人原片", description: "检查口型、人物动作和声音，再确认去停顿处理。", label: "打开数字人原片", run: () => void previewJob(avatarJob), disabled: false };
    else if (!renderJob) nextStep = { title: "合成可验收的完整视频", description: generationIssue("render") || `本地拼接${postproduction.trim_pauses ? "、去停顿" : ""}${postproduction.burn_subtitles ? "并烧录字幕" : "，同时保留独立字幕文件"}。先核对本次合成范围。`, label: directMode ? "合成完整视频" : "核算整片合成", run: () => void prepareJob("render"), disabled: Boolean(generationIssue("render")) };
    else if (!reviewCurrent(project, "video")) nextStep = playedJobs.includes(renderJob.id)
      ? { title: "验收这条完整视频", description: "检查画面、声音、字幕及各平台发布资料。确认后可下载发布包。", label: "确认成片并继续", run: () => void submitReview("video", "approved"), disabled: false }
      : { title: "播放完整成片后再验收", description: "右侧加载真实成片，请播放并核对内容。", label: "打开完整成片", run: () => void previewJob(renderJob), disabled: false };
    else nextStep = { title: "视频已完成验收", description: "下载视频、封面、独立字幕和三个平台的发布资料。", label: "下载已验收发布包", run: () => void exportPackage(), disabled: false };
  }
  if (isGenerating && !dirty) nextStep = { title: "正在制作，请等待任务完成", description: "任务会自动更新，也可在右侧刷新状态。离开页面后仍可回来继续。", label: "任务处理中", run: () => {}, disabled: true };

  return <div className="video-studio video-project-workspace">
    <aside className="video-project-rail" aria-label="视频项目">
      <button className="video-new-project" type="button" disabled={Boolean(busy)} onClick={newProject}><Plus size={15} />新建视频</button><span className="video-rail-title">已保存项目</span>
      <div className="video-projects">{projects.map((item) => <button type="button" key={item.id} className={project?.id === item.id ? "active" : ""} disabled={Boolean(busy)} onClick={() => void openProject(item.id)}><span><Clapperboard size={14} /></span><p><strong>{item.plan?.brief?.title || item.topic}</strong><small>{item.mode === "avatar" ? "数字人" : "普通视频"} · 版本 {item.revision}</small></p></button>)}{!projects.length && <p className="video-empty-rail">{busy === "list" ? "正在读取项目…" : connected ? "还没有已保存的视频" : "登录后台后读取个人项目"}</p>}</div>
      <div className="video-model-note"><Save size={15} /><div><strong>项目与任务可恢复</strong><span>方案和确认记录保存到个人后台；未保存修改会在离开前提醒。</span></div></div>
    </aside>
    <section className="video-editor-pane"><div className="video-editor-scroll">
      <section className="video-next-step" aria-label="当前制作步骤"><strong>{nextStep.title}</strong><p>{nextStep.description}</p>{directMode && hasPlan && <p className="video-inline-note">云端生成会按服务商规则计费。</p>}{project && <span>{mode === "avatar" ? "整条数字人口播" : "普通 AI 视频"} · {totalDuration ? `策划约 ${totalDuration} 秒` : "等待方案"}{avatarPreflight ? ` · 实测 ${Number(avatarPreflight.total_seconds || 0).toFixed(1)} 秒 / ${Number(avatarPreflight.segment_count || 0)} 段` : ""}</span>}</section>
      <section className="video-source-section"><div><span className="video-section-icon"><WandSparkles size={17} /></span><div><h2>{project ? "视频内容方案" : "给一个主题，规划整条视频"}</h2><p>AI 规划内容、人设、分镜和兴趣钩子。你逐步确认后开始制作。</p></div></div>
        <label className="video-topic-label" htmlFor="video-topic">视频主题</label><textarea id="video-topic" placeholder="例如：为什么手机电池怕冷？" value={topic} disabled={Boolean(project) || (Boolean(busy) && busy !== "list")} onChange={(event) => setTopic(event.target.value)} />
        <div className="video-format-options"><fieldset disabled={Boolean(project) || Boolean(busy)}><legend>视频形式</legend><label><input type="radio" name="video-mode" checked={mode === "standard"} onChange={() => setMode("standard")} />普通 AI 视频</label><label><input type="radio" name="video-mode" checked={mode === "avatar"} onChange={() => setMode("avatar")} />数字人口播</label></fieldset><fieldset disabled={Boolean(project) || Boolean(busy)}><legend>发布平台（可选）</legend>{platformOptions.map((platform) => <label key={platform}><input type="checkbox" checked={platforms.includes(platform)} onChange={() => setPlatforms((current) => current.includes(platform) ? current.filter((item) => item !== platform) : [...current, platform])} />{platform}</label>)}</fieldset></div>
        <div className="video-source-actions"><span>{project ? `项目版本 ${project.revision} · ${totalDuration ? `${totalDuration} 秒` : "待策划"}` : "只需主题，其余由 AI 提出方案"}</span><button className="primary-button" type="button" disabled={!connected || !topic.trim() || Boolean(busy) || dirty} onClick={() => void compose()}><Sparkles size={15} />{busy === "compose" ? "正在规划…" : project && hasPlan ? "重新规划整片" : "AI 规划方案"}</button></div>
      </section>
      {!connected && <p className="video-feedback error" role="alert">请先登录并在设置中连接后台，才能保存项目和规划视频。</p>}{error && <p className="video-feedback error" role="alert">{error}</p>}{notice && <p className="video-feedback" role="status">{notice}</p>}
      {capabilities.planning?.ready === false && <p className="video-feedback error">策划服务待配置：{capabilities.planning.reason || "请连接策划模型"}</p>}
      {capabilities.planning?.ready && capabilities.planning.source === "hermes" && <p className="video-feedback" role="status">策划已复用 Hermes 现有模型{capabilities.planning.model ? `（${capabilities.planning.model}）` : ""}。</p>}
      <p className="video-inline-note">{directMode ? "AI 策划复用 Hermes 已配置模型。" : "AI 策划调用按 Hermes 已配置模型的账单计费，单独于视频制作预算。"}</p>
      {capabilities.voice?.credentials_configured && capabilities.voice.credential_source === "hermes" && <p className="video-feedback" role="status">{directMode ? "千问配音已接入 Hermes 的百炼凭证。" : "千问配音已接入 Hermes 的百炼凭证，生成时单独确认费用。"}</p>}
      {mode === "standard" && sceneCapability?.provider === "runninghub" && <p className="video-feedback" role="status">普通 AI 视频使用 RunningHub{sceneCapability.model ? `（${sceneCapability.model}）` : ""}{sceneCapability.credential_type === "SHARED" ? "，已接入企业级共享 Key" : ""}{directMode ? "。" : "，费用按本次官方报价币种结算。"}{sceneCapability.ready === false ? `当前待就绪：${sceneCapability.reason || "请核对通道配置"}` : directMode ? "按确认的镜头范围生成。" : "生成前逐批核算并确认。"}</p>}
      {mode === "standard" && sceneCapability?.provider === "seedance" && <p className="video-feedback" role="status">普通 AI 视频使用火山方舟直连{sceneModelName ? `（${sceneModelName}）` : ""}{sceneCapability.credential_source === "hermes" ? "，已复用 Hermes 的火山凭证。" : "。"}按确认的镜头范围生成。</p>}
      {mode === "standard" && sceneCapability && generationIssue("scene_batch") && <p className="video-feedback error" role="status">{generationIssue("scene_batch")}</p>}
      {mode === "avatar" && <p className="video-feedback" role="status">数字人口播使用 RunningHub，按已确认的整条配音生成人物视频。{capabilities.avatar?.ready === false ? `当前待就绪：${capabilities.avatar.reason || "请检查数字人服务配置"}` : ""}</p>}
      {!hasPlan && <section className="video-planning-empty"><FileText size={28} /><h2>{project ? "项目已保存，等待 AI 策划" : "从你的主题开始"}</h2><p>方案会包含受众、完整旁白、画面布局和开场疑问的回答位置。未连接生成服务时，会明确提示配置缺项。</p><p>普通视频使用场景与图解；数字人模式另行确认人物和声音素材。</p></section>}
      {hasPlan && <fieldset className="video-plan-fields" disabled={Boolean(busy)}>
        <section className="video-module-section"><SectionHeading icon="persona" title="内容、人设与视觉" description="先看清楚说给谁听、怎样讲，再确认制作。" /><div className="video-module-body">
          <div className="video-field-grid two-columns"><PlanField label="视频标题" value={plan.brief?.title || ""} locked={locked("brief.title")} onLock={() => toggleLock("brief.title")} onChange={(value) => edit({ brief: { ...plan.brief, title: value } })} /><PlanField label="目标受众" value={plan.brief?.audience || ""} locked={locked("brief.audience")} onLock={() => toggleLock("brief.audience")} onChange={(value) => edit({ brief: { ...plan.brief, audience: value } })} /></div>
          <PlanField label="核心信息" value={plan.brief?.core_message || ""} multiline locked={locked("brief.core_message")} onLock={() => toggleLock("brief.core_message")} onChange={(value) => edit({ brief: { ...plan.brief, core_message: value } })} />
          {([["persona", "讲解人设"], ["visual_style", "视频风格"], ["layout", "画面布局"]] as const).map(([key, label]) => <PlanField key={key} label={label} value={typeof plan[key] === "string" ? plan[key] as string : plan[key] == null ? "" : formatValue(plan[key])} multiline locked={locked(key)} onLock={() => toggleLock(key)} onChange={(value) => edit({ [key]: value })} />)}
          {project?.mode === "avatar" && <p className="video-inline-note">数字人素材以项目配置为准。未选择可用人像和声音时，不能启动数字人生成。</p>}
        </div></section>
        <section className="video-module-section"><SectionHeading title="各平台发布方案" description="视频号、抖音和小红书各自保存标题、封面方向、发布文案和布局要求。" /><div className="video-module-body">
          {platformKeys.filter(([, label]) => platforms.includes(label)).map(([key, label]) => <details className="video-platform-plan" key={key}><summary>{label}<span>{plan.platform_variants?.[key]?.title || "待 AI 规划"}</span></summary><div>
            {([["title", "发布标题"], ["cover_direction", "封面方向"], ["caption", "发布文案"], ["layout_notes", "平台布局"]] as const).map(([field, title]) => <PlanField key={field} label={`${label}${title}`} multiline={field !== "title"} value={plan.platform_variants?.[key]?.[field] || ""} locked={locked(`platform_variants.${key}.${field}`)} onLock={() => toggleLock(`platform_variants.${key}.${field}`)} onChange={(value) => edit({ platform_variants: { ...plan.platform_variants, [key]: { ...plan.platform_variants?.[key], [field]: value } } })} />)}
          </div></details>)}
          <p className="video-inline-note">当前导出共用一条竖屏成片；三平台的标题、封面说明、文案与布局记录分别打包。独立重剪需另行制作与授权。</p>
        </div></section>
        {project?.mode === "avatar" && <section className="video-module-section"><SectionHeading icon="persona" title="数字人人物素材" description="使用自己有权使用的照片；上传和预览不启动视频生成。" /><div className="video-module-body"><label className="video-upload-label">选择人物图<input type="file" aria-label="数字人人物图" accept="image/png,image/jpeg,image/webp" onChange={(event) => { void uploadAvatar(event.target.files?.[0]); event.target.value = ""; }} /></label><p className="video-inline-note">{avatar.image_path ? "人物图已上传，可在右侧查看。修改后需重新确认。" : "待上传人物图片（PNG、JPEG、WebP，最大 10MB）。"}</p>{Boolean(avatar.image_path) && <label className="video-budget-consent"><input type="checkbox" checked={avatar.image_use_confirmed === true} onChange={(event) => edit({ avatar: { ...avatar, image_use_confirmed: event.target.checked } })} />我确认已查看人物图，并拥有使用此人物图生成视频的授权。</label>}</div></section>}
        {project?.mode === "avatar" && <section className="video-module-section"><SectionHeading icon="scene" title="数字人动作与形象" description="确认生成方案前，核对本次站姿、手部和动作。" /><div className="video-module-body">{([["motion", "人物动作"], ["hands", "手部姿态"], ["posture", "站姿"]] as const).map(([key, label]) => <PlanField key={key} label={label} multiline={key === "motion"} value={typeof avatar[key] === "string" ? avatar[key] as string : ""} locked={locked(`avatar.${key}`)} onLock={() => toggleLock(`avatar.${key}`)} onChange={(value) => edit({ avatar: { ...avatar, [key]: value } })} />)}<details className="video-advanced"><summary>高级：人物画风类型</summary><PlanField label="人物画风类型" value={typeof avatar.image_type === "string" ? avatar.image_type : ""} locked={locked("avatar.image_type")} onLock={() => toggleLock("avatar.image_type")} onChange={(value) => edit({ avatar: { ...avatar, image_type: value } })} /><p>沿用 Skill 的类型标记。更换照片后，可让 AI 重新核对动作和形象。</p></details></div></section>}
        <section className="video-module-section"><SectionHeading title="吸引观众与兑现承诺" description="标出什么时候提出问题、在哪里回答；用真实内容兑现开头的承诺。" /><div className="video-module-body"><ol className="video-interest-hooks">{(plan.interest_hooks || []).map((hook) => <li key={hook.id}><strong>{hook.kind === "opening" ? "开场引入" : hook.kind === "progression" ? "中段推进" : "答案兑现"}</strong><p>{hook.copy || hook.text || hook.viewer_question}</p>{hook.promise && <p>承诺：{hook.promise}</p>}{hook.visual && <p>画面：{hook.visual}</p>}<small>提出：{scenePosition(hook.scene_id)} · 回答：{scenePosition(hook.payoff_scene_id)}</small></li>)}</ol>{!plan.interest_hooks?.length && <p className="video-inline-note">当前方案未包含兴趣钩子，请让 AI 补充提出和回答的位置。</p>}</div></section>
        <section className="video-module-section"><SectionHeading icon="audio" title="旁白与声音" description={directMode ? "先生成真实配音，试听确认后再制作画面。" : "先制作试听，再确认声音；试听任务也单独核算费用。"} /><div className="video-module-body"><div className="video-field-grid two-columns">{([["narration_style", "旁白风格"], ["voice", "声音人设"], ["music", "音乐方向"], ["sound_notes", "声音提示"]] as const).map(([key, label]) => <PlanField key={key} label={label} value={plan.audio_plan?.[key] || ""} locked={locked(`audio_plan.${key}`)} onLock={() => toggleLock(`audio_plan.${key}`)} onChange={(value) => edit({ audio_plan: { ...plan.audio_plan, [key]: value } })} />)}</div><details className="video-advanced"><summary>高级：实际配音音色</summary><PlanField label="配音音色 ID" value={typeof plan.audio_plan?.voice_id === "string" ? plan.audio_plan.voice_id : ""} locked={locked("audio_plan.voice_id")} onLock={() => toggleLock("audio_plan.voice_id")} onChange={(value) => edit({ audio_plan: { ...plan.audio_plan, voice_id: value } })} /><p>留空时使用后台配置的默认 AI 音色。声音描述需以实际试听确认；不会自动克隆真人声音。</p></details><button className="secondary-button" type="button" disabled={Boolean(busy) || dirty || Boolean(generationIssue("voice"))} onClick={() => void prepareJob("voice")}><AudioLines size={14} />{directMode ? voiceJob ? "重新生成配音试听" : "单独生成配音试听" : "单独核算配音试听"}</button></div></section>
        <section className="video-module-section"><SectionHeading icon="scene" title="分镜与完整旁白" description="逐镜确认画面与讲述。修改后可要求 AI 只调整当前镜头。" /><div className="video-module-body"><div className="video-scene-tabs" role="tablist" aria-label="视频分镜">{scenes.map((scene, index) => <button type="button" key={scene.id} role="tab" aria-selected={selectedScene?.id === scene.id} className={selectedScene?.id === scene.id ? "active" : ""} onClick={() => setSceneId(scene.id)}><span>{index + 1}</span><p><strong>{scene.title}</strong><small>{scene.duration} 秒</small></p></button>)}</div>
          {selectedScene && <><div className="video-scene-meta"><input aria-label="镜头名称" value={selectedScene.title} onChange={(event) => editScene({ title: event.target.value })} /><label>预计秒数<input aria-label="镜头预计时长" type="number" min={mode === "avatar" ? 1 : durationLimits?.duration_min} max={mode === "avatar" ? 14 : durationLimits?.duration_max} step={mode === "avatar" ? 1 : durationLimits?.duration_step} value={selectedScene.duration} onChange={(event) => editScene({ duration: Number(event.target.value) })} /></label></div>{mode === "standard" && <p className="video-inline-note">{sceneSpecification}</p>}<PlanField label="本镜头旁白" multiline value={selectedScene.narration} locked={locked(`scenes.${selectedScene.id}.narration`)} onLock={() => toggleLock(`scenes.${selectedScene.id}.narration`)} onChange={(value) => editScene({ narration: value })} /><PlanField label="画面意图" multiline value={selectedScene.visual_intent} locked={locked(`scenes.${selectedScene.id}.visual_intent`)} onLock={() => toggleLock(`scenes.${selectedScene.id}.visual_intent`)} onChange={(value) => editScene({ visual_intent: value })} /><div className="video-scene-actions"><button className="secondary-button" type="button" disabled={Boolean(busy) || dirty} onClick={() => void compose(`分镜 ${selectedScene.id}`)}><RefreshCw size={14} />AI 只改这一镜</button></div><details className="video-advanced"><summary>高级：模型提示词和项目配置</summary>{mode === "standard" && <button className="secondary-button" type="button" disabled={Boolean(busy) || dirty || Boolean(generationIssue("scene"))} onClick={() => void prepareJob("scene")}><Clapperboard size={14} />{directMode ? "生成当前镜头" : "核算当前镜头"}</button>}<PlanField label="模型提示词" multiline value={selectedScene.seedance_prompt} locked={locked(`scenes.${selectedScene.id}.seedance_prompt`)} onLock={() => toggleLock(`scenes.${selectedScene.id}.seedance_prompt`)} onChange={(value) => editScene({ seedance_prompt: value })} /><p>{mode === "avatar" ? "此处是内容策划段（1～14 秒），实际数字人按整条配音自动分段；选择镜头不会限制数字人的生成范围。" : directMode ? "按当前版本固定的缺失镜头名单制作；实际规格以当前生成通道核验结果为准。" : "主流程会核算当前版本全部缺失镜头，再单次授权制作；实际规格以当前生成通道核验结果为准。"}</p></details></>}
        </div></section>
        <section className="video-module-section"><SectionHeading title="后处理与字幕" description={mode === "avatar" ? "先选处理方式；人物原片生成后仍可调整。只改这里会复用已完成素材，再重新验片。" : "镜头完成后本地合成；独立字幕文件会随发布包提供。"} /><div className="video-module-body">
          {mode === "avatar" && <label className="video-budget-consent"><input type="checkbox" checked={postproduction.trim_pauses} onChange={(event) => edit({ postproduction: { ...postproduction, trim_pauses: event.target.checked } })} />去除数字人口播停顿（先看原片并确认，再处理）</label>}
          <label className="video-budget-consent"><input type="checkbox" checked={postproduction.burn_subtitles} onChange={(event) => edit({ postproduction: { ...postproduction, burn_subtitles: event.target.checked } })} />将字幕烧录到成片画面</label>
          <p className="video-inline-note">{mode === "avatar" ? "默认保留原始停顿、不开启画面字幕。去停顿会改变节奏和总时长，最终仍需播放验收。" : "默认烧录字幕，按已确认的平台布局导出竖屏成片。"}</p>
          {avatarJob && <button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => void previewJob(avatarJob)}>查看当前数字人原片</button>}
        </div></section>
        <section className="video-module-section"><SectionHeading title="告诉 AI 怎样调整" description="不需要编写提示词。已锁定的字段会保留。" /><div className="video-module-body"><textarea className="video-change-instruction" aria-label="方案修改要求" placeholder="例如：讲给家长听，语气更温和；开头从生活场景切入。" value={instruction} onChange={(event) => setInstruction(event.target.value)} /><button className="secondary-button" type="button" disabled={!instruction.trim() || Boolean(busy) || dirty} onClick={() => void compose()}><Sparkles size={14} />按要求调整方案</button></div></section>
      </fieldset>}
      {!directMode && jobRequest && <section id="video-budget-review" className="video-budget-review" aria-label="本次费用授权"><h2>确认本次生成费用</h2><dl>
        <div><dt>范围</dt><dd>{jobNames[jobRequest.kind]}{jobRequest.sceneId ? ` · ${scenes.find((scene) => scene.id === jobRequest.sceneId)?.title || jobRequest.sceneId}` : ""}</dd></div>
        {jobRequest.kind === "scene_batch" && <div><dt>镜头名单</dt><dd>{jobRequest.sceneIds?.map((id) => scenes.find((scene) => scene.id === id)?.title || id).join("、") || "等待后台核对缺失镜头"}</dd></div>}
        {(jobRequest.quote.duration_seconds != null || jobRequest.quote.segment_count != null) && <div><dt>本次用量</dt><dd>{jobRequest.quote.duration_seconds != null ? `${jobRequest.quote.duration_seconds} 秒` : ""}{jobRequest.quote.segment_count != null ? ` · ${jobRequest.quote.segment_count} 段` : ""}{jobRequest.kind === "avatar" ? "（按真实配音，包含全部自动分段）" : ""}</dd></div>}
        <div><dt>{jobRequest.quote.pricing_source === "runninghub_price_preview" ? "RunningHub 实时报价" : jobRequest.quote.pricing_kind === "estimate" ? "官方刊例价估算" : jobRequest.quote.pricing_kind === "reservation" ? "软件预算预留" : "预计费用"}</dt><dd>{jobRequest.quote.amount == null ? "暂无法核算" : `${jobRequest.quote.amount} ${currencyUnit(jobRequest.quote.currency)}`}</dd></div>
        {jobRequest.quote.pricing_kind === "estimate" && jobRequest.quote.reservation_amount != null && <div><dt>建议预算预留</dt><dd>{jobRequest.quote.reservation_amount} {currencyUnit(jobRequest.quote.currency)}</dd></div>}
        {providerName(jobRequest.quote.provider) && <div><dt>生成通道</dt><dd>{providerName(jobRequest.quote.provider)}</dd></div>}<div><dt>结算币种</dt><dd>{currencyName(jobRequest.quote.currency)}{jobRequest.quote.credential_type === "SHARED" ? " · 企业级共享 Key" : ""}</dd></div>
        <div><dt>核算依据</dt><dd>{jobRequest.quote.basis}</dd></div>
        {jobRequest.quote.quoted_at && <div><dt>报价时间</dt><dd>{quoteTime(jobRequest.quote.quoted_at)}</dd></div>}
        {jobRequest.quote.expires_at && <div><dt>报价有效至</dt><dd>{quoteTime(jobRequest.quote.expires_at)}</dd></div>}
        {(pricingSource || jobRequest.quote.verified_at) && <div><dt>价格说明</dt><dd>{pricingSource && <a href={pricingSource} target="_blank" rel="noopener noreferrer">查看官方计费说明</a>}{jobRequest.quote.verified_at && <span>{pricingSource ? " · " : ""}核验于 {jobRequest.quote.verified_at}</span>}</dd></div>}
      </dl>
        {jobRequest.quote.model && <details className="video-advanced"><summary>查看本次模型</summary><p>{jobRequest.quote.model}</p></details>}
        {jobRequest.quote.budget_notice && <p className="video-inline-note">{jobRequest.quote.budget_notice}</p>}
        {jobRequest.quote.credential_type === "SHARED" && <p className="video-inline-note">普通视频按本次官方模型报价币种确认；数字人另按其报价，RH币余额不可替代模型调用预算。</p>}
        {!!jobRequest.quote.segments?.length && <details className="video-advanced"><summary>查看本次逐段用量</summary><ol className="video-quote-segments">{jobRequest.quote.segments.map((segment, index) => <li key={String(segment.id || segment.scene_id || index)}>第 {index + 1} 段{segment.scene_id ? ` · ${scenes.find((scene) => scene.id === segment.scene_id)?.title || segment.scene_id}` : ""} · {Number(segment.duration_seconds ?? segment.duration ?? 0).toFixed(1)} 秒</li>)}</ol></details>}
        {jobRequest.quote.blocked_reason && <p className="video-feedback error" role="alert">{jobRequest.quote.blocked_reason}</p>}
        {pendingQuoteIssue && <p className="video-feedback error" role="alert">{pendingQuoteIssue}</p>}
        <label className="video-budget-limit">{usesBudgetReservation(jobRequest.quote) ? "本次软件预算" : "本次预算上限"} · {currencyUnit(jobRequest.quote.currency)}<input aria-label="本次预算上限" type="number" min="0" step="0.0001" value={maxCost} onChange={(event) => setMaxCost(event.target.value)} /></label>
        <label className="video-budget-consent"><input type="checkbox" checked={budgetAccepted} onChange={(event) => setBudgetAccepted(event.target.checked)} />{usesBudgetReservation(jobRequest.quote) ? `仅授权上述一次任务或列明的一批镜头。${softBudgetNotice}` : "仅授权上述一次任务或列明的一批镜头，在此上限内产生费用；重做和后续任务需另行确认。"}</label>
        <div className="video-scene-actions"><button className="primary-button" type="button" disabled={Boolean(pendingBudget) || Boolean(busy) || dirty} onClick={() => void submitJob()}>授权并提交本次任务</button><button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => setJobRequest(null)}>取消本次授权</button></div>
      </section>}
    </div><div className="video-editor-actions"><span role="status">{dirty ? "有未保存修改；保存后再确认或生成。" : busy ? "正在处理，请稍候…" : project ? `已保存 · 版本 ${project.revision}` : "输入主题后开始规划"}</span><div>{dirty && <button className="secondary-button" type="button" disabled={!project || Boolean(busy)} onClick={() => void save()}><Save size={14} />保存方案</button>}{hasPlan && <button className="primary-button" type="button" disabled={nextStep.disabled || Boolean(busy) || (!directMode && Boolean(jobRequest))} onClick={nextStep.run}>{nextStep.label}</button>}</div></div></section>
    <aside className="video-preview-pane video-review-pane" aria-label="确认与视频预览"><h2>制作确认</h2><p className="video-side-intro">依次确认内容、声音、制作范围和完整成片。{directMode ? "点击生成后开始制作。" : "每批费用另行授权。"}</p><ol className="video-review-stages">{reviewStages.map((stage, index) => { const title = stageNames[stage]; const review = project?.reviews[stage]; const current = project && reviewCurrent(project, stage); return <li key={stage} className={current ? "confirmed" : ""}><div><span>{current ? <CheckCircle2 size={18} /> : index + 1}</span><strong>{title}</strong><small>{current ? review?.decision === "skipped" ? "已跳过" : "已确认" : review ? "变更后待重确认" : "待确认"}</small></div><p>{stage === "inputs" ? "核对主题、人设、脚本、钩子和平台方案" : stage === "voice" ? "播放试听，核对声音与语速" : stage === "generation" ? mode === "avatar" ? "核对整条口播的真实分段、人物动作及后处理选项" : "核对整片镜头范围、布局与后处理选项" : stage === "postprocess" ? "播放人物原片，确认对这条原片去停顿" : "播放完整视频，检查字幕和平台版本"}</p><button type="button" className="secondary-button" disabled={!canReview(stage)} onClick={() => void submitReview(stage, "approved")}>确认{title}</button>{stage === "voice" && !current && <details className="video-advanced"><summary>不需要声音试听</summary><p>仍须生成真实配音供视频使用；这里仅跳过人工试听。</p><button type="button" className="video-text-button" disabled={!project || dirty || Boolean(busy) || !reviewCurrent(project, "inputs")} onClick={() => void submitReview(stage, "skipped")}>明确跳过试听</button></details>}</li>; })}</ol>
      {mode === "avatar" && avatarPreflight && <section className="video-production-scope"><h2>数字人实际生成范围</h2><p>整条配音 {Number(avatarPreflight.total_seconds || 0).toFixed(1)} 秒，共 {Number(avatarPreflight.segment_count || 0)} 段，全部自动分段生成。</p><p>当前选中的策划镜头不改变此次生成范围。</p></section>}
      <div className="video-side-heading"><h2>任务与试听</h2><button className="video-text-button" type="button" disabled={!project || dirty || Boolean(busy)} onClick={() => void refresh()}><RefreshCw size={13} />刷新状态</button></div>{!project?.jobs.length && <p className="video-inline-note">{directMode ? "暂无生成任务。先确认方案，再生成配音与画面。" : "暂无生成任务。先确认方案，再核算试听或整片费用。"}</p>}
      <ul className="video-job-list">{[...(project?.jobs || [])].reverse().map((job) => <li key={job.id}><div><strong>{jobNames[job.kind]}{job.scene_id ? ` · ${scenes.find((scene) => scene.id === job.scene_id)?.title || job.scene_id}` : ""}</strong><span>{statusNames[job.status] || job.status}</span></div><small>版本 {job.revision}{job.result?.reused_from_job_id ? " · 已复用原素材，无需再次生成" : ""}</small>{providerName(job.estimated_cost?.provider || job.result?.provider) && <small>生成通道：{providerName(job.estimated_cost?.provider || job.result?.provider)}</small>}{job.error && <p className="error">{job.error}</p>}
        {!directMode && job.result?.billing && <JobBilling billing={job.result.billing} />}
        {job.kind === "scene_batch" ? ((job.result?.scene_results as Array<Record<string, unknown>> | undefined) || []).filter((item) => item.status === "succeeded").map((item) => <button key={String(item.scene_id)} type="button" className="video-text-button" disabled={Boolean(busy)} onClick={() => void previewJob({ ...job, kind: "scene", result: item })}>查看镜头：{scenes.find((scene) => scene.id === item.scene_id)?.title || String(item.scene_id)}</button>) : ["succeeded", "completed"].includes(job.status) && <button type="button" className="video-text-button" disabled={Boolean(busy)} onClick={() => void previewJob(job)}>{job.kind === "voice" ? "播放配音试听" : job.kind === "avatar" ? "查看数字人原片" : "查看生成视频"}</button>}
      </li>)}</ul>
      {media && <div className="video-actual-media"><strong>{media.label}</strong>{media.kind === "avatar_image" ? <img src={media.url} alt="数字人人物图预览" /> : media.kind === "voice" ? <audio key={media.url} controls preload="metadata" src={media.url} aria-label="配音试听" onPlaying={markPlayed} onLoadedMetadata={() => setMediaError(false)} onError={() => setMediaError(true)} /> : <video key={media.url} controls preload="metadata" src={media.url} aria-label="生成视频预览" onPlaying={markPlayed} onLoadedMetadata={() => setMediaError(false)} onError={() => setMediaError(true)} />}{mediaError && <><p className="video-feedback error" role="alert">当前文件加载失败。请重新加载现有文件后再试听或预览。</p>{media.sourceJob && <button className="secondary-button" type="button" disabled={Boolean(busy)} onClick={() => void previewJob(media.sourceJob!)}><RefreshCw size={14} />重新加载现有文件</button>}</>}<p className="video-inline-note">{media.kind === "avatar_image" ? "请核对人物图和使用授权。" : media.jobId && playedJobs.includes(media.jobId) ? "已开始播放。请核对完整内容后确认。" : "点击播放器中的播放按钮，核对实际内容后再确认。"}</p></div>}
      <div className="video-export-section"><h2>发布交付</h2><p>通过成片验收后，下载视频、封面、字幕和各平台发布资料。</p><button type="button" className="secondary-button" disabled={!project || !reviewCurrent(project, "video") || dirty || Boolean(busy)} onClick={() => void exportPackage()}><Download size={14} />下载发布包</button><button type="button" className="video-text-button" disabled={!project || Boolean(busy)} onClick={() => { if (project) downloadBlob(new Blob([JSON.stringify(project, null, 2)], { type: "application/json" }), `视频项目-${project.id}.json`); }}>导出项目记录</button></div>
    </aside>
  </div>;
}

function JobBilling({ billing }: { billing: VideoJobBilling }) {
  if (!["provider_usage", "provider_report"].includes(billing.source)) return null;
  const unknownRequests = Number.isInteger(billing.unknown_requests) && Number(billing.unknown_requests) > 0 ? billing.unknown_requests : 0;
  const knownCurrency = billing.currency === "CNY" || billing.currency === "USD";
  const reportedAmount = typeof billing.reported_amount === "number" && Number.isFinite(billing.reported_amount) && billing.reported_amount >= 0 ? billing.reported_amount : null;
  const reportedCurrencyKnown = billing.reported_currency === "CNY" || billing.reported_currency === "USD";
  const complete = billing.complete === true && !unknownRequests && knownCurrency && (reportedAmount == null || reportedCurrencyKnown);
  const knownAmount = knownCurrency && Number.isFinite(billing.known_cost) && billing.known_cost >= 0 && (billing.known_cost > 0 || complete);
  const thirdPartyReported = typeof billing.third_party_reported_cny === "number" && Number.isFinite(billing.third_party_reported_cny) && billing.third_party_reported_cny > 0 ? billing.third_party_reported_cny : null;
  return <p className="video-job-billing">{knownAmount ? `${billing.source === "provider_usage" ? "已知用量费用" : "供应商返回金额"}：${billing.known_cost} ${currencyUnit(billing.currency)}` : "费用待对账"}<small>{complete ? "用量记录完整" : "待对账"}{unknownRequests ? ` · 另有 ${unknownRequests} 次请求待对账` : ""}</small>{reportedAmount != null && <small>{reportedCurrencyKnown ? `供应商返回原始金额：${reportedAmount} ${currencyUnit(billing.reported_currency)}（单独记录，未直接合计）` : `供应商返回原始金额 ${reportedAmount}，币种待核`}</small>}{thirdPartyReported != null && <small>第三方返回金额（包含关系待核）：{thirdPartyReported} 元</small>}{typeof billing.note === "string" && billing.note.trim() && <small>{billing.note}</small>}</p>;
}

function SectionHeading({ title, description, icon }: { title: string; description: string; icon?: "persona" | "audio" | "scene" }) {
  const Icon = icon === "persona" ? UserRound : icon === "audio" ? AudioLines : icon === "scene" ? Clapperboard : Sparkles;
  return <div className="video-module-heading"><span><Icon size={17} /></span><div><h2>{title}</h2><p>{description}</p></div></div>;
}

function PlanField({ label, value, multiline, locked, onChange, onLock }: { label: string; value: string; multiline?: boolean; locked: boolean; onChange: (value: string) => void; onLock: () => void }) {
  return <div className={`video-ai-field ${locked ? "locked" : ""}`}><div className="video-ai-field-heading"><strong>{label}</strong><button type="button" className="video-text-button" aria-label={`${locked ? "解锁" : "锁定"}${label}`} onClick={onLock}>{locked ? <Lock size={13} /> : <LockOpen size={13} />}{locked ? "已锁定" : "锁定"}</button></div>{multiline ? <textarea aria-label={label} value={value} disabled={locked} onChange={(event) => onChange(event.target.value)} /> : <input aria-label={label} value={value} disabled={locked} onChange={(event) => onChange(event.target.value)} />}</div>;
}
