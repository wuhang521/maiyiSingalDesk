import { _electron as electron, expect } from "@playwright/test";
import { execFile, spawn } from "node:child_process";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { promisify } from "node:util";

const root = process.cwd();
const evidenceRoot = path.join(root, "artifacts", "wechat-pairing-20261112");
await mkdir(evidenceRoot, { recursive: true });
const run = await mkdtemp(path.join(evidenceRoot, "native-"));
const listener = createServer();
await new Promise((resolve, reject) => { listener.once("error", reject); listener.listen(0, "127.0.0.1", resolve); });
const base = `http://127.0.0.1:${listener.address().port}`;
await new Promise((resolve) => listener.close(resolve));

const python = process.env.MEMBER_TEST_PYTHON || path.resolve(root, "../.venv-signaldesk/Scripts/python.exe");
const serviceKey = "isolated-pairing-service";
const routeFile = path.join(run, "wechat-routes.json");
await writeFile(routeFile, JSON.stringify({ bindings: {}, routes: { default: {
  url: "http://127.0.0.1:9876/p/default/webhooks/signaldesk-channel-weixin-default", secret: "isolated-route-key",
} } }));
const backend = spawn(python, ["server/tests/member_preview.py", new URL(base).port], {
  cwd: root, windowsHide: true, stdio: ["ignore", "ignore", "pipe"],
  env: { ...process.env, PYTHONIOENCODING: "utf-8", TASK_API_KEY: serviceKey,
    TASK_DATABASE_URL: `sqlite:///${path.join(run, "member-e2e.db").replaceAll("\\", "/")}`,
    TASK_HERMES_WEBHOOK_URL: "", TASK_PROMPT_API_KEY: "", TASK_CHANNEL_PUSH_ENABLED: "false",
    TASK_CHANNEL_WECHAT_ROUTES_FILE: routeFile, TASK_WECHAT_PAIRING_PUBLIC_BASE_URL: base },
});
let startupError = "";
backend.stderr.on("data", (chunk) => { startupError += chunk; });
backend.on("error", (error) => { startupError += error.message; });

const pluginSimulation = `
import asyncio, importlib, os, sys
from types import SimpleNamespace
sys.path.insert(0, 'hermes-plugin')
plugin = importlib.import_module('wechat-pairing')
peer = 'isolated-scan-identity@im.wechat'
source = SimpleNamespace(platform='weixin', chat_type='dm', chat_id=peer, user_id=peer)
event = SimpleNamespace(source=source, text=os.environ['SIGNALDESK_E2E_PAIRING_COMMAND'])
gateway = SimpleNamespace(_primary_profile_name='default')
result = plugin._pre_gateway_dispatch(event=event, gateway=gateway)
print(asyncio.run(plugin._result_command(result['text'].split()[1])))
`;

