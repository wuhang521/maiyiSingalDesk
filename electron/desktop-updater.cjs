"use strict";

const RELEASE_URL = "https://github.com/wuhang521/maiyiSingalDesk/releases";
const FEED_URL = `${RELEASE_URL}/latest/download`;
const STARTUP_DELAY_MS = 10_000;
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1_000;
const BUSY = new Set(["checking", "downloading", "installing"]);

function releaseVersion(info) {
  const version = String(info?.version || "");
  return version.length <= 80 && /^\d+\.\d+\.\d+(?:-[\da-z.-]+)?(?:\+[\da-z.-]+)?$/i.test(version)
    ? version
    : null;
}

function friendlyError(error) {
  const code = String(error?.code || "");
  const text = String(error?.message || "");
  if (/CHECKSUM|SIGNATURE/i.test(code)) return "更新文件校验失败，请稍后重新下载。";
  if (/INVALID.*(?:FEED|UPDATE_INFO|RELEASE)|NO_PUBLISHED_VERSIONS/i.test(code)) {
    return "更新信息暂不可用，请稍后重试。";
  }
  if (/ENOTFOUND|ECONN|ETIMEDOUT|CERT_|ERR_INTERNET|ERR_NETWORK/i.test(code + text)) {
    return "无法连接更新服务器，请检查网络后重试。";
  }
  return "检查或下载更新失败，请稍后重试。";
}

function blockedMessage(value) {
  const fallback = "当前有未完成任务或未保存内容，请处理后再安装。";
  const message = typeof value?.message === "string" ? value.message.trim() : "";
  // Only accept a short, plain explanation from the local installation guard.
  return message && message.length <= 160 && !/[\\/\r\n]|[a-z]:|\b[a-f\d]{24,}\b|(?:key|token|secret)\s*[:=]/i.test(message)
    ? message
    : fallback;
}

