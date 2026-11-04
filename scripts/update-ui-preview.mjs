import { chromium, expect } from "@playwright/test";
import { build } from "esbuild";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";

const root = process.cwd();
const fixture = path.join(root, "artifacts/update-ui-preview");
const output = path.resolve(process.env.UPDATE_UI_PREVIEW_OUTPUT || "D:/maiyi/docs/delivery/maiyi-autoupdate-release-20261023/prototype");
const port = Number(process.env.UPDATE_UI_PREVIEW_PORT || 15176);
const keepServing = process.argv.includes("--serve");
const previewUrl = `http://127.0.0.1:${port}/artifacts/update-ui-preview/index.html`;
await Promise.all([mkdir(fixture, { recursive: true }), mkdir(output, { recursive: true })]);

await writeFile(path.join(fixture, "index.html"), `<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>迈医协同 · 软件更新交互预览（模拟主进程）</title>
<style>#root{height:calc(100% - 52px)!important}.preview-controls{height:52px;display:flex;align-items:center;gap:16px;padding:0 16px;background:var(--surface);border-top:1px solid var(--border);font-size:12px}.preview-controls label{display:flex;align-items:center;gap:6px}.preview-controls select{padding:5px;border:1px solid var(--border);border-radius:5px;background:var(--surface-soft)}.preview-controls output{color:var(--green-dark)}</style>
</head><body><div id="root"></div><div class="preview-controls" aria-label="模拟主进程控制台"><strong>模拟主进程 · 不下载、不安装、不登录</strong><label>预览状态<select id="preview-state" aria-label="预览状态"><option value="downloading">下载中 37%</option><option value="ready">已下载，等待重启</option><option value="error">更新失败</option><option value="idle">自动更新说明</option></select></label><output id="preview-action" aria-live="polite">尚未请求安装</output></div>
<script type="module">
import React, {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {SettingsView} from '/src/components/SettingsView.tsx';
import {Sidebar} from '/src/components/Sidebar.tsx';
import {Topbar} from '/src/components/Topbar.tsx';
import {ToastStack} from '/src/components/Toast.tsx';
import {defaultSettings} from '/src/data/demo.ts';
import logo from '/src/assets/mayi-logo.webp';
import '/src/styles.css';
localStorage.clear();
const originalFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const target = new URL(typeof input === 'string' ? input : input.url || input.toString(), location.href);
  if (target.origin !== location.origin) return Promise.reject(new Error('此预览禁止外部网络请求。'));
  return originalFetch(input, init);
};
const listeners = new Set();
const actions = [];
const common = {currentVersion:'0.6.0',latestVersion:'0.6.1'};
function makeState(name) {
  if (name === 'downloading') return {...common,status:'downloading',progress:37};
  if (name === 'ready') return {...common,status:'ready',progress:100};
  if (name === 'error') return {...common,status:'error',message:'网络连接中断，请重试。'};
  return {...common,status:'idle'};
}
let state = makeState(new URLSearchParams(location.search).get('state') || 'downloading');
const control = document.getElementById('preview-state');
const actionOutput = document.getElementById('preview-action');
control.value = state.status;
function record(action) { actions.push(action); actionOutput.textContent = action; }
function emit(next) { state = {...next}; for (const listener of listeners) listener({...state}); }
control.addEventListener('change', () => emit(makeState(control.value)));
window.updatePreview = {actions,setState(name){control.value=name;emit(makeState(name));},getState(){return {...state};}};
window.signalDesk = {
  getAppInfo:async()=>({version:'0.6.0',platform:'win32',launchAtLogin:false}),
  getUpdateState:async()=>({...state}),
  onUpdateState(callback){listeners.add(callback);return()=>listeners.delete(callback);},
  async checkForUpdates(){record('已模拟重试检查，恢复下载');emit({...common,status:'downloading',progress:37});return {...state};},
  async downloadUpdate(){record('已模拟开始下载');emit({...common,status:'downloading',progress:37});return {...state};},
  async installUpdate(){
    record('已请求模拟重启确认');
    const accepted = window.confirm('模拟确认：是否重启并安装？此预览不会下载、退出或安装任何程序。');
    if (!accepted) {record('已模拟取消，更新仍然就绪');return {started:false,reason:'已取消本次安装，可稍后重启安装。'};}
    record('已模拟确认安装，未启动任何安装程序');emit({...common,status:'installing'});return {started:true};
  },
  onMessage:()=>()=>{},onNavigate:()=>()=>{},subscribe:async()=>({subscriptionId:'preview-only'}),unsubscribe:async()=>true,
  setLaunchAtLogin:async(enabled)=>enabled,showNotification:async()=>false,pollMessages:async()=>[],
  openExternal:async()=>record('预览已拦截外部链接'),toggleTaskWidget:async()=>({visible:false,launchAtLogin:false}),
};
const h = React.createElement;
function Preview() {
  const [update,setUpdate] = useState(state);
  const [settings,setSettings] = useState({...defaultSettings,backendUrl:'',backendAuthToken:''});
  const [view,setView] = useState('settings');
  const [installing,setInstalling] = useState(false);
  const [toasts,setToasts] = useState([]);
  useEffect(()=>window.signalDesk.onUpdateState(setUpdate),[]);
  async function install() {
    setInstalling(true);
    try {const result=await window.signalDesk.installUpdate();if(!result.started&&result.reason)setToasts([{id:'cancelled',title:'更新尚未安装',message:result.reason,tone:'info'}]);}
    finally {setInstalling(false);}
  }
  return h('div',{className:'app-shell platform-win32'},
    h('div',{className:'window-drag-region','aria-hidden':true},h('img',{src:logo,alt:''}),h('span',null,'迈医协同')),
    h(Sidebar,{activeView:view,channels:[],unreadCount:0,savedCount:0,trashCount:0,statuses:{},onViewChange:setView,onChannelSelect:()=>{},onToggleTaskWidget:()=>{}}),
    h('main',{className:'main-surface',style:update.status==='ready'?{gridTemplateRows:'72px auto minmax(0,1fr)'}:undefined},
      h(Topbar,{activeView:view,searchQuery:'',refreshing:false,onSearchChange:()=>{},onRefresh:()=>{},onCreateChannel:()=>{}}),
      update.status==='ready'&&h('div',{className:'settings-row',role:'status',style:{background:'var(--green-soft)'}},
        h('div',null,h('strong',null,'新版本 v'+update.latestVersion+'已下载'),h('span',null,'可在方便时重启安装，保留已有账号、数据和配置。')),
        h('button',{className:'primary-button',type:'button',disabled:installing,onClick:install},installing?'等待重启确认…':'新版已下载，重启安装')),
      h('div',{className:'page-body'},view==='settings'
        ?h(SettingsView,{settings,appVersion:'0.6.0',platform:'win32',updateCheck:update,automaticUpdates:true,installingUpdate:installing,onChange:setSettings,onCheckForUpdates:()=>window.signalDesk.checkForUpdates(),onDownloadUpdate:()=>window.signalDesk.downloadUpdate(),onInstallUpdate:install,onReset:()=>record('预览不执行数据重置'),onLogout:()=>{}})
        :h('p',{style:{padding:24}},'本页只预览全局更新提示，其他业务未接入。'))),
    h(ToastStack,{toasts,onDismiss:()=>setToasts([])}));
}
createRoot(document.getElementById('root')).render(h(Preview));
</script></body></html>`, "utf8");