let app;
try {
  await expect.poll(async () => {
    if (startupError || backend.exitCode !== null) throw new Error(startupError || "Fixture exited");
    try { return (await fetch(`${base}/health`)).status; } catch { return 0; }
  }, { timeout: 25_000 }).toBe(200);
  const env = { ...process.env, SIGNALDESK_E2E_USER_DATA: path.join(run, "profile"),
    SIGNALDESK_BACKEND_URL: base, SIGNALDESK_RELEASE_API_URL: `${base}/preview-release` };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.VITE_DEV_SERVER_URL;
  const launch = process.env.SIGNALDESK_PACKAGE_DIR
    ? { executablePath: path.join(process.env.SIGNALDESK_PACKAGE_DIR, "迈医协同.exe") }
    : { args: [root] };
  app = await electron.launch({ ...launch, env });
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) window.webContents.setBackgroundThrottling(false);
  });
  await page.route("**/*", (route) => {
    const url = route.request().url();
    return /^https?:/.test(url) && !url.startsWith(`${base}/`) ? route.abort() : route.continue();
  });
  await page.setViewportSize({ width: 1280, height: 840 });
  await page.getByLabel("账号", { exact: true }).fill("preview-admin");
  await page.getByLabel("密码", { exact: true }).fill("preview-only-password");
  await page.getByRole("button", { name: "继续", exact: true }).click();
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "用户管理", exact: true }).click();
  await page.getByRole("button", { name: "管理 本地普通成员", exact: true }).click();
  const channels = page.getByRole("region", { name: "消息接收渠道" });
  const scanButton = channels.getByRole("button", { name: "扫码绑定微信", exact: true });
  const dialog = page.getByRole("dialog", { name: "扫码绑定微信" });
  const creation = page.waitForResponse((response) => response.url().endsWith("/wechat-pairing") && response.request().method() === "POST");
  await scanButton.click();
  const created = await creation;
  expect(created.ok()).toBe(true);
  const { scan_url: scanUrl, id } = await created.json();
  await expect(dialog.getByRole("img", { name: "微信绑定二维码" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认绑定" })).toHaveCount(0);
  await page.screenshot({ path: path.join(run, "desktop-qr.png") });

  const mobileWindow = app.waitForEvent("window");
  await app.evaluate(({ BrowserWindow }, url) => {
    const window = new BrowserWindow({ width: 390, height: 700, show: true, webPreferences: { backgroundThrottling: false } });
    void window.loadURL(url);
  }, scanUrl);
  const mobile = await mobileWindow;
  mobile.on("pageerror", (error) => errors.push(error.message));
  const input = mobile.getByRole("textbox", { name: "一次性绑定指令" });
  await expect(input).toHaveValue(/^绑定迈医 [A-Za-z0-9_-]{32}$/);
  await expect(mobile).toHaveURL(`${base}/api/v1/wechat-pairing/scan`);
  await mobile.getByRole("button", { name: "复制绑定指令" }).click();
  await expect(mobile.getByRole("status")).toHaveText(/已复制|长按/);
  const command = await input.inputValue();
  await mobile.screenshot({ path: path.join(run, "mobile-scan-page.png"), mask: [input], maskColor: "#e7ede9" });
  await mobile.close();

  const received = await promisify(execFile)(python, ["-B", "-c", pluginSimulation], {
    cwd: root, windowsHide: true, timeout: 15_000,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", TASK_API_BASE_URL: base, TASK_API_KEY: serviceKey,
      SIGNALDESK_E2E_PAIRING_COMMAND: command },
  });
  expect(received.stdout).toContain("微信已验证，请回到迈医协同客户端确认绑定");
  await expect(dialog.getByText("微信已验证", { exact: true })).toBeVisible();
  await expect(channels.getByText("isolated-scan-identity@im.wechat", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: path.join(run, "desktop-confirm.png") });
  await dialog.getByRole("button", { name: "确认绑定" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "微信已绑定" })).toBeVisible();
  await expect(channels.getByText("isolated-scan-identity@im.wechat", { exact: true })).toBeVisible();

  await scanButton.click();
  await expect(dialog.getByRole("img", { name: "微信绑定二维码" })).toBeVisible();
  const cancelled = page.waitForResponse((response) => response.url().includes("/wechat-pairing/") && response.request().method() === "DELETE");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  const cancellation = await (await cancelled).json();
  expect(cancellation).toMatchObject({ state: "expired" });
  expect(cancellation.id).not.toBe(id);
  await expect(channels.getByText("isolated-scan-identity@im.wechat", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  await writeFile(path.join(run, "results.json"), JSON.stringify({ result: "passed", cases: [
    "WINDOWS_ADMIN_CREATES_QR", "MOBILE_PAGE_LOADS_COMMAND_AND_COPY_CONTROL", "HERMES_PLUGIN_CLAIMS_SYNTHETIC_DM_OVER_HTTP",
    "DESKTOP_REQUIRES_CONFIRMATION", "CONFIRM_REFRESHES_MEMBER_BINDING", "CANCEL_INVALIDATES_NEW_CODE",
  ] }, null, 2));
  console.log(`Windows Electron WeChat pairing passed. Evidence ${run}`);
} finally {
  if (app) await app.close();
  backend.kill();
}
