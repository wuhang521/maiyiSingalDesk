import { _electron as electron, expect } from "@playwright/test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { createServer as createPortServer } from "node:net";
import { createHash, randomBytes } from "node:crypto";
import { access, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";

const root = process.cwd();
const selfTest = process.argv.includes("--self-test");
const idMapRed = process.argv.includes("--id-map-red");
const idMapGreen = process.argv.includes("--id-map-green");
const localOnly = selfTest || idMapRed || idMapGreen;
const artifacts = path.resolve(process.env.HEALTH_CHANNEL_E2E_ARTIFACTS || path.join(root, "artifacts/channel-e2e"));
assert.equal(path.basename(artifacts), "channel-e2e");
if (!localOnly && process.env.HEALTH_CHANNEL_UI_SLOT !== "granted") throw new Error("Reserve the serial Electron UI slot before running this harness");
const packageDir = process.env.SIGNALDESK_PACKAGE_DIR && path.resolve(process.env.SIGNALDESK_PACKAGE_DIR);
if (!localOnly) {
  assert.ok(packageDir && path.basename(packageDir) === "win-unpacked", "Requires the final win-unpacked program");
  const relative = path.relative(root, packageDir);
  assert.ok(!relative.startsWith("..") && !path.isAbsolute(relative), "Only the candidate's own disposable package may launch");
  await access(path.join(packageDir, "迈医协同.exe"));
  await access(path.join(packageDir, "resources/app.asar"));
}
await mkdir(artifacts, { recursive: true });
const run = await mkdtemp(path.join(artifacts, "run-"));
const attempt = path.basename(run);
const diagnosticKey = randomBytes(24).toString("hex");
const checks = [], errors = [], rendererErrors = [], externalRenderer = [], readinessErrors = [];
const sourceBindings = {
  harness: await sha(path.resolve("scripts/health-channel-e2e.mjs")),
  preview: await sha(path.resolve("server/tests/health_channel_preview.py")),
  main: await sha(path.resolve("electron/main.cjs")),
};
let app, page, backend, secondLaunch, lifecycle, explicitQuit, backendLog = "", result;
const ntfy = await ntfyStub();
const port = await availablePort();
const base = `http://127.0.0.1:${port}`;
const previewHeaders = { "X-Preview-Key": diagnosticKey };
const cleanEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(TASK_|ARK_|DASHSCOPE_|OPENAI_|ANTHROPIC_|AWS_|VOLC_|TTS_|RUNNINGHUB_|SIGNALDESK_|HEALTH_E2E_|PYTHONPATH$|PYTHONHOME$)/.test(key)));
const python = process.env.KNOWLEDGE_TEST_PYTHON || path.resolve(root, "../.venv-signaldesk/Scripts/python.exe");
backend = spawn(python, ["server/tests/health_channel_preview.py", String(port)], {
  cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: {
    ...cleanEnv, PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1", HEALTH_E2E_RUN_DIR: run,
    HEALTH_E2E_DIAGNOSTIC_KEY: diagnosticKey, HEALTH_E2E_NTFY_URL: ntfy.url,
    TASK_DATABASE_URL: `sqlite:///${path.join(run, "health-channel-e2e.db").replaceAll("\\", "/")}`,
    TASK_API_KEY: "synthetic-preview-only", TASK_KNOWLEDGE_STORAGE_DIR: path.join(run, "files"), TASK_VIDEO_STORAGE_DIR: path.join(run, "video"),
    TASK_KNOWLEDGE_ASR_ENABLED: "false", TASK_KNOWLEDGE_CLOUD_ASR_ENABLED: "false", TASK_KNOWLEDGE_OCR_ENABLED: "false",
    TASK_KNOWLEDGE_CLOUD_QA_ENABLED: "false", TASK_KNOWLEDGE_SEMANTIC_ENABLED: "false", TASK_KNOWLEDGE_DISPATCH_ENABLED: "false",
    TASK_KNOWLEDGE_ORGANIZATION_ENABLED: "false", TASK_KNOWLEDGE_PAID_DAILY_LIMIT: "0", TASK_KNOWLEDGE_PAID_MONTHLY_LIMIT: "0",
    TASK_HERMES_WEBHOOK_URL: "", TASK_KNOWLEDGE_GATEWAY_SECRET: "", TASK_PROMPT_API_KEY: "", TASK_KNOWLEDGE_QWEN_API_KEY: "",
  },
});
for (const stream of [backend.stdout, backend.stderr]) stream.on("data", data => { backendLog = (backendLog + data).slice(-100000); });
backend.on("error", error => { backendLog += error.message; });
async function stats() { return json(`${base}/preview-health/stats`, { headers: previewHeaders }); }
async function snapshot() { return page.evaluate(() => ({ channels: JSON.parse(localStorage.getItem("signaldesk.channels.v1") || "[]"), feed: JSON.parse(localStorage.getItem("signaldesk.feed.v1") || "[]"), trash: JSON.parse(localStorage.getItem("signaldesk.trash.v1") || "[]"), receipts: JSON.parse(localStorage.getItem("signaldesk.ntfy-receipts.v1") || "[]") })); }
async function notifications() { return app.evaluate(() => globalThis.__healthHarness.notifications); }
async function capture(name) {
  for (const [width, height, size] of [[1440, 920, "desktop"], [1080, 700, "compact"]]) {
    await page.setViewportSize({ width, height });
    await page.screenshot({ path: path.join(artifacts, `${attempt}-${size}-${name}.png`) });
  }
}

