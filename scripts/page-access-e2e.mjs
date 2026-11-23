import { _electron as electron, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:net";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifacts = path.join(root, "artifacts", "members");
const python = process.env.MEMBER_TEST_PYTHON;
if (!python || !path.isAbsolute(python)) throw new Error("Set MEMBER_TEST_PYTHON to the absolute test Python path");

const dist = path.join(root, "dist");
const digest = data => createHash("sha256").update(data).digest("hex");
const index = await readFile(path.join(dist, "index.html"));
const entry = index.toString().match(/src="\.\/(assets\/index-[\w-]+\.js)"/)?.[1];
if (!entry) throw new Error("Cannot identify the built Electron renderer entry");
const build = { indexSha256: digest(index), entry, entrySha256: digest(await readFile(path.join(dist, entry))) };
if (build.entrySha256 !== process.env.PAGE_ACCESS_E2E_ENTRY_SHA256) throw new Error("Renderer entry SHA does not match the authorized build");
const tracked = ["dist/index.html", `dist/${entry}`, "electron/main.cjs", "electron/preload.cjs", "electron/video-desktop.cjs",
  "server/app/main.py", "server/app/auth.py", "server/app/auth_routes.py", "server/app/page_access.py",
  "server/app/models.py", "server/app/schemas.py", "server/app/members.py", "server/tests/member_preview.py"];
async function trackedHashes() {
  return Object.fromEntries(await Promise.all(tracked.map(async file => [file, digest(await readFile(path.join(root, file)))])));
}
const loadedFiles = await trackedHashes();

await mkdir(artifacts, { recursive: true });
const run = await mkdtemp(path.join(artifacts, "page-access-run-"));
const steps = [];
const screenshots = [];
const apiChecks = [];
const unexpectedOrigins = [];
const pageErrors = [];
const apps = [];
const pages = new Map();
const sockets = [];
const password = "preview-only-password";
const serviceKey = "page-access-e2e-service";
const ownTitle = "隔离验收：成员本人的任务";
const adminTitle = "隔离验收：管理员名下的任务";
const account = "本地普通成员";
const navigation = page => page.getByRole("navigation", { name: "主导航" });
const navButton = (page, name) => navigation(page).getByRole("button", { name, exact: true });
const button = (page, name) => page.getByRole("button", { name, exact: true });
function recordStep(message) {
  steps.push(message);
  console.log(message);
}
const hostEnv = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
  /^(appdata|localappdata|userprofile|windir|systemroot|path|pathext|comspec|temp|tmp|programdata|programfiles(?:\(x86\))?)$/i.test(name)));

async function listen() {
  const socket = createServer(connection => connection.destroy());
  sockets.push(socket);
  await new Promise((resolve, reject) => {
    socket.once("error", reject);
    socket.listen(0, "127.0.0.1", resolve);
  });
  return socket;
}

async function screenshot(page, name) {
  const filename = `${name}.png`;
  await page.screenshot({ path: path.join(run, filename), animations: "disabled" });
  screenshots.push(filename);
}

let base;
let backend;
let backendExit;
let backendLog = "";
let failure;

async function api(endpoint, options = {}) {
  const response = await fetch(`${base}${endpoint}`, { signal: AbortSignal.timeout(8_000), ...options });
  return { status: response.status, body: await response.json().catch(() => null) };
}

async function checkApi(label, endpoint, headers, status) {
  const response = await api(endpoint, { headers });
  apiChecks.push({ label, status: response.status });
  expect(response.status, label).toBe(status);
  return response.body;
}

