// Packaged desktop integration check: real existing login, real sidecar, no cloud generation.
import { _electron as electron, expect } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import process from "node:process";

const source = process.cwd();
const output = "D:/maiyi/outputs/maiyi-video-integrated-20261022";
const seedanceCheck = process.env.VIDEO_SMOKE_SEEDANCE === "1";
const medicalCheck = seedanceCheck || process.env.VIDEO_SMOKE_MEDICAL === "1";
const stage = process.env.VIDEO_SMOKE_STAGE || path.join(output, "stage");
const evidence = process.env.VIDEO_SMOKE_EVIDENCE || "D:/maiyi/docs/delivery/maiyi-video-integrated-20261022/evidence";
const installed = "D:/maiyi/signaldesk-desktop";
const originalProfile = path.join(process.env.APPDATA, "迈医协同");
const useInstalled = process.env.VIDEO_CHECK_INSTALLED === "1";
const probe = path.join(source, "artifacts", seedanceCheck ? "video-volcengine-mini-smoke" : medicalCheck ? "video-medical-confirmation-smoke" : "video-integrated-package-smoke");
const profile = useInstalled ? originalProfile : path.join(probe, "profile");
const executableRoot = useInstalled ? installed : path.join(probe, "app");
const label = useInstalled ? "installed" : "packaged";
const digest = data => crypto.createHash("sha256").update(data).digest("hex");
await fs.mkdir(evidence, { recursive: true });

async function clone(directory, destination, skip = []) {
  await fs.mkdir(destination, { recursive: true });
  for (const item of await fs.readdir(directory, { withFileTypes: true })) {
    if (skip.includes(item.name)) continue;
    if (item.isSymbolicLink()) throw new Error("Linked native runtime is unsupported");
    const from = path.join(directory, item.name), to = path.join(destination, item.name);
    if (item.isDirectory()) await clone(from, to);
    else {
      try { await fs.link(from, to); } catch (error) {
        if (error.code !== "EEXIST") await fs.copyFile(from, to);
      }
    }
  }
}

