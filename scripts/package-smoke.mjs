import { _electron as electron } from "@playwright/test";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const profile = await mkdtemp(path.join(tmpdir(), "signaldesk-package-smoke-"));
const artifacts = path.join(root, "artifacts");
const packageDirectory = process.env.SIGNALDESK_PACKAGE_DIR || path.join(root, "release", "win-unpacked");
let electronApp;

try {
  await mkdir(artifacts, { recursive: true });
  electronApp = await electron.launch({
    executablePath: path.join(packageDirectory, "迈医协同.exe"),
    env: { ...process.env, SIGNALDESK_E2E_USER_DATA: profile },
  });
  const page = await electronApp.firstWindow();
  const nativeWindow = await electronApp.browserWindow(page);
  await page.keyboard.press(process.platform === "darwin" ? "Meta+-" : "Control+-");
  await page.waitForTimeout(100);
  const zoomFactor = await nativeWindow.evaluate((window) => window.webContents.getZoomFactor());
  if (zoomFactor !== 1) throw new Error(`页面缩放未锁定在 100%：${zoomFactor}`);
  await page.getByRole("heading", { name: "登录工作台" }).waitFor({ timeout: 15_000 });
  await page.screenshot({ path: path.join(artifacts, "packaged-app.png") });
  await page.setViewportSize({ width: 1080, height: 700 });
  await page.screenshot({ path: path.join(artifacts, "packaged-app-1080.png") });
  if ((await page.title()) !== "迈医协同") throw new Error("打包应用标题不正确");
} finally {
  if (electronApp) await electronApp.close();
  await rm(profile, { recursive: true, force: true });
}