async function launch(label, proxyPort) {
  const profile = path.join(run, `${label}-profile`);
  const app = await electron.launch({
    args: [root, `--user-data-dir=${profile}`, `--disk-cache-dir=${path.join(profile, "cache")}`,
      `--proxy-server=http://127.0.0.1:${proxyPort}`, "--proxy-bypass-list=127.0.0.1", "--disable-background-networking"],
    cwd: run,
    env: { ...hostEnv, SIGNALDESK_E2E_USER_DATA: profile, SIGNALDESK_BACKEND_URL: base },
    timeout: 25_000,
  });
  apps.push(app);
  const isolation = await app.evaluate(({ app: desktop, BrowserWindow, session }, origin) => {
    for (const window of BrowserWindow.getAllWindows()) window.webContents.setBackgroundThrottling(false);
    session.defaultSession.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*"] }, (request, done) => {
      done({ cancel: new URL(request.url).origin !== origin });
    });
    return { userData: desktop.getPath("userData"), sessionData: desktop.getPath("sessionData"), pid: process.pid, packaged: desktop.isPackaged };
  }, base);
  expect(path.resolve(isolation.userData)).toBe(profile);
  expect(path.resolve(isolation.sessionData)).toBe(profile);
  expect(isolation.packaged).toBe(false);
  const page = await app.firstWindow();
  pages.set(label, page);
  await page.setViewportSize({ width: 1440, height: 920 });
  page.on("request", request => {
    const url = new URL(request.url());
    if (["http:", "https:"].includes(url.protocol) && url.origin !== base) unexpectedOrigins.push(url.origin);
  });
  page.on("pageerror", error => pageErrors.push(error.message));
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  return { page, pid: isolation.pid };
}

async function signIn(page, username) {
  const usernameInput = page.getByLabel("账号", { exact: true });
  const passwordInput = page.getByLabel("密码", { exact: true });
  await page.bringToFront();
  await usernameInput.pressSequentially(username, { delay: 12 });
  await passwordInput.pressSequentially(password, { delay: 12 });
  await passwordInput.press("Tab");
  expect(await usernameInput.inputValue() === username, "synthetic username reached the login form").toBe(true);
  expect(await passwordInput.inputValue() === password, "synthetic password reached the login form").toBe(true);
  const responsePromise = page.waitForResponse(response => response.url() === `${base}/api/v1/auth/login` && response.request().method() === "POST");
  await button(page, "继续").click();
  const response = await responsePromise;
  const submitted = response.request().postDataJSON();
  expect(submitted?.username === username, "login submits the synthetic username exactly").toBe(true);
  expect(submitted?.password === password, `login submits the synthetic password exactly (received length ${submitted?.password?.length ?? 0})`).toBe(true);
  expect(response.status(), `isolated ${username} sign-in`).toBe(200);
  const login = await response.json();
  await expect(navigation(page)).toBeVisible();
  return { user: login.user, headers: {
    Authorization: `Bearer ${login.access_token}`, "X-SignalDesk-Operator": encodeURIComponent(login.user.display_name),
  } };
}

async function editGrant(page, labels, capture) {
  await button(page, `管理 ${account}`).click();
  const mode = page.getByRole("combobox", { name: /页面访问方式/ });
  if (await mode.inputValue() !== "custom") await mode.selectOption("custom");
  const group = page.getByRole("group", { name: "可访问页面" });
  const checkboxes = group.getByRole("checkbox");
  await expect(checkboxes).toHaveCount(10);
  for (const checkbox of await checkboxes.all()) await checkbox.uncheck();
  for (const label of labels) await group.getByRole("checkbox", { name: label, exact: true }).check();
  if (capture) {
    await group.scrollIntoViewIfNeeded();
    await screenshot(page, capture);
  }
  await button(page, "保存用户设置").click();
  await expect(page.getByRole("status").filter({ hasText: "用户设置已保存" })).toBeVisible();
}