if (!useInstalled) {
  await clone(installed, executableRoot, ["resources"]);
  await fs.mkdir(path.join(executableRoot, "resources"), { recursive: true });
  await fs.copyFile(path.join(stage, "app.asar"), path.join(executableRoot, "resources/app.asar"));
  await fs.cp(path.join(stage, "video-runtime"), path.join(executableRoot, "resources/video-runtime"), { recursive: true });
  await fs.mkdir(profile, { recursive: true });
  await fs.copyFile(path.join(originalProfile, "auth-session.json"), path.join(profile, "auth-session.json"));
  // Windows Chromium safeStorage uses the profile's OS-encrypted key.
  await fs.copyFile(path.join(originalProfile, "Local State"), path.join(profile, "Local State"));
  const config = path.join(profile, "video-production/config");
  await fs.mkdir(config, { recursive: true });
  let configurationSource = path.join(originalProfile, "video-production/config");
  try { await fs.access(path.join(configurationSource, "video-config.json")); }
  catch { configurationSource = path.join(source, "artifacts/video-local-trial"); }
  for (const name of ["video-config.json", "hermes-provider-config.dpapi", "hermes-voice-config.dpapi", "hermes-scene-config.dpapi"]) {
    await fs.copyFile(path.join(configurationSource, name), path.join(config, name));
  }
}
const beforeSession = digest(await fs.readFile(path.join(originalProfile, "auth-session.json")));
const safeEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PATH|SYSTEMROOT|WINDIR|COMSPEC|TEMP|TMP|USERPROFILE|APPDATA|LOCALAPPDATA|PROGRAMFILES|PROGRAMFILES\(X86\)|PATHEXT|SYSTEMDRIVE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i.test(key)));
let application, page;
const requests = [], errors = [];
async function capture(name) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const window = await application.browserWindow(page);
  const png = await window.evaluate(async win => {
    const image = await win.webContents.capturePage(undefined, { stayHidden: true, stayAwake: true });
    if (image.isEmpty()) throw new Error("Packaged window capture is empty");
    return image.toPNG().toString("base64");
  });
  await fs.writeFile(path.join(evidence, name), Buffer.from(png, "base64"));
}
try {
  application = await electron.launch({ executablePath: path.join(executableRoot, "迈医协同.exe"),
    env: { ...safeEnv, ...(useInstalled ? {} : { SIGNALDESK_E2E_USER_DATA: profile }), SIGNALDESK_RELEASE_API_URL: "http://127.0.0.1:9/no-release" } });
  // The user's enabled task widget can finish loading before the main window.
  const mainPage = () => application.windows().find(candidate => {
    try { const url = new URL(candidate.url()); return url.pathname.endsWith("/index.html") && url.searchParams.get("widget") !== "1"; }
    catch { return false; }
  });
  await expect.poll(() => Boolean(mainPage()), { timeout: 45000 }).toBe(true);
  page = mainPage();
  const nativeWindow = await application.browserWindow(page);
  await nativeWindow.evaluate(win => {
    win.webContents.setBackgroundThrottling(false);
    if (win.isMinimized()) win.restore(); win.show(); win.focus();
  });
  await page.bringToFront();
  await page.waitForFunction(() => Boolean(window.signalDesk?.loadAuthSession));
  console.log("CHECK: packaged window and preload ready");
  const sessionCheck = await page.evaluate(async () => {
    const session = await window.signalDesk.loadAuthSession();
    return { saved_login_readable: Boolean(session?.accessToken && session?.backendUrl) };
  });
  if (!sessionCheck.saved_login_readable) throw new Error("Original encrypted login could not be read in the verification profile");
  console.log("CHECK: saved encrypted login readable");
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => { const url = new URL(request.url()); if (url.pathname.includes("/api/v1/")) requests.push({ host: url.hostname, path: url.pathname, method: request.method(), origin: request.headers().origin }); });
  await expect(page.getByRole("button", { name: "内容生成", exact: true })).toBeVisible({ timeout: 45000 });
  console.log("CHECK: existing account restored");
  console.log("WINDOW:", JSON.stringify(await nativeWindow.evaluate(win => ({ visible: win.isVisible(), minimized: win.isMinimized(), focused: win.isFocused() }))));
  await page.getByRole("button", { name: "内容生成", exact: true }).click();
  await page.getByRole("tab", { name: "视频制作", exact: true }).click();
  await expect(page.getByRole("heading", { name: "给一个主题，规划整条视频" })).toBeVisible();
  const proof = await page.evaluate(async medicalCheck => {
    const session = await window.signalDesk.loadAuthSession();
    const connection = await window.signalDesk.getVideoConnection({ backendUrl: session.backendUrl, accessToken: session.accessToken });
    if (!connection || connection.accessToken === session.accessToken) throw new Error("Desktop video must have a separate local capability");
    const response = await fetch(connection.baseUrl + "/api/v1/video/projects/capabilities", { headers: { Authorization: "Bearer " + connection.accessToken }, redirect: "error" });
    if (!response.ok) throw new Error("Packaged video capabilities failed " + response.status);
    const capabilities = await response.json();
    const status = await (await fetch(connection.baseUrl + "/desktop/status", { headers: { Authorization: "Bearer " + connection.accessToken } })).json();
    let existingMedicalProject;
    if (medicalCheck) {
      const listed = await (await fetch(connection.baseUrl + "/api/v1/video/projects", { headers: { Authorization: "Bearer " + connection.accessToken } })).json();
      const candidate = listed.items.find(project => project.plan?.medical?.is_medical || /医疗|健康|体检|尿酸|疾病|血压|血糖|疫苗|用药|帕金森/.test(project.topic));
      if (candidate) {
        if (candidate.medical_gate?.required === true || "cleared" in (candidate.medical_gate || {}) || "publishable" in (candidate.medical_gate || {})) throw new Error("Existing medical project still has an automatic review gate");
        existingMedicalProject = { id: candidate.id, title: candidate.plan?.brief?.title || candidate.topic,
          medical_policy: candidate.medical_gate, current_content_confirmation: candidate.reviews?.inputs?.revision === candidate.revision };
      }
    }
    return { capabilities, status, business_backend_preserved: !session.backendUrl.startsWith(connection.baseUrl),
      distinct_local_capability: true, video_host: new URL(connection.baseUrl).hostname, identity_hash: connection.identityHash, existing_medical_project: existingMedicalProject };
  }, medicalCheck);
  if (!proof.business_backend_preserved || proof.capabilities.cost_review_required !== false || !proof.capabilities.scene.ready || !proof.capabilities.voice.ready || !proof.capabilities.avatar.ready) throw new Error("Formal video route or existing credentials were not integrated");
  if (seedanceCheck) {
    const scene = proof.capabilities.scene;
    if (scene.provider !== "seedance" || scene.model !== "doubao-seedance-2-0-mini-260615" || scene.base_url !== "https://ark.cn-beijing.volces.com/api/v3" || scene.credential_source !== "hermes") throw new Error("Ordinary video must use the existing Hermes credential for official Seedance Mini");
    if (proof.capabilities.avatar.provider !== "oldx-runninghub" || proof.capabilities.avatar.credential_type !== "SHARED" || proof.status.scene_provider !== "seedance") throw new Error("Scene and avatar routes are not split as requested");
  } else if (proof.capabilities.scene.provider !== "runninghub" || proof.capabilities.scene.credential_type !== "SHARED") throw new Error("RunningHub scene route was not retained");
  if (proof.status.identity_hash !== proof.identity_hash) throw new Error("Formal account scope mismatch");
  if (medicalCheck && !proof.capabilities.render.ready) throw new Error("Medical video post-processing must be ready");
  if (useInstalled && proof.existing_medical_project) {
    const title = proof.existing_medical_project.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    await page.getByRole("button", { name: new RegExp(title) }).click();
    delete proof.existing_medical_project.title;
  }
  if (useInstalled && process.env.VIDEO_SMOKE_AUDIO === "1") {
    // Read and play the existing completed file only. Never submit or confirm.
    await page.getByRole("button", { name: "播放配音试听", exact: true }).first().click();
    const audio = page.getByLabel("配音试听", { exact: true });
    await expect(audio).toBeVisible();
    await page.waitForFunction(() => {
      const audio = document.querySelector('audio[aria-label="配音试听"]');
      return audio?.readyState >= 2 && Number.isFinite(audio.duration) && audio.duration > 0;
    });
    await audio.evaluate(element => { element.muted = true; return element.play(); });
    await page.waitForFunction(() => document.querySelector('audio[aria-label="配音试听"]').currentTime > .25);
    proof.existing_audio = await audio.evaluate(element => {
      element.pause();
      return { duration: element.duration, current_time: element.currentTime, ready_state: element.readyState, error: element.error?.code ?? null, muted: element.muted };
    });
    await expect(page.getByRole("button", { name: "确认声音试听", exact: true })).toBeEnabled();
    await capture(`${label}-existing-audio.png`);
    if (proof.existing_audio.error !== null) throw new Error("Existing audio failed in the installed client");
    console.log("CHECK: installed existing audio decoded and playback time advanced");
  }
  if (!useInstalled) {
    const title = medicalCheck ? "医疗科普内容确认自检（不生成）" : "正式客户端集成验证（不生成）";
    await page.evaluate(async ({title,medicalCheck}) => {
      const session = await window.signalDesk.loadAuthSession();
      const connection = await window.signalDesk.getVideoConnection({ backendUrl: session.backendUrl, accessToken: session.accessToken });
      async function request(route, body, method) {
        const response = await fetch(connection.baseUrl + "/api/v1/video/projects" + route, { method, headers: { Authorization: "Bearer " + connection.accessToken, "Content-Type": "application/json" }, body: JSON.stringify(body), redirect: "error" });
        if (!response.ok) throw new Error("Metadata-only packaged smoke failed " + response.status);
        return response.json();
      }
      const project = await request("", { topic: medicalCheck ? "体检发现尿酸高，如何理解体检结果" : "太阳为什么发光" }, "POST");
      await request("/" + project.id + "/plan", { expected_revision: project.revision, locked_fields: [], plan: {
        brief: { title, audience: "普通观众", core_message: "集成自检" }, persona: "科普资料讲解员", visual_style: "简洁图解", layout: "9:16竖屏", resolution: "720p",
        audio_plan: { narration_style: "清晰自然", voice: "默认AI声音", music: "无", sound_notes: "旁白清晰" },
        scenes: [{ id: "scope-1", title: medicalCheck ? "体检结果的疑问" : "太阳的疑问", duration: 6, narration: medicalCheck ? "体检报告出现尿酸高，该如何理解？这条仅用于客户端流程自检。" : "太阳为什么能够持续发光？", visual_intent: medicalCheck ? "体检报告科普示意" : "太阳科学示意", seedance_prompt: medicalCheck ? "体检报告科普示意，无文字" : "太阳科学示意，无文字" }],
        interest_hooks: [{ id: "opening", kind: "opening", scene_id: "scope-1", copy: medicalCheck ? "体检数值该如何理解？" : "太阳的能量从哪里来？", payoff_scene_id: "scope-1" }],
        ...(medicalCheck ? {medical:{is_medical:true,reviewer:null,doctor_credentials_verified:false,review_status:"pending"}} : {}),
      } }, "PUT");
      if (medicalCheck) {
        const current = await (await fetch(connection.baseUrl + "/api/v1/video/projects/" + project.id, { headers: { Authorization: "Bearer " + connection.accessToken } })).json();
        if (current.medical_gate?.required === true || "cleared" in (current.medical_gate || {}) || "publishable" in (current.medical_gate || {})) throw new Error("Medical production still requires a credential/reviewer gate");
        if (current.reviews.inputs) throw new Error("Medical content confirmation must not be invented");
      }
    }, {title,medicalCheck});
    await page.reload();
    await page.getByRole("button", { name: "内容生成", exact: true }).click();
    await page.getByRole("tab", { name: "视频制作", exact: true }).click();
    await page.getByRole("button", { name: new RegExp(title.replace(/[()]/g, "\\$&")) }).click();
    await expect(page.getByRole("button", { name: /生成配音试听/ })).toBeVisible();
    if (medicalCheck) {
      await expect(page.getByText("医疗内容审核与依据", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "单独生成配音试听", exact: true })).toBeEnabled();
    }
  }
  await expect(page.getByRole("region", { name: "本次费用授权" })).toHaveCount(0);
  if (medicalCheck) await expect(page.getByText("医疗内容审核与依据", { exact: true })).toHaveCount(0);
  if (seedanceCheck) await expect(page.getByText(/普通 AI 视频使用火山方舟直连（Seedance 2.0 Mini），已复用 Hermes 的火山凭证/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^(报价|核算|确认费用)/ })).toHaveCount(0);
  await capture(`${label}-video-1440.png`);
  const window = await application.browserWindow(page);
  await window.evaluate(win => win.setSize(1080, 760));
  await capture(`${label}-video-1080.png`);
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await capture(`${label}-original-tasks.png`);
  await page.getByRole("button", { name: "内容生成", exact: true }).click();
  await page.getByRole("tab", { name: "视频制作", exact: true }).click();
  if (errors.length) throw new Error("Packaged renderer errors: " + errors.length);
  if (requests.some(request => request.path.endsWith("/quote") || request.path.endsWith("/jobs"))) throw new Error("Smoke must not quote or submit cloud jobs");
  const actual = await application.evaluate(({ app }) => ({ packaged: app.isPackaged, app_path: app.getAppPath(), user_data: app.getPath("userData") }));
  const report = { status: "PASS", mode: label, medical_confirmation_check: medicalCheck, split_provider_check: seedanceCheck, actual, proof, app_asar_sha256: digest(await fs.readFile(path.join(executableRoot, "resources/app.asar"))), original_login_ciphertext_unchanged: beforeSession === digest(await fs.readFile(path.join(originalProfile, "auth-session.json"))),
    local_video_requests: requests.filter(request => request.host === "127.0.0.1" && request.path.includes("/video/projects")).length,
    business_requests: requests.filter(request => request.host !== "127.0.0.1").length,
    observed_video_origins: [...new Set(requests.filter(request => request.host === "127.0.0.1").map(request => request.origin).filter(Boolean))],
    quote_requests: 0, paid_provider_calls: 0, production_database_copied: false, trial_database_copied: false };
  if (!report.original_login_ciphertext_unchanged || !actual.packaged || !report.local_video_requests) throw new Error("Installed identity preservation was not verified");
  await fs.writeFile(path.join(evidence, `${label}-smoke.json`), JSON.stringify(report, null, 2));
  console.log(`PASS: ${label} 迈医协同; existing login/backend retained; real local video service; no quote or paid generation`);
} catch (error) {
  if (page) await capture(`${label}-failed.png`).catch(() => {});
  // Do not print session, capability, HTML bodies or credentials.
  console.error("Packaged desktop smoke failed:", error.message);
  process.exitCode = 1;
} finally {
  if (application) await application.close();
  if (!useInstalled && path.resolve(profile).startsWith(path.resolve(probe) + path.sep)) await fs.rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
}
