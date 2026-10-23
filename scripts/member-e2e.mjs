import { _electron as electron, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const artifacts = path.join(root, "artifacts", "members");
await mkdir(artifacts, { recursive: true });
const run = await mkdtemp(path.join(artifacts, "run-"));
const socket = createServer();
await new Promise((resolve, reject) => { socket.once("error", reject); socket.listen(0, "127.0.0.1", resolve); });
const port = socket.address().port;
await new Promise((resolve) => socket.close(resolve));
const base = `http://127.0.0.1:${port}`;
const python = process.env.MEMBER_TEST_PYTHON || path.resolve(root, "../.venv-signaldesk/Scripts/python.exe");
const backend = spawn(python, ["server/tests/member_preview.py", String(port)], {
  cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, PYTHONIOENCODING: "utf-8", TASK_DATABASE_URL: `sqlite:///${path.join(run, "member-e2e.db").replaceAll("\\", "/")}`, TASK_API_KEY: "preview-only-service", TASK_HERMES_WEBHOOK_URL: "", TASK_PROMPT_API_KEY: "" },
});
let startupError = "";
backend.stderr.on("data", (data) => { startupError += data; });
backend.on("error", (error) => { startupError += error.message; });
let app;
let page;
const pageErrors = [];
const outgoing = [];
const serviceHeaders = { "Content-Type": "application/json", "X-SignalDesk-Key": "preview-only-service" };
async function api(url, options = {}) {
  const response = await fetch(`${base}${url}`, options);
  const body = await response.json().catch(() => null);
  return { status: response.status, body };
}
async function signIn(username, password = "preview-only-password") {
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "继续", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
}
async function signOut() {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
}

