import { _electron as electron, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { mkdir, mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const artifacts = path.join(root, "artifacts", process.env.SIGNALDESK_PACKAGE_DIR ? "knowledge-ocr-package" : "knowledge-ocr");
await mkdir(artifacts, { recursive: true });
const run = await mkdtemp(path.join(artifacts, "run-"));
const server = createServer();
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const port = server.address().port;
await new Promise((resolve) => server.close(resolve));
const base = `http://127.0.0.1:${port}`;
const backend = spawn(process.env.KNOWLEDGE_TEST_PYTHON || path.resolve(root, "../.venv-signaldesk/Scripts/python.exe"), ["server/tests/knowledge_preview.py", String(port)], {
  cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONIOENCODING: "utf-8", TASK_DATABASE_URL: `sqlite:///${path.join(run, "knowledge-e2e.db").replaceAll("\\", "/")}`,
    TASK_API_KEY: "isolated-preview-service", TASK_KNOWLEDGE_STORAGE_DIR: path.join(run, "files"), TASK_KNOWLEDGE_ASR_ENABLED: "false", TASK_KNOWLEDGE_TRASH_RETENTION_DAYS: "0", TASK_KNOWLEDGE_GATEWAY_SECRET: "", TASK_HERMES_WEBHOOK_URL: "", TASK_PROMPT_API_KEY: "", TASK_KNOWLEDGE_CLOUD_QA_ENABLED: "false" },
});
let diagnostic = "";
backend.stderr.on("data", (data) => { diagnostic += data; });
backend.on("error", (error) => { diagnostic += error.message; });
let app, page;
const errors = [], outgoing = [];
const detail = () => page.getByRole("article", { name: "知识资料详情" });
const screenshotSizes = [[1440, 920, "desktop"], [1080, 700, "compact"], [2178, 1401, "user-2178"]];
async function capture(name) {
  if (!name.startsWith("ocr-")) return;
  for (const [width, height, size] of screenshotSizes) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => { for (const panel of document.querySelectorAll(".kb-collect,.kb-ask,.kb-detail,.kb-list")) panel.scrollTop = 0; });
    if (name === "history" || name === "purge") {
      await page.locator(name === "history" ? ".kb-history" : ".kb-purge").evaluate((panel) => panel.scrollIntoView({ block: "start" }));
    }
    if (name === "cloud-citations" || name === "ocr-citations") await page.locator(".kb-citations").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifacts, `${size}-${name}.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  }
}

async function reload() {
  await page.reload();
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.setBackgroundThrottling(false);
      window.show(); window.focus();
    }
  });
  await page.bringToFront();
}

try {
  await expect.poll(async () => {
    if (backend.exitCode !== null) throw new Error(diagnostic || "Preview exited");
    try { return (await fetch(`${base}/health`)).status; } catch { return 0; }
  }, { timeout: 20000 }).toBe(200);
  const launchEnv = { ...process.env, SIGNALDESK_E2E_USER_DATA: path.join(run, "profile"), SIGNALDESK_BACKEND_URL: base, SIGNALDESK_RELEASE_API_URL: `${base}/preview-release` };
  delete launchEnv.ELECTRON_RUN_AS_NODE;
  app = await electron.launch({ ...(process.env.SIGNALDESK_PACKAGE_DIR ? { executablePath: path.join(process.env.SIGNALDESK_PACKAGE_DIR, "迈医协同.exe") } : { args: [root] }), env: launchEnv });
  page = await app.firstWindow();
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.setBackgroundThrottling(false);
      window.show();
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (/^https?:/.test(url) && !url.startsWith(`${base}/`)) { outgoing.push(url.split("?")[0]); return route.abort(); }
    return route.continue();
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByLabel("账号", { exact: true }).fill("preview-kb");
  await page.getByLabel("密码", { exact: true }).fill("preview-only-password");
  await page.getByRole("button", { name: "继续", exact: true }).click();
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await expect(page.getByText("仅本人可见")).toBeVisible();
  await expect(detail().getByRole("heading", { name: "演示：待补充的视频号素材" })).toBeVisible();
  await expect(detail().getByText("还没有可搜索的正文")).toBeVisible();
  await capture("needs-input");

  await page.getByRole("button", { name: "收集资料", exact: true }).click();
  await page.getByLabel("资料标题（选填）").fill("演示：新收藏笔记");
  await page.getByLabel("正文 / 转写文字（选填）").fill("健康管理资料测试：引用必须能追溯到原始材料。此文仅为软件测试材料。");
  await page.getByLabel("标签（用中文或英文逗号分隔）").fill("健康管理，测试");
  await capture("collect");
  await page.getByRole("button", { name: "保存到知识库" }).click();
  await expect(detail().getByRole("heading", { name: "演示：新收藏笔记" })).toBeVisible();
  await expect(detail().getByText("健康管理资料测试：引用必须能追溯到原始材料。此文仅为软件测试材料。", { exact: true })).toBeVisible();
  await capture("ready");

  await page.getByRole("tab", { name: "知识问答" }).click();
  await page.getByLabel("问题或关键词").fill("健康管理");
  await page.getByRole("button", { name: "查找依据" }).click();
  await expect(page.getByText(/找到以下相关原文/)).toBeVisible();
  await expect(page.getByRole("button", { name: /演示：新收藏笔记.*版本 1/ })).toBeVisible();
  await capture("answer");
  await page.getByRole("button", { name: /演示：新收藏笔记.*版本 1/ }).click();
  await expect(detail().getByRole("heading", { name: "演示：新收藏笔记" })).toBeVisible();

  await page.getByRole("button", { name: "补充 / 修订正文" }).click();
  await page.getByLabel("正文 / 转写文字").fill("健康管理人工修订的测试资料，保留版本。");
  await page.getByRole("button", { name: "保存修订" }).click();
  await expect(detail().getByText("健康管理人工修订的测试资料，保留版本。", { exact: true })).toBeVisible();
  await detail().getByText("正文版本记录与对照", { exact: true }).click();
  const history = page.getByRole("region", { name: "历史正文对照" });
  await expect(history.getByRole("region", { name: "左侧版本 1 正文" })).toContainText("引用必须能追溯到原始材料");
  await expect(history.getByRole("region", { name: "右侧版本 2 正文" })).toContainText("健康管理人工修订");
  await capture("history");
  await page.getByRole("button", { name: "移入回收站" }).click();
  await expect(page.getByText("已移入知识回收站，不再参与检索。", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "知识回收站" }).click();
  await expect(detail().getByRole("button", { name: "恢复资料" })).toBeVisible();
  await capture("removed");
  await detail().getByRole("button", { name: "恢复资料" }).click();
  await page.getByRole("tab", { name: "全部资料" }).click();

  await page.getByLabel("上传知识文件").setInputFiles({ name: "测试字幕.srt", mimeType: "text/plain", buffer: Buffer.from("1\n00:00:01,000 --> 00:00:03,000\n健康管理字幕测试材料。\n", "utf8") });
  await expect(detail().getByRole("heading", { name: "测试字幕.srt" })).toBeVisible();
  await expect(detail().getByText("[00:00:01,000 --> 00:00:03,000] 健康管理字幕测试材料。", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(detail().getByRole("button", { name: /测试字幕.srt.*下载/ })).toBeVisible();
  const savedPath = path.join(run, "downloaded.srt");
  await app.evaluate(({ dialog }, target) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: target }); }, savedPath);
  await detail().getByRole("button", { name: /测试字幕.srt.*下载/ }).click();
  await expect.poll(async () => { try { return await readFile(savedPath, "utf8"); } catch { return ""; } }).toBe("1\n00:00:01,000 --> 00:00:03,000\n健康管理字幕测试材料。\n");
  await capture("subtitle");

  await page.getByRole("tab", { name: "待处理" }).click();
  await expect(detail().getByRole("heading", { name: "演示：待补充的视频号素材" })).toBeVisible();
  await page.getByLabel("补充知识文件").setInputFiles({ name: "preview.mp4", mimeType: "video/mp4", buffer: Buffer.from("not-real-media; ASR disabled") });
  await expect(detail().getByRole("status")).toContainText("尚未启用语音识别", { timeout: 15000 });
  await page.getByRole("tab", { name: "全部资料" }).click();
  await page.getByLabel("搜索知识资料").fill("字幕");
  await expect(detail().getByRole("heading", { name: "测试字幕.srt" })).toBeVisible();
  await expect(page.getByRole("button", { name: /演示：健康管理笔记/ })).toHaveCount(0);
  await detail().getByRole("button", { name: "移入回收站" }).click();
  await page.getByRole("tab", { name: "知识回收站" }).click();
  await expect(detail().getByRole("heading", { name: "测试字幕.srt" })).toBeVisible();
  await detail().getByRole("button", { name: "永久删除…" }).click();
  const purge = page.getByRole("form", { name: "永久删除资料" });
  await expect(purge.getByRole("button", { name: "确认永久删除" })).toBeDisabled();
  await purge.getByLabel("输入“永久删除”确认").fill("永久删除");
  await capture("purge");
  await purge.getByRole("button", { name: "取消永久删除" }).click();
  await expect(detail().getByRole("heading", { name: "测试字幕.srt" })).toBeVisible();
  await detail().getByRole("button", { name: "永久删除…" }).click();
  await purge.getByLabel("输入“永久删除”确认").fill("永久删除");
  await purge.getByRole("button", { name: "确认永久删除" }).click();
  await expect(page.getByRole("status").filter({ hasText: "已永久删除" })).toBeVisible();
  await expect(detail().getByRole("heading", { name: "测试字幕.srt" })).toHaveCount(0);
  await expect.poll(async () => (await readdir(path.join(run, "files"))).filter((name) => name.endsWith(".srt")).length,
    { timeout: 15000 }).toBe(0);
  await page.getByRole("tab", { name: "知识问答" }).click();
  const cloud = page.getByRole("checkbox", { name: "使用云端 AI 综合（可选）" });
  await expect(cloud).toBeDisabled();
  await capture("cloud-unconfigured");
  await fetch(`${base}/preview-cloud/mode`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ failure: false }) });
  await reload();
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await page.getByRole("tab", { name: "知识问答" }).click();
  await expect(cloud).toBeEnabled();
  await expect(cloud).not.toBeChecked();
  await expect(page.getByText(/向DeepSeek发送本次问题及最多 5 段/)).toBeVisible();
  await page.getByLabel("问题或关键词").fill("健康管理");
  await capture("cloud-choice");
  expect((await (await fetch(`${base}/preview-cloud/calls`)).json()).calls).toBe(0);
  await cloud.check();
  await page.getByRole("button", { name: "发送片段并综合" }).click();
  await expect(page.getByRole("heading", { name: "AI 综合 · 请核对出处" })).toBeVisible();
  await expect(page.getByText(/软件测试模拟综合/)).toBeVisible();
  await capture("cloud-answer");
  await capture("cloud-citations");
  expect((await (await fetch(`${base}/preview-cloud/calls`)).json()).calls).toBe(1);
  await fetch(`${base}/preview-cloud/mode`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ failure: true }) });
  await page.getByRole("button", { name: "发送片段并综合" }).click();
  await expect(page.getByRole("alert")).toContainText("未自动重试");
  await expect(page.getByLabel("问题或关键词")).toHaveValue("健康管理");
  await capture("cloud-error");
  expect((await (await fetch(`${base}/preview-cloud/calls`)).json()).calls).toBe(2);
  await page.getByRole("button", { name: "改用原文摘录" }).click();
  await expect(cloud).not.toBeChecked();
  await page.getByRole("button", { name: "查找依据" }).click();
  await expect(page.getByRole("heading", { name: "资料摘录", exact: true })).toBeVisible();
  expect((await (await fetch(`${base}/preview-cloud/calls`)).json()).calls).toBe(2);
  await page.getByRole("tab", { name: "全部资料" }).click();
  await page.getByLabel("上传知识文件").setInputFiles({ name: "演示截图.png", mimeType: "image/png", buffer: Buffer.from("89504e470d0a1a0a73796e746865746963", "hex") });
  await expect(detail().getByRole("heading", { name: "演示截图.png", exact: true })).toBeVisible();
  await expect(detail().getByRole("checkbox", { name: "同意发送选中的整份原件到云端识别" })).toBeDisabled();
  await expect(detail().getByRole("status")).toContainText("图片原件已保存", { timeout: 10000 });
  await capture("ocr-unconfigured");
  await fetch(`${base}/preview-ocr/mode`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ failure: false }) });
  await reload();
  await page.getByRole("button", { name: "知识库", exact: true }).click();
  await page.getByLabel("搜索知识资料").fill("演示截图");
  const consent = detail().getByRole("checkbox", { name: "同意发送选中的整份原件到云端识别" });
  await expect(consent).toBeEnabled();
  await expect(page.locator(".kb-list-item")).toHaveCount(1);
  await expect(consent).not.toBeChecked();
  await expect(detail().getByRole("button", { name: "发送文件并识别" })).toBeDisabled();
  await capture("ocr-consent");
  await consent.check();
  await detail().getByRole("button", { name: "发送文件并识别" }).click();
  await expect(detail().getByRole("heading", { name: "AI 识别正文 · 待核对（暂不参与检索）" })).toBeVisible({ timeout: 15000 });
  await expect(detail().getByText(/软件测试模拟 OCR/, { exact: false })).toBeVisible();
  await capture("ocr-draft");
  await page.getByRole("tab", { name: "知识问答" }).click();
  await page.getByLabel("问题或关键词").fill("截图归档");
  await page.getByRole("button", { name: "查找依据" }).click();
  await expect(page.getByText(/没有找到足够依据/)).toBeVisible();
  await page.getByRole("tab", { name: "全部资料" }).click();
  await page.getByLabel("搜索知识资料").fill("演示截图");
  await detail().getByRole("button", { name: "已核对，入库" }).click();
  await expect(detail().getByRole("heading", { name: "已提取正文" })).toBeVisible();
  await expect(page.locator(".kb-list-item.selected").getByText("已入库", { exact: true })).toBeVisible();
  await capture("ocr-confirmed");
  await page.getByRole("tab", { name: "知识问答" }).click();
  await page.getByLabel("问题或关键词").fill("截图归档");
  await page.getByRole("button", { name: "查找依据" }).click();
  await expect(page.getByRole("button", { name: /演示截图.*第 1 页（AI OCR）/ })).toBeVisible();
  await capture("ocr-citations");
  await page.getByRole("button", { name: /演示截图.*第 1 页（AI OCR）/ }).click();
  await fetch(`${base}/preview-ocr/mode`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ failure: true }) });
  await detail().getByText("重新识别原件（可能再次计费）", { exact: true }).click();
  await consent.check();
  await detail().getByRole("button", { name: "发送文件并识别" }).click();
  await expect(detail().getByRole("status")).toContainText("未自动重试", { timeout: 15000 });
  await expect(consent).not.toBeChecked();
  await capture("ocr-error");
  expect((await (await fetch(`${base}/preview-ocr/calls`)).json()).calls).toBe(2);
  expect(errors).toEqual([]); expect(outgoing).toEqual([]);
  await writeFile(path.join(artifacts, "result.json"), JSON.stringify({ passed: true, backend: "isolated SQLite + real parser worker + mock cloud HTTP transport (not real model)", packaged: !!process.env.SIGNALDESK_PACKAGE_DIR, sizes: screenshotSizes, checks: ["collection", "revision", "historical body comparison", "cited extractive answer", "delete/restore", "permanent delete confirmation and cancel", "real original file cleanup", "real subtitle parser", "private download", "ASR unconfigured state", "keyword filter", "cloud unconfigured", "explicit opt-in and disclosure", "mock cloud synthesis", "mock cloud failure without retry", "offline excerpt recovery", "no external HTTP", "no renderer errors"] }, null, 2));
  console.log("知识库 Electron E2E 通过：既有功能回归 + 模拟 OCR 授权、待核对隔离、确认入库、页码引用及失败不重复识别；三种桌面尺寸。");
} catch (error) {
  await writeFile(path.join(artifacts, "result.json"), JSON.stringify({ passed: false, packaged: !!process.env.SIGNALDESK_PACKAGE_DIR, sizes: screenshotSizes }, null, 2));
  if (page) await page.screenshot({ path: path.join(artifacts, "failure.png"), timeout: 10000 }).catch(() => undefined);
  console.error(diagnostic.slice(-2000));
  throw error;
} finally {
  await app?.close(); backend.kill();
}