try {
  const backendSocket = await listen();
  const port = backendSocket.address().port;
  await new Promise(resolve => backendSocket.close(resolve));
  const proxy = await listen();
  base = `http://127.0.0.1:${port}`;
  backend = spawn(python, ["-B", path.join(root, "server", "tests", "member_preview.py"), String(port)], {
    cwd: run, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...hostEnv, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1",
      TASK_DATABASE_URL: `sqlite:///${path.join(run, "member-e2e.db").replaceAll("\\", "/")}`, TASK_API_KEY: serviceKey },
  });
  backendExit = new Promise(resolve => {
    backend.once("close", (code, signal) => resolve({ code, signal }));
  });
  backend.on("error", error => { backendLog += error.message; });
  for (const stream of [backend.stdout, backend.stderr]) stream.on("data", data => { backendLog += data.toString("utf8"); });
  await expect.poll(async () => {
    if (backend.exitCode !== null || backend.signalCode !== null) throw new Error("The isolated preview backend exited before readiness");
    try { return (await api("/health")).status; } catch { return 0; }
  }, { timeout: 90_000 }).toBe(200);

  const adminApp = await launch("admin", proxy.address().port);
  const admin = adminApp.page;
  const adminLogin = await signIn(admin, "preview-admin");
  expect(adminLogin.user.role).toBe("admin");
  const roster = await checkApi("admin sees synthetic roster", "/api/v1/admin/members", adminLogin.headers, 200);
  const memberId = roster.find(member => member.name === account)?.id;
  expect(memberId).toBeGreaterThan(0);
  for (const [title, assignee_id] of [[ownTitle, memberId], [adminTitle, adminLogin.user.staff_id]]) {
    const response = await api("/api/v1/tasks", { method: "POST",
      headers: { "X-SignalDesk-Key": serviceKey, "Content-Type": "application/json" },
      body: JSON.stringify({ title, assignee_id, remind_enabled: false }) });
    expect(response.status).toBe(201);
  }
  const adminTasks = await checkApi("admin list includes two owners", "/api/v1/tasks", adminLogin.headers, 200);
  expect(adminTasks.items.map(task => task.assignee_id).sort()).toEqual([memberId, adminLogin.user.staff_id].sort());
  const adminTaskId = adminTasks.items.find(task => task.title === adminTitle).id;
  await navButton(admin, "任务中心").click();
  await expect(admin.getByText(ownTitle, { exact: true })).toBeVisible();
  await expect(admin.getByText(adminTitle, { exact: true })).toBeVisible();
  await expect(button(admin, "按人员")).toBeVisible();
  await screenshot(admin, "01-admin-two-task-owners");
  recordStep("管理员在实际桌面任务列表可见两名负责人及人员分组入口");

  await button(admin, "用户管理").click();
  await editGrant(admin, ["任务中心"], "02-admin-edits-member-grants");
  recordStep("管理员在桌面用户管理中把普通成员设置为仅任务中心");

  const memberApp = await launch("member", proxy.address().port);
  expect(memberApp.pid).not.toBe(adminApp.pid);
  const member = memberApp.page;
  const memberLogin = await signIn(member, "preview-member");
  expect(memberLogin.user.allowed_pages).toEqual(["tasks"]);
  await expect(navButton(member, "任务中心")).toBeVisible();
  await expect(button(member, "我的任务")).toBeVisible();
  await expect(member.getByText(ownTitle, { exact: true })).toBeVisible();
  await expect(member.getByText(adminTitle, { exact: true })).toHaveCount(0);
  for (const label of ["今日", "全部信息", "智能频道", "知识库", "内容生成", "收藏", "回收站"]) {
    await expect(navButton(member, label)).toHaveCount(0);
  }
  await expect(navigation(member).getByRole("link", { name: "内部管理系统" })).toHaveCount(0);
  for (const label of ["设置", "用户管理", "按人员"]) await expect(button(member, label)).toHaveCount(0);
  await expect(member.getByLabel("负责人筛选")).toHaveCount(0);
  await member.keyboard.press("Control+n");
  await expect(member.getByRole("dialog")).toHaveCount(0);
  await screenshot(member, "03-member-own-task-restricted-sidebar");
  const memberTasks = await checkApi("unfiltered member tasks contain only own task", "/api/v1/tasks", memberLogin.headers, 200);
  expect(memberTasks.items.map(task => [task.title, task.assignee_id])).toEqual([[ownTitle, memberId]]);
  await checkApi("member cannot read another owner's task", `/api/v1/tasks/${adminTaskId}`, memberLogin.headers, 404);
  for (const [label, endpoint] of Object.entries({ knowledge: "/api/v1/knowledge/capabilities", content: "/api/v1/content/schema",
    video: "/api/v1/video/projects/capabilities", channels: "/api/v1/channels", articles: "/api/v1/articles", roster: "/api/v1/admin/members" })) {
    await checkApi(`tasks-only member denied ${label}`, endpoint, memberLogin.headers, 403);
  }
  recordStep("普通成员仅见本人任务及获准页面，后台拒绝跨负责人详情和受限接口");

  await editGrant(admin, ["知识库"], "04-admin-switches-live-grant");
  const changed = await checkApi("same live session sees changed grants", "/api/v1/auth/me", memberLogin.headers, 200);
  expect(changed.allowed_pages).toEqual(["knowledge"]);
  await checkApi("revoked tasks blocked immediately by API", "/api/v1/tasks", memberLogin.headers, 403);
  await checkApi("newly granted knowledge accessible by API", "/api/v1/knowledge/capabilities", memberLogin.headers, 200);
  await expect(navButton(member, "知识库")).toBeVisible({ timeout: 45_000 });
  await expect(navButton(member, "任务中心")).toHaveCount(0);
  await expect(button(member, "桌面挂件")).toHaveCount(0);
  await expect(member.getByText(ownTitle, { exact: true })).toHaveCount(0);
  await expect(member.getByRole("heading", { name: "知识库", exact: true })).toBeVisible();
  await screenshot(member, "05-member-live-refresh-knowledge-only");
  recordStep("同一成员会话即时收到后端新权限，已打开桌面自动移除任务并切到知识库");

  await editGrant(admin, []);
  await expect(member.getByText("暂无可访问的工作页面", { exact: true })).toBeVisible({ timeout: 45_000 });
  await expect(navButton(member, "知识库")).toHaveCount(0);
  await checkApi("empty grant rejects knowledge", "/api/v1/knowledge/capabilities", memberLogin.headers, 403);
  await screenshot(member, "06-member-no-business-pages");
  await button(member, "退出登录").click();
  await expect(member.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  await checkApi("logout revokes same bearer", "/api/v1/auth/me", memberLogin.headers, 401);
  recordStep("空授权保留退出入口，退出后原会话失效");
  expect(pageErrors).toEqual([]);
  expect(unexpectedOrigins).toEqual([]);
} catch (error) {
  failure = error;
  for (const [label, page] of pages) await page.screenshot({ path: path.join(run, `failure-${label}.png`), timeout: 5_000 }).catch(() => undefined);
} finally {
  const closeResults = await Promise.allSettled(apps.map(app => app.close()));
  const ownedElectronClosed = closeResults.every(result => result.status === "fulfilled");
  if (backend?.exitCode === null) backend.kill();
  const backendClosed = backendExit ? await backendExit : null;
  const backendListenerClosed = !backend || await api("/health").then(() => false, () => true);
  for (const socket of sockets.filter(socket => socket.listening)) await new Promise(resolve => socket.close(resolve));
  if (!ownedElectronClosed && !failure) failure = new Error("An owned Electron instance did not close");
  if (!backendListenerClosed && !failure) failure = new Error("The isolated preview backend is still listening");
  const unchangedFiles = JSON.stringify(await trackedHashes()) === JSON.stringify(loadedFiles);
  if (!unchangedFiles && !failure) failure = new Error("A loaded application file changed during acceptance");
  await writeFile(path.join(run, "backend.log"), backendLog, "utf8");
  await writeFile(path.join(run, "result.json"), JSON.stringify({ passed: !failure,
    backend: "isolated localhost SQLite", renderer: "Electron development executable + built dist", build, loadedFiles, unchangedFiles,
    steps, apiChecks, screenshots, pageErrors, unexpectedOrigins, ownedElectronClosed, backendClosed, backendListenerClosed,
    error: failure?.message ?? null,
  }, null, 2) + "\n");
  console.log(`Permission acceptance evidence: ${run}`);
}

if (failure) throw failure;
console.log(`Permission acceptance passed: ${steps.length} Electron scenarios and ${apiChecks.length} API assertions.`);