try {
  await expect.poll(async () => {
    if (backend.exitCode !== null) throw new Error(startupError || "Local backend exited");
    try { return (await fetch(`${base}/health`)).status; } catch { return 0; }
  }, { timeout: 20000 }).toBe(200);
  const launchOptions = process.env.SIGNALDESK_PACKAGE_DIR
    ? { executablePath: path.join(process.env.SIGNALDESK_PACKAGE_DIR, "迈医协同.exe") }
    : { args: [root] };
  const launchEnv = { ...process.env, SIGNALDESK_E2E_USER_DATA: path.join(run, "profile"), SIGNALDESK_BACKEND_URL: base, SIGNALDESK_RELEASE_API_URL: `${base}/preview-release` };
  delete launchEnv.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ ...launchOptions, env: launchEnv });
  page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.setBackgroundThrottling(false);
      window.show();
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (/^https?:/.test(url) && !url.startsWith(`${base}/`)) { outgoing.push(url.split("?")[0]); return route.abort(); }
    return route.continue();
  });
  page.on("dialog", (dialog) => dialog.accept());
  await expect(page.getByRole("heading", { name: "登录工作台" })).toBeVisible();
  await signIn("preview-admin");
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await page.getByRole("button", { name: "用户管理", exact: true }).click();
  await expect(page.getByRole("row", { name: /本地测试管理员/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "移除 本地测试管理员" })).toBeDisabled();

  await page.getByRole("button", { name: "添加用户", exact: true }).click();
  await page.getByLabel("姓名或称呼").fill("新加入的测试同事");
  await page.getByLabel("登录账号", { exact: false }).fill("new-colleague");
  await page.getByLabel("初始密码").fill("test-new-password");
  await page.getByRole("button", { name: "确认添加" }).click();
  await expect(page.getByRole("status").filter({ hasText: "用户已添加" })).toBeVisible();
  await expect(page.getByRole("button", { name: "添加用户", exact: true })).toBeFocused();
  await expect(page.getByRole("row", { name: /新加入的测试同事/ })).toBeVisible();
  await page.getByRole("button", { name: "管理 新加入的测试同事" }).click();
  await page.getByRole("combobox", { name: /接收渠道/ }).selectOption("qq");
  await page.getByLabel("渠道接收 ID").fill("synthetic-qq-recipient");
  await page.getByRole("button", { name: "绑定渠道", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "消息渠道已绑定" })).toBeVisible();

  const personal = await api("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "new-colleague", password: "test-new-password" }) });
  expect(personal.status).toBe(200);
  const tokenHeaders = { Authorization: `Bearer ${personal.body.access_token}` };
  const staffId = personal.body.user.staff_id;
  const task = await api("/api/v1/tasks", { method: "POST", headers: serviceHeaders, body: JSON.stringify({ title: "需保留的历史测试任务", assignee_id: staffId, remind_enabled: false }) });
  expect(task.status).toBe(201);
  await page.getByRole("button", { name: "管理 新加入的测试同事" }).click();
  await page.getByLabel("启用该用户").uncheck();
  await page.getByRole("button", { name: "保存用户设置" }).click();
  await expect(page.getByRole("status").filter({ hasText: "用户设置已保存" })).toBeVisible();
  expect((await api("/api/v1/auth/me", { headers: tokenHeaders })).status).toBe(401);
  await expect(page.getByRole("row", { name: /新加入的测试同事/ })).toContainText("已停用");
  await page.getByRole("button", { name: "移除 新加入的测试同事" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已移除" })).toBeVisible();
  await expect(page.getByRole("row", { name: /新加入的测试同事/ })).toHaveCount(0);
  expect((await api(`/api/v1/tasks/${task.body.id}`, { headers: serviceHeaders })).body.title).toBe("需保留的历史测试任务");
  await page.getByLabel("人员状态").selectOption("removed");
  await page.getByRole("button", { name: "恢复用户", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "已恢复为停用状态" })).toBeVisible();
  await page.getByLabel("人员状态").selectOption("disabled");
  await page.getByRole("button", { name: "管理 新加入的测试同事" }).click();
  await page.getByLabel("启用该用户").check();
  await page.getByLabel("重置密码（选填）", { exact: true }).fill("new-reset-password");
  await page.getByRole("button", { name: "保存用户设置" }).click();
  await expect(page.getByRole("status").filter({ hasText: "用户设置已保存" })).toBeVisible();
  expect((await api("/api/v1/auth/me", { headers: tokenHeaders })).status).toBe(401);
  await page.getByLabel("人员状态").selectOption("current");
  await page.getByRole("button", { name: "管理 待开通账号的人员" }).click();
  await page.getByLabel("登录账号", { exact: false }).fill("existing-staff");
  await page.getByLabel("初始密码").fill("staff-password");
  await page.getByRole("button", { name: "保存用户设置" }).click();
  await expect(page.getByRole("status").filter({ hasText: "用户设置已保存" })).toBeVisible();
  await page.getByRole("button", { name: "管理 新加入的测试同事" }).click();
  for (const [width, height, name] of [[1440, 920, "desktop"], [1080, 700, "compact"]]) {
    await page.setViewportSize({ width, height });
    await page.getByRole("heading", { name: "用户管理", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifacts, `${name}.png`) });
    await page.getByRole("button", { name: "绑定渠道", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifacts, `${name}-channels.png`) });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
  }
  await page.getByLabel("渠道接收 ID").fill("synthetic-qq-recipient");
  await page.getByRole("button", { name: "绑定渠道", exact: true }).click();
  const channelError = page.getByRole("region", { name: "消息接收渠道" }).getByRole("alert");
  await expect(channelError).toContainText("已存在");
  await expect(channelError).toBeFocused();
  await expect(page.getByLabel("渠道接收 ID")).toHaveValue("synthetic-qq-recipient");
  await page.screenshot({ path: path.join(artifacts, "compact-channel-error.png") });
  await page.getByRole("button", { name: "关闭用户表单" }).click();
  await page.getByRole("heading", { name: "用户管理", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(artifacts, "compact-roster.png") });
  await signOut();
  await signIn("new-colleague", "new-reset-password");
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await expect(page.getByRole("button", { name: "用户管理", exact: true })).toHaveCount(0);
  await expect(page.getByText("需保留的历史测试任务", { exact: true })).toBeVisible();
  expect(pageErrors).toEqual([]);
  expect(outgoing).toEqual([]);
  await writeFile(path.join(artifacts, "result.json"), JSON.stringify({ passed: true, backend: "isolated SQLite", checks: ["admin-only entry", "add login", "bind channel", "disable revokes sessions", "remove preserves tasks", "restore disabled", "reset password", "grant existing staff", "member login and permission", "1440/1080 layout", "keyboard focus after save", "visible channel failure preserving input"] }, null, 2));
  console.log("用户管理 Electron E2E 通过：添加、绑定渠道、停用、撤销会话、移除保留历史、恢复、密码重置、已有人员开通、普通成员权限、两种桌面尺寸。");
} catch (error) {
  if (page) await page.screenshot({ path: path.join(artifacts, "failure.png"), timeout: 10000 }).catch(() => undefined);
  console.error("本地测试后端诊断：", startupError.slice(-2000));
  throw error;
} finally {
  await app?.close();
  backend.kill();
}
