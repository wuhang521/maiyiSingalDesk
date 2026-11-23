import { _electron as electron, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = process.env.SIGNALDESK_SOURCE_DIR || root;
const evidence = process.env.SIGNALDESK_E2E_EVIDENCE || path.join(root, "docs/delivery/admin-member-subscriptions-20261120");
const artifacts = process.env.SIGNALDESK_E2E_ARTIFACTS || path.join(root, "artifacts/admin-member-subscriptions-20261120");
await mkdir(evidence, { recursive: true });
await mkdir(artifacts, { recursive: true });
const run = await mkdtemp(path.join(artifacts, "run-"));
const socket = createServer();
await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
const port = socket.address().port;
await new Promise((resolve) => socket.close(resolve));
const base = `http://127.0.0.1:${port}`;
const backend = spawn(path.resolve(root, "../.venv-signaldesk/Scripts/python.exe"), ["-X", "utf8", "server/tests/member_preview.py", String(port)], {
  cwd: source, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, TASK_DATABASE_URL: `sqlite:///${path.join(run, "member-e2e.db").replaceAll("\\", "/")}`,
    TASK_API_KEY: "preview-only-service", TASK_HERMES_WEBHOOK_URL: "", TASK_PROMPT_API_KEY: "" },
});
let backendErrors = "";
backend.stderr.on("data", (value) => { backendErrors += value; });
let app;
const pageErrors = [];
const external = [];
const checks = [];

async function request(url, { token, method = "GET", body } = {}) {
  const response = await fetch(base + url, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: response.status, body: await response.json().catch(() => null) };
}