function createDesktopUpdater({ app, dialog, autoUpdater, sendState = () => {}, canInstall = () => true }) {
  let state = { status: "idle", currentVersion: app.getVersion() };
  let checkPromise = null;
  let downloadPromise = null;
  let installPromise = null;
  let startupTimer = null;
  let intervalTimer = null;
  let started = false;
  let configured = false;

  function getState() { return { ...state }; }

  function publish(patch) {
    state = { ...state, ...patch };
    try { sendState(getState()); } catch { /* A closing window must not break the updater. */ }
    return getState();
  }

  function failed(error) {
    return publish({ status: "error", progress: undefined, message: friendlyError(error) });
  }

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;
  // Never forward electron-updater's raw errors or its logger to the renderer.
  autoUpdater.logger = null;
  try {
    autoUpdater.setFeedURL({ provider: "generic", url: FEED_URL });
    configured = true;
  } catch (error) { failed(error); }

  autoUpdater.on("checking-for-update", () => {
    if (!["downloading", "ready", "installing"].includes(state.status)) {
      publish({ status: "checking", message: undefined, progress: undefined });
    }
  });
  autoUpdater.on("update-not-available", (info) => {
    if (["downloading", "ready", "installing"].includes(state.status)) return;
    publish({ status: "current", latestVersion: releaseVersion(info) || state.currentVersion,
      message: undefined, progress: undefined, releaseUrl: `${RELEASE_URL}/latest` });
  });
  autoUpdater.on("update-available", (info) => {
    const version = releaseVersion(info);
    if (!version) return failed({ code: "ERR_UPDATER_INVALID_UPDATE_INFO" });
    if (state.status === "installing" || (state.status === "ready" && state.latestVersion === version)) return;
    publish({ status: autoUpdater.autoDownload ? "downloading" : "available", latestVersion: version,
      progress: undefined, message: undefined, releaseUrl: `${RELEASE_URL}/tag/v${version}` });
  });
  autoUpdater.on("download-progress", (progress) => {
    if (["ready", "installing"].includes(state.status)) return;
    const percent = Number(progress?.percent);
    publish({ status: "downloading", progress: Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : undefined,
      message: undefined });
  });
  autoUpdater.on("update-downloaded", (info) => {
    const version = releaseVersion(info);
    if (!version) return failed({ code: "ERR_UPDATER_INVALID_UPDATE_INFO" });
    if (state.status === "installing") return;
    publish({ status: "ready", latestVersion: version, progress: 100, message: undefined,
      releaseUrl: `${RELEASE_URL}/tag/v${version}` });
  });
  autoUpdater.on("error", (error) => { failed(error); });

  function check() {
    if (checkPromise) return checkPromise;
    if (BUSY.has(state.status) || state.status === "ready") return Promise.resolve(getState());
    if (app.isPackaged === false) {
      return Promise.resolve(publish({ status: "idle", message: "开发模式不检查自动更新，请使用正式安装版。" }));
    }
    if (!configured) return Promise.resolve(failed());
    publish({ status: "checking", progress: undefined, message: undefined });
    checkPromise = Promise.resolve().then(() => autoUpdater.checkForUpdates()).then((result) => {
      // The provider owns auto-download. Do not call downloadUpdate a second time.
      if (result?.downloadPromise) {
        Promise.resolve(result.downloadPromise).catch((error) => { failed(error); });
      }
      if (!result && state.status === "checking") {
        publish({ status: "idle", message: "当前环境暂不支持自动更新。" });
      }
      return getState();
    }).catch(failed).finally(() => { checkPromise = null; });
    return checkPromise;
  }

  function download() {
    if (downloadPromise) return downloadPromise;
    if (state.status === "downloading" || state.status === "ready" || state.status === "installing") {
      return Promise.resolve(getState());
    }
    if (state.status !== "available") return Promise.resolve(getState());
    publish({ status: "downloading", progress: undefined, message: undefined });
    downloadPromise = Promise.resolve().then(() => autoUpdater.downloadUpdate()).then(() => getState())
      .catch(failed).finally(() => { downloadPromise = null; });
    return downloadPromise;
  }

  async function installationAllowed() {
    try {
      const result = await canInstall();
      return result === true || result?.allowed === true
        ? { allowed: true }
        : { allowed: false, message: blockedMessage(result) };
    } catch { return { allowed: false, message: blockedMessage() }; }
  }

  function install() {
    if (installPromise) return installPromise;
    if (state.status !== "ready") return Promise.resolve(getState());
    installPromise = Promise.resolve().then(async () => {
      let guard = await installationAllowed();
      if (!guard.allowed) return publish({ message: guard.message });
      let answer;
      try {
        answer = await dialog.showMessageBox({ type: "question", title: "安装迈医协同更新",
          message: `新版本 ${state.latestVersion} 已下载，是否安装并重启？`,
          detail: "请先保存编辑内容并等待视频任务完成。现有账号配置和本地资料将保留。",
          buttons: ["稍后", "安装并重启"], defaultId: 0, cancelId: 0, noLink: true });
      } catch { return publish({ message: "暂时无法确认安装，请稍后重试。" }); }
      if (answer?.response !== 1) return publish({ message: "更新已保留，可稍后安装。" });
      if (state.status !== "ready") return getState();
      guard = await installationAllowed();
      if (!guard.allowed) return publish({ message: guard.message });
      if (state.status !== "ready") return getState();
      // Only this explicit, confirmed path can install. A normal app quit never does.
      publish({ status: "installing", message: "正在安装更新并重启…" });
      try { autoUpdater.quitAndInstall(false, true); }
      catch { return publish({ status: "ready", message: "安装未能启动，请稍后重试。" }); }
      return getState();
    }).finally(() => { installPromise = null; });
    return installPromise;
  }

  function start() {
    if (started || app.isPackaged === false) return getState();
    started = true;
    startupTimer = setTimeout(() => { startupTimer = null; void check(); }, STARTUP_DELAY_MS);
    intervalTimer = setInterval(() => { void check(); }, CHECK_INTERVAL_MS);
    startupTimer.unref?.();
    intervalTimer.unref?.();
    return getState();
  }

  function stop() {
    if (startupTimer) clearTimeout(startupTimer);
    if (intervalTimer) clearInterval(intervalTimer);
    startupTimer = null;
    intervalTimer = null;
    started = false;
    // Keep error listeners while an in-flight download winds down during shutdown.
    return getState();
  }

  return { getState, check, download, install, start, stop };
}

module.exports = { createDesktopUpdater, FEED_URL, STARTUP_DELAY_MS, CHECK_INTERVAL_MS };
