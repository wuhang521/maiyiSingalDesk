import { chromium, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

const root = process.cwd();
const seedanceFixture = process.env.VIDEO_E2E_SEEDANCE === "1";
const directFixture = seedanceFixture || process.env.VIDEO_E2E_DIRECT === "1";
const runninghubFixture = !seedanceFixture && (directFixture || process.env.VIDEO_E2E_RUNNINGHUB === "1");
const sceneProvider = seedanceFixture ? "seedance" : "runninghub";
const sceneProviderLabel = seedanceFixture ? "火山方舟" : "RunningHub";
const testEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(TASK_|ARK_|DASHSCOPE_|RUNNINGHUB_|BL_|RH_|SEEDANCE_|TTS_)/.test(key)));
testEnv.VIDEO_E2E_RUNNINGHUB = runninghubFixture ? "1" : "0";
testEnv.VIDEO_E2E_DIRECT = directFixture ? "1" : "0";
const artifacts = path.resolve(root, "artifacts/video");
const run = path.join(artifacts, `run-${randomUUID()}`);
await mkdir(run, { recursive: true });
const python = process.env.VIDEO_TEST_PYTHON || path.resolve(root, "../.venv-signaldesk/Scripts/python.exe");
const backend = spawn(python, ["server/tests/video_preview.py"], { cwd: root, windowsHide: true, env: { ...testEnv, TASK_DATABASE_URL: `sqlite:///${path.join(run, "test.db").replaceAll("\\", "/")}`, VIDEO_E2E_STORAGE: path.join(run, "media"), PYTHONIOENCODING: "utf-8" }, stdio: ["ignore", "pipe", "pipe"] });
const vite = spawn(process.execPath, ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "15174"], { cwd: root, env: testEnv, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let logs = "";
backend.stderr.on("data", (value) => { logs += value; });
backend.stdout.on("data", (value) => { logs += value; });
vite.stderr.on("data", (value) => { logs += value; });
vite.stdout.on("data", (value) => { logs += value; });
backend.on("error", (error) => { logs += error.message; });
vite.on("error", (error) => { logs += error.message; });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function ready(url) {
  for (let i = 0; i < 240; i++) {
    try { const response = await fetch(url); if (response.status < 500) return; } catch { /* 等待本地服务。 */ }
    await delay(250);
  }
  throw new Error(`测试服务未启动：${logs}`);
}
const checks = [];
let browser;
let page;
const errors = [];
let quoteRequests = 0;
const submittedGenerations = [];
async function startJob(legacyLabel, directLabel, kind) {
  if (directFixture) {
    const responsePromise = page.waitForResponse((response) => /\/api\/v1\/video\/projects\/[^/]+\/jobs$/.test(response.url()) && response.request().method() === "POST");
    await page.getByRole("button", { name: directLabel, exact: true }).click();
    const response = await responsePromise;
    const request = response.request().postDataJSON();
    if (!response.ok()) throw new Error(`直接生成测试失败（${kind}）：${await response.text()}`);
    if (request.kind !== kind || request.generation_consent !== true || "budget_grant" in request || "quote_fingerprint" in request) throw new Error("直接生成意外依赖报价或费用确认");
    if (["scene", "scene_batch"].includes(kind) && request.expected_provider !== sceneProvider) throw new Error(`直接生成未确认 ${sceneProviderLabel} 通道`);
    const submitted = await response.json();
    if (["scene", "scene_batch"].includes(kind) && submitted.estimated_cost?.provider !== sceneProvider) throw new Error("普通视频任务未保存实际生成通道");
    if (kind === "avatar" && submitted.estimated_cost?.provider !== "oldx-runninghub") throw new Error("数字人任务未保持 RunningHub");
    submittedGenerations.push({ kind, expected_provider: request.expected_provider ?? null, actual_provider: submitted.estimated_cost?.provider ?? null });
    if (kind === "scene_batch" && request.scene_ids?.join(",") !== "scene-1,scene-2") throw new Error("直接生成未固定缺失镜头名单");
    await expect(page.getByRole("region", { name: "本次费用授权" })).toHaveCount(0);
    return;
  }
  await page.getByRole("button", { name: legacyLabel, exact: true }).click();
  const budget = page.getByRole("region", { name: "本次费用授权" });
  await budget.getByRole("checkbox").check();
  await budget.getByRole("button", { name: "授权并提交本次任务" }).click();
  await expect(budget).toHaveCount(0);
}
try {
  await Promise.all([ready("http://127.0.0.1:18778/health"), ready("http://127.0.0.1:15174/")]);
  await writeFile(path.join(artifacts, "preview.html"), `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><title>视频流程测试（不付费）</title><style>html,body,#root{height:100%;margin:0}body{font-family:system-ui,sans-serif}</style></head><body><div id="root"></div><script type="module">
    import React from 'react'; import {createRoot} from 'react-dom/client'; import {VideoGenerationWorkspace} from '/src/components/VideoGenerationWorkspace.tsx'; import '/src/styles.css';
    createRoot(document.getElementById('root')).render(React.createElement(VideoGenerationWorkspace,{settings:{backendUrl:'http://127.0.0.1:18778',backendAuthToken:'video-e2e-only'}}));
  </script></body></html>`, "utf8");
  browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--no-proxy-server"] });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  await page.route("**/*", (route) => route.request().url().startsWith("http://127.0.0.1:") ? route.continue() : route.abort());
  await page.context().tracing.start({ screenshots: true, snapshots: true, sources: true });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => { if (new URL(request.url()).pathname.endsWith("/quote")) quoteRequests += 1; });
  page.on("requestfailed", (request) => { if (request.url().startsWith("http://127.0.0.1:")) logs += `\nLOCAL_REQUEST_FAILED ${request.url()} ${request.failure()?.errorText}`; });
  page.on("dialog", (dialog) => dialog.dismiss());
  await page.goto("http://127.0.0.1:15174/artifacts/video/preview.html", { waitUntil: "domcontentloaded", timeout: 60000 });
  await expect(page.getByRole("radio", { name: "普通 AI 视频" })).toBeChecked();
  if (runninghubFixture) {
    await expect(page.getByText(/普通 AI 视频使用 RunningHub（Seedance 2.0 Mini）/)).toContainText(directFixture ? "按确认的镜头范围生成" : "按本次官方报价币种结算");
  }
  if (seedanceFixture) {
    await expect(page.getByText(/普通 AI 视频使用火山方舟直连（Seedance 2.0 Mini）/)).toContainText("已复用 Hermes 的火山凭证");
    await expect(page.getByText(/普通 AI 视频使用 RunningHub/)).toHaveCount(0);
  }
  const budget = page.getByRole("region", { name: "本次费用授权" });
  await page.screenshot({ path: path.join(artifacts, "new-desktop.png") });
  await page.getByLabel("视频主题", { exact: true }).fill("太阳为什么发光");
  await page.getByRole("button", { name: "AI 规划方案", exact: true }).click();
  await expect(page.getByLabel("视频标题", { exact: true })).toHaveValue("测试方案：太阳为什么发光");
  await expect(page.getByText("提出：开场疑问（测试）（约 0–6 秒） · 回答：回答疑问（测试）（约 6–12 秒）")).toBeVisible();
  checks.push("只需主题→普通模式→真实API策划与兴趣兑现位置");
  await page.locator(".video-editor-scroll").evaluate((element) => { element.scrollTop = 0; });
  await page.screenshot({ path: path.join(artifacts, "plan-desktop.png") });
  await page.setViewportSize({ width: 1080, height: 760 });
  await page.screenshot({ path: path.join(artifacts, "plan-compact.png") });
  await expect(page.getByRole("button", { name: /测试方案：太阳为什么发光/ })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "确认内容方案", exact: true }).click();
  await startJob("核算配音试听", "生成配音试听", "voice");
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await page.getByRole("button", { name: "播放配音试听", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("audio")?.readyState >= 2);
  await page.screenshot({ path: path.join(artifacts, "audio-desktop.png") });
  await page.locator("audio").evaluate((element) => element.play());
  await page.getByRole("button", { name: "确认声音试听", exact: true }).click();
  await page.getByRole("button", { name: "确认生成方案", exact: true }).click();
  checks.push(directFixture ? "直接生成配音、无报价与预算勾选→真实WAV可解码→独立试听确认" : "试听独立报价→真实WAV可解码→内容/试听/生成三阶段独立确认");
  if (directFixture) {
    await expect(page.getByRole("button", { name: /核算/ })).toHaveCount(0);
    await expect(budget).toHaveCount(0);
    await expect(page.getByText("云端生成会按服务商规则计费。", { exact: true })).toBeVisible();
    await page.locator(".video-editor-scroll").evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: path.join(artifacts, "generation-desktop.png") });
    await startJob("核算剩余全部镜头", "生成剩余全部镜头", "scene_batch");
  } else {
  await page.getByRole("button", { name: "核算剩余全部镜头", exact: true }).click();
  await expect(budget.getByText(runninghubFixture ? "2.5 美元（USD）" : "2.5 元", { exact: true })).toBeVisible();
  if (runninghubFixture) {
    await expect(budget.getByText("RunningHub 实时报价", { exact: true })).toBeVisible();
    await expect(budget.getByText("3 美元（USD）", { exact: true })).toBeVisible();
    await expect(budget.getByLabel("本次预算上限", { exact: true })).toHaveValue("3");
    await expect(budget.getByText("美元（USD） · 企业级共享 Key", { exact: true })).toBeVisible();
    await expect(budget.getByRole("link", { name: "查看官方计费说明" })).toHaveAttribute("href", "https://www.runninghub.cn/runninghub-api-doc-cn/");
    await expect(budget.getByRole("checkbox")).toHaveAccessibleName(/预留不是云端硬上限/);
  }
  await expect(budget.getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
  await budget.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(artifacts, "budget-desktop.png") });
  await budget.getByRole("checkbox").check();
  if (runninghubFixture) {
    await budget.getByLabel("本次预算上限", { exact: true }).fill("2.9");
    await expect(budget.getByRole("button", { name: "授权并提交本次任务" })).toBeDisabled();
    await budget.getByLabel("本次预算上限", { exact: true }).fill("3");
  }
  const batchSubmission = page.waitForResponse((response) => /\/api\/v1\/video\/projects\/[^/]+\/jobs$/.test(response.url()) && response.request().method() === "POST");
  await budget.getByRole("button", { name: "授权并提交本次任务" }).click();
  const submittedBatch = await batchSubmission;
  if (runninghubFixture) {
    const payload = submittedBatch.request().postDataJSON();
    const submittedJob = await submittedBatch.json();
    if (!submittedBatch.ok()) throw new Error(`模拟 RunningHub 批次提交失败：${JSON.stringify(submittedJob)}`);
    if (!/^[a-f0-9]{64}$/.test(payload.quote_fingerprint) || payload.quote_fingerprint !== submittedJob.estimated_cost?.quote_fingerprint) throw new Error("美元批次未绑定生产 API 计算的报价指纹");
    if (payload.budget_grant?.currency !== "USD" || payload.budget_grant?.max_cost !== 3 || payload.scene_ids?.length !== 2) throw new Error("美元预算、预留或固定镜头范围不一致");
  }
  await expect(budget).toHaveCount(0);
  }
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看镜头：开场疑问（测试）", exact: true })).toBeVisible();
  if (runninghubFixture || seedanceFixture) {
    await expect(page.locator(".video-job-list").getByText(`生成通道：${sceneProviderLabel}`, { exact: true })).toBeVisible();
    if (directFixture) await expect(page.locator(".video-job-list").getByText(/供应商返回金额|用量费用|美元/)).toHaveCount(0);
    else await expect(page.locator(".video-job-list").getByText(/供应商返回金额：2.5 美元（USD）/)).toBeVisible();
    await expect(page.locator(".video-job-list").getByText(`生成通道：${seedanceFixture ? "RunningHub" : "火山方舟"}`, { exact: true })).toHaveCount(0);
  }
  await page.getByRole("button", { name: "查看镜头：开场疑问（测试）", exact: true }).click();
  await expect(page.getByLabel("生成视频预览", { exact: true })).toBeVisible();
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
  checks.push(directFixture ? `固定缺失镜头名单与${sceneProviderLabel}通道→直接批次生成，不询价→逐镜鉴权媒体可解码` : "整片缺失镜头固定名单/币种/一次预算授权→批次任务→逐镜鉴权媒体可解码");
  await page.reload();
  await page.getByRole("button", { name: /测试方案：太阳为什么发光/ }).click();
  await expect(page.getByRole("button", { name: "查看镜头：开场疑问（测试）", exact: true })).toBeVisible();
  if (runninghubFixture || seedanceFixture) await expect(page.locator(".video-job-list").getByText(`生成通道：${sceneProviderLabel}`, { exact: true })).toBeVisible();
  checks.push("刷新页面→列表恢复方案/批次任务/确认");
  await page.getByLabel("视频标题", { exact: true }).fill("临时未保存标题");
  await expect(page.getByRole("button", { name: "确认内容方案", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "新建视频", exact: true }).click();
  await expect(page.getByLabel("视频标题", { exact: true })).toHaveValue("临时未保存标题");
  checks.push("脏编辑阻止生成，切换项目取消时保留编辑");
  await page.getByLabel("视频标题", { exact: true }).fill("测试方案：太阳为什么发光");
  await page.getByRole("button", { name: "保存方案", exact: true }).click();
  // No content changed: the server may retain or advance a revision, so confirm current version again.
  const inputReview = page.getByRole("button", { name: "确认内容方案", exact: true });
  if (await inputReview.isEnabled()) await inputReview.click();
  const generateReview = page.getByRole("button", { name: "确认生成方案", exact: true });
  if (await generateReview.isEnabled()) await generateReview.click();
  await startJob("核算整片合成", "合成完整视频", "render");
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认成片验收", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "打开完整成片", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
  await page.locator("video").evaluate((element) => element.play());
  await expect(page.getByRole("button", { name: "确认成片验收", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "下载发布包", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "确认成片验收", exact: true }).click();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载发布包", exact: true }).click();
  const download = await downloadEvent; await download.saveAs(path.join(artifacts, "test-publication-package.zip"));
  await page.getByRole("button", { name: "查看生成视频", exact: true }).first().click();
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
  await page.locator(".video-review-pane").evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await page.screenshot({ path: path.join(artifacts, "final-desktop.png") });
  checks.push("本地合成测试替身→成片确认→发布包真实下载");
  await page.getByRole("button", { name: "新建视频", exact: true }).click();
  await page.getByLabel("视频主题", { exact: true }).fill("缺配置分支");
  await page.getByRole("button", { name: "AI 规划方案", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("策划服务未配置");
  await expect(page.getByText("项目已保存，等待 AI 策划")).toBeVisible();
  await page.screenshot({ path: path.join(artifacts, "missing-config-desktop.png") });
  checks.push("缺配置明确失败，项目保留且不伪造生成结果");
  await page.getByRole("button", { name: "新建视频", exact: true }).click();
  await page.getByLabel("视频主题", { exact: true }).fill("医疗健康知识测试");
  const medicalPlanResponse = page.waitForResponse((response) => /\/api\/v1\/video\/projects\/[^/]+\/compose$/.test(response.url()) && response.request().method() === "POST");
  await page.getByRole("button", { name: "AI 规划方案", exact: true }).click();
  const medicalProject = await (await medicalPlanResponse).json();
  await expect(page.getByRole("button", { name: "确认方案并继续", exact: true })).toBeEnabled();
  await expect(page.getByText("医疗内容审核与依据", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/医疗专业资质及审稿尚未核验|publishable|cleared/)).toHaveCount(0);
  if (directFixture) {
    const unconfirmed = await page.request.post(`http://127.0.0.1:18778/api/v1/video/projects/${medicalProject.id}/jobs`, {
      headers: { Authorization: "Bearer video-e2e-only" },
      data: { expected_revision: medicalProject.revision, kind: "voice", generation_consent: true, idempotency_key: "medical-unconfirmed-must-fail" },
    });
    if (unconfirmed.status() !== 428) throw new Error(`医疗主题未确认内容时应要求普通确认，实际 ${unconfirmed.status()}`);
    await expect(budget).toHaveCount(0);
  }
  await page.getByRole("button", { name: "确认内容方案", exact: true }).click();
  await startJob("核算配音试听", "生成配音试听", "voice");
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await page.getByRole("button", { name: "播放配音试听", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("audio")?.readyState >= 2);
  await page.locator("audio").evaluate((element) => element.play());
  await page.getByRole("button", { name: "确认声音试听", exact: true }).click();
  await page.getByRole("button", { name: "确认生成方案", exact: true }).click();
  await expect(page.getByRole("button", { name: directFixture ? "生成剩余全部镜头" : "核算剩余全部镜头", exact: true })).toBeEnabled();
  await page.screenshot({ path: path.join(artifacts, "medical-confirmation-desktop.png") });
  await startJob("核算剩余全部镜头", "生成剩余全部镜头", "scene_batch");
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(page.getByRole("button", { name: /^查看镜头：/ })).toHaveCount(2);
  await page.getByRole("button", { name: /^查看镜头：/ }).first().click();
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
  await page.locator("video").evaluate((element) => element.play());
  await page.screenshot({ path: path.join(artifacts, "medical-generated-desktop.png") });
  checks.push("医疗主题正常策划→普通内容/试听/范围确认→提交本地替身生成；无资质或专业审稿门槛");
  await page.getByRole("button", { name: "新建视频", exact: true }).click();
  await page.getByLabel("视频主题", { exact: true }).fill("潮汐数字人流程测试");
  await page.getByRole("radio", { name: "数字人口播", exact: true }).check();
  await expect(page.getByText(/数字人口播使用 RunningHub，按已确认的整条配音生成人物视频/)).toBeVisible();
  await page.getByRole("button", { name: "AI 规划方案", exact: true }).click();
  await expect(page.getByLabel("镜头预计时长", { exact: true })).toHaveAttribute("max", "14");
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=", "base64");
  await page.getByLabel("数字人人物图", { exact: true }).setInputFiles({ name: "synthetic-pixel.png", mimeType: "image/png", buffer: png });
  await page.getByRole("checkbox", { name: /拥有使用此人物图/ }).check();
  await page.getByRole("button", { name: "保存方案", exact: true }).click();
  await page.getByRole("button", { name: "确认内容方案", exact: true }).click();
  await startJob("核算配音试听", "生成配音试听", "voice");
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await page.getByRole("button", { name: "播放配音试听", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("audio")?.readyState >= 2);
  await page.locator("audio").evaluate((element) => element.play());
  await page.getByRole("button", { name: "确认声音试听", exact: true }).click();
  await page.getByRole("button", { name: "确认生成方案", exact: true }).click();
  if (directFixture) {
    await expect(page.getByText(/整条配音 1.0 秒，共 1 段/)).toBeVisible();
    await startJob("核算整条数字人口播", "生成整条数字人口播", "avatar");
  } else {
  await page.getByRole("button", { name: "核算整条数字人口播", exact: true }).click();
  await expect(budget.getByText("人民币（CNY） · 企业级共享 Key", { exact: true })).toBeVisible();
  await expect(budget.getByText(/1 秒 · 1 段/)).toBeVisible();
  await budget.getByRole("checkbox").check();
  await budget.getByRole("button", { name: "授权并提交本次任务" }).click();
  await expect(budget).toHaveCount(0);
  }
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看数字人原片", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: /去除数字人口播停顿/ }).check();
  const saveOptions = page.waitForResponse((response) => response.url().endsWith("/plan") && response.request().method() === "PUT");
  await page.getByRole("button", { name: "保存方案", exact: true }).click();
  const optionsResponse = await saveOptions;
  const reused = await optionsResponse.json();
  if (!optionsResponse.ok()) throw new Error(`后处理选项保存失败（HTTP ${optionsResponse.status()}）：${JSON.stringify(reused)}`);
  const raw = reused.jobs.filter((job) => job.kind === "avatar" && job.revision === reused.revision).at(-1);
  if (!raw?.result?.reused_from_job_id) throw new Error("仅后处理变化未复用数字人原片");
  if (!reused.reviews.inputs || !reused.reviews.voice || !reused.reviews.generation || reused.reviews.postprocess || reused.reviews.video) throw new Error("仅后处理变化的确认复用/失效不符合预期");
  await expect(page.getByRole("button", { name: "确认原片与后处理", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "打开数字人原片", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
  await page.locator("video").evaluate((element) => element.play());
  const acceptRaw = page.waitForResponse((response) => response.url().endsWith("/reviews") && response.request().method() === "POST");
  await page.getByRole("button", { name: "确认后处理并继续", exact: true }).click();
  const accepted = await (await acceptRaw).json();
  if (accepted.reviews.postprocess?.job_id !== raw.id) throw new Error("去停顿确认未绑定当前复用原片");
  if (accepted.jobs.some((job) => job.kind === "render" && job.revision === accepted.revision)) throw new Error("确认后处理意外自动合成");
  await expect(page.getByRole("button", { name: directFixture ? "合成完整视频" : "核算整片合成", exact: true })).toBeVisible();
  await page.locator(".video-editor-scroll").evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await page.screenshot({ path: path.join(artifacts, "avatar-postprocess-desktop.png") });
  checks.push(directFixture ? "数字人整条真实配音直接生成→原片后改去停顿→复用原片与配音→绑定原片确认；不自动合成" : "数字人整条配音真实分段/CNY共享Key预算→原片后改去停顿→复用原片与配音→播放后绑定原片确认；不自动提交合成");
  if (directFixture && quoteRequests !== 0) throw new Error(`直接模式意外发起 ${quoteRequests} 次报价请求`);
  if (seedanceFixture && (submittedGenerations.filter(job => ["scene", "scene_batch"].includes(job.kind)).some(job => job.expected_provider !== "seedance" || job.actual_provider !== "seedance") || !submittedGenerations.some(job => job.kind === "avatar" && job.actual_provider === "oldx-runninghub"))) throw new Error("混合通道验证失败：普通视频需火山，数字人需RunningHub");
  if (directFixture) await expect(page.getByRole("button", { name: /核算/ })).toHaveCount(0);
  if (errors.length) throw new Error(errors.join("\n"));
  await writeFile(path.join(artifacts, "e2e-result.json"), JSON.stringify({ passed: true, mode: seedanceFixture ? "seedance_rh_avatar_direct_fixture" : directFixture ? "runninghub_direct_fixture" : runninghubFixture ? "runninghub_fixture" : "legacy_fixture", quote_requests: quoteRequests, submitted_generations: submittedGenerations, mixed_providers_verified: seedanceFixture, direct_generation_consents_verified: directFixture, checks, paid_calls: 0, test_doubles: ["planner", "supplier", "price", "sine-wave audio", "solid-color test video"], runninghub_quote_currency: runninghubFixture && !directFixture ? "USD" : null, runninghub_quote_amount: runninghubFixture && !directFixture ? 2.5 : null, runninghub_budget_reservation: runninghubFixture && !directFixture ? 3 : null, quote_fingerprint_verified: runninghubFixture && !directFixture, external_browser_requests_blocked: true, viewport: ["1440x1000", "1080x760"], actual_database: path.relative(root, path.join(run, "test.db")), quality_verified: false }, null, 2));
  console.log(`视频端到端验证通过：${checks.length} 项流程；实际 API/数据库；供应商均为测试替身，付费调用 0。`);
} catch (error) {
  if (page) { await page.screenshot({ path: path.join(artifacts, "failure.png") }); console.error((await page.locator("body").innerText()).slice(0, 3000)); }
  await writeFile(path.join(artifacts, "e2e-result.json"), JSON.stringify({ passed: false, checks, error: String(error), logs, paid_calls: 0 }, null, 2));
  await writeFile(path.join(run, "failure-result.json"), JSON.stringify({ passed: false, checks, error: String(error), logs, paid_calls: 0 }, null, 2));
  throw error;
} finally { if (page) await page.context().tracing.stop({ path: path.join(run, "trace.zip") }).catch(() => {}); await browser?.close(); backend.kill(); vite.kill(); }