const inlineHtml = await readFile(path.join(fixture, "index.html"), "utf8");
const entry = inlineHtml.match(/<script type="module">([\s\S]*?)<\/script>/)?.[1];
if (!entry) throw new Error("预览入口缺少组件模块。");
await writeFile(path.join(fixture, "entry.js"), entry, "utf8");
await build({
  absWorkingDir: root,
  entryPoints: [path.join(fixture, "entry.js")],
  outfile: path.join(fixture, "bundle.js"),
  bundle: true,
  format: "esm",
  platform: "browser",
  jsx: "automatic",
  loader: { ".webp": "dataurl" },
  define: { "import.meta.env": "{}", "process.env.NODE_ENV": '"development"' },
  plugins: [{ name: "local-source-root", setup(builder) { builder.onResolve({ filter: /^\/src\// }, ({ path: sourcePath }) => ({ path: path.join(root, sourcePath.slice(1)) })); } }],
});
await writeFile(path.join(fixture, "index.html"), inlineHtml.replace(/<script type="module">[\s\S]*?<\/script>/, '<script type="module" src="./bundle.js"></script>').replace("</head>", '<link rel="stylesheet" href="./bundle.css"></head>'), "utf8");
await Promise.all(["index.html", "bundle.js", "bundle.css"].map((filename) => copyFile(path.join(fixture, filename), path.join(output, filename))));
const contentTypes = { "index.html": "text/html;charset=utf-8", "bundle.js": "application/javascript;charset=utf-8", "bundle.css": "text/css;charset=utf-8" };
const server = createServer(async (request, response) => {
  const filename = new URL(request.url, previewUrl).pathname.split("/").at(-1);
  if (!Object.hasOwn(contentTypes, filename)) { response.writeHead(404); response.end(); return; }
  try { response.setHeader("Content-Type", contentTypes[filename]); response.end(await readFile(path.join(fixture, filename))); }
  catch { response.writeHead(500); response.end("预览文件读取失败。"); }
});
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
console.log(`PREVIEW_LISTENING ${previewUrl}`);
let browser;
let passed = false;
const errors = [];
const checks = [];
const layout = [];
try {
  browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--no-proxy-server"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.route("**/*", (route) => new URL(route.request().url()).origin === new URL(previewUrl).origin ? route.continue() : route.abort());
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(previewUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const updateRow = page.locator(".settings-row").filter({ has: page.getByText("软件更新", { exact: true }) });
  for (const width of [1440, 1080]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 800 });
    for (const state of ["downloading", "ready", "error"]) {
      await page.getByLabel("预览状态", { exact: true }).selectOption(state);
      await updateRow.scrollIntoViewIfNeeded();
      if (state === "downloading") {
        await expect(page.getByRole("progressbar", { name: "更新下载进度" })).toHaveAttribute("value", "37");
        await expect(page.getByRole("button", { name: "正在下载…", exact: true })).toBeDisabled();
      } else if (state === "ready") {
        await expect(page.getByRole("button", { name: "重启并安装", exact: true })).toBeEnabled();
        await expect(page.getByRole("button", { name: "新版已下载，重启安装", exact: true })).toBeVisible();
      } else {
        await expect(page.getByText("更新失败：网络连接中断，请重试。", { exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "重试更新", exact: true })).toBeEnabled();
      }
      const boxes = await page.evaluate(() => {
        const main = document.querySelector('.main-surface').getBoundingClientRect();
        const body = document.querySelector('.page-body').getBoundingClientRect();
        const row = [...document.querySelectorAll('.settings-row')].find((element) => element.querySelector('strong')?.textContent === '软件更新');
        const button = row.querySelector('button').getBoundingClientRect();
        return {mainWidth:main.width,bodyHeight:body.height,horizontalOverflow:document.documentElement.scrollWidth>innerWidth,buttonRight:button.right,viewportWidth:innerWidth};
      });
      if (boxes.horizontalOverflow || boxes.buttonRight > width || boxes.bodyHeight < 200) throw new Error(`更新界面布局异常：${JSON.stringify(boxes)}`);
      layout.push({ width, state, ...boxes });
      await page.screenshot({ path: path.join(output, `${state}-${width}.png`), animations: "disabled" });
    }
  }
  checks.push("真实 SettingsView/Sidebar/Topbar/CSS 在 1440 和 1080 宽度的下载37%、ready、error均可操作且无水平溢出");

  await page.getByRole("button", { name: "重试更新", exact: true }).click();
  await expect(page.getByRole("progressbar", { name: "更新下载进度" })).toHaveAttribute("value", "37");
  checks.push("失败后点击重试经模拟桥接回到下载中");
  await page.getByLabel("预览状态", { exact: true }).selectOption("ready");
  await updateRow.scrollIntoViewIfNeeded();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "重启并安装", exact: true }).click();
  await expect(page.getByText("已取消本次安装，可稍后重启安装。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "重启并安装", exact: true })).toBeEnabled();
  checks.push("模拟原生确认取消后保留ready和已下载文件，不触发安装");

  await page.getByRole("button", { name: "今日", exact: true }).click();
  await expect(page.getByRole("button", { name: "新版已下载，重启安装", exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(output, "ready-global-1080.png"), animations: "disabled" });
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "新版已下载，重启安装", exact: true }).click();
  await expect(page.getByRole("button", { name: "新版已下载，重启安装", exact: true })).toHaveCount(0);
  checks.push("离开设置仍有全局ready提示；模拟确认仅更新状态，不退出或运行安装器");
  const actions = await page.evaluate(() => window.updatePreview.actions);
  if (errors.length) throw new Error(errors.join("\n"));
  await writeFile(path.join(output, "result.json"), JSON.stringify({ passed: true, previewUrl, checks, layout, actions, pageErrors: errors, evidence: "真实 SettingsView/Sidebar/Topbar/ToastStack 与 CSS；全局提示壳复用 App 的样式和文案；主进程/更新服务器/下载/安装全部模拟；不读取生产登录或数据。" }, null, 2), "utf8");
  await writeFile(path.join(output, "README.md"), `# 迈医协同更新交互预览\n\n入口：[本地交互预览](${previewUrl})\n\n使用真实 SettingsView、Sidebar、Topbar、ToastStack 和 CSS；底部控制台可切换下载中、已就绪和更新失败。全局提示壳复用 App 的样式和文案。主进程、更新服务器、下载和重启确认均为模拟，不会运行安装器或接触生产数据。\n\n重新启动预览：在源码目录运行\n\n\`\`\`powershell\nnode scripts/update-ui-preview.mjs --serve\n\`\`\`\n\n六张状态截图分别覆盖 1080/1440 宽度，另有全局提示截图；result.json 记录实际浏览器交互与布局检查。正式安装升级由真实 Electron 验证另行记录。\n`, "utf8");
  console.log(JSON.stringify({ passed: true, previewUrl, output, checks: checks.length, screenshots: 7, mode: "mock-main-no-download-no-install" }));
  passed = true;
} catch (error) {
  console.error(`PREVIEW_FAILED ${JSON.stringify({ pageErrors: errors })}`);
  throw error;
} finally {
  await browser?.close();
  if (!keepServing || !passed) await new Promise((resolve) => server.close(resolve));
}

if (keepServing) {
  console.log(`PREVIEW_RUNNING ${previewUrl}`);
  const stop = () => { server.close(() => process.exit(0)); server.closeAllConnections(); };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}