try {
  await expect.poll(async () => {
    if (backend.exitCode !== null) throw new Error(backendErrors);
    try { return (await fetch(base + "/health")).status; } catch { return 0; }
  }, { timeout: 30000 }).toBe(200);
  const admin = await request("/api/v1/auth/login", { method: "POST", body: { username: "preview-admin", password: "preview-only-password" } });
  const member = await request("/api/v1/auth/login", { method: "POST", body: { username: "preview-member", password: "preview-only-password" } });
  expect(admin.status).toBe(200); expect(member.status).toBe(200);
  const adminToken = admin.body.access_token;
  const memberToken = member.body.access_token;
  const staffId = member.body.user.staff_id;
  const binding = await request(`/api/v1/staff/${staffId}/channels`, { token: adminToken, method: "POST", body: { platform: "weixin", recipient_id: "synthetic-member-wechat", enabled: true } });
  expect(binding.status).toBe(200);
  const channels = {};
  for (const [key, name, enabled] of [["ai", "AI 决策雷达", true], ["medical", "医疗前沿频道", true], ["restricted", "内部频道", true], ["paused", "暂停频道", false]]) {
    const result = await request("/api/v1/channel-subscriptions/configs", { token: adminToken, method: "POST",
      body: { name, description: "本地验收演示数据", enabled, schedule: "09:00", max_items: 20, source_type: "articles", include_terms: [], exclude_terms: [] } });
    expect(result.status).toBe(200); channels[key] = result.body.id;
  }
  expect((await request(`/api/v1/channel-subscriptions/configs/${channels.restricted}/audience`, { token: adminToken, method: "PUT",
    body: { previous_user_ids: null, allowed_user_ids: [] } })).status).toBe(200);

  const env = { ...process.env, SIGNALDESK_E2E_USER_DATA: path.join(run, "profile"), SIGNALDESK_BACKEND_URL: base,
    SIGNALDESK_RELEASE_API_URL: base + "/preview-release" };
  delete env.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ ...(process.env.SIGNALDESK_PACKAGE_EXE
    ? { executablePath: process.env.SIGNALDESK_PACKAGE_EXE } : { args: [source] }), env });
  const page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.setBackgroundThrottling(false);
      window.show();
    }
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (/^https?:/.test(url) && !url.startsWith(base + "/")) { external.push(url.split("?")[0]); return route.abort(); }
    return route.continue();
  });
  await page.getByLabel("账号", { exact: true }).fill("preview-admin");
  await page.getByLabel("密码", { exact: true }).fill("preview-only-password");
  await page.getByRole("button", { name: "继续", exact: true }).click();
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "用户管理", exact: true }).click();
  await page.getByRole("button", { name: "管理 本地普通成员", exact: true }).click();
  await page.getByRole("button", { name: "频道订阅", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "为本地普通成员设置频道订阅" });
  await expect(dialog.getByRole("checkbox", { name: /AI 决策雷达/ })).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: /内部频道/ })).toBeDisabled();
  await expect(dialog.getByRole("checkbox", { name: /暂停频道/ })).toBeDisabled();
  checks.push("用户管理按成员入口及真实权限禁用原因");

  await page.evaluate(() => {
    const note = document.querySelector(".member-subscriptions-intro");
    note.textContent = "交互原型（演示数据）。" + note.textContent;
  });
  await app.evaluate(({ BrowserWindow }) => { const window = BrowserWindow.getAllWindows()[0]; window.setSize(1480, 960); window.webContents.setBackgroundThrottling(false); });
  const dialogBounds = await dialog.boundingBox();
  const saveBounds = await dialog.getByRole("button", { name: "保存频道订阅" }).boundingBox();
  expect(saveBounds.y + saveBounds.height, "保存操作必须完整显示在弹窗内").toBeLessThanOrEqual(dialogBounds.y + dialogBounds.height);
  await page.screenshot({ path: path.join(evidence, "prototype-desktop.png") });
  const html = await page.evaluate(() => {
    const copy = document.documentElement.cloneNode(true);
    const css = Array.from(document.styleSheets).flatMap((sheet) => Array.from(sheet.cssRules, (rule) => rule.cssText)).join("\n");
    copy.querySelectorAll("script, link, meta[http-equiv='Content-Security-Policy']").forEach((node) => node.remove());
    const style = document.createElement("style"); style.textContent = css; copy.querySelector("head").append(style);
    const preview = document.createElement("script");
    preview.textContent = `const dialog=document.querySelector('dialog');dialog.removeAttribute('open');dialog.showModal();dialog.querySelector('form').onsubmit=e=>{e.preventDefault();dialog.querySelector('.member-subscriptions-intro').textContent='演示保存成功。正式功能将保存到所选成员的真实订阅。'};dialog.querySelectorAll('button[type=button]').forEach(b=>b.onclick=()=>dialog.close());`;
    copy.querySelector("body").append(preview);
    return "<!doctype html>\n" + copy.outerHTML;
  });
  await writeFile(path.join(evidence, "prototype.html"), html);
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1040, 800));
  await page.screenshot({ path: path.join(evidence, "prototype-compact.png") });
  const bounds = await dialog.boundingBox();
  expect(bounds.width).toBeLessThanOrEqual(1040); expect(bounds.height).toBeLessThanOrEqual(800);
  await dialog.getByRole("checkbox", { name: /AI 决策雷达/ }).check();
  await dialog.getByRole("checkbox", { name: /医疗前沿频道/ }).check();
  await dialog.getByRole("button", { name: "保存频道订阅" }).click();
  await expect(dialog).not.toBeVisible();
  let catalog = await request("/api/v1/channel-subscriptions/catalog", { token: memberToken });
  expect(catalog.body.channels.filter((row) => row.subscribed).map((row) => row.id).sort()).toEqual([channels.ai, channels.medical].sort());
  const adminCatalog = await request("/api/v1/channel-subscriptions/catalog", { token: adminToken });
  expect(adminCatalog.body.channels.some((row) => row.subscribed)).toBe(false);
  checks.push("管理员保存后成员实际订阅直接生效，管理员本人未被代订阅");

  await page.getByRole("button", { name: "频道订阅", exact: true }).click();
  await expect(dialog.getByRole("checkbox", { name: /AI 决策雷达/ })).toBeChecked();
  await dialog.getByRole("checkbox", { name: /AI 决策雷达/ }).uncheck();
  await dialog.getByRole("button", { name: "保存频道订阅" }).click();
  await expect(dialog).not.toBeVisible();
  catalog = await request("/api/v1/channel-subscriptions/catalog", { token: memberToken });
  expect(catalog.body.channels.filter((row) => row.subscribed).map((row) => row.id)).toEqual([channels.medical]);
  checks.push("重新打开读回保存状态，取消后个人目录立即撤销");
  const state = await request(`/api/v1/channel-subscriptions/members/${staffId}`, { token: adminToken });
  expect((await request(`/api/v1/channel-subscriptions/members/${staffId}`, { token: memberToken, method: "PUT",
    body: { previous_channel_ids: state.body.subscribed_channel_ids, channel_ids: [], binding_revision: state.body.binding_revision } })).status).toBe(403);
  checks.push("普通成员真实登录越权请求返回 403");
  await page.goto(pathToFileURL(path.join(evidence, "prototype.html")).href);
  await expect(page.getByRole("checkbox", { name: /AI 决策雷达/ })).toBeVisible();
  await page.getByRole("checkbox", { name: /AI 决策雷达/ }).check();
  await page.getByRole("button", { name: "保存频道订阅" }).click();
  await expect(page.getByText("演示保存成功。正式功能将保存到所选成员的真实订阅。")).toBeVisible();
  checks.push("导出的独立 HTML 原型可直接打开并勾选演示保存");
  expect(pageErrors).toEqual([]); expect(external).toEqual([]);
  await writeFile(path.join(evidence, "electron-results.json"), JSON.stringify({ status: "PASS", run, checks, pageErrors, external,
    database: "isolated synthetic SQLite", productionWrites: 0, wechatMessagesSent: 0 }, null, 2));
  console.log(JSON.stringify({ status: "PASS", checks, evidence }));
} catch (error) {
  if (app) {
    const page = (await app.windows())[0];
    if (page) {
      await page.screenshot({ path: path.join(evidence, "electron-failure.png"), timeout: 5000 }).catch(() => {});
      await writeFile(path.join(evidence, "electron-failure.html"), await page.content()).catch(() => {});
    }
  }
  await writeFile(path.join(evidence, "electron-failure.json"), JSON.stringify({ error: String(error), backendErrors, pageErrors, run }, null, 2));
  throw error;
} finally {
  if (app) await app.close();
  backend.kill();
}
