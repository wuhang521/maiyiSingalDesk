"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");
const { createDesktopUpdater, FEED_URL, STARTUP_DELAY_MS, CHECK_INTERVAL_MS } = require("../electron/desktop-updater.cjs");

class Provider extends EventEmitter {
  checks = 0;
  downloads = 0;
  installs = [];
  setFeedURL(value) { this.feed = value; }
  async checkForUpdates() { this.checks++; this.emit("update-not-available", { version: "0.5.3" }); return { updateInfo: { version: "0.5.3" } }; }
  async downloadUpdate() { this.downloads++; this.emit("update-downloaded", { version: "0.5.4" }); return []; }
  quitAndInstall(...args) { this.installs.push(args); }
}

function fixture(overrides = {}) {
  const provider = new Provider();
  const sent = [];
  const dialogs = [];
  const app = { isPackaged: true, getVersion: () => "0.5.3" };
  const dialog = { showMessageBox: async (options) => { dialogs.push(options); return { response: 0 }; } };
  const updater = createDesktopUpdater({ app, dialog, autoUpdater: provider, sendState: (state) => sent.push(state), ...overrides });
  return { updater, provider, sent, dialogs, app, dialog };
}

test("uses the public generic download feed and disables installation on ordinary quit", () => {
  const { provider, updater } = fixture();
  assert.deepEqual(provider.feed, { provider: "generic", url: FEED_URL });
  assert.equal(FEED_URL, "https://github.com/wuhang521/maiyiSingalDesk/releases/latest/download");
  assert.equal(provider.autoDownload, true);
  assert.equal(provider.autoInstallOnAppQuit, false);
  assert.equal(provider.allowDowngrade, false);
  assert.equal(provider.allowPrerelease, false);
  assert.equal(provider.logger, null);
  const copy = updater.getState(); copy.status = "installing";
  assert.equal(updater.getState().status, "idle");
  updater.stop(); assert.equal(provider.installs.length, 0);
});

test("deduplicates checks and exposes no-update state", async () => {
  const { provider, updater } = fixture();
  await Promise.all([updater.check(), updater.check(), updater.check()]);
  assert.equal(provider.checks, 1);
  assert.equal(updater.getState().status, "current");
  assert.equal(updater.getState().currentVersion, "0.5.3");
});

test("lets provider auto-download once, clamps progress and exposes ready state", async () => {
  const { provider, updater } = fixture();
  let finishDownload;
  const pending = new Promise((resolve) => { finishDownload = resolve; });
  provider.checkForUpdates = async () => {
    provider.checks++;
    provider.emit("update-available", { version: "0.5.4", releaseNotes: "private" });
    return { downloadPromise: pending };
  };
  await updater.check();
  assert.equal(updater.getState().status, "downloading");
  await Promise.all([updater.check(), updater.download(), updater.download()]);
  assert.equal(provider.checks, 1); assert.equal(provider.downloads, 0);
  provider.emit("download-progress", { percent: 137, path: "C:/private" });
  assert.equal(updater.getState().progress, 100);
  provider.emit("download-progress", { percent: -1 });
  assert.equal(updater.getState().progress, 0);
  provider.emit("update-downloaded", { version: "0.5.4", downloadedFile: "C:/private" });
  finishDownload([]); await pending;
  assert.equal(updater.getState().status, "ready");
  assert.equal(updater.getState().releaseUrl, "https://github.com/wuhang521/maiyiSingalDesk/releases/tag/v0.5.4");
  assert.equal(JSON.stringify(updater.getState()).includes("private"), false);
});

test("manual download is deduplicated when automatic downloading is disabled", async () => {
  const { provider, updater } = fixture();
  provider.autoDownload = false;
  provider.emit("update-available", { version: "0.5.4" });
  assert.equal(updater.getState().status, "available");
  await Promise.all([updater.download(), updater.download()]);
  assert.equal(provider.downloads, 1);
  assert.equal(updater.getState().status, "ready");
});

test("errors are sanitized and metadata cannot inject an arbitrary release URL", async () => {
  const { provider, updater, sent } = fixture();
  provider.checkForUpdates = async () => { throw Object.assign(new Error("C:/Users/private api_key=hiddensecretvalue"), { code: "ENOTFOUND" }); };
  await updater.check();
  assert.equal(updater.getState().status, "error");
  assert.match(updater.getState().message, /网络/);
  provider.emit("error", Object.assign(new Error("token=secret"), { code: "ERR_UPDATER_CHECKSUM_MISMATCH" }));
  assert.match(updater.getState().message, /校验/);
  provider.emit("update-available", { version: "../../private" });
  assert.equal(updater.getState().status, "error");
  assert.equal(JSON.stringify(sent).includes("hiddensecretvalue"), false);
  assert.equal(JSON.stringify(sent).includes("Users"), false);
});

test("handles rejected automatic download promises without exposing raw error", async () => {
  const { updater, provider } = fixture();
  provider.checkForUpdates = async () => {
    provider.emit("update-available", { version: "0.5.4" });
    return { downloadPromise: Promise.reject(new Error("C:/private/sensitive")) };
  };
  await updater.check(); await Promise.resolve();
  assert.equal(updater.getState().status, "error");
  assert.equal(JSON.stringify(updater.getState()).includes("sensitive"), false);
});

