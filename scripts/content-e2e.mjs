import { chromium, expect } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { randomUUID } from "node:crypto";

const root = process.cwd();
const artifacts = path.resolve(root, "artifacts/content");
await mkdir(artifacts, { recursive: true });
const python = process.env.CONTENT_TEST_PYTHON || path.resolve(root, "../.venv-server312/Scripts/python.exe");
const db = path.join(artifacts, `e2e-${randomUUID()}.db`).replaceAll("\\", "/");
const backend = spawn(python, ["server/tests/content_preview.py"], { cwd: root, windowsHide: true, env: { ...process.env, TASK_DATABASE_URL: `sqlite:///${db}`, TASK_API_KEY: "content-preview-service", PYTHONIOENCODING: "utf-8" }, stdio: ["ignore", "pipe", "pipe"] });
const vite = spawn(process.execPath, ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "15173"], { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
let serverErrors = "";
backend.stderr.on("data", (data) => { serverErrors += data; });
vite.stderr.on("data", (data) => { serverErrors += data; });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitServer(url) {
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(url); if (r.status < 500) return; } catch { /* 等待本地服务启动。 */ }
    await pause(250);
  }
  throw new Error(`本地测试服务未启动：${serverErrors}`);
}
let browser;
let page;
const errors = [];
try {
  await Promise.all([waitServer("http://127.0.0.1:18777/health"), waitServer("http://127.0.0.1:15173/")]);
  await writeFile(path.join(artifacts, "preview.html"), `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><title>内容创作测试预览</title></head><body><div id="root"></div><script type="module">
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { ContentGenerationView } from '/src/components/ContentGenerationView.tsx';
    import '/src/styles.css';
    const response = await fetch('http://127.0.0.1:18777/api/v1/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:'content-preview',password:'content-preview-only'}) });
    const session = await response.json();
    createRoot(document.getElementById('root')).render(React.createElement(ContentGenerationView, {settings:{ backendUrl:'http://127.0.0.1:18777', backendAuthToken:session.access_token, operatorName:'测试审核员' }}));
  </script></body></html>`, "utf8");
  browser = await chromium.launch({ channel: "chrome", headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("http://127.0.0.1:15173/artifacts/content/preview.html");
  await page.getByRole("heading", { name: "这期，想把什么问题讲清楚？" }).waitFor();
  await page.screenshot({ path: path.join(artifacts, "new-desktop.png") });
  await page.setViewportSize({ width: 1080, height: 760 });
  await page.screenshot({ path: path.join(artifacts, "new-compact.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel("本期主题", { exact: false }).fill("家庭讨论前，先把问题列清楚");
  await page.getByRole("button", { name: "生成选题建议", exact: true }).click();
  await expect(page.getByRole("button", { name: "通过并进入下一步" })).toBeEnabled();
  await expect(page.getByLabel("选题表述", { exact: false })).toHaveValue("家庭讨论前，先把问题列清楚");
  await page.screenshot({ path: path.join(artifacts, "step1-desktop.png") });
  await page.setViewportSize({ width: 1080, height: 760 });
  await page.screenshot({ path: path.join(artifacts, "step1-compact.png") });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "通过并进入下一步" }).click();
  await expect(page.getByLabel("一句话核心观点", { exact: false })).toHaveValue("先把问题列清楚，再讨论怎样选择。");
  await expect(page.getByRole("button", { name: "通过并进入下一步" })).toBeEnabled();
  await page.getByRole("button", { name: "通过并进入下一步" }).click();

  async function verify() {
    await expect(page.getByLabel("人工核查结论", { exact: false }).first()).toBeVisible();
    const types = page.getByLabel("表述性质", { exact: false });
    for (let i = 0; i < await types.count(); i++) {
      await types.nth(i).selectOption("价值判断");
      await page.getByLabel("适用对象与限制", { exact: false }).nth(i).fill("日常家庭沟通");
      await page.getByLabel("人工核查结论", { exact: false }).nth(i).selectOption("不适用");
      await page.getByLabel("核查意见", { exact: true }).nth(i).fill("价值建议，不含外部事实");
    }
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(page.getByRole("button", { name: "通过并进入下一步" })).toBeEnabled();
    await page.getByRole("button", { name: "通过并进入下一步" }).click();
  }
  await verify();
  await expect(page.getByLabel("开头方案", { exact: false })).toHaveValue("每个人想解决的问题，可能并不一样。");
  await expect(page.getByRole("button", { name: "通过并进入下一步" })).toBeEnabled();
  await page.getByRole("button", { name: "通过并进入下一步" }).click();
  await expect(page.getByRole("textbox", { name: "正文 / 完整脚本", exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(artifacts, "draft-desktop.png") });
  await page.getByRole("button", { name: /3.*资料与专业核查/ }).click();
  await verify();
  await page.getByLabel("已人工检查立场", { exact: false }).check();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("button", { name: "通过并进入下一步" })).toBeEnabled();
  await page.getByRole("button", { name: "通过并进入下一步" }).click();
  await expect(page.getByLabel("最终标题", { exact: false })).toHaveValue("家庭讨论前，先把问题列清楚");
  await page.getByRole("button", { name: /3.*资料与专业核查/ }).click();
  await verify();
  await page.getByLabel("已审核本平台标题", { exact: false }).check();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认定稿", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "确认定稿", exact: true }).click();
  await expect(page.getByRole("button", { name: "导出文案", exact: true })).toBeEnabled();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出文案", exact: true }).click();
  const download = await downloaded;
  await download.saveAs(path.join(artifacts, "verified-export.md"));
  await page.screenshot({ path: path.join(artifacts, "final-desktop.png") });
  await page.reload();
  await page.getByRole("button", { name: "内容档案", exact: true }).click();
  await expect(page.getByRole("button", { name: "家庭讨论前，先把问题列清楚", exact: true })).toBeVisible();
  if (errors.length) throw new Error(errors.join("\n"));
  console.log("内容创作端到端验证通过：主题 → 六步审核 → 回退核查 → 定稿导出 → 重开档案。");
} catch (error) {
  if (page) {
    console.error('页面错误：', errors.join('\n'));
    console.error('页面当前内容：', (await page.locator('body').innerText()).slice(0, 2000));
  }
  throw error;
} finally {
  await browser?.close();
  backend.kill(); vite.kill();
}