try {
  await expect.poll(async () => {
    if (backend.exitCode !== null || backend.signalCode !== null) throw new Error(backendLog || "Preview exited");
    try { return (await fetch(`${base}/health`)).status; } catch (error) { readinessErrors.push({ message: error.message, code: error.cause?.code }); return 0; }
  }, { timeout: 90000 }).toBe(200);
  const fixture = await json(`${base}/preview-health/fixture`, { headers: previewHeaders });
  const before = await stats();
  if (idMapRed || idMapGreen) {
    const login = await json(`${base}/api/v1/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "preview-health", password: "synthetic-health-only-password" }) });
    const incoming = message("wire-random-id-red");
    const original = { id: "daily:health:preingested-red", channelId: "health", title: incoming.title, body: incoming.message,
      source: "ntfy.synthetic.test", timestamp: incoming.time * 1000, tags: [], priority: 3 };
    const headers = { Authorization: `Bearer ${login.access_token}`, "Content-Type": "application/json", "X-SignalDesk-Operator": encodeURIComponent(fixture.operator) };
    await json(`${base}/api/v1/articles/batch`, { method: "POST", headers, body: JSON.stringify({ items: [original] }) });
    const replay = await json(`${base}/api/v1/articles/batch${idMapGreen ? "?resolve_ids=true" : ""}`, { method: "POST", headers, body: JSON.stringify({ items: [{ ...original, id: incoming.id }] }) });
    if (idMapRed) assert.deepEqual(replay, { accepted: 1 });
    else assert.deepEqual(replay, { accepted: 1, idMap: { [incoming.id]: original.id } });
    const articleId = idMapGreen ? replay.idMap[incoming.id] : incoming.id;
    const patch = await fetch(`${base}/api/v1/articles/${articleId}/state`, { method: "PATCH", headers,
      body: JSON.stringify({ read: true, saved: true, stateUpdatedAt: Date.now() }) });
    result = { passed: false, mode: idMapGreen ? "idmap-contract-green" : "idmap-contract-red", checks, batch_response: replay, state_patch_status: patch.status,
      canonical_original_retained: (await stats()).articles.some(item => item.id === original.id) };
    assert.equal(patch.status, 200, "Current frontend keeps the wire ID: red evidence requires its state PATCH to fail before ID resolution");
    const actual = await stats();
    assert.equal(actual.articles.filter(item => item.id === original.id || item.id === incoming.id).length, 1);
    assert.ok(actual.states.some(item => item.articleId === original.id && item.read && item.saved));
    assert.deepEqual(actual.states.filter(item => before.states.some(old => old.articleId === item.articleId)), before.states);
    checks.push("preingest + same-hash random wire ID resolves to one original and actual read/save PATCH200; old states retained");
    result.passed = true;
  } else if (selfTest) {
    const login = await json(`${base}/api/v1/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "preview-health", password: "synthetic-health-only-password" }) });
    const headers = { Authorization: `Bearer ${login.access_token}`, "Content-Type": "application/json" };
    const merged = await json(`${base}/api/v1/channels`, { method: "POST", headers, body: JSON.stringify({ id: "demo-health", name: "obsolete synthetic cache", topic: "", state: "draft" }) });
    assert.equal(merged.id, "health");
    const listed = await json(`${base}/api/v1/channels`, { headers });
    assert.deepEqual(listed.map(item => item.id), ["health"]);
    assert.equal(listed[0].managed, true); assert.equal(listed[0].maxItems, 100); assert.deepEqual(listed[0].aliases, ["demo-health"]);
    checks.push("actual login/SQLite persistent alias and managed cache-write guard");
    await json(`${base}/api/v1/articles/batch`, { method: "POST", headers, body: JSON.stringify({ items: [{ ...fixture.items[0], id: "synthetic-status-not-article", tags: ["health_status"] }] }) });
    const after = await stats();
    assert.equal(after.articles.length, before.articles.length);
    assert.deepEqual(after.states, before.states);
    assert.equal(after.external_attempts.length, 0);
    checks.push("preview preserves original rows/states and rejects health_status as an article");
    const controller = new AbortController();
    const response = await fetch(`${base}/api/v1/health-relay/health/json?since=10m`, { headers: { Authorization: `Bearer ${login.access_token}` }, signal: controller.signal });
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    let text = new TextDecoder().decode((await reader.read()).value);
    ntfy.emit(message("self-test-message"));
    while (!text.includes("self-test-message")) text += new TextDecoder().decode((await reader.read()).value);
    assert.equal(ntfy.state.maxActive, 1);
    controller.abort(); await reader.cancel().catch(() => undefined);
    await expect.poll(() => ntfy.state.active).toBe(0);
    checks.push("real authenticated relay + loopback NDJSON streaming/open/close completion accounting");
    result = { passed: true, mode: "self-test", checks, sqlite: true, electron_launched: false };
  } else {
    const asar = await import("@electron/asar");
    const packedMain = asar.extractFile(path.join(packageDir, "resources/app.asar"), "electron/main.cjs");
    const packedIndex = asar.extractFile(path.join(packageDir, "resources/app.asar"), "dist/index.html");
    assert.equal(createHash("sha256").update(packedMain).digest("hex"), sourceBindings.main, "Package must contain the final current main process");
    assert.equal(createHash("sha256").update(packedIndex).digest("hex"), await sha(path.resolve("dist/index.html")), "Package must contain the final Vite index");
    const env = { ...cleanEnv, SIGNALDESK_E2E_USER_DATA: path.join(run, "profile"), SIGNALDESK_BACKEND_URL: base, SIGNALDESK_RELEASE_API_URL: `${base}/preview-health/release` };
    delete env.ELECTRON_RUN_AS_NODE;
    app = await electron.launch({ executablePath: path.join(packageDir, "迈医协同.exe"), env });
    page = await app.firstWindow();
    page.setDefaultTimeout(15000);
    page.on("pageerror", error => rendererErrors.push(error.message));
    await app.context().route("**/*", route => {
      const url = route.request().url();
      if (/^https?:/.test(url) && ![base, ntfy.url].some(origin => url.startsWith(origin + "/"))) { externalRenderer.push(new URL(url).origin); return route.abort(); }
      return route.continue();
    });
    const info = await app.evaluate(({ app, BrowserWindow, ipcMain, session }, config) => {
      const nodeRequire = typeof require === "function" ? require : process.mainModule.require.bind(process.mainModule);
      const req = nodeRequire("node:module").createRequire(app.getAppPath() + "/package.json");
      req("electron-updater").autoUpdater.checkForUpdates = async () => null;
      globalThis.__healthHarness = { notifications: [], blocked: [], loginItemCalls: [] };
      // Widget toggle is real, but its OS startup integration must stay inside
      // this synthetic process rather than changing the user's Run registry.
      app.setLoginItemSettings = settings => { globalThis.__healthHarness.loginItemCalls.push({ openAtLogin: Boolean(settings.openAtLogin) }); };
      app.getLoginItemSettings = () => ({ openAtLogin: false, openAsHidden: false, wasOpenedAtLogin: false,
        wasOpenedAsHidden: false, restoreState: false, executableWillLaunchAtLogin: false, launchItems: [] });
      session.defaultSession.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*"] }, (details, callback) => {
        const origin = new URL(details.url).origin;
        const blocked = !config.origins.includes(origin);
        if (blocked) globalThis.__healthHarness.blocked.push(origin);
        callback({ cancel: blocked });
      });
      ipcMain.removeHandler("notification:show");
      ipcMain.handle("notification:show", (_event, payload) => { globalThis.__healthHarness.notifications.push(payload); return true; });
      for (const window of BrowserWindow.getAllWindows()) window.show();
      return { version: app.getVersion(), userData: app.getPath("userData") };
    }, { origins: [base, ntfy.url] });
    assert.equal(info.version, "0.6.4");
    assert.equal(path.resolve(info.userData), path.resolve(run, "profile"));
    checks.push("final 0.6.4 executable owns an isolated profile; network is loopback-only; OS notification IPC recorded");
    app.on("window", window => { window.setDefaultTimeout(15000); window.on("pageerror", error => rendererErrors.push(error.message)); });
    await app.context().addInitScript(() => {
      globalThis.__healthEvents = [];
      globalThis.__healthStatuses = [];
      window.signalDesk.onMessage(event => {
        if (event.type === "message") globalThis.__healthEvents.push({ id: event.payload?.id, topic: event.payload?.topic });
        if (event.type === "status") globalThis.__healthStatuses.push({ id: event.subscriptionId, status: event.status });
      });
    });
    const oldServer = ntfy.url + "/retired-relay";
    const shared = { name: "健康管理商业情报", description: "stale local cache", prompt: "synthetic cache rules", schedule: "08:00", maxItems: 20, includeTerms: [], excludeTerms: [], color: "green", state: "active", createdAt: fixture.timestamp - 100, updatedAt: fixture.timestamp, isDemo: false };
    const channels = [
      { ...shared, id: "demo-health", server: oldServer, topic: "retired-health", authToken: "synthetic-private-local-token", authTokenServer: oldServer, authTokenTopic: "retired-health", lastSyncAt: fixture.timestamp },
      { ...shared, id: "health", server: fixture.logical_server, topic: fixture.logical_topic, managed: true, readViaApi: true, aliases: ["demo-health"], authToken: "" },
    ];
    const feed = fixture.items.filter(item => item.id !== "health-trash").map(item => ({ ...item, channelId: "demo-health" }));
    const trashItem = { ...fixture.items.find(item => item.id === "health-trash"), channelId: "demo-health" };
    feed.push({ ...feed[0], id: "demo-message-preserved-history", title: "合成：保留演示历史", source: "示例数据" });
    await page.evaluate(({ channels, feed, trashItem, timestamp }) => {
      localStorage.setItem("signaldesk.channels.v1", JSON.stringify(channels));
      localStorage.setItem("signaldesk.feed.v1", JSON.stringify(feed));
      localStorage.setItem("signaldesk.trash.v1", JSON.stringify([{ id: trashItem.id, item: trashItem, deletedAt: timestamp }, { id: "purged-local-tombstone", deletedAt: timestamp, purged: true }]));
      localStorage.setItem("signaldesk.settings.v1", JSON.stringify({ notifications: true, notificationSound: false, globalAuthToken: "", historyWindow: "all" }));
    }, { channels, feed, trashItem, timestamp: fixture.timestamp });
    await page.reload();
    await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
    assert.equal(ntfy.state.active, 0); assert.equal(ntfy.state.opened, 0);
    await page.getByLabel("账号", { exact: true }).fill("preview-health");
    await page.getByLabel("密码", { exact: true }).fill("synthetic-health-only-password");
    await page.getByRole("button", { name: "继续", exact: true }).click();
    await expect(page.getByRole("heading", { name: "今日情报" })).toBeVisible();
    await expect.poll(async () => (await snapshot()).channels.find(item => item.id === "health")?.managed).toBe(true);
    await expect.poll(() => ntfy.state.active).toBe(1);
    let local = await snapshot(), actual = await stats();
    assert.deepEqual(local.channels.map(item => item.id), ["health"]);
    assert.equal(local.channels[0].authToken, "synthetic-private-local-token");
    assert.equal(local.channels[0].authTokenServer, oldServer); assert.equal(local.channels[0].authTokenTopic, "retired-health");
    assert.ok(actual.inbound.some(item => item.path === "/api/v1/channels"));
    assert.ok(actual.inbound.every(item => item.channelIds.every(id => id !== "demo-health")));
    assert.ok(local.feed.some(item => item.id === "demo-message-preserved-history"));
    assert.deepEqual(actual.states, before.states);
    assert.equal(ntfy.state.maxActive, 1); assert.equal(ntfy.state.authorizationSeen, true); assert.equal(ntfy.state.unexpectedAuthorization, false);
    assert.ok(ntfy.state.requests.every(item => item.pathname === "/synthetic-health/json"));
    assert.ok(actual.inbound.filter(item => item.path === "/api/v1/channels").every(item => !item.keys.some(key => ["authToken", "authTokenServer", "authTokenTopic"].includes(key))));
    checks.push("legacy ID + canonical managed-relay cache normalize before the first POST; no unauthenticated relay subscription; old-bound token stays local");
    await page.getByRole("button", { name: "智能频道", exact: true }).click();
    let row = page.locator(".channel-table-row").filter({ hasText: "健康管理商业情报" });
    await expect(row).toHaveCount(1);
    await expect(row.getByText("100 条", { exact: true })).toBeVisible();
    await expect(row.getByText("每天北京时间 09:00（服务器推送）", { exact: true })).toBeVisible();
    await expect(row.getByRole("button", { name: "暂停", exact: true })).toBeDisabled();
    await expect(row.getByRole("button", { name: "删除", exact: true })).toBeDisabled();
    await expect(row.getByText("运行中", { exact: true })).toBeVisible();
    await row.getByRole("button", { name: "检查接收", exact: true }).click();
    await expect(page.getByText("接收链路正常", { exact: true })).toBeVisible();
    assert.ok((await stats()).relay_requests.some(item => item.poll === "1" && item.since === "10m" && item.status === 200));
    await capture("managed-channel");
    await row.getByRole("button", { name: "查看受管配置" }).click();
    await expect(page.getByRole("note")).toContainText("提前准备情报");
    await expect(page.getByLabel("服务器", { exact: true })).toBeDisabled();
    await expect(page.getByLabel("Topic", { exact: true })).toBeDisabled();
    await expect(page.getByLabel("访问令牌", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("note")).toContainText("历史文章从服务器同步");
    await page.getByRole("button", { name: "完成", exact: true }).click();
    checks.push("managed health shows Beijing09:00/max100, read-only reception check, protected edits, and server-managed credentials");
    ntfy.state.failureStatus = 502; ntfy.endStreams();
    await expect.poll(async () => (await stats()).relay_requests.some(item => item.status === 502), { timeout: 15000 }).toBe(true);
    await expect(row.getByText("重连中", { exact: true })).toBeVisible();
    const opensBeforeRecovery = ntfy.state.opened;
    ntfy.state.failureStatus = 0;
    await expect.poll(() => ntfy.state.opened, { timeout: 15000 }).toBeGreaterThan(opensBeforeRecovery);
    await expect(row.getByText("运行中", { exact: true })).toBeVisible();
    assert.equal(ntfy.state.maxActive, 1);
    checks.push("actual main-process EOF/HTTP502 recovery uses real connected/reconnecting events and one active stream");
    const normal = message("normal-business", { title: "合成：每日健康商业情报", message: "这是完整的程序联调合成文章。" });
    const session = await page.evaluate(() => window.signalDesk.loadAuthSession());
    const apiHeaders = { Authorization: `Bearer ${session.accessToken}`, "Content-Type": "application/json" };
    const originalId = "daily:health:preingested-original";
    await json(`${base}/api/v1/articles/batch`, { method: "POST", headers: apiHeaders, body: JSON.stringify({ items: [{
      id: originalId, channelId: "health", title: normal.title, body: normal.message, source: fixture.logical_server.replace(/^https?:\/\//, ""),
      timestamp: normal.time * 1000, tags: [], priority: 3,
    }] }) });
    const retired = message("retired-topic", { topic: "retired-health", title: "退休topic不能入库" });
    const status = message("preparing-status", { title: "internal-status-fingerprint", message: "今天的健康情报仍在准备，完成后会继续推送。", tags: ["health_status"] });
    for (const item of [normal, normal, status, status]) ntfy.emit(item);
    await page.waitForFunction(() => globalThis.__healthEvents.length >= 4);
    await expect.poll(async () => (await notifications()).length).toBe(2);
    await expect.poll(async () => (await snapshot()).feed.some(item => item.id === originalId)).toBe(true);
    actual = await stats(); local = await snapshot();
    assert.equal(actual.articles.filter(item => [normal.id, originalId].includes(item.id)).length, 1);
    assert.ok(!actual.articles.some(item => [retired.id, status.id].includes(item.id)));
    assert.ok(actual.inbound.every(item => item.articleIds.every(id => id !== status.id && id !== retired.id)));
    assert.ok(!local.feed.some(item => [retired.id, status.id].includes(item.id)));
    assert.deepEqual(actual.states.filter(item => before.states.some(old => old.articleId === item.articleId)), before.states);
    const shown = await notifications();
    assert.equal(shown.filter(item => item.messageId === originalId).length, 1);
    assert.equal(shown.filter(item => item.messageId === "health:status").length, 1);
    assert.equal(shown.find(item => item.messageId === "health:status").title, "健康情报推送状态");
    checks.push("real NDJSON normal/duplicate/retired-topic/status events yield one article and one notification per valid event; status never enters API/feed");
    await page.getByRole("button", { name: /^全部信息/ }).click();
    await expect(page.locator(".feed-row").filter({ hasText: normal.title })).toHaveCount(1);
    await page.locator(".feed-row").filter({ hasText: normal.title }).click();
    await page.getByRole("button", { name: "收藏", exact: true }).last().click();
    await expect.poll(async () => (await stats()).states.some(item => item.articleId === originalId && item.read && item.saved)).toBe(true);
    const patches = (await stats()).state_patches.filter(item => item.articleId === originalId);
    assert.ok(patches.length >= 2); assert.ok(patches.every(item => item.status === 200));
    assert.ok(!(await stats()).state_patches.some(item => item.articleId === normal.id));
    checks.push("worker-style preingest precedes random wire ID; one original is shown and actual read/save PATCH succeeds on the canonical ID");
    const openedBeforeRetired = ntfy.state.opened;
    ntfy.emit(retired);
    await expect.poll(() => ntfy.state.opened, { timeout: 15000 }).toBeGreaterThan(openedBeforeRetired);
    assert.equal((await notifications()).length, 2);
    await capture("received-normal");
    await page.getByRole("button", { name: "同步", exact: true }).click();
    await expect(page.getByText("同步完成", { exact: true })).toBeVisible();
    actual = await stats(); local = await snapshot();
    assert.ok(actual.relay_requests.some(item => item.poll === "1" && item.since === "24h" && item.status === 200));
    assert.ok(actual.inbound.every(item => item.articleIds.every(id => id !== status.id && id !== retired.id)));
    assert.ok(!local.feed.some(item => [status.id, retired.id].includes(item.id)));
    checks.push("manual historical poll also excludes status and retired topic before API batching");
    await page.reload();
    await expect(page.getByRole("heading", { name: "今日情报" })).toBeVisible();
    await page.waitForFunction(({ normal, status }) => globalThis.__healthEvents.some(item => item.id === normal) && globalThis.__healthEvents.some(item => item.id === status), { normal: normal.id, status: status.id });
    await expect.poll(async () => (await stats()).inflight).toBe(0);
    assert.equal((await notifications()).length, 2);
    await expect.poll(() => ntfy.state.active).toBe(1);
    assert.equal(ntfy.state.maxActive, 1);
    local = await snapshot(); actual = await stats();
    assert.deepEqual(local.channels.map(item => item.id), ["health"]);
    assert.equal(local.channels[0].authToken, "synthetic-private-local-token");
    assert.ok(local.trash.some(item => item.id === "health-trash" && item.item.saved && item.item.read));
    assert.ok(local.trash.some(item => item.id === "purged-local-tombstone" && item.purged && !item.item));
    assert.ok(local.feed.some(item => item.id === "daily:demo-health:original-id" && item.channelId === "health" && item.read && item.saved));
    assert.ok(actual.inbound.every(item => item.channelIds.every(id => id !== "demo-health")));
    assert.deepEqual(actual.states.filter(item => before.states.some(old => old.articleId === item.articleId)), before.states);
    checks.push("renderer reload preserves canonical channel/old article IDs/user states/local credential and replays without duplicate notification or stream");
    await page.getByRole("button", { name: /^回收站/ }).click();
    await expect(page.getByText("合成：保留回收站", { exact: true }).first()).toBeVisible();
    await capture("preserved-trash");
    const beforeReopen = await snapshot();
    await page.getByRole("button", { name: "桌面挂件", exact: true }).click();
    await expect.poll(() => app.windows().filter(window => window.url().includes("widget=1")).length).toBe(1);
    const windowBefore = await app.evaluate(({ BrowserWindow, app }) => ({
      pid: process.pid, profile: app.getPath("userData"),
      windows: BrowserWindow.getAllWindows().map(window => ({ id: window.id, webContentsId: window.webContents.id,
        backgroundThrottling: window.webContents.getBackgroundThrottling(), widget: window.webContents.getURL().includes("widget=1") })),
    }));
    const mainBefore = windowBefore.windows.find(window => !window.widget), widgetBefore = windowBefore.windows.find(window => window.widget);
    assert.ok(mainBefore && widgetBefore);
    assert.equal(mainBefore.backgroundThrottling, true, "Background case must use the production throttling default");
    const retainedPage = page;
    await app.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id).close(), mainBefore.id);
    await expect.poll(() => app.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.isMinimized(), mainBefore.id)).toBe(true);
    assert.equal(retainedPage.isClosed(), false);
    const whileClosed = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map(window => ({
      id: window.id, webContentsId: window.webContents.id, widget: window.webContents.getURL().includes("widget=1") })));
    assert.equal(whileClosed.find(window => !window.widget).id, mainBefore.id);
    assert.equal(whileClosed.find(window => !window.widget).webContentsId, mainBefore.webContentsId);
    assert.equal(whileClosed.find(window => window.widget).id, widgetBefore.id);
    assert.equal(ntfy.state.active, 1);
    const background = message("widget-background-real-ndjson", { title: "合成：主窗关闭后后台接收", message: "Synthetic background delivery with the production renderer retained." });
    ntfy.emit(background);
    await expect.poll(async () => (await notifications()).length, { timeout: 20000 }).toBe(3);
    await expect.poll(async () => (await snapshot()).feed.filter(item => item.title === background.title).length).toBe(1);
    const backgroundArticle = (await snapshot()).feed.find(item => item.title === background.title);
    assert.equal((await notifications()).filter(item => item.messageId === backgroundArticle.id).length, 1);
    ntfy.emit(background);
    await expect.poll(async () => (await stats()).inflight).toBe(0);
    assert.equal((await notifications()).length, 3);
    const openedBeforeBackgroundReconnect = ntfy.state.opened;
    ntfy.endStreams();
    await expect.poll(() => ntfy.state.opened).toBeGreaterThan(openedBeforeBackgroundReconnect);
    await expect.poll(() => ntfy.state.active).toBe(1);
    await page.waitForFunction(id => globalThis.__healthEvents.filter(item => item.id === id).length >= 3, background.id);
    assert.equal((await notifications()).length, 3);
    assert.equal((await snapshot()).feed.filter(item => item.title === background.title).length, 1);
    const beforeRestore = await snapshot();
    const widgetPage = app.windows().find(window => window.url().includes("widget=1"));
    await widgetPage.screenshot({ path: path.join(artifacts, `${attempt}-widget-main-minimized-background.png`) });
    checks.push("native Main close with a live widget retains the same renderer and one stream; real loopback NDJSON is saved/notified once with production background throttling; duplicate and reconnect add no notification");
    secondLaunch = spawn(path.join(packageDir, "迈医协同.exe"), [], { env, windowsHide: true, stdio: "ignore" });
    const secondExit = new Promise((resolve, reject) => { secondLaunch.once("error", reject); secondLaunch.once("exit", (code, signal) => resolve({ code, signal })); });
    const secondary = await Promise.race([secondExit, new Promise((_, reject) => { const timer = setTimeout(() => reject(Error("Second instance did not exit after handoff")), 15000); timer.unref(); })]);
    await expect.poll(() => app.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.isMinimized(), mainBefore.id)).toBe(false);
    assert.equal(page, retainedPage);
    await expect(page.getByText("合成：保留回收站", { exact: true }).first()).toBeVisible();
    assert.equal(secondary.code, 0);
    await expect.poll(() => ntfy.state.active).toBe(1);
    await page.waitForFunction(({ normal, status }) => globalThis.__healthEvents.some(item => item.id === normal) && globalThis.__healthEvents.some(item => item.id === status), { normal: normal.id, status: status.id });
    await expect.poll(async () => (await stats()).inflight).toBe(0);
    const afterReopen = await snapshot();
    const normalize = value => ({ ...value, feed: [...value.feed].sort((a,b) => a.id.localeCompare(b.id)), trash: [...value.trash].sort((a,b) => a.id.localeCompare(b.id)) });
    assert.deepEqual(normalize(afterReopen), normalize(beforeRestore));
    assert.equal((await notifications()).length, 3); assert.equal(ntfy.state.maxActive, 1);
    const windowAfter = await app.evaluate(({ BrowserWindow, app }) => ({ pid: process.pid, profile: app.getPath("userData"),
      windows: BrowserWindow.getAllWindows().map(window => ({ id: window.id, widget: window.webContents.getURL().includes("widget=1") })) }));
    assert.equal(windowAfter.pid, windowBefore.pid); assert.equal(windowAfter.profile, windowBefore.profile);
    assert.equal(windowAfter.windows.filter(window => window.widget).length, 1);
    assert.equal(windowAfter.windows.find(window => window.widget).id, widgetBefore.id);
    assert.equal(windowAfter.windows.filter(window => !window.widget).length, 1);
    assert.equal(windowAfter.windows.find(window => !window.widget).id, mainBefore.id);
    checks.push("actual same-profile second EXE restores the same minimized Main; no second renderer, stream or notification is created");
    // The newly received row initially has a local state clock of zero. A real
    // API reload supplies its server clock and explicit null clickUrl. Settle
    // that documented transport enrichment before comparing recreate bytes,
    // while checking every business field and every pre-existing row exactly.
    const beforeHydration = await snapshot();
    await page.reload();
    await expect(page.getByRole("heading", { name: "今日情报" })).toBeVisible();
    await expect.poll(() => ntfy.state.active).toBe(1);
    await expect.poll(async () => (await snapshot()).feed.find(item => item.id === backgroundArticle.id)?.stateUpdatedAt).toBeGreaterThan(0);
    await expect.poll(async () => (await stats()).inflight).toBe(0);
    const beforeDestroyed = await snapshot();
    const hydratedBackground = beforeDestroyed.feed.find(item => item.id === backgroundArticle.id);
    assert.equal(hydratedBackground.clickUrl, null);
    const hydrationProjection = value => normalize({ ...value, feed: value.feed.map(item => item.id === backgroundArticle.id ?
      { ...item, clickUrl: null, stateUpdatedAt: 0 } : item) });
    assert.deepEqual(hydrationProjection(beforeDestroyed), hydrationProjection(beforeHydration));
    assert.equal((await notifications()).length, 3);
    const unexpectedlyClosed = page;
    const rebuiltWindow = app.waitForEvent("window", { timeout: 20000 });
    await app.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id).destroy(), mainBefore.id);
    await expect.poll(() => unexpectedlyClosed.isClosed()).toBe(true);
    secondLaunch = spawn(path.join(packageDir, "迈医协同.exe"), [], { env, windowsHide: true, stdio: "ignore" });
    const rebuiltExit = new Promise((resolve, reject) => { secondLaunch.once("error", reject); secondLaunch.once("exit", (code, signal) => resolve({ code, signal })); });
    page = await rebuiltWindow;
    const rebuiltSecondary = await rebuiltExit;
    assert.equal(rebuiltSecondary.code, 0);
    await expect(page.getByRole("heading", { name: "今日情报" })).toBeVisible();
    await expect.poll(() => ntfy.state.active).toBe(1);
    await expect.poll(async () => (await stats()).inflight).toBe(0);
    assert.deepEqual(normalize(await snapshot()), normalize(beforeDestroyed));
    assert.equal((await notifications()).length, 3);
    await page.getByRole("button", { name: "智能频道", exact: true }).click();
    row = page.locator(".channel-table-row").filter({ hasText: "健康管理商业情报" });
    await expect(row.getByText("运行中", { exact: true })).toBeVisible();
    await capture("reopened-main");
    assert.deepEqual(normalize(await snapshot()), normalize(beforeDestroyed));
    const sortedKeys = value => Array.isArray(value) ? value.map(sortedKeys) : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sortedKeys(value[key])])) : value;
    const stateBeforeHash = createHash("sha256").update(JSON.stringify(sortedKeys(normalize(beforeDestroyed)))).digest("hex");
    const stateAfterHash = createHash("sha256").update(JSON.stringify(sortedKeys(normalize(await snapshot())))).digest("hex");
    assert.equal(stateAfterHash, stateBeforeHash);
    lifecycle = { main_close_retained_renderer_by_minimizing: true, background_throttling_override: false,
      background_message_notifications: 1, background_message_articles: 1, duplicate_or_reconnect_notifications: 0,
      second_native_process_pid: secondLaunch.pid, secondary_exit_code: secondary.code,
      same_original_application_pid: true, same_isolated_profile: true, widget_identity_retained: true,
      same_main_restored_once: true, unexpected_destroy_rebuilt_main_once: true,
      cached_article_trash_receipt_credential_state_preserved: true, state_before_sha256: stateBeforeHash, state_after_sha256: stateAfterHash,
      notifications_after_reopen: (await notifications()).length, max_active_streams: ntfy.state.maxActive, close_path: "BrowserWindow.close() normal closed-event lifecycle" };
    checks.push("unexpected actual Main destroy still permits same-profile second EXE recreation with all cached states, receipts and one health stream preserved");
    await json(`${base}/preview-health/revoke`, { method: "POST", headers: previewHeaders });
    ntfy.endStreams();
    await expect.poll(async () => (await stats()).relay_requests.some(item => item.status === 401), { timeout: 15000 }).toBe(true);
    await page.getByRole("button", { name: "智能频道", exact: true }).click();
    await expect(row.getByText("重连中", { exact: true })).toBeVisible();
    assert.equal(ntfy.state.active, 0); assert.equal(ntfy.state.unexpectedAuthorization, false);
    assert.ok(ntfy.state.requests.every(item => item.method === "GET"));
    checks.push("expired synthetic login yields HTTP401/reconnecting with no fallback or personal-token forwarding to upstream");
    const refreshedLogin = await json(`${base}/api/v1/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "preview-health", password: "synthetic-health-only-password" }) });
    await page.evaluate(async credentials => {
      const previous = await window.signalDesk.loadAuthSession();
      await window.signalDesk.saveAuthSession({ ...previous, accessToken: credentials.access_token, expiresAt: credentials.expires_at });
    }, refreshedLogin);
    await page.reload();
    await expect(page.getByRole("heading", { name: "今日情报" })).toBeVisible();
    await expect.poll(() => ntfy.state.active).toBe(1);
    assert.equal((await notifications()).length, 3);
    const guards = await app.evaluate(() => globalThis.__healthHarness.blocked);
    assert.deepEqual(rendererErrors, []); assert.deepEqual(externalRenderer, []); assert.deepEqual(guards, []); assert.deepEqual(actual.external_attempts, []);
    checks.push("no renderer errors, external requests, real profile, cloud calls or external recipient messages");
    const allShown = await notifications();
    const ownProcess = app.process();
    const streamsBeforeQuit = ntfy.state.active;
    assert.equal(streamsBeforeQuit, 1);
    const quitExit = new Promise(resolve => ownProcess.once("exit", (code, signal) => resolve({ code, signal })));
    await app.evaluate(({ app }) => { setTimeout(() => app.quit(), 0); });
    const quitResult = await Promise.race([quitExit, new Promise((_, reject) => { const timer = setTimeout(() => reject(Error("Explicit app.quit was prevented")), 20000); timer.unref(); })]);
    assert.equal(quitResult.code, 0);
    await expect.poll(() => ntfy.state.active).toBe(0);
    explicitQuit = { actual_app_quit: true, primary_exit_code: quitResult.code, live_streams_before_quit: streamsBeforeQuit, own_streams_closed: true };
    checks.push("explicit actual app.quit exits with the widget alive and releases every owned stream");
    result = { passed: true, mode: "packaged-electron-background-r3", revision: "background-r3", version: info.version, checks, app_asar_sha256: await sha(path.join(packageDir, "resources/app.asar")), package_dir: packageDir,
      notifications: allShown, lifecycle, explicitQuit, actual_electron: true, actual_api_sqlite: true, actual_ntfy_ndjson_http: true, synthetic_https_transport_adapter: true, real_public_tls_verified: false, os_notification_delivery_mocked: true, os_launch_at_login_mocked: true };
  }
} catch (error) {
  errors.push({ name: error.name, message: error.message, stack: error.stack });
  result = { ...result, passed: false, mode: idMapGreen ? "idmap-contract-green" : idMapRed ? "idmap-contract-red" : selfTest ? "self-test" : "packaged-electron", checks, error: error.message };
  if (page) await page.screenshot({ path: path.join(artifacts, `${attempt}-failure.png`) }).catch(() => undefined);
  process.exitCode = 1;
} finally {
  if (app && !explicitQuit) await app.close().catch(error => errors.push({ name: "close", message: error.message }));
  if (secondLaunch && secondLaunch.exitCode === null && secondLaunch.signalCode === null) {
    secondLaunch.kill(); await Promise.race([new Promise(resolve => secondLaunch.once("exit", resolve)), new Promise(resolve => setTimeout(resolve, 5000))]);
  }
  if (backend && backend.exitCode === null) { backend.kill(); await Promise.race([new Promise(resolve => backend.once("exit", resolve)), new Promise(resolve => setTimeout(resolve, 5000))]); }
  await ntfy.close();
  result = { ...result, attempt, ntfy: ntfy.state, renderer_errors: rendererErrors, external_renderer: externalRenderer, paid_calls: 0, external_messages_sent: 0,
    source_sha256: sourceBindings,
    synthetic_only: true, original_installation_touched: false, source_product_modified_by_harness: false, profile: "isolated synthetic profile under the run directory",
    readiness_errors: readinessErrors.slice(-3), backend_pid: backend?.pid, cleanup_backend_exited: backend?.exitCode !== null || backend?.signalCode !== null };
  const safeLog = backendLog.replaceAll(diagnosticKey, "[preview diagnostic nonce]");
  await writeFile(path.join(artifacts, `${attempt}-backend.log`), safeLog);
  await writeFile(path.join(artifacts, `${attempt}-errors.json`), JSON.stringify(errors, null, 2));
  await writeFile(path.join(artifacts, `${attempt}-result.json`), JSON.stringify(result, null, 2));
  await writeFile(path.join(artifacts, idMapGreen ? "idmap-contract.green.json" : idMapRed ? "idmap-contract.red.json" : selfTest ? "self-test-result.json" : "result.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: result.passed, checks: checks.length, mode: result.mode, max_active_streams: ntfy.state.maxActive, renderer_errors: rendererErrors.length, run: attempt }));
}

function message(id, extra = {}) { return { id, event: "message", topic: "synthetic-health", time: Math.floor(Date.now() / 1000), title: "合成测试消息", message: "synthetic only", ...extra }; }
async function sha(filename) { return createHash("sha256").update(await readFile(filename)).digest("hex"); }
async function json(url, options) { const response = await fetch(url, options); if (!response.ok) throw new Error(`Loopback API ${new URL(url).pathname}: HTTP${response.status} ${(await response.text()).slice(0, 300)}`); return response.json(); }
async function availablePort() { const server = createPortServer(); await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); }); const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port; }
async function ntfyStub() {
  const streams = new Set(), history = new Map(), sockets = new Set();
  const state = { active: 0, maxActive: 0, opened: 0, closed: 0, polls: 0, authorizationSeen: false, unexpectedAuthorization: false, failureStatus: 0, requests: [] };
  const server = createHttpServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    state.requests.push({ method: request.method, pathname: url.pathname, poll: url.searchParams.get("poll") === "1", authorization: Boolean(request.headers.authorization) });
    if (request.headers.authorization) state.authorizationSeen = true;
    state.unexpectedAuthorization ||= Boolean(request.headers.authorization && request.headers.authorization !== "Bearer synthetic-upstream-token");
    if (request.method !== "GET" || url.pathname !== "/synthetic-health/json") { response.writeHead(404); response.end(); return; }
    if (state.failureStatus) { response.writeHead(state.failureStatus, { "Content-Type": "application/json" }).end('{"detail":"Synthetic upstream unavailable"}'); return; }
    response.writeHead(200, { "Content-Type": "application/x-ndjson", "Cache-Control": "no-cache", Connection: "keep-alive" });
    if (url.searchParams.get("poll") === "1") { state.polls++; response.end(Array.from(history.values()).map(item => JSON.stringify(item) + "\n").join("")); return; }
    streams.add(response); state.active = streams.size; state.opened++; state.maxActive = Math.max(state.maxActive, state.active);
    response.write(JSON.stringify({ event: "open", topic: "synthetic-health", id: "synthetic-open", time: Math.floor(Date.now() / 1000) }) + "\n");
    for (const item of history.values()) response.write(JSON.stringify(item) + "\n");
    response.on("close", () => { if (streams.delete(response)) state.closed++; state.active = streams.size; });
  });
  const heartbeat = setInterval(() => {
    const frame = JSON.stringify({ event: "keepalive", topic: "synthetic-health", id: "heartbeat-" + Date.now(), time: Math.floor(Date.now() / 1000) }) + "\n";
    for (const response of streams) if (!response.writableEnded && !response.destroyed) response.write(frame);
  }, 15000);
  heartbeat.unref();
  server.on("connection", socket => { sockets.add(socket); socket.on("close", () => sockets.delete(socket)); });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const url = `http://127.0.0.1:${server.address().port}`;
  return { url, state, emit(item) { if (item.topic === "synthetic-health") history.set(item.id, item); for (const response of streams) response.write(JSON.stringify(item) + "\n"); },
    endStreams() { for (const response of streams) response.end(); },
    async close() { clearInterval(heartbeat); for (const response of streams) response.destroy(); for (const socket of sockets) socket.destroy(); await new Promise(resolve => server.close(resolve)); } };
}