test("installation confirmation defaults to later and cancellation does not quit", async () => {
  const { updater, provider, dialogs } = fixture();
  provider.emit("update-downloaded", { version: "0.5.4" });
  await Promise.all([updater.install(), updater.install()]);
  assert.equal(dialogs.length, 1);
  assert.deepEqual(dialogs[0].buttons, ["稍后", "安装并重启"]);
  assert.equal(dialogs[0].defaultId, 0); assert.equal(dialogs[0].cancelId, 0);
  assert.equal(provider.installs.length, 0);
  assert.equal(updater.getState().status, "ready");
});

test("only explicit confirmation installs once with visible installer and restart", async () => {
  const { updater, provider } = fixture({ dialog: { showMessageBox: async () => ({ response: 1 }) } });
  await updater.install(); assert.equal(provider.installs.length, 0);
  provider.emit("update-downloaded", { version: "0.5.4" });
  await Promise.all([updater.install(), updater.install(), updater.install()]);
  await updater.install();
  assert.deepEqual(provider.installs, [[false, true]]);
  assert.equal(updater.getState().status, "installing");
});

test("an active task or unsaved edit blocks before showing a dialog", async () => {
  const { updater, provider, dialogs } = fixture({ canInstall: async () => ({ allowed: false, message: "请先保存编辑内容。" }) });
  provider.emit("update-downloaded", { version: "0.5.4" });
  await updater.install();
  assert.equal(dialogs.length, 0); assert.equal(provider.installs.length, 0);
  assert.equal(updater.getState().status, "ready");
  assert.equal(updater.getState().message, "请先保存编辑内容。");
});

test("rechecks safety after confirmation and fails closed on guard exceptions", async () => {
  let count = 0;
  const f = fixture({ dialog: { showMessageBox: async () => ({ response: 1 }) }, canInstall: async () => ++count === 1 });
  f.provider.emit("update-downloaded", { version: "0.5.4" });
  await f.updater.install(); assert.equal(count, 2); assert.equal(f.provider.installs.length, 0);
  const g = fixture({ canInstall: () => { throw new Error("C:/private"); } });
  g.provider.emit("update-downloaded", { version: "0.5.4" });
  await g.updater.install();
  assert.equal(g.dialogs.length, 0); assert.equal(g.provider.installs.length, 0);
  assert.equal(JSON.stringify(g.updater.getState()).includes("private"), false);
});

test("failed dialogs or installation keep the downloaded update available", async () => {
  const f = fixture({ dialog: { showMessageBox: async () => { throw new Error("C:/private"); } } });
  f.provider.emit("update-downloaded", { version: "0.5.4" }); await f.updater.install();
  assert.equal(f.updater.getState().status, "ready"); assert.equal(f.provider.installs.length, 0);
  const g = fixture({ dialog: { showMessageBox: async () => ({ response: 1 }) } });
  g.provider.quitAndInstall = () => { throw new Error("C:/private"); };
  g.provider.emit("update-downloaded", { version: "0.5.4" }); await g.updater.install();
  assert.equal(g.updater.getState().status, "ready");
});

test("does not install if readiness is lost while the confirmation dialog is open", async () => {
  let answer;
  const f = fixture({ dialog: { showMessageBox: () => new Promise((resolve) => { answer = resolve; }) } });
  f.provider.emit("update-downloaded", { version: "0.5.4" });
  const pending = f.updater.install();
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(typeof answer, "function");
  f.provider.emit("error", new Error("download no longer available"));
  answer({ response: 1 }); await pending;
  assert.equal(f.provider.installs.length, 0);
  assert.equal(f.updater.getState().status, "error");
});

test("startup checks after ten seconds, repeats every four hours and clears timers", async () => {
  const originals = { setTimeout, setInterval, clearTimeout, clearInterval };
  const scheduled = [], cleared = [];
  try {
    global.setTimeout = (fn, ms) => { const timer = { fn, ms, kind: "timeout", unref() {} }; scheduled.push(timer); return timer; };
    global.setInterval = (fn, ms) => { const timer = { fn, ms, kind: "interval", unref() {} }; scheduled.push(timer); return timer; };
    global.clearTimeout = (timer) => cleared.push(timer);
    global.clearInterval = (timer) => cleared.push(timer);
    const { updater, provider } = fixture();
    updater.start(); updater.start();
    assert.equal(scheduled.length, 2); assert.equal(provider.checks, 0);
    assert.equal(scheduled[0].ms, STARTUP_DELAY_MS); assert.equal(STARTUP_DELAY_MS, 10_000);
    assert.equal(scheduled[1].ms, CHECK_INTERVAL_MS); assert.equal(CHECK_INTERVAL_MS, 14_400_000);
    scheduled[0].fn(); await updater.check(); assert.equal(provider.checks, 1);
    scheduled[1].fn(); await updater.check(); assert.equal(provider.checks, 2);
    updater.stop(); updater.stop(); assert.deepEqual(cleared, [scheduled[1]]);
    updater.start(); assert.equal(scheduled.length, 4); updater.stop();
    assert.equal(cleared.includes(scheduled[2]), true); assert.equal(cleared.includes(scheduled[3]), true);
  } finally { Object.assign(global, originals); }
});

test("development mode skips automatic and manual network checks", async () => {
  const { updater, provider } = fixture({ app: { isPackaged: false, getVersion: () => "0.5.3" } });
  updater.start(); await updater.check(); updater.stop();
  assert.equal(provider.checks, 0); assert.equal(provider.downloads, 0);
  assert.equal(updater.getState().status, "idle");
  assert.match(updater.getState().message, /开发模式/);
});
